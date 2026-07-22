"""Private GemRate client.

The API key is read only from GEMRATE_API_KEY. This module never writes the key,
request headers, or provider-native identifiers into public output.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_ROOT = "https://api.gemrate.com/v1"


def private_record_path(output: Path, identifier: str) -> Path:
    """Return an opaque, containment-checked filename for a provider record."""
    root = output.resolve()
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    destination = (root / f"{digest}.json").resolve()
    if destination.parent != root:
        raise ValueError("GemRate output escaped the private staging directory")
    return destination


class GemRateClient:
    def __init__(self, api_key: str | None = None, timeout_seconds: int = 30) -> None:
        self._api_key = api_key or os.environ.get("GEMRATE_API_KEY")
        if not self._api_key:
            raise RuntimeError("GEMRATE_API_KEY is not set")
        self._timeout_seconds = timeout_seconds

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        suffix = f"?{urllib.parse.urlencode(query)}" if query else ""
        request = urllib.request.Request(
            f"{API_ROOT}{path}{suffix}",
            headers={"x-api-key": self._api_key, "accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise RuntimeError(f"GemRate request failed with HTTP {error.code}") from error

    def population(self, gemrate_id: str) -> dict[str, Any]:
        return self._get(
            f"/cards/{urllib.parse.quote(gemrate_id, safe='')}/population",
            {"grader": "psa", "parsed_description": "true"},
        )

    def population_history(self, gemrate_id: str) -> dict[str, Any]:
        return self._get(
            f"/cards/{urllib.parse.quote(gemrate_id, safe='')}/population/history",
            {"grader": "psa"},
        )


def psa10_population(payload: dict[str, Any]) -> int:
    value = payload["data"]["population"]["population_data"]["by_grader"]["psa"]["grades"]["psa_10"]
    if not isinstance(value, int) or value < 0:
        raise ValueError("GemRate response has no valid PSA 10 population")
    return value


def exact_gemrate_refs(connection: sqlite3.Connection) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            """SELECT DISTINCT o.source_card_ref
               FROM source_observation o
               JOIN source_identity_map m ON m.observation_id = o.observation_id
               WHERE o.source_provider = 'gemrate' AND m.mapping_status = 'exact'
               ORDER BY o.source_card_ref"""
        )
    ]


def acquire_exact_populations(
    connection: sqlite3.Connection,
    observed_at: str,
    client: GemRateClient | None = None,
    delay: float = 0.12,
) -> dict[str, Any]:
    refs = exact_gemrate_refs(connection)
    if not refs:
        return {"status": "unavailable", "reason": "no_exact_gemrate_mappings", "rows": [], "failed": 0}
    if client is None and not os.environ.get("GEMRATE_API_KEY"):
        return {"status": "unavailable", "reason": "gemrate_key_not_configured", "rows": [], "failed": 0}
    api = client or GemRateClient()
    rows: list[dict[str, Any]] = []
    failed = 0
    for source_ref in refs:
        try:
            rows.append(
                {
                    "sourceCardRef": source_ref,
                    "populationPsa10": psa10_population(api.population(source_ref)),
                    "observedAt": observed_at,
                    "source": "gemrate",
                }
            )
        except (KeyError, TypeError, ValueError, RuntimeError, urllib.error.URLError, TimeoutError):
            failed += 1
        time.sleep(max(0.0, delay))
    return {
        "status": "ready" if rows else "unavailable",
        "reason": None if rows else "gemrate_refresh_failed",
        "rows": rows,
        "failed": failed,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch GemRate records into a private staging directory")
    parser.add_argument("--ids", type=Path, help="Private JSON list of GemRate IDs")
    parser.add_argument("--output", type=Path, help="Private output directory")
    parser.add_argument("--delay", type=float, default=0.35)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        fixture = {
            "data": {
                "population": {
                    "population_data": {
                        "by_grader": {"psa": {"grades": {"psa_10": 1234}}}
                    }
                }
            }
        }
        connection = sqlite3.connect(":memory:")
        connection.executescript(
            """
            CREATE TABLE source_observation(observation_id TEXT PRIMARY KEY, source_provider TEXT, source_card_ref TEXT);
            CREATE TABLE source_identity_map(observation_id TEXT PRIMARY KEY, printing_id TEXT, mapping_status TEXT);
            INSERT INTO source_observation VALUES ('one', 'gemrate', 'official-1');
            INSERT INTO source_identity_map VALUES ('one', 'cmc_1', 'exact');
            INSERT INTO source_observation VALUES ('two', 'gemrate', 'unreviewed');
            INSERT INTO source_identity_map VALUES ('two', 'cmc_2', 'demo_observed');
            """
        )

        class FixtureClient:
            def population(self, _identifier: str) -> dict[str, Any]:
                return fixture

        result = acquire_exact_populations(
            connection, "2026-07-22T00:00:00Z", FixtureClient(), delay=0
        )
        connection.close()
        if len(result["rows"]) != 1 or result["rows"][0]["populationPsa10"] != 1234:
            raise AssertionError(f"GemRate exact-mapping gate failed: {result}")
        traversal_probe = private_record_path(Path("private-staging"), "../outside")
        if traversal_probe.name != f"{hashlib.sha256(b'../outside').hexdigest()}.json":
            raise AssertionError("GemRate private filename hashing failed")
        print(json.dumps({"status": result["status"], "rows": len(result["rows"]), "psa10": 1234}))
        return 0

    if not args.ids or not args.output:
        parser.error("--ids and --output are required")

    if "public" in {part.lower() for part in args.output.parts}:
        raise SystemExit("GemRate responses must not be written under a public directory")

    identifiers = json.loads(args.ids.read_text(encoding="utf-8"))
    if not isinstance(identifiers, list) or not all(isinstance(value, str) for value in identifiers):
        raise SystemExit("--ids must contain a JSON string list")

    client = GemRateClient()
    args.output.mkdir(parents=True, exist_ok=True)
    for identifier in identifiers:
        record = {
            "sourceCardRef": identifier,
            "population": client.population(identifier),
            "populationHistory": client.population_history(identifier),
        }
        destination = private_record_path(args.output, identifier)
        destination.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True), encoding="utf-8")
        time.sleep(max(0.0, args.delay))
    print(json.dumps({"fetched": len(identifiers), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
