#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What a PriceCharting product page has to prove before a card may attach.

Two rules are held here, and they only make sense together. Reading the print
treatment as part of the card's name was wrong in both directions -- it refused
pages that were right and admitted pages that were wrong -- but dropping the
treatment alone would have opened a hole, because until 2026-08-09 nothing made
a pokemon page prove its collector number. Every case below is a real page from
the 2026-08-09 gap sweep.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_full_shard_runner as P  # noqa: E402
from rebuild_036 import card_name_without_treatment as bare  # noqa: E402

FAILED: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got == want:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}: got {got!r}, want {want!r}")


# 1. A treatment is not a name.
check("a known print phrase is dropped",
      bare("Full Art/Charizard GX"), "Charizard GX")
check("case and spacing do not save it",
      bare("Reverse Foil/Pikachu"), "Pikachu")
check("an unknown left side is kept whole",
      bare("Team Rocket/Meowth"), "Team Rocket/Meowth")
check("a name with no slash is untouched",
      bare("Moltres & Zapdos & Articuno GX"), "Moltres & Zapdos & Articuno GX")
check("empty stays empty", bare(""), "")


# 2. The treatment was actively admitting the wrong card.
#
# name_ok short-circuits on the first word, so "Full Art/M Pidgeot EX" matched
# a Trainer card named "Double Full Heal" -- they share "full".
DOUBLE_FULL_HEAL = "Double Full Heal #105 Prices | Pokemon Diamond & Pearl | Pokemon Cards"
check("the glued name matched an unrelated Trainer card",
      P.name_ok("Full Art/M Pidgeot EX", DOUBLE_FULL_HEAL), True)
check("the bare name does not",
      P.name_ok(bare("Full Art/M Pidgeot EX"), DOUBLE_FULL_HEAL), False)

# ...and refusing pages that were right.
CHARIZARD_GX = "Charizard GX #SV49 Prices | Pokemon Hidden Fates | Pokemon Cards"
check("the glued name refused its own product page",
      P.name_ok("Full Art/Charizard GX", CHARIZARD_GX), False)
check("the bare name accepts it",
      P.name_ok(bare("Full Art/Charizard GX"), CHARIZARD_GX), True)


# 3. A pokemon page must now prove the number, and does.
def pokemon(num: str, language: str = "en") -> dict[str, object]:
    return {"name": "x", "set": "", "num": num, "tcg": "pokemon", "language": language}


check("a set-prefixed page number matches a bare card number",
      P.page_identity_ok(
          pokemon("050"),
          "Charizard V #SWSH050 Prices | Pokemon Promo | Pokemon Cards",
          "https://www.pricecharting.com/game/pokemon-promo/charizard-v-swsh050"),
      True)
check("a shared prefix matches",
      P.page_identity_ok(
          pokemon("SV49"), CHARIZARD_GX,
          "https://www.pricecharting.com/game/pokemon-hidden-fates/charizard-gx-sv49"),
      True)
check("the alt-art secret is not the card that asked for 095",
      P.page_identity_ok(
          pokemon("095"),
          "Umbreon VMAX #215 Prices | Pokemon Evolving Skies | Pokemon Cards",
          "https://www.pricecharting.com/game/pokemon-evolving-skies/umbreon-vmax-215"),
      False)
check("an English #001 does not attach to a Chinese #708",
      P.page_identity_ok(
          pokemon("001"),
          "Pikachu [Full Art] #708 Prices | Pokemon Chinese Gem Pack | Pokemon Cards",
          "https://www.pricecharting.com/game/pokemon-chinese-gem-pack/pikachu-full-art-708"),
      False)
check("a page printing no number proves nothing",
      P.page_identity_ok(
          pokemon("050"),
          "Charizard V Prices | Pokemon Promo | Pokemon Cards",
          "https://www.pricecharting.com/game/pokemon-promo/charizard-v"),
      False)
check("a card with no number of its own proves nothing either",
      P.page_identity_ok(
          pokemon(""),
          "Charizard V #SWSH050 Prices | Pokemon Promo | Pokemon Cards",
          "https://www.pricecharting.com/game/pokemon-promo/charizard-v-swsh050"),
      False)

# The language gate still outranks a matching number.
check("a Japanese page still refuses an English card",
      P.page_identity_ok(
          pokemon("005"),
          "Pikachu V #5 Prices | Pokemon Japanese 25th Anniversary Golden Box",
          "https://www.pricecharting.com/game/pokemon-japanese-25th-anniversary/pikachu-v-5"),
      False)


# 4. One Piece keeps its own reading. "OP09-106" is one token, and splitting it
#    the pokemon way would read 9106, so the two rules stay separate.
def one_piece(num: str) -> dict[str, object]:
    return {"name": "x", "set": "", "num": num, "tcg": "one-piece", "language": "en"}


check("a One Piece single-token number still resolves",
      P.page_identity_ok(
          one_piece("OP09-106"),
          "Nico Olvia [Let's Get Started Full Art] OP09-106 Prices | One Piece",
          "https://www.pricecharting.com/game/one-piece/nico-olvia-op09-106"),
      True)
check("a One Piece number absent from the page is refused",
      P.page_identity_ok(
          one_piece("OP09-106"),
          "Tony Tony.Chopper EB01-006 Prices | One Piece",
          "https://www.pricecharting.com/game/one-piece/chopper-eb01-006"),
      False)


print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    raise SystemExit(1)
print("pc shard identity rules hold")
