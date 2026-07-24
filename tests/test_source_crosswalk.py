from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PIPELINES = ROOT / "pipelines"
sys.path.insert(0, str(PIPELINES))
MODULE_PATH = PIPELINES / "source_crosswalk.py"
SPEC = importlib.util.spec_from_file_location("cardz_source_crosswalk", MODULE_PATH)
assert SPEC and SPEC.loader
crosswalk = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = crosswalk
SPEC.loader.exec_module(crosswalk)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class SourceCrosswalkTests(unittest.TestCase):
    def test_collector_evidence_normalizer_only_equates_explicit_formats(self) -> None:
        self.assertTrue(crosswalk.same_collector_number_evidence("110/80", "110/080"))
        self.assertTrue(crosswalk.same_collector_number_evidence("1/25", "001/025"))
        self.assertTrue(crosswalk.same_collector_number_evidence("SM-P 288", "288/SM-P"))
        self.assertTrue(crosswalk.same_collector_number_evidence("290 SM-P", "290/SM-P"))
        self.assertTrue(crosswalk.same_collector_number_evidence("SVP EN 085", "085/SVP"))

        # A bare number must never inherit a set suffix, and only the explicit
        # EN token above is ignored for comparison evidence.
        self.assertFalse(crosswalk.same_collector_number_evidence("085", "085/SVP"))
        self.assertFalse(crosswalk.same_collector_number_evidence("SVP JP 085", "085/SVP"))
        self.assertFalse(crosswalk.same_collector_number_evidence("SM-P XY-P 288", "288/SM-P"))

    def test_direct_receipt_proposal_confirms_only_complete_candidate_evidence(self) -> None:
        gemrate_id = "a" * 40
        payload = {"data": {"gemrate_id": gemrate_id, "parsed_description": {"cardNumber": "OP01-120"}}}
        receipt = {
            "requestedGemrateId": gemrate_id, "entityGemrateId": gemrate_id,
            "payloadSha256": __import__("hashlib").sha256(crosswalk.stable_json(payload)).hexdigest(),
            "parsedDescription": {"cardNumber": "OP01-120"}, "graders": {},
        }
        candidate = {
            "gemrateId": gemrate_id, "tcg": "one-piece", "setEvidence": "Romance Dawn",
            "collectorNumberEvidence": "OP01-120", "languageEvidence": "Japanese",
            "editionEvidence": "standard", "parallelEvidence": "Manga", "finishEvidence": "foil",
        }
        proposal = crosswalk.build_gemrate_direct_identity_proposal(candidate, receipt, payload)
        self.assertEqual(proposal["identityStatus"], "exact_confirmed")
        self.assertEqual(proposal["canonicalIdentity"]["collectorNumber"], "OP01-120")

    def test_direct_receipt_description_cannot_fill_missing_candidate_identity(self) -> None:
        gemrate_id = "b" * 40
        payload = {"data": {"gemrate_id": gemrate_id, "parsed_description": {
            "setName": "Romance Dawn", "cardNumber": "OP01-120", "language": "Japanese",
            "edition": "standard", "parallel": "Manga", "finish": "foil",
        }}}
        receipt = {
            "requestedGemrateId": gemrate_id, "entityGemrateId": gemrate_id,
            "payloadSha256": __import__("hashlib").sha256(crosswalk.stable_json(payload)).hexdigest(),
            "parsedDescription": payload["data"]["parsed_description"], "graders": {},
        }
        proposal = crosswalk.build_gemrate_direct_identity_proposal(
            {"gemrateId": gemrate_id, "tcg": "one-piece"}, receipt, payload,
        )
        self.assertEqual(proposal["identityStatus"], "review")
        self.assertIn("collector_number_incomplete", proposal["reviewReasons"])
        self.assertIn("language_unavailable", proposal["reviewReasons"])
    def make_source(self, root: Path) -> None:
        write_json(
            root / "index" / "ptcg" / "constituents.json",
            {
                "rows": [
                    {"name": "Pikachu 085", "lang": "EN", "url": "https://private.invalid/card/snkrdunk/101"},
                    {"name": "Pikachu 085", "lang": "JP", "url": "https://private.invalid/card/snkrdunk/102"},
                    {"name": "Guess Me 227", "lang": "JP", "url": "https://private.invalid/card/snkrdunk/103"},
                ]
            },
        )
        write_json(
            root / "index" / "opcg" / "constituents.json",
            {"rows": [{"name": "Luffy", "lang": "JP", "url": "https://private.invalid/card/snkrdunk/201"}]},
        )
        assets = {
            "101": {"cardName": "Pikachu", "cardId": "085/SVP", "language": "English", "setName": "Scarlet & Violet Promo", "edition": "standard", "parallel": "", "finish": "holo"},
            "102": {"cardName": "Pikachu", "cardId": "085/SVP", "language": "Japanese", "setName": "Scarlet & Violet Promo", "edition": "standard", "parallel": "", "finish": "holo"},
            # A bare number must not silently inherit a promo suffix from its name.
            "103": {"cardName": "Guess Me", "cardId": "227", "language": "Japanese", "setName": "Promo"},
            # The row says JP while the canonical asset says EN: do not guess which wins.
            "201": {"cardName": "Luffy", "cardId": "OP01-120", "language": "English", "setName": "Romance Dawn"},
        }
        for external_id, asset in assets.items():
            card = root / "cards" / "snkrdunk" / external_id
            write_json(card / "asset_info.json", asset)
            write_json(card / "populations.json", {"source": f"https://example.invalid/card/{external_id:0>40}"})

    def test_exact_identity_builds_key_and_quarantines_incomplete_or_conflicting_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            document = crosswalk.build_crosswalk(root, datetime(2026, 7, 24, tzinfo=timezone.utc))

        cards = {str(card["canonicalExternalId"]): card for card in document["cards"]}
        accepted = cards["101"]
        self.assertEqual(accepted["identityStatus"], "confirmed")
        self.assertEqual(accepted["canonicalPrintingKey"], "pokemon|scarlet & violet promo|085/svp|en|standard||holo")
        self.assertEqual(cards["103"]["identityStatus"], "review")
        self.assertIsNone(cards["103"]["canonicalPrintingKey"])
        self.assertIn("collector_number_incomplete", cards["103"]["reviewReasons"])
        self.assertEqual(cards["201"]["identityStatus"], "review")
        self.assertIn("language_conflict", cards["201"]["reviewReasons"])
        queue = document["identityReviewQueue"]
        self.assertEqual([(row["externalId"], row["reasons"]) for row in queue], [
            ("103", ["collector_number_incomplete"]),
            ("201", ["language_conflict"]),
        ])
        self.assertEqual(document["counts"]["identityConfirmed"], 2)
        self.assertEqual(document["counts"]["identityReview"], 2)

    def test_crosswalk_is_deterministic_and_duplicate_printing_keys_are_reviewed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.make_source(root)
            # Both source records now claim the same exact English printing.
            duplicate = root / "cards" / "snkrdunk" / "102" / "asset_info.json"
            asset = json.loads(duplicate.read_text(encoding="utf-8"))
            asset["language"] = "English"
            duplicate.write_text(json.dumps(asset), encoding="utf-8")
            index = root / "index" / "ptcg" / "constituents.json"
            rows = json.loads(index.read_text(encoding="utf-8"))
            rows["rows"][1]["lang"] = "EN"
            index.write_text(json.dumps(rows), encoding="utf-8")
            first = crosswalk.build_crosswalk(root, datetime(2026, 7, 24, tzinfo=timezone.utc))
            second = crosswalk.build_crosswalk(root, datetime(2026, 7, 24, tzinfo=timezone.utc))

        self.assertEqual(first["payloadSha256"], second["payloadSha256"])
        cards = {str(card["canonicalExternalId"]): card for card in first["cards"]}
        self.assertEqual(cards["102"]["identityStatus"], "review")
        self.assertIn("duplicate_canonical_printing_key", cards["102"]["reviewReasons"])
        self.assertEqual(cards["101"]["identityStatus"], "review")
        self.assertEqual(first["counts"]["identityConfirmed"], 0)


if __name__ == "__main__":
    unittest.main()
