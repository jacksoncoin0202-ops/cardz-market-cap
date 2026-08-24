"""Shared identity predicates for the 036 rebuild and daily reverify lanes.

The monolithic orchestrator configures the small set of stage primitives below,
then re-exports this module's public symbols. Keeping the predicates here makes
daily consumers import one coherent rule surface without duplicating policy.
"""

from __future__ import annotations

import json
import re
import sys
from typing import Any, Mapping

_REQUIRED_API = (
    "ROOT",
    "REJECTION_VERDICT_ACTIONS",
    "REJECTION_RED_LIST_KEY",
    "_NOISE_TOKENS",
    "EVIDENCE_TYPE_GEMRATE",
    "_norm_text",
    "_fingerprint_variant_conflicts",
    "_set_signals",
    "canonical_json",
    "sha256_bytes",
)

_OPTIONAL_API = ("_PC_BRACKET_SYNONYMS",)


def configure(api: Mapping[str, Any]) -> None:
    """Bind primitives owned by rebuild_036 without creating an import cycle."""

    missing = [name for name in _REQUIRED_API if name not in api]
    if missing:
        raise RuntimeError(f"rebuild identity rules missing API: {', '.join(missing)}")
    globals().update({name: api[name] for name in _REQUIRED_API})
    globals().update({name: api[name] for name in _OPTIONAL_API if name in api})


# A human ruling on the ROW, under whatever action its writer chose.
# operator-zero-20260814 wrote nine PriceCharting rows and three SNKRDUNK rows
# "live same-number already on board; GemRate identity kept; PC/SNK exact
# rejected so card cannot become product_ready" under action 'accept' -- the
# HOLD is what was accepted. Nothing read it: both reverify lanes select by
# rejection verdict only, and on 2026-08-22 v1203 (one of the nine) passed
# every evidence check and was one --write away from exact. A reason that
# names its operator is a ruling; a reverify lane holds the row and says so.
OPERATOR_RULING_REASON_PREFIX = "operator-"


def operator_ruling(bind_evidence_json: Any) -> str:
    """The operator's ruling recorded on a binding row, or ""."""

    evidence: Any = bind_evidence_json
    if isinstance(evidence, (str, bytes)):
        try:
            evidence = json.loads(evidence)
        except ValueError:
            return ""
    if not isinstance(evidence, Mapping):
        return ""
    reason = str(evidence.get("reason") or "")
    return reason if reason.startswith(OPERATOR_RULING_REASON_PREFIX) else ""


# The ruling is about the CARD and was written on whichever of its rows existed
# that day: v1203 carries it on its SNKRDUNK row only, and the PriceCharting row
# pc-identity-discover wrote for it on 2026-08-22 knows nothing of it. Select
# the newest operator ruling on any OTHER row of the same variant alongside the
# row's own. '%%' because the PC lane executes with parameters (pymysql
# formats the query) and the SNK lane without; LIKE reads both as a wildcard.
VARIANT_OPERATOR_RULING_SQL = (
    " (SELECT CONCAT(o.source_code, ': ',"
    "         JSON_UNQUOTE(JSON_EXTRACT(o.bind_evidence_json, '$.reason')))"
    "    FROM catalog_source_identity o WHERE o.variant_id=si.variant_id"
    "     AND NOT (o.source_code=si.source_code"
    "              AND o.external_entity_id=si.external_entity_id)"
    "     AND JSON_UNQUOTE(JSON_EXTRACT(o.bind_evidence_json, '$.reason'))"
    f"         LIKE '{OPERATOR_RULING_REASON_PREFIX}%%'"
    "   ORDER BY o.updated_at DESC LIMIT 1) AS ruled_elsewhere,"
)


NUMBER_SET_CODE_RE = re.compile(r"^([A-Z]{2,4}\d{2})-")


# A catalog-majority map from set code to set name used to live here, and
# `set_names_a_card_could_carry` took it as a second source for the name a
# printed code carries. It could not be one. catalog_variant.set_name asserts
# which product a particular card was PULLED FROM; no count over such rows
# converts that into what a set code is NAMED, and the docstring's claim that
# the majority made it safe did not survive the data: of 156 (tcg, language,
# code) keys, 80 were decided by three rows or fewer and 12 had a top-two tie
# at one row each, so the winner was whatever the execution plan emitted.
#
# Measured 2026-08-11 on the live catalog: of the 24 one-piece/en codes, the 9
# that `limitless_product_name` does not cover -- EB01, EB02, ST01, ST10, ST13,
# ST14, ST16, ST18, ST21 -- fell through to this map, and all 9 answered with
# ANOTHER set's name (ST01 -> 'Awakening of the New Era', which is OP05;
# ST13 -> OP12; ST14 -> OP10; EB01/ST16/ST18 -> OP11's 'A Fist of Divine
# Speed'; EB02 -> 'One Piece Promos'). Not one was right. That name is handed
# to `product_agrees`, the only check separating a reprint from a different
# product once the number agrees, so it made an OP11 page acceptable for an
# ST18 card. A code with no proved product name now simply yields no extra
# name -- the honest state.
def set_names_a_card_could_carry(row: Mapping[str, Any]) -> list[str]:
    """The catalog's set name, plus the one this card's NUMBER names.

    One Piece reprints a card into a later product without renumbering it, so
    "OP09-Emperors in the New World Nami ... 106" and OP08-106 are the same
    card described from two ends. PriceCharting files it under the number's
    set, and a product check that only knows the catalog's set_name refuses its
    page for saying "Two Legends" where we said "Emperors".

    Two ways to learn the number's set, and the second exists because the first
    only works when the code is written INTO the number. Measured 2026-08-10:
    of the 121 One Piece cards with no price source, all but a handful carry a
    bare "044" and NUMBER_SET_CODE_RE matches none of them, so this returned
    one name and the reprint page was never read. `printed_set_code` reads the
    code off the Limitless page for the product GemRate itself named, which is
    where the bare number's prefix was recorded in the first place.

    Widening where we look, not what we accept: every name returned here is
    still put to `product_agrees`, and the number, character and print
    signature still have to match on whichever page answers.
    """

    names = [str(row.get("set_name") or "")]
    match = NUMBER_SET_CODE_RE.match(str(row.get("collector_number") or "").upper())
    codes = [match.group(1)] if match else []
    import op_identity_rules  # deferred: it imports this module at its top

    for extra in (
        op_identity_rules.printed_set_code(row.get("variant_id")),
        op_identity_rules.sold_in_set_code(row.get("variant_id")),
    ):
        if extra and extra not in codes:
            codes.append(extra)
    for code in codes:
        alt = op_identity_rules.limitless_product_name(code)
        if alt and alt not in names:
            names.append(alt)
    return [name for name in names if name]


def _pc_number_set_explaining(
    fp: Mapping[str, Any], row: Mapping[str, Any], conflicts: list[str],
) -> str:
    """The set this card's NUMBER names, when that is what the page is filed under.

    PriceCharting files a One Piece reprint under the set its number names
    (see set_names_a_card_could_carry), so the page's set text disagrees with
    the catalog's set_name by construction and _fingerprint_variant_conflicts
    raises `set:` for it -- ahead of the product check that was widened to
    read that very page (e487c360), which therefore never ran. Measured
    2026-08-22 over the 272 PC manual_review rows: 131 hard_conflict holds,
    19 of them SP/TR reprints and promos whose page agreed on number,
    character, language and bracket.

    Only a `set:` token conflict is ever explained, and only by re-running the
    same conflict check with a name the card's number names in place of the
    catalog's: language, collector number, set-code and tcg conflicts stay
    conflicts, and a second name that raises any conflict of its own explains
    nothing. Scoped to One Piece, where the vocabulary was derived. Returns the
    explaining name so the caller can refuse a page that is the number's set's
    own print -- see _pc_print_belongs_to_number_set -- or "" when nothing is
    explained.
    """

    if str(row.get("tcg_code") or "") != "one-piece":
        return ""
    if not conflicts or not all(c.startswith("set:") for c in conflicts):
        return ""
    for alt in set_names_a_card_could_carry(row)[1:]:
        if not _fingerprint_variant_conflicts(fp, {**dict(row), "set_name": alt}):
            return alt
    return ""


# Print treatments that exist only as a LATER set's reprint of an older
# number: One Piece's SP ("Special") cards and Treasure Rares are always
# reissues of a card from an earlier set, so a "[SP] OP01-047" page can only
# be the reprint's. Base, Alternate Art and Manga are printed by the number's
# own set and a page carrying them is that set's card.
_PC_REPRINT_ONLY_PRINTINGS = frozenset({"sp", "tr"})


def _pc_bracket_printing_code(page_parallel: str) -> str:
    """The catalog printing code a PriceCharting bracket names, or "".

    Read off the same vocabulary _pc_print_signature_ok accepts: a code's
    long forms first ("[Manga]" is the manga RARE, not the manga parallel --
    see the synonym table), then a bracket that is the code itself ("[SP]").
    A bracket neither can name ("[Bandai Card Games Fest]", "[Best
    Selection]", "[1st Anniversary]") names a product, not a treatment.
    """

    bracket = _norm_text(page_parallel)
    if not bracket:
        return ""
    for code, longforms in _PC_BRACKET_SYNONYMS.items():
        if bracket in longforms:
            return code
    if bracket in _PC_BRACKET_SYNONYMS:
        return bracket
    return ""


def _pc_print_belongs_to_number_set(via_number_set: str, page_parallel: str) -> bool:
    """Does a page reached only through the card's NUMBER belong to the
    number's own set -- that is, to the card whose catalog set that is?

    A reprint keeps its number, so a promo, collection or Premium Booster
    card reaches the number's set page through _pc_number_set_explaining and
    agrees there on number, character and language. What separates it from
    that set's own cards is the bracket:

    - none: the set's base print. Nine such proposals on 2026-08-22 (v1876,
      1915, 1962, 1974, 2034, 2077, 2146, 2153, 2172 onto booster base
      pages) while PriceCharting sells each as its own bracketed product,
      and owner ruling operator-zero-20260814 on three of them says they
      must not become product_ready;
    - a treatment the set printed itself (Alternate Art, Manga): v2054, the
      PRB01 reissue of the OP05 Luffy alt-art, would otherwise have taken
      the OP05 booster's own "[Alternate Art] OP05-119" page (pid 8506784)
      from v267, the card that page is;
    - SP or Treasure Rare: only ever a later set's reprint, so the page is
      the reprint's -- 15 of the 19 bindings newly provable on 2026-08-22;
    - a product name ("[Bandai Card Games Fest]"): the promo's, and
      product_agrees has already held it to the catalog's own words.

    The same predicate runs in the discover lane so the proposal is never
    written in the first place.
    """

    if not via_number_set:
        return False
    if not str(page_parallel or "").strip():
        return True
    code = _pc_bracket_printing_code(page_parallel)
    return bool(code) and code not in _PC_REPRINT_ONLY_PRINTINGS


def red_listed_variants() -> list[int]:
    """The cards a human read on the 034 audit sheet and refused, and nobody
    has since identified: the sheet's thirteen minus the 036 release.

    Derived, never copied: the sheet is the authority, and a correction to it
    has to reach every lane without anybody remembering which lanes exist. It
    lives here rather than in one lane because the failure it prevents is a
    NEW lane -- one that never heard of the list -- and a new lane imports this
    module before it imports any other.

    Fail-closed on purpose: this raises rather than returning a short list. On
    2026-08-09 the PriceCharting discovery lane proposed three red cards
    because it could not see the ruling, prices and sales went live for two of
    them, and validator034's red13 invariant was what noticed -- one gate later
    than it should have been. AGENTS.md rule 11.

    The 036 release (data/editorial/red-sheet-036-release.json, written by
    adjudicate_operator_knowledge_20260813) is the later ruling: an operator
    identified the card and bound it, so it is no longer refused.
    psa_identity_repair already subtracts it; the discovery lanes did not,
    and kept refusing to look for identities on cards the owner had released.
    The reverify lanes hold by this list too, because the row-level stamp
    (REJECTION_RED_LIST_KEY) is what the 2026-08-20 replay wiped when it
    rewrote bindings with no evidence at all.
    """

    sys.path.insert(0, str(ROOT / "scripts"))
    import stamp_red_sheet_quarantine as RED

    return RED.active_red_variant_ids()


def rejection_is_verdict(bind_evidence_json: Any) -> bool:
    """Did something actually rule against this binding, or is it collateral?

    Unreadable evidence counts as a verdict. A row whose reasoning cannot be
    parsed is not thereby proved harmless, and the cost of the two mistakes is
    not symmetric: honouring a stale quarantine leaves a card off the front end
    until someone re-runs discovery, while overturning a real rejection binds a
    card to the wrong product and prices it wrong.
    """

    if bind_evidence_json is None:
        return False
    claim = bind_evidence_json
    if isinstance(claim, (str, bytes)):
        try:
            claim = json.loads(claim)
        except (ValueError, TypeError):
            return True
    if not isinstance(claim, Mapping):
        return True
    if str(claim.get("action") or "") in REJECTION_VERDICT_ACTIONS:
        return True
    return bool(claim.get(REJECTION_RED_LIST_KEY))


def _gemrate_printing_sha(fields: Mapping[str, str]) -> str:
    """Same 10-field recipe as resolve_active_psa_identity.printing_sha (§3.5).

    Kept in lockstep by value, not import, so this module stays standalone."""

    identity = {
        key: str(fields.get(key) or "").strip().casefold()
        for key in (
            "tcg_code", "card_language", "set_name", "set_code", "collector_number",
            "printing_code", "rarity_code", "edition_code", "parallel_code", "finish_code",
        )
    }
    return sha256_bytes(canonical_json(identity))


def _derive_print_fields(fp: Mapping[str, Any]) -> tuple[dict[str, str] | None, str]:
    """Provider-native printing tuple for minting a new variant (D7).

    Fails closed: any underivable piece returns (None, reason) so the member
    stays identity_pending instead of minting a guessed identity."""

    from g10_public_snapshot import normalize_collector

    set_name = str(fp.get("setName") or "")
    lowered = set_name.casefold()
    if lowered.startswith("one piece"):
        tcg = "one-piece"
    elif lowered.startswith("pokemon"):
        tcg = "pokemon"
    else:
        return None, "tcg_underivable"
    language = str(fp.get("derivedLanguage") or "")
    if language == "zh":
        # Catalog vocabulary needs zhCN/zhTW; GemRate set names only say Chinese.
        return None, "language_ambiguous_zh"
    if not str(fp.get("description") or ""):
        return None, "description_missing"
    collector = normalize_collector(fp.get("cardNumber"))
    if not collector.display or collector.display == "Unknown":
        return None, "collector_unknown"
    return {
        "tcg_code": tcg,
        "card_language": language,
        "set_name": set_name,
        "set_code": "",
        "collector_number": collector.display,
        "printing_code": "",
        "rarity_code": "",
        "edition_code": "",
        "parallel_code": _norm_text(fp.get("parallel") or ""),
        "finish_code": "",
    }, ""


def _tcg_from_set(set_name: Any) -> str:
    lowered = str(set_name or "").casefold()
    if lowered.startswith("one piece"):
        return "one-piece"
    if lowered.startswith("pokemon"):
        return "pokemon"
    return ""  # honest unknown; column default is '' too


def _sig_tokens(value: str) -> set[str]:
    # Single-character tokens ("Monkey D. Luffy" → "d", possessive "s") carry
    # no discriminating power and punish spelling drift; drop them.
    return {
        token for token in re.split(r"[^a-z0-9]+", _norm_text(value))
        if len(token) > 1 and token not in _NOISE_TOKENS
    }


def _token_covered(small: set[str], large: set[str]) -> bool:
    # Prefix-tolerant coverage, same tolerance as the set-signal comparison.
    return all(
        any(
            a == b or (min(len(a), len(b)) >= 4 and (a.startswith(b) or b.startswith(a)))
            for b in large
        )
        for a in small
    )


def _name_agrees(fp_name: str, variant_name: str) -> bool:
    """Does the provider's card name appear in the variant's canonical name?

    Set + collector number + parallel do NOT pin a card: one-piece numbers
    several distinct alt-arts 118, so adoption must never place a Luffy on a
    Rayleigh variant. Empty either side: no signal, leave to other checks.
    """

    f_tokens = _sig_tokens(fp_name)
    v_tokens = _sig_tokens(variant_name)
    if not f_tokens or not v_tokens:
        return True
    return _token_covered(f_tokens, v_tokens)


_PRINT_PHRASES = (
    "red manga alternate art", "manga alternate art", "special alternate art",
    "wanted alternate art", "alternate art", "full art", "reverse foil",
    "reverse holo", "master ball", "1st edition", "sec",
)


def _print_phrases(text: str) -> frozenset[str]:
    """Print-treatment wording present in a name/parallel blob.

    GemRate's parallel field often says just "Base" while the treatment lives
    in the name ("Full Art/Pikachu-Reverse Foil"), and catalog rows may carry
    it only in canonical_name ("… Manga Alternate Art 061" with an empty
    parallel_code). Comparing extracted phrase sets keeps a Base fingerprint
    from adopting a phrase-marked variant and vice versa. Token-bounded so
    "sec" never matches inside "second".
    """

    blob = " " + " ".join(
        t for t in re.split(r"[^a-z0-9]+", _norm_text(text)) if t
    ) + " "
    return frozenset(p for p in _PRINT_PHRASES if f" {p} " in blob)


def card_name_without_treatment(name: str) -> str:
    """The character's name, with a treatment GemRate glued to the front removed.

    GemRate fingerprints arrive as "Full Art/Charizard GX" -- the parallel and
    the name in a single field. Read whole, that string is not a name, and
    everything downstream that treated it as one was wrong in both directions:
    it put "Full Art/" into PriceCharting search queries (which answered a
    Pokemon query with a One Piece product), it refused 43 correct product
    pages whose titles say "Charizard GX" and never say "Full Art", and it
    admitted "Double Full Heal #105" for "Full Art/M Pidgeot EX" because the
    similarity check found the word "full" in both.

    The treatment still has to be proven -- it just cannot be proven against a
    page title that never spells it out. That job belongs to the print-signature
    rules, which read _PRINT_PHRASES from the same vocabulary as this function.

    Only a known print phrase is dropped, so a card whose name genuinely
    contains a slash keeps it.
    """

    head, separator, tail = (name or "").partition("/")
    if separator and _norm_text(head).strip() in _PRINT_PHRASES:
        return tail.strip()
    return name


def _snk_treatment_mirror(master_name: str, localized: str) -> bool:
    """SNK titles carry the print treatment after the name colon
    ("Pikachu: Mirror [s8a 001/028]"). Only that slot may claim mirror —
    a card NAMED Mirror (e.g. "Mirror Energy") never triggers it.

    Only a BARE mirror slot counts: qualified treatments like
    "マスターボール ミラー" (151 Master Ball reverse) name a pattern parallel
    whose variant-side vocabulary never says mirror, so they must not
    hard-conflict on this axis."""

    for text in (master_name, localized):
        head = re.split(r"[\[（(]", text, 1)[0]
        parts = re.split(r"[:：]", head, 1)
        if len(parts) != 2:
            continue
        slot = parts[1].strip().strip("・･ 　")
        if slot == "ミラー" or re.fullmatch(r"(?i)mirror", slot):
            return True
    return False


def _same_listing(fp_a: Mapping[str, Any], fp_b: Mapping[str, Any]) -> bool:
    """Two provider listings describing the same physical card (dup ids).

    Mutual description coverage is required — one-way coverage would fold a
    "Pikachu-Reverse" listing into a "Pikachu Base" one.
    """

    a = _sig_tokens(str(fp_a.get("description") or ""))
    b = _sig_tokens(str(fp_b.get("description") or ""))
    return bool(a) and bool(b) and _token_covered(a, b) and _token_covered(b, a)


def _parallel_agrees(fp_parallel: str, variant_parallel: str) -> bool:
    fpp = _norm_text(fp_parallel)
    vpp = _norm_text(variant_parallel)
    if fpp == vpp:
        return True
    # A variant with no printing evidence + a Base fingerprint is the default
    # print of the same card; alt-art wordings never collapse into "".
    return {fpp, vpp} == {"", "base"} or (vpp == "" and fpp == "base")


def _print_signature_agrees(fp_parallel: str, variant: Mapping[str, Any]) -> bool:
    """Does the fingerprint's print wording describe this variant's print?

    printing_code is the stronger catalog evidence when present (a 1st Edition
    print is stored as printing_code='1st' with parallel_code left ''), so it
    takes precedence and the ""≡Base default-print collapse must not apply.
    """

    printing = _norm_text(variant.get("printing_code") or "")
    if printing:
        fpp = _norm_text(fp_parallel)
        return fpp == printing or fpp == f"{printing} edition"
    return _parallel_agrees(fp_parallel, variant.get("parallel_code") or "")


def _gemrate_bind_evidence(fp: Mapping[str, Any], generation: str) -> tuple[dict[str, Any], str]:
    """Contract shape for operator_strict_source_identity (037): providerClaims
    carry what the provider itself said; evidence ties the bind to a
    raw-verified v2 observation via rawPayloadSha256."""

    codes, _ = _set_signals(fp.get("setName") or "")
    evidence = {
        "providerClaims": {
            "tcgCode": _tcg_from_set(fp.get("setName")),
            "cardLanguage": str(fp.get("derivedLanguage") or ""),
            "collectorNumber": str(fp.get("cardNumber") or ""),
            "setCode": sorted(codes)[0] if codes else "",
            "printingCode": "",
            "parallelCode": str(fp.get("parallel") or ""),
        },
        "evidence": {
            "type": EVIDENCE_TYPE_GEMRATE,
            "rawPayloadSha256": fp["rawSha256"],
            "path": f"data/private/gemrate/cards/{fp['gemrateId']}/card_details.json",
            "canonicalUrl": fp["canonicalUrl"],
            "settledId": fp["settledId"],
            "generation": generation,
        },
    }
    return evidence, sha256_bytes(canonical_json(evidence))


