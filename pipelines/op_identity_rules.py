#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One Piece identity vocabulary, shared by every provider lane.

The shared conflict check in rebuild_036 stops as soon as the set CODES agree
-- "an agreeing set code is the vocabulary-free signal". That is true for
Pokemon and false for One Piece, because a One Piece card keeps its ORIGINAL
number when it is reprinted. OP01-016 Nami is the Romance Dawn booster
parallel AND the 25th Anniversary premium collection AND a gift-collection
promo, all wearing the same number.

Both discovery lanes walked into it independently:

  SNKRDUNK  a dry run proposed the 25th Anniversary item for the Romance Dawn
            card, because every conflict check agreed.
  PriceCharting  a search resolved "Boa Hancock 038" onto the OP07 booster
            Alternate Art when the catalog row was the PSA Magazine promo.

So the rule lives here, once, and every lane imports it. Nothing in this file
knows which provider it is judging: it compares the words OUR catalog uses for
a product against the words a listing uses, whoever wrote the listing.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rebuild_036 as R

# "One Piece Japanese OP05-Awakening of the New Era" -> OP05; "...PRB01-..." -> PRB01
SET_CODE_RE = re.compile(r"\b([A-Z]{2,4}\d{2})\b")

# SNKRDUNK writes the treatment as a suffix on the rarity code, right before
# the bracketed product number: "Nami R-P [OP01-016]". Every pair below was
# read off a One Piece binding this database already accepted, not guessed:
#
#   SEC     [OP10-118]  <-> printing base   (item 563129)
#   R-P     [OP13-051]  <-> printing aa     "Alternate Art"        (718301)
#   SEC-P   [OP05-119]  <-> printing aa     "Alternate Art"        (198698)
#   R-SPC   [OP01-016]  <-> printing sp     "Special Alternate Art" (198702)
#   SEC-SPC [OP05-119]  <-> printing sp     "Wanted Alternate Art"  (471531)
#   SR-TR   [OP07-109]  <-> printing tr     "Treasure Rare"        (385091)
#   SEC-GSP [OP09-118]  <-> "Gold"                                  (349472)
#
# A bare "-SP" is deliberately absent: the only -SP listings seen so far spell
# a gloss next to it ("(Comic Parallel)" / "(コミパラ)"), and without that gloss
# there is nothing proving which treatment it is. Unmapped holds, never guesses.
SNK_SUFFIX_TREATMENT = {
    "": "base",
    "P": "aa",
    "SPC": "sp",
    "TR": "tr",
    "GSP": "gsp",
}
# GemRate's own wording for the same treatments (fingerprint.parallel).
GEMRATE_TREATMENT = {
    "": "base",
    "base": "base",
    "alternate art": "aa",
    "manga alternate art": "manga",
    "special alternate art": "sp",
    "wanted alternate art": "sp",
    "treasure rare": "tr",
    "gold": "gsp",
}
RARITY_TOKEN_RE = re.compile(r"^(?P<rarity>[A-Z]{1,4})(?:-(?P<suffix>[A-Z]{1,4}))?$")


def snk_treatment(master_name: str, localized: str) -> str:
    """Canonical treatment token SNKRDUNK claims, '' when it claims nothing
    this vocabulary can name."""

    text = f"{master_name} {localized}"
    head = master_name.split("[")[0].split("(")[0].strip()
    tokens = head.replace("　", " ").split()
    if not tokens:
        return ""
    match = RARITY_TOKEN_RE.match(tokens[-1])
    if not match:
        return ""
    suffix = (match.group("suffix") or "").upper()
    if suffix == "SP":
        # Only the spelled-out comic gloss makes an -SP readable.
        lowered = text.casefold()
        if "comic parallel" in lowered or "コミパラ" in text:
            return "manga"
        return ""
    return SNK_SUFFIX_TREATMENT.get(suffix, "")


def gemrate_treatment(parallel_words: str) -> str:
    return GEMRATE_TREATMENT.get(R._norm_text(parallel_words), "")


# Words that appear on nearly every One Piece listing and therefore prove
# nothing about WHICH product a card came from.
_PRODUCT_STOPWORDS = frozenset({
    "one", "piece", "japanese", "english", "version", "card", "cards", "the",
    "of", "in", "a", "an", "and", "for", "booster", "pack", "set", "edition",
    "collection", "deck", "vol", "no", "op", "st", "prb", "eb", "p",
    # "One Piece Japanese Promos" is GemRate's bucket for every promo ever
    # printed, so it names no product at all. Treating it as distinctive would
    # let any listing with the word "Promotional" in it look like a match.
    "promo", "promos", "promotional",
    # PriceCharting spells the same idea in its own words.
    "prices", "price", "psa", "graded", "ungraded", "loose", "new",
})


# GemRate stamps the grading year on the front of a set name ("2025 Carrying
# On His Will Alternate Art"). No provider prints it, so requiring it would
# reject every correct binding on a word that names no product.
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
# The same set code is written "OP13", "OP-13" and "OP 13" across sources.
# Join it back up before tokenising, or one spelling leaves a bare "13" behind
# that the other spelling can never match. Only the real One Piece prefixes are
# listed: any-two-letters-plus-two-digits also swallows "Fest 23-24", and a
# product word lost to the join is a product word that can never disagree.
_SPLIT_SET_CODE_RE = re.compile(r"\b(op|st|eb|prb)[\s\-]+(\d{2})\b")


def _product_tokens(text: str) -> set[str]:
    normalised = _SPLIT_SET_CODE_RE.sub(r"\1\2", R._norm_text(text))
    words = re.split(r"[^0-9a-z]+", normalised)
    return {
        word for word in words
        if word and word not in _PRODUCT_STOPWORDS
        and not _YEAR_RE.match(word)
        and not SET_CODE_RE.match(word.upper())
    }


def product_agrees(
    our_set_name: str, our_parallel: str, *listing_text: str,
) -> tuple[bool, str]:
    """Does the provider's listing name the same product our catalog does?

    Which of our fields carries the product depends on the row. A booster card
    names its product in set_name and its treatment in parallel ("Alternate
    Art"). A promo names nothing in set_name -- GemRate files every promo ever
    printed under one bucket -- and names the product in parallel instead
    ("Ichiban Kuji Purchase Bonus", "PSA Magazine Exclusive"). The
    GEMRATE_TREATMENT table already decides which of those two a parallel is:
    a wording it can name is a treatment and says nothing about the product,
    and a wording it cannot name is a product and has to be proved like one.

    listing_text is whatever the provider prints -- a SNKRDUNK master name and
    its Japanese localisation, or a PriceCharting page title and product URL.
    """

    ours = _product_tokens(our_set_name)
    if not gemrate_treatment(our_parallel):
        ours |= _product_tokens(our_parallel)
    if not ours:
        return False, "our_set_name_has_no_distinctive_token"
    theirs = _product_tokens(" ".join(listing_text))
    missing = sorted(
        token for token in ours
        if not any(
            token == other
            or (min(len(token), len(other)) >= 4
                and (token.startswith(other) or other.startswith(token)))
            for other in theirs
        )
    )
    if missing:
        return False, f"product_mismatch:missing={missing}"
    return True, ""
