#!/usr/bin/env python3
"""Persist daily market ranks, coverage evidence and candidate alert episodes.

The evaluator is deliberately scoped: without a complete discovery manifest it
labels the result ``observed``, never ``certified``. Missing/stale history stays
``accumulating`` or ``unavailable`` and cannot open or resolve an alert.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ranking_derivation import market_cap_usd as formula_market_cap_usd

POLICY_VERSION = "candidate-v7-mean2xpc"
INDEX_CODE = "tcg-combined"
INDEX_VERSION = "psa10-v3-complete"
DEFAULT_DISCOVERY = Path(__file__).resolve().parents[1] / "data" / "runtime" / "private-source-map" / "discovery-radar.json"
# Prefer liquid JP/US sources; any source still usable (default priority 100).
PRICE_SOURCE_PRIORITY = {
    "pricecharting": 5,
    "snk_psa10": 10,
    "snk": 10,
    "snkrdunk": 10,
    # tcgpricelookup removed 2026-07-30 (operator purge — wrong printing slugs)
    "g10_kline": 20,
    "g10": 20,
    "ebay": 40,
}
# Align with pipelines/canonical_db_qc.py EXACT_PRICE_SOURCES (DADDY 2026-07-30):
# REAL market prices only: SNK + eBay (G10-path / PC).
# DADDY 2026-07-30: g10_kline is G10-internal invented series — NEVER for mcap/FE.
# tcgpricelookup remains purged/risky — never trusted.
TRUSTED_PRICE_SOURCES = frozenset(
    {"pricecharting", "snk_psa10", "snk", "snkrdunk", "ebay"}  # never g10_kline (invented)
)
RISKY_PRICE_SOURCES = frozenset({"g10", "tcgpricelookup"})
# Reject risky quote if it is more than this multiple of trusted median.
PRICE_OUTLIER_RATIO = 5.0
POPULATION_SOURCE_PRIORITY = {"gemrate": 10, "g10": 20}
PRE_ENTRY_POPULATION_MINIMUM = 971
PRE_ENTRY_POPULATION_MAXIMUM = 999
CURRENT_POPULATION_MAX_GAP_DAYS = 2
# Operator 2026-07-29: FE Top100 first — price may be stale for membership.
# Freshness is a preference (pick_observation still prefers latest observed_date),
# not a hard kill switch that empties /one-piece to ~27 cards.
PRICE_MAX_GAP_DAYS_FOR_RANK = 90
PRICE_HISTORY_LOOKBACK_DAYS = 120
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_CONFIG = ROOT / "data" / "runtime" / "config" / "backend.env"


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load_db_env(path: Path = DEFAULT_DB_CONFIG) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def add_connection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CARDZ_DB_PORT", "3308")))
    parser.add_argument("--database", default=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"))
    parser.add_argument("--user", default=os.environ.get("CARDZ_DB_USER", "cardz"))
    parser.add_argument("--password")


@dataclass(frozen=True)
class CandidateSnapshot:
    variant_id: int
    candidate_key: str
    member_role: str
    shadow_rank: int | None
    eligible_rank: int | None
    reference_price_usd: float | None
    psa10_population: int | None
    market_cap_usd: float | None
    cutoff_ratio: float | None
    projected_pop1000_ratio: float | None
    change_1d_pct: float | None
    change_7d_pct: float | None
    change_30d_pct: float | None
    population_change_7d_pct: float | None
    population_change_30d_pct: float | None
    metric_status: str


@dataclass(frozen=True)
class AlertSignal:
    alert_type: str
    severity: str
    immediate: bool
    evidence: Mapping[str, Any]


def percent_change(current: float | int | None, previous: float | int | None) -> float | None:
    if current is None or previous is None or float(previous) <= 0:
        return None
    return (float(current) / float(previous) - 1.0) * 100.0


def alert_signals(current: CandidateSnapshot, previous: CandidateSnapshot | None) -> list[AlertSignal]:
    if current.metric_status != "ready" or current.market_cap_usd is None:
        return []
    signals: list[AlertSignal] = []
    previous_rank = previous.eligible_rank if previous else None
    if previous is not None and current.eligible_rank is not None and current.eligible_rank <= 100 and (
        previous_rank is None or previous_rank > 100
    ):
        signals.append(
            AlertSignal(
                "entered_top100",
                "critical",
                True,
                {"currentRank": current.eligible_rank, "previousRank": previous_rank},
            )
        )

    price_breakout = (current.change_1d_pct or float("-inf")) >= 10 or (
        current.change_7d_pct or float("-inf")
    ) >= 20
    previous_shadow = previous.shadow_rank if previous else None
    rank_gain = (
        previous_shadow - current.shadow_rank
        if previous_shadow is not None and current.shadow_rank is not None
        else None
    )
    outside_top100 = current.eligible_rank is None or current.eligible_rank > 100
    near_cutoff = outside_top100 and (
        (current.eligible_rank is not None and 101 <= current.eligible_rank <= 130)
        or (current.cutoff_ratio is not None and current.cutoff_ratio >= 0.8)
    )
    if near_cutoff and (price_breakout or (rank_gain is not None and rank_gain >= 10)):
        immediate = (current.change_1d_pct or float("-inf")) >= 25
        signals.append(
            AlertSignal(
                "near_top100",
                "high",
                immediate,
                {
                    "shadowRank": current.shadow_rank,
                    "eligibleRank": current.eligible_rank,
                    "cutoffRatio": current.cutoff_ratio,
                    "rankGain": rank_gain,
                    "change1dPct": current.change_1d_pct,
                    "change7dPct": current.change_7d_pct,
                },
            )
        )

    population = current.psa10_population
    if (
        current.member_role == "monitoring"
        and population is not None
        and PRE_ENTRY_POPULATION_MINIMUM <= population <= PRE_ENTRY_POPULATION_MAXIMUM
        and (
        current.projected_pop1000_ratio is not None and current.projected_pop1000_ratio >= 0.8
        )
    ):
        pop_breakout = current.population_change_7d_pct is not None and current.population_change_7d_pct >= 10
        price_momentum = current.change_7d_pct is not None and current.change_7d_pct >= 20
        if pop_breakout or price_momentum:
            immediate = (
                current.projected_pop1000_ratio >= 1.2
                and current.change_1d_pct is not None
                and current.change_1d_pct >= 20
            )
            signals.append(
                AlertSignal(
                    "pre1000_breakout",
                    "high",
                    immediate,
                    {
                        "population": population,
                        "projectedPop1000Ratio": current.projected_pop1000_ratio,
                        "populationChange7dPct": current.population_change_7d_pct,
                        "change7dPct": current.change_7d_pct,
                    },
                )
            )
    return signals


def _observation_date(row: Mapping[str, Any]) -> date:
    observed = row["observed_date"]
    if isinstance(observed, datetime):
        return observed.date()
    return observed


def pick_observation(
    rows: Sequence[Mapping[str, Any]],
    target: date,
    source_priority: Mapping[str, int],
    *,
    max_gap_days: int,
    value_key: str,
) -> Mapping[str, Any] | None:
    usable = []
    for row in rows:
        observed = _observation_date(row)
        if observed > target or (target - observed).days > max_gap_days:
            continue
        value = row.get(value_key)
        if value is None:
            continue
        if row.get("estimated"):
            continue
        usable.append(row)
    if not usable:
        return None
    return min(
        usable,
        key=lambda row: (
            -(_observation_date(row).toordinal()),
            source_priority.get(str(row.get("source_code") or "").casefold(), 100),
            -int(row.get("id") or 0),
        ),
    )


def _identity_exact_codes(identities: Sequence[Mapping[str, Any]] | None) -> set[str]:
    """source_codes with match_status=exact only (reject derived/candidate)."""
    if not identities:
        return set()
    out: set[str] = set()
    for row in identities:
        if str(row.get("match_status") or "").casefold() != "exact":
            continue
        out.add(str(row.get("source_code") or "").casefold())
    return out


def pick_rank_price(
    rows: Sequence[Mapping[str, Any]],
    target: date,
    source_priority: Mapping[str, int],
    *,
    max_gap_days: int,
    value_key: str = "price_usd",
    outlier_ratio: float = PRICE_OUTLIER_RATIO,
    identities: Sequence[Mapping[str, Any]] | None = None,
) -> Mapping[str, Any] | None:
    """Pick ranking reference price aligned with QC current exact PSA10.

    Canonical QC exact sources: pricecharting, ebay, and snk_psa10/snk/snkrdunk
    only (not TPL, not g10_kline).
    ebay requires catalog_source_identity match_status=exact (G10-path or PC).
    SNK family requires snkrdunk/snk exact identity.
    """

    del outlier_ratio  # API-stable; risky override removed to match QC exact price.

    exact_ids = _identity_exact_codes(identities)

    def source_allowed(src: str) -> bool:
        if src not in TRUSTED_PRICE_SOURCES:
            return True  # non-trusted handled below as fallback
        if src in {"snk_psa10", "snk", "snkrdunk"}:
            return bool(exact_ids & {"snkrdunk", "snk", "snk_psa10"})
        if src == "ebay":
            # Exact eBay medians can be G10-altxyz bound or PriceCharting-product
            # bound; both paths are rechecked by their writer before this stage.
            return bool(exact_ids & {"ebay", "pricecharting"})
        if src == "pricecharting":
            return "pricecharting" in exact_ids
        return False

    usable: list[Mapping[str, Any]] = []
    for row in rows:
        src = str(row.get("source_code") or "").casefold()
        if src in TRUSTED_PRICE_SOURCES and identities is not None and not source_allowed(src):
            continue
        # Trusted exact quotes are bulk-stamped with fresh effective_at while
        # observed_date may be historical market days. Gate them on effective_at
        # so materialization matches QC current exact PSA10 price selection.
        if src in TRUSTED_PRICE_SOURCES:
            effective = row.get("effective_at")
            if isinstance(effective, datetime):
                gate_day = effective.date()
            else:
                gate_day = _observation_date(row)
        else:
            gate_day = _observation_date(row)
        if gate_day > target or (target - gate_day).days > max_gap_days:
            continue
        value = row.get(value_key)
        if value is None or float(value) <= 0:
            continue
        if row.get("estimated"):
            continue
        usable.append(row)
    if not usable:
        return None

    def rank_key(row: Mapping[str, Any]) -> tuple[int, int, int]:
        src = str(row.get("source_code") or "").casefold()
        return (
            -(_observation_date(row).toordinal()),
            source_priority.get(src, 100),
            -int(row.get("id") or 0),
        )

    def trusted_key(row: Mapping[str, Any]) -> tuple[float, int, int]:
        # Align with canonical_db_qc current-price key: max(effective_at, -source_priority, id).
        effective = row.get("effective_at")
        if isinstance(effective, datetime):
            if effective.tzinfo is None:
                effective = effective.replace(tzinfo=timezone.utc)
            effective_ord = effective.timestamp()
        else:
            effective_ord = float(_observation_date(row).toordinal())
        return (
            -effective_ord,
            int(row.get("source_priority") or 100),
            -int(row.get("id") or 0),
        )

    trusted = [
        row
        for row in usable
        if str(row.get("source_code") or "").casefold() in TRUSTED_PRICE_SOURCES
    ]
    if not trusted:
        # Fail-closed: never fall back to g10_kline / invent (daddy 2026-07-30).
        return None

    # Must mirror canonical_db_qc.select_display_exact_price: one fresh exact
    # authority is enough; multiple fresh authority families are averaged only
    # when their max/min spread is no greater than 2x.
    current_trusted = []
    for row in trusted:
        effective = row.get("effective_at")
        gate_day = effective.date() if isinstance(effective, datetime) else _observation_date(row)
        if 0 <= (target - gate_day).days <= 2:
            current_trusted.append(row)
    if not current_trusted:
        return None
    pc_family = frozenset({"pricecharting"})
    snk_family = frozenset({"snk_psa10", "snk", "snkrdunk"})
    ebay_family = frozenset({"ebay"})
    pc_rows = [
        r for r in current_trusted if str(r.get("source_code") or "").casefold() in pc_family
    ]
    snk_rows = [
        r for r in current_trusted if str(r.get("source_code") or "").casefold() in snk_family
    ]
    ebay_rows = [
        r for r in current_trusted if str(r.get("source_code") or "").casefold() in ebay_family
    ]
    pc = min(pc_rows, key=trusted_key) if pc_rows else None
    snk = min(snk_rows, key=trusted_key) if snk_rows else None
    ebay = min(ebay_rows, key=trusted_key) if ebay_rows else None
    family_rows = [row for row in (pc, ebay, snk) if row is not None]
    values = [float(row["price_usd"]) for row in family_rows]
    if len(values) >= 2 and max(values) / min(values) > 2.0:
        return None
    selected = dict(pc or ebay or snk or min(current_trusted, key=trusted_key))
    selected["price_usd"] = round(sum(values) / len(values), 6)
    return selected


def load_qc_report_variants(report_path: Path, expected_sha256: str) -> tuple[list[int], str]:
    """Load one immutable QC cohort for read-only recomputation only."""

    expected = str(expected_sha256 or "").strip().casefold()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("--expected-universe-candidate-sha256 must be 64 hexadecimal characters")
    payload = report_path.resolve().read_bytes()
    report = json.loads(payload)
    receipt_path = report_path.resolve().with_name("receipt.json")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    universe = report.get("universe") if isinstance(report, Mapping) else None
    actual = str((universe or {}).get("candidateSha256") or "").casefold()
    if actual != expected or str(receipt.get("universeCandidateSha256") or "").casefold() != expected:
        raise RuntimeError("QC report candidate hash does not match the explicit expected hash")
    if str(receipt.get("reportSha256") or "").casefold() != hashlib.sha256(payload).hexdigest():
        raise RuntimeError("QC receipt does not bind the supplied report bytes")
    if not isinstance(universe, Mapping) or universe.get("schemaVersion") != "qc-discovery-v1":
        raise RuntimeError("QC report universe contract is invalid")
    cards = report.get("cards")
    if not isinstance(cards, list) or int(universe.get("qualified") or -1) != len(cards):
        raise RuntimeError("QC report qualified cohort is invalid")
    variant_ids = [int(card["variantId"]) for card in cards if isinstance(card, Mapping)]
    if len(variant_ids) != len(cards) or any(variant_id <= 0 for variant_id in variant_ids):
        raise RuntimeError("QC report contains an invalid variant id")
    if len(set(variant_ids)) != len(variant_ids):
        raise RuntimeError("QC report contains duplicate variant ids")
    return sorted(variant_ids), expected


def price_sales_eligible_variant_ids(report_path: Path) -> list[int]:
    report = json.loads(report_path.resolve().read_text(encoding="utf-8"))
    eligible: list[int] = []
    for card in report.get("cards") or []:
        if not isinstance(card, Mapping):
            continue
        facts = card.get("facts")
        facts = facts if isinstance(facts, Mapping) else {}
        price = facts.get("price")
        price = price if isinstance(price, Mapping) else {}
        priority = price.get("priorityMeta")
        priority = priority if isinstance(priority, Mapping) else {}
        cross_source = priority.get("crossSourceQc")
        cross_source = cross_source if isinstance(cross_source, Mapping) else {}
        sales = facts.get("sales30d")
        sales = sales if isinstance(sales, Mapping) else {}
        blockers = [str(value) for value in card.get("blockers") or []]
        if (
            cross_source.get("status") == "confirmed"
            and int(sales.get("purePsa10Count") or 0) >= 10
            and not any(
                value.startswith("sale_") or value.startswith("psa10_sales_30d_")
                for value in blockers
            )
        ):
            eligible.append(int(card["variantId"]))
    return sorted(eligible)


def load_staging_cohort(
    connection: Any,
    *,
    lock_sha256: str,
    qc_report: Path,
    expected_candidate_sha256: str,
    for_update: bool = False,
) -> tuple[int, list[int], str]:
    """Resolve only a receipt-bound, non-current QC staging lock."""

    from qc_cohort_lock import load_staging_plan

    lock_hash = str(lock_sha256 or "").strip().casefold()
    if len(lock_hash) != 64 or any(char not in "0123456789abcdef" for char in lock_hash):
        raise ValueError("--staging-universe-lock-sha256 must be 64 hexadecimal characters")
    plan = load_staging_plan(qc_report, expected_candidate_sha256)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT id,policy_json,member_count,is_current FROM market_universe_lock "
            "WHERE lock_sha256=%s" + (" FOR UPDATE" if for_update else ""),
            (lock_hash,),
        )
        lock = cursor.fetchone()
        if lock is None:
            raise RuntimeError("explicit QC staging lock does not exist")
        if int(lock.get("is_current") or 0) != 0:
            raise RuntimeError("explicit QC staging lock must remain non-current")
        if int(lock.get("member_count") or -1) != len(plan["members"]):
            raise RuntimeError("explicit QC staging lock member count mismatch")
        if json.loads(lock["policy_json"]) != plan["policy"]:
            raise RuntimeError("explicit QC staging lock policy/report binding mismatch")
        cursor.execute(
            "SELECT variant_id,segment_code,member_role,selection_signals_json "
            "FROM market_universe_member WHERE universe_lock_id=%s ORDER BY variant_id",
            (int(lock["id"]),),
        )
        rows = list(cursor.fetchall())
    expected_ids = [int(row["variantId"]) for row in plan["members"]]
    if [int(row["variant_id"]) for row in rows] != expected_ids:
        raise RuntimeError("explicit QC staging lock members do not match the receipt-bound cohort")
    for row in rows:
        signals = json.loads(row["selection_signals_json"])
        if (
            row["segment_code"] != "qc-discovery"
            or row["member_role"] != "staging"
            or signals.get("candidateSha256") != plan["candidateSha256"]
            or signals.get("reportSha256") != plan["reportSha256"]
        ):
            raise RuntimeError("explicit QC staging lock member evidence mismatch")
    return int(lock["id"]), expected_ids, str(plan["candidateSha256"])


def load_candidates(
    connection: Any,
    effective_date: date,
    *,
    variant_ids: Sequence[int] | None = None,
    universe_lock_id: int | None = None,
) -> tuple[int | None, list[CandidateSnapshot]]:
    with connection.cursor() as cursor:
        if variant_ids is None:
            cursor.execute(
                """
                SELECT l.id AS universe_lock_id, m.variant_id, m.member_role, c.opaque_id
                FROM market_universe_lock l
                JOIN market_universe_member m ON m.universe_lock_id = l.id
                JOIN catalog_variant c ON c.id = m.variant_id
                WHERE l.is_current = 1
                ORDER BY m.variant_id
                """
            )
        else:
            requested = sorted({int(variant_id) for variant_id in variant_ids})
            if not requested or any(variant_id <= 0 for variant_id in requested):
                raise RuntimeError("explicit QC cohort is empty or invalid")
            marks = ",".join(["%s"] * len(requested))
            cursor.execute(
                f"""
                SELECT NULL AS universe_lock_id,id AS variant_id,'qualified' AS member_role,opaque_id
                FROM catalog_variant WHERE id IN ({marks}) ORDER BY id
                """,
                requested,
            )
        members = list(cursor.fetchall())
        if not members:
            raise RuntimeError("current market universe is missing")
        if variant_ids is not None and {int(row["variant_id"]) for row in members} != set(requested):
            raise RuntimeError("explicit QC cohort does not resolve to the canonical catalog")
        variant_ids = [int(row["variant_id"]) for row in members]
        placeholders = ",".join(["%s"] * len(variant_ids))
        since = effective_date - timedelta(days=PRICE_HISTORY_LOOKBACK_DAYS)
        # Exact trusted quotes may have historical observed_date (TPL/SNK day
        # series) while effective_at is the current harvest stamp. Load them
        # even when observed_date is older than the risky lookback floor so
        # market cap binds QC current exact PSA10 price.
        trusted_codes = tuple(sorted(TRUSTED_PRICE_SOURCES))
        trusted_marks = ",".join(["%s"] * len(trusted_codes))
        cursor.execute(
            f"""
            SELECT id, variant_id, source_code, observed_date, effective_at,
                   price_usd, metric_status, source_priority
            FROM market_price_observation
            WHERE variant_id IN ({placeholders})
              AND observed_date <= %s
              AND (
                observed_date >= %s
                OR LOWER(source_code) IN ({trusted_marks})
              )
            ORDER BY variant_id, observed_date, id
            """,
            (*variant_ids, effective_date, since, *trusted_codes),
        )
        price_rows = list(cursor.fetchall())
        # Population may be older than price; keep a wide window so POP gate is not the empty-board cause.
        # Exact-gemrate only for gemrate rows — matches QC latest_exact_population_rows so
        # market_cap_current_population_mismatch cannot fire from conflict/alias POP.
        pop_since = effective_date - timedelta(days=max(PRICE_HISTORY_LOOKBACK_DAYS, 30))
        cursor.execute(
            f"""
            SELECT p.id, p.variant_id, p.source_code, p.observed_date, p.effective_at,
                   p.top_grade_population, p.estimated
            FROM market_grader_population_observation p
            WHERE p.grader_code = 'PSA' AND p.variant_id IN ({placeholders})
              AND p.observed_date BETWEEN %s AND %s
              AND (
                LOWER(p.source_code) <> 'gemrate'
                OR EXISTS (
                  SELECT 1 FROM catalog_source_identity i
                  WHERE i.variant_id = p.variant_id
                    AND LOWER(i.source_code) = 'gemrate'
                    AND LOWER(i.match_status) = 'exact'
                    AND LOWER(REPLACE(i.external_entity_id, 'gemrate:', ''))
                      = LOWER(REPLACE(p.external_entity_id, 'gemrate:', ''))
                )
              )
            ORDER BY p.variant_id, p.observed_date, p.id
            """,
            (*variant_ids, pop_since, effective_date),
        )
        population_rows = list(cursor.fetchall())
        # Fallback: gemrate watchlist PSA10 pop when observation table is sparse.
        cursor.execute(
            f"""
            SELECT variant_id, psa10_population, population_as_of
            FROM market_gemrate_psa10_watchlist
            WHERE variant_id IN ({placeholders})
            """,
            tuple(variant_ids),
        )
        watch_pop = {int(r["variant_id"]): r for r in cursor.fetchall()}

    prices: dict[int, list[Mapping[str, Any]]] = {}
    populations: dict[int, list[Mapping[str, Any]]] = {}
    for row in price_rows:
        prices.setdefault(int(row["variant_id"]), []).append(row)
    for row in population_rows:
        populations.setdefault(int(row["variant_id"]), []).append(row)

    # Identity map for exact-only price legs (blocks derived ebay G10 poison ranks)
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT variant_id, source_code, match_status, external_entity_id
            FROM catalog_source_identity
            WHERE variant_id IN ({placeholders})
            """,
            tuple(variant_ids),
        )
        identities_by: dict[int, list[Mapping[str, Any]]] = {}
        for row in cursor.fetchall():
            identities_by.setdefault(int(row["variant_id"]), []).append(row)

    raw: list[dict[str, Any]] = []
    for member in members:
        variant_id = int(member["variant_id"])
        current_price = pick_rank_price(
            prices.get(variant_id, []),
            effective_date,
            PRICE_SOURCE_PRIORITY,
            max_gap_days=PRICE_MAX_GAP_DAYS_FOR_RANK,
            value_key="price_usd",
            identities=identities_by.get(variant_id),
        )
        current_population = pick_observation(
            populations.get(variant_id, []),
            effective_date,
            POPULATION_SOURCE_PRIORITY,
            max_gap_days=max(CURRENT_POPULATION_MAX_GAP_DAYS, 30),
            value_key="top_grade_population",
        )
        price = float(current_price["price_usd"]) if current_price else None
        population = int(current_population["top_grade_population"]) if current_population else None
        if population is None:
            wp = watch_pop.get(variant_id)
            if wp and wp.get("psa10_population") is not None and int(wp["psa10_population"]) > 0:
                population = int(wp["psa10_population"])
        # Single formula path: ranking_derivation.market_cap_usd (price × POP).
        cap = formula_market_cap_usd(price, population)

        def prior_price(days: int) -> float | None:
            row = pick_rank_price(
                prices.get(variant_id, []),
                effective_date - timedelta(days=days),
                PRICE_SOURCE_PRIORITY,
                max_gap_days=max(14, PRICE_MAX_GAP_DAYS_FOR_RANK // 3),
                value_key="price_usd",
            )
            return float(row["price_usd"]) if row else None

        def prior_population(days: int) -> int | None:
            row = pick_observation(
                populations.get(variant_id, []),
                effective_date - timedelta(days=days),
                POPULATION_SOURCE_PRIORITY,
                max_gap_days=14,
                value_key="top_grade_population",
            )
            return int(row["top_grade_population"]) if row else None

        metric_status = "ready" if price is not None and population is not None else (
            "accumulating" if price is not None or population is not None else "unavailable"
        )
        raw.append(
            {
                "variant_id": variant_id,
                "candidate_key": str(member["opaque_id"]),
                "member_role": str(member["member_role"]),
                "price": price,
                "population": population,
                "cap": cap,
                "change1": percent_change(price, prior_price(1)),
                "change7": percent_change(price, prior_price(7)),
                "change30": percent_change(price, prior_price(30)),
                "pop7": percent_change(population, prior_population(7)),
                "pop30": percent_change(population, prior_population(30)),
                "metric_status": metric_status,
            }
        )

    shadow = sorted((row for row in raw if row["cap"] is not None), key=lambda row: (-row["cap"], row["variant_id"]))
    shadow_ranks = {row["variant_id"]: index for index, row in enumerate(shadow, start=1)}
    eligible = [row for row in shadow if row["population"] >= 1000]
    eligible_ranks = {row["variant_id"]: index for index, row in enumerate(eligible, start=1)}
    cutoff = eligible[99]["cap"] if len(eligible) >= 100 else None
    snapshots = []
    for row in raw:
        cap = row["cap"]
        price = row["price"]
        snapshots.append(
            CandidateSnapshot(
                variant_id=row["variant_id"],
                candidate_key=row["candidate_key"],
                member_role=row["member_role"],
                shadow_rank=shadow_ranks.get(row["variant_id"]),
                eligible_rank=eligible_ranks.get(row["variant_id"]),
                reference_price_usd=price,
                psa10_population=row["population"],
                market_cap_usd=cap,
                cutoff_ratio=(cap / cutoff) if cap is not None and cutoff else None,
                projected_pop1000_ratio=(price * 1000 / cutoff) if price is not None and cutoff else None,
                change_1d_pct=row["change1"],
                change_7d_pct=row["change7"],
                change_30d_pct=row["change30"],
                population_change_7d_pct=row["pop7"],
                population_change_30d_pct=row["pop30"],
                metric_status=row["metric_status"],
            )
        )
    lock_id = universe_lock_id if universe_lock_id is not None else members[0]["universe_lock_id"]
    return (int(lock_id) if lock_id is not None else None), snapshots


def dry_run_evaluation(
    connection: Any,
    effective_date: date,
    *,
    variant_ids: Sequence[int] | None = None,
    universe_lock_id: int | None = None,
    universe_candidate_sha256: str | None = None,
) -> dict[str, Any]:
    """Read-only materialization preview; it never acquires locks or writes rows."""

    if variant_ids is None:
        lock_id, snapshots = load_candidates(connection, effective_date)
    else:
        lock_id, snapshots = load_candidates(
            connection,
            effective_date,
            variant_ids=variant_ids,
            universe_lock_id=universe_lock_id,
        )
    with connection.cursor() as cursor:
        liquid = variants_with_sale_30d(cursor)
        ids = [row.variant_id for row in snapshots]
        marks = ",".join(["%s"] * len(ids))
        cursor.execute(
            f"SELECT id,tcg_code FROM catalog_variant WHERE id IN ({marks})", ids
        )
        tcg_by_variant = {int(row["id"]): str(row["tcg_code"]) for row in cursor.fetchall()}
    indexes = tracked_indexes(snapshots, tcg_by_variant, liquid)
    eligible_variant_ids = sorted(
        row.variant_id for row in snapshots if row.eligible_rank is not None
    )
    return {
        "status": "dry-run",
        "effectiveDate": effective_date.isoformat(),
        "universeLockId": lock_id,
        "universeCandidateSha256": universe_candidate_sha256,
        "candidates": len(snapshots),
        "ready": sum(row.metric_status == "ready" for row in snapshots),
        "eligible": len(eligible_variant_ids),
        "eligibleVariantIds": eligible_variant_ids,
        "wouldCreateEvaluation": lock_id is not None,
        "wouldCreateCandidateSnapshotRows": len(snapshots) if lock_id is not None else 0,
        "wouldCreateIndexes": len(indexes) if lock_id is not None else 0,
        "wouldCreateIndexConstituentRows": sum(len(rows) for rows in indexes.values()) if lock_id is not None else 0,
        "indexRows": {name: len(rows) for name, rows in indexes.items()},
        "writeBlockedReason": (
            "qc_report_cohort_requires_materialized_matching_universe_lock"
            if lock_id is None else None
        ),
    }


def snapshot_hash(snapshot: CandidateSnapshot) -> str:
    return hashlib.sha256(canonical_json(asdict(snapshot))).hexdigest()


def variants_with_sale_30d(cursor: Any) -> set[int]:
    """Variants with >=10 exact, single-unit PSA10 sales in the last 30 days.

    This ranking hint must not be weaker than the public gate.  The snapshot
    exporter remains the public authority, but a raw grade match, bundle, or
    unbound source identity must never be enough to make a card look liquid.
    """

    cursor.execute(
        """
        SELECT sale.variant_id
        FROM market_sale_observation AS sale
        WHERE sale.sold_at IS NOT NULL
          AND sale.sold_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
          AND UPPER(sale.grader_code)='PSA'
          AND UPPER(sale.grade_label) IN ('10', 'PSA 10', 'PSA10')
          AND sale.quantity=1 AND sale.unit_price_usd > 0
          AND sale.transaction_value_usd=sale.unit_price_usd
          AND sale.timestamp_quality IN ('exact', 'date', 'timestamp', 'exact_date', 'relative_resolved', 'relative_subday')
          AND sale.coverage_status IN ('partial', 'complete', 'certified')
          AND LOWER(sale.source_payload_sha256) REGEXP '^[0-9a-f]{64}$'
          AND (
            (sale.source_code IN ('snk_psa10', 'snk', 'snkrdunk', 'snk_grade')
             AND sale.external_entity_id REGEXP '^[0-9]+$'
             AND CAST(sale.external_entity_id AS UNSIGNED)>0
             AND EXISTS (
                 SELECT 1 FROM catalog_source_identity AS identity
                 LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                 WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                   AND identity.source_code IN ('snk', 'snkrdunk') AND identity.match_status='exact'
                   AND identity.external_entity_id REGEXP '^[0-9]+$'
                   AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(sale.external_entity_id AS UNSIGNED)
             ))
            OR (sale.source_code='ebay' AND (
              (sale.external_entity_id REGEXP '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
               AND EXISTS (
                   SELECT 1 FROM catalog_source_identity AS identity
                   LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                   WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                     AND identity.source_code='ebay' AND identity.match_status='exact'
                     AND LOWER(identity.external_entity_id)=LOWER(sale.external_entity_id)
               ))
              OR (sale.external_entity_id REGEXP '^[0-9]+$' AND CAST(sale.external_entity_id AS UNSIGNED)>0
               AND EXISTS (
                   SELECT 1 FROM catalog_source_identity AS identity
                   LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                   WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                     AND identity.source_code IN ('snk', 'snkrdunk') AND identity.match_status='exact'
                     AND identity.external_entity_id REGEXP '^[0-9]+$'
                     AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(sale.external_entity_id AS UNSIGNED)
               ))
              OR (sale.external_entity_id REGEXP '^pc:[0-9]+$'
               AND EXISTS (
                   SELECT 1 FROM catalog_source_identity AS identity
                   LEFT JOIN catalog_variant_alias AS alias ON alias.duplicate_variant_id=identity.variant_id
                   WHERE COALESCE(alias.canonical_variant_id, identity.variant_id)=sale.variant_id
                     AND identity.source_code='pricecharting' AND identity.match_status='exact'
                     AND identity.external_entity_id REGEXP '^[0-9]+$'
                     AND CAST(identity.external_entity_id AS UNSIGNED)=CAST(SUBSTRING(sale.external_entity_id, 4) AS UNSIGNED)
               ))
            ))
          )
        GROUP BY sale.variant_id
        HAVING COUNT(*) >= 10
        """
    )
    return {int(row["variant_id"]) for row in cursor.fetchall()}


def tracked_indexes(
    snapshots: Sequence[CandidateSnapshot],
    tcg_by_variant: Mapping[int, str],
    liquid_30d: set[int] | None = None,
    exclusive_ids: set[int] | None = None,
) -> dict[str, list[CandidateSnapshot]]:
    ready = [
        row for row in snapshots
        if row.metric_status == "ready"
        and row.psa10_population is not None
        and row.psa10_population >= 1000
        and row.market_cap_usd is not None
    ]
    # HARD: liquid (30d PSA10 sales) first; illiquid only after all liquid
    # so Top100 seats cannot be taken by 0-sales cards when liquid supply exists.
    # POLICY_FE_TOP100_LIQUIDITY.md
    # POLICY_EXCLUSIVE_RANK_IGNORE: exclusive products rank after non-exclusive
    # (ignore for Top100 impact) but stay in index — not deleted / not QC-hard-killed.
    liquid = liquid_30d or set()
    exclusive = exclusive_ids or set()

    def ordered(rows: list[CandidateSnapshot]) -> list[CandidateSnapshot]:
        def cap_key(row: CandidateSnapshot) -> tuple[float, int]:
            return (-float(row.market_cap_usd or 0), row.variant_id)

        def bucket(row: CandidateSnapshot) -> list[CandidateSnapshot]:
            return [row]

        non_ex_liq = sorted(
            [r for r in rows if r.variant_id not in exclusive and r.variant_id in liquid],
            key=cap_key,
        )
        non_ex_dry = sorted(
            [r for r in rows if r.variant_id not in exclusive and r.variant_id not in liquid],
            key=cap_key,
        )
        ex_liq = sorted(
            [r for r in rows if r.variant_id in exclusive and r.variant_id in liquid],
            key=cap_key,
        )
        ex_dry = sorted(
            [r for r in rows if r.variant_id in exclusive and r.variant_id not in liquid],
            key=cap_key,
        )
        return non_ex_liq + non_ex_dry + ex_liq + ex_dry

    return {
        "tcg-combined": ordered(list(ready)),
        "pokemon": ordered(
            [row for row in ready if tcg_by_variant.get(row.variant_id) == "pokemon"]
        ),
        "one-piece": ordered(
            [row for row in ready if tcg_by_variant.get(row.variant_id) == "one-piece"]
        ),
    }


def materialize_index_revisions(
    cursor: Any,
    evaluation_id: int,
    effective_date: date,
    snapshots: Sequence[CandidateSnapshot],
) -> int:
    """Create only missing evaluation-bound indexes, including legacy reuse."""

    if not snapshots:
        return 0
    cursor.execute(
        "SELECT id FROM market_ingest_run "
        "WHERE status='complete' ORDER BY effective_at DESC,id DESC LIMIT 1"
    )
    run = cursor.fetchone()
    if not run:
        return 0
    cursor.execute(
        "SELECT id,tcg_code FROM catalog_variant WHERE id IN ("
        + ",".join(["%s"] * len(snapshots))
        + ")",
        tuple(row.variant_id for row in snapshots),
    )
    tcg_by_variant = {
        int(row["id"]): str(row["tcg_code"])
        for row in cursor.fetchall()
    }
    liquid_30d = variants_with_sale_30d(cursor)
    # exclusive name/set demotion for ranking only (POLICY_EXCLUSIVE_RANK_IGNORE)
    try:
        from exclusive_product import is_exclusive_product
    except ImportError:
        from pipelines.exclusive_product import is_exclusive_product  # type: ignore
    exclusive_ids: set[int] = set()
    if snapshots:
        cursor.execute(
            "SELECT id, canonical_name, set_name FROM catalog_variant WHERE id IN ("
            + ",".join(["%s"] * len(snapshots))
            + ")",
            tuple(row.variant_id for row in snapshots),
        )
        for row in cursor.fetchall():
            if is_exclusive_product(row.get("canonical_name"), row.get("set_name")):
                exclusive_ids.add(int(row["id"]))
    created = 0
    for index_code, rows in tracked_indexes(
        snapshots, tcg_by_variant, liquid_30d, exclusive_ids
    ).items():
        if not rows:
            continue
        cursor.execute(
            """
            SELECT id FROM market_index_snapshot
            WHERE evaluation_id=%s AND index_code=%s AND index_version=%s
            LIMIT 1
            """,
            (evaluation_id, index_code, INDEX_VERSION),
        )
        if cursor.fetchone():
            continue
        index_hash = hashlib.sha256(
            canonical_json([asdict(row) for row in rows])
        ).hexdigest()
        cursor.execute(
            """
            INSERT INTO market_index_snapshot
                (run_id,evaluation_id,index_code,index_version,effective_at,effective_date,
                 constituent_count,total_market_cap_usd,snapshot_sha256)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                run["id"],
                evaluation_id,
                index_code,
                INDEX_VERSION,
                datetime.combine(effective_date, datetime.min.time()),
                effective_date,
                len(rows),
                sum(row.market_cap_usd or 0 for row in rows),
                index_hash,
            ),
        )
        index_id = int(cursor.lastrowid)
        for rank, row in enumerate(rows, start=1):
            cursor.execute(
                """
                INSERT INTO market_index_constituent
                    (index_snapshot_id,variant_id,rank_position,reference_price_usd,
                     psa10_population,market_cap_usd,change_30d_pct,metric_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    index_id,
                    row.variant_id,
                    rank,
                    row.reference_price_usd,
                    row.psa10_population,
                    row.market_cap_usd,
                    row.change_30d_pct,
                    row.metric_status,
                ),
            )
        created += 1
    return created


def evaluation_input_hash(
    snapshots: Sequence[CandidateSnapshot],
    *,
    discovery_sha256: str,
    coverage_status: str,
    unresolved_high_potential_count: int,
) -> str:
    """Bind one daily result to both canonical metrics and discovery evidence."""

    return hashlib.sha256(
        canonical_json(
            {
                "snapshots": [asdict(row) for row in snapshots],
                "discoverySha256": discovery_sha256,
                "coverageStatus": coverage_status,
                "unresolvedHighPotentialCount": unresolved_high_potential_count,
            }
        )
    ).hexdigest()


def active_key(candidate_key: str, alert_type: str) -> str:
    return hashlib.sha256(f"{POLICY_VERSION}|{candidate_key}|{alert_type}".encode("utf-8")).hexdigest()


def event_key(alert_id: int, evaluation_id: int, event_type: str) -> str:
    return hashlib.sha256(f"{alert_id}|{evaluation_id}|{event_type}".encode("ascii")).hexdigest()


def previous_snapshots(connection: Any, effective_date: date) -> dict[int, CandidateSnapshot]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT s.* FROM market_candidate_daily_snapshot s
            WHERE s.evaluation_id = (
                SELECT e2.id FROM market_alert_evaluation e2
                WHERE e2.index_code=%s AND e2.index_version=%s
                  AND e2.policy_version=%s AND e2.effective_date < %s
                ORDER BY e2.effective_date DESC, e2.id DESC
                LIMIT 1
              )
            """,
            (INDEX_CODE, INDEX_VERSION, POLICY_VERSION, effective_date),
        )
        rows = list(cursor.fetchall())
    result = {}
    for row in rows:
        result[int(row["variant_id"])] = CandidateSnapshot(
            variant_id=int(row["variant_id"]),
            candidate_key="",
            member_role=str(row["member_role"]),
            shadow_rank=row["shadow_rank"],
            eligible_rank=row["eligible_rank"],
            reference_price_usd=float(row["reference_price_usd"]) if row["reference_price_usd"] is not None else None,
            psa10_population=row["psa10_population"],
            market_cap_usd=float(row["market_cap_usd"]) if row["market_cap_usd"] is not None else None,
            cutoff_ratio=float(row["cutoff_ratio"]) if row["cutoff_ratio"] is not None else None,
            projected_pop1000_ratio=float(row["projected_pop1000_ratio"]) if row["projected_pop1000_ratio"] is not None else None,
            change_1d_pct=float(row["change_1d_pct"]) if row["change_1d_pct"] is not None else None,
            change_7d_pct=float(row["change_7d_pct"]) if row["change_7d_pct"] is not None else None,
            change_30d_pct=float(row["change_30d_pct"]) if row["change_30d_pct"] is not None else None,
            population_change_7d_pct=float(row["population_change_7d_pct"]) if row["population_change_7d_pct"] is not None else None,
            population_change_30d_pct=float(row["population_change_30d_pct"]) if row["population_change_30d_pct"] is not None else None,
            metric_status=str(row["metric_status"]),
        )
    return result


def insert_event(cursor: Any, alert_id: int, evaluation_id: int, event_type: str, severity: str, payload: Mapping[str, Any]) -> None:
    cursor.execute(
        """
        INSERT IGNORE INTO market_alert_event
            (alert_id, evaluation_id, event_key, event_type, severity, payload_json, delivery_status)
        VALUES (%s, %s, %s, %s, %s, %s, 'pending')
        """,
        (alert_id, evaluation_id, event_key(alert_id, evaluation_id, event_type), event_type, severity, json.dumps(payload, sort_keys=True)),
    )


def apply_alerts(
    cursor: Any,
    evaluation_id: int,
    effective_date: date,
    snapshots: Sequence[CandidateSnapshot],
    snapshot_ids: Mapping[int, int],
    previous: Mapping[int, CandidateSnapshot],
) -> dict[str, int]:
    seen_keys: set[str] = set()
    opened = escalated = resolved = 0
    severity_order = {"medium": 1, "high": 2, "critical": 3}
    for snapshot in snapshots:
        for signal in alert_signals(snapshot, previous.get(snapshot.variant_id)):
            dedupe = active_key(snapshot.candidate_key, signal.alert_type)
            seen_keys.add(dedupe)
            cursor.execute("SELECT * FROM market_alert WHERE active_dedupe_key=%s FOR UPDATE", (dedupe,))
            row = cursor.fetchone()
            evidence = json.dumps(dict(signal.evidence), sort_keys=True)
            if not row:
                hits = 1
                status = "open" if signal.immediate else "observing"
                cursor.execute(
                    """
                    INSERT INTO market_alert
                        (variant_id, candidate_key, alert_type, severity, status, active_dedupe_key,
                         first_seen_date, last_seen_date, consecutive_hits, consecutive_misses,
                         latest_snapshot_id, latest_evidence_json)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,%s,%s)
                    """,
                    (snapshot.variant_id, snapshot.candidate_key, signal.alert_type, signal.severity, status,
                     dedupe, effective_date, effective_date, hits, snapshot_ids[snapshot.variant_id], evidence),
                )
                alert_id = int(cursor.lastrowid)
                if status == "open":
                    insert_event(cursor, alert_id, evaluation_id, "opened", signal.severity, signal.evidence)
                    opened += 1
                continue
            hits = int(row["consecutive_hits"]) + (0 if row["last_seen_date"] == effective_date else 1)
            old_status = str(row["status"])
            old_severity = str(row["severity"])
            new_status = "open" if signal.immediate or hits >= 2 or old_status in {"open", "acknowledged"} else "observing"
            new_severity = signal.severity if severity_order[signal.severity] > severity_order.get(old_severity, 0) else old_severity
            cursor.execute(
                """
                UPDATE market_alert SET severity=%s, status=%s, last_seen_date=%s,
                    consecutive_hits=%s, consecutive_misses=0, latest_snapshot_id=%s,
                    latest_evidence_json=%s WHERE id=%s
                """,
                (new_severity, new_status, effective_date, hits, snapshot_ids[snapshot.variant_id], evidence, row["id"]),
            )
            if old_status == "observing" and new_status == "open":
                insert_event(cursor, int(row["id"]), evaluation_id, "opened", new_severity, signal.evidence)
                opened += 1
            elif severity_order[new_severity] > severity_order.get(old_severity, 0) and new_status == "open":
                insert_event(cursor, int(row["id"]), evaluation_id, "escalated", new_severity, signal.evidence)
                escalated += 1

    cursor.execute("SELECT * FROM market_alert WHERE active_dedupe_key IS NOT NULL FOR UPDATE")
    for row in cursor.fetchall():
        dedupe = str(row["active_dedupe_key"])
        if dedupe in seen_keys:
            continue
        variant_id = row["variant_id"]
        current = next((snapshot for snapshot in snapshots if snapshot.variant_id == variant_id), None)
        if current is None or current.metric_status != "ready" or row["last_seen_date"] == effective_date:
            continue
        misses = int(row["consecutive_misses"]) + 1
        if misses >= 3:
            cursor.execute(
                """UPDATE market_alert SET status='resolved', active_dedupe_key=NULL,
                   consecutive_misses=%s, resolved_at=%s WHERE id=%s""",
                (misses, datetime.now(timezone.utc).replace(tzinfo=None), row["id"]),
            )
            insert_event(cursor, int(row["id"]), evaluation_id, "resolved", str(row["severity"]), {"misses": misses})
            resolved += 1
        else:
            cursor.execute(
                "UPDATE market_alert SET consecutive_misses=%s, consecutive_hits=0 WHERE id=%s",
                (misses, row["id"]),
            )
    return {"opened": opened, "escalated": escalated, "resolved": resolved}


def evaluate(
    connection: Any,
    effective_date: date,
    *,
    discovery_sha256: str,
    coverage_status: str,
    unresolved_high_potential_count: int,
    variant_ids: Sequence[int] | None = None,
    universe_lock_id: int | None = None,
    staging: bool = False,
) -> dict[str, Any]:
    if coverage_status == "certified" and unresolved_high_potential_count:
        raise RuntimeError("certified coverage cannot contain unresolved high-potential candidates")
    if staging:
        coverage_status = "blocked"
    if unresolved_high_potential_count:
        coverage_status = "blocked"
    if variant_ids is None:
        lock_id, snapshots = load_candidates(connection, effective_date)
    else:
        lock_id, snapshots = load_candidates(
            connection,
            effective_date,
            variant_ids=variant_ids,
            universe_lock_id=universe_lock_id,
        )
    if lock_id is None:
        raise RuntimeError("market evaluation requires a materialized universe lock")
    eligible = sorted(
        (row for row in snapshots if row.eligible_rank is not None),
        key=lambda row: row.eligible_rank or 999999,
    )
    cutoff = eligible[99].market_cap_usd if len(eligible) >= 100 else None
    if len(eligible) < 100:
        coverage_status = "blocked"
    input_hash = evaluation_input_hash(
        snapshots,
        discovery_sha256=discovery_sha256,
        coverage_status=coverage_status,
        unresolved_high_potential_count=unresolved_high_potential_count,
    )
    previous = previous_snapshots(connection, effective_date)
    with connection.cursor() as cursor:
        cursor.execute("SELECT GET_LOCK('cardz_market_alert_evaluation', 0) AS acquired")
        if int(cursor.fetchone()["acquired"] or 0) != 1:
            raise RuntimeError("another market alert evaluation is active")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, input_sha256, coverage_status, eligible_count, publish_gate_status
                FROM market_alert_evaluation
                WHERE index_code=%s AND index_version=%s AND effective_date=%s
                  AND policy_version=%s AND input_sha256=%s
                ORDER BY id DESC
                LIMIT 1
                """,
                (INDEX_CODE, INDEX_VERSION, effective_date, POLICY_VERSION, input_hash),
            )
            existing = cursor.fetchone()
            if existing:
                evaluation_id = int(existing["id"])
                revisions_created = materialize_index_revisions(
                    cursor,
                    evaluation_id,
                    effective_date,
                    snapshots,
                )
                connection.commit()
                return {
                    "status": "reused",
                    "evaluationId": evaluation_id,
                    "effectiveDate": effective_date.isoformat(),
                    "coverageStatus": existing["coverage_status"],
                    "publishGateStatus": existing["publish_gate_status"],
                    "eligible": int(existing["eligible_count"]),
                    "indexRevisionsCreated": revisions_created,
                }
            cursor.execute(
                """
                INSERT INTO market_alert_evaluation
                    (universe_lock_id,index_code,index_version,policy_version,effective_date,
                     discovery_sha256,input_sha256,eligible_count,top100_cutoff_usd,
                     unresolved_high_potential_count,coverage_status,completed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (lock_id, INDEX_CODE, INDEX_VERSION, POLICY_VERSION, effective_date,
                 discovery_sha256, input_hash, len(eligible), cutoff, unresolved_high_potential_count,
                 coverage_status, datetime.now(timezone.utc).replace(tzinfo=None)),
            )
            evaluation_id = int(cursor.lastrowid)
            snapshot_ids: dict[int, int] = {}
            for snapshot in snapshots:
                cursor.execute(
                    """
                    INSERT INTO market_candidate_daily_snapshot
                        (evaluation_id,variant_id,member_role,shadow_rank,eligible_rank,
                         reference_price_usd,psa10_population,market_cap_usd,cutoff_ratio,
                         projected_pop1000_ratio,change_1d_pct,change_7d_pct,change_30d_pct,
                         population_change_7d_pct,population_change_30d_pct,metric_status,evidence_sha256)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (evaluation_id, snapshot.variant_id, snapshot.member_role, snapshot.shadow_rank,
                     snapshot.eligible_rank, snapshot.reference_price_usd, snapshot.psa10_population,
                     snapshot.market_cap_usd, snapshot.cutoff_ratio, snapshot.projected_pop1000_ratio,
                     snapshot.change_1d_pct, snapshot.change_7d_pct, snapshot.change_30d_pct,
                     snapshot.population_change_7d_pct, snapshot.population_change_30d_pct,
                     snapshot.metric_status, snapshot_hash(snapshot)),
                )
                snapshot_ids[snapshot.variant_id] = int(cursor.lastrowid)
            events = apply_alerts(cursor, evaluation_id, effective_date, snapshots, snapshot_ids, previous)

            revisions_created = (
                materialize_index_revisions(cursor, evaluation_id, effective_date, snapshots)
                if cutoff is not None
                else 0
            )
        connection.commit()
        return {
            "status": "evaluated",
            "evaluationId": evaluation_id,
            "effectiveDate": effective_date.isoformat(),
            "coverageStatus": coverage_status,
                "publishGateStatus": "pending",
                "releaseEligible": False if staging else None,
            "eligible": len(eligible),
            "cutoffUsd": cutoff,
            "candidates": len(snapshots),
            "indexRevisionsCreated": revisions_created,
            **events,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK('cardz_market_alert_evaluation')")
        connection.commit()


def mark_evaluation_passed(connection: Any, evaluation_id: int) -> dict[str, Any]:
    """Make one exact immutable evaluation exportable after external gates pass."""

    if evaluation_id <= 0:
        raise ValueError("evaluation ID must be a positive integer")
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT e.id,e.coverage_status,e.publish_gate_status,
                       (
                           SELECT COUNT(*) FROM market_index_constituent c
                           JOIN market_index_snapshot s ON s.id=c.index_snapshot_id
                           WHERE s.evaluation_id=e.id AND s.index_code=%s
                             AND s.index_version=%s
                       ) AS combined_count,
                       (
                           SELECT COUNT(*) FROM market_index_constituent c
                           JOIN market_index_snapshot s ON s.id=c.index_snapshot_id
                           WHERE s.evaluation_id=e.id AND s.index_code=%s
                             AND s.index_version=%s
                       ) AS pokemon_count,
                       (
                           SELECT COUNT(*) FROM market_index_constituent c
                           JOIN market_index_snapshot s ON s.id=c.index_snapshot_id
                           WHERE s.evaluation_id=e.id AND s.index_code=%s
                             AND s.index_version=%s
                       ) AS one_piece_count
                FROM market_alert_evaluation e
                WHERE e.id=%s
                FOR UPDATE
                """,
                (
                    INDEX_CODE,
                    INDEX_VERSION,
                    "pokemon",
                    INDEX_VERSION,
                    "one-piece",
                    INDEX_VERSION,
                    evaluation_id,
                ),
            )
            evaluation = cursor.fetchone()
            if not evaluation:
                raise RuntimeError(f"market alert evaluation does not exist: {evaluation_id}")
            if str(evaluation["coverage_status"]) == "blocked":
                raise RuntimeError(
                    f"market alert evaluation {evaluation_id} has blocked coverage"
                )
            constituent_counts = {
                "tcg-combined": int(evaluation["combined_count"] or 0),
                "pokemon": int(evaluation["pokemon_count"] or 0),
                "one-piece": int(evaluation["one_piece_count"] or 0),
            }
            minimums = {"tcg-combined": 300, "pokemon": 100, "one-piece": 100}
            short = {
                index_code: {
                    "actual": constituent_counts[index_code],
                    "required": minimum,
                }
                for index_code, minimum in minimums.items()
                if constituent_counts[index_code] < minimum
            }
            if short:
                raise RuntimeError(
                    "market alert evaluation "
                    f"{evaluation_id} does not satisfy top300_boards counts: "
                    f"{json.dumps(short, sort_keys=True)}"
                )
            current_status = str(evaluation["publish_gate_status"])
            if current_status not in {"pending", "passed"}:
                raise RuntimeError(
                    f"market alert evaluation {evaluation_id} has invalid publish gate: {current_status}"
                )
            cursor.execute(
                """
                UPDATE market_alert_evaluation
                SET publish_gate_status='passed',
                    publish_gate_passed_at=COALESCE(publish_gate_passed_at,CURRENT_TIMESTAMP(6))
                WHERE id=%s
                """,
                (evaluation_id,),
            )
        connection.commit()
        return {
            "status": "gate-passed",
            "evaluationId": evaluation_id,
            "publishGateStatus": "passed",
            "updated": current_status != "passed",
            "constituentCounts": constituent_counts,
        }
    except Exception:
        connection.rollback()
        raise


def latest_price_date(connection: Any) -> date:
    with connection.cursor() as cursor:
        cursor.execute("SELECT MAX(observed_date) AS effective_date FROM market_price_observation")
        row = cursor.fetchone()
    if not row or not row["effective_date"]:
        raise RuntimeError("no canonical daily price is available for alert evaluation")
    return row["effective_date"]


def discovery_state(path: Path, max_age_hours: float) -> tuple[str, str, int]:
    if not path.is_file():
        return "0" * 64, "blocked", 0
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise RuntimeError("discovery radar manifest is invalid")
    digest = str(document.get("discoverySha256") or "").casefold()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise RuntimeError("discovery radar SHA-256 is invalid")
    captured = datetime.fromisoformat(str(document.get("capturedAt") or "").replace("Z", "+00:00"))
    if captured.tzinfo is None:
        raise RuntimeError("discovery radar timestamp must include a timezone")
    age = datetime.now(timezone.utc) - captured.astimezone(timezone.utc)
    status = str(document.get("coverageStatus") or "observed")
    if age < timedelta(0) or age > timedelta(hours=max_age_hours):
        status = "blocked"
    if status not in {"observed", "blocked"}:
        raise RuntimeError("private discovery radar cannot self-declare certified coverage")
    unresolved = int(document.get("unresolvedHighPotentialCount") or 0)
    if unresolved < 0:
        raise RuntimeError("discovery unresolved count is invalid")
    return digest, status, unresolved


def self_test() -> dict[str, Any]:
    previous = CandidateSnapshot(1, "fixture", "watchlist", 120, 120, 80, 1200, 96000, 0.85, 0.7, 4, 10, 15, None, None, "ready")
    near = CandidateSnapshot(1, "fixture", "watchlist", 105, 105, 100, 1200, 120000, 0.9, 0.75, 12, 22, 30, None, None, "ready")
    entered = CandidateSnapshot(1, "fixture", "top100", 99, 99, 100, 1200, 120000, 1.1, 0.75, 12, 22, 30, None, None, "ready")
    accumulating = CandidateSnapshot(2, "missing", "watchlist", None, None, None, 700, None, None, None, None, None, None, None, None, "accumulating")
    return {
        "nearSignals": [signal.alert_type for signal in alert_signals(near, previous)],
        "enteredSignals": [signal.alert_type for signal in alert_signals(entered, previous)],
        "missingSignals": len(alert_signals(accumulating, None)),
        "missingChange": percent_change(10, None),
    }


def write_result(path: Path, report: Mapping[str, Any]) -> None:
    """Write the evaluation receipt atomically for the parent daily attempt."""

    resolved = path.resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f".{resolved.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(report, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, resolved)


def main() -> int:
    load_db_env()
    parser = argparse.ArgumentParser(description="Evaluate CARDZ daily market candidate alerts")
    add_connection_args(parser)
    parser.add_argument("--effective-date", type=date.fromisoformat)
    parser.add_argument("--discovery-manifest", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--discovery-max-age-hours", type=float, default=48)
    parser.add_argument("--discovery-sha256")
    parser.add_argument("--coverage-status", choices=("certified", "observed", "blocked"))
    parser.add_argument("--unresolved-high-potential", type=int)
    parser.add_argument("--mark-passed-evaluation-id", type=int)
    parser.add_argument("--result-out", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--universe-qc-report", type=Path)
    parser.add_argument("--expected-universe-candidate-sha256")
    parser.add_argument("--staging-universe-lock-sha256")
    parser.add_argument("--price-sales-only", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    from db_runtime import connection_from_args

    connection = connection_from_args(args)
    try:
        staging = bool(args.staging_universe_lock_sha256)
        if staging:
            if not args.universe_qc_report or not args.expected_universe_candidate_sha256:
                raise RuntimeError("staging evaluation requires both QC report and expected candidate hash")
            if args.mark_passed_evaluation_id is not None:
                raise RuntimeError("staging evaluation cannot mark a release gate passed")
            lock_id, variant_ids, candidate_sha256 = load_staging_cohort(
                connection,
                lock_sha256=args.staging_universe_lock_sha256,
                qc_report=args.universe_qc_report,
                expected_candidate_sha256=args.expected_universe_candidate_sha256,
                for_update=not args.dry_run,
            )
            if args.price_sales_only:
                eligible_ids = set(
                    price_sales_eligible_variant_ids(args.universe_qc_report)
                )
                variant_ids = [
                    variant_id
                    for variant_id in variant_ids
                    if variant_id in eligible_ids
                ]
            effective = args.effective_date or latest_price_date(connection)
            if args.dry_run:
                report = dry_run_evaluation(
                    connection, effective, variant_ids=variant_ids,
                    universe_lock_id=lock_id,
                    universe_candidate_sha256=candidate_sha256,
                )
                report["coverageStatus"] = "blocked"
                report["releaseEligible"] = False
            else:
                manifest_sha, _manifest_status, manifest_unresolved = discovery_state(
                    args.discovery_manifest.resolve(), args.discovery_max_age_hours
                )
                report = evaluate(
                    connection, effective, discovery_sha256=(args.discovery_sha256 or manifest_sha).casefold(),
                    coverage_status="blocked", unresolved_high_potential_count=(args.unresolved_high_potential if args.unresolved_high_potential is not None else manifest_unresolved),
                    variant_ids=variant_ids, universe_lock_id=lock_id, staging=True,
                )
            if args.price_sales_only:
                report["priceSalesEligibleVariantIds"] = sorted(variant_ids)
        elif args.universe_qc_report:
            if not args.dry_run:
                raise RuntimeError("a QC report cohort is read-only; materialize and promote its matching universe lock first")
            variant_ids, candidate_sha256 = load_qc_report_variants(
                args.universe_qc_report, args.expected_universe_candidate_sha256
            )
            if args.price_sales_only:
                eligible_ids = set(
                    price_sales_eligible_variant_ids(args.universe_qc_report)
                )
                variant_ids = [
                    variant_id
                    for variant_id in variant_ids
                    if variant_id in eligible_ids
                ]
            effective = args.effective_date or latest_price_date(connection)
            report = dry_run_evaluation(
                connection,
                effective,
                variant_ids=variant_ids,
                universe_candidate_sha256=candidate_sha256,
            )
            if args.price_sales_only:
                report["priceSalesEligibleVariantIds"] = sorted(variant_ids)
        elif args.dry_run:
            effective = args.effective_date or latest_price_date(connection)
            report = dry_run_evaluation(connection, effective)
        if args.mark_passed_evaluation_id is not None:
            report = mark_evaluation_passed(connection, args.mark_passed_evaluation_id)
        elif not args.dry_run and not staging:
            manifest_sha, manifest_status, manifest_unresolved = discovery_state(
                args.discovery_manifest.resolve(), args.discovery_max_age_hours
            )
            discovery_sha = (args.discovery_sha256 or manifest_sha).casefold()
            coverage_status = args.coverage_status or manifest_status
            unresolved = (
                args.unresolved_high_potential
                if args.unresolved_high_potential is not None
                else manifest_unresolved
            )
            if len(discovery_sha) != 64 or any(
                char not in "0123456789abcdef" for char in discovery_sha
            ):
                raise RuntimeError("discovery SHA-256 must be 64 hexadecimal characters")
            effective = args.effective_date or latest_price_date(connection)
            report = evaluate(
                connection,
                effective,
                discovery_sha256=discovery_sha,
                coverage_status=coverage_status,
                unresolved_high_potential_count=unresolved,
            )
        if args.result_out:
            write_result(args.result_out, report)
        print(json.dumps(report, sort_keys=True, default=str))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
