#!/usr/bin/env python3
"""Read-only audit: can every universe member be priced from a real PSA10 sale?

Step 0 of the "PSA10 price = latest real sale" cutover.  This tool answers one
question with the *actual* eligibility SQL the new selector will use, because
the two exploratory readers that preceded it disagreed (1,604 vs 1,602) — they
were reading different definitions of "eligible sale".  Nothing downstream may
rely on either number; it has to be recomputed here.

What it does NOT do: write to the database.  It opens one connection, sets a
60s statement timeout, runs SELECTs, and writes a single JSON receipt to disk.

Eligibility (plan section 3.3, corrected against the real column names):
  source_code in the routed lane ('pricecharting' for EN, 'snkrdunk' otherwise)
  UPPER(grader_code) = 'PSA'
  UPPER(REPLACE(grade_label,' ','')) in {10, 10.0, PSA10, GEMMINT10}
      -- SNKRDUNK carries both ('psa','10') and ('PSA','PSA 10')
  coverage_status in {partial, complete, certified}   -- positive allow-list:
      -- a future unknown status must not silently become eligible
  unit_price_usd > 0 and quantity > 0
  timestamp_quality in the pc_psa10_price_derivation whitelist
  transaction_fingerprint non-empty, deduplicated per (variant, source)
  external_entity_id, normalised, equals the variant's strict-identity id for
      the parent source  -- kills the "one card, two SNK item ids" shape where
      the quote is bound to one id and the sales sit on the other
  (EN lane) sale id not in the PC title/collector-number quarantine receipt

Outlier walk-back (plan section 3.4) is implemented inline, on purpose: step 0
must be runnable before pipelines/sale_price_outlier.py exists, and the two
implementations agreeing later is a real cross-check rather than a tautology.

Usage:
    python3 -X utf8 scripts/audit_latest_sale_coverage.py \
        --out data/runtime/operator/audit/latest_sale_coverage_baseline.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

# --- plan section 3.4 constants (mirrored from pipelines/sealed_price_compose.py) ---
TRIM_HIGH = Decimal("2.0")
TRIM_LOW = Decimal("2.5")
SOLD_MIN_N = 3
PRIOR_LIMIT = 10
PRIOR_WINDOW_DAYS = 180

# --- plan section 3.3 eligibility constants ---
QUOTE_SALE_SOURCES = ("pricecharting", "snkrdunk")
GRADE_LABELS = frozenset({"10", "10.0", "PSA10", "GEMMINT10"})
COVERAGE_ALLOWED = frozenset({"partial", "complete", "certified"})
TIMESTAMP_QUALITY_ALLOWED = frozenset({
    "exact", "date", "timestamp", "exact_date", "relative_resolved",
    "relative_subday",
})
EXTERNAL_ID_PREFIXES = ("pc:", "snkrdunk:")

PC_TITLE_QUARANTINE = (
    ROOT / "data" / "runtime" / "operator" / "audit"
    / "pc_sale_title_quarantine_current.json"
)
DEFAULT_OUT = (
    ROOT / "data" / "runtime" / "operator" / "audit"
    / "latest_sale_coverage_baseline.json"
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def normalise_external_id(value: Any) -> str:
    text = str(value or "").strip().casefold()
    for prefix in EXTERNAL_ID_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def normalise_language(value: Any) -> str:
    return str(value or "").strip().replace("_", "-").casefold()


def routed_sources(language: str) -> tuple[str, str]:
    """Owner routing: EN reads PriceCharting sales first, everyone else SNKRDUNK.

    The fallback slot is the same second route the policy table has always
    carried, not a new mechanism.
    """

    if normalise_language(language) == "en":
        return ("pricecharting", "snkrdunk")
    return ("snkrdunk", "pricecharting")


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def median_decimal(values: Sequence[Decimal]) -> Decimal:
    ordered = sorted(values)
    count = len(ordered)
    if count == 0:
        raise ValueError("median of an empty sequence")
    if count % 2 == 1:
        return ordered[count // 2]
    return (ordered[count // 2 - 1] + ordered[count // 2]) / Decimal(2)


def percentiles(values: Sequence[float], points: Iterable[int]) -> dict[str, float]:
    """Nearest-rank percentiles; empty input yields an empty mapping."""

    ordered = sorted(values)
    if not ordered:
        return {}
    out: dict[str, float] = {}
    for point in points:
        index = max(0, min(len(ordered) - 1, round(point / 100 * len(ordered)) - 1))
        out[f"p{point}"] = round(ordered[index], 6)
    return out


# --------------------------------------------------------------------------
# selection (plan section 3.4)
# --------------------------------------------------------------------------
def candidate_order(sales: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Deterministic newest-first walk order.

    Newest ``sold_at`` first.  Inside one ``sold_at`` value the median-priced
    sale leads (odd count takes the middle, even count takes the lower), then
    the remainder by price then fingerprint.  PriceCharting stores date-only
    ``sold_at``, so this tie-break is the rule that actually decides ~18% of the
    EN board -- it must be deterministic or payload_sha256 is not reproducible.
    """

    by_day: dict[datetime, list[Mapping[str, Any]]] = {}
    for sale in sales:
        by_day.setdefault(sale["sold_at"], []).append(sale)
    ordered: list[Mapping[str, Any]] = []
    for sold_at in sorted(by_day, reverse=True):
        group = sorted(
            by_day[sold_at],
            key=lambda row: (row["unit_price_usd"], row["transaction_fingerprint"]),
        )
        middle = (len(group) - 1) // 2
        ordered.append(group[middle])
        ordered.extend(row for index, row in enumerate(group) if index != middle)
    return ordered


def prior_window(
    sales: Sequence[Mapping[str, Any]],
    candidate: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    """Up to PRIOR_LIMIT eligible sales strictly before the candidate, <=180d."""

    floor = candidate["sold_at"] - timedelta(days=PRIOR_WINDOW_DAYS)
    prior = [
        sale for sale in sales
        if floor <= sale["sold_at"] < candidate["sold_at"]
    ]
    prior.sort(
        key=lambda row: (
            row["sold_at"],
            row["unit_price_usd"],
            row["transaction_fingerprint"],
        )
    )
    # Ascending sort then take the tail: the PRIOR_LIMIT most recent, chosen
    # deterministically when a single day overflows the limit.
    return prior[-PRIOR_LIMIT:]


def select_latest_sale(
    sales: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any] | None, dict[str, Any]]:
    """Newest eligible sale that survives the outlier band, with walk-back.

    Returns ``(chosen_or_None, evidence)``.  ``evidence['rejected']`` is always
    present, empty list included: a gate that throws away its rejections cannot
    tell "nothing was near the threshold" from "the filter never ran".
    """

    rejected: list[dict[str, Any]] = []
    for candidate in candidate_order(sales):
        prior = prior_window(sales, candidate)
        price = candidate["unit_price_usd"]
        if len(prior) < SOLD_MIN_N:
            return candidate, {
                "verdict": "accepted",
                "ungated": True,
                "priorN": len(prior),
                "medianUsd": None,
                "rejected": rejected,
                "walkbacks": len(rejected),
            }
        median_usd = median_decimal([row["unit_price_usd"] for row in prior])
        if median_usd <= 0:
            reason = "prior_median_not_positive"
        elif price > median_usd * TRIM_HIGH:
            reason = "above_band"
        elif price < median_usd / TRIM_LOW:
            reason = "below_band"
        else:
            return candidate, {
                "verdict": "accepted",
                "ungated": False,
                "priorN": len(prior),
                "medianUsd": str(median_usd),
                "rejected": rejected,
                "walkbacks": len(rejected),
            }
        rejected.append({
            "saleObservationId": int(candidate["id"]),
            "soldAt": candidate["sold_at"].isoformat(),
            "unitPriceUsd": str(price),
            "medianUsd": str(median_usd),
            "priorN": len(prior),
            "ratio": str(round(price / median_usd, 6)) if median_usd else None,
            "reason": reason,
        })
    return None, {
        "verdict": "no_eligible_sale",
        "ungated": False,
        "priorN": 0,
        "medianUsd": None,
        "rejected": rejected,
        "walkbacks": len(rejected),
    }


# --------------------------------------------------------------------------
# loaders (read-only)
# --------------------------------------------------------------------------
def load_universe(cursor: Any) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT m.variant_id,
               pi.card_language,
               pi.collector_number,
               pi.set_name,
               v.canonical_name
        FROM market_universe_member m
        INNER JOIN market_universe_lock l
          ON l.id = m.universe_lock_id AND l.is_current = 1
        INNER JOIN catalog_printing_identity pi ON pi.variant_id = m.variant_id
        INNER JOIN catalog_variant v ON v.id = m.variant_id
        ORDER BY m.variant_id
        """
    )
    return list(cursor.fetchall())


def load_strict_identity(cursor: Any) -> dict[tuple[int, str], set[str]]:
    cursor.execute(
        """
        SELECT variant_id, source_code, external_entity_id
        FROM operator_strict_source_identity
        WHERE source_code IN (%s, %s)
        """,
        QUOTE_SALE_SOURCES,
    )
    bound: dict[tuple[int, str], set[str]] = {}
    for row in cursor.fetchall():
        key = (int(row["variant_id"]), str(row["source_code"]))
        bound.setdefault(key, set()).add(
            normalise_external_id(row["external_entity_id"])
        )
    return bound


def load_pc_title_quarantine() -> set[int]:
    if not PC_TITLE_QUARANTINE.exists():
        raise RuntimeError(
            "PC title quarantine receipt is missing -- fail closed rather than"
            f" audit without it: {PC_TITLE_QUARANTINE}"
        )
    document = json.loads(PC_TITLE_QUARANTINE.read_text(encoding="utf-8"))
    entries = document.get("entries") or []
    return {int(entry["saleObservationId"]) for entry in entries}


def load_candidate_sales(
    cursor: Any,
    variant_ids: Sequence[int],
) -> dict[tuple[int, str], list[dict[str, Any]]]:
    """Eligible sale rows keyed by (variant_id, source_code).

    The identity bind, the fingerprint dedupe and the PC quarantine subtraction
    happen in Python (they need the receipt and the strict-identity map), but
    every column-level predicate is pushed into SQL so the query the plan
    describes is the query that ran.
    """

    grades = tuple(sorted(GRADE_LABELS))
    coverage = tuple(sorted(COVERAGE_ALLOWED))
    quality = tuple(sorted(TIMESTAMP_QUALITY_ALLOWED))
    rows: list[dict[str, Any]] = []
    chunk_size = 500
    for start in range(0, len(variant_ids), chunk_size):
        chunk = tuple(variant_ids[start:start + chunk_size])
        sql = (
            "SELECT id, variant_id, source_code, external_entity_id,"
            " transaction_fingerprint, grader_code, grade_label, sold_at,"
            " timestamp_quality, unit_price_usd, quantity, coverage_status,"
            " listing_item_id, listing_url, listing_title"
            " FROM market_sale_observation"
            " WHERE variant_id IN (" + ",".join(["%s"] * len(chunk)) + ")"
            "   AND source_code IN (" + ",".join(["%s"] * len(QUOTE_SALE_SOURCES)) + ")"
            "   AND UPPER(grader_code) = 'PSA'"
            "   AND UPPER(REPLACE(grade_label,' ','')) IN ("
            + ",".join(["%s"] * len(grades)) + ")"
            "   AND coverage_status IN (" + ",".join(["%s"] * len(coverage)) + ")"
            "   AND unit_price_usd > 0"
            "   AND quantity > 0"
            "   AND LOWER(timestamp_quality) IN ("
            + ",".join(["%s"] * len(quality)) + ")"
            "   AND transaction_fingerprint IS NOT NULL"
            "   AND transaction_fingerprint <> ''"
            "   AND sold_at IS NOT NULL"
        )
        cursor.execute(
            sql,
            chunk + QUOTE_SALE_SOURCES + grades + coverage + quality,
        )
        rows.extend(cursor.fetchall())
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = {}
    for row in rows:
        row["unit_price_usd"] = _dec(row["unit_price_usd"])
        if row["unit_price_usd"] is None:
            continue
        grouped.setdefault(
            (int(row["variant_id"]), str(row["source_code"])), []
        ).append(row)
    return grouped


def load_current_quotes(cursor: Any) -> dict[int, dict[str, Any]]:
    cursor.execute(
        "SELECT MAX(business_date) AS business_date FROM market_variant_source_state"
    )
    row = cursor.fetchone()
    business_date = row and row.get("business_date")
    if business_date is None:
        return {}
    cursor.execute(
        """
        SELECT s.variant_id,
               q.price_usd,
               q.source_code AS storage_source_code,
               q.source_period_at,
               q.checked_at,
               s.selected_policy_version
        FROM market_variant_source_state s
        INNER JOIN market_current_quote_revision q
          ON q.id = s.selected_quote_revision_id
        WHERE s.capability = 'canonical_quote'
          AND s.is_selected = 1
          AND s.business_date = %s
        """,
        (business_date,),
    )
    quotes: dict[int, dict[str, Any]] = {}
    for record in cursor.fetchall():
        quotes[int(record["variant_id"])] = {
            "businessDate": str(business_date),
            "priceUsd": _dec(record["price_usd"]),
            "sourceCode": str(record["storage_source_code"]),
            "sourcePeriodAt": record["source_period_at"],
            "checkedAt": record["checked_at"],
            "policyVersion": record["selected_policy_version"],
        }
    return quotes


# --------------------------------------------------------------------------
# audit
# --------------------------------------------------------------------------
def audit(cursor: Any) -> dict[str, Any]:
    members = load_universe(cursor)
    variant_ids = [int(row["variant_id"]) for row in members]
    bound_ids = load_strict_identity(cursor)
    quarantined_sale_ids = load_pc_title_quarantine()
    sales_by_key = load_candidate_sales(cursor, variant_ids)
    current_quotes = load_current_quotes(cursor)

    rows: list[dict[str, Any]] = []
    no_eligible: list[dict[str, Any]] = []
    ratios: list[float] = []
    by_source: dict[str, int] = {}
    total_rejected = 0
    total_walkbacks = 0
    total_ungated = 0
    identity_dropped = 0
    quarantine_dropped = 0
    fingerprint_dropped = 0

    for member in members:
        variant_id = int(member["variant_id"])
        language = normalise_language(member["card_language"])
        primary, fallback = routed_sources(language)

        chosen: Mapping[str, Any] | None = None
        chosen_source: str | None = None
        evidence: dict[str, Any] = {}
        for source in (primary, fallback):
            pool = sales_by_key.get((variant_id, source)) or []
            allowed = bound_ids.get((variant_id, source)) or set()
            eligible: list[dict[str, Any]] = []
            seen_fingerprints: set[str] = set()
            for sale in pool:
                if normalise_external_id(sale["external_entity_id"]) not in allowed:
                    identity_dropped += 1
                    continue
                if source == "pricecharting" and int(sale["id"]) in quarantined_sale_ids:
                    quarantine_dropped += 1
                    continue
                fingerprint = str(sale["transaction_fingerprint"])
                if fingerprint in seen_fingerprints:
                    fingerprint_dropped += 1
                    continue
                seen_fingerprints.add(fingerprint)
                eligible.append(sale)
            if not eligible:
                continue
            candidate, candidate_evidence = select_latest_sale(eligible)
            total_rejected += len(candidate_evidence["rejected"])
            if candidate is not None:
                chosen = candidate
                chosen_source = source
                evidence = candidate_evidence
                break
            evidence = candidate_evidence

        current = current_quotes.get(variant_id) or {}
        current_price = current.get("priceUsd")
        ratio: float | None = None
        if chosen is not None and current_price and current_price > 0:
            ratio = float(chosen["unit_price_usd"] / current_price)
            ratios.append(ratio)

        if chosen is None:
            no_eligible.append({
                "variantId": variant_id,
                "language": member["card_language"],
                "collectorNumber": member["collector_number"],
                "setName": member["set_name"],
                "canonicalName": member["canonical_name"],
                "routedSource": primary,
                "fallbackSource": fallback,
                "rejectedCandidates": evidence.get("rejected") or [],
                "currentQuoteUsd": str(current_price) if current_price else None,
                "currentQuoteSource": current.get("sourceCode"),
            })
        else:
            total_walkbacks += int(evidence.get("walkbacks") or 0)
            if evidence.get("ungated"):
                total_ungated += 1
            by_source[chosen_source] = by_source.get(chosen_source, 0) + 1

        rows.append({
            "variantId": variant_id,
            "language": member["card_language"],
            "routedSource": primary,
            "chosenSource": chosen_source,
            "chosenSaleId": int(chosen["id"]) if chosen is not None else None,
            "soldAt": chosen["sold_at"].isoformat() if chosen is not None else None,
            "unitPriceUsd": str(chosen["unit_price_usd"]) if chosen is not None else None,
            "priorMedian": evidence.get("medianUsd"),
            "priorN": evidence.get("priorN"),
            "verdict": evidence.get("verdict") or "no_eligible_sale",
            "ungated": bool(evidence.get("ungated")),
            "walkbacks": int(evidence.get("walkbacks") or 0),
            "rejectedCandidates": evidence.get("rejected") or [],
            "currentQuoteUsd": str(current_price) if current_price else None,
            "currentQuoteSource": current.get("sourceCode"),
            "ratio": round(ratio, 6) if ratio is not None else None,
        })

    with_eligible = sum(1 for row in rows if row["chosenSaleId"] is not None)
    summary = {
        "members": len(members),
        "withEligibleSale": with_eligible,
        "noEligibleSale": no_eligible,
        "noEligibleSaleCount": len(no_eligible),
        "rejectedSales": total_rejected,
        "walkbacks": total_walkbacks,
        "ungated": total_ungated,
        "ratioPercentiles": percentiles(ratios, (1, 5, 10, 25, 50, 75, 90, 95, 99)),
        "ratioSampleCount": len(ratios),
        "moversUp1_5": sum(1 for value in ratios if value > 1.5),
        "moversDown0_667": sum(1 for value in ratios if value < 0.667),
        "bySource": dict(sorted(by_source.items())),
        "droppedByStrictIdentity": identity_dropped,
        "droppedByTitleQuarantine": quarantine_dropped,
        "droppedByDuplicateFingerprint": fingerprint_dropped,
        "currentQuoteBusinessDate": next(
            (value.get("businessDate") for value in current_quotes.values()), None
        ),
    }
    return {"summary": summary, "rows": rows}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--credentials-env", type=Path, default=R.DAILY_CREDENTIALS_ENV)
    args = parser.parse_args(argv)

    connection = R.connect(args.credentials_env)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time=60000")
            report = audit(cursor)
    finally:
        connection.close()

    report["contract"] = "psa10_latest_sale_coverage_audit_v1"
    report["generatedAt"] = datetime.now(timezone.utc).isoformat()
    report["constants"] = {
        "trimHigh": str(TRIM_HIGH),
        "trimLow": str(TRIM_LOW),
        "soldMinN": SOLD_MIN_N,
        "priorLimit": PRIOR_LIMIT,
        "priorWindowDays": PRIOR_WINDOW_DAYS,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=1, ensure_ascii=False, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], indent=1, ensure_ascii=False, default=str))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
