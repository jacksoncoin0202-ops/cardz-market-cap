from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "canonical_public_snapshot",
    ROOT / "pipelines" / "canonical_public_snapshot.py",
)
assert SPEC and SPEC.loader
snapshot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(snapshot)

SHA_PACK = "a" * 64
SHA_NEW = "b" * 64


def catalog_row(
    opaque_id: str,
    *,
    name: str = "Charizard ex",
    set_name: str = "2024 Scarlet and Violet Obsidian Flames",
    collector: str = "223/197",
    tcg: str = "pokemon",
    language: str = "en",
    identity_status: str = "confirmed",
    printing_status: str = "canonical",
    edition: str = "unlimited",
    parallel: str = "standard",
    finish: str = "holofoil",
) -> dict[str, object]:
    row: dict[str, object] = {
        "opaque_id": opaque_id,
        "canonical_name": name,
        "set_name": set_name,
        "collector_number": collector,
        "tcg_code": tcg,
        "card_language": language,
        "identity_status": identity_status,
        "printing_tcg_code": tcg,
        "printing_card_language": language,
        "printing_set_name": set_name,
        "printing_collector_number": collector,
        "edition_code": edition,
        "parallel_code": parallel,
        "finish_code": finish,
        "printing_identity_status": printing_status,
        "printing_evidence_sha256": "e" * 64,
    }
    row["canonical_printing_sha256"] = snapshot.printing_key_sha256(
        snapshot.printing_key(
            tcg,
            set_name,
            collector,
            edition,
            parallel,
            finish,
            language=language,
        )
    )
    return row


def pack_card(
    card_id: str,
    *,
    collector: str = "085/SVP",
    set_name: str = "2024 Scarlet and Violet Obsidian Flames",
    language: str = "en",
    sha: str = SHA_PACK,
) -> dict[str, object]:
    key = snapshot.printing_key(
        "pokemon",
        set_name,
        collector,
        "unlimited",
        "standard",
        "holofoil",
        language=language,
    )
    return {
        "id": card_id,
        "tcg": "pokemon",
        "language": language,
        "cardLanguage": language,
        "collectorNumber": {"complete": True, "display": collector, "normalized": collector.casefold()},
        "sets": {"en": set_name, "zhTW": None, "zhCN": None, "ja": None},
        "printingIdentity": {
            "setName": set_name,
            "collectorNumber": collector,
            "editionCode": "unlimited",
            "parallelCode": "standard",
            "finishCode": "holofoil",
            "cardLanguage": language,
            "canonicalPrintingSha256": snapshot.printing_key_sha256(key),
            "evidenceSha256": "e" * 64,
        },
        "image": {"sha256": sha, "src": f"/market-assets/{sha}.webp", "kind": "raw_front"},
    }


class PresentationResolutionTests(unittest.TestCase):
    def images(self, *, by_public_id: dict[str, dict[str, object]] | None = None, allowed: set[str] | None = None):
        return snapshot.PublicImages(by_public_id or {}, allowed if allowed is not None else {SHA_PACK})

    def qc_image(self, sha: str = SHA_NEW) -> dict[str, object]:
        return {
            "height": 600,
            "kind": "raw_front",
            "qcAt": "2026-07-25T00:00:00Z",
            "sha256": sha,
            "src": f"/market-assets/{sha}.webp",
            "variants": {"200": f"/market-assets/{sha}_200.webp"},
            "width": 429,
        }

    def test_db_empty_does_not_fall_back_to_manifest_public_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            assets = root / "market-assets"
            assets.mkdir()
            manifest = root / "image-qc.json"
            manifest.write_text(
                json.dumps(
                    {"records": [{"publicId": "cmc_test", "contentSha256": SHA_NEW,
                                  "imageKind": "raw_front", "publicAllowed": True,
                                  "width": 429, "height": 600}]}
                ),
                encoding="utf-8",
            )
            with patch.object(snapshot, "fetchall", return_value=[]):
                images = snapshot.load_public_images(manifest, assets, connection=object())
            self.assertEqual(images.by_public_id, {})

    def test_db_public_image_query_requires_current_human_review_version(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "market-assets"
            assets.mkdir()
            with patch.object(snapshot, "fetchall", return_value=[]) as query:
                snapshot.load_public_images(
                    Path(temporary) / "image-qc.json",
                    assets,
                    connection=object(),
                )
            self.assertIn(
                "q.qc_version = 'human-review-v2'",
                query.call_args.args[1],
            )

    def test_new_card_with_identity_and_qc_image_enters_the_snapshot(self) -> None:
        row = catalog_row("cmc_000000000000000000000001")
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(
            [row],
            {},
            self.images(by_public_id={"cmc_000000000000000000000001": self.qc_image()}),
            {},
        )
        self.assertEqual(skipped, [])
        self.assertEqual(tiers["new_from_catalog"], 1)
        entry = resolved["cmc_000000000000000000000001"]
        self.assertEqual(entry["names"], {"en": "Charizard ex", "zhTW": None, "zhCN": None, "ja": None})
        self.assertEqual(entry["sets"]["en"], "2024 Scarlet and Violet Obsidian Flames")
        self.assertEqual(entry["collectorNumber"], {"complete": True, "display": "223/197", "normalized": "223/197"})
        self.assertEqual(entry["image"]["sha256"], SHA_NEW)
        self.assertEqual(entry["image"]["alt"]["en"], "Charizard ex")
        self.assertEqual(entry["identityStatus"], "confirmed")
        self.assertEqual(entry["printingIdentity"]["parallelCode"], "standard")
        self.assertEqual(entry["stories"], {"en": None, "zhTW": None, "zhCN": None, "ja": None})

    def test_new_card_without_public_image_is_skipped_and_the_export_survives(self) -> None:
        keeper = pack_card("cmc_00000000000000000000000a")
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(
            [catalog_row("cmc_00000000000000000000000a", collector="085/SVP"), catalog_row("cmc_000000000000000000000002")],
            {"cmc_00000000000000000000000a": keeper},
            self.images(),
            {},
        )
        self.assertEqual(skipped, [("cmc_000000000000000000000002", "image_unavailable")])
        self.assertEqual(tiers["pack_id"], 1)
        self.assertIn("cmc_00000000000000000000000a", resolved)
        self.assertNotIn("cmc_000000000000000000000002", resolved)

    def test_pack_card_whose_artwork_is_no_longer_public_allowed_is_skipped(self) -> None:
        card = pack_card("cmc_00000000000000000000000a")
        resolved, skipped, _ = snapshot.resolve_presentation_entries(
            [catalog_row("cmc_00000000000000000000000a", collector="085/SVP")],
            {"cmc_00000000000000000000000a": card},
            self.images(allowed=set()),
            {},
        )
        self.assertEqual(resolved, {})
        self.assertEqual(skipped, [("cmc_00000000000000000000000a", "image_unavailable")])

    def test_new_card_without_complete_identity_is_skipped_and_the_export_survives(self) -> None:
        rows = [
            catalog_row("cmc_000000000000000000000011", name="   "),
            catalog_row("cmc_000000000000000000000012", set_name=""),
            catalog_row("cmc_000000000000000000000013", collector="untitled"),
            catalog_row("cmc_000000000000000000000014", identity_status="provisional"),
            catalog_row("cmc_000000000000000000000015"),
        ]
        images = self.images(by_public_id={str(row["opaque_id"]): self.qc_image() for row in rows})
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(rows, {}, images, {})
        self.assertEqual(
            skipped,
            [
                ("cmc_000000000000000000000011", "identity_incomplete"),
                ("cmc_000000000000000000000012", "identity_incomplete"),
                ("cmc_000000000000000000000013", "identity_incomplete"),
                ("cmc_000000000000000000000014", "identity_incomplete"),
            ],
        )
        self.assertEqual(tiers["new_from_catalog"], 1)
        self.assertEqual(list(resolved), ["cmc_000000000000000000000015"])

    def test_renamed_card_relinks_to_its_pack_entry_by_printing_key(self) -> None:
        card = pack_card("cmc_0000000000000000000000ff", collector="085/SVP")
        row = catalog_row("cmc_000000000000000000000021", collector="085/SVP")
        key = snapshot.row_printing_key(row)
        self.assertIsNotNone(key)
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(
            [row],
            {"cmc_0000000000000000000000ff": card},
            self.images(),
            {key: 1},  # type: ignore[dict-item]
        )
        self.assertEqual(skipped, [])
        self.assertEqual(tiers["relinked_printing_key"], 1)
        self.assertEqual(
            resolved["cmc_000000000000000000000021"]["id"],
            "cmc_000000000000000000000021",
        )

    def test_one_pack_entry_is_never_reused_by_two_ranked_cards(self) -> None:
        card = pack_card("cmc_0000000000000000000000ff", collector="085/SVP")
        key = snapshot.row_printing_key(
            catalog_row("cmc_000000000000000000000021", collector="085/SVP")
        )
        self.assertIsNotNone(key)
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(
            [
                catalog_row("cmc_000000000000000000000021", collector="085/SVP"),
                catalog_row("cmc_000000000000000000000022", collector="085/SVP"),
            ],
            {"cmc_0000000000000000000000ff": card},
            self.images(),
            {key: 1},  # type: ignore[dict-item]
        )
        self.assertEqual(tiers["relinked_printing_key"], 1)
        self.assertEqual(list(resolved), ["cmc_000000000000000000000021"])
        self.assertEqual(skipped, [("cmc_000000000000000000000022", "image_unavailable")])

    def test_ambiguous_printing_key_never_borrows_another_cards_artwork(self) -> None:
        pack = {
            "cmc_0000000000000000000000f1": pack_card("cmc_0000000000000000000000f1", collector="OP05-119"),
            "cmc_0000000000000000000000f2": pack_card("cmc_0000000000000000000000f2", collector="OP05-119"),
        }
        row = catalog_row("cmc_000000000000000000000031", collector="OP05-119")
        key = snapshot.row_printing_key(row)
        self.assertIsNotNone(key)
        resolved, skipped, _ = snapshot.resolve_presentation_entries(
            [row],
            pack,
            self.images(),
            {key: 2},  # type: ignore[dict-item]
        )
        self.assertEqual(resolved, {})
        self.assertEqual(skipped, [("cmc_000000000000000000000031", "ambiguous_printing_key")])

    def test_subset_collector_number_is_published_with_its_printed_denominator(self) -> None:
        row = catalog_row(
            "cmc_000000000000000000000041",
            name="Giratina Vstar",
            set_name="2023 Sword and Shield Crown Zenith Galarian Gallery Secret Rare",
            collector="GG69",
        )
        resolved, _, _ = snapshot.resolve_presentation_entries(
            [row],
            {},
            self.images(by_public_id={"cmc_000000000000000000000041": self.qc_image()}),
            {},
        )
        self.assertEqual(
            resolved["cmc_000000000000000000000041"]["collectorNumber"],
            {"complete": True, "display": "GG69/GG70", "normalized": "gg69"},
        )

    def test_subset_collector_without_attested_denominator_is_incomplete(self) -> None:
        collector = snapshot.normalize_collector(
            "GG69",
            "en",
            "Unknown Pokemon Set",
        )
        self.assertEqual(collector.display, "GG69")
        self.assertEqual(collector.normalized, "gg69")
        self.assertFalse(collector.complete)

    def test_reused_pack_entry_regains_the_denominator_it_was_frozen_without(self) -> None:
        card = pack_card("cmc_000000000000000000000042", collector="TG20")
        row = catalog_row(
            "cmc_000000000000000000000042",
            name="Rayquaza Vmax",
            set_name="2022 Sword and Shield Silver Tempest Trainer Gallery",
            collector="TG20",
        )
        resolved, _, tiers = snapshot.resolve_presentation_entries([row], {card["id"]: card}, self.images(), {})
        self.assertEqual(tiers["pack_id"], 1)
        self.assertEqual(
            resolved["cmc_000000000000000000000042"]["collectorNumber"],
            {"complete": True, "display": "TG20/TG30", "normalized": "tg20"},
        )

    def test_missing_printing_qualifier_is_rejected_without_guessing(self) -> None:
        row = catalog_row(
            "cmc_000000000000000000000043",
            edition="",
            parallel="",
            finish="",
        )
        resolved, skipped, _ = snapshot.resolve_presentation_entries(
            [row],
            {},
            self.images(by_public_id={str(row["opaque_id"]): self.qc_image()}),
            {},
        )
        self.assertEqual(resolved, {})
        self.assertEqual(
            skipped,
            [(row["opaque_id"], "printing_identity_incomplete")],
        )

    def test_printing_identity_requires_canonical_status_base_parity_and_hash(self) -> None:
        candidate = catalog_row(
            "cmc_000000000000000000000045",
            printing_status="candidate",
        )
        self.assertIsNone(snapshot.public_printing_identity(candidate))

        mismatched = catalog_row("cmc_000000000000000000000046")
        mismatched["set_name"] = "Conflicting variant set"
        self.assertIsNone(snapshot.public_printing_identity(mismatched))

        bad_hash = catalog_row("cmc_000000000000000000000047")
        bad_hash["canonical_printing_sha256"] = "0" * 64
        self.assertIsNone(snapshot.public_printing_identity(bad_hash))

    def test_same_collector_in_another_set_never_relinks(self) -> None:
        card = pack_card("cmc_0000000000000000000000ff", collector="085/SVP")
        row = catalog_row(
            "cmc_000000000000000000000044",
            collector="085/SVP",
            set_name="A different canonical set",
        )
        key = snapshot.row_printing_key(row)
        self.assertIsNotNone(key)
        resolved, skipped, tiers = snapshot.resolve_presentation_entries(
            [row],
            {str(card["id"]): card},
            self.images(),
            {key: 1},  # type: ignore[dict-item]
        )
        self.assertEqual(resolved, {})
        self.assertEqual(tiers["relinked_printing_key"], 0)
        self.assertEqual(skipped, [(row["opaque_id"], "image_unavailable")])

    def test_subset_normalization_keeps_identity_stable_and_never_invents_a_denominator(self) -> None:
        bare = snapshot.normalize_collector("SV49", "en", "2019 Sun and Moon Hidden Fates")
        qualified = snapshot.normalize_collector("SV49/SV94", "en", "2019 Sun and Moon Hidden Fates")
        self.assertEqual(bare.display, "SV49/SV94")
        self.assertEqual(bare.normalized, qualified.normalized)
        self.assertEqual(qualified.display, "SV49/SV94")

        unknown = snapshot.normalize_collector("RC12", "en", "Pokemon XY Generations")
        self.assertEqual(unknown.display, "RC12")

        for promo in ("SWSH123", "SM210", "XY42"):
            self.assertEqual(snapshot.normalize_collector(promo, "en", "Pokemon Sword and Shield Promo").display, promo)

    def test_public_images_only_index_qc_approved_artwork_present_on_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "market-assets"
            assets.mkdir()
            master = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
            master.paste((255, 255, 255, 255), (1, 1, 428, 599))
            master.save(assets / f"{SHA_PACK}.webp", "WEBP", lossless=True)
            Image.new("RGB", (200, 280), "white").save(
                assets / f"{SHA_PACK}_200.webp",
                "WEBP",
            )
            master.save(
                assets / f"{SHA_PACK}_600.webp",
                "WEBP",
                lossless=True,
            )
            master.save(assets / f"{SHA_NEW}.webp", "WEBP", lossless=True)
            manifest = Path(temporary) / "image-qc.json"
            manifest.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "publicId": "cmc_000000000000000000000001",
                                "contentSha256": SHA_PACK,
                                "imageKind": "raw_front",
                                "publicAllowed": True,
                                "qcAt": "2026-07-25T00:00:00Z",
                                "width": 429,
                                "height": 600,
                            },
                            {
                                "publicId": "cmc_000000000000000000000002",
                                "contentSha256": SHA_NEW,
                                "imageKind": "raw_front",
                                "publicAllowed": False,
                                "width": 429,
                                "height": 600,
                            },
                            {
                                "publicId": "cmc_000000000000000000000003",
                                "contentSha256": "c" * 64,
                                "imageKind": "raw_front",
                                "publicAllowed": True,
                                "width": 429,
                                "height": 600,
                            },
                        ]
                    }
                ),
                encoding="utf-8",
            )
            images = snapshot.load_public_images(manifest, assets)
        self.assertEqual(list(images.by_public_id), ["cmc_000000000000000000000001"])
        self.assertEqual(images.allowed_sha, {SHA_PACK})
        entry = images.by_public_id["cmc_000000000000000000000001"]
        self.assertEqual(entry["src"], f"/market-assets/{SHA_PACK}.webp")
        self.assertEqual(
            entry["variants"],
            {
                "200": f"/market-assets/{SHA_PACK}_200.webp",
                "600": f"/market-assets/{SHA_PACK}_600.webp",
            },
        )

    def test_public_image_without_both_responsive_derivatives_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            assets = Path(temporary) / "market-assets"
            assets.mkdir()
            master = Image.new("RGBA", (429, 600), (0, 0, 0, 0))
            master.paste((255, 255, 255, 255), (1, 1, 428, 599))
            master.save(assets / f"{SHA_PACK}.webp", "WEBP", lossless=True)
            Image.new("RGB", (200, 280), "white").save(
                assets / f"{SHA_PACK}_200.webp",
                "WEBP",
            )
            manifest = Path(temporary) / "image-qc.json"
            manifest.write_text(
                json.dumps(
                    {
                        "records": [
                            {
                                "publicId": "cmc_000000000000000000000001",
                                "contentSha256": SHA_PACK,
                                "imageKind": "raw_front",
                                "publicAllowed": True,
                                "width": 429,
                                "height": 600,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            images = snapshot.load_public_images(manifest, assets)
        self.assertEqual(images, snapshot.PublicImages({}, set()))

    def test_missing_qc_manifest_does_not_abort_the_export(self) -> None:
        images = snapshot.load_public_images(ROOT / "manifests" / "does-not-exist.json", ROOT)
        self.assertEqual(images, snapshot.PublicImages({}, set()))

    def test_summary_line_reports_admissions_and_skip_reasons(self) -> None:
        line = snapshot.resolution_summary(
            5,
            {"pack_id": 2, "relinked_printing_key": 1, "new_from_catalog": 1},
            [("cmc_000000000000000000000031", "image_unavailable")],
        )
        self.assertEqual(
            line,
            "canonical export: 5 ranked, 4 published (pack_id=2, relinked_printing_key=1, "
            "new_from_catalog=1), 1 skipped (image_unavailable=1)",
        )


if __name__ == "__main__":
    unittest.main()
