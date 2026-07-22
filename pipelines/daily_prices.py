"""Append-only daily price capture and CARDZ-owned window-change resolver."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SOURCE_PRIORITY = {
    "g10": 0,
    "snk": 1,
    "legacy": 2,
}

WINDOWS = {
    "change_1d_pct": (24 * 3600, 6 * 3600),
    "change_7d_pct": (7 * 24 * 3600, 12 * 3600),
    "change_30d_pct": (30 * 24 * 3600, 24 * 3600),
}

UPSTREAM_CHANGE_READY_HOURS = 30
UPSTREAM_CHANGE_STALE_HOURS = 48


@dataclass(frozen=True)
class PricePoint:
    observed_at: float
    price_usd: float
    source: str


def iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def epoch_from_iso(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def provider_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source.lower(), 99)


def select_current(points: Iterable[PricePoint], as_of: float) -> tuple[PricePoint | None, str]:
    usable = [point for point in points if point.price_usd > 0 and point.observed_at <= as_of]
    ready_candidates = [point for point in usable if as_of - point.observed_at <= 30 * 3600]
    stale_candidates = [point for point in usable if as_of - point.observed_at <= 48 * 3600]
    if ready_candidates:
        return (
            min(ready_candidates, key=lambda point: (provider_rank(point.source), as_of - point.observed_at)),
            "ready",
        )
    if stale_candidates:
        return (
            min(stale_candidates, key=lambda point: (provider_rank(point.source), as_of - point.observed_at)),
            "stale",
        )
    return None, "unavailable"


def resolve_change(
    points: Iterable[PricePoint],
    as_of: float,
    window_seconds: int,
    tolerance_seconds: int,
) -> dict[str, Any]:
    usable = [point for point in points if point.price_usd > 0 and point.observed_at <= as_of]
    if not usable:
        return {"value": None, "status": "unavailable", "asOf": None, "anchorAt": None}

    current, current_status = select_current(usable, as_of)
    if current is None:
        return {"value": None, "status": "unavailable", "asOf": None, "anchorAt": None}

    target = current.observed_at - window_seconds
    anchor_candidates = [
        point
        for point in usable
        if point.observed_at < current.observed_at and abs(point.observed_at - target) <= tolerance_seconds
    ]
    if not anchor_candidates:
        oldest = min(point.observed_at for point in usable)
        status = "accumulating" if oldest > target + tolerance_seconds else "unavailable"
        return {
            "value": None,
            "status": status,
            "asOf": iso_from_epoch(current.observed_at),
            "anchorAt": None,
        }

    same_source = [point for point in anchor_candidates if point.source == current.source]
    pool = same_source or anchor_candidates
    anchor = min(
        pool,
        key=lambda point: (
            abs(point.observed_at - target),
            point.observed_at > target,
            provider_rank(point.source),
        ),
    )
    value = round(((current.price_usd / anchor.price_usd) - 1.0) * 100.0, 6)
    return {
        "value": value,
        "status": current_status,
        "asOf": iso_from_epoch(current.observed_at),
        "anchorAt": iso_from_epoch(anchor.observed_at),
        "sourceMatched": anchor.source == current.source,
    }


def resolve_windows(points: Iterable[PricePoint], as_of: float) -> dict[str, dict[str, Any]]:
    # One close per provider/day prevents multiple intraday observations from
    # masquerading as the previous day's 1d anchor.
    materialized = collapse_daily(points)
    return {
        kind: resolve_change(materialized, as_of, window_seconds, tolerance_seconds)
        for kind, (window_seconds, tolerance_seconds) in WINDOWS.items()
    }


def resolve_change_30d_with_upstream(
    derived: dict[str, Any],
    upstream_value: float | int | None,
    upstream_observed_at: float | None,
    as_of: float,
    allow_upstream: bool,
) -> dict[str, Any]:
    """Prefer a ready CARDZ daily anchor, then a fresh exact-mapped upstream metric."""
    if derived.get("status") == "ready" and derived.get("value") is not None:
        return {**derived, "origin": "daily_ledger"}

    upstream: dict[str, Any] | None = None
    if (
        allow_upstream
        and isinstance(upstream_value, (int, float))
        and upstream_observed_at is not None
        and upstream_observed_at <= as_of
    ):
        age_hours = (as_of - upstream_observed_at) / 3600
        status = None
        if age_hours <= UPSTREAM_CHANGE_READY_HOURS:
            status = "ready"
        elif age_hours <= UPSTREAM_CHANGE_STALE_HOURS:
            status = "stale"
        if status:
            upstream = {
                "value": float(upstream_value),
                "status": status,
                "asOf": iso_from_epoch(upstream_observed_at),
                "anchorAt": None,
                "origin": "upstream",
            }

    if upstream and upstream["status"] == "ready":
        return upstream
    if derived.get("value") is not None:
        return {**derived, "origin": "daily_ledger"}
    if upstream:
        return upstream
    return {**derived, "origin": "daily_ledger"}


def collapse_daily(points: Iterable[PricePoint]) -> list[PricePoint]:
    """Keep the latest observation for each provider and UTC date."""
    selected: dict[tuple[str, str], PricePoint] = {}
    for point in points:
        key = (point.source, iso_from_epoch(point.observed_at)[:10])
        previous = selected.get(key)
        if previous is None or point.observed_at > previous.observed_at:
            selected[key] = point
    return sorted(selected.values(), key=lambda point: (point.observed_at, provider_rank(point.source)))


def deterministic_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def capture(connection: sqlite3.Connection, rows: list[dict[str, Any]], input_sha256: str) -> dict[str, int | str]:
    if not rows:
        raise ValueError("daily capture input is empty")
    normalized = sorted(rows, key=lambda row: (row["observedAt"], row["sourceCardRef"], row["source"]))
    for row in normalized:
        if row.get("source") not in SOURCE_PRIORITY:
            raise ValueError("daily capture source is not in the private provider priority contract")
        if not isinstance(row.get("sourceCardRef"), str) or not row["sourceCardRef"]:
            raise ValueError("daily capture requires a stable sourceCardRef")
        if not isinstance(row.get("priceUsd"), (int, float)) or row["priceUsd"] <= 0:
            raise ValueError("daily capture priceUsd must be positive")
    started_at = normalized[0]["observedAt"]
    completed_at = normalized[-1]["observedAt"]
    run_id = deterministic_id("price_run", input_sha256, started_at, completed_at)
    existing = connection.execute(
        "SELECT received_count, inserted_count, duplicate_count FROM price_capture_run WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    if existing:
        return {
            "runId": run_id,
            "received": int(existing[0]),
            "inserted": 0,
            "duplicates": int(existing[0]),
            "changeMetrics": 0,
            "replayed": 1,
        }
    inserted = 0
    duplicates = 0
    change_metrics = 0
    connection.execute(
        """INSERT OR IGNORE INTO price_capture_run
           VALUES (?, ?, ?, ?, ?, 0, 0, 'completed')""",
        (run_id, started_at, completed_at, input_sha256, len(normalized)),
    )
    for row in normalized:
        mapping = connection.execute(
            """SELECT m.printing_id, m.observation_id
               FROM source_identity_map m
               JOIN source_observation o ON o.observation_id = m.observation_id
               WHERE o.source_card_ref = ? AND m.mapping_status = 'exact'
               ORDER BY m.mapped_at DESC
               LIMIT 1""",
            (row["sourceCardRef"],),
        ).fetchone()
        if not mapping:
            continue
        printing_id = mapping[0]
        observation_id = deterministic_id(
            "price",
            printing_id,
            row["source"],
            row["observedAt"],
            row["priceUsd"],
        )
        cursor = connection.execute(
            """INSERT OR IGNORE INTO price_observation
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                printing_id,
                row["source"],
                row["sourceCardRef"],
                float(row["priceUsd"]),
                row["observedAt"],
                row["observedAt"][:10],
                run_id,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
        change_30d = row.get("change30dPct")
        if isinstance(change_30d, (int, float)):
            metric_id = deterministic_id(
                "metric",
                printing_id,
                "upstream_change_30d_pct",
                row["source"],
                row["observedAt"][:10],
            )
            metric_cursor = connection.execute(
                """INSERT OR IGNORE INTO metric_observation
                   VALUES (?, ?, 'upstream_change_30d_pct', ?, 'percent', 'ready', 0, ?, ?)""",
                (metric_id, printing_id, float(change_30d), row["observedAt"], mapping[1]),
            )
            change_metrics += int(bool(metric_cursor.rowcount))
    connection.execute(
        """UPDATE price_capture_run
           SET inserted_count = ?, duplicate_count = ? WHERE run_id = ?""",
        (inserted, duplicates, run_id),
    )
    connection.commit()
    return {
        "runId": run_id,
        "received": len(normalized),
        "inserted": inserted,
        "duplicates": duplicates,
        "changeMetrics": change_metrics,
        "replayed": 0,
    }


def self_test() -> int:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp()
    ready = resolve_change(
        [PricePoint(now, 110.0, "g10"), PricePoint(now - 24 * 3600, 100.0, "g10")],
        now,
        24 * 3600,
        3 * 3600,
    )
    accumulating = resolve_change(
        [PricePoint(now, 110.0, "g10"), PricePoint(now - 2 * 3600, 105.0, "g10")],
        now,
        24 * 3600,
        3 * 3600,
    )
    single_close = resolve_change(
        [PricePoint(now, 110.0, "g10")],
        now,
        24 * 3600,
        3 * 3600,
    )
    gap = resolve_change(
        [PricePoint(now, 110.0, "g10"), PricePoint(now - 40 * 3600, 100.0, "g10")],
        now,
        24 * 3600,
        3 * 3600,
    )
    fresh_fallback = resolve_change(
        [
            PricePoint(now - 40 * 3600, 200.0, "g10"),
            PricePoint(now - 1 * 3600, 110.0, "snk"),
            PricePoint(now - 25 * 3600, 100.0, "snk"),
        ],
        now,
        24 * 3600,
        6 * 3600,
    )
    earlier_tie = resolve_change(
        [
            PricePoint(now, 120.0, "g10"),
            PricePoint(now - 25 * 3600, 100.0, "g10"),
            PricePoint(now - 23 * 3600, 110.0, "g10"),
        ],
        now,
        24 * 3600,
        6 * 3600,
    )
    if ready["status"] != "ready" or ready["value"] != 10.0:
        raise AssertionError(f"ready window mismatch: {ready}")
    if accumulating["status"] != "accumulating" or accumulating["value"] is not None:
        raise AssertionError(f"accumulating window mismatch: {accumulating}")
    if single_close["status"] != "accumulating" or single_close["value"] is not None:
        raise AssertionError(f"single close must not self-anchor: {single_close}")
    if gap["status"] != "unavailable" or gap["value"] is not None:
        raise AssertionError(f"gap window mismatch: {gap}")
    if fresh_fallback["status"] != "ready" or fresh_fallback["value"] != 10.0:
        raise AssertionError(f"fresh fallback should beat stale primary: {fresh_fallback}")
    if earlier_tie["anchorAt"] != "2026-07-20T23:00:00Z" or earlier_tie["value"] != 20.0:
        raise AssertionError(f"earlier anchor must win an equal-distance tie: {earlier_tie}")

    derived_ready = {"value": 8.0, "status": "ready", "asOf": iso_from_epoch(now), "anchorAt": None}
    derived_wins = resolve_change_30d_with_upstream(
        derived_ready, -5.0, now - 3600, now, allow_upstream=True
    )
    upstream_bootstrap = resolve_change_30d_with_upstream(
        accumulating, -5.0, now - 3600, now, allow_upstream=True
    )
    blocked_upstream = resolve_change_30d_with_upstream(
        accumulating, -5.0, now - 3600, now, allow_upstream=False
    )
    if derived_wins["value"] != 8.0 or derived_wins["origin"] != "daily_ledger":
        raise AssertionError(f"ready daily ledger must win: {derived_wins}")
    if upstream_bootstrap["value"] != -5.0 or upstream_bootstrap["status"] != "ready":
        raise AssertionError(f"fresh exact upstream should bootstrap 30d: {upstream_bootstrap}")
    if blocked_upstream["value"] is not None:
        raise AssertionError(f"unmapped upstream must not bootstrap 30d: {blocked_upstream}")

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE source_observation(observation_id TEXT PRIMARY KEY, source_card_ref TEXT NOT NULL);
        CREATE TABLE source_identity_map(observation_id TEXT PRIMARY KEY, printing_id TEXT NOT NULL, mapping_status TEXT NOT NULL, mapped_at TEXT NOT NULL);
        CREATE TABLE metric_observation(metric_id TEXT PRIMARY KEY, printing_id TEXT NOT NULL, metric_kind TEXT NOT NULL, value REAL, unit TEXT NOT NULL, status TEXT NOT NULL, is_estimate INTEGER NOT NULL, observed_at TEXT, source_observation_id TEXT);
        CREATE TABLE price_capture_run(run_id TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT, input_sha256 TEXT, received_count INTEGER, inserted_count INTEGER, duplicate_count INTEGER, status TEXT);
        CREATE TABLE price_observation(observation_id TEXT PRIMARY KEY, printing_id TEXT, source_provider TEXT, source_card_ref TEXT, price_usd REAL, observed_at TEXT, observed_date TEXT, captured_run_id TEXT, UNIQUE(printing_id, source_provider, observed_date));
        INSERT INTO source_observation VALUES ('obs', 'private-1');
        INSERT INTO source_identity_map VALUES ('obs', 'cmc_123', 'exact', '2026-07-22T00:00:00Z');
        """
    )
    capture_rows = [
        {
            "sourceCardRef": "private-1",
            "priceUsd": 100,
            "change30dPct": -4.5,
            "observedAt": "2026-07-22T00:00:00Z",
            "source": "g10",
        }
    ]
    first = capture(connection, capture_rows, "abc")
    replay = capture(connection, capture_rows, "abc")
    if first["inserted"] != 1 or replay["inserted"] != 0 or replay["replayed"] != 1:
        raise AssertionError(f"idempotency mismatch: first={first}, replay={replay}")
    same_day_rows = [
        {
            "sourceCardRef": "private-1",
            "priceUsd": 101,
            "observedAt": "2026-07-22T12:00:00Z",
            "source": "g10",
        }
    ]
    same_day = capture(connection, same_day_rows, "different-batch")
    if same_day["inserted"] != 0 or same_day["duplicates"] != 1:
        raise AssertionError(f"daily uniqueness mismatch: {same_day}")
    collapsed = collapse_daily(
        [
            PricePoint(now - 10 * 3600, 100, "g10"),
            PricePoint(now - 2 * 3600, 101, "g10"),
            PricePoint(now - 1 * 3600, 99, "snk"),
        ]
    )
    if len(collapsed) != 2 or next(point for point in collapsed if point.source == "g10").price_usd != 101:
        raise AssertionError(f"daily collapse mismatch: {collapsed}")
    connection.close()
    print(
        json.dumps(
            {
                "ready": ready,
                "accumulating": accumulating,
                "singleClose": single_close,
                "windowKeys": list(WINDOWS),
                "gap": gap,
                "freshFallback": fresh_fallback,
                "earlierTie": earlier_tie,
                "derivedWins": derived_wins,
                "upstreamBootstrap": upstream_bootstrap,
                "blockedUpstream": blocked_upstream,
                "first": first,
                "replay": replay,
                "sameDay": same_day,
                "collapsedCount": len(collapsed),
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay a private daily price fixture into an explicit SQLite replay database"
    )
    parser.add_argument("--replay-db", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        return self_test()
    if not args.replay_db or not args.input:
        parser.error("--replay-db and --input are required")
    if "public" in {part.lower() for part in args.input.parts}:
        raise SystemExit("daily provider input must remain private")
    payload = args.input.read_bytes()
    rows = json.loads(payload.decode("utf-8"))
    if not isinstance(rows, list):
        raise SystemExit("daily input must be a JSON list")
    connection = sqlite3.connect(args.replay_db)
    result = capture(connection, rows, hashlib.sha256(payload).hexdigest())
    connection.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"daily capture failed: {error}", file=sys.stderr)
        raise
