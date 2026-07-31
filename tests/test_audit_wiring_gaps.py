from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "audit_wiring_gaps",
    ROOT / "scripts" / "audit_wiring_gaps.py",
)
assert SPEC and SPEC.loader
audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit)


class ScalarCursor:
    def __init__(self, values: list[int]) -> None:
        self.values = iter(values)
        self.queries: list[str] = []

    def execute(self, query: str) -> None:
        self.queries.append(query)

    def fetchone(self) -> dict[str, int]:
        return {"value": next(self.values)}


class WiringCoverageTests(unittest.TestCase):
    def test_price_coverage_counts_only_current_universe_members(self) -> None:
        cursor = ScalarCursor([261, 339, 70])
        result = audit.probe_price_coverage(cursor, 1468)
        self.assertEqual(result["rows"], 339)
        self.assertEqual(result["exactIdentityRows"], 261)
        self.assertEqual(result["freshRows"], 70)
        self.assertTrue(all("market_universe_member" in query for query in cursor.queries))

    def test_population_daily_peak_intersects_the_current_universe(self) -> None:
        cursor = ScalarCursor([1211])
        result = audit.probe_pop_coverage(cursor, 1468)
        self.assertEqual(result["rows"], 1211)
        self.assertIn("market_universe_member", cursor.queries[0])


if __name__ == "__main__":
    unittest.main()
