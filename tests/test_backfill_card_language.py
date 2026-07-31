from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from backfill_card_language import build_identity_update_plan, resolve_language  # noqa: E402
from card_identity import opaque_id_from_row, printing_identity_sha256_from_row  # noqa: E402


def write_gemrate_receipt(root: Path, gemrate_id: str, *, url: str, set_name: str) -> None:
    path = root / "daily_20260731T000000Z" / "cards" / gemrate_id / "card_details.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps({"publicCardPage": {"routeVerified": True, "canonicalUrl": url, "identity": {"set_name": set_name}}}),
        encoding="utf-8",
    )


class BackfillCardLanguageTests(unittest.TestCase):
    @staticmethod
    def identity_row(
        variant_id: int,
        *,
        language: str,
        name: str = "Charmander",
        collector: str = "168/165",
        opaque: str | None = None,
        printing_hash: str | None = None,
    ) -> dict[str, object]:
        row: dict[str, object] = {
            "id": variant_id,
            "opaque_id": opaque or f"cmc_old_{variant_id}",
            "tcg_code": "pokemon",
            "canonical_name": name,
            "set_name": "Pokemon Card 151",
            "collector_number": collector,
            "card_language": language,
            "printing_variant_id": variant_id,
            "printing_card_language": language,
            "edition_code": "",
            "parallel_code": "",
            "finish_code": "",
            "canonical_printing_sha256": printing_hash or ("a" * 63 + str(variant_id)),
        }
        return row

    def test_identity_plan_recomputes_opaque_and_printing_hash(self) -> None:
        row = self.identity_row(7, language="en")
        plan = build_identity_update_plan([row], {7: "ja"})
        self.assertEqual(len(plan), 1)
        target = dict(row, card_language="ja")
        self.assertEqual(plan[0]["opaque_id"], opaque_id_from_row(target))
        self.assertEqual(plan[0]["printing_sha256"], printing_identity_sha256_from_row(target))
        self.assertNotEqual(plan[0]["opaque_id"], row["opaque_id"])
        self.assertNotEqual(plan[0]["printing_sha256"], row["canonical_printing_sha256"])

    def test_identity_plan_fails_closed_if_new_printing_hash_hits_unchanged_row(self) -> None:
        updating = self.identity_row(7, language="en", name="Charmander")
        target = dict(updating, card_language="ja")
        unchanged = self.identity_row(
            8,
            language="ja",
            name="Charmeleon",  # opaque is distinct; printing key deliberately collides
            printing_hash=printing_identity_sha256_from_row(target),
        )
        with self.assertRaisesRegex(ValueError, "canonical_printing_sha256 collision"):
            build_identity_update_plan([updating, unchanged], {7: "ja"})

    def test_gemrate_japanese_beats_english_image_pointer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gemrate_receipt(
                root, "gem-1",
                url="https://www.gemrate.com/card/gem-1/2023-pokemon-japanese-sv2a-pokemon-card-151-201",
                set_name="Pokemon Japanese SV2a Pokemon Card 151",
            )
            lang, evidence, status = resolve_language(
                set_name="", sources=[{"source_code": "gemrate", "external_entity_id": "gem-1"}],
                g10_root=None, pointer_paths=["pkmn-tcg-en/card.webp"], gemrate_runs_root=root,
            )
        self.assertEqual((lang, status), ("ja", "ok"))
        self.assertIn("ja@100:gemrate:gem-1:receipt:daily_20260731T000000Z", evidence)
        self.assertIn("diagnostic:ptr:en:pkmn-tcg-en/card.webp", evidence)
        self.assertFalse(any(item.startswith("en@") for item in evidence))

    def test_pointer_only_is_no_evidence_and_never_a_write_vote(self) -> None:
        lang, evidence, status = resolve_language(
            set_name="", sources=[], g10_root=None,
            pointer_paths=["pkmn-tcg-en/card.webp"], gemrate_runs_root=Path("/missing"),
        )
        self.assertEqual((lang, status), (None, "no_evidence"))
        self.assertEqual(evidence, ["diagnostic:ptr:en:pkmn-tcg-en/card.webp"])

    def test_conflicting_strong_gemrate_and_g10_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gemrate_receipt(
                root, "gem-1",
                url="https://www.gemrate.com/card/gem-1/2023-pokemon-japanese-sv2a-pokemon-card-151-201",
                set_name="Pokemon Japanese SV2a Pokemon Card 151",
            )
            g10 = root / "g10"
            asset = g10 / "snkrdunk" / "123" / "asset_info.json"
            asset.parent.mkdir(parents=True)
            asset.write_text(json.dumps({"language": "en"}), encoding="utf-8")
            lang, _evidence, status = resolve_language(
                set_name="", sources=[
                    {"source_code": "gemrate", "external_entity_id": "gem-1"},
                    {"source_code": "snkrdunk", "external_entity_id": "123"},
                ], g10_root=g10, pointer_paths=[], gemrate_runs_root=root,
            )
        self.assertEqual((lang, status), (None, "conflict"))

    def test_two_strong_votes_do_not_outvote_a_conflicting_strong_provider(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_gemrate_receipt(
                root, "gem-1",
                url="https://www.gemrate.com/card/gem-1/2023-pokemon-japanese-sv2a-pokemon-card-151-201",
                set_name="Pokemon Japanese SV2a Pokemon Card 151",
            )
            g10 = root / "g10"
            asset = g10 / "snkrdunk" / "123" / "asset_info.json"
            asset.parent.mkdir(parents=True)
            asset.write_text(json.dumps({"language": "ja"}), encoding="utf-8")
            lang, _evidence, status = resolve_language(
                set_name="",
                sources=[
                    {"source_code": "gemrate", "external_entity_id": "gem-1"},
                    {"source_code": "snkrdunk", "external_entity_id": "123"},
                    {"source_code": "tcgpricelookup", "external_entity_id": "pokemon-en-conflict"},
                ],
                g10_root=g10,
                pointer_paths=[],
                gemrate_runs_root=root,
            )
        self.assertEqual((lang, status), (None, "conflict"))


if __name__ == "__main__":
    unittest.main()
