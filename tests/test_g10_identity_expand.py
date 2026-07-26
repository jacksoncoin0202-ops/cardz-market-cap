from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_identity_expand.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_identity_expand", MODULE_PATH)
assert SPEC and SPEC.loader
expand = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = expand
SPEC.loader.exec_module(expand)


def variant(vid: int, name: str, set_name: str, collector: str, language: str = "ja"):
    return expand.VariantRow(
        id=vid, canonical_name=name, set_name=set_name,
        collector_number=collector, card_language=language,
    )


def asset(
    directory: str = "100090",
    provider: str = "snkrdunk",
    gemrate_id: str | None = None,
    card_name: str | None = "Pikachu Munch",
    set_name: str | None = "SM-P",
    collector_number: str | None = "SM-P 288",
    language: str | None = "jp",
):
    return expand.G10Asset(
        provider=provider, directory=directory, gemrate_id=gemrate_id,
        card_name=card_name, set_name=set_name, collector_number=collector_number,
        language=language, source_pointer=f"g10/{provider}/{directory}/populations.json",
        observed_at=datetime(2026, 7, 25, 21, 46, 50),
    )


class NormalisationTests(unittest.TestCase):
    def test_collector_number_is_order_and_padding_insensitive(self) -> None:
        # 呢兩對係 G10 vs DB 真實出現過嘅寫法差異。
        self.assertEqual(expand.normalize_collector("SM-P 288"), expand.normalize_collector("288/SM-P"))
        self.assertEqual(expand.normalize_collector("56/76"), expand.normalize_collector("056/076"))
        self.assertEqual(expand.normalize_collector("290 SM-P"), expand.normalize_collector("290/SM-P"))

    def test_collector_number_still_separates_different_cards(self) -> None:
        self.assertNotEqual(expand.normalize_collector("104/100"), expand.normalize_collector("1"))
        self.assertNotEqual(expand.normalize_collector("OP01-078"), expand.normalize_collector("EB03-026"))

    def test_name_normalisation_ignores_punctuation_and_case(self) -> None:
        self.assertEqual(expand.normalize_name("Monkey D. Luffy"), expand.normalize_name("monkey d luffy"))
        self.assertNotEqual(expand.normalize_name("Sabo"), expand.normalize_name("Sabo (Super)"))

    def test_set_normalisation_drops_suffix_after_colon(self) -> None:
        self.assertEqual(expand.normalize_set("SM-P: Sun & Moon Promos"), expand.normalize_set("SM-P"))

    def test_language_maps_g10_codes_to_catalog_codes(self) -> None:
        self.assertEqual(expand.normalize_language("jp"), "ja")
        self.assertEqual(expand.normalize_language("en"), "en")
        self.assertEqual(expand.normalize_language("zh-hans"), "zhCN")


class ResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.variants = [
            variant(1, "Pikachu Munch", "SM-P", "288/SM-P"),
            variant(2, "Pikachu V", "S4: Amazing Volt Tackle", "104/100"),
            variant(3, "Mew", "Crown Zenith Galarian Gallery", "GG10", "en"),
            variant(4, "Mew", "Pokemon Sword and Shield Crown Zenith", "GG10", "en"),
            variant(5, "Shanks", "One Piece Emperors", "OP09-004", "ja"),
        ]
        self.indexes = expand.build_variant_indexes(self.variants)

    def test_direct_identity_is_reused_without_writing(self) -> None:
        res = expand.resolve_asset(asset(), {("snkrdunk", "100090"): 1}, self.indexes)
        self.assertEqual(res.variant_id, 1)
        self.assertEqual(res.method, "direct")
        self.assertFalse(res.write_identity)
        self.assertFalse(res.write_alias)

    def test_gemrate_anchor_creates_identity_and_alias(self) -> None:
        gid = "a" * 40
        res = expand.resolve_asset(asset(gemrate_id=gid), {("gemrate", gid): 1}, self.indexes)
        self.assertEqual(res.variant_id, 1)
        self.assertEqual(res.method, "gemrate")
        self.assertEqual(res.match_status, "exact")
        self.assertTrue(res.write_identity)
        self.assertTrue(res.write_alias)

    def test_direct_and_agreeing_gemrate_writes_alias_only(self) -> None:
        gid = "b" * 40
        identities = {("snkrdunk", "100090"): 1, ("gemrate", gid): 1}
        res = expand.resolve_asset(asset(gemrate_id=gid), identities, self.indexes)
        self.assertEqual(res.method, "direct+gemrate")
        self.assertFalse(res.write_identity)
        self.assertTrue(res.write_alias)

    def test_conflicting_gemrate_keeps_direct_and_queues_review(self) -> None:
        gid = "c" * 40
        identities = {("snkrdunk", "100090"): 1, ("gemrate", gid): 2}
        res = expand.resolve_asset(asset(gemrate_id=gid), identities, self.indexes)
        # 唔准攞 gemrate 頂替 direct，亦唔准寫 alias。
        self.assertEqual(res.variant_id, 1)
        self.assertEqual(res.method, "direct+gemrate_conflict")
        self.assertFalse(res.write_identity)
        self.assertFalse(res.write_alias)
        self.assertEqual(res.reason_code, expand.REASON_VARIANT_CONFLICT)

    def test_name_plus_collector_unique_match_is_derived(self) -> None:
        res = expand.resolve_asset(asset(), {}, self.indexes)
        self.assertEqual(res.variant_id, 1)
        self.assertEqual(res.method, "name_collector")
        self.assertEqual(res.match_status, "derived")
        self.assertTrue(res.write_identity)
        self.assertFalse(res.write_alias)

    def test_ambiguous_name_collector_resolved_by_set(self) -> None:
        res = expand.resolve_asset(
            asset(card_name="Mew", set_name="Pokemon Sword and Shield Crown Zenith",
                  collector_number="GG10", language="en"),
            {}, self.indexes,
        )
        self.assertEqual(res.variant_id, 4)
        self.assertEqual(res.method, "name_collector_set")
        self.assertEqual(res.match_status, "derived")

    def test_ambiguous_without_tiebreak_goes_to_review(self) -> None:
        res = expand.resolve_asset(
            asset(card_name="Mew", set_name="Totally Unknown Set", collector_number="GG10", language="en"),
            {}, self.indexes,
        )
        self.assertIsNone(res.variant_id)
        self.assertEqual(res.reason_code, expand.REASON_AMBIGUOUS)
        self.assertEqual(res.evidence["candidateVariantIds"], [3, 4])

    def test_same_name_different_collector_is_never_matched(self) -> None:
        # 呢個係卡名 join 嘅陷阱：G10 `Pikachu V #1` 唔係我哋嘅 `Pikachu V 104/100`。
        res = expand.resolve_asset(
            asset(card_name="Pikachu V", set_name="Start Deck 100", collector_number="1"),
            {}, self.indexes,
        )
        self.assertIsNone(res.variant_id)
        self.assertEqual(res.reason_code, expand.REASON_COLLECTOR_MISMATCH)
        self.assertEqual(res.evidence["sameNameVariantIds"], [2])

    def test_unknown_name_goes_to_review(self) -> None:
        res = expand.resolve_asset(asset(card_name="Totally Unknown Card", collector_number="999"), {}, self.indexes)
        self.assertIsNone(res.variant_id)
        self.assertEqual(res.reason_code, expand.REASON_NAME_NOT_IN_CATALOG)

    def test_missing_asset_info_is_rejected_not_guessed(self) -> None:
        res = expand.resolve_asset(asset(card_name=None, set_name=None, collector_number=None), {}, self.indexes)
        self.assertIsNone(res.variant_id)
        self.assertEqual(res.reason_code, expand.REASON_NO_ASSET_INFO)


class SummaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexes = expand.build_variant_indexes([variant(1, "Pikachu Munch", "SM-P", "288/SM-P")])

    def test_counts_partition_every_observed_directory(self) -> None:
        gid = "d" * 40
        resolutions = [
            expand.resolve_asset(asset(directory="1"), {("snkrdunk", "1"): 1}, self.indexes),
            expand.resolve_asset(asset(directory="2", gemrate_id=gid), {("gemrate", gid): 1}, self.indexes),
            expand.resolve_asset(asset(directory="3", card_name="Nope", collector_number="9"), {}, self.indexes),
            expand.resolve_asset(asset(directory="4", card_name=None), {}, self.indexes),
        ]
        summary = expand.summarize(resolutions)
        self.assertEqual(summary["observed"], 4)
        self.assertEqual(summary["accepted"], 2)
        self.assertEqual(summary["quarantined"], 1)
        self.assertEqual(summary["rejected"], 1)
        self.assertEqual(
            summary["observed"],
            summary["accepted"] + summary["quarantined"] + summary["rejected"],
        )

    def test_conflict_counts_as_accepted_but_is_still_reviewed(self) -> None:
        gid = "e" * 40
        indexes = expand.build_variant_indexes(
            [variant(1, "Pikachu Munch", "SM-P", "288/SM-P"), variant(2, "Pikachu Munch", "Other", "288/SM-P")]
        )
        res = expand.resolve_asset(asset(gemrate_id=gid), {("snkrdunk", "100090"): 1, ("gemrate", gid): 2}, indexes)
        summary = expand.summarize([res])
        self.assertEqual(summary["accepted"], 1)
        self.assertEqual(summary["quarantined"], 0)
        self.assertEqual(summary["reasons"][expand.REASON_VARIANT_CONFLICT], 1)


class RunKeyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.indexes = expand.build_variant_indexes([variant(1, "Pikachu Munch", "SM-P", "288/SM-P")])

    def test_run_key_is_stable_for_identical_input(self) -> None:
        self.assertEqual(expand.compute_run_key([asset()]), expand.compute_run_key([asset()]))

    def test_run_key_ignores_outcome_so_replays_reuse_the_same_run(self) -> None:
        # 第一次 --write 之後判定會由 name_collector 變成 direct；run_key 唔可以跟住變，
        # 否則第二次跑就會多開一條 market_ingest_run。
        before = expand.resolve_asset(asset(), {}, self.indexes)
        after = expand.resolve_asset(asset(), {("snkrdunk", "100090"): 1}, self.indexes)
        self.assertNotEqual(before.method, after.method)
        self.assertEqual(expand.compute_run_key([before.asset]), expand.compute_run_key([after.asset]))

    def test_run_key_changes_when_g10_input_changes(self) -> None:
        self.assertNotEqual(
            expand.compute_run_key([asset()]),
            expand.compute_run_key([asset(collector_number="999")]),
        )

    def test_evidence_sha256_is_deterministic(self) -> None:
        a = expand.resolve_asset(asset(), {}, self.indexes)
        b = expand.resolve_asset(asset(), {}, self.indexes)
        self.assertEqual(a.evidence_sha256, b.evidence_sha256)
        self.assertEqual(len(a.evidence_sha256), 64)


class ScanTests(unittest.TestCase):
    def test_scan_reads_gemrate_id_and_asset_info(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            card = root / "snkrdunk" / "100081"
            card.mkdir(parents=True)
            gid = "d79fbc875fac2d5ca71159a226b809c13f540c08"
            (card / "populations.json").write_text(
                json.dumps({
                    "population": [{"gradeName": "PSA", "total": 5639, "topGrade": 5166}],
                    "source": f"https://www.gemrate.com/universal-search?gemrate_id={gid}",
                }),
                encoding="utf-8",
            )
            (card / "asset_info.json").write_text(
                json.dumps({"cardName": "Pikachu V", "setName": "Start Deck 100", "cardId": "1", "language": "jp"}),
                encoding="utf-8",
            )
            uuid_card = root / "altxyz" / "2e5f7c2f-f3a4-46f2-9fdc-7e33e0e92729"
            uuid_card.mkdir(parents=True)
            (uuid_card / "populations.json").write_text(json.dumps({"source": "https://x/?no_id=1"}), encoding="utf-8")

            assets = expand.scan_g10_root(root)

        self.assertEqual(len(assets), 2)
        by_dir = {a.directory: a for a in assets}
        snk = by_dir["100081"]
        self.assertEqual(snk.gemrate_id, gid)
        self.assertEqual(snk.identity_source, "snkrdunk")
        self.assertEqual(snk.card_name, "Pikachu V")
        self.assertTrue(snk.has_asset_info)
        alt = by_dir["2e5f7c2f-f3a4-46f2-9fdc-7e33e0e92729"]
        self.assertIsNone(alt.gemrate_id)
        self.assertEqual(alt.identity_source, "ebay")
        self.assertFalse(alt.has_asset_info)

    def test_alias_type_never_collides_with_gemrate_receipt_types(self) -> None:
        reserved = {"entity", "universal", "grader_member", "spec"}
        self.assertFalse(set(expand.PROVIDER_ALIAS_TYPE.values()) & reserved)


if __name__ == "__main__":
    unittest.main()
