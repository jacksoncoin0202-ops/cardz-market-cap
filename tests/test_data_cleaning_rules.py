from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from data_cleaning_rules import DataCleaningRuleError, explain_rule, load_and_validate  # noqa: E402


class DataCleaningRulesTests(unittest.TestCase):
    def test_raw_landing_is_immutable_and_mysql_uses_only_pointers(self) -> None:
        rules = load_and_validate()
        raw = next(layer for layer in rules["layers"] if layer["id"] == "raw_landing")
        self.assertEqual(raw["mysqlPolicy"], "no_raw_payload_body")
        self.assertEqual(rules["mysql"]["rawPayloadStorage"], "private_pointer_only_after_ingest")
        self.assertEqual(
            rules["mysql"]["rawPayloadPointerTables"],
            ["market_raw_payload_object", "market_source_observation_payload_pointer", "market_source_effective_observation"],
        )

    def test_population_bands_keep_formal_pre_entry_and_discovery_separate(self) -> None:
        rules = load_and_validate()
        bands = rules["populationBands"]
        self.assertEqual(bands["formalRanking"]["minimumInclusivePsa10Population"], 1000)
        self.assertEqual(bands["preEntryRadar"]["minimumInclusivePsa10Population"], 971)
        self.assertEqual(bands["preEntryRadar"]["maximumInclusivePsa10Population"], 999)
        self.assertEqual(bands["discoveryOnly"]["maximumInclusivePsa10Population"], 970)

    def test_rejects_any_lowered_pre_entry_or_formal_population_gate(self) -> None:
        rules = load_and_validate()
        invalid = copy.deepcopy(rules)
        invalid["populationBands"]["preEntryRadar"]["minimumInclusivePsa10Population"] = 970
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "rules.json"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            with self.assertRaisesRegex(DataCleaningRuleError, "971-999"):
                load_and_validate(path)

    def test_explain_population_exposes_gemrate_authority(self) -> None:
        explanation = explain_rule("psa10_population")
        self.assertEqual(explanation["kind"], "dataCleaningMetric")
        self.assertEqual(explanation["value"]["authority"], "gemrate")
        self.assertEqual(explanation["value"]["requiredGrade"], "10")


if __name__ == "__main__":
    unittest.main()
