"""Targeted PriceCharting and SNK identity reverify commands.

rebuild_036 configures the shared stage primitives and re-exports the commands.
The split keeps the nightly identity path reviewable without cloning policy.
"""

from __future__ import annotations

import argparse
import json
import re
import time
from html import unescape as html_unescape
from pathlib import Path
from typing import Any, Mapping

_REQUIRED_API = (
    "ROOT",
    "DAILY_CREDENTIALS_ENV",
    "EVIDENCE_TYPE_PROVIDER_PAGE",
    "NOT_A_REJECTION_VERDICT_SQL",
    "VARIANT_OPERATOR_RULING_SQL",
    "_PC_BRACKET_SYNONYMS",
    "_collector_core",
    "_fingerprint_variant_conflicts",
    "_norm_text",
    "_parallel_agrees",
    "_pc_bracket_printing_code",
    "_pc_number_set_explaining",
    "_pc_page_identity",
    "_pc_print_belongs_to_number_set",
    "_pc_rarity_only_parallel",
    "_snk_claim_number",
    "_snk_collector_claim",
    "_snk_language",
    "_snk_tcg",
    "_snk_treatment_mirror",
    "canonical_json",
    "connect",
    "operator_ruling",
    "red_listed_variants",
    "set_names_a_card_could_carry",
    "pc_product_candidate_sets",
    "sha256_bytes",
    "sha256_file",
    "snk_claim_set_agrees",
    "snk_has_collector_claim",
)


def configure(api: Mapping[str, Any]) -> None:
    """Bind stage primitives without importing the monolith back."""

    missing = [name for name in _REQUIRED_API if name not in api]
    if missing:
        raise RuntimeError(f"rebuild reverify missing API: {', '.join(missing)}")
    globals().update({name: api[name] for name in _REQUIRED_API})


_PC_BASE_PRINTINGS = frozenset({"", "base"})

# One Piece 3rd Anniversary metal prints. GemRate welds the product and the
# metal into one parallel label ("3rd Anniversary-Silver"); PriceCharting sells
# the same physical card as its own product and brackets only the metal
# ("[SP Silver]"). printing_code 'spc' is the catalog's own name for that
# print -- 3 rows database-wide (2026-08-24) -- and is not the coarse 'sp'
# code whose [SP Foil]/[SP Gold] exclusion above must stay exactly as it is.
_PC_SPC_METALS = frozenset({"gold", "silver"})


def _pc_spc_metal(row: Mapping[str, Any]) -> str:
    """The metal word an 'spc' catalog row claims for ITSELF, '' otherwise.

    Read off this row's own parallel_code and nothing else. That is the whole
    rule: v112 is "3rd Anniversary-Silver" and v36 is "3rd Anniversary-Gold",
    so each may only answer to its own metal or the twins take each other's
    page -- the same corroboration shape the dated-event rule uses, where the
    year has to be the variant's own.
    """

    if _norm_text(str(row.get("printing_code") or "")) != "spc":
        return ""
    tokens = set(re.split(r"[^a-z0-9]+", _norm_text(str(row.get("parallel_code") or ""))))
    metals = sorted(tokens & _PC_SPC_METALS)
    return metals[0] if len(metals) == 1 else ""


def _pc_spc_metal_parallel(row: Mapping[str, Any], *listing_text: str) -> str:
    """The words product agreement should read for an spc row, '' to use its own.

    product_agrees reads a parallel it cannot name as PRODUCT words, so
    "3rd Anniversary-Silver" asked PriceCharting to print "3rd" and
    "anniversary" beside a bracket that only ever says "[SP Silver]" -- v112
    and v36 were refused `product_mismatch:missing=['3rd', 'anniversary']`
    with their own product's page in hand.

    The brand words are dropped only where the listing itself corroborates the
    metal this row names, and what is handed back is PriceCharting's own
    bracket wording ("sp silver"), so BOTH words stay required. Spelling the
    metal alone would not survive product agreement: "gold" is a treatment
    GEMRATE_TREATMENT can name, so it would contribute no required word at all
    and a promo-bucket set name would be left with nothing to prove.

    A listing that does not print our metal gets the full label back and is
    refused exactly as before, which is what stops the Gold page answering for
    the Silver card at this gate as well as at the print signature.
    """

    metal = _pc_spc_metal(row)
    if not metal:
        return ""
    theirs = set(re.split(r"[^a-z0-9]+", _norm_text(" ".join(listing_text))))
    return f"sp {metal}" if metal in theirs else ""


def _pc_page_product_id(html: str) -> str:
    """The page's own numeric product id; empty when the page doesn't say.

    Redirect captures ("…_r.html") land on whatever product PC now serves, so
    the binding's external id must be re-proved from the page body itself."""

    for pattern in (
        r"VGPC\.product\s*=\s*\{[^{}]*?\bid\b\s*[:=]\s*(\d+)",
        r'data-product-id="(\d+)"',
    ):
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return ""


def pc_capture_for_product(
    pages_dir: Path, variant_id: int, product_id: str, mapped: Path | None = None
) -> Path | None:
    """The capture that IS this product's page, not whichever sorts first.

    A variant collects several files under `{variant}_*.html`: product pages,
    and the search-results pages a lane saved while looking for one. Picking
    sorted()[0] let "2026_search-products-q-one-piece-Shanks-001….html" win on
    the letter 'e' over "2026_shanks-magazine-op09-001_r.html", and on
    2026-08-10 five cards were held `page_parse_failed: canonical_not_product`
    with their own product page sitting in the same folder.

    Nothing here decides whether a binding is right -- the caller still demands
    page id == bound id == map id and the whole contract after it. This only
    stops an arbitrary file from answering a question it is not about."""

    candidates: list[Path] = []
    if mapped is not None and mapped.is_file():
        candidates.append(mapped)
    candidates.extend(
        path for path in sorted(pages_dir.glob(f"{variant_id}_*.html"))
        if path not in candidates
    )
    fallback: Path | None = None
    for path in candidates:
        html = path.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(html) != str(product_id):
            continue
        # A search-results page can carry a product id too, so agreeing on the
        # id is not enough: it has to parse as that product's own page.
        identity, _ = _pc_page_identity(html)
        if identity is not None:
            return path
        if fallback is None:
            fallback = path
    return fallback or (candidates[0] if candidates else None)


def _pc_bracket_names_our_product(page_parallel: str, row: Mapping[str, Any]) -> bool:
    """Does a PriceCharting bracket name the very product our catalog names?

    PriceCharting sells a promo, anniversary set or gift collection as its own
    product and writes the PRODUCT in the bracket ("[1st Anniversary]",
    "[Gift Collection]"), while the catalog carries that product in set_name
    and claims no treatment at all (printing_code "", parallel "base"). The
    bracket comparison above only ever reads a bracket as a TREATMENT, so nine
    such cards were held print_signature_mismatch on 2026-08-22 with their own
    product's page in hand.

    The bracket has to be EARNED out of a product name THIS row already
    carries, one direction only: every distinctive word the bracket says must
    already be in a name set_names_a_card_could_carry returned. That is what
    separates a card from its sibling product -- v2146 is "1st Anniversary
    Set" and v2126 is "Film Red", so neither can take the other's page.

    Promo-bucket set_names ("One Piece Japanese Promos") have no distinctive
    token, so our_product_text falls through to canonical_name. Official
    Event Top Prize is filed on PriceCharting as "[Flagship Battle]"; that
    synonym is an extra name on the leftover, not a read of every booster's
    canonical. Reading canonical_name as the bracket's covering text for a
    booster (v1424) would still hand it an anniversary page -- extras from
    set_names_a_card_could_carry do not add that.

    A bracket that is nothing but a set code ("[PRB01]") carries no
    distinctive word at all -- _product_tokens drops set codes, because every
    listing repeats them -- so it is answered by the codes this row itself
    names, never by the code its collector number prints. OP05-119 is printed
    on both the OP05 booster Luffy and the PRB01 reissue; only the PRB01 row
    says PRB01.
    """

    import op_identity_rules  # deferred: it imports this module at its top

    bracket = _norm_text(page_parallel)
    if not bracket:
        return False
    names = list(set_names_a_card_could_carry(row))
    if not names:
        names = [str(row.get("set_name") or "")]
    code = op_identity_rules.SET_CODE_RE.fullmatch(bracket.upper())
    for candidate_set in names:
        product_text = op_identity_rules.our_product_text(row, candidate_set)
        if not product_text:
            continue
        if code:
            ours = {
                str(row.get("v_set_code") or row.get("set_code") or "").upper(),
                *(
                    match.group(1)
                    for match in op_identity_rules.SET_CODE_RE.finditer(
                        product_text.upper()
                    )
                ),
            } - {""}
            if code.group(1) in ours:
                return True
            continue
        # Arguments deliberately reversed: product_agrees asks "does THEIR
        # listing say every distinctive word OURS does", and here the
        # bracket is the smaller claim that our own product words have to
        # cover. Promo-bucket set_names have no distinctive token;
        # our_product_text reads canonical. A leftover extra such as
        # Flagship Battle is a replacement product name, not an add-on.
        agrees, _why = op_identity_rules.product_agrees(
            page_parallel, "", product_text,
        )
        if not agrees:
            continue
        # Champion extra "Flagship Battle" must not cover leftover extra
        # "Flagship Battle Top 8": the shorter bracket is a subset.
        cand_n = _norm_text(candidate_set)
        if "flagship battle" in cand_n:
            if ("top 8" in cand_n) != ("top 8" in bracket):
                continue
        return True
    return False


def _printing_code_from_parallel(parallel: str) -> str:
    """printing_code the synonym table assigns this parallel_code, or "".

    GemRate often leaves printing_code blank and writes the treatment in
    parallel_code ("special alternate art", "manga alternate art"). The
    synonym table is keyed by printing_code, so a blank code never hit it.
    Reverse lookup does not change what a bracket means: the page still has
    to resolve to the SAME code via _pc_bracket_printing_code, which keeps
    bare [Manga] as mr and [SP Foil] unmatched.
    """

    wording = _norm_text(parallel)
    if not wording:
        return ""
    if wording in _PC_BRACKET_SYNONYMS:
        return wording
    for code, longforms in _PC_BRACKET_SYNONYMS.items():
        if wording in longforms:
            return code
    return ""


def _pc_unbracketed_sp_on_own_set(
    via_number_set: str,
    page_parallel: str,
    row: Mapping[str, Any],
) -> bool:
    """PriceCharting files some One Piece SPs unbracketed on the leftover's set.

    2167 JP Shirahoshi Special Alternate Art / Fist of Divine Speed pid
    9362267 (ungraded $64.48; sales 'SP ALTERNATE ART'). The EN twin is
    leftover-5 v1438/9362360. Memorial Collection unbracketed is the cheap
    base. SP is always a later set's reprint, so an unbracketed page already
    on the leftover's own set cannot be the number's base.

    Yamato is unbracketed on the NUMBER's original set, so via_number_set is
    non-empty and this returns False; number_set_own_print still refuses.
    _pc_print_signature_ok('', SP) stays False.
    """

    if via_number_set:
        return False
    if str(page_parallel or "").strip():
        return False
    if str(row.get("tcg_code") or "") != "one-piece":
        return False
    printing = _norm_text(
        str(row.get("printing_code") or row.get("v_printing_code") or "")
    )
    variant_parallel = str(
        row.get("parallel_code") or row.get("fp_parallel") or ""
    )
    derived = printing or _printing_code_from_parallel(variant_parallel)
    return derived == "sp"


def _pc_unbracketed_own_set_print(
    via_number_set: str,
    page_parallel: str,
    row: Mapping[str, Any],
) -> bool:
    # 2026-09-25: the Mega Dream EX texture-error carve-out is gone. It bound
    # v1904 (Incorrect Texture) to PC 11302596 on a 963 page census, but every
    # PSA10 sale on that page is the regular MAR (v339) at a ninth of the error
    # print's price. An unbracketed heading is PriceCharting's base print.
    return _pc_unbracketed_sp_on_own_set(via_number_set, page_parallel, row)


def _pc_print_signature_ok(page_parallel: str, row: Mapping[str, Any]) -> bool:
    """Phase-D parallel agreement plus the abbreviation vocabulary.

    printing_code is the stronger catalog evidence when present (mirrors
    _print_signature_agrees); the synonym table only ever maps a page bracket
    onto that code, never loosens the no-bracket path."""

    variant_parallel = str(
        row.get("parallel_code") or row.get("fp_parallel") or ""
    )
    if _parallel_agrees(page_parallel, variant_parallel):
        return True
    printing = _norm_text(
        str(row.get("printing_code") or row.get("v_printing_code") or "")
    )
    if not page_parallel:
        # A heading with no bracket is PriceCharting's base print. Letting it
        # satisfy a variant whose printing_code names a treatment is how seven
        # live bindings ended up on the cheap card: "Yamato OP01-121" (base)
        # was bound exact to OP05 Yamato Special Alternate Art, and while it
        # sat there the base card's own variant could not claim its page.
        # leftover-5 EN OP11 reprints also omit [SP]/[TR]; that exception is
        # leftover5_go.hold_exact_against_refresh, not a relaxation here.
        # Measured 2026-08-09 over all 928 exact PC bindings: 919 unaffected,
        # 7 refused, and all 7 were parallels bound to a base print.
        #
        # JP 1st-only sets (Pokekyun, Dream Shine): the whole set is 1st and
        # PriceCharting does not split 1st/Unlimited. WOTC Jungle twins keep
        # refusing because sole_1st_printing is false when an unlimited
        # sibling shares the number. Not a relaxation of the Yamato hole.
        # English is never sole-1st: every WOTC English 1st Edition card has
        # an Unlimited print on PriceCharting even when our catalog lacks the
        # sibling, and the unbracketed heading is that Unlimited print
        # (2026-09-26: nine Fossil/Rocket/Jungle 1st were bound to it).
        language = _norm_text(str(row.get("card_language") or ""))
        if row.get("sole_1st_printing") and language and language not in {"en", "english"} and (
            printing == "1st"
            or _norm_text(variant_parallel) in {"1st", "1st edition", "first edition"}
        ):
            return True
        # Pokemon event-prize promos: the catalog's only 288/SV-P is the
        # Victini BWR Event Prize, and PriceCharting files it unbracketed.
        # printing_code 'prize' is not a treatment (Yamato is SP); requiring
        # a [Prize] bracket held the leftover on its own page. One Piece
        # prize leftovers still need their product bracket (Flagship Battle).
        if (
            str(row.get("tcg_code") or "") == "pokemon"
            and printing == "prize"
            and "prize" in _norm_text(str(row.get("canonical_name") or ""))
        ):
            return True
        if printing not in _PC_BASE_PRINTINGS:
            return False
        name_n = _norm_text(str(row.get("canonical_name") or "")).replace(
            "reverse foil", "reverse holo"
        )
        vpp = _norm_text(variant_parallel)
        # Pokemon promo whose only PC product is unbracketed, but the card
        # IS the holo / reverse foil (Pretend Gyarados, Neo Premium File
        # Charizard). A treated printing_code still refuses (Yamato).
        if (
            str(row.get("tcg_code") or "") == "pokemon"
            and "holo" in name_n
        ):
            return True
        # Event name lives in the heading, not a bracket (20th Anniversary
        # Festa). The parallel must already be spelled in the canonical name
        # so a random unbracketed page cannot claim it. Treatments
        # (special alternate art, manga, SP) still refuse: that is the
        # Yamato hole that promoted leftover v2167 onto Shirahoshi base.
        import op_identity_rules
        if (
            vpp
            and len(vpp) >= 8
            and vpp in name_n
            and not op_identity_rules.names_a_treatment(vpp)
        ):
            return True
        return _pc_rarity_only_parallel(variant_parallel)
    bracket = _norm_text(page_parallel)
    # catalog_printing_identity is sometimes an empty row (measured 2026-08-27
    # leftover v1020/v1050) while catalog_variant.printing_code and the
    # rebuild fingerprint still carry mb / Master Ball Reverse Holo. The
    # judge SQL already selects those as v_printing_code / fp_parallel.
    derived = printing or _printing_code_from_parallel(variant_parallel)
    page_code = _pc_bracket_printing_code(page_parallel)
    if not printing:
        if derived and page_code and derived == page_code:
            return True
    # PriceCharting files One Piece manga AA as "[Manga]". Catalog stores
    # printing_code manga / parallel "manga alternate art". The synonym table
    # still maps "[Manga]" to mr so number_set_own_print treats the booster
    # [Manga] as the number's own print (PRB reprints cannot steal it).
    # Alternate Art still refuses [Manga]: derived is aa. OP catalog has
    # zero `mr` rows (measured 2026-08-28).
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and page_code == "mr"
        and derived == "manga"
    ):
        return True
    # PRB reprint of an alt-art: PC brackets "[Alternate Art PRB01]". Catalog
    # parallel is still "alternate art". The rest of the bracket is the
    # product, answered by _pc_bracket_names_our_product so the OP05 booster
    # AA cannot take the PRB01 page.
    # ONE PIECE Chopper's 1 comic-bundle promo: PC brackets [Comic].
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and bracket == "comic"
        and "chopper" in _norm_text(str(row.get("canonical_name") or ""))
        and "chopper" in _norm_text(variant_parallel + " " + str(row.get("canonical_name") or ""))
    ):
        return True
    # Official Event Top Prize is PriceCharting's [Flagship Battle] (champion
    # stamp). Official Event Prize (no Top) is [Flagship Battle Top 8].
    # Asia is a third cut and stays out.
    prize_blob = _norm_text(
        str(row.get("canonical_name") or "") + " " + variant_parallel
    )
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and "flagship battle" in bracket
        and "top 8" not in bracket
        and "top prize" in prize_blob
        and "asia" not in prize_blob
    ):
        return True
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and "flagship battle" in bracket
        and "top 8" in bracket
        and "official event" in prize_blob
        and "prize" in prize_blob
        and "top prize" not in prize_blob
        and "asia" not in prize_blob
    ):
        return True
    # PriceCharting files One Piece Special Alternate Art as "[SP Foil]".
    # [SP Gold] is a different PC product and stays refused. A coarse
    # printing_code 'sp' whose parallel is not the spelled-out treatment
    # (sr-spc) still cannot say which of those two the card is.
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and bracket == "sp foil"
        and (
            "special alternate art" in _norm_text(variant_parallel)
            or "sp alternate art" in _norm_text(variant_parallel)
        )
    ):
        return True
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and derived == "aa"
        and bracket.startswith("alternate art ")
    ):
        rest = bracket[len("alternate art "):].strip()
        if rest and _pc_bracket_names_our_product(rest, row):
            return True
    if printing and bracket:
        if bracket == printing or bracket == f"{printing} edition":
            return True
        if bracket in _PC_BRACKET_SYNONYMS.get(printing, frozenset()):
            return True
        # Compound codes ("aa-errata"): collapse the long-forms inside the
        # bracket to their codes, then still demand exact equality.
        collapsed = bracket
        for code, longforms in _PC_BRACKET_SYNONYMS.items():
            for longform in longforms:
                collapsed = collapsed.replace(longform, code)
        collapsed = " ".join(collapsed.split())
        if collapsed == printing:
            return True
        # Compound codes are written "aa-errata" in the catalog and "Alternate
        # Art Errata" on the page: same tokens, different separator. Equality
        # is still demanded, only the separator is normalised.
        if collapsed.replace("-", " ") == printing.replace("-", " "):
            return True
    # Dated event promos: PC brackets the event year ("[Battle Festa 2015]")
    # while PSA/GemRate's parallel label omits it ("Battle Festa"), so the
    # catalog cannot carry it either. The year is not noise to strip -- the
    # 2014/2015 Battle Festa Pikachu twins differ ONLY by collector number and
    # year -- so it must CORROBORATE: accept exactly "<parallel> <year>" where
    # <year> is the variant's own leading canonical year. The other twin's
    # year differs, so this page keeps refusing it (proven both directions in
    # scripts/test_pc_print_signature_dated_event.py).
    vpp = _norm_text(variant_parallel)
    year = re.match(r"(19|20)\d{2}\b", str(row.get("canonical_name") or ""))
    if vpp and year and bracket == f"{vpp} {year.group(0)}":
        return True
    # SM-P 061 is Battle Festa 2017. GemRate filed the same card as
    # "Pokemon Card Festa". Year must corroborate so 2014/2015 twins
    # cannot take each other's page.
    name_festa = _norm_text(str(row.get("canonical_name") or ""))
    if (
        str(row.get("tcg_code") or "") == "pokemon"
        and "festa" in bracket
        and "festa" in name_festa
    ):
        b_year = re.search(r"(?:19|20)\d{2}", bracket)
        c_year = re.search(r"(?:19|20)\d{2}", name_festa)
        if b_year and c_year and b_year.group(0) == c_year.group(0):
            return True
    # Yu Nagaba campaigns: GemRate stored parallel_code as standard/base while
    # both the canonical name and the PC bracket say Nagaba. Corroborate the
    # same word on both sides; a Nagaba page cannot claim a non-Nagaba card.
    if (
        str(row.get("tcg_code") or "") == "pokemon"
        and "nagaba" in _norm_text(str(row.get("canonical_name") or ""))
        and "nagaba" in bracket
    ):
        return True
    # Master Ball seal: PC brackets "[Master Ball]" while GemRate writes the
    # finish into the canonical name. Same corroboration as Nagaba — the
    # reverse-holo sibling page "[Reverse Holo]" has no "master ball" in the
    # bracket, so it stays refused.
    if (
        str(row.get("tcg_code") or "") == "pokemon"
        and "master ball" in _norm_text(str(row.get("canonical_name") or ""))
        and "master ball" in bracket
    ):
        return True
    # Wanted AA: GemRate writes "Wanted Alternate Art" (sometimes under
    # printing_code sp); PC brackets [Wanted] / [Wanted Poster] /
    # [Wanted Poster Foil]. The word must be on BOTH sides, so a manga AA
    # leftover cannot take the Wanted page.
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and "wanted" in _norm_text(
            str(row.get("canonical_name") or "") + " " + variant_parallel
        )
        and "wanted" in bracket
    ):
        return True
    # JP Start Deck 100 files reverse holo as "[Mirror Holo]". Catalog
    # parallel is "reverse holo". Master Ball / monster ball stay out:
    # their names also contain those words and have their own brackets.
    # Multi-word brackets that the catalog already spells (Mega Tokyo's,
    # Tea Party, Official Event Top Prize vs [Event Top Prize]). Short
    # treatment codes stay out: "sp" is a substring of "special".
    catalog_blob = _norm_text(
        str(row.get("canonical_name") or "") + " " + variant_parallel
    )
    if (
        " " in bracket
        and bracket in catalog_blob
        and printing in _PC_BASE_PRINTINGS | frozenset({"promo", "prize"})
        and bracket not in {"reverse holo", "sp foil", "sp gold"}
        and not (
            "master ball" in catalog_blob and "master ball" not in bracket
        )
    ):
        set_n = _norm_text(str(row.get("set_name") or ""))
        # v1424: an OP05 booster whose canonical mentions 1st Anniversary.
        # The real anniversary leftover names that product in set_name or
        # in parallel_code (v2045); canonical alone must not earn the page.
        if not (
            bracket == "1st anniversary"
            and "1st anniversary" not in set_n
            and "1st anniversary" not in _norm_text(variant_parallel)
        ):
            return True
    catalog_finish = _norm_text(
        variant_parallel + " " + str(row.get("canonical_name") or "")
    ).replace("reverse foil", "reverse holo")
    if (
        str(row.get("tcg_code") or "") == "pokemon"
        and "reverse holo" in catalog_finish
        and "master ball" not in catalog_finish
        and "monster ball" not in catalog_finish
        and bracket == "mirror holo"
    ):
        return True
    # Holo / reverse-holo finish written into the canonical name while the
    # catalog printing stays base (McDonald's Charmander-Holo, 25th Reverse
    # Foil). Same corroboration as Nagaba: the word must be on BOTH sides, so
    # a non-holo card cannot take the holo page and reverse cannot take plain
    # holo. Foil and holo are the same finish word on these JP promo names.
    if (
        str(row.get("tcg_code") or "") == "pokemon"
        and printing in _PC_BASE_PRINTINGS
    ):
        name_n = _norm_text(str(row.get("canonical_name") or "")).replace(
            "reverse foil", "reverse holo"
        )
        if "holo" in name_n and "holo" in bracket:
            name_rev = "reverse" in name_n
            page_rev = "reverse" in bracket
            if name_rev == page_rev:
                return True
    # 3rd Anniversary metal prints, corroborated the same way: PC brackets
    # "[SP Silver]" for a card the catalog labels "3rd Anniversary-Silver"
    # with printing_code 'spc'. The metal is not noise to strip -- Silver and
    # Gold are two products of one card -- so it must be the metal THIS row's
    # own parallel names, and the Gold page keeps refusing the Silver card
    # (proven both directions in scripts/test_pc_spc_metal_bracket.py). The
    # coarse 'sp' code is untouched: [SP Foil] and [SP Gold] stay refused
    # there, because that catalog code cannot say which product it is.
    spc_metal = _pc_spc_metal(row)
    if spc_metal and bracket == f"sp {spc_metal}":
        return True
    # The bracket is our own PRODUCT, not a treatment. Written as one and-chain
    # so it commutes with the dated-event rule above: neither can answer for a
    # bracket the other accepts. Every conjunct is load-bearing --
    # _pc_bracket_printing_code refuses any wording that names a treatment
    # ("[Wanted]", "[Manga]", "[SP]"), _pc_bracket_names_our_product makes the
    # bracket earn itself out of set_name, and the recursive call re-asks the
    # bracket-less question the catalog can already answer, so a card whose
    # printing_code claims a treatment is refused here exactly as it is there.
    # The printing_code conjunct is spelled out rather than left to the
    # recursive call: _parallel_agrees collapses "" and "base" into each other,
    # so a row reading printing_code='sp' with parallel_code='base' would
    # satisfy the bracket-less path on the parallel alone -- the Yamato hole,
    # reached through a bracket this time. The catalog has to claim NO
    # treatment for a product bracket to be readable as the product.
    printing_for_product = _norm_text(str(row.get("printing_code") or ""))
    # "promo" is GemRate's bucket for a product stamp, not a treatment.
    # The unbracketed recursive call would refuse it (not in _PC_BASE_PRINTINGS)
    # which is correct for a booster base page; the product BRACKET is what
    # proves the promo. sp/aa/manga stay out.
    if (
        str(row.get("tcg_code") or "") == "one-piece"
        and not _pc_bracket_printing_code(page_parallel)
        and (
            printing_for_product in _PC_BASE_PRINTINGS
            or printing_for_product == "promo"
        )
        and _pc_bracket_names_our_product(page_parallel, row)
        and (
            printing_for_product == "promo"
            or _pc_print_signature_ok("", row)
        )
    ):
        return True
    return False


def _pc_map_url_by_product() -> dict[str, str]:
    """product id -> PriceCharting URL, from every private source map on disk.

    The map rows were built out of page HTML without unescaping, so a console
    slug arrives as "pokemon-scarlet-&amp;-violet-151" while the real URL
    carries a literal ampersand -- the same entity leak the heading parser
    had. Fetching the escaped form gets a 404, the page is recorded missing,
    and the card drops out of product_ready for a reason that has nothing to
    do with the card."""

    urls: dict[str, str] = {}
    for path in sorted(ROOT.glob("data/runtime/private-source-map/*.jsonl")):
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            product_id = str(row.get("pc_product_id") or "")
            raw = row.get("pc_url")
            if product_id and raw and product_id not in urls:
                urls[product_id] = html_unescape(str(raw))
    return urls


def _pc_fetch_missing_pages(conn, pages_dir: Path, map_html_by_variant: dict[int, str]) -> dict[str, Any]:
    """Capture the pages pc-identity-reverify would hold as missing or unparseable.

    Run by hand on 2026-08-10 for 25 bindings; 20 came back and stopped being
    held. The command holds a binding when the page is not on disk, which is a
    statement about the folder rather than about the binding, so the fetch
    belongs next to the check that needs it. Same capture contract as
    pc_identity_discover: fetch, prove the page's own product id is the one
    asked for, keep it or delete it."""

    import pricecharting_cf_session as cf_session

    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT si.external_entity_id AS pid, si.variant_id"
            "  FROM catalog_source_identity si"
            " WHERE si.source_code='pricecharting'"
            "   AND si.match_status IN ('manual_review','rejected')"
            f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
        )
        candidates = cursor.fetchall()

    urls = _pc_map_url_by_product()
    counts = {"considered": len(candidates), "alreadyGood": 0, "noUrl": 0,
              "fetched": 0, "fetchFailed": 0, "pageSaysOther": 0}
    for row in candidates:
        pid = str(row["pid"])
        variant_id = int(row["variant_id"])
        mapped = map_html_by_variant.get(variant_id, "")
        existing = pc_capture_for_product(
            pages_dir, variant_id, pid, (ROOT / mapped) if mapped else None
        )
        if existing is not None:
            body = existing.read_text(encoding="utf-8", errors="replace")
            if _pc_page_product_id(body) == pid and _pc_page_identity(body)[0] is not None:
                counts["alreadyGood"] += 1
                continue
        url = urls.get(pid)
        if not url:
            counts["noUrl"] += 1
            continue
        out = pages_dir / f"{variant_id}_{pid}.html"
        code = cf_session.cmd_fetch(url, out, timeout_s=90)
        if code != 0 or not out.is_file():
            counts["fetchFailed"] += 1
            continue
        body = out.read_text(encoding="utf-8", errors="replace")
        if _pc_page_product_id(body) != pid:
            out.unlink(missing_ok=True)
            counts["pageSaysOther"] += 1
            continue
        counts["fetched"] += 1
        time.sleep(2.0)
    print(json.dumps({"phase": "pc-fetch-missing", "counts": counts}, ensure_ascii=False), flush=True)
    return counts


def _stamp_sole_1st_printing(conn: Any, bindings: list[Mapping[str, Any]]) -> None:
    """Mark 1st-edition rows that have no unlimited/base sibling on the same number.

    JP sets that exist only as 1st (Pokekyun, Dream Shine) get an unbracketed
    PriceCharting heading. WOTC Jungle 1st still has an Unlimited sibling, so
    this stays false and the unbracketed page keeps refusing the 1st card.
    """

    for row in bindings:
        if isinstance(row, dict):
            row["sole_1st_printing"] = False
    def _is_1st_row(row: Mapping[str, Any]) -> bool:
        if _norm_text(str(row.get("printing_code") or row.get("v_printing_code") or "")) == "1st":
            return True
        return _norm_text(str(row.get("parallel_code") or row.get("fp_parallel") or "")) in {
            "1st", "1st edition", "first edition",
        }

    firsts = [row for row in bindings if isinstance(row, dict) and _is_1st_row(row)]
    if not firsts:
        return
    ids = [int(row["variant_id"]) for row in firsts]
    placeholders = ",".join(["%s"] * len(ids))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT a.id AS vid, a.collector_number AS our_num,"
            " a.set_name AS our_set, a.set_code AS our_code,"
            " b.collector_number AS sib_num, b.set_name AS sib_set,"
            " b.set_code AS sib_code, b.printing_code AS sib_print"
            " FROM catalog_variant a"
            " JOIN catalog_variant b ON b.id<>a.id"
            "  AND b.tcg_code=a.tcg_code AND b.card_language=a.card_language"
            f" WHERE a.id IN ({placeholders})",
            tuple(ids),
        )
        sibs = cursor.fetchall()
    has_other: set[int] = set()
    for sib in sibs:
        if _collector_core(str(sib["sib_num"] or "")) != _collector_core(
            str(sib["our_num"] or "")
        ):
            continue
        our_code = str(sib["our_code"] or "").strip().upper()
        sib_code = str(sib["sib_code"] or "").strip().upper()
        same_set = (our_code and sib_code and our_code == sib_code) or (
            _norm_text(str(sib["our_set"] or "")) == _norm_text(str(sib["sib_set"] or ""))
        )
        if not same_set:
            continue
        if _norm_text(str(sib["sib_print"] or "")) != "1st":
            has_other.add(int(sib["vid"]))
    for row in firsts:
        row["sole_1st_printing"] = int(row["variant_id"]) not in has_other


def cmd_pc_identity_reverify(args: argparse.Namespace) -> int:
    """Re-verify manual_review PC bindings against freshly captured pages.

    S6 Phase D re-derived bindings from the replay archive; sweeps since then
    capture newer pages under data/private/pricecharting_session/html/full900.
    This command applies the same fail-closed contract to those captures and
    promotes a binding to exact ONLY when the page itself proves it:
    page product id == bound external id == map product id, no hard identity
    conflicts, and an agreeing print signature. Everything else keeps its
    status and is listed in the report for a human ruling. Never touches
    exact or conflict rows, and never overturns a rejection anybody reasoned
    about.

    It DOES reconsider a rejection nobody reasoned about. psa_identity_repair
    rejects every non-gemrate binding on a card whose gemrate identity would not
    resolve, without examining those bindings; skipping the whole status meant
    those cards could never come back even after the gemrate side was repaired.
    REJECTION_VERDICT_ACTIONS draws the line, and the page still has to prove the
    binding on its own -- reconsidering costs a card nothing, because the
    fail-closed contract below is the same one every other row faces."""

    from datetime import datetime, timezone

    # Imported here, not at module scope: op_identity_rules imports this
    # module for its text normaliser, so a top-level import would be circular.
    import leftover5_go
    import op_identity_rules

    credentials = args.credentials_env or DAILY_CREDENTIALS_ENV
    pages_dir = args.pages_dir or (
        ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
    )
    if not pages_dir.is_dir():
        raise SystemExit(f"pc-identity-reverify ABORT: pages dir missing: {pages_dir}")

    map_product_by_variant: dict[int, str] = {}
    map_html_by_variant: dict[int, str] = {}
    map_path = args.map or (
        ROOT / "data" / "runtime" / "private-source-map" / "c11_pc_ebay_map_full900.jsonl"
    )
    if map_path.is_file():
        for line in map_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            variant_id = int(entry.get("variant_id") or 0)
            if variant_id:
                map_product_by_variant[variant_id] = str(entry.get("pc_product_id") or "")
                map_html_by_variant[variant_id] = str(
                    entry.get("html_path") or entry.get("htmlPath") or ""
                )

    conn = connect(credentials)
    if getattr(args, "fetch_missing", False):
        _pc_fetch_missing_pages(conn, pages_dir, map_html_by_variant)
    counts = {
        "reviewBindings": 0, "quarantineReconsidered": 0,
        "pageMissing": 0, "pageParseFailures": 0,
        "pageProductMismatch": 0, "mapProductMismatch": 0, "hardConflicts": 0,
        "productMismatch": 0, "printSignatureMismatch": 0, "promoted": 0,
        "operatorRuled": 0, "redListed": 0,
    }
    promoted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cursor:
            binding_sql = (
                "SELECT si.external_entity_id AS pid, si.variant_id,"
                " si.match_status, si.bind_evidence_json,"
                + VARIANT_OPERATOR_RULING_SQL +
                # GemRate's own wording for the printing. For a booster card it
                # names the treatment; for a promo it names the product, and
                # the product-agreement rule below needs to tell them apart.
                " (SELECT JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,"
                "         '$.fingerprint.parallel'))"
                "    FROM catalog_rebuild_member rm WHERE rm.variant_id=v.id"
                "   ORDER BY rm.computed_at DESC LIMIT 1) AS fp_parallel,"
                " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
                " v.canonical_name,"
                " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
                " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
                " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
                " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
                " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
                " FROM catalog_source_identity si"
                " JOIN catalog_variant v ON v.id=si.variant_id"
                " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
                " WHERE si.source_code='pricecharting'"
                "   AND si.match_status IN ('manual_review','rejected')"
                f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
            )
            binding_params: list[Any] = []
            variant_ids = getattr(args, "variant_ids", None)
            if variant_ids is not None:
                scoped = sorted({int(variant_id) for variant_id in variant_ids})
                if not scoped:
                    bindings = []
                else:
                    binding_sql += (
                        f" AND si.variant_id IN ({','.join(['%s'] * len(scoped))})"
                    )
                    binding_params.extend(scoped)
                    cursor.execute(binding_sql, tuple(binding_params))
                    bindings = cursor.fetchall()
            else:
                cursor.execute(binding_sql, tuple(binding_params))
                bindings = cursor.fetchall()
        _stamp_sole_1st_printing(conn, bindings)

        updates: list[tuple[str, str, str, dict[str, Any], tuple[str, ...], Path]] = []
        still_red = set(red_listed_variants())
        for row in bindings:
            counts["reviewBindings"] += 1
            if str(row["match_status"]) == "rejected":
                counts["quarantineReconsidered"] += 1
            pid = str(row["pid"])
            variant_id = int(row["variant_id"])

            def hold(reason: str, detail: str = "") -> None:
                held.append({
                    "variant_id": variant_id, "pid": pid,
                    "reason": reason, "detail": detail,
                })

            if variant_id in still_red:
                counts["redListed"] += 1
                hold("red_listed", "034 audit sheet red row: a human refused this card")
                continue
            ruling = operator_ruling(row.get("bind_evidence_json")) or str(
                row.get("ruled_elsewhere") or ""
            )
            pin_holds = leftover5_go.hold_exact_against_refresh(
                "pricecharting", variant_id, pid,
            )
            if ruling and not pin_holds:
                counts["operatorRuled"] += 1
                hold("operator_ruled", ruling[:160])
                continue
            map_product = map_product_by_variant.get(variant_id, "")
            if map_product and map_product != pid:
                counts["mapProductMismatch"] += 1
                hold("map_product_mismatch", f"map={map_product}")
                continue
            mapped_html = map_html_by_variant.get(variant_id, "")
            html_path = pc_capture_for_product(
                pages_dir, variant_id, pid,
                (ROOT / mapped_html) if mapped_html else None,
            )
            if html_path is None:
                counts["pageMissing"] += 1
                hold("page_missing")
                continue
            html = html_path.read_text(encoding="utf-8", errors="replace")
            page_product = _pc_page_product_id(html)
            if page_product != pid:
                counts["pageProductMismatch"] += 1
                hold("page_product_mismatch", f"page={page_product or '?'}")
                continue
            identity, reason = _pc_page_identity(html)
            if identity is None:
                counts["pageParseFailures"] += 1
                hold("page_parse_failed", reason)
                continue
            pseudo_fp = {
                "cardNumber": identity["collector"],
                "derivedLanguage": identity["language"],
                "setName": identity["setText"],
            }
            conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
            # PriceCharting files a One Piece reprint under the set its NUMBER
            # names, so the page's set text disagrees with the catalog's
            # set_name by construction. Let the number's set answer for that
            # one `set:` conflict: the product check below still has to agree
            # on the same name, and a page reached this way that is the
            # number's set's own print is refused before it does.
            via_number_set = _pc_number_set_explaining(pseudo_fp, row, conflicts)
            if via_number_set:
                conflicts = []
            if identity["tcg"] and str(row["tcg_code"] or "") and \
                    identity["tcg"] != str(row["tcg_code"]):
                conflicts.append(f"tcg:{identity['tcg']}!={row['tcg_code']}")
            heading_n = _norm_text(identity.get("heading") or "")
            name_n = _norm_text(str(row.get("canonical_name") or ""))
            if "gengar" in name_n and "gengar" not in heading_n:
                conflicts.append("character:gengar")
            if conflicts:
                counts["hardConflicts"] += 1
                hold("hard_conflict", ";".join(conflicts))
                continue
            # A One Piece card keeps its ORIGINAL number when reprinted, so
            # the conflict check above passing on an agreeing collector number
            # is not proof of the same product: a search for "Boa Hancock 038"
            # resolved the PSA Magazine promo onto the OP07 booster Alternate
            # Art, and every check up to here agreed. The rule is applied only
            # where its vocabulary was derived.
            if str(row["tcg_code"] or "") == "one-piece":
                if _pc_print_belongs_to_number_set(
                    via_number_set, identity["parallel"], row,
                ):
                    counts["productMismatch"] += 1
                    hold(
                        "product_mismatch",
                        "product_mismatch:number_set_own_print:"
                        f"[{identity['parallel']}]:{via_number_set}",
                    )
                    continue
                same_product, why = False, "product_mismatch:no_set_name"
                listing_text = (
                    identity["setText"], identity["canonicalUrl"], identity["heading"],
                )
                our_parallel = (
                    _pc_spc_metal_parallel(row, *listing_text)
                    or str(row["fp_parallel"] or "")
                )
                if _pc_bracket_names_our_product(identity["parallel"], row):
                    same_product, why = True, ""
                else:
                    for candidate_set in pc_product_candidate_sets(
                        row, via_number_set,
                    ):
                        same_product, why = op_identity_rules.product_agrees(
                            op_identity_rules.our_product_text(row, candidate_set),
                            our_parallel, *listing_text,
                        )
                        if same_product:
                            break
                if not same_product:
                    counts["productMismatch"] += 1
                    hold("product_mismatch", why)
                    continue
            if not _pc_print_signature_ok(identity["parallel"], row):
                if not _pc_unbracketed_own_set_print(
                    via_number_set, identity["parallel"], row,
                ):
                    counts["printSignatureMismatch"] += 1
                    hold(
                        "print_signature_mismatch",
                        f"page=[{identity['parallel']}] printing={row['printing_code']}"
                        f" parallel={row['parallel_code']}",
                    )
                    continue
            digest = sha256_file(html_path)
            evidence = {
                "providerClaims": {
                    "tcgCode": identity["tcg"],
                    "cardLanguage": identity["language"],
                    "collectorNumber": identity["collector"],
                    "setCode": "",
                    "printingCode": "",
                    "parallelCode": identity["parallel"],
                },
                "evidence": {
                    "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                    "sha256": digest,
                    "path": html_path.relative_to(ROOT).as_posix(),
                    "canonicalUrl": identity["canonicalUrl"],
                    "pageHeading": identity["heading"],
                    "capturedAt": datetime.fromtimestamp(
                        html_path.stat().st_mtime, tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "generation": "pc_reverify_full900",
                },
            }
            evidence_sha = sha256_bytes(canonical_json(evidence))
            if row["canonical_printing_sha256"]:
                mirror = (
                    str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                    str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                    str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                    str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
                )
            else:
                mirror = (
                    str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                    str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                    str(row["v_printing_code"] or ""), "", "", "",
                )
            updates.append(
                (pid, evidence_sha, identity["collector"],
                 {"evidence": evidence}, mirror, html_path)
            )
            promoted.append({
                "variant_id": variant_id, "pid": pid,
                "bracket": identity["parallel"],
                "printing_code": str(row["printing_code"] or ""),
                "evidence_sha256": evidence_sha,
            })

        if args.write and updates:
            try:
                with conn.cursor() as cursor:
                    for pid, evidence_sha, collector, extra, mirror, html_path in updates:
                        evidence = extra["evidence"]
                        cursor.execute(
                            "INSERT INTO catalog_provider_capture_receipt (source_code,"
                            " external_entity_id, capture_sha256, capture_path,"
                            " captured_at, generation_id, parser_version)"
                            " VALUES ('pricecharting', %s, %s, %s, %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " capture_sha256=VALUES(capture_sha256),"
                            " capture_path=VALUES(capture_path),"
                            " captured_at=VALUES(captured_at),"
                            " generation_id=VALUES(generation_id),"
                            " parser_version=VALUES(parser_version)",
                            (
                                pid, evidence["evidence"]["sha256"],
                                evidence["evidence"]["path"][:500],
                                datetime.strptime(
                                    evidence["evidence"]["capturedAt"],
                                    "%Y-%m-%dT%H:%M:%SZ",
                                ).replace(tzinfo=timezone.utc),
                                "pc_reverify_full900", "pc_reverify_v1",
                            ),
                        )
                        cursor.execute(
                            "UPDATE catalog_source_identity SET match_status='exact',"
                            " evidence_sha256=%s, source_product_number=%s,"
                            " bind_evidence_json=%s, bound_tcg_code=%s,"
                            " bound_card_language=%s, bound_set_code=%s,"
                            " bound_collector_number=%s, bound_printing_code=%s,"
                            " bound_parallel_code=%s, bound_edition_code=%s,"
                            " bound_finish_code=%s"
                            " WHERE source_code='pricecharting'"
                            " AND external_entity_id=%s"
                            # Re-checked against the stored row, so a status that
                            # moved since the read above changes zero rows rather
                            # than overwriting whatever it moved to. 'exact' and
                            # 'conflict' are excluded by naming the two statuses
                            # this command is allowed to promote.
                            " AND match_status IN ('manual_review','rejected')"
                            f" AND {NOT_A_REJECTION_VERDICT_SQL}",
                            (
                                evidence_sha, str(collector or "")[:64],
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                                mirror[0][:32], mirror[1][:8], mirror[2][:24],
                                mirror[3][:96], mirror[4][:24], mirror[5][:64],
                                mirror[6][:191], mirror[7][:64], pid,
                            ),
                        )
                conn.commit()
                counts["promoted"] = len(updates)
            except Exception:
                conn.rollback()
                raise
        elif updates:
            counts["promoted"] = 0

        report = {
            "pcIdentityReverify": True,
            "write": bool(args.write),
            "pagesDir": pages_dir.relative_to(ROOT).as_posix()
            if pages_dir.is_relative_to(ROOT) else str(pages_dir),
            "counts": counts,
            "promotable": len(updates),
            "promotedSample": promoted[:40],
            "held": held,
        }
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"pc-identity-reverify-{stamp}.json"
        )
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


def cmd_snk_identity_reverify(args: argparse.Namespace) -> int:
    """Re-verify held SNK bindings against freshly fetched masters.

    Same acceptance contract as S7 (stage_snk_refresh): the provider master
    itself must resolve a 1-card PSA10 variant, assert no hard identity
    conflict, and agree on the print signature. Promotions write the same
    receipt + bind evidence shape S7 writes. Everything held is listed with a
    reason for a human ruling.

    Like cmd_pc_identity_reverify, this reconsiders a rejection nobody
    reasoned about. This lane used to read manual_review only, which is what
    it was born with -- REJECTION_VERDICT_ACTIONS arrived later and the PC
    lane learned it while this one did not. The consequence was measurable:
    82 snkrdunk rows sat at match_status='rejected' with their own
    bind_evidence_json still reading action='confirm', collateral from
    psa_identity_repair's blanket non-gemrate reject, and no lane in the repo
    could ever look at them again. Real verdicts and the 034 red list are
    still excluded by NOT_A_REJECTION_VERDICT_SQL, and reconsidering costs a
    card nothing because the fail-closed contract below is the same one every
    other row faces."""

    from datetime import datetime, timezone

    import snk_market_data

    credentials = args.credentials_env or DAILY_CREDENTIALS_ENV
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base_dir = ROOT / "data" / "runtime" / "rebuild-036" / "snk-identity-reverify" / stamp
    items_dir = base_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    parser_version = "snkmd_" + sha256_file(
        ROOT / "pipelines" / "snk_market_data.py"
    )[:12]

    conn = connect(credentials)
    counts = {
        "reviewBindings": 0, "quarantineReconsidered": 0,
        "pageMissing": 0, "noPsa10OneCard": 0,
        "hardConflicts": 0, "parallelSoftMismatch": 0, "promoted": 0,
        "operatorRuled": 0, "redListed": 0,
    }
    promoted: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    try:
        with conn.cursor() as cursor:
            binding_sql = (
                "SELECT si.external_entity_id AS iid, si.variant_id,"
                " si.match_status, si.bind_evidence_json,"
                + VARIANT_OPERATOR_RULING_SQL +
                " v.tcg_code, v.card_language, v.set_name, v.collector_number,"
                " v.canonical_name,"
                " v.set_code AS v_set_code, v.printing_code AS v_printing_code,"
                " p.parallel_code, p.printing_code, p.canonical_printing_sha256,"
                " p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,"
                " p.set_code AS p_set_code, p.collector_number AS p_collector_number,"
                " p.edition_code AS p_edition_code, p.finish_code AS p_finish_code"
                " FROM catalog_source_identity si"
                " JOIN catalog_variant v ON v.id=si.variant_id"
                " LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id"
                " WHERE si.source_code='snkrdunk'"
                "   AND si.match_status IN ('manual_review','rejected')"
                f"   AND {NOT_A_REJECTION_VERDICT_SQL}"
            )
            # The same scoping the PC lane already has. Without it, ruling on
            # one SNKRDUNK card re-harvests every held binding in the table --
            # the worklist below is built from whatever this query returns --
            # so a three-row supersede would go fetch the provider for all of
            # them. Shape copied from cmd_pc_identity_reverify on purpose: an
            # empty --variant-id list selects nothing rather than everything.
            binding_params: list[Any] = []
            variant_ids = getattr(args, "variant_ids", None)
            if variant_ids is not None:
                scoped = sorted({int(variant_id) for variant_id in variant_ids})
                if not scoped:
                    bindings = []
                else:
                    binding_sql += (
                        f" AND si.variant_id IN ({','.join(['%s'] * len(scoped))})"
                    )
                    binding_params.extend(scoped)
                    cursor.execute(binding_sql, tuple(binding_params))
                    bindings = cursor.fetchall()
            else:
                cursor.execute(binding_sql, tuple(binding_params))
                bindings = cursor.fetchall()

        worklist = sorted({
            int(row["iid"]) for row in bindings if str(row["iid"]).isdigit()
        })
        harvest_path = base_dir / "snk_reverify_harvest.jsonl"
        snk_market_data.run(
            worklist, harvest_path, delay=0.0,
            condition_code=snk_market_data.PSA10_CONDITION,
            run_id=f"snk_reverify_{stamp}", workers=8,
        )
        if not harvest_path.is_file():
            partial = harvest_path.with_suffix(harvest_path.suffix + ".partial")
            if not partial.is_file():
                raise SystemExit(f"snk reverify harvest produced no file: {harvest_path}")
            harvest_path = partial
        rows_by_id: dict[int, dict[str, Any]] = {}
        with harvest_path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                item_id = payload.get("item_id")
                if isinstance(item_id, int):
                    rows_by_id[item_id] = payload

        updates: list[tuple[str, int, str, str, dict[str, Any], tuple[str, ...]]] = []
        still_red = set(red_listed_variants())
        for row in bindings:
            counts["reviewBindings"] += 1
            if str(row["match_status"]) == "rejected":
                counts["quarantineReconsidered"] += 1
            iid_str = str(row["iid"])
            variant_id = int(row["variant_id"])

            def hold(reason: str, detail: str = "") -> None:
                held.append({
                    "variant_id": variant_id, "iid": iid_str,
                    "reason": reason, "detail": detail,
                })

            if variant_id in still_red:
                counts["redListed"] += 1
                hold("red_listed", "034 audit sheet red row: a human refused this card")
                continue
            ruling = operator_ruling(row.get("bind_evidence_json")) or str(
                row.get("ruled_elsewhere") or ""
            )
            if ruling:
                counts["operatorRuled"] += 1
                hold("operator_ruled", ruling[:160])
                continue
            if not iid_str.isdigit():
                counts["pageMissing"] += 1
                hold("non_numeric_id")
                continue
            payload = rows_by_id.get(int(iid_str))
            if payload is None or payload.get("error"):
                counts["pageMissing"] += 1
                hold("page_missing", str((payload or {}).get("error") or ""))
                continue
            if not payload.get("quantity_variant_id"):
                counts["noPsa10OneCard"] += 1
                hold("no_psa10_1card_variant")
                continue
            master = (payload.get("source_payload") or {}).get("master") or {}
            master_name = str(master.get("name") or "")
            localized = str(master.get("localizedName") or "")
            product_number = str(payload.get("product_number") or "").strip()
            language = _snk_language(master_name, localized)
            tcg = _snk_tcg(master_name, localized)
            claim = _snk_collector_claim(master_name, localized, product_number)
            pseudo_fp = {
                "cardNumber": _snk_claim_number(claim),
                "derivedLanguage": language,
                "setName": f"{master_name} {localized}",
            }
            conflicts = _fingerprint_variant_conflicts(pseudo_fp, row)
            # Same supersede rule as S7: the bracket designation's own set
            # claim beats the noisy title-vs-set-name token comparison.
            if snk_claim_set_agrees(claim, row, master_name, localized):
                conflicts = [
                    c for c in conflicts
                    if not c.startswith("set:") and not c.startswith("set_code:")
                ]
            if tcg and str(row["tcg_code"] or "") and tcg != str(row["tcg_code"]):
                conflicts.append(f"tcg:{tcg}!={row['tcg_code']}")
            if not snk_has_collector_claim(claim, master_name, localized, row):
                conflicts.append("product_number_missing")
            snk_mirror = _snk_treatment_mirror(master_name, localized)
            variant_blob = " ".join((
                str(row["parallel_code"] or ""),
                str(row["printing_code"] or ""),
                str(row["v_printing_code"] or ""),
            ))
            variant_mirror = "ミラー" in variant_blob or bool(
                re.search(r"(?i)mirror", variant_blob)
            )
            if snk_mirror != variant_mirror:
                conflicts.append(f"parallel:mirror {snk_mirror}!={variant_mirror}")
            if conflicts:
                counts["hardConflicts"] += 1
                hold("hard_conflict", ";".join(conflicts))
                continue
            snk_parallel = "parallel" if (
                "パラレル" in master_name or "parallel" in localized.casefold()
            ) else ""
            variant_parallel = str(row["parallel_code"] or "")
            parallel_ok = _parallel_agrees(snk_parallel, variant_parallel)
            if not parallel_ok and not snk_parallel:
                parallel_ok = _pc_rarity_only_parallel(variant_parallel)
            if not parallel_ok:
                counts["parallelSoftMismatch"] += 1
                hold(
                    "parallel_soft_mismatch",
                    f"snk=[{snk_parallel}] parallel={variant_parallel}",
                )
                continue

            item_id = int(iid_str)
            evidence_doc = {
                "itemId": item_id,
                "master": master,
                "conditionFilter": payload.get("condition_filter"),
                "quantityVariantId": payload.get("quantity_variant_id"),
                "productNumber": payload.get("product_number"),
                "imageUrl": payload.get("image_url"),
                "fetchedAt": payload.get("fetched_at"),
            }
            blob = canonical_json(evidence_doc)
            item_path = items_dir / f"{item_id}.json"
            item_path.write_bytes(blob)
            digest = sha256_bytes(blob)
            rel = item_path.relative_to(ROOT).as_posix()
            fetched = str(payload.get("fetched_at") or "")
            evidence = {
                "providerClaims": {
                    "tcgCode": tcg,
                    "cardLanguage": language,
                    "collectorNumber": claim,
                    "setCode": "",
                    "printingCode": "",
                    "parallelCode": snk_parallel,
                },
                "evidence": {
                    "type": EVIDENCE_TYPE_PROVIDER_PAGE,
                    "sha256": digest,
                    "path": rel,
                    "canonicalUrl": f"https://snkrdunk.com/en/trading-cards/{item_id}",
                    "capturedAt": fetched,
                    "generation": "snk_reverify",
                },
            }
            evidence_sha = sha256_bytes(canonical_json(evidence))
            if row["canonical_printing_sha256"]:
                mirror = (
                    str(row["p_tcg_code"] or ""), str(row["p_card_language"] or ""),
                    str(row["p_set_code"] or ""), str(row["p_collector_number"] or ""),
                    str(row["printing_code"] or ""), str(row["parallel_code"] or ""),
                    str(row["p_edition_code"] or ""), str(row["p_finish_code"] or ""),
                )
            else:
                mirror = (
                    str(row["tcg_code"] or ""), str(row["card_language"] or ""),
                    str(row["v_set_code"] or ""), str(row["collector_number"] or ""),
                    str(row["v_printing_code"] or ""), "", "", "",
                )
            updates.append(
                (iid_str, variant_id, evidence_sha, claim,
                 {"evidence": evidence, "fetched": fetched}, mirror)
            )
            promoted.append({
                "variant_id": variant_id, "iid": iid_str,
                "claim": claim, "evidence_sha256": evidence_sha,
            })

        if args.write and updates:
            try:
                with conn.cursor() as cursor:
                    for iid, variant_id, evidence_sha, claim, extra, mirror in updates:
                        evidence = extra["evidence"]
                        fetched = extra["fetched"]
                        captured_at = datetime.strptime(
                            fetched, "%Y-%m-%dT%H:%M:%S%z"
                        ) if fetched else datetime.now(timezone.utc)
                        cursor.execute(
                            "INSERT INTO catalog_provider_capture_receipt (source_code,"
                            " external_entity_id, capture_sha256, capture_path,"
                            " captured_at, generation_id, parser_version)"
                            " VALUES ('snkrdunk', %s, %s, %s, %s, %s, %s)"
                            " ON DUPLICATE KEY UPDATE"
                            " capture_sha256=VALUES(capture_sha256),"
                            " capture_path=VALUES(capture_path),"
                            " captured_at=VALUES(captured_at),"
                            " generation_id=VALUES(generation_id),"
                            " parser_version=VALUES(parser_version)",
                            (
                                iid, evidence["evidence"]["sha256"],
                                evidence["evidence"]["path"][:500],
                                captured_at, "snk_reverify", parser_version[:64],
                            ),
                        )
                        cursor.execute(
                            "UPDATE catalog_source_identity SET match_status='exact',"
                            " evidence_sha256=%s, source_product_number=%s,"
                            " bind_evidence_json=%s, bound_tcg_code=%s,"
                            " bound_card_language=%s, bound_set_code=%s,"
                            " bound_collector_number=%s, bound_printing_code=%s,"
                            " bound_parallel_code=%s, bound_edition_code=%s,"
                            " bound_finish_code=%s"
                            " WHERE source_code='snkrdunk'"
                            " AND external_entity_id=%s AND variant_id=%s"
                            # Re-checked against the stored row so a status that
                            # moved since the read above changes zero rows rather
                            # than overwriting whatever it moved to.
                            " AND match_status IN ('manual_review','rejected')"
                            f" AND {NOT_A_REJECTION_VERDICT_SQL}",
                            (
                                evidence_sha, str(claim or "")[:64],
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                                mirror[0][:32], mirror[1][:8], mirror[2][:24],
                                mirror[3][:96], mirror[4][:24], mirror[5][:64],
                                mirror[6][:191], mirror[7][:64], iid, variant_id,
                            ),
                        )
                conn.commit()
                counts["promoted"] = len(updates)
            except Exception:
                conn.rollback()
                raise

        report = {
            "snkIdentityReverify": True,
            "write": bool(args.write),
            "harvest": harvest_path.relative_to(ROOT).as_posix(),
            "counts": counts,
            "promotable": len(updates),
            "promotedSample": promoted[:40],
            "held": held,
        }
        artifact_path = ROOT / "data" / "runtime" / "rebuild-036" / (
            f"snk-identity-reverify-{stamp}.json"
        )
        artifact_path.write_bytes(canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()



