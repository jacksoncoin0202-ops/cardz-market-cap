#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Find the PriceCharting product a gap card actually is, by reading its SET.

The gap this closes was measured, not guessed. 104 English One Piece cards sit
in `qualified_market_pending` with no exact price source, and the reverify lane
cannot help them: reverify re-judges the binding a card ALREADY has, and these
cards' bindings point at the wrong product. Every refusal it prints says so --
"product_mismatch:missing=['captain','wings']" is our catalog saying Wings of
the Captain while the bound page says Pillars of Strength, because a One Piece
card keeps its ORIGINAL number when it is reprinted and the number is what the
old binding was built from.

Searching PriceCharting for each card was tried and measured at 2 of 35
uniquely resolved (`docs/POSTMORTEM_OP_GAP_20260809.md`), so this lane does not
search. It enumerates:

    /category/one-piece-cards   ->  137 console slugs, one per set
    /console/<slug>             ->  every product in that set, with its id

A set page states the whole set at once, which turns "which of PriceCharting's
million products is this" into "which row of these 233". The collector number
then picks a handful and the shared identity rules judge those.

WHAT THIS LANE MAY DECIDE, AND WHAT IT MAY NOT
----------------------------------------------
It proposes; it does not promote. A survivor is written as `manual_review` with
its page captured and a row in the proposal ledger (NOT the canonical map --
that file's rows are approved bindings), and `pc-identity-reverify` applies
the existing fail-closed contract to promote it to `exact` -- page product id ==
bound id == map id, no fingerprint conflict, product agreement, print signature.
Keeping one judge means a card admitted through this lane was admitted by the
same rules as every card already in the world, and a bug here can cost us a
missing binding but never a wrong one.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from collections import Counter
from datetime import datetime, timezone
from html import unescape as html_unescape
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rebuild_036 as R
import op_identity_rules
import pricecharting_cf_session as CF
from op_identity_rules import character_agrees, product_agrees

CONSOLE_DIR = R.ROOT / "data" / "private" / "pricecharting_session" / "html" / "console"
PAGES_DIR = R.ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
# A proposal is not a binding, so it does not go in the canonical map.
# consolidate_pc_map.py is that file's only writer and it globs
# c11_pc_ebay_map_full900_shard*.jsonl for transport rows, so a proposal here
# turns into a map row exactly when reverify promotes it into the registry.
# Appending straight to the canonical map (what this lane did until
# 2026-08-10) left two live rows for 26 cards and broke every reader that
# trusts one-row-per-variant, including the morning collect lane.
PROPOSAL_MAP_PATH = (R.ROOT / "data" / "runtime" / "private-source-map"
                     / "c11_pc_ebay_map_full900_shard_identity_discover.jsonl")

# A console listing page serves at most this many rows and then hands back a
# cursor. Read off the form PriceCharting itself renders
# (`<input name="cursor" value="150">`), not chosen by us: asking for a
# different stride returns the same 150 and silently drops the rest.
CONSOLE_PAGE_SIZE = 150

CATEGORY_BY_TCG = {
    "one-piece": "one-piece-cards",
    "pokemon": "pokemon-cards",
}

# `<tr id="product-6578585" data-product="6578585">` … `<td class="title" …>
# <a href="/game/<console>/<slug>">Roronoa Zoro [Alternate Art Manga] OP06-118</a>`
CONSOLE_ROW_RE = re.compile(
    r'<tr id="product-(?P<pid>\d+)"[^>]*>(?P<body>.*?)</tr>', re.S
)
ROW_TITLE_RE = re.compile(
    r'<td class="title"[^>]*>\s*<a href="(?P<href>[^"]+)"[^>]*>(?P<title>[^<]*)</a>', re.S
)


def progress(message: str) -> None:
    print(message, flush=True)


# ---------------------------------------------------------------------------
# Console index: our set name -> PriceCharting console slug
# ---------------------------------------------------------------------------

def _console_index_path(tcg: str) -> Path:
    return CONSOLE_DIR / f"_category-{CATEGORY_BY_TCG[tcg]}.html"


def load_console_index(tcg: str, *, allow_fetch: bool, timeout_s: int) -> dict[str, str]:
    """slug -> the words that slug spells, for every set of this game.

    One page, fetched once. The alternative -- deriving a slug from our own set
    name -- is a guess that fails silently: "One Piece Promos" would become
    `one-piece-promos` and PriceCharting spells it `one-piece-promo`, and a
    guessed slug that 404s is indistinguishable from a set that has no cards.
    """

    path = _console_index_path(tcg)
    if not path.is_file():
        if not allow_fetch:
            raise SystemExit(f"console index missing and --no-fetch given: {path}")
        url = f"https://www.pricecharting.com/category/{CATEGORY_BY_TCG[tcg]}"
        code = CF.cmd_fetch(url, path, timeout_s=timeout_s)
        if code != 0 or not path.is_file():
            raise SystemExit(f"could not fetch console index ({code}): {url}")
    html = path.read_text(encoding="utf-8", errors="replace")
    slugs = dict.fromkeys(re.findall(r'href="/console/([^"?#]+)"', html))
    prefix = tcg if tcg != "one-piece" else "one-piece"
    return {
        slug: urllib.parse.unquote(slug).replace("-", " ")
        for slug in slugs
        if slug.startswith(prefix)
    }


def match_console(set_name: str, language: str, index: dict[str, str]) -> tuple[str, str]:
    """Which set page is this card's set? ('', why) when nothing is provable.

    The containment runs THEIRS-in-OURS, not the other way round, and that
    direction is the whole design. Our set name is the noisy side: GemRate
    welds the treatment into it ("2025 Carrying On His Will (Op13) - English
    Manga Alt. Art Parallel"), and PriceCharting's set page has no reason to
    say "manga" or "parallel" -- those words belong to one card, not to the
    set. Demanding our words appear on their page therefore refuses the correct
    page on the strength of a treatment. Demanding THEIR words appear in ours
    asks the only question that has an answer: does this card's set name
    contain everything that names this set?

    The tie-break prefers the set page that says MORE, so a specific set always
    beats a broader one that happens to be a subset of it.

    Language is a hard filter, not a score: `one-piece-japanese-romance-dawn`
    and `one-piece-romance-dawn` share every distinctive token, and an English
    card resolving onto the Japanese set is exactly the wrong-product binding
    this lane exists to stop.
    """

    ours = op_identity_rules._product_tokens(set_name)
    want_japanese = language == "ja"
    scored: list[tuple[int, str]] = []
    nameless: list[str] = []
    for slug, words in index.items():
        if ("japanese" in words.split()) != want_japanese:
            continue
        theirs = op_identity_rules._product_tokens(words)
        if not theirs:
            # A set page whose name is nothing but stopwords is the promo
            # bucket. It is a subset of everything, so it can only be matched
            # by a card whose set name is equally nameless -- below.
            nameless.append(slug)
            continue
        if theirs <= ours:
            scored.append((len(theirs), slug))
    if not ours:
        # "One Piece Promos" names no product, and neither does
        # `one-piece-promo`. That agreement is the match; two of them would be
        # an ambiguity we must not guess at.
        if len(nameless) == 1:
            return nameless[0], ""
        return "", (f"set_name_has_no_distinctive_token:{len(nameless)}"
                    " nameless console(s)")
    if not scored:
        return "", f"no_console_for_set:{sorted(ours)}"
    scored.sort(reverse=True)
    if len(scored) > 1 and scored[0][0] == scored[1][0]:
        return "", f"console_ambiguous:{scored[0][1]},{scored[1][1]}"
    return scored[0][1], ""


# ---------------------------------------------------------------------------
# Console rows: every product in one set
# ---------------------------------------------------------------------------

def _console_page_path(slug: str, cursor: int) -> Path:
    safe = urllib.parse.unquote(slug).replace("/", "_").replace("'", "-")
    suffix = "" if cursor == 0 else f"~{cursor}"
    return CONSOLE_DIR / f"{safe}{suffix}.html"


def parse_console_rows(html: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for match in CONSOLE_ROW_RE.finditer(html):
        title_match = ROW_TITLE_RE.search(match.group("body"))
        if not title_match:
            continue
        href = title_match.group("href")
        rows.append({
            "pid": match.group("pid"),
            "slug": href.rstrip("/").rsplit("/", 1)[-1],
            # The page is HTML: "Hody &amp; Hyouzou" is one card, not a card
            # whose name contains the word "amp". Left escaped, the entity
            # becomes a token and every name check downstream compares it.
            "title": " ".join(html_unescape(title_match.group("title")).split()),
            "url": href if href.startswith("http")
                   else f"https://www.pricecharting.com{href}",
        })
    return rows


def console_rows(
    slug: str, *, allow_fetch: bool, timeout_s: int, delay: float,
) -> list[dict[str, str]]:
    """Every product in one set, following PriceCharting's own cursor.

    Stops when a page returns fewer rows than the page size: that is the page
    saying it has no more, and it is checked instead of assumed because a set
    whose size happens to be an exact multiple of 150 would otherwise lose its
    tail without a single error.
    """

    collected: list[dict[str, str]] = []
    seen: set[str] = set()
    cursor = 0
    while True:
        path = _console_page_path(slug, cursor)
        if not path.is_file():
            if not allow_fetch:
                break
            url = f"https://www.pricecharting.com/console/{slug}"
            if cursor:
                url += f"?cursor={cursor}&when=none&sort="
            code = CF.cmd_fetch(url, path, timeout_s=timeout_s)
            if code != 0 or not path.is_file():
                progress(f"  [console] {slug} cursor={cursor} fetch failed ({code})")
                break
            if delay:
                time.sleep(delay)
        page_rows = parse_console_rows(path.read_text(encoding="utf-8", errors="replace"))
        fresh = [row for row in page_rows if row["pid"] not in seen]
        seen.update(row["pid"] for row in fresh)
        collected.extend(fresh)
        if len(page_rows) < CONSOLE_PAGE_SIZE or not fresh:
            break
        cursor += CONSOLE_PAGE_SIZE
    return collected


# ---------------------------------------------------------------------------
# Judging a listing row
# ---------------------------------------------------------------------------

# "Roronoa Zoro [Alternate Art Manga] OP06-118" / "Nami OP01-016"
LISTING_NUMBER_RE = re.compile(r"\b([A-Z]{2,4}\d{2}-\d{2,4}[A-Za-z]?)\b")
# The promo bucket numbers its cards "P-062" instead, which the booster pattern
# cannot match (one letter, no set digits).
LISTING_PROMO_NUMBER_RE = re.compile(r"\bP-(\d{1,4})\b")
LISTING_BRACKET_RE = re.compile(r"\[([^\]]+)\]")


def listing_number(row: dict[str, str]) -> str:
    """The collector number this listing prints, '' when it prints none.

    Read from the title first and the slug second. Both are the provider's own
    words; the slug is the fallback because PriceCharting lowercases it
    ("...-op06-118") and a lowercased number still compares fine, while a title
    that omits the number entirely leaves the slug as the only witness.
    """

    for text in (row["title"].upper(), row["slug"].upper().replace("_", "-")):
        match = LISTING_NUMBER_RE.search(text)
        if match:
            return match.group(1).upper()
        match = LISTING_PROMO_NUMBER_RE.search(text)
        if match:
            return f"P-{match.group(1)}"
    return ""


def numbers_agree(ours: str, theirs: str, tcg: str) -> bool:
    """Is this the same collector number, written the two providers' two ways?

    One Piece promos are the only place the spellings differ, and the
    difference is that GemRate drops the prefix. Measured against the served
    pages (2026-08-09):

        ours 062 "O-Nami / Illustration Box Vol.1"   -> OP05-062 O-Nami
        ours 109 "Monkey D. Luffy / Ill. Box Vol.3"  -> OP07-109 Monkey.D.Luffy
        ours 113 "Roronoa Zoro / Ill. Box Vol.3"     -> OP07-113 Roronoa Zoro

    So a bare number of ours is the TAIL of theirs. It is deliberately not read
    as "P-062": the promo page really does carry a P-062 and it is Hody &
    Hyouzou, a different card entirely -- which is why the tail alone never
    decides anything here. A row that agrees on the tail still has to pass
    character, product and print-signature agreement, and a tail that fits more
    than one surviving row is held as ambiguous rather than guessed at.

    Scoped to One Piece on purpose. Pokémon promos are spelled "085/SVP" and
    "SVP-085", and a rule written for one game's notation quietly deciding a
    second game's identities is a defect this repo has already shipped once.
    """

    if ours == theirs:
        return True
    if tcg != "one-piece" or not ours.isdigit() or "-" not in theirs:
        return False
    tail = theirs.rsplit("-", 1)[1]
    return tail.isdigit() and int(tail) == int(ours)


def judge_listing(
    row: dict[str, Any], listing: dict[str, str], set_name: str = "",
) -> tuple[bool, str]:
    """Cheap pre-checks, in the same vocabulary the promoting gate will use.

    Deliberately not the whole contract: the product page has evidence a
    listing row does not (canonical URL, VGPC product id, the heading's own set
    text), and pc-identity-reverify reads all of it before anything becomes
    exact. What these checks buy is that we only spend a page fetch -- and only
    write a manual_review row -- on candidates that already agree on the three
    things a listing row CAN state.

    `set_name` names the product this listing is being judged against. It
    defaults to the catalog's set_name -- the set the card was PULLED FROM --
    and the caller passes the set the card's NUMBER names when it is reading
    that set's page instead. See `console_candidates`.
    """

    ours_number = str(row.get("collector_number") or "").strip().upper()
    theirs_number = listing_number(listing)
    if not ours_number:
        return False, "our_collector_number_missing"
    if not theirs_number:
        return False, "listing_number_missing"
    if not numbers_agree(ours_number, theirs_number, str(row.get("tcg_code") or "")):
        return False, f"number:{theirs_number}!={ours_number}"

    same_character, why = character_agrees(
        row.get("fp_name") or row.get("canonical_name") or "", listing["title"],
    )
    if not same_character:
        return False, why

    same_product, why = product_agrees(
        set_name or str(row.get("set_name") or ""),
        str(row.get("fp_parallel") or ""),
        listing["title"], urllib.parse.unquote(listing["url"]),
    )
    if not same_product:
        return False, why

    bracket = LISTING_BRACKET_RE.search(listing["title"])
    if not R._pc_print_signature_ok(bracket.group(1).strip() if bracket else "", row):
        return False, (
            f"print_signature:[{bracket.group(1).strip() if bracket else ''}]"
            f" vs printing={row.get('printing_code') or ''}"
            f" parallel={row.get('parallel_code') or ''}"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def console_candidates(
    row: dict[str, Any], index: dict[str, str],
) -> list[tuple[str, str, str]]:
    """The set pages this card could be on, best first: (slug, set name, why).

    Two, because One Piece reprints a card into a later product without
    renumbering it. GemRate files such a card under the product it was pulled
    from -- "OP09-Emperors in the New World Nami Special Alternate Art 106" --
    while the number printed on the card stays OP08-106. PriceCharting files it
    the other way: measured 2026-08-09, the Emperors page carries 189 rows and
    not one of them is OP08-106, while the Two Legends page carries
    "Nami [SP Foil] OP08-106".

    Reading only the set_name page cost 22 of 59 English holds. So the number's
    own set is tried too, and the set name that page is judged against is that
    set's, not the pulled-from set's -- otherwise product_agrees would refuse
    every row on it for saying "Two Legends" when we said "Emperors".

    This widens where we look; it does not widen what we accept. The number
    still has to match in full (OP08-106 names its own set), the character
    still has to match, and the print signature still has to match.
    """

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    language = str(row["card_language"] or "")
    catalog_set = str(row["set_name"] or "")
    slug, why = match_console(catalog_set, language, index)
    if slug:
        out.append((slug, catalog_set, "set_name"))
        seen.add(slug)
    first_why = why

    names = R.set_names_a_card_could_carry(row)
    number_set = names[1] if len(names) > 1 else ""
    if number_set and number_set != catalog_set:
        slug2, why2 = match_console(number_set, language, index)
        if slug2 and slug2 not in seen:
            out.append((slug2, number_set, "collector_number"))
        elif not slug2 and not out:
            first_why = f"{first_why}; number_set:{why2}"
    return out or [("", "", first_why)]


def red_listed_variants() -> list[int]:
    """The human refusals, read off the sheet by the shared derivation.

    This lane is the reason `R.red_listed_variants` exists; see its docstring.
    """

    return R.red_listed_variants()


def select_targets(
    conn: Any, generation: str, min_pop: int, tcg: str, limit: int, language: str,
) -> list[dict[str, Any]]:
    """Qualified, bound to a variant, and with no exact price source yet.

    Mirrors snk_identity_discover.select_targets, with the language filter
    inverted: that lane is the non-English one because English cards route to
    PriceCharting under D4 language primacy, and this is where they route to.

    Red-listed cards are removed here rather than refused later, so they never
    reach a page fetch or a proposal record at all.
    """

    sql = """
        SELECT rm.variant_id, rm.latest_psa10_population AS pop,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json, '$.fingerprint.name')) AS fp_name,
               JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json, '$.fingerprint.parallel')) AS fp_parallel,
               v.tcg_code, v.card_language, v.set_name, v.collector_number,
               v.canonical_name, v.set_code AS v_set_code,
               v.printing_code AS v_printing_code,
               p.parallel_code, p.printing_code, p.canonical_printing_sha256,
               p.tcg_code AS p_tcg_code, p.card_language AS p_card_language,
               p.set_code AS p_set_code, p.collector_number AS p_collector_number,
               p.edition_code AS p_edition_code, p.finish_code AS p_finish_code
          FROM catalog_rebuild_member rm
          JOIN catalog_variant v ON v.id = rm.variant_id
          LEFT JOIN catalog_printing_identity p ON p.variant_id = v.id
         WHERE rm.generation_id = %s
           AND rm.cohort <> 'non_qualified'
           AND rm.latest_psa10_population >= %s
           AND v.card_language = %s
           AND NOT EXISTS (
                 SELECT 1 FROM catalog_source_identity si
                  WHERE si.variant_id = v.id
                    AND si.source_code IN ('snkrdunk', 'snk_psa10', 'pricecharting')
                    AND si.match_status = 'exact'
               )
    """
    params: list[Any] = [generation, min_pop, language]
    red = red_listed_variants()
    sql += f" AND v.id NOT IN ({','.join(['%s'] * len(red))})"
    params.extend(red)
    if tcg:
        sql += " AND v.tcg_code = %s"
        params.append(tcg)
    sql += " ORDER BY rm.latest_psa10_population DESC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        rows = [dict(row) for row in cursor.fetchall()]
    # The same weld the SNK lane strips: GemRate writes the treatment into the
    # name ("Full Art/Pikachu Vmax"), and character_agrees compares the last
    # word of it. Left alone, "Vmax" is compared instead of the character.
    for row in rows:
        row["fp_name"] = R.card_name_without_treatment(row.get("fp_name") or "")
    return rows


def existing_pc_owner(conn: Any, pids: list[str]) -> dict[str, dict[str, Any]]:
    if not pids:
        return {}
    placeholders = ",".join(["%s"] * len(pids))
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT external_entity_id, variant_id, match_status, bind_evidence_json"
            " FROM catalog_source_identity"
            f" WHERE source_code='pricecharting' AND external_entity_id IN ({placeholders})",
            tuple(pids),
        )
        return {str(row["external_entity_id"]): dict(row) for row in cursor.fetchall()}


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def cmd_pc_identity_discover(args: argparse.Namespace) -> int:
    CONSOLE_DIR.mkdir(parents=True, exist_ok=True)
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    conn = R.connect(args.credentials_env or R.DAILY_CREDENTIALS_ENV)
    try:
        generation = args.generation or _latest_generation(conn)
        targets = select_targets(
            conn, generation, args.min_pop, args.tcg, args.limit, args.language,
        )
        progress(f"[targets] {len(targets)} card(s) generation={generation}"
                 f" tcg={args.tcg or '*'} language={args.language}")
        index = load_console_index(
            args.tcg or "one-piece", allow_fetch=not args.no_fetch,
            timeout_s=args.timeout,
        )
        progress(f"[index] {len(index)} console slugs")

        counts = {
            "targets": len(targets), "noConsole": 0, "consoleEmpty": 0,
            "candidates": 0, "accepted": 0, "ambiguous": 0, "noSurvivor": 0,
            "pageFetchFailed": 0, "alreadyOwned": 0, "alreadyRejected": 0,
            "proposed": 0, "written": 0, "repointed": 0,
        }
        proposals: list[dict[str, Any]] = []
        held: list[dict[str, Any]] = []
        rows_cache: dict[str, list[dict[str, str]]] = {}
        writes: list[dict[str, Any]] = []

        for row in targets:
            vid = int(row["variant_id"])
            candidates = console_candidates(row, index)
            if not candidates[0][0]:
                counts["noConsole"] += 1
                why = candidates[0][2]
                held.append({"variant_id": vid, "reason": "no_console", "detail": why,
                             "card": str(row["canonical_name"])[:120]})
                progress(f"[rule] v{vid} no_console :: {why}")
                continue

            survivors: list[dict[str, str]] = []
            rejections: list[str] = []
            refused_by: Counter[str] = Counter()
            page_numbers: Counter[str] = Counter()
            reached_identity = 0
            slug = ""
            console_empty = False
            tried: list[str] = []
            for slug, judged_set, via in candidates:
                if slug not in rows_cache:
                    rows_cache[slug] = console_rows(
                        slug, allow_fetch=not args.no_fetch,
                        timeout_s=args.timeout, delay=args.delay,
                    )
                    progress(f"[console] {slug} -> {len(rows_cache[slug])} products")
                listings = rows_cache[slug]
                tried.append(f"{slug}({via})")
                if not listings:
                    console_empty = True
                    continue
                console_empty = False
                # Each page is judged on its own: a page that refuses every row
                # must not leave its counters behind to describe the next one.
                survivors, rejections = [], []
                refused_by, page_numbers, reached_identity = Counter(), Counter(), 0
                for listing in listings:
                    ok, reason = judge_listing(row, listing, judged_set)
                    if ok:
                        survivors.append(listing)
                        continue
                    refused_by[reason.split(":", 1)[0]] += 1
                    if reason.startswith("number:") or reason.startswith("listing_number_"):
                        # Listing the ~200 rows that are simply a different card
                        # says nothing, but dropping them silently was worse: a
                        # hold then printed `"rejections": []` and the operator
                        # could not tell "nothing came close" from "the filter
                        # never ran". Count the shapes instead, so the record
                        # explains itself.
                        theirs = listing_number(listing)
                        page_numbers[theirs.split("-", 1)[0] if "-" in theirs else "?"] += 1
                        continue
                    reached_identity += 1
                    rejections.append(f"{listing['pid']}:{reason}")
                if len(survivors) == 1:
                    break
            if console_empty and not survivors:
                counts["consoleEmpty"] += 1
                held.append({"variant_id": vid, "reason": "console_empty",
                             "detail": ",".join(tried),
                             "card": str(row["canonical_name"])[:120]})
                continue
            counts["candidates"] += len(survivors)

            if len(survivors) != 1:
                if survivors:
                    counts["ambiguous"] += 1
                    held.append({
                        "variant_id": vid, "reason": "ambiguous_survivors",
                        "detail": slug, "consolesTried": tried,
                        "card": str(row["canonical_name"])[:120],
                        "survivors": [
                            {"pid": s["pid"], "title": s["title"][:100]} for s in survivors
                        ],
                    })
                else:
                    counts["noSurvivor"] += 1
                    held.append({
                        "variant_id": vid, "reason": "no_survivor", "detail": slug,
                        "card": str(row["canonical_name"])[:120],
                        "askedNumber": str(row.get("collector_number") or ""),
                        "consolesTried": tried,
                        "reachedIdentityChecks": reached_identity,
                        "pageCarries": page_numbers.most_common(6),
                        "refusedBy": dict(refused_by),
                        "rejections": rejections[:12],
                    })
                if survivors:
                    progress(f"[rule] v{vid} pop={row['pop']} ambiguous console={slug}"
                             f" :: {str(row['canonical_name'])[:60]}")
                else:
                    carries = ",".join(
                        f"{prefix}x{count}" for prefix, count in page_numbers.most_common(4)
                    ) or "no numbered row"
                    progress(
                        f"[rule] v{vid} pop={row['pop']} hold console={slug}"
                        f" :: asked {row.get('collector_number') or '(none)'},"
                        f" page carries {carries};"
                        f" {reached_identity} row(s) reached the identity checks"
                    )
                continue

            winner = survivors[0]
            writes.append({"row": row, "listing": winner, "console": slug, "via": via})
            counts["proposed"] += 1
            proposals.append({
                "variant_id": vid, "pid": winner["pid"], "console": slug, "via": via,
                "title": winner["title"][:120], "pop": int(row["pop"]),
                "card": str(row["canonical_name"])[:120],
            })
            progress(f"[rule] v{vid} pop={row['pop']} ACCEPT pid={winner['pid']}"
                     f" :: {winner['title'][:70]}")

        if args.write and writes:
            owners = existing_pc_owner(conn, [w["listing"]["pid"] for w in writes])
            map_rows: list[dict[str, Any]] = []
            try:
                with conn.cursor() as cursor:
                    for item in writes:
                        row, listing = item["row"], item["listing"]
                        vid, pid = int(row["variant_id"]), listing["pid"]
                        owner = owners.get(pid)
                        if owner is not None and str(owner["match_status"]) == "rejected":
                            # A contract already looked at this pairing and threw
                            # it out. Re-proposing it would overwrite a settled
                            # verdict with a guess made from one listing row.
                            counts["alreadyRejected"] += 1
                            held.append({
                                "variant_id": vid, "reason": "pid_already_rejected",
                                "detail": f"pid={pid} rejected on v{owner['variant_id']}",
                            })
                            continue
                        repoint_from = None
                        if owner is not None and int(owner["variant_id"] or 0) != vid:
                            # Somebody else's product. Proposing is free; taking
                            # is not, and this lane has no authority to move a
                            # binding off another card -- unless the operator
                            # says so AND what is being taken is an unproven
                            # claim. A promoted binding is never taken: the
                            # reprints this lane exists for are exactly where
                            # two cards share a collector number, so the card
                            # already holding the page is the likeliest wrong
                            # one, and the likeliest right one too.
                            if (not args.allow_repoint
                                    or str(owner["match_status"]) == "exact"):
                                counts["alreadyOwned"] += 1
                                held.append({
                                    "variant_id": vid, "reason": "pid_owned_elsewhere",
                                    "detail": f"pid={pid} held by v{owner['variant_id']}"
                                              f" ({owner['match_status']})",
                                })
                                continue
                            repoint_from = int(owner["variant_id"] or 0)
                        page_path = PAGES_DIR / f"{vid}_{pid}.html"
                        if not page_path.is_file():
                            code = CF.cmd_fetch(
                                listing["url"], page_path, timeout_s=args.timeout,
                            )
                            if code != 0 or not page_path.is_file():
                                counts["pageFetchFailed"] += 1
                                held.append({
                                    "variant_id": vid, "reason": "page_fetch_failed",
                                    "detail": f"pid={pid} code={code}",
                                })
                                continue
                            if args.delay:
                                time.sleep(args.delay)
                        # Prove the capture is the product we asked for before
                        # anything points at it. A redirect that lands on a
                        # different product would otherwise become a binding
                        # nobody notices until its prices are already public.
                        page_bytes = page_path.read_bytes()
                        page_pid = R._pc_page_product_id(
                            page_bytes.decode("utf-8", errors="replace")
                        )
                        if page_pid != pid:
                            counts["pageFetchFailed"] += 1
                            held.append({
                                "variant_id": vid, "reason": "page_product_mismatch",
                                "detail": f"asked {pid}, page says {page_pid or '?'}",
                            })
                            page_path.unlink(missing_ok=True)
                            continue
                        evidence = {
                            "action": "pc-identity-discover",
                            "console": item["console"],
                            "consoleFoundVia": item.get("via") or "set_name",
                            "listingTitle": listing["title"][:200],
                            "canonicalUrl": listing["url"],
                            "capturePath": page_path.relative_to(R.ROOT).as_posix(),
                            "captureSha256": R.sha256_bytes(page_bytes),
                            "proposedAt": stamp,
                            "note": "proposal only; pc-identity-reverify decides",
                        }
                        # A proposal's evidence is the proposal itself, and the
                        # column is NOT NULL: every row in this table has to be
                        # able to say what it was built from. Reverify replaces
                        # this with the product-page digest when it promotes.
                        evidence_sha = R.sha256_bytes(R.canonical_json(evidence))
                        if repoint_from is not None:
                            evidence["repointedFrom"] = repoint_from
                            evidence_sha = R.sha256_bytes(R.canonical_json(evidence))
                            # The two extra conditions are re-checked against the
                            # stored row, not against what was read minutes ago:
                            # anything that promoted or refused this pairing in
                            # between changes zero rows and stops the run rather
                            # than quietly overwriting a settled decision.
                            cursor.execute(
                                "UPDATE catalog_source_identity SET variant_id=%s,"
                                " match_status='manual_review', evidence_sha256=%s,"
                                " source_product_number=%s, bind_evidence_json=%s"
                                " WHERE source_code='pricecharting'"
                                "   AND external_entity_id=%s"
                                "   AND match_status <> 'exact'"
                                f"   AND {R.NOT_A_REJECTION_VERDICT_SQL}",
                                (
                                    vid, evidence_sha,
                                    str(row["collector_number"] or "")[:96],
                                    json.dumps(evidence, ensure_ascii=False,
                                               sort_keys=True),
                                    pid,
                                ),
                            )
                            if cursor.rowcount != 1:
                                raise SystemExit(
                                    f"repoint of product {pid} changed"
                                    f" {cursor.rowcount} rows, not 1: the row moved,"
                                    " was proven, or was refused while this run was"
                                    " thinking"
                                )
                            counts["repointed"] += 1
                        else:
                            cursor.execute(
                                "INSERT INTO catalog_source_identity (source_code,"
                                " external_entity_id, variant_id, match_status,"
                                " evidence_sha256, source_product_number,"
                                " bind_evidence_json)"
                                " VALUES ('pricecharting', %s, %s, 'manual_review',"
                                " %s, %s, %s)"
                                " ON DUPLICATE KEY UPDATE variant_id=VALUES(variant_id),"
                                "  evidence_sha256=VALUES(evidence_sha256),"
                                "  source_product_number=VALUES(source_product_number),"
                                "  bind_evidence_json=VALUES(bind_evidence_json)",
                                (
                                    pid, vid, evidence_sha,
                                    str(row["collector_number"] or "")[:96],
                                    json.dumps(evidence, ensure_ascii=False,
                                               sort_keys=True),
                                ),
                            )
                        map_rows.append({
                            "card_name": str(row["canonical_name"] or "")[:200],
                            "collector_number": str(row["collector_number"] or ""),
                            "confidence": "high",
                            "html_path": page_path.relative_to(R.ROOT).as_posix(),
                            "htmlPath": page_path.relative_to(R.ROOT).as_posix(),
                            "mapped_at": stamp,
                            "notes": f"pc-identity-discover console={item['console']}",
                            "pc_product_id": int(pid),
                            "pc_url": listing["url"],
                            "ready_for_c12": True,
                            "set_name": str(row["set_name"] or ""),
                            "source": "pc_identity_discover",
                            "status": "mapped",
                            "variant_id": vid,
                        })
                        counts["written"] += 1
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            if map_rows:
                # Appended only after the commit: a ledger row pointing at a
                # binding that never landed is the one inconsistency the
                # reverify lane cannot see, because it reads the row as truth.
                # Keyed by (variant, product) so re-running the lane restates a
                # proposal instead of stacking another copy of it.
                seen: set[tuple[int, int]] = set()
                if PROPOSAL_MAP_PATH.is_file():
                    for line in PROPOSAL_MAP_PATH.read_text(
                            encoding="utf-8-sig").splitlines():
                        if line.strip():
                            old = json.loads(line)
                            seen.add((int(old.get("variant_id") or 0),
                                      int(old.get("pc_product_id") or 0)))
                fresh = [row for row in map_rows
                         if (int(row["variant_id"]),
                             int(row["pc_product_id"])) not in seen]
                if fresh:
                    with PROPOSAL_MAP_PATH.open("a", encoding="utf-8") as handle:
                        handle.write("\n".join(
                            json.dumps(row, ensure_ascii=False, sort_keys=True)
                            for row in fresh) + "\n")
            progress(f"[bind] {counts['written']} manual_review proposal(s) written"
                     f" ({counts['repointed']} taken from unproven claims);"
                     f" run pc-identity-reverify to promote")

        report = {
            "pcIdentityDiscover": True,
            "write": bool(args.write),
            "generation": generation,
            "counts": counts,
            "proposals": proposals,
            "held": held,
        }
        artifact = (R.ROOT / "data" / "runtime" / "rebuild-036"
                    / f"pc-identity-discover-{stamp}.json")
        artifact.write_bytes(R.canonical_json(report))
        print(json.dumps(report, ensure_ascii=False, indent=1, default=str))
        return 0
    finally:
        conn.close()


def _latest_generation(conn: Any) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT generation_id FROM catalog_rebuild_member"
            " ORDER BY computed_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
    if not row:
        raise SystemExit("no catalog_rebuild_member rows: nothing to discover against")
    return str(row["generation_id"])


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--generation", default="")
    parser.add_argument("--tcg", default="one-piece")
    parser.add_argument("--language", default="en")
    parser.add_argument("--min-pop", dest="min_pop", type=int, default=1000)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument(
        "--allow-repoint", dest="allow_repoint", action="store_true",
        help="move a product off a card that only CLAIMS it (manual_review) onto"
             " the card this lane proved. Never touches an 'exact' binding or a"
             " row a rejection verdict already settled.",
    )
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument(
        "--no-fetch", dest="no_fetch", action="store_true",
        help="use only console pages already cached; never touch the network",
    )
    # A Path, not a str: R.connect reads the file. Declared as a bare string
    # the flag parsed fine and then died inside connect(), which is the worst
    # place to find out -- the freeze was already up and the run had started.
    parser.add_argument("--credentials-env", dest="credentials_env", type=Path,
                        default=None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    add_arguments(parser)
    return cmd_pc_identity_discover(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
