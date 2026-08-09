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
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402
from snk_identity_discover import (  # noqa: E402
    gemrate_treatment,
    character_agrees,
    product_agrees,
    snk_treatment,
)
from op_identity_rules import names_a_treatment  # noqa: E402

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

# --- 4. tokenisation: what is NOT a product word ----------------------------
# GemRate stamps the grading year on the set name; no provider prints it.
year_ok, year_why = product_agrees(
    "2025 Carrying On His Will Alternate Art", "Base",
    "Monkey.D.Luffy [Alternate Art] OP13-118 Prices | One Piece Carrying On His Will",
    "",
)
check("grading year is not a product word", year_ok, True)
check("year case says nothing missing", year_why, "")

# "OP-13" and "OP13" are the same set code written two ways. Left split, one
# spelling leaves a bare "13" the other spelling can never match.
hyphen_ok, _ = product_agrees(
    "One Piece Carrying On His Will OP-13", "Wanted Alternate Art",
    "Monkey.D.Luffy [Wanted Poster] OP13-121 Prices | One Piece Carrying On His Will",
    "",
)
check("hyphenated set code still agrees", hyphen_ok, True)

# Volume numbers are NOT year-like and must keep discriminating.
vol_ok, vol_why = product_agrees(
    "One Piece Promos", "Illustration Box Vol.1",
    "O-Nami [Illustration Box Vol. 3] OP05-062 Prices | One Piece Promos", "",
)
check("Vol.1 does not match Vol.3", vol_ok, False)
check("volume rejection names the number", "'1'" in vol_why, True)

# A real difference in product words still holds, year fix or not.
word_ok, _ = product_agrees(
    "2025 3rd Anniversary! One Piece Card Treasure Campaign Pack", "Base",
    "Monkey.D.Luffy [3rd Anniversary] ST21-014 Prices | One Piece Starter Deck", "",
)
check("missing product words still hold", word_ok, False)


# --- 5. the character on the card -------------------------------------------
# Every case below is a proposal this lane actually produced on 2026-08-09.
# The first one is why the check exists: set code, number, language and
# treatment all agreed, and the card was a different Charlotte.
cracker_ok, cracker_why = character_agrees(
    "Charlotte Pudding",
    "Charlotte Cracker R-P [OP03-112]", "(Booster Pack Formidable Enemy)",
)
check("a different Charlotte is rejected", cracker_ok, False)
check("rejection names our character", "pudding" in cracker_why, True)

# Romanisation drifts between GemRate and SNKRDUNK. It must not read as a
# different person -- these two ARE the same proposal, twice accepted.
for ours, theirs in (
    ("Nefeltari Vivi", "Nefertari Vivi L-P [OP04-001]"),
    ("Nefeltari Vivi", "Nefertari Vivi SEC-P [OP04-118]"),
    ("Monkey D. Luffy", "Monkey D Luffy SR-P [OP07-109]"),
    ("Portgas D. Ace", "Portgas D Ace SR-P [OP02-013]"),
    ("Jewelry Bonney", "Jewelry Bonney SEC-SP (Comic Parallel) [OP12-118]"),
    ("Rebecca", "Rebecca L-P [OP04-039]"),
    ("Uta", "Uta SEC-P [OP02-120]"),
):
    same, why = character_agrees(ours, theirs, "")
    check(f"{ours} matches its own listing", same, True)

# A tail that differs only in romanisation is the same name; a stem that
# differs is not, however close the spelling looks.
check("Kaidou matches Kaido", character_agrees("Kaidou", "Kaido SR [OP01-001]")[0], True)
check("Nami is not Nojiko", character_agrees("Nami", "Nojiko R [OP01-020]")[0], False)

# An unreadable listing must not invent a verdict. A master name written only
# in Japanese leaves nothing to compare, and this check has to stand aside
# rather than hold a binding the other rules would have accepted.
check(
    "a listing with no Latin name stands aside",
    character_agrees("Nami", "ナミ", "")[0], True,
)
check(
    "a card with no name of ours stands aside",
    character_agrees("", "Nami L-P [OP03-040]")[0], True,
)


# --- 6. exactly one check judges the parallel --------------------------------
# GEMRATE_TREATMENT is the arbiter. What it can name is a treatment and belongs
# to the treatment comparison; what it cannot name is a product and belongs to
# product_agrees. These assert the split itself, because when both checks
# claimed the field the promos lost: product_agrees proved them and the
# treatment check rejected them anyway.
for product_parallel in (
    "Illustration Box Vol.1", "PSA Magazine Exclusive",
    "Official Event Top Prize", "Box Topper",
):
    check(
        f"'{product_parallel}' is a product, not a treatment",
        gemrate_treatment(product_parallel), "",
    )
for treatment_parallel in ("Alternate Art", "Manga Alternate Art", "Treasure Rare"):
    check(
        f"'{treatment_parallel}' is a treatment",
        bool(gemrate_treatment(treatment_parallel)), True,
    )

# A promo is proved by its product words appearing on the listing...
promo_ok, _ = product_agrees(
    "One Piece Japanese Promos", "Illustration Box Vol.1",
    "O-Nami R-P [OP05-062] (Illustration Box Vol. 1)", "",
)
check("promo proved by its own product words", promo_ok, True)
# ...and by nothing weaker. Same box, different volume, is a different card.
promo_bad, _ = product_agrees(
    "One Piece Japanese Promos", "Illustration Box Vol.1",
    "O-Nami R-P [OP05-062] (Illustration Box Vol. 3)", "",
)
check("wrong volume is still rejected", promo_bad, False)


# --- 7. which rejections may be reconsidered -------------------------------
# match_status='rejected' carries two meanings and only one is a ruling. Every
# blob below was read off a real row on 2026-08-09, when the database held 379
# rejected bindings: 2 verdicts and 377 collateral.
check(
    "an examined rejection is a verdict",
    R.rejection_is_verdict('{"action": "reject", "reason": "wrong product"}'), True,
)
check(
    "a wrong-printing rejection is a verdict",
    R.rejection_is_verdict('{"action": "reject-wrong-printing-source"}'), True,
)
# The shape psa_identity_repair left behind: the row still carries the evidence
# of the bind it was quarantining, so it literally says "confirm".
check(
    "collateral quarantine keeping stale confirm evidence is not a verdict",
    R.rejection_is_verdict(
        '{"action": "confirm", "contract": "active-762-exact-identity-repair-031-v1"}'
    ),
    False,
)
check(
    "the stamped quarantine is not a verdict either",
    R.rejection_is_verdict('{"action": "quarantine-unresolved-variant-identity"}'),
    False,
)
check("no evidence at all is not a verdict", R.rejection_is_verdict(None), False)
check(
    "an already-decoded blob is read the same way",
    R.rejection_is_verdict({"action": "reject"}), True,
)
# Unreadable evidence must NOT be reconsidered. Binding a card to the wrong
# product prices it wrong; leaving a card off the front end does not.
check("unparseable evidence holds", R.rejection_is_verdict("{not json"), True)
check("a JSON scalar holds", R.rejection_is_verdict('"reject"'), True)

# The 034 audit sheet's thirteen red rows are a human ruling about the CARD.
# It reaches a binding only by being copied onto it, and until it was, a
# reverify pass that reconsiders collateral promoted seven of the thirteen.
check(
    "a red-sheet quarantine is a verdict",
    R.rejection_is_verdict(json.dumps({
        "action": "quarantine-unresolved-variant-identity",
        "redListed": True, "reasonCode": "red_sheet_row",
    })),
    True,
)
check(
    "a quarantine that is NOT red-listed stays reconsiderable",
    R.rejection_is_verdict(json.dumps({
        "action": "quarantine-unresolved-variant-identity",
        "redListed": False, "reasonCode": "printing_mismatch",
    })),
    False,
)
check(
    "SQL predicate reads the same key",
    f"$.{R.REJECTION_RED_LIST_KEY}" in R.NOT_A_REJECTION_VERDICT_SQL, True,
)

# The SQL predicate is generated from the same set, so the two cannot drift.
for action in R.REJECTION_VERDICT_ACTIONS:
    check(
        f"SQL predicate names '{action}'",
        f"'{action}'" in R.NOT_A_REJECTION_VERDICT_SQL, True,
    )

# --- 6. the rules were One Piece's; pokemon is a second game, not a subset ---
# GemRate opens every pokemon set_name with the game's own name and SNKRDUNK's
# Japanese titles do not repeat it, so requiring it refused cards on a word that
# names no product. "one" and "piece" were stopwords from the first day; the
# second game's name simply was not.
poke_ok, poke_why = product_agrees(
    "2022 Pokemon Japanese Sword & Shield Vstar Universe",
    "Base",
    "ポケモンカードゲーム ソード&シールド VSTAR Universe",
    "",
)
check("the game's own name is not product evidence",
      "pokemon" in poke_why, False)

# A rarity that comes with its own collector number is proven by the number.
# Asking a Japanese storefront to print "special", "art" and "rare" is asking
# it to speak GemRate's English.
sar_ok, sar_why = product_agrees(
    "2023 Pokemon Japanese Sv1v-Violet EX",
    "Special Art Rare",
    "Pokemon Card Game SV1V Violet ex",
    "ポケモンカードゲーム 強化拡張パック バイオレットex",
)
check("Special Art Rare is not demanded as product words", sar_ok, True)
# Proof it is the rarity being excused and not the set going unchecked: the
# same listing for a DIFFERENT set is still refused.
wrong_set_ok, _ = product_agrees(
    "2023 Pokemon Japanese Sv2a-Pokemon Card 151",
    "Special Art Rare",
    "Pokemon Card Game SV1V Violet ex",
    "",
)
check("excusing the rarity does not excuse the set", wrong_set_ok, False)
check("Special Art Rare names a treatment",
      names_a_treatment("Special Art Rare"), True)
check("Illustration Rare names a treatment",
      names_a_treatment("Illustration Rare"), True)

# Finishes share a number with the base print, so the number cannot tell them
# apart and the words stay the only evidence there is. This must NOT relax.
mb_ok, mb_why = product_agrees(
    "2023 Pokemon Japanese Sv2a-Pokemon Card 151",
    "Master Ball Reverse Holo",
    "ポケモンカードゲーム SV2a ポケモンカード151 ピカチュウ",
    "",
)
check("Master Ball Reverse Holo is still required", mb_ok, False)
check("and the refusal names the missing finish", "master" in mb_why, True)
check("Master Ball Reverse Holo is NOT a treatment",
      names_a_treatment("Master Ball Reverse Holo"), False)
check("Reverse Holo is NOT a treatment", names_a_treatment("Reverse Holo"), False)
check("1st Edition is NOT a treatment", names_a_treatment("1st Edition"), False)

# A promo's product words still have to be proved: the parallel is the only
# thing naming the product, and no rarity table may swallow it.
promo_ok, promo_why = product_agrees(
    "2024 Pokemon Japanese SV-P Promo",
    "Gym Event Campaign",
    "ポケモンカードゲーム SV-P プロモ ピカチュウ",
    "",
)
check("a promo's own product words are still required", promo_ok, False)
check("'Gym Event Campaign' is not a treatment",
      names_a_treatment("Gym Event Campaign"), False)

# The treatment welded onto the name made this lane search for a card nobody
# sells, and three of the first 25 gap cards came back with zero hits.
check("the search name drops the welded treatment",
      R.card_name_without_treatment("Full Art/Pikachu Vmax"), "Pikachu Vmax")
check("a genuine slash in a name survives",
      R.card_name_without_treatment("Sabo/Koala"), "Sabo/Koala")

print()
if failures:
    print(f"{len(failures)} FAILURE(S)")
    raise SystemExit(1)
print("all snk discovery rules hold")
