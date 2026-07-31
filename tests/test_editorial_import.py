"""Unit tests for pipelines/editorial_import.py (A09 QC-A09-EDITORIAL-LOCALES).

Invariants under test:
1. Every required locale cell reaches a terminal status.
2. Missing locale content stays missing (value null) — never fabricated.
3. Stories never English-fallback.
4. English placeholder equal to English name is not counted as a translation.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "editorial_import.py"
SPEC = importlib.util.spec_from_file_location("cardz_editorial_import", MODULE_PATH)
assert SPEC and SPEC.loader
imp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = imp
SPEC.loader.exec_module(imp)


def _story(seed: str = "a") -> str:
    base = (
        "This promo recasts a known character through a clear release lane and "
        "collecting context that traders track beyond raw quotes. "
        "The exact printing, collaboration, and condition remain the demand drivers. "
    )
    text = base + (seed * 80)
    assert len(text) >= imp.STORY_MIN_CHARS
    return text


def _zh_tw(seed: str = "甲") -> str:
    base = (
        "這張宣傳卡以明確發行與收藏脈絡重新詮釋角色，需求超越單純報價。"
        "合作來源與保存狀態才是市場關注的重點。"
    )
    text = base + (seed * 80)
    assert len(text) >= imp.STORY_MIN_CHARS
    return text


def _zh_cn(seed: str = "乙") -> str:
    base = (
        "这张宣传卡以明确发行与收藏脉络重新诠释角色，需求超越单纯报价。"
        "合作来源与保存状态才是市场关注的重点。"
    )
    text = base + (seed * 80)
    assert len(text) >= imp.STORY_MIN_CHARS
    return text


def _ja(seed: str = "あ") -> str:
    base = (
        "このプロモは明確な発売経路と収集文脈でキャラクターを再解釈しています。"
        "コラボ由来と保存状態が需要を大きく左右します。"
    )
    text = base + (seed * 80)
    assert len(text) >= imp.STORY_MIN_CHARS
    return text


class ClassifyNameLocaleTests(unittest.TestCase):
    def test_en_ready_from_identity(self) -> None:
        cell = imp.classify_name_locale("en", "Pikachu", None)
        self.assertEqual("ready", cell.status)
        self.assertEqual("Pikachu", cell.value)

    def test_missing_translation_stays_null(self) -> None:
        cell = imp.classify_name_locale("zhTW", "Pikachu", None)
        self.assertEqual("missing", cell.status)
        self.assertIsNone(cell.value)
        self.assertEqual("translation_missing", cell.reason)

    def test_true_translation_ready(self) -> None:
        cell = imp.classify_name_locale("zhTW", "Pikachu", {"zhTW": "皮卡丘"})
        self.assertEqual("ready", cell.status)
        self.assertEqual("皮卡丘", cell.value)

    def test_english_placeholder_not_translation(self) -> None:
        cell = imp.classify_name_locale("ja", "Pikachu", {"ja": "Pikachu"})
        self.assertEqual("missing", cell.status)
        self.assertIsNone(cell.value)
        self.assertEqual("english_placeholder_not_translation", cell.reason)


class ClassifySetLocaleTests(unittest.TestCase):
    def test_set_translation_missing_not_fabricated(self) -> None:
        cell = imp.classify_set_locale(
            "zhCN",
            "Pokemon Card 151 Japanese",
            None,
            en_provenance="catalog_identity.setName",
        )
        self.assertEqual("missing", cell.status)
        self.assertIsNone(cell.value)


class ClassifyStoriesTests(unittest.TestCase):
    def test_missing_story_all_null(self) -> None:
        result = imp.classify_stories(None)
        self.assertEqual("missing", result["status"])
        self.assertFalse(result["producerEnglishFallbackEligible"])
        for locale in imp.REQUIRED_LOCALES:
            self.assertIsNone(result["values"][locale])

    def test_ready_four_locales(self) -> None:
        pack = {
            "status": "ready",
            "reviewReason": None,
            "evidence": ["asset_identity", "research_summary"],
            "evidenceSha256": "a" * 64,
            "stories": {
                "en": _story("e"),
                "zhTW": _zh_tw(),
                "zhCN": _zh_cn(),
                "ja": _ja(),
            },
        }
        result = imp.classify_stories(pack)
        self.assertEqual("ready", result["status"])
        self.assertEqual(4, sum(1 for v in result["values"].values() if v))

    def test_does_not_fill_story_with_english(self) -> None:
        pack = {
            "status": "ready",
            "stories": {
                "en": _story("e"),
                "zhTW": None,
                "zhCN": None,
                "ja": None,
            },
        }
        result = imp.classify_stories(pack)
        self.assertNotEqual("ready", result["status"])
        self.assertIsNone(result["values"]["zhTW"])
        self.assertIsNone(result["values"]["zhCN"])
        self.assertIsNone(result["values"]["ja"])
        # English remains ready only if long enough — but incomplete locales force review
        self.assertEqual("review_required", result["status"])

    def test_pack_review_required_with_null_stories(self) -> None:
        pack = {
            "status": "review_required",
            "reviewReason": "evidence_too_thin",
            "evidence": ["asset_identity"],
            "stories": {"en": None, "zhTW": None, "zhCN": None, "ja": None},
        }
        result = imp.classify_stories(pack)
        self.assertEqual("review_required", result["status"])
        self.assertIn("evidence_too_thin", result["reasons"])

    def test_not_distinct_locales(self) -> None:
        same = _zh_tw()
        pack = {
            "status": "ready",
            "stories": {
                "en": _story("e"),
                "zhTW": same,
                "zhCN": same,
                "ja": _ja(),
            },
        }
        result = imp.classify_stories(pack)
        self.assertEqual("review_required", result["status"])
        self.assertIn("stories_not_independently_localized", result["reasons"])


class ClassifyCardTests(unittest.TestCase):
    def test_card_terminal_and_no_fabrication(self) -> None:
        card = {
            "id": "cmc_test",
            "variantId": 1,
            "marketRank": 10,
            "tcg": "pokemon",
            "segment": "qualified",
            "facts": {
                "identity": {
                    "set": "QC Set Name",
                    "collectorNumber": "001",
                    "variantId": 1,
                }
            },
        }
        identity = {
            "variantId": 1,
            "opaqueId": "cmc_test",
            "name": "Pikachu",
            "setName": "Identity Set Name",
            "collectorNumber": "001",
            "tcg": "pokemon",
        }
        record = imp.classify_card(
            card,
            identity=identity,
            name_pack={"Pikachu": {"zhTW": "皮卡丘", "zhCN": "皮卡丘", "ja": "ピカチュウ"}},
            set_pack={},
            story_index={},
        )
        self.assertTrue(record["terminal"])
        self.assertIn(record["status"], imp.TERMINAL_STATUSES)
        # Set translations missing → null
        for locale in imp.TRANSLATED_LOCALES:
            self.assertIsNone(record["sets"]["values"][locale])
        # Stories missing → null, not English
        for locale in imp.REQUIRED_LOCALES:
            self.assertIsNone(record["stories"]["values"][locale])
        self.assertFalse(record["stories"]["producerEnglishFallbackEligible"])
        self.assertEqual("missing", record["outOfContractLocales"]["ko"]["status"])


class CohortAggregationTests(unittest.TestCase):
    def test_summarize_all_terminal(self) -> None:
        cards = [
            {
                "id": f"cmc_{i}",
                "variantId": i,
                "marketRank": i,
                "tcg": "pokemon",
                "segment": "qualified",
                "facts": {"identity": {"set": f"Set {i}", "collectorNumber": str(i)}},
            }
            for i in range(1, 4)
        ]
        identity = {
            i: {
                "variantId": i,
                "opaqueId": f"cmc_{i}",
                "name": f"Name {i}",
                "setName": f"Set {i}",
                "tcg": "pokemon",
                "collectorNumber": str(i),
            }
            for i in range(1, 4)
        }
        records = imp.classify_cohort(
            cards,
            identity_by_variant=identity,
            name_pack={},
            set_pack={},
            story_index={},
        )
        summary = imp.summarize_records(records)
        self.assertEqual(3, summary["cardCount"])
        self.assertTrue(summary["allTerminal"])
        self.assertEqual(0, summary["fabricatedMissingLocaleValues"])
        reasons = imp.aggregate_missing_review_reasons(records)
        self.assertGreater(len(reasons["reasonCounts"]), 0)


class WriteOutputsTests(unittest.TestCase):
    def test_writes_shard_json_and_jsonl(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        card = {
            "id": "cmc_x",
            "variantId": 9,
            "marketRank": 1,
            "tcg": "pokemon",
            "segment": "qualified",
            "facts": {"identity": {"set": "S", "collectorNumber": "1"}},
        }
        identity = {
            9: {
                "variantId": 9,
                "opaqueId": "cmc_x",
                "name": "X",
                "setName": "S",
                "tcg": "pokemon",
                "collectorNumber": "1",
            }
        }
        records = imp.classify_cohort(
            [card],
            identity_by_variant=identity,
            name_pack={},
            set_pack={},
            story_index={},
        )
        meta = {
            "workItemId": "QC-A09-EDITORIAL-LOCALES",
            "agentId": "A09",
            "waveId": "wave-3-domains",
            "baselineId": "baseline_qc_20260729_wave0_c1_v1",
            "cohortSha256": "abc",
            "asOf": "2026-07-29T09:02:59Z",
            "classifiedAt": "2026-07-29T12:00:00Z",
        }
        paths = imp.write_outputs(root, records=records, meta=meta)
        self.assertTrue(paths["shard_json"].is_file())
        self.assertTrue(paths["shard_jsonl"].is_file())
        self.assertTrue(paths["reasons"].is_file())
        self.assertTrue(paths["summary_md"].is_file())
        shard = json.loads(paths["shard_json"].read_text(encoding="utf-8"))
        self.assertEqual(1, len(shard["cards"]))
        lines = paths["shard_jsonl"].read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(1, len(lines))


if __name__ == "__main__":
    unittest.main()
