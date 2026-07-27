#!/usr/bin/env python3
"""Writer for the 28 long-form English card stories (agent: opus-story, 2026-07-27).

Output contract matches the G10 research standard read by
`pipelines/g10_research_ingest.py` `parse_summary_document` - the required key is
`summary` (non-empty str); extra metadata keys are tolerated by that parser.

Files land in the layout `iter_card_dirs` expects, so the existing ingest picks
them up with `--g10-root data/editorial/long-form-stories` and no code change:

    long-form-stories/altxyz/<ebay external_entity_id>/summary_en.json

`altxyz` is the ingest's provider key that maps to `catalog_source_identity`
source_code `ebay` (see `PROVIDER_IDENTITY_SOURCE`); it is a loader requirement,
not a claim about where the prose came from.

Both dicts below are verbatim DB reads taken 2026-07-27 -- META from
`catalog_variant`, EBAY_ID from `catalog_source_identity` where source_code='ebay'.
Re-run the two SELECTs in ../FINDING.md before trusting them again.

Usage: python -X utf8 story_batch1.py   (each batch imports write_stories)
"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[4]
OUT = ROOT / "data" / "editorial" / "long-form-stories" / "altxyz"

REQUIRED_SECTIONS = ("### Basic Info", "### Community Pulse", "### Card Fun Facts", "### Summary (TLDR)")
G10_FLOOR = 1938

# (tcg, card_language, canonical_name, set_name, collector_number, opaque_id)
META: dict[int, tuple[str, str, str, str, str, str]] = {
    19: ("one-piece", "en", "Monkey D Luffy", "2023 Awakening of the New Era Special Art", "ST01-012", "cmc_334667e52d3f7f5aff7fd1a5"),
    24: ("one-piece", "en", "Monkey D. Luffy", "2023 Awakening of the New Era Manga Alternate Art", "OP05-119", "cmc_058d84cf05991c2c0ab2409b"),
    34: ("pokemon", "en", "Mew/Mewtwo Gx", "2019 Sun and Moon Black Star Promo Power Partnership Tins", "SM191", "cmc_209c6e27c8c27d348cf8aaf0"),
    39: ("pokemon", "en", "Giratina Vstar", "2023 Sword and Shield Crown Zenith Galarian Gallery Secret Rare", "GG69", "cmc_d1a8a79fe5185c3cceafaa15"),
    41: ("pokemon", "en", "Mewtwo Vstar", "2023 Sword and Shield Crown Zenith Galarian Gallery", "GG44", "cmc_b26568a2eee0439cdd89ae53"),
    43: ("one-piece", "en", "Monkey D. Luffy", "2025 A Fist of Divine Speed 3rd Anniversary Gold Foil", "OP05-119", "cmc_2c605964de9f67642f9d6197"),
    48: ("pokemon", "en", "Charizard Gx", "2019 Sun and Moon Hidden Fates", "SV49", "cmc_19727069ac175f158769c2f6"),
    60: ("one-piece", "en", "Roronoa Zoro", "2024 Wings of the Captain Manga Alternate Art", "OP06-118", "cmc_0df175352be781a08a19275c"),
    63: ("one-piece", "en", "Monkey D. Luffy", "2025 Eb02-Extra Booster -Anime 25th Collection- Manga Alternate Art", "EB02-061", "cmc_fc1d979ca8dcf60b9baed043"),
    69: ("one-piece", "en", "Monkey D. Luffy", "2025 Piece Card Game Carrying On His Will", "OP13-118", "cmc_4b5d3876c5f0c2a6d7fcfb1b"),
    72: ("one-piece", "en", "Monkey D. Luffy", "2025 Piece Card Game A Fist of Divine Speed", "OP05-119", "cmc_dfe920ea3ab9aa3c55471fd7"),
    74: ("pokemon", "en", "Charizard VMAX SUR", "Sword Shield Shining Fates", "SV107", "cmc_79a84c35a02d207feebd5c4e"),
    75: ("pokemon", "en", "Pikachu/Zekrom Gx", "2019 Sun and Moon Black Star Promo Tag Team Tins", "SM168", "cmc_77c97c76a50a49a4d668858c"),
    78: ("one-piece", "en", "Nami", "2024 Premium Booster the Best Manga Alternate Art", "OP01-016", "cmc_16890293ef095bbeecf8792a"),
    101: ("pokemon", "en", "Arceus Vstar", "2023 Sword and Shield Crown Zenith Galarian Gallery Secret Rare", "GG70", "cmc_c77b02511149164339ca40c9"),
    103: ("one-piece", "en", "Monkey D. Luffy", "2022 Romance Dawn Alternate Art", "OP01-003", "cmc_7f757e4ecc3aa9b69b5dd012"),
    104: ("pokemon", "en", "Rayquaza Vmax", "2022 Sword and Shield Silver Tempest Trainer Gallery", "TG20", "cmc_5964def2b98493de2fbea6d7"),
    107: ("one-piece", "en", "Boa Hancock", "2024 500 Years In the Future Manga Art", "OP07-051", "cmc_efdcf43dc1f206f6636fa2d0"),
    128: ("one-piece", "en", "Monkey D. Luffy", "2025 A Fist of Divine Speed Manga Alternate Art", "OP11-118", "cmc_a97002894deef0055a443602"),
    132: ("one-piece", "en", "Boa Hancock", "2023 Kingdoms of Intrigue", "OP01-078", "cmc_dacb08e98dd4781013b9b9de"),
    134: ("one-piece", "en", "Monkey D. Luffy", "2025 Carrying On His Will (Op13) - English Manga Alt. Art Parallel", "OP13-118", "cmc_79579a6d522f2e49f0e54949"),
    146: ("one-piece", "en", "Monkey D Luffy", "2024 Premium Booster: the Best English Manga Alt. Art Parallel", "OP05-119", "cmc_d911b2f32f701c32ada0f8af"),
    163: ("one-piece", "en", "Monkey D. Luffy", "2025 3rd Anniversary! One Piece Card Treasure Campaign Pack", "ST21-014", "cmc_a0accd63b7240ff7357ec7ba"),
    171: ("one-piece", "en", "Monkey D Luffy", "2024 Emperors In the New World (Op09) - English (Borderless, Manga Art) Alt Art", "OP09-119", "cmc_b9e3dbde32ffa1418c85447c"),
    175: ("one-piece", "en", "Tony Tony Chopper", "2024 Memorial Collection Manga Alternate Art", "EB01-006", "cmc_d28f13261d43426bf2db2076"),
    188: ("one-piece", "en", "Portgas D. Ace", "2023 Japanese Manga Art", "OP02-013", "cmc_0dca1574369a3b550922f228"),
    192: ("one-piece", "en", "Sanji", "2025 Premium Booster: the Best Vol.2 (Prb-02) - English Manga Alt. Art Parallel (En Above Japan On Border)", "OP06-119", "cmc_48f49134505612b25e5792fa"),
    197: ("one-piece", "en", "Nami", "2025 Card Game Promo Championship Offline Regionals Top  Prize", "OP09-050", "cmc_6b7c379edd7d4a83728b6fc3"),
}

EBAY_ID: dict[int, str] = {
    19: "b69a3067-c2cc-414f-902e-76db8b5fd3f4",
    24: "231edf8e-4170-439b-8134-ce15ee82f0d9",
    34: "712df551-2987-42dd-ab50-906887cf53d4",
    39: "105efdc0-db8f-42cd-8b8e-4c558e10ac4b",
    41: "38ca51e4-8da1-4cd1-94e8-c086f18df5dc",
    43: "d26d574b-0e45-4e7a-aa57-66bd27ef61a1",
    48: "57be9054-6cee-407e-b3e8-ebae69d90171",
    60: "d68bf285-3bb0-4268-92e9-c643d266984c",
    63: "1e8aadca-d25a-49c5-99ba-2c1ce7b85892",
    69: "f31ebe4b-7675-46f1-86c3-245e4bc10005",
    72: "d2210393-3c59-4cb3-a438-ed7a70370d3d",
    74: "eb8b7c12-3252-4688-9c51-e070ce60950f",
    75: "e1e4b0a7-dba2-4b49-8e29-24695e0c3a70",
    78: "dde83737-3fdb-4631-8b57-7836fb264422",
    101: "40bfa984-9405-4eda-b61a-6b774218799f",
    103: "d1302f6a-183f-43e8-84c1-1f6867e12d10",
    104: "8ba5e47a-9f2e-4c14-a2b4-bd5bb65f1e74",
    107: "d00d2083-0262-4d4f-8550-348e76573afc",
    128: "d2c7ca43-7bc6-420b-8321-d817970ba02e",
    132: "8f872687-5dac-4339-bd85-cc26c7107e9d",
    134: "f3741d7c-c2f3-4775-a8f2-f08b2f9cab66",
    146: "7c57a2c7-6647-4603-9651-d522e997db3f",
    163: "1bd945cb-dc7c-42be-a90e-a62fb2dbfd5d",
    171: "484c087a-94ae-4e5a-90ac-06f75236c848",
    175: "ea7108b2-3e49-4253-a736-5f6923bd15c1",
    188: "d2b07a31-e001-4819-a6f8-d405548ac6bf",
    192: "e10dfc65-66d6-4c21-80e8-355455c075bb",
    197: "14b5af37-916e-491e-afbe-d3098d745868",
}


def write_stories(stories: dict[int, str]) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    for vid, raw in stories.items():
        tcg, lang, name, set_name, number, opaque = META[vid]
        text = raw.strip()
        for section in REQUIRED_SECTIONS:
            if section not in text:
                raise SystemExit(f"variant {vid}: missing required section {section!r}")
        if len(text) < G10_FLOOR:
            raise SystemExit(f"variant {vid}: {len(text)} chars is under the {G10_FLOOR} char G10 floor")
        doc = {
            "summary": text,
            "variantId": vid,
            "opaqueId": opaque,
            "locale": "en",
            "tcg": tcg,
            "cardLanguage": lang,
            "canonicalName": name,
            "setNameCatalog": set_name,
            "collectorNumber": number,
            "authoredBy": "opus-story",
            "authoredAt": now,
            "sourceStandard": "g10-summary_en-v1",
            "contentSha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        card_dir = OUT / EBAY_ID[vid]
        card_dir.mkdir(parents=True, exist_ok=True)
        path = card_dir / "summary_en.json"
        path.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"{vid:>4} {number:<12} chars={len(text):>5} words={len(text.split()):>4} -> altxyz/{EBAY_ID[vid]}/summary_en.json")
