"""Append-only private population capture and freshness resolver."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


SOURCE_PRIORITY = {"gemrate": 0, "g10": 1, "legacy": 2}
READY_SECONDS = 48 * 3600
STALE_SECONDS = 7 * 24 * 3600


@dataclass(frozen=True)
class PopulationPoint:
    observed_at: float
    population: int
    source: str


def iso_from_epoch(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def deterministic_id(prefix: str, *parts: object) -> str:
    payload = "\x1f".join(str(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:32]}"


def provider_rank(source: str) -> int:
    return SOURCE_PRIORITY.get(source.lower(), 99)


def select_population(
    points: Iterable[PopulationPoint], as_of: float
) -> tuple[PopulationPoint | None, str]:
    usable = [point for point in points if point.population >= 0 and point.observed_at <= as_of]
    ready = [point for point in usable if as_of - point.observed_at <= READY_SECONDS]
    stale = [point for point in usable if as_of - point.observed_at <= STALE_SECONDS]
    if ready:
        return min(ready, key=lambda point: (provider_rank(point.source), as_of - point.observed_at)), "ready"
    if stale:
        return min(stale, key=lambda point: (provider_rank(point.source), as_of - point.observed_at)), "stale"
    return None, "unavailable"


def capture_population(
    connection: sqlite3.Connection, rows: list[dict[str, Any]], input_sha256: str
) -> dict[str, int | str]:
    if not rows:
        raise ValueError("population capture input is empty")
    normalized = sorted(rows, key=lambda row: (row["observedAt"], row["sourceCardRef"], row["source"]))
    for row in normalized:
        if row.get("source") not in SOURCE_PRIORITY:
            raise ValueError("population source is not in the private provider priority contract")
        if not isinstance(row.get("sourceCardRef"), str) or not row["sourceCardRef"]:
            raise ValueError("population capture requires a stable sourceCardRef")
        if not isinstance(row.get("populationPsa10"), int) or row["populationPsa10"] < 0:
            raise ValueError("populationPsa10 must be a non-negative integer")

    started_at = normalized[0]["observedAt"]
    completed_at = normalized[-1]["observedAt"]
    run_id = deterministic_id("population_run", input_sha256, started_at, completed_at)
    existing = connection.execute(
        "SELECT received_count FROM population_capture_run WHERE run_id = ?", (run_id,)
    ).fetchone()
    if existing:
        return {
            "runId": run_id,
            "received": int(existing[0]),
            "inserted": 0,
            "duplicates": int(existing[0]),
            "replayed": 1,
        }

    connection.execute(
        """INSERT INTO population_capture_run
           VALUES (?, ?, ?, ?, ?, 0, 0, 'completed')""",
        (run_id, started_at, completed_at, input_sha256, len(normalized)),
    )
    inserted = 0
    duplicates = 0
    for row in normalized:
        mapping = connection.execute(
            """SELECT m.printing_id
               FROM source_identity_map m
               JOIN source_observation o ON o.observation_id = m.observation_id
               WHERE o.source_card_ref = ? AND m.mapping_status = 'exact'
               ORDER BY m.mapped_at DESC LIMIT 1""",
            (row["sourceCardRef"],),
        ).fetchone()
        if not mapping:
            continue
        printing_id = mapping[0]
        observation_id = deterministic_id(
            "population",
            printing_id,
            row["source"],
            row["observedAt"][:10],
        )
        cursor = connection.execute(
            """INSERT OR IGNORE INTO population_observation
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                observation_id,
                printing_id,
                row["source"],
                row["sourceCardRef"],
                int(row["populationPsa10"]),
                row["observedAt"],
                row["observedAt"][:10],
                run_id,
            ),
        )
        if cursor.rowcount:
            inserted += 1
        else:
            duplicates += 1
    connection.execute(
        "UPDATE population_capture_run SET inserted_count = ?, duplicate_count = ? WHERE run_id = ?",
        (inserted, duplicates, run_id),
    )
    connection.commit()
    return {
        "runId": run_id,
        "received": len(normalized),
        "inserted": inserted,
        "duplicates": duplicates,
        "replayed": 0,
    }


def self_test() -> int:
    now = datetime(2026, 7, 22, tzinfo=timezone.utc).timestamp()
    official, official_status = select_population(
        [PopulationPoint(now - 3600, 1200, "g10"), PopulationPoint(now - 7200, 1300, "gemrate")],
        now,
    )
    fallback, fallback_status = select_population(
        [PopulationPoint(now - 72 * 3600, 1300, "gemrate"), PopulationPoint(now - 3600, 1200, "g10")],
        now,
    )
    stale, stale_status = select_population([PopulationPoint(now - 72 * 3600, 1100, "legacy")], now)
    if official is None or official.population != 1300 or official_status != "ready":
        raise AssertionError("fresh GemRate population must have priority")
    if fallback is None or fallback.population != 1200 or fallback_status != "ready":
        raise AssertionError("fresh G10 must beat stale GemRate")
    if stale is None or stale.population != 1100 or stale_status != "stale":
        raise AssertionError("last-good population must remain stale for seven days")

    connection = sqlite3.connect(":memory:")
    connection.executescript(
        """
        CREATE TABLE source_observation(observation_id TEXT PRIMARY KEY, source_card_ref TEXT NOT NULL);
        CREATE TABLE source_identity_map(observation_id TEXT PRIMARY KEY, printing_id TEXT NOT NULL, mapping_status TEXT NOT NULL, mapped_at TEXT NOT NULL);
        CREATE TABLE population_capture_run(run_id TEXT PRIMARY KEY, started_at TEXT, completed_at TEXT, input_sha256 TEXT, received_count INTEGER, inserted_count INTEGER, duplicate_count INTEGER, status TEXT);
        CREATE TABLE population_observation(observation_id TEXT PRIMARY KEY, printing_id TEXT, source_provider TEXT, source_card_ref TEXT, population INTEGER, observed_at TEXT, observed_date TEXT, captured_run_id TEXT, UNIQUE(printing_id, source_provider, observed_date));
        INSERT INTO source_observation VALUES ('mapped', 'gemrate-native-1');
        INSERT INTO source_identity_map VALUES ('mapped', 'cmc_1', 'exact', '2026-07-22T00:00:00Z');
        """
    )
    rows = [{
        "sourceCardRef": "gemrate-native-1",
        "populationPsa10": 1500,
        "observedAt": "2026-07-22T00:00:00Z",
        "source": "gemrate",
    }]
    first = capture_population(connection, rows, "fixture")
    replay = capture_population(connection, rows, "fixture")
    connection.close()
    if first["inserted"] != 1 or replay["replayed"] != 1:
        raise AssertionError(f"population capture must be idempotent: {first}, {replay}")
    print(json.dumps({
        "official": {"value": official.population, "status": official_status},
        "fallback": {"value": fallback.population, "status": fallback_status},
        "stale": {"value": stale.population, "status": stale_status},
        "first": first,
        "replay": replay,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(self_test())
