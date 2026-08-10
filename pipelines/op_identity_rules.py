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

import json
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


# Japanese pokemon rarities that carry their OWN collector number: an SAR, AR,
# SR, UR, HR or IR print is numbered above the set size, so the number already
# says which print this is and no provider has to repeat the words. Demanding
# them as product evidence refused cards on their rarity -- "Special Art Rare"
# asked a Japanese storefront to print "special", "art" and "rare".
#
# Finishes are deliberately absent. "Master Ball Reverse Holo", "Reverse Holo",
# "Holo", "Reverse Foil" and "1st Edition" share a number with the base print,
# so the number cannot tell them apart and the words are the only evidence
# there is. They stay required.
_NUMBERED_RARITY_WORDINGS = frozenset({
    "special illustration rare", "illustration rare", "special art rare",
    "art rare", "ultra rare", "super rare", "hyper rare", "secret",
    "mega ultra rare", "mega hyper rare", "mega attack rare",
    "black white rare", "shiny rare", "shiny super rare", "character rare",
    "trainer rare", "amazing rare", "radiant rare",
})


def names_a_treatment(parallel_words: str) -> bool:
    """Is this parallel field a treatment rather than a product name?

    Broader than gemrate_treatment(), and only for deciding whether the words
    are product evidence. gemrate_treatment() answers a second, narrower
    question -- can this treatment be COMPARED against the provider's own
    vocabulary -- and a wording can be a treatment we recognise without being
    one SNKRDUNK's One Piece rarity suffixes can speak about.
    """

    return bool(gemrate_treatment(parallel_words)) or (
        R._norm_text(parallel_words) in _NUMBERED_RARITY_WORDINGS
    )


# Words that appear on nearly every One Piece listing and therefore prove
# nothing about WHICH product a card came from.
_PRODUCT_STOPWORDS = frozenset({
    # The game's own name. "one" and "piece" were here from the start; "pokemon"
    # was not, because these rules were written for One Piece and then asked to
    # serve a second game. GemRate opens every pokemon set_name with it
    # ("2022 Pokemon Japanese Sword & Shield Vstar Universe") and SNKRDUNK's
    # Japanese product titles do not repeat it, so it refused cards on a word
    # that names no product. Which game a listing belongs to is proved
    # separately and hard: judge() raises tcg:<theirs>!=<ours> as a conflict
    # before product agreement is ever consulted.
    "pokemon",
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
    if not names_a_treatment(our_parallel):
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


# A One Piece character's given name is the last word both sources print:
# "Monkey D. Luffy" and "Portgas D Ace" put the family name and the initial in
# front, and every provider keeps that order. The initial is one letter, so
# requiring two drops it without a special case.
_NAME_WORD_RE = re.compile(r"[a-z]{2,}")


def character_agrees(our_name: str, *listing_text: str) -> tuple[bool, str]:
    """Is the provider's listing even the same CHARACTER as our card?

    Nothing else in the rules asks. Set code, number, language and treatment
    can all agree while the card is somebody else entirely: OP03-112 came back
    as "Charlotte Cracker" for a catalog row that reads "Charlotte Pudding",
    and every other check passed it. Two Charlottes share a family name, so the
    comparison has to be the given name -- the last word -- not any word.

    A disagreement is a rejection; an unreadable listing is not. When a master
    name carries no Latin word at all there is nothing to compare and this must
    not invent a verdict, so it returns to the caller exactly as it would have
    before this check existed. Only a name that IS printed and IS different
    stops a binding.
    """

    ours = _NAME_WORD_RE.findall(R._norm_text(our_name))
    if not ours:
        return True, ""
    given = ours[-1]
    theirs = set(_NAME_WORD_RE.findall(R._norm_text(" ".join(listing_text))))
    if not theirs:
        return True, ""
    # Romanisation drifts in the tail ("Kaidou"/"Kaido"), never in the stem.
    if any(
        given == other
        or (min(len(given), len(other)) >= 4
            and (given.startswith(other) or other.startswith(given)))
        for other in theirs
    ):
        return True, ""
    return False, f"character_mismatch:ours={given}"


# --------------------------------------------------------------------------
# printed set codes
# --------------------------------------------------------------------------
_PRINTED_CODES: dict[int, str] | None = None
_SOLD_IN_CODES: dict[int, str] | None = None
_PRODUCT_NAMES: dict[str, str] | None = None
PRINTED_CODE_POLICY = R.ROOT / "data" / "policy" / "op-printed-codes.json"


def _policy_codes() -> dict[str, dict]:
    """The `codes` tier of the printed-code policy. Raises if the file is gone.

    All three readers below used to guard with `if PRINTED_CODE_POLICY.is_file()`
    and leave their map empty otherwise. A missing policy file then looked
    identical to "no card in this run has a proven printed code": every gate
    that consults these went quiet and the binder fell back to comparing
    GemRate against GemRate. Absent evidence and absent file are not the same
    answer, so only one of them is returned as data.
    """

    if not PRINTED_CODE_POLICY.is_file():
        raise RuntimeError(
            f"printed-code policy missing: {PRINTED_CODE_POLICY}. "
            "Rebuild it before binding; an empty map silently disables the set-code gate."
        )
    payload = json.loads(PRINTED_CODE_POLICY.read_text(encoding="utf-8"))
    return payload.get("codes") or {}


def printed_set_code(variant_id: int | None) -> str:
    """The set code this One Piece card actually PRINTS, or "".

    GemRate names the product a card was sold in; the card prints the code of
    the set it first appeared in. Measured 2026-08-10: of the 121 One Piece
    cards with no price source, 88 print a code different from the product
    GemRate filed them under -- an alternate-art Kaido from the OP05 booster
    prints OP04-044 -- and both discovery lanes were throwing away the correct
    provider listing with `hard_conflict:set_code` because of it.

    This is an INPUT correction, not a relaxed gate. The lanes still have to
    agree on character, number, language, treatment, tcg and mirror. All this
    changes is which code counts as "ours", and it changes it only where a
    Limitless product page -- the page for the product GemRate itself named --
    lists that number under that code. The file's `advisory` block, filled by
    scanning every promo product for a number and a name, is deliberately NOT
    read here: for a promo an agreeing set code makes the binder skip the
    product-name comparison, which is the only check a promo has left.

    Empty string, never a guess: a card this cannot prove is left exactly as
    the catalog has it.
    """

    global _PRINTED_CODES
    if _PRINTED_CODES is None:
        _PRINTED_CODES = {}
        for key, record in _policy_codes().items():
            code = str((record or {}).get("printedCode") or "")
            prefix = code.rpartition("-")[0]
            if prefix:
                _PRINTED_CODES[int(key)] = prefix.upper()
    if variant_id is None:
        return ""
    return _PRINTED_CODES.get(int(variant_id), "")


def sold_in_set_code(variant_id: int | None) -> str:
    """The set code of the product Limitless lists this card IN, or "".

    The other half of printed_set_code. A reprint answers to two codes and the
    catalog only ever carries one of them: 48 of these cards had a blank
    set_code and printed_set_code filled it, but 21 already carried the printed
    code and were failing the other way -- GemRate said `op10`, the catalog said
    `op08`, and adding the printed code again changed nothing (measured
    2026-08-10 against variants 19, 1214, 1228).

    Not the same as trusting the catalog's set_name back. This code is the
    Limitless product page the resolver FOUND the card on, by name or by sole
    number -- a source independent of GemRate. Reading the catalog's own
    set_name instead would compare GemRate against GemRate and the set check
    would stop refusing anything.
    """

    global _SOLD_IN_CODES
    if _SOLD_IN_CODES is None:
        _SOLD_IN_CODES = {}
        for key, record in _policy_codes().items():
            product = str((record or {}).get("product") or "")
            match = SET_CODE_RE.match(product.upper())
            if match:
                _SOLD_IN_CODES[int(key)] = match.group(1).upper()
    if variant_id is None:
        return ""
    return _SOLD_IN_CODES.get(int(variant_id), "")


def limitless_product_name(code: str) -> str:
    """The product name Limitless gives a One Piece set code, or "".

    `set_name_by_code` builds its map out of catalog rows -- set_code from one
    row, set_name from the same row -- and on a reprint those two columns
    describe different products. Measured 2026-08-10: it answered OP02 with
    "One Piece Two Legends" (that is OP08) off variant 1427, and ST01 with
    "One Piece Awakening of the New Era" (that is OP05) off variant 19. So the
    map both LOSES the name the product check needed and OFFERS a name from an
    unrelated set, which is a way to accept the wrong page, not just refuse the
    right one.

    The slugs in the policy file are the fix: `op02-paramount-war` is one
    string carrying both halves, written by Limitless, and a slug says nothing
    about any particular card. Read from the `codes` tier only, same as
    printed_set_code -- the `advisory` tier is promo guesswork and must not
    reach a gate by any route, including through a name.
    """

    global _PRODUCT_NAMES
    if _PRODUCT_NAMES is None:
        _PRODUCT_NAMES = {}
        for record in _policy_codes().values():
            slug = str((record or {}).get("product") or "")
            match = re.match(r"^([a-z]{2,4}\d{2})-(.+)$", slug)
            if match:
                _PRODUCT_NAMES[match.group(1).upper()] = match.group(2).replace("-", " ")
    return _PRODUCT_NAMES.get(str(code or "").upper(), "")
