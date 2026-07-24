from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_routing import DEFAULT_ROUTES, load_and_validate  # noqa: E402


class DataRoutingTests(unittest.TestCase):
    def test_repository_contract_binds_population_to_gemrate_only(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        population = next(route for route in document["routes"] if route["metric"] == "psa10_population")
        self.assertEqual(population["primary"], "gemrate")
        self.assertEqual(population["fallback"], [])
        self.assertEqual(population["failureMode"], "exclude_from_ranking")

    def test_non_gemrate_population_fallback_is_rejected(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        population = next(route for route in document["routes"] if route["metric"] == "psa10_population")
        population["fallback"] = ["g10"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "non-GemRate fallback"):
                load_and_validate(path)

    def test_price_and_sales_routes_do_not_promote_g10(self) -> None:
        document = load_and_validate(DEFAULT_ROUTES)
        routes = {route["metric"]: route for route in document["routes"]}
        price = routes["psa10_reference_price"]
        self.assertEqual(price["primary"], "snk")
        self.assertEqual(price["secondary"], ["ebay_psa10_sold_validation"])
        self.assertEqual(price["fallback"], [])
        self.assertEqual(price["bootstrapEvidence"], ["grade10_last_good"])
        sales = routes["tracked_sales"]
        self.assertEqual(sales["primary"], "snk_recent_trades")
        self.assertEqual(sales["secondary"], ["ebay_psa10_sold"])
        self.assertEqual(sales["bootstrapEvidence"], ["grade10_ebay_sale_history"])

    def test_g10_cannot_become_reference_price_fallback(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        price = next(route for route in document["routes"] if route["metric"] == "psa10_reference_price")
        price["fallback"] = ["grade10_last_good"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "without a G10 ranking fallback"):
                load_and_validate(path)

    def test_registry_requires_valid_data_cleaning_policy(self) -> None:
        document = json.loads(DEFAULT_ROUTES.read_text(encoding="utf-8"))
        document["dataCleaningPolicy"]["rulesPath"] = "config/not-a-rule.json"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "routes.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "data-cleaning rules invalid"):
                load_and_validate(path)


if __name__ == "__main__":
    unittest.main()
