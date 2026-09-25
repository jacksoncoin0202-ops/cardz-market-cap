#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Input corrections for leftover PC pages that already exist.

Promo stamps live on the number's home console with the product in the
bracket. JP Gym 2 is Challenge from the Darkness. Holo in the name
corroborates [Holo]. 1st-only JP sets have no Unlimited sibling.

These are INPUT corrections (shape 14 / 21). Unbracketed SP still
refuses (Yamato). [SP Foil] is how PriceCharting files Special
Alternate Art; [SP Gold] stays refused. Bare [Manga] matches manga AA
(PC's filing) but not Alternate Art. Crossing the ruins is not
aliased to Neo 2.

Run: python -X utf8 scripts/test_pc_leftover_input_corrections.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import (  # noqa: E402
    _fingerprint_variant_conflicts,
    _pc_language_from_console_slug,
    _pc_print_signature_ok,
    _pc_unbracketed_sp_on_own_set,
    _pc_unbracketed_own_set_print,
)
from rebuild_036_identity_rules import (  # noqa: E402
    _pc_number_set_explaining,
    _pc_print_belongs_to_number_set,
    pc_product_candidate_sets,
    set_names_a_card_could_carry,
)
from rebuild_036_reverify import _pc_bracket_names_our_product  # noqa: E402
from op_identity_rules import (  # noqa: E402
    names_a_treatment,
    our_product_text,
    product_agrees,
)

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    if got != want:
        FAILED.append(f"FAIL {label}: got {got!r}, want {want!r}")


DALLAS = {
    "tcg_code": "one-piece",
    "card_language": "en",
    "set_name": "One Piece Promos",
    "collector_number": "113",
    "canonical_name": (
        "2025 One Piece Promos Roronoa Zoro One Piece Day Dallas 113"
    ),
    "printing_code": "",
    "parallel_code": "",
    "v_set_code": "",
}
BOOSTER_ZORO = {
    "tcg_code": "one-piece",
    "card_language": "en",
    "set_name": "One Piece OP07-500 Years in the Future",
    "collector_number": "OP07-113",
    "canonical_name": (
        "2024 One Piece OP07-500 Years in the Future Roronoa Zoro Base OP07-113"
    ),
    "printing_code": "",
    "parallel_code": "base",
    "v_set_code": "OP07",
}
GYM2 = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese Gym 2",
    "collector_number": "6",
    "canonical_name": (
        "1999 Pokemon Japanese Gym 2 Blaine's Charizard-Holo Base 6"
    ),
}
HOLO = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese McDonald's",
    "collector_number": "004/018",
    "canonical_name": (
        "2002 Pokemon Japanese McDonald's Charmander-Holo Base 004/018"
    ),
    "printing_code": "",
    "parallel_code": "base",
}
FIRST_SOLE = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese XY Pokekyun Collection",
    "collector_number": "10/32",
    "canonical_name": (
        "2016 Pokemon Japanese XY Pokekyun Collection "
        "Full Art/Pikachu 1st Edition 10/32"
    ),
    "printing_code": "1st",
    "parallel_code": "1st Edition",
    "sole_1st_printing": True,
}
FIRST_TWIN = dict(FIRST_SOLE, sole_1st_printing=False)

DALLAS_FP = {
    "cardNumber": "OP07-113",
    "derivedLanguage": "en",
    "setName": "One Piece 500 Years in the Future",
}
GYM2_FP = {
    "cardNumber": "6",
    "derivedLanguage": "ja",
    "setName": "Pokemon Japanese Challenge from the Darkness",
}

# --- Gym 2 ≡ Challenge from the Darkness, not a random other JP set -----
check(
    "gym 2 tokens agree with Challenge from the Darkness",
    _fingerprint_variant_conflicts(GYM2_FP, GYM2),
    [],
)
neo_fp = {
    "cardNumber": "6",
    "derivedLanguage": "ja",
    "setName": "Pokemon Japanese Neo Destiny",
}
check(
    "gym 2 still conflicts with an unrelated JP set",
    bool(_fingerprint_variant_conflicts(neo_fp, GYM2)),
    True,
)
MASK = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese SV6-Transformation Mask",
    "collector_number": "130/101",
}
check(
    "Transformation Mask agrees with Mask of Change",
    _fingerprint_variant_conflicts(
        {"cardNumber": "130/101", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Mask of Change"},
        MASK,
    ),
    [],
)
RAY = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese M Rayquaza EX Battle Deck",
    "collector_number": "006/018",
}
check(
    "Mega-Rayquaza hyphen agrees with M Rayquaza",
    _fingerprint_variant_conflicts(
        {"cardNumber": "006/018", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Mega-Rayquaza EX Battle Deck"},
        RAY,
    ),
    [],
)
check(
    "Rayquaza-EX Mega Battle Deck agrees with M Rayquaza EX Battle Deck",
    _fingerprint_variant_conflicts(
        {"cardNumber": "006/018", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Rayquaza-EX Mega Battle Deck"},
        RAY,
    ),
    [],
)
ruins_fp = {
    "cardNumber": "6",
    "derivedLanguage": "ja",
    "setName": "Pokemon Japanese Crossing the Ruins",
}
check(
    "Crossing the ruins is not aliased to Gym 2",
    bool(_fingerprint_variant_conflicts(ruins_fp, GYM2)),
    True,
)

# --- promo bucket product lives in canonical_name -------------------------
check(
    "Promos set_name has no distinctive token; Dallas comes from canonical",
    "dallas" in our_product_text(DALLAS).lower(),
    True,
)
check(
    "booster set_name is used as-is; canonical anniversary words stay out",
    "dallas" in our_product_text(BOOSTER_ZORO).lower(),
    False,
)
ok, _ = product_agrees(
    our_product_text(DALLAS),
    "",
    "Roronoa Zoro [One Piece Day Dallas] OP07-113",
    "https://www.pricecharting.com/game/one-piece-500-years-in-the-future/"
    "roronoa-zoro-one-piece-day-dallas-op07-113",
)
check("Dallas listing proves the promo leftover", ok, True)
ok_wrong, _ = product_agrees(
    our_product_text(DALLAS),
    "",
    "Roronoa Zoro OP07-113",
    "https://www.pricecharting.com/game/one-piece-500-years-in-the-future/"
    "roronoa-zoro-op07-113",
)
check("booster unbracketed Zoro does not prove Dallas leftover", ok_wrong, False)

# --- number-set explaining for bare collector 113 -------------------------
explained = _pc_number_set_explaining(
    DALLAS_FP, DALLAS,
    _fingerprint_variant_conflicts(DALLAS_FP, DALLAS),
)
check("bare 113 promo is explained by the OP07 page set", bool(explained), True)
check(
    "[Dallas] is not the OP07 set's own print",
    _pc_print_belongs_to_number_set(explained, "One Piece Day Dallas"),
    False,
)
check(
    "unbracketed OP07 Zoro IS the number set's own print",
    _pc_print_belongs_to_number_set(explained, ""),
    True,
)

# --- product bracket earned from canonical --------------------------------
check(
    "Dallas leftover earns [One Piece Day Dallas] from canonical",
    _pc_bracket_names_our_product("One Piece Day Dallas", DALLAS),
    True,
)
check(
    "booster leftover cannot earn the Dallas product bracket",
    _pc_bracket_names_our_product("One Piece Day Dallas", BOOSTER_ZORO),
    False,
)
check(
    "Dallas leftover print signature accepts the product bracket",
    _pc_print_signature_ok("One Piece Day Dallas", DALLAS),
    True,
)
dodgers = dict(
    DALLAS,
    collector_number="EB02-010",
    printing_code="promo",
    parallel_code="Dodgers X One Piece Night",
    canonical_name=(
        "2025 One Piece Promos Monkey D. Luffy Dodgers X One Piece Night EB02-010"
    ),
)
check(
    "printing_code promo still accepts a product bracket earned from canonical",
    _pc_print_signature_ok("Dodgers", dodgers),
    True,
)
check(
    "Pokekyun 1st via parallel_code (blank printing_code) accepts unbracketed",
    _pc_print_signature_ok("", {
        **FIRST_SOLE, "printing_code": "", "parallel_code": "1st Edition",
    }),
    True,
)

# --- holo corroboration ---------------------------------------------------
check(
    "Charmander-Holo name corroborates [Holo]",
    _pc_print_signature_ok("Holo", HOLO),
    True,
)
non_holo = dict(HOLO, canonical_name="2002 Pokemon Japanese McDonald's Charmander Base 004/018")
check(
    "non-holo name cannot take the [Holo] page",
    _pc_print_signature_ok("Holo", non_holo),
    False,
)

# --- 1st-only JP set vs WOTC twin ----------------------------------------
check(
    "Pokekyun 1st-only accepts unbracketed heading",
    _pc_print_signature_ok("", FIRST_SOLE),
    True,
)
check(
    "WOTC 1st with Unlimited sibling still refuses unbracketed",
    _pc_print_signature_ok("", FIRST_TWIN),
    False,
)
check(
    "English WOTC 1st stamped sole-1st still refuses unbracketed Unlimited",
    _pc_print_signature_ok("", dict(
        FIRST_SOLE, card_language="en", set_name="Pokemon Fossil",
        canonical_name="1999 Pokemon Fossil 1st Edition Psyduck 53",
        collector_number="53",
    )),
    False,
)
check(
    "sole-1st with no card_language refuses (fail closed)",
    _pc_print_signature_ok("", dict(FIRST_SOLE, card_language="")),
    False,
)
check(
    "unbracketed still refuses SP (Yamato hole stays closed)",
    _pc_print_signature_ok("", {
        "tcg_code": "one-piece", "printing_code": "sp",
        "parallel_code": "Special Alternate Art",
        "canonical_name": "Yamato Special Alternate Art OP01-121",
        "sole_1st_printing": True,
    }),
    False,
)

# --- manga AA is PC's [Manga]; AA is not ---------------------------------
MANGA_AA = {
    "tcg_code": "one-piece",
    "printing_code": "",
    "parallel_code": "manga alternate art",
    "canonical_name": (
        "2024 One Piece Japanese OP08-Two Legends Silvers Rayleigh "
        "Manga Alternate Art OP08-118"
    ),
}
AA = {
    "tcg_code": "one-piece",
    "printing_code": "aa",
    "parallel_code": "Alternate Art",
    "canonical_name": (
        "2023 One Piece Japanese OP05-Awakening of the New Era "
        "Monkey D. Luffy Alternate Art OP05-119"
    ),
}
check("manga AA leftover accepts PC [Manga]", _pc_print_signature_ok("Manga", MANGA_AA), True)
check("printing_code manga also accepts [Manga]", _pc_print_signature_ok("Manga", {
    **MANGA_AA, "printing_code": "manga", "parallel_code": "",
}), True)
check("AA leftover still refuses [Manga]", _pc_print_signature_ok("Manga", AA), False)
PRB_AA = {
    "tcg_code": "one-piece",
    "printing_code": "",
    "parallel_code": "alternate art",
    "set_name": "One Piece Japanese PRB01-Premium Booster -One Piece Card the Best-",
    "canonical_name": (
        "2024 One Piece Japanese PRB01-Premium Booster "
        "-One Piece Card the Best- Monkey D. Luffy Alternate Art OP05-119"
    ),
    "v_set_code": "",
}
check(
    "PRB01 AA leftover accepts [Alternate Art PRB01]",
    _pc_print_signature_ok("Alternate Art PRB01", PRB_AA),
    True,
)
check(
    "booster AA leftover refuses [Alternate Art PRB01]",
    _pc_print_signature_ok("Alternate Art PRB01", AA),
    False,
)
check("[SP Foil] names Special Alternate Art leftover", _pc_print_signature_ok("SP Foil", {
    "tcg_code": "one-piece", "printing_code": "",
    "parallel_code": "special alternate art",
}), True)
check("[SP Gold] still unmatched for SP leftover", _pc_print_signature_ok("SP Gold", {
    "tcg_code": "one-piece", "printing_code": "",
    "parallel_code": "special alternate art",
}), False)

# --- chinese console slug is not English ---------------------------------
check("JP console stays ja", _pc_language_from_console_slug(
    "pokemon-japanese-sv2a-pokemon-card-151"), "ja")
check("EN console stays en", _pc_language_from_console_slug(
    "pokemon-scarlet-violet-151"), "en")
check("Chinese 151 console is zh, not en", _pc_language_from_console_slug(
    "pokemon-chinese-151-collect"), "zh")
check("Chinese gem pack console is zh", _pc_language_from_console_slug(
    "pokemon-chinese-gem-pack-3"), "zh")
_zh_conflicts = _fingerprint_variant_conflicts(
    {"cardNumber": "172", "derivedLanguage": "zh",
     "setName": "Pokemon Chinese 151 Collect"},
    {"tcg_code": "pokemon", "card_language": "zhCN",
     "set_name": "Pokemon Simplified Chinese 151 C-Collection 151",
     "collector_number": "172/151"},
)
check(
    "zh vs zhCN does not raise a language conflict",
    any(c.startswith("language:") for c in _zh_conflicts),
    False,
)

# --- TR page on the number's original console -----------------------------
TR = {
    "tcg_code": "one-piece",
    "card_language": "en",
    "set_name": "One Piece OP06-Wings of the Captain",
    "collector_number": "ST01-007",
    "canonical_name": (
        "2024 One Piece OP06-Wings of the Captain Nami Treasure Rare ST01-007"
    ),
    "printing_code": "tr",
    "parallel_code": "c-tr",
    "v_set_code": "ST01",
}
check(
    "via_number_set is a product candidate the TR leftover can agree with",
    "One Piece Starter Deck 1: Straw Hat Crew" in pc_product_candidate_sets(
        TR, "One Piece Starter Deck 1: Straw Hat Crew",
    ),
    True,
)
check(
    "[Treasure Rare] is reprint-only, not the number set's own print",
    _pc_print_belongs_to_number_set(
        "One Piece Starter Deck 1: Straw Hat Crew", "Treasure Rare",
    ),
    False,
)

# --- wanted AA corroborates [Wanted]; manga AA does not -------------------
WANTED = {
    "tcg_code": "one-piece",
    "printing_code": "",
    "parallel_code": "wanted alternate art",
    "canonical_name": (
        "2024 One Piece Japanese OP09-Emperors in the New World Buggy "
        "Wanted Alternate Art OP09-051"
    ),
}
check("Gold Manga Alternate Art is a treatment, not a product",
      names_a_treatment("Gold Manga Alternate Art"), True)
check(
    "[Wanted] is reprint-only, not the number set's own print",
    _pc_print_belongs_to_number_set("awakening of the new era", "Wanted"),
    False,
)
check("wanted AA accepts [Wanted Poster Foil]",
      _pc_print_signature_ok("Wanted Poster Foil", WANTED), True)
ok_wsj, why_wsj = product_agrees(
    our_product_text({
        "tcg_code": "one-piece",
        "set_name": "One Piece Japanese Promos",
        "canonical_name": (
            "2023 One Piece Japanese Promos Monkey D. Luffy "
            "Weekly Shonen Jump-Issue 36-37 P-043"
        ),
        "parallel_code": "Weekly Shonen Jump-Issue 36-37",
        "printing_code": "promo",
    }),
    "",
    "Monkey.D.Luffy [Weekly Shonen Jump Foil] P-043",
    "https://www.pricecharting.com/game/one-piece-japanese-promo/"
    "monkeydluffy-weekly-shonen-jump-foil-p-043",
)
check("WSJ Issue 36-37 leftover agrees with [Weekly Shonen Jump Foil]", ok_wsj, True)
check(
    "WSJ leftover print signature accepts [Weekly Shonen Jump Foil]",
    _pc_print_signature_ok("Weekly Shonen Jump Foil", {
        "tcg_code": "one-piece",
        "set_name": "One Piece Japanese Promos",
        "canonical_name": (
            "2023 One Piece Japanese Promos Monkey D. Luffy "
            "Weekly Shonen Jump-Issue 36-37 P-043"
        ),
        "parallel_code": "Weekly Shonen Jump-Issue 36-37",
        "printing_code": "promo",
    }),
    True,
)
GYARADOS = {
    "tcg_code": "pokemon",
    "printing_code": "",
    "parallel_code": "holo",
    "canonical_name": (
        "2015 Pokemon Japanese XY Promo Pretend Gyarados Pikachu Holo 151/XY-P"
    ),
}
check("Pretend Gyarados-Holo accepts unbracketed PC heading",
      _pc_print_signature_ok("", GYARADOS), True)
check("Yamato SP still refuses unbracketed",
      _pc_print_signature_ok("", {
          "tcg_code": "one-piece", "printing_code": "sp",
          "parallel_code": "Special Alternate Art",
          "canonical_name": "Yamato Special Alternate Art OP01-121",
      }), False)
check(
    "unbracketed base still refuses leftover SP whose NAME spells the treatment",
    _pc_print_signature_ok("", {
        "tcg_code": "one-piece", "printing_code": "",
        "parallel_code": "special alternate art",
        "canonical_name": (
            "2025 One Piece Japanese OP11-A Fist of Divine Speed "
            "Shirahoshi Special Alternate Art EB01-057"
        ),
    }),
    False,
)
FESTA = {
    "tcg_code": "pokemon",
    "printing_code": "",
    "parallel_code": "20th anniversary festa",
    "canonical_name": (
        "2016 Pokemon Japanese XY Promo Pikachu-Holo 20th Anniversary Festa 279/XY-P"
    ),
}
check("20th Anniversary Festa leftover accepts unbracketed heading that names it",
      _pc_print_signature_ok("", FESTA), True)
CHOPPER = {
    "tcg_code": "one-piece",
    "printing_code": "",
    "parallel_code": "one piece chopper's 1",
    "set_name": "One Piece Japanese Promos",
    "canonical_name": (
        "2026 One Piece Japanese Promos Tony Tony Chopper One Piece Chopper's 1 EB02-003"
    ),
}
check("Chopper's 1 leftover accepts [Comic]",
      _pc_print_signature_ok("Comic", CHOPPER), True)
ok_ch, _ = product_agrees(
    our_product_text(CHOPPER),
    "",
    "Tony Tony.Chopper [Comic] EB02-003",
    "https://www.pricecharting.com/game/one-piece-japanese-promo/"
    "tony-tonychopper-comic-eb02-003",
)
check("Chopper's 1 listing agrees with [Comic] product page", ok_ch, True)
PRIZE = {
    "tcg_code": "one-piece",
    "set_name": "One Piece Japanese Promos",
    "printing_code": "prize",
    "parallel_code": "Official Event Top Prize",
    "canonical_name": (
        "2023 One Piece Japanese Promos Roronoa Zoro Official Event Top Prize OP01-025"
    ),
}
PRIZE_NOT_TOP = {
    "tcg_code": "one-piece",
    "set_name": "One Piece Japanese Promos",
    "printing_code": "",
    "parallel_code": "official event prize",
    "canonical_name": (
        "2024 One Piece Japanese Promos Perona Official Event Prize OP06-093"
    ),
}
check("Official Event Top Prize accepts [Flagship Battle]",
      _pc_print_signature_ok("Flagship Battle", PRIZE), True)
check("Official Event Top Prize refuses [Flagship Battle Top 8]",
      _pc_print_signature_ok("Flagship Battle Top 8", PRIZE), False)
check(
    "Official Event Top Prize leftover carries Flagship Battle as a product name",
    "Flagship Battle" in set_names_a_card_could_carry(PRIZE),
    True,
)
ok_fb, _ = product_agrees(
    our_product_text(PRIZE, "Flagship Battle"),
    "",
    "Roronoa Zoro [Flagship Battle] OP01-025",
    "https://www.pricecharting.com/game/one-piece-japanese-romance-dawn/"
    "roronoa-zoro-flagship-battle-op01-025",
)
check("Flagship Battle listing agrees with Official Event Top Prize leftover", ok_fb, True)
check(
    "[Flagship Battle] bracket names Official Event Top Prize leftover",
    _pc_bracket_names_our_product("Flagship Battle", PRIZE),
    True,
)
check(
    "[Flagship Battle Top 8] still does not name Official Event Top Prize",
    _pc_bracket_names_our_product("Flagship Battle Top 8", PRIZE),
    False,
)
check(
    "Official Event Prize (not Top) does not carry champion Flagship Battle",
    "Flagship Battle" in set_names_a_card_could_carry(PRIZE_NOT_TOP),
    False,
)
check(
    "Official Event Prize leftover carries Flagship Battle Top 8",
    "Flagship Battle Top 8" in set_names_a_card_could_carry(PRIZE_NOT_TOP),
    True,
)
check(
    "[Flagship Battle Top 8] names Official Event Prize leftover",
    _pc_bracket_names_our_product("Flagship Battle Top 8", PRIZE_NOT_TOP),
    True,
)
check(
    "Official Event Prize accepts [Flagship Battle Top 8]",
    _pc_print_signature_ok("Flagship Battle Top 8", PRIZE_NOT_TOP),
    True,
)
check(
    "Official Event Prize refuses champion [Flagship Battle]",
    _pc_print_signature_ok("Flagship Battle", PRIZE_NOT_TOP),
    False,
)
check(
    "[Flagship Battle] does not name Official Event Prize leftover",
    _pc_bracket_names_our_product("Flagship Battle", PRIZE_NOT_TOP),
    False,
)
check(
    "Asia prize leftover does not carry Flagship Battle Top 8",
    "Flagship Battle Top 8" in set_names_a_card_could_carry({
        "tcg_code": "one-piece",
        "set_name": "One Piece Japanese Promos",
        "parallel_code": "official event prize-asia",
        "canonical_name": (
            "2024 One Piece Japanese Promos Perona "
            "Official Event Prize-Asia OP06-093"
        ),
    }),
    False,
)
check(
    "booster leftover does not earn [Flagship Battle] from its number set",
    _pc_bracket_names_our_product("Flagship Battle", BOOSTER_ZORO),
    False,
)
ANNIV = {
    "tcg_code": "one-piece",
    "set_name": "One Piece Japanese OP05-Awakening of the New Era",
    "collector_number": "ST01-012",
    "printing_code": "",
    "parallel_code": "1st anniversary",
    "canonical_name": (
        "2023 One Piece Japanese OP05-Awakening of the New Era "
        "Monkey D. Luffy 1st Anniversary ST01-012"
    ),
}
check(
    "1st anniversary leftover carries 1st Anniversary as a product name",
    "1st Anniversary" in set_names_a_card_could_carry(ANNIV),
    True,
)
check(
    "[1st Anniversary] bracket names leftover stamped 1st anniversary",
    _pc_bracket_names_our_product("1st Anniversary", ANNIV),
    True,
)
check(
    "[1st Anniversary Signature] still does not name 1st anniversary leftover",
    _pc_bracket_names_our_product("1st Anniversary Signature", ANNIV),
    False,
)
BOOSTER_ANNIV_CANONICAL = {
    "tcg_code": "one-piece",
    "set_name": "One Piece Japanese OP05-Awakening of the New Era",
    "printing_code": "base",
    "parallel_code": "base",
    "canonical_name": (
        "2023 One Piece Japanese OP05-Awakening of the New Era "
        "Monkey D. Luffy 1st Anniversary ST01-012"
    ),
}
check(
    "booster with 1st Anniversary only in canonical does not earn the bracket",
    _pc_bracket_names_our_product("1st Anniversary", BOOSTER_ANNIV_CANONICAL),
    False,
)
check(
    "booster canonical 1st Anniversary still refuses the anniversary page",
    _pc_print_signature_ok("1st Anniversary", BOOSTER_ANNIV_CANONICAL),
    False,
)
check(
    "1st anniversary leftover accepts [1st Anniversary] print signature",
    _pc_print_signature_ok("1st Anniversary", ANNIV),
    True,
)
VICTINI = {
    "tcg_code": "pokemon",
    "printing_code": "prize",
    "parallel_code": "standard",
    "canonical_name": (
        "2025 Pokemon Japanese SV-P Promo Victini "
        "Victini Bwr Event Prize 288/SV-P"
    ),
}
check(
    "pokemon event-prize leftover accepts unbracketed PC page",
    _pc_print_signature_ok("", VICTINI),
    True,
)
check(
    "One Piece prize leftover still refuses unbracketed (Yamato-shaped)",
    _pc_print_signature_ok("", PRIZE),
    False,
)
check(
    "SP leftover still refuses unbracketed after prize exception",
    _pc_print_signature_ok("", {
        "tcg_code": "one-piece",
        "printing_code": "",
        "parallel_code": "special alternate art",
        "canonical_name": "Shirahoshi Special Alternate Art EB01-057",
    }),
    False,
)
check(
    "[SP Foil] names Nami Special Alternate Art leftover",
    _pc_print_signature_ok("SP Foil", {
        "tcg_code": "one-piece", "printing_code": "",
        "parallel_code": "special alternate art",
        "set_name": "One Piece Japanese OP08-Two Legends",
        "canonical_name": "Nami Special Alternate Art OP08-106",
    }),
    True,
)
check(
    "coarse sr-spc still refuses [SP Foil]",
    _pc_print_signature_ok("SP Foil", {
        "tcg_code": "one-piece", "printing_code": "sp",
        "parallel_code": "sr-spc",
    }),
    False,
)
PRB2 = {
    "tcg_code": "one-piece",
    "set_name": (
        "One Piece Japanese PRB02-Premium Booster "
        "-One Piece Card the Best- Vol.2"
    ),
    "collector_number": "OP06-119",
    "canonical_name": (
        "2025 One Piece Japanese PRB02-Premium Booster "
        "-One Piece Card the Best- Vol.2 Sanji Manga Alternate Art OP06-119"
    ),
}
check(
    "PRB02 leftover carries PC Premium Booster 2 as a product candidate",
    "One Piece Japanese Premium Booster 2" in set_names_a_card_could_carry(PRB2),
    True,
)
ok_prb2, _ = product_agrees(
    our_product_text(PRB2, "One Piece Japanese Premium Booster 2"),
    "",
    "Sanji [Manga] OP06-119 One Piece Japanese Premium Booster 2",
    "https://www.pricecharting.com/game/one-piece-japanese-premium-booster-2/"
    "sanji-manga-op06-119",
)
check("PRB02 leftover agrees with the PC PRB2 console listing", ok_prb2, True)
BOOSTER_SANJI = {
    "tcg_code": "one-piece",
    "set_name": "One Piece Japanese OP06-Wings of the Captain",
    "collector_number": "OP06-119",
    "canonical_name": "Sanji Base OP06-119",
}
check(
    "OP06 booster leftover does not grow a PRB02 console name",
    "One Piece Japanese Premium Booster 2" in set_names_a_card_could_carry(BOOSTER_SANJI),
    False,
)
check("manga AA still refuses [Wanted Poster]",
      _pc_print_signature_ok("Wanted Poster", MANGA_AA), False)

# --- JP Start Deck reverse holo is [Mirror Holo] --------------------------
MIRROR = {
    "tcg_code": "pokemon",
    "printing_code": "",
    "parallel_code": "reverse holo",
    "canonical_name": (
        "2025 Pokemon Japanese MC-Start Deck 100 Battle Collection "
        "Pikachu Reverse Holo 225/742"
    ),
}
check(
    "Mega Tokyo's name corroborates [Mega Tokyo's]",
    _pc_print_signature_ok("Mega Tokyo's", {
        "tcg_code": "pokemon", "printing_code": "", "parallel_code": "",
        "canonical_name": (
            "2014 Pokemon Japanese XY Promo Mega Tokyo's Pikachu Base 098/XY-P"
        ),
    }),
    True,
)
check(
    "Tea Party name corroborates [Tea Party]",
    _pc_print_signature_ok("Tea Party", {
        "tcg_code": "pokemon", "printing_code": "base", "parallel_code": "",
        "canonical_name": (
            "2019 Pokemon Japanese SM Promo Tea Party Pikachu "
            "Pokemon Center Kyoto 325/SM-P"
        ),
    }),
    True,
)
check(
    "Official Event Top Prize accepts [Event Top Prize]",
    _pc_print_signature_ok("Event Top Prize", {
        "tcg_code": "one-piece", "printing_code": "prize",
        "parallel_code": "Official Event Top Prize",
        "canonical_name": (
            "2025 One Piece Japanese Promos Monkey D. Luffy "
            "Official Event Top Prize ST21-014"
        ),
    }),
    True,
)
FESTA = {
    "tcg_code": "pokemon", "printing_code": "base",
    "parallel_code": "standard",
    "canonical_name": (
        "2017 Pokemon Japanese SM Promo Pikachu Pokemon Card Festa 061/SM-P"
    ),
}
check(
    "Card Festa 2017 leftover accepts [Battle Festa 2017] (same SM-P 061)",
    _pc_print_signature_ok("Battle Festa 2017", FESTA),
    True,
)
check(
    "Card Festa 2017 leftover refuses [Battle Festa 2015]",
    _pc_print_signature_ok("Battle Festa 2015", FESTA),
    False,
)
NEO2 = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese Neo 2",
    "collector_number": "197",
    "canonical_name": "2000 Pokemon Japanese Neo 2 Umbreon-Holo Base 197",
}
check(
    "Neo 2 agrees with Crossing the Ruins",
    _fingerprint_variant_conflicts(
        {"cardNumber": "197", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Crossing the Ruins"},
        NEO2,
    ),
    [],
)
NEO2_PROMO = dict(
    NEO2,
    set_name="Pokemon Japanese Neo 2 Promo",
    canonical_name="2000 Pokemon Japanese Neo 2 Promo Umbreon Promo 197",
    parallel_code="promo",
)
check(
    "Neo 2 Promo still conflicts with Crossing the Ruins booster page",
    bool(_fingerprint_variant_conflicts(
        {"cardNumber": "197", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Crossing the Ruins"},
        NEO2_PROMO,
    )),
    True,
)
GB = {
    "tcg_code": "pokemon",
    "card_language": "ja",
    "set_name": "Pokemon Japanese Promo Game Boy",
    "collector_number": "149",
    "canonical_name": "1998 Pokemon Japanese Promo Game Boy Dragonite-Holo Base 149",
}
check(
    "Game Boy promo agrees with Japanese Promo console",
    _fingerprint_variant_conflicts(
        {"cardNumber": "149", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Promo"},
        GB,
    ),
    [],
)
check(
    "Game Boy promo still conflicts with Topsun",
    bool(_fingerprint_variant_conflicts(
        {"cardNumber": "149", "derivedLanguage": "ja",
         "setName": "Pokemon Japanese Topsun"},
        GB,
    )),
    True,
)
CBB3 = {
    "tcg_code": "pokemon",
    "card_language": "zhCN",
    "set_name": "Pokemon Simplified Chinese CBB3 C-Gem Pack Vol 3",
    "collector_number": "07",
    "canonical_name": (
        "2025 Pokemon Simplified Chinese CBB3 C-Gem Pack Vol 3 Gengar Base 07"
    ),
}
check(
    "CBB3 Gengar 07 agrees with PC #307 on gem pack 3",
    _fingerprint_variant_conflicts(
        {"cardNumber": "307", "derivedLanguage": "zh",
         "setName": "Pokemon Chinese Gem Pack 3"},
        CBB3,
    ),
    [],
)
check(
    "CBB3 Gengar 07 still conflicts with a non-gem-pack 307",
    bool(_fingerprint_variant_conflicts(
        {"cardNumber": "307", "derivedLanguage": "zh",
         "setName": "Pokemon Chinese 151"},
        CBB3,
    )),
    True,
)
check(
    "PRB manga leftover may take [Manga] on the number's console",
    _pc_print_belongs_to_number_set(
        "romance dawn", "Manga",
        {"set_name": "One Piece Japanese PRB01-Premium Booster -One Piece Card the Best-"},
    ),
    False,
)
check(
    "booster leftover still treats [Manga] as the number's own print",
    _pc_print_belongs_to_number_set("romance dawn", "Manga"),
    True,
)
SHIRAHOSHI_SP = {
    "tcg_code": "one-piece",
    "printing_code": "",
    "parallel_code": "special alternate art",
    "set_name": "One Piece Japanese OP11-A Fist of Divine Speed",
    "canonical_name": (
        "2025 One Piece Japanese OP11-A Fist of Divine Speed "
        "Shirahoshi Special Alternate Art EB01-057"
    ),
}
YAMATO_SP = {
    "tcg_code": "one-piece",
    "printing_code": "sp",
    "parallel_code": "special alternate art",
    "set_name": "One Piece Japanese OP05-Awakening of the New Era",
    "canonical_name": "Yamato Special Alternate Art OP01-121",
}
check(
    "unbracketed SP leftover still refuses print_signature (Yamato hole)",
    _pc_print_signature_ok("", SHIRAHOSHI_SP),
    False,
)
check(
    "unbracketed SP on leftover's own set is the reprint PC filed without a bracket",
    _pc_unbracketed_sp_on_own_set("", "", SHIRAHOSHI_SP),
    True,
)
check(
    "unbracketed SP on the NUMBER's set stays False (Yamato)",
    _pc_unbracketed_sp_on_own_set("romance dawn", "", YAMATO_SP),
    False,
)
check(
    "bracketed SP does not use the unbracketed own-set carve-out",
    _pc_unbracketed_sp_on_own_set("", "SP Foil", SHIRAHOSHI_SP),
    False,
)
check(
    "AA leftover still refuses unbracketed own-set carve-out",
    _pc_unbracketed_sp_on_own_set("", "", {
        "tcg_code": "one-piece",
        "printing_code": "aa",
        "parallel_code": "alternate art",
        "set_name": "One Piece Japanese OP05-Awakening of the New Era",
        "canonical_name": "Monkey D. Luffy Alternate Art OP05-119",
    }),
    False,
)

GENGAR_ERR = {
    "tcg_code": "pokemon",
    "printing_code": "",
    "parallel_code": "mega attack rare-incorrect texture",
    "set_name": "Pokemon Japanese M2a-Mega Dream EX",
    "canonical_name": (
        "2025 Pokemon Japanese M2a-Mega Dream EX Mega Gengar EX "
        "Mega Attack Rare-Incorrect Texture 230/193"
    ),
}
GENGAR_MA = {
    "tcg_code": "pokemon",
    "printing_code": "base",
    "parallel_code": "Mega Attack Rare",
    "set_name": "Pokemon Japanese M2a-Mega Dream EX",
    "canonical_name": (
        "2025 Pokemon Japanese M2a-Mega Dream EX Mega Gengar EX "
        "Mega Attack Rare 230/193"
    ),
}
check(
    "blank printing_code leftover still accepts unbracketed heading",
    _pc_print_signature_ok("", GENGAR_ERR),
    True,
)
check(
    "unbracketed Mega Dream page is the regular MAR, never the texture-error print",
    _pc_unbracketed_own_set_print("", "", GENGAR_ERR),
    False,
)
check(
    "correct MA leftover gets no own-set carve-out either",
    _pc_unbracketed_own_set_print("", "", GENGAR_MA),
    False,
)

check("reverse holo leftover accepts [Mirror Holo]",
      _pc_print_signature_ok("Mirror Holo", MIRROR), True)
check("Master Ball leftover still refuses [Mirror Holo]",
      _pc_print_signature_ok("Mirror Holo", {
          "tcg_code": "pokemon", "printing_code": "mb",
          "parallel_code": "master ball reverse holo",
          "canonical_name": "Pikachu Master Ball Reverse Holo 025",
      }), False)

if FAILED:
    print("\n".join(FAILED))
    raise SystemExit(1)
print("POSITIVE_OK leftover input corrections")
