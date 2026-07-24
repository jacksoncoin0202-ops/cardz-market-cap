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

POLICY_VERSION = "candidate-v4-complete"
INDEX_CODE = "tcg-combined"
INDEX_VERSION = "psa10-v3-complete"
DEFAULT_DISCOVERY = Path(__file__).resolve().parents[1] / "data" / "runtime" / "private-source-map" / "discovery-radar.json"
PRICE_SOURCE_PRIORITY = {"snk_psa10": 10, "snk": 10, "g10": 20}
POPULATION_SOURCE_PRIORITY = {"gemrate": 10, "g10": 20}
PRE_ENTRY_POPULATION_MINIMUM = 971
PRE_ENTRY_POPULATION_MAXIMUM = 999
CURRENT_POPULATION_MAX_GAP_DAYS = 2


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        observed = row["observed_date"]
        if isinstance(observed, datetime):
            observed = observed.date()
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
            -(row["observed_date"].toordinal()),
            source_priority.get(str(row.get("source_code") or "").casefold(), 100),
            -int(row.get("id") or 0),
        ),
    )


def load_candidates(connection: Any, effective_date: date) -> tuple[int, list[CandidateSnapshot]]:
    with connection.cursor() as cursor:
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
        members = list(cursor.fetchall())
        if not members:
            raise RuntimeError("current market universe is missing")
        variant_ids = [int(row["variant_id"]) for row in members]
        placeholders = ",".join(["%s"] * len(variant_ids))
        since = effective_date - timedelta(days=35)
        cursor.execute(
            f"""
            SELECT id, variant_id, source_code, observed_date, effective_at,
                   price_usd, metric_status, source_priority
            FROM market_price_observation
            WHERE variant_id IN ({placeholders}) AND observed_date BETWEEN %s AND %s
            ORDER BY variant_id, observed_date, id
            """,
            (*variant_ids, since, effective_date),
        )
        price_rows = list(cursor.fetchall())
        cursor.execute(
            f"""
            SELECT id, variant_id, source_code, observed_date, effective_at,
                   top_grade_population, estimated
            FROM market_grader_population_observation
            WHERE grader_code = 'PSA' AND variant_id IN ({placeholders})
              AND observed_date BETWEEN %s AND %s
            ORDER BY variant_id, observed_date, id
            """,
            (*variant_ids, since, effective_date),
        )
        population_rows = list(cursor.fetchall())

    prices: dict[int, list[Mapping[str, Any]]] = {}
    populations: dict[int, list[Mapping[str, Any]]] = {}
    for row in price_rows:
        prices.setdefault(int(row["variant_id"]), []).append(row)
    for row in population_rows:
        populations.setdefault(int(row["variant_id"]), []).append(row)

    raw: list[dict[str, Any]] = []
    for member in members:
        variant_id = int(member["variant_id"])
        current_price = pick_observation(
            prices.get(variant_id, []), effective_date, PRICE_SOURCE_PRIORITY,
            max_gap_days=2, value_key="price_usd",
        )
        current_population = pick_observation(
            populations.get(variant_id, []), effective_date, POPULATION_SOURCE_PRIORITY,
            max_gap_days=CURRENT_POPULATION_MAX_GAP_DAYS, value_key="top_grade_population",
        )
        price = float(current_price["price_usd"]) if current_price else None
        population = int(current_population["top_grade_population"]) if current_population else None
        cap = price * population if price is not None and population is not None else None

        def prior_price(days: int) -> float | None:
            row = pick_observation(
                prices.get(variant_id, []), effective_date - timedelta(days=days), PRICE_SOURCE_PRIORITY,
                max_gap_days=3, value_key="price_usd",
            )
            return float(row["price_usd"]) if row else None

        def prior_population(days: int) -> int | None:
            row = pick_observation(
                populations.get(variant_id, []), effective_date - timedelta(days=days), POPULATION_SOURCE_PRIORITY,
                max_gap_days=3, value_key="top_grade_population",
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
    return int(members[0]["universe_lock_id"]), snapshots


def snapshot_hash(snapshot: CandidateSnapshot) -> str:
    return hashlib.sha256(canonical_json(asdict(snapshot))).hexdigest()


def tracked_indexes(
    snapshots: Sequence[CandidateSnapshot],
    tcg_by_variant: Mapping[int, str],
) -> dict[str, list[CandidateSnapshot]]:
    ready = [
        row for row in snapshots
        if row.metric_status == "ready"
        and row.psa10_population is not None
        and row.psa10_population >= 1000
        and row.market_cap_usd is not None
    ]
    key = lambda row: (-float(row.market_cap_usd or 0), row.variant_id)
    return {
        "tcg-combined": sorted(ready, key=key),
        "pokemon": sorted(
            (row for row in ready if tcg_by_variant.get(row.variant_id) == "pokemon"),
            key=key,
        ),
        "one-piece": sorted(
            (row for row in ready if tcg_by_variant.get(row.variant_id) == "one-piece"),
            key=key,
        ),
    }


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
            JOIN market_alert_evaluation e ON e.id = s.evaluation_id
            WHERE e.index_code=%s AND e.index_version=%s AND e.policy_version=%s
              AND e.effective_date < %s
              AND e.effective_date = (
                SELECT MAX(e2.effective_date) FROM market_alert_evaluation e2
                WHERE e2.index_code=e.index_code AND e2.index_version=e.index_version
                  AND e2.policy_version=e.policy_version AND e2.effective_date < %s
              )
            """,
            (INDEX_CODE, INDEX_VERSION, POLICY_VERSION, effective_date, effective_date),
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
) -> dict[str, Any]:
    if coverage_status == "certified" and unresolved_high_potential_count:
        raise RuntimeError("certified coverage cannot contain unresolved high-potential candidates")
    if unresolved_high_potential_count:
        coverage_status = "blocked"
    lock_id, snapshots = load_candidates(connection, effective_date)
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
                SELECT id, input_sha256, coverage_status, eligible_count
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
                connection.rollback()
                return {
                    "status": "reused",
                    "evaluationId": int(existing["id"]),
                    "effectiveDate": effective_date.isoformat(),
                    "coverageStatus": existing["coverage_status"],
                    "eligible": int(existing["eligible_count"]),
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

            if cutoff is not None:
                cursor.execute("SELECT id FROM market_ingest_run WHERE status='complete' ORDER BY effective_at DESC,id DESC LIMIT 1")
                run = cursor.fetchone()
                if run:
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
                    indexes = tracked_indexes(snapshots, tcg_by_variant)
                    for index_code, rows in indexes.items():
                        if not rows:
                            continue
                        index_hash = hashlib.sha256(canonical_json([asdict(row) for row in rows])).hexdigest()
                        cursor.execute(
                            """
                            INSERT IGNORE INTO market_index_snapshot
                                (run_id,index_code,index_version,effective_at,effective_date,constituent_count,
                                 total_market_cap_usd,snapshot_sha256)
                            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                            """,
                            (run["id"], index_code, INDEX_VERSION,
                             datetime.combine(effective_date, datetime.min.time()), effective_date,
                             len(rows), sum(row.market_cap_usd or 0 for row in rows), index_hash),
                        )
                        if not cursor.rowcount:
                            continue
                        index_id = int(cursor.lastrowid)
                        for rank, row in enumerate(rows, start=1):
                            cursor.execute(
                                """
                                INSERT INTO market_index_constituent
                                    (index_snapshot_id,variant_id,rank_position,reference_price_usd,
                                     psa10_population,market_cap_usd,change_30d_pct,metric_status)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                                """,
                                (index_id, row.variant_id, rank, row.reference_price_usd,
                                 row.psa10_population, row.market_cap_usd, row.change_30d_pct,
                                 row.metric_status),
                            )
        connection.commit()
        return {
            "status": "evaluated",
            "evaluationId": evaluation_id,
            "effectiveDate": effective_date.isoformat(),
            "coverageStatus": coverage_status,
            "eligible": len(eligible),
            "cutoffUsd": cutoff,
            "candidates": len(snapshots),
            **events,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT RELEASE_LOCK('cardz_market_alert_evaluation')")
        connection.commit()


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate CARDZ daily market candidate alerts")
    add_connection_args(parser)
    parser.add_argument("--effective-date", type=date.fromisoformat)
    parser.add_argument("--discovery-manifest", type=Path, default=DEFAULT_DISCOVERY)
    parser.add_argument("--discovery-max-age-hours", type=float, default=48)
    parser.add_argument("--discovery-sha256")
    parser.add_argument("--coverage-status", choices=("certified", "observed", "blocked"))
    parser.add_argument("--unresolved-high-potential", type=int)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    manifest_sha, manifest_status, manifest_unresolved = discovery_state(
        args.discovery_manifest.resolve(), args.discovery_max_age_hours
    )
    discovery_sha = (args.discovery_sha256 or manifest_sha).casefold()
    coverage_status = args.coverage_status or manifest_status
    unresolved = args.unresolved_high_potential if args.unresolved_high_potential is not None else manifest_unresolved
    if len(discovery_sha) != 64 or any(char not in "0123456789abcdef" for char in discovery_sha):
        raise RuntimeError("discovery SHA-256 must be 64 hexadecimal characters")
    from db_runtime import connection_from_args

    connection = connection_from_args(args)
    try:
        effective = args.effective_date or latest_price_date(connection)
        report = evaluate(
            connection,
            effective,
            discovery_sha256=discovery_sha,
            coverage_status=coverage_status,
            unresolved_high_potential_count=unresolved,
        )
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
