#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mint the ranked PSA10 quote from the latest REAL sale.

Owner 2026-08-23: the published PSA10 price of a card must be the newest
completed PSA 10 sale of that exact card -- PriceCharting's sold comps on the
EN board, SNKRDUNK's completed trades on the JP board -- with outrageous sales
excluded.  Until today every ranked price on the board (1,604 / 1,604) came
from a chart series instead: PriceCharting's `manualonly.last` month head and
SNKRDUNK's K-line head bar.  Those observations still land in
market_price_observation for the charts; they may no longer become the price.

What this module is NOT:
  * not a new collection adapter.  Nothing is fetched: the sales are already in
    market_sale_observation, landed nightly by pc_ebay_sales / snk_trades.
    Registering an adapter would also make sync_source_registry rewrite the
    alias rows' canonical/identity codes every tick.
  * not a filter on landing.  Sales land unfiltered; the eligibility rules
    below are applied at selection time, which is where they belong.

Two timestamps do different jobs and mixing them breaks the chain:
  * `source_period_at` / `observed_date` = the SALE day.  This is what the card
    page shows as "last sale", and it is honestly old when the market is quiet.
  * `checked_at` / `observed_at`         = the HARVEST clock (`as_of`).  The
    business-window barrier and the accept-side staleness gate both read
    checked_at; writing the sale day there would make every card older than
    priceMaxAgeDays vanish from the selector and abort the chain.

Usage:
  python -X utf8 pipelines/psa10_latest_sale_quote.py --source pricecharting \
      --variants all --dry-run --report data/runtime/operator/audit/x.json
  python -X utf8 pipelines/psa10_latest_sale_quote.py --source snkrdunk \
      --variants all --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from current_quote_revision import (  # noqa: E402
    QUOTE_MINT_PURPOSE_LIVE,
    QUOTE_STORAGE_ALIASES,
    SALE_QUOTE_CONTRACT,
    SALE_QUOTE_METHOD,
    SALE_QUOTE_OBSERVATION_KIND,
    SALE_QUOTE_SOURCE_PRIORITY,
    insert_quote_revision,
    mintable_quote_storage_source_codes,
)
from sale_price_outlier import price_text, select_latest_sale  # noqa: E402

CONTRACT = SALE_QUOTE_CONTRACT
METHOD = SALE_QUOTE_METHOD
RECEIPT_CONTRACT = "psa10_latest_sale_quote_receipt_v1"
RUN_KEY_PREFIX = "psa10_latest_sale_"
AUDIT_DIR = ROOT / "data" / "runtime" / "operator" / "audit"
QUARANTINE_RECEIPT = AUDIT_DIR / "pc_sale_title_quarantine_current.json"

# Which parent source each sale lane belongs to.  Derived from the shared alias
# table so the storage code can never drift from what F-MINT will accept.
SALE_LANE_BY_SOURCE: dict[str, str] = {
    parent: alias
    for parent, aliases in QUOTE_STORAGE_ALIASES.items()
    for alias in aliases
    if alias in set(mintable_quote_storage_source_codes())
}

# §3.3 eligibility.  Every value is copied from an existing implementation, not
# invented: the timestamp whitelist and the grade normalisation are
# pc_psa10_price_derivation.py:143-152, the coverage allow-list is the positive
# form of the same check (a negative `<> 'quarantined'` would silently admit
# any status added later).
GRADE_LABELS = ("10", "10.0", "PSA10", "GEMMINT10")
COVERAGE_STATUSES = ("partial", "complete", "certified")
TIMESTAMP_QUALITIES = (
    "exact",
    "date",
    "timestamp",
    "exact_date",
    "relative_resolved",
    "relative_subday",
)


def normalize_external_entity_id(value: Any) -> str:
    """`snkrdunk:807560` / `pc:5834844` / `5834844` -> `5834844`.

    operator_strict_source_identity stores bare ids; market_sale_observation
    carries a provider prefix on 6,508 SNKRDUNK rows.  The bare form is what
    the quote must store, because the eligibility view proves identity with a
    plain equality (no normalisation on its side) -- a prefixed id mints a
    revision that is invisible to selection and to acceptance.
    """

    text = str(value or "").strip()
    if ":" in text:
        text = text.rsplit(":", 1)[-1]
    return text.casefold()


def canonical_payload_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def payload_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_payload_bytes(payload)).hexdigest()


def load_title_quarantine(path: Path = QUARANTINE_RECEIPT) -> set[int]:
    """Quarantined sale ids for EVERY sale lane, fail-closed.

    The receipt is pc_sale_title_quarantine.py's union of its discriminators
    and every market_pc_sale_title_quarantine row.  The table keys on
    market_sale_observation.id, which is source-neutral, so since 2026-09-25 it
    also holds SNKRDUNK sales (e.g. a JPY 1,999,999 placeholder trade).
    The receipt is regenerated before every bake and a DB gate keeps it from
    going stale.  A missing receipt is a hard stop, never an empty set: the
    2026-07-14 v1326 Latias sale is exactly the shape that becomes the headline
    price under this module.
    """

    if not path.is_file():
        raise FileNotFoundError(
            f"PC sale title quarantine receipt is missing: {path}."
            " Run pipelines/pc_sale_title_quarantine.py first."
        )
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"quarantine receipt has no entries list: {path}")
    return {int(entry["saleObservationId"]) for entry in entries}


def _chunks(values: Sequence[Any], size: int = 400) -> Iterable[list[Any]]:
    for start in range(0, len(values), size):
        yield list(values[start:start + size])


def current_universe_variant_ids(cursor: Any) -> list[int]:
    cursor.execute(
        """
        SELECT m.variant_id
        FROM market_universe_member m
        INNER JOIN market_universe_lock l
          ON l.id=m.universe_lock_id AND l.is_current=1
        """
    )
    return sorted({int(row["variant_id"]) for row in cursor.fetchall()})


def load_strict_identities(
    cursor: Any, *, source: str, variant_ids: Sequence[int]
) -> dict[int, dict[str, str]]:
    """variant -> {normalised id: the id EXACTLY as the identity view stores it}.

    The stored form is what the quote must carry.  The eligibility view joins
    `si.external_entity_id = q.source_external_entity_id` with a plain equality
    (current_quote_revision.py:423-426), so a normalised-but-not-identical id
    mints a revision that no selection query can ever see.
    """

    bound: dict[int, dict[str, str]] = {}
    for chunk in _chunks(list(variant_ids)):
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT variant_id, external_entity_id
            FROM operator_strict_source_identity
            WHERE source_code=%s AND variant_id IN ({marks})
            """,
            (source, *chunk),
        )
        for row in cursor.fetchall():
            raw = str(row["external_entity_id"])
            bound.setdefault(int(row["variant_id"]), {})[
                normalize_external_entity_id(raw)
            ] = raw
    return bound


def load_candidate_sales(
    cursor: Any,
    *,
    source: str,
    variant_ids: Sequence[int],
    quarantined_sale_ids: set[int] | None = None,
) -> dict[int, list[dict[str, Any]]]:
    """Eligible PSA10 sales per variant, newest-first is NOT assumed.

    Bound to operator_strict_source_identity on purpose.  Variant 1279 carries
    two SNKRDUNK item ids and its quote is bound to the older one while the
    $1,374-$1,697 sales sit on the other; without the bind those sales would
    become the price of a card they do not belong to.
    """

    quarantined = set(quarantined_sale_ids or ())
    bound = load_strict_identities(cursor, source=source, variant_ids=variant_ids)
    wanted = [vid for vid in variant_ids if bound.get(vid)]
    out: dict[int, list[dict[str, Any]]] = {}
    grade_marks = ",".join(["%s"] * len(GRADE_LABELS))
    coverage_marks = ",".join(["%s"] * len(COVERAGE_STATUSES))
    stamp_marks = ",".join(["%s"] * len(TIMESTAMP_QUALITIES))
    for chunk in _chunks(wanted):
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT s.id, s.variant_id, s.external_entity_id, s.sold_at,
                   s.unit_price_usd, s.quantity, s.transaction_fingerprint,
                   s.listing_item_id, s.listing_url, s.listing_title
            FROM market_sale_observation s
            WHERE s.source_code=%s
              AND s.variant_id IN ({marks})
              AND UPPER(s.grader_code)='PSA'
              AND UPPER(REPLACE(s.grade_label,' ','')) IN ({grade_marks})
              AND LOWER(s.coverage_status) IN ({coverage_marks})
              AND LOWER(s.timestamp_quality) IN ({stamp_marks})
              AND s.unit_price_usd>0
              AND s.quantity>0
              AND s.transaction_fingerprint IS NOT NULL
              AND s.transaction_fingerprint<>''
              AND s.sold_at IS NOT NULL
            """,
            (
                source,
                *chunk,
                *GRADE_LABELS,
                *COVERAGE_STATUSES,
                *TIMESTAMP_QUALITIES,
            ),
        )
        for row in cursor.fetchall():
            variant_id = int(row["variant_id"])
            external = bound.get(variant_id, {}).get(
                normalize_external_entity_id(row["external_entity_id"])
            )
            if external is None:
                continue
            if int(row["id"]) in quarantined:
                continue
            out.setdefault(variant_id, []).append({
                "saleObservationId": int(row["id"]),
                "variantId": variant_id,
                "externalEntityId": external,
                "soldAt": row["sold_at"],
                "unitPriceUsd": row["unit_price_usd"],
                "quantity": int(row["quantity"] or 0),
                "transactionFingerprint": str(row["transaction_fingerprint"]),
                "listingItemId": row.get("listing_item_id"),
                "listingUrl": row.get("listing_url"),
                "listingTitle": row.get("listing_title"),
            })
    # One fingerprint counts once per variant; a duplicated landing row must not
    # deepen the prior window and drag the median.
    for variant_id, sales in out.items():
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in sorted(sales, key=lambda s: int(s["saleObservationId"])):
            fingerprint = item["transactionFingerprint"]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(item)
        out[variant_id] = unique
    return out


def build_payload(
    *,
    source: str,
    storage_source: str,
    variant_id: int,
    chosen: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    """§3.6 evidence body.  market_current_quote_revision has no evidence
    column; payload_sha256 pointing at market_source_observation.payload_json
    IS the quote's evidence, so everything a reader needs to re-judge the price
    lives here.

    The harvest clock is deliberately NOT in this payload.  payload_sha256 is
    part of market_source_observation's unique key, so a wall-clock field would
    write a fresh evidence row every night for an unchanged sale and break
    replay idempotency.  Freshness is carried by checked_at / observed_at.
    """

    return {
        "contract": CONTRACT,
        "method": METHOD,
        "source": "market_sale_observation",
        "field": None,
        "sourceCode": source,
        "storageSourceCode": storage_source,
        "variantId": int(variant_id),
        "saleObservationId": int(chosen["saleObservationId"]),
        "transactionFingerprint": str(chosen["transactionFingerprint"]),
        "soldAt": _as_naive(chosen["soldAt"]).isoformat(sep=" "),
        "unitPriceUsd": price_text(chosen["unitPriceUsd"]),
        "quantity": int(chosen["quantity"]),
        "externalEntityId": str(chosen["externalEntityId"]),
        "listingItemId": _text_or_none(chosen.get("listingItemId")),
        "listingUrl": _text_or_none(chosen.get("listingUrl")),
        "listingTitle": _text_or_none(chosen.get("listingTitle")),
        "outlierGuard": evidence["outlierGuard"],
        "rejectedCandidates": evidence["rejectedCandidates"],
        "salesScanned": int(evidence["salesScanned"]),
    }


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _as_naive(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    text = str(value).replace("Z", "+00:00").replace(" ", "T")
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo is None else parsed.astimezone(timezone.utc).replace(tzinfo=None)


def plan(
    cursor: Any,
    *,
    source: str,
    variant_ids: Sequence[int],
    as_of: datetime,
    quarantine_receipt: Path | None = None,
) -> dict[str, Any]:
    """Decide one quote per variant.  Reads only; writes nothing.

    Deliberately independent of market_source_registry and of
    market_quote_route_policy: a dry-run has to be runnable before the
    registry/route migration is applied, and minting is not where routing is
    decided.  Which of the two lanes wins for a card is the route policy's job
    (sales priority 10 beats chart priority 90); this module's job is to make
    sure the sale lane HAS a quote to offer wherever a real sale exists.
    """

    storage_source = SALE_LANE_BY_SOURCE.get(source)
    if not storage_source:
        raise ValueError(
            f"{source!r} has no mintable sale lane; known lanes: {SALE_LANE_BY_SOURCE}"
        )
    # Every lane reads the receipt (2026-09-25).  The 058 history view drops a
    # quarantined sale of ANY source, so reading it only for pricecharting let
    # a quarantined SNKRDUNK sale (v126's JPY 1,999,999 placeholder, sale
    # 2452342) leave the history and the window anchors yet still be minted as
    # the headline price.  scripts/test_psa10_latest_sale_quote.py section 3b.
    quarantined = load_title_quarantine(quarantine_receipt or QUARANTINE_RECEIPT)
    sales_by_variant = load_candidate_sales(
        cursor,
        source=source,
        variant_ids=variant_ids,
        quarantined_sale_ids=quarantined,
    )
    rows: list[dict[str, Any]] = []
    rejected_sales: list[dict[str, Any]] = []
    walkbacks: list[dict[str, Any]] = []
    no_eligible_sale: list[int] = []
    sales_scanned = 0
    for variant_id in variant_ids:
        sales = sales_by_variant.get(variant_id) or []
        sales_scanned += len(sales)
        chosen, evidence = select_latest_sale(sales)
        for item in evidence["rejectedCandidates"]:
            rejected_sales.append({"variantId": variant_id, **item})
        if chosen is None:
            no_eligible_sale.append(variant_id)
            continue
        if evidence["walkbacks"]:
            walkbacks.append({
                "variantId": variant_id,
                "walkbacks": int(evidence["walkbacks"]),
                "chosenSaleObservationId": int(chosen["saleObservationId"]),
            })
        payload = build_payload(
            source=source,
            storage_source=storage_source,
            variant_id=variant_id,
            chosen=chosen,
            evidence=evidence,
        )
        rows.append({
            "variantId": variant_id,
            "sourceCode": source,
            "storageSourceCode": storage_source,
            "externalEntityId": str(chosen["externalEntityId"]),
            "saleObservationId": int(chosen["saleObservationId"]),
            "priceUsd": price_text(chosen["unitPriceUsd"]),
            "soldAt": _as_naive(chosen["soldAt"]),
            "observedDate": _as_naive(chosen["soldAt"]).date(),
            "checkedAt": as_of,
            "ungated": bool((evidence["outlierGuard"] or {}).get("ungated")),
            "payload": payload,
            "payloadSha256": payload_sha256(payload),
        })
    plan_sha256 = hashlib.sha256(
        canonical_payload_bytes({
            "contract": CONTRACT,
            "source": source,
            "rows": [
                {
                    "variantId": row["variantId"],
                    "saleObservationId": row["saleObservationId"],
                    "payloadSha256": row["payloadSha256"],
                }
                for row in rows
            ],
        })
    ).hexdigest()
    return {
        "contract": CONTRACT,
        "source": source,
        "storageSourceCode": storage_source,
        "asOf": as_of,
        "variantsPlanned": len(variant_ids),
        "salesScanned": sales_scanned,
        "rows": rows,
        "rejectedSales": rejected_sales,
        "fallbackWalkbacks": walkbacks,
        "noEligibleSale": no_eligible_sale,
        "planSha256": plan_sha256,
    }


def _open_run(cursor: Any, *, source: str, storage_source: str, plan_sha256: str,
              as_of: datetime, count: int) -> int:
    # market_ingest_run.run_key is CHAR(64) and test_price_lane_contracts walks
    # it with a LIKE prefix to tell canonical lanes apart from the ad-hoc ones
    # that poisoned market_price_observation in 2026-08.  Keep the lane legible
    # in the key and spend the rest on the plan lineage.
    digest = hashlib.sha256(
        f"psa10_latest_sale|{source}|{plan_sha256}|{as_of.isoformat()}".encode("utf-8")
    ).hexdigest()
    run_key = (RUN_KEY_PREFIX + digest)[:64]
    cursor.execute(
        """
        INSERT INTO market_ingest_run
          (run_key, source_code, ingest_mode, effective_at, payload_sha256,
           manifest_sha256, status, observed_count, started_at)
        VALUES (%s,%s,'incremental',%s,%s,%s,'running',%s,%s)
        """,
        (run_key, storage_source, as_of, plan_sha256, plan_sha256, count, as_of),
    )
    run_id = int(cursor.lastrowid or 0)
    if run_id <= 0:
        raise RuntimeError("sale quote ingest run insert returned no id")
    return run_id


def _v2_business_window() -> tuple[datetime, datetime] | None:
    """The V2 run's business window (naive UTC), or None outside the chain."""

    from daily_chain_v2_db import business_window_from_env

    return business_window_from_env()


def same_day_standing_quotes(
    cursor: Any,
    rows: Sequence[Mapping[str, Any]],
    *,
    storage_source: str,
    window: tuple[datetime, datetime],
) -> set[int]:
    """Variant ids whose planned quote already stands, unchanged, in the window.

    A06/A07 2026-08-23 [KNOWN, DB probe]: every rehearsal of the same business
    day re-minted 1,753 sale quotes (+1,618 bootstrap revisions on top of
    them) although not one chosen sale had moved, because the harvest clock
    (as_of) is part of the quote lineage and the evidence rows were re-stamped
    to it.  The V2 freshness gates need ONE capture per business day
    (checked_at inside the window); a quote that already stands inside this
    window with the same payload_sha256 is that capture.  Both halves must
    stand -- the history point (market_price_observation: same sale day and
    payload, metric_status ready, effective_at inside the window) and the
    quote revision -- otherwise the row is minted exactly as before.  Outside
    the chain (no business date) nothing is skipped.
    """

    start, end = window
    by_variant: dict[int, Mapping[str, Any]] = {
        int(row["variantId"]): row for row in rows
    }
    standing_prices: set[int] = set()
    standing_quotes: set[int] = set()
    for chunk in _chunks(sorted(by_variant)):
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(
            "SELECT variant_id, observed_date, payload_sha256, effective_at, metric_status"
            " FROM market_price_observation"
            f" WHERE source_code=%s AND variant_id IN ({marks})",
            (storage_source, *chunk),
        )
        for found in cursor.fetchall() or ():
            row = by_variant.get(int(found["variant_id"]))
            effective_at = found.get("effective_at")
            if (
                row is not None
                and str(found.get("metric_status")) == "ready"
                and str(found.get("payload_sha256") or "").casefold()
                == str(row["payloadSha256"]).casefold()
                and str(found.get("observed_date"))[:10] == str(row["observedDate"])[:10]
                and isinstance(effective_at, datetime)
                and start <= effective_at < end
            ):
                standing_prices.add(int(found["variant_id"]))
        cursor.execute(
            "SELECT variant_id, source_external_entity_id, payload_sha256"
            " FROM market_current_quote_revision"
            " WHERE source_code=%s AND checked_at>=%s AND checked_at<%s"
            f" AND variant_id IN ({marks})",
            (storage_source, start, end, *chunk),
        )
        for found in cursor.fetchall() or ():
            row = by_variant.get(int(found["variant_id"]))
            if (
                row is not None
                and str(found.get("payload_sha256") or "").casefold()
                == str(row["payloadSha256"]).casefold()
                and str(found.get("source_external_entity_id")) == str(row["externalEntityId"])
            ):
                standing_quotes.add(int(found["variant_id"]))
    return standing_prices & standing_quotes


def materialize(connection: Any, plan_doc: Mapping[str, Any]) -> dict[str, int]:
    """One transaction: evidence -> price observation -> quote revision.

    The market_price_observation row is the sale lane's history point; the
    chart lanes keep writing theirs, untouched, because the charts still need
    the series (owner: landing is never filtered).
    """

    rows = list(plan_doc["rows"])
    as_of = plan_doc["asOf"]
    storage_source = str(plan_doc["storageSourceCode"])
    minted = 0
    standing: set[int] = set()
    with connection.cursor() as cursor:
        cursor.execute("SET SESSION max_execution_time=60000")
        window = _v2_business_window()
        if rows and window is not None:
            standing = same_day_standing_quotes(
                cursor, rows, storage_source=storage_source, window=window
            )
        pending = [row for row in rows if int(row["variantId"]) not in standing]
        if not pending:
            connection.commit()
            return {"quotesMinted": 0, "quotesStanding": len(standing), "runId": 0}
        run_id = _open_run(
            cursor,
            source=str(plan_doc["source"]),
            storage_source=storage_source,
            plan_sha256=str(plan_doc["planSha256"]),
            as_of=as_of,
            count=len(pending),
        )
        for row in pending:
            payload_json = canonical_payload_bytes(row["payload"]).decode("utf-8")
            cursor.execute(
                """
                INSERT INTO market_source_observation
                  (run_id, source_code, external_entity_id, observation_kind,
                   effective_at, observed_date, payload_sha256, payload_json,
                   observed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  id=LAST_INSERT_ID(id), run_id=VALUES(run_id),
                  effective_at=VALUES(effective_at),
                  payload_json=VALUES(payload_json),
                  observed_at=VALUES(observed_at)
                """,
                (
                    run_id, storage_source, row["externalEntityId"],
                    SALE_QUOTE_OBSERVATION_KIND, as_of, row["observedDate"],
                    row["payloadSha256"], payload_json, as_of,
                ),
            )
            source_observation_id = int(cursor.lastrowid or 0)
            if source_observation_id <= 0:
                raise RuntimeError("sale source observation upsert returned no id")
            cursor.execute(
                """
                INSERT INTO market_price_observation
                  (run_id,variant_id,source_code,source_external_entity_id,
                   source_observation_id,observed_date,effective_at,price_usd,
                   native_price,native_currency,source_priority,metric_status,
                   payload_sha256)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  last_run_id=VALUES(run_id),
                  restamp_count=restamp_count+1,
                  source_external_entity_id=VALUES(source_external_entity_id),
                  source_observation_id=VALUES(source_observation_id),
                  effective_at=VALUES(effective_at),
                  price_usd=VALUES(price_usd),
                  source_priority=VALUES(source_priority),
                  metric_status=CASE WHEN market_price_observation.metric_status='quarantined'
                                     THEN 'quarantined' ELSE VALUES(metric_status) END,
                  payload_sha256=VALUES(payload_sha256)
                """,
                (
                    run_id, int(row["variantId"]), storage_source,
                    row["externalEntityId"], source_observation_id,
                    row["observedDate"], as_of, row["priceUsd"],
                    SALE_QUOTE_SOURCE_PRIORITY, "ready", row["payloadSha256"],
                ),
            )
            cursor.execute(
                """
                SELECT id FROM market_price_observation
                WHERE variant_id=%s AND source_code=%s AND observed_date=%s
                LIMIT 1
                """,
                (int(row["variantId"]), storage_source, row["observedDate"]),
            )
            price_row = cursor.fetchone() or {}
            insert_quote_revision(
                cursor,
                variant_id=int(row["variantId"]),
                source_code=storage_source,
                source_external_entity_id=row["externalEntityId"],
                price_usd=row["priceUsd"],
                # The sale day is what the card page publishes; the harvest
                # clock is what freshness gates read.  Never the same value.
                source_period_at=row["observedDate"],
                checked_at=as_of,
                payload_sha256=row["payloadSha256"],
                source_observation_id=source_observation_id,
                market_price_observation_id=int(price_row["id"]) if price_row.get("id") else None,
                run_id=run_id,
                purpose=QUOTE_MINT_PURPOSE_LIVE,
            )
            minted += 1
        cursor.execute(
            "UPDATE market_ingest_run SET status='completed',accepted_count=%s,"
            "completed_at=%s WHERE id=%s",
            (minted, datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
    connection.commit()
    return {"quotesMinted": minted, "quotesStanding": len(standing), "runId": run_id}


def write_receipt(
    plan_doc: Mapping[str, Any],
    *,
    run_id: int,
    quotes_minted: int,
    path: Path | None = None,
    dry_run: bool = False,
    quotes_standing: int = 0,
) -> Path:
    """§3.7.  `rejectedSales` is always present, even empty.

    A gate that discards its rejections cannot tell "nothing came close" apart
    from "the filter never ran", and that ambiguity is how a dead filter
    survives for weeks.
    """

    as_of: datetime = plan_doc["asOf"]
    stamp = as_of.strftime("%Y%m%dT%H%M%S%fZ")
    target = path or (
        AUDIT_DIR / f"psa10_latest_sale_quote_{plan_doc['source']}_{stamp}.json"
    )
    doc = {
        "contract": RECEIPT_CONTRACT,
        "quoteContract": CONTRACT,
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "asOf": as_of.isoformat(sep=" "),
        "businessDate": as_of.date().isoformat(),
        "runId": int(run_id),
        "dryRun": bool(dry_run),
        "source": plan_doc["source"],
        "storageSourceCode": plan_doc["storageSourceCode"],
        "variantsPlanned": int(plan_doc["variantsPlanned"]),
        "salesScanned": int(plan_doc["salesScanned"]),
        "quotesMinted": int(quotes_minted),
        # planned quotes that already stood, unchanged, inside the V2 business
        # window (always 0 outside the chain and on a dry run)
        "quotesStanding": int(quotes_standing),
        "ungated": sum(1 for row in plan_doc["rows"] if row["ungated"]),
        "rejectedSales": list(plan_doc["rejectedSales"]),
        "fallbackWalkbacks": list(plan_doc["fallbackWalkbacks"]),
        "noEligibleSale": list(plan_doc["noEligibleSale"]),
        "planSha256": plan_doc["planSha256"],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


# --------------------------------------------------------------------------
# dry-run comparison against the quote the board is publishing today
# --------------------------------------------------------------------------
def load_current_chart_quotes(
    cursor: Any, *, source: str, variant_ids: Sequence[int]
) -> dict[int, Decimal]:
    """Latest existing quote per variant under the parent's chart codes."""

    chart_codes = [
        code
        for code in (source, *QUOTE_STORAGE_ALIASES.get(source, ()))
        if code not in set(mintable_quote_storage_source_codes())
    ]
    code_marks = ",".join(["%s"] * len(chart_codes))
    out: dict[int, Decimal] = {}
    for chunk in _chunks(list(variant_ids)):
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT q.variant_id, q.price_usd
            FROM market_current_quote_revision q
            INNER JOIN (
              SELECT variant_id, MAX(id) AS id
              FROM market_current_quote_revision
              WHERE source_code IN ({code_marks}) AND variant_id IN ({marks})
              GROUP BY variant_id
            ) latest ON latest.id=q.id
            """,
            (*chart_codes, *chunk),
        )
        for row in cursor.fetchall():
            out[int(row["variant_id"])] = Decimal(str(row["price_usd"]))
    return out


def load_variant_names(cursor: Any, variant_ids: Sequence[int]) -> dict[int, str]:
    out: dict[int, str] = {}
    for chunk in _chunks(list(variant_ids)):
        marks = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"SELECT id, canonical_name FROM catalog_variant WHERE id IN ({marks})",
            tuple(chunk),
        )
        for row in cursor.fetchall():
            out[int(row["id"])] = str(row["canonical_name"] or "")
    return out


def _percentiles(values: Sequence[Decimal]) -> dict[str, str]:
    if not values:
        return {}
    ordered = sorted(values)

    def pick(pct: float) -> str:
        index = min(len(ordered) - 1, max(0, int(round(pct / 100 * (len(ordered) - 1)))))
        return format(ordered[index].quantize(Decimal("0.001")), "f")

    return {f"p{int(p)}": pick(p) for p in (1, 10, 25, 50, 75, 90, 99)}


def compare(plan_doc: Mapping[str, Any], current: Mapping[int, Decimal]) -> dict[str, Any]:
    ratios: list[Decimal] = []
    movers: list[dict[str, Any]] = []
    for row in plan_doc["rows"]:
        old = current.get(int(row["variantId"]))
        new = Decimal(row["priceUsd"])
        if old is None or old <= 0:
            continue
        ratio = (new / old).quantize(Decimal("0.0001"))
        ratios.append(ratio)
        movers.append({
            "variantId": int(row["variantId"]),
            "oldUsd": format(old.quantize(Decimal("0.01")), "f"),
            "newUsd": format(new.quantize(Decimal("0.01")), "f"),
            "ratio": format(ratio, "f"),
            "soldAt": row["soldAt"].isoformat(sep=" "),
            "ungated": row["ungated"],
        })
    movers.sort(key=lambda item: Decimal(item["ratio"]))
    return {
        "compared": len(ratios),
        "ratioPercentiles": _percentiles(ratios),
        "moversAbove1_5x": sum(1 for r in ratios if r > Decimal("1.5")),
        "moversBelow0_667x": sum(1 for r in ratios if r < Decimal("0.667")),
        "medianRatio": format(median(sorted(ratios)), "f") if ratios else None,
        "top10Down": movers[:10],
        "top10Up": list(reversed(movers[-10:])),
    }


def print_distribution(plan_doc: Mapping[str, Any], comparison: Mapping[str, Any],
                       names: Mapping[int, str]) -> None:
    print(
        f"variants={plan_doc['variantsPlanned']}"
        f"  minted={len(plan_doc['rows'])}"
        f"  ungated={sum(1 for row in plan_doc['rows'] if row['ungated'])}"
        f"  rejectedSales={len(plan_doc['rejectedSales'])}"
        f"  walkbacks={len(plan_doc['fallbackWalkbacks'])}"
        f"  noEligibleSale={len(plan_doc['noEligibleSale'])}"
        f"  salesScanned={plan_doc['salesScanned']}"
    )
    pct = comparison.get("ratioPercentiles") or {}
    print(
        "ratio(new/old): "
        + " ".join(f"{key} {value}" for key, value in pct.items())
        + f"   compared={comparison.get('compared')}"
    )
    print(
        f"movers>1.5x: {comparison.get('moversAbove1_5x')}"
        f"   movers<0.667x: {comparison.get('moversBelow0_667x')}"
    )
    for label, key in (("top10 up", "top10Up"), ("top10 down", "top10Down")):
        print(f"-- {label} --")
        for item in comparison.get(key) or ():
            print(
                f"  v{item['variantId']:>5}  {item['oldUsd']:>12} -> {item['newUsd']:>12}"
                f"  x{item['ratio']:<8} sold {item['soldAt'][:10]}"
                f"  {names.get(item['variantId'], '')[:60]}"
            )
    if plan_doc["noEligibleSale"]:
        print("-- noEligibleSale --")
        for variant_id in plan_doc["noEligibleSale"]:
            print(f"  v{variant_id}  {names.get(variant_id, '')[:70]}")


def resolve_variant_ids(cursor: Any, spec: str) -> list[int]:
    text = str(spec or "").strip()
    if text.casefold() == "all":
        return current_universe_variant_ids(cursor)
    path = Path(text)
    if path.is_file():
        doc = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(doc, Mapping):
            doc = doc.get("variantIds") or doc.get("rows") or []
        ids = []
        for item in doc:
            ids.append(int(item["variantId"]) if isinstance(item, Mapping) else int(item))
        return sorted(set(ids))
    return sorted({int(part) for part in text.replace(" ", "").split(",") if part})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, choices=sorted(SALE_LANE_BY_SOURCE))
    parser.add_argument("--variants", default="all",
                        help="'all', a comma list of variant ids, or a JSON file path")
    parser.add_argument("--dry-run", action="store_true", help="read-only; zero DB writes")
    parser.add_argument("--write", action="store_true", help="mint quotes")
    parser.add_argument("--report", default=None, help="receipt path")
    args = parser.parse_args(argv)

    if args.write == args.dry_run:
        parser.error("pass exactly one of --write / --dry-run")

    from rebuild_036 import DAILY_CREDENTIALS_ENV, connect  # noqa: E402

    as_of = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    connection = connect(DAILY_CREDENTIALS_ENV)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time=60000")
            variant_ids = resolve_variant_ids(cursor, args.variants)
            plan_doc = plan(
                cursor, source=args.source, variant_ids=variant_ids, as_of=as_of
            )
            comparison: dict[str, Any] = {}
            names: dict[int, str] = {}
            if args.dry_run:
                current = load_current_chart_quotes(
                    cursor, source=args.source, variant_ids=variant_ids
                )
                comparison = compare(plan_doc, current)
                interesting = [int(v) for v in plan_doc["noEligibleSale"]]
                for key in ("top10Up", "top10Down"):
                    interesting += [int(item["variantId"]) for item in comparison[key]]
                names = load_variant_names(cursor, sorted(set(interesting)))
        run_id = 0
        quotes_minted = 0
        quotes_standing = 0
        if args.write:
            result = materialize(connection, plan_doc)
            run_id = int(result["runId"])
            quotes_minted = int(result["quotesMinted"])
            quotes_standing = int(result.get("quotesStanding", 0))
        else:
            # A dry run must not leave a transaction open on the writer.
            connection.rollback()
            quotes_minted = len(plan_doc["rows"])
            print_distribution(plan_doc, comparison, names)
    finally:
        connection.close()

    receipt = write_receipt(
        plan_doc,
        run_id=run_id,
        quotes_minted=quotes_minted,
        path=Path(args.report) if args.report else None,
        dry_run=bool(args.dry_run),
        quotes_standing=quotes_standing,
    )
    if args.dry_run:
        summary = {
            "receipt": str(receipt),
            "planSha256": plan_doc["planSha256"],
            **{key: comparison.get(key) for key in
               ("compared", "ratioPercentiles", "moversAbove1_5x", "moversBelow0_667x",
                "medianRatio", "top10Up", "top10Down")},
        }
        Path(str(receipt)).write_text(
            json.dumps(
                {
                    **json.loads(Path(str(receipt)).read_text(encoding="utf-8")),
                    "comparison": summary,
                },
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    print(f"receipt: {receipt}")
    print(
        f"source={args.source} variants={plan_doc['variantsPlanned']}"
        f" minted={quotes_minted} standing={quotes_standing} runId={run_id}"
        f" noEligibleSale={len(plan_doc['noEligibleSale'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
