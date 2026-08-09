#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the two rules that decide whether a discovered SNKRDUNK item may bind.

Both were written after a dry run tried to bind the wrong card:

1. Treatment vocabulary. SNKRDUNK spells the treatment as a rarity suffix
   (R-P, SEC-SPC, SR-TR) and GemRate spells it in words. Anything either side
   says that the table cannot name must hold, not pass.

2. Product agreement. One Piece reprints a card under its ORIGINAL number in
   many later products, so an agreeing set code is NOT proof of the same
   product -- OP01-016 Nami exists as the Romance Dawn parallel AND the 25th
   Anniversary premium collection. The real dry run proposed the wrong one.

Run: python -X utf8 scripts/test_snk_identity_discover_rules.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from snk_identity_discover import (  # noqa: E402
    gemrate_treatment,
    product_agrees,
    snk_treatment,
)

failures: list[str] = []


def check(label: str, got: object, want: object) -> None:
    if got != want:
        failures.append(f"{label}: got {got!r}, want {want!r}")
        print(f"FAIL {label}: got {got!r}, want {want!r}")
    else:
        print(f"ok   {label}")


# --- 1. treatment vocabulary, every pair read off an accepted binding -------
check("SEC -> base", snk_treatment("Monkey.D.Luffy SEC [OP10-118] [EN](Booster Pack)", ""), "base")
check("R-P -> aa", snk_treatment("Boa Hancock R-P [OP13-051] [EN](Booster Pack)", ""), "aa")
check("SEC-P -> aa", snk_treatment("Monkey.D.Luffy SEC-P [OP05-119] [EN](Booster)", ""), "aa")
check("R-SPC -> sp", snk_treatment("Nami R-SPC [OP01-016] [EN](Booster Pack)", ""), "sp")
check("SR-TR -> tr", snk_treatment("Monkey.D.Luffy SR-TR [OP07-109] [EN](Booster)", ""), "tr")
check("SEC-GSP -> gsp", snk_treatment("Gol D. Roger SEC-GSP [OP09-118](Booster)", ""), "gsp")
check(
    "SR-SP + comic gloss -> manga",
    snk_treatment("Boa Hancock SR-SP (Comic Parallel) [OP07-051](Booster)", ""),
    "manga",
)
# A bare -SP names no treatment this table can prove: it must hold.
check("bare SR-SP -> unmapped", snk_treatment("Someone SR-SP [OP07-051](Booster)", ""), "")
check("no rarity token -> unmapped", snk_treatment("Nami [OP01-016] [CHN] (1st Anniversary)", ""), "")

check("GemRate Alternate Art", gemrate_treatment("Alternate Art"), "aa")
check("GemRate Manga Alternate Art", gemrate_treatment("Manga Alternate Art"), "manga")
check("GemRate Special Alternate Art", gemrate_treatment("Special Alternate Art"), "sp")
check("GemRate Treasure Rare", gemrate_treatment("Treasure Rare"), "tr")
check("GemRate empty -> base", gemrate_treatment(""), "base")
# Product-specific wordings are not treatments and must not silently pass.
check("GemRate '1st Anniversary' unmapped", gemrate_treatment("1st Anniversary"), "")
check("GemRate 'Dodgers X One Piece Night' unmapped",
      gemrate_treatment("Dodgers X One Piece Night"), "")

# --- 2. product agreement ---------------------------------------------------
# The exact pair the dry run got wrong: same set code, different product.
wrong_ok, wrong_why = product_agrees(
    "One Piece Japanese OP01-Romance Dawn",
    "Alternate Art",
    "Nami R-P [OP01-016] (Premium Card Collection 25th Anniversary Edition)",
    "ナミ R-P  [OP01-016] (プレミアムカードコレクション25周年エディション)",
)
check("25th Anniversary rejected for a Romance Dawn card", wrong_ok, False)
check("rejection names the missing words", "product_mismatch" in wrong_why, True)

right_ok, _ = product_agrees(
    "One Piece Japanese OP01-Romance Dawn",
    "Alternate Art",
    "Nami R-P [OP01-016] (Booster Pack ROMANCE DAWN)",
    "ナミ R-P [OP01-016] (ブースターパック ロマンスドーン)",
)
check("Romance Dawn booster accepted", right_ok, True)

future_ok, _ = product_agrees(
    "One Piece Japanese OP07-500 Years in the Future",
    "Manga Alternate Art",
    'Boa Hancock SR-SP (Comic Parallel) [OP07-051](Booster Pack "The Future After 500 years")',
    "",
)
check("word-order drift still agrees", future_ok, True)

# --- 3. promos: the product moves out of set_name and into parallel ----------
# "One Piece Japanese Promos" is GemRate's bucket for every promo ever
# printed. Alone it proves nothing, so it must not accept a listing merely for
# containing the word "Promotional".
loose_ok, loose_why = product_agrees(
    "One Piece Japanese Promos",
    "Ichiban Kuji Purchase Bonus",
    'Monkey.D.Luffy SR-P (Opened) [OP07-109](Promotional Card "Luffy Get Campaign")',
    "",
)
check("promo bucket does not accept any promotional listing", loose_ok, False)
check("promo rejection names the parallel words",
      "ichiban" in loose_why and "kuji" in loose_why, True)

# When the listing does name the same event, the parallel carries the proof.
event_ok, _ = product_agrees(
    "One Piece Japanese Promos",
    "Bandai Card Games Fest",
    "Monkey.D.Luffy P-P [P-001](Bandai Card Games Fest 23-24 Winner Prize)",
    "",
)
check("promo accepted when the listing names the same event", event_ok, True)

# A promo whose parallel is empty has nothing left to prove identity with.
blind_ok, blind_why = product_agrees(
    "One Piece Japanese Promos", "",
    "Monkey.D.Luffy SR-P [P-001](Promotional Card)", "",
)
check("promo with no parallel holds", blind_ok, False)
check("blind promo says why", blind_why, "our_set_name_has_no_distinctive_token")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    raise SystemExit(1)
print("all snk discovery rules hold")
