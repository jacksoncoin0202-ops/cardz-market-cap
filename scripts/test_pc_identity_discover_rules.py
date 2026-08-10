#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""What pc_identity_discover may and may not conclude, checked against real HTML.

The console-page parsing is checked against a page PriceCharting actually
served (one-piece-wings-of-the-captain, captured 2026-08-09), not a hand-written
fixture, because the thing most likely to break is PriceCharting's markup and a
fixture I wrote would keep passing after it changed.

Run: python -X utf8 scripts/test_pc_identity_discover_rules.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_identity_discover as D

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}: got {got!r}, want {want!r}")


def truthy(label: str, got) -> None:
    global CHECKS
    CHECKS += 1
    if not got:
        FAILED.append(f"FAIL {label}: got {got!r}, want truthy")


# --- 1. console index: our set name -> the right set page ------------------
INDEX = {
    "one-piece-wings-of-the-captain": "one piece wings of the captain",
    "one-piece-japanese-wings-of-the-captain": "one piece japanese wings of the captain",
    "one-piece-pillars-of-strength": "one piece pillars of strength",
    "one-piece-promo": "one piece promo",
    "one-piece-japanese-promo": "one piece japanese promo",
    "one-piece-romance-dawn": "one piece romance dawn",
    "one-piece-japanese-romance-dawn": "one piece japanese romance dawn",
    "one-piece-carrying-on-his-will": "one piece carrying on his will",
    "one-piece-500-years-in-the-future": "one piece 500 years in the future",
}

check("plain set name resolves",
      D.match_console("One Piece Wings of the Captain", "en", INDEX)[0],
      "one-piece-wings-of-the-captain")
# The English card must not land on the Japanese set page: those two slugs
# share every distinctive token, so only the language filter separates them.
check("English card does not take the Japanese set page",
      D.match_console("One Piece Romance Dawn", "en", INDEX)[0],
      "one-piece-romance-dawn")
check("Japanese card takes the Japanese set page",
      D.match_console("One Piece Romance Dawn", "ja", INDEX)[0],
      "one-piece-japanese-romance-dawn")
# GemRate spells the same set four ways across the gap list.
for spelling in (
    "One Piece Carrying On His Will",
    "One Piece Carrying On His Will OP-13",
    "2025 Carrying On His Will (Op13) - English Manga Alt. Art Parallel",
    "2025 Carrying On His Will Alternate Art",
):
    check(f"spelling resolves: {spelling[:38]}",
          D.match_console(spelling, "en", INDEX)[0],
          "one-piece-carrying-on-his-will")
check("plural promos reaches the singular promo page",
      D.match_console("One Piece Promos", "en", INDEX)[0], "one-piece-promo")
# A set page that says LESS than our set name is a different product.
slug, why = D.match_console("One Piece Wings of the Captain", "en",
                            {"one-piece-promo": "one piece promo"})
check("a broader page is not accepted as our set", slug, "")
truthy("and it says why", why.startswith("no_console_for_set"))
# Digits carry meaning in a set name and must not be dropped as noise.
check("numeric set name resolves",
      D.match_console("One Piece 500 Years in the Future", "en", INDEX)[0],
      "one-piece-500-years-in-the-future")


# --- 2. console page parsing, against HTML PriceCharting served ------------
PAGE = (ROOT / "data" / "private" / "pricecharting_session" / "html" / "console"
        / "one-piece-wings-of-the-captain.html")
if not PAGE.is_file():
    FAILED.append(f"FAIL fixture missing: {PAGE} (fetch it before running this test)")
else:
    rows = D.parse_console_rows(PAGE.read_text(encoding="utf-8", errors="replace"))
    # A parser that finds nothing passes every assertion about what it found.
    truthy("the real page yields a full page of rows", len(rows) >= D.CONSOLE_PAGE_SIZE)
    by_pid = {row["pid"]: row for row in rows}
    zoro = by_pid.get("6578585")
    truthy("a known product id is present", zoro is not None)
    if zoro:
        check("title is read whole", zoro["title"],
              "Roronoa Zoro [Alternate Art Manga] OP06-118")
        check("slug is read", zoro["slug"], "roronoa-zoro-alternate-art-manga-op06-118")
        truthy("url is absolute", zoro["url"].startswith("https://www.pricecharting.com/game/"))
        check("number comes off the title", D.listing_number(zoro), "OP06-118")
    truthy("every row has a product id", all(row["pid"].isdigit() for row in rows))
    truthy("every row has a title", all(row["title"] for row in rows))

# The number falls back to the slug when the title omits it.
check("number falls back to the slug",
      D.listing_number({"title": "Roronoa Zoro [Alternate Art]",
                        "slug": "roronoa-zoro-alternate-art-op06-118"}), "OP06-118")
check("no number anywhere reads as empty",
      D.listing_number({"title": "Booster Box", "slug": "booster-box"}), "")


# --- 3. judging a listing row ---------------------------------------------
def variant(**over):
    row = {
        "collector_number": "OP06-118", "fp_name": "Roronoa Zoro",
        "canonical_name": "2024 One Piece OP06-Wings of the Captain Roronoa Zoro"
                          " Manga Alternate Art 118",
        "set_name": "One Piece Wings of the Captain",
        "fp_parallel": "Manga Alternate Art",
        "parallel_code": "aa", "printing_code": "manga",
    }
    row.update(over)
    return row


def listing(**over):
    row = {
        "pid": "6578585",
        "title": "Roronoa Zoro [Alternate Art Manga] OP06-118",
        "slug": "roronoa-zoro-alternate-art-manga-op06-118",
        "url": "https://www.pricecharting.com/game/one-piece-wings-of-the-captain/"
               "roronoa-zoro-alternate-art-manga-op06-118",
    }
    row.update(over)
    return row


ok, why = D.judge_listing(variant(), listing())
check(f"the matching listing is accepted ({why})", ok, True)

# The number is the coarse filter and has to actually filter.
ok, why = D.judge_listing(variant(), listing(
    title="Roronoa Zoro [Alternate Art Manga] OP06-119",
    slug="roronoa-zoro-alternate-art-manga-op06-119"))
check("a different number is refused", ok, False)
truthy("and the reason names the number", why.startswith("number:"))

# Same number, different character: the exact failure that put Charlotte
# Cracker on Charlotte Pudding's card before character_agrees existed.
ok, why = D.judge_listing(variant(), listing(
    title="Nami [Alternate Art Manga] OP06-118",
    slug="nami-alternate-art-manga-op06-118"))
check("a different character is refused", ok, False)
truthy("and the reason names the character", "character" in why)

# Same number and character, wrong treatment: base and parallel share a number,
# so the bracket is the only thing that can tell them apart.
ok, why = D.judge_listing(variant(), listing(
    title="Roronoa Zoro OP06-118", slug="roronoa-zoro-op06-118"))
check("the base print is refused for a parallel card", ok, False)
truthy("and the reason names the print signature", why.startswith("print_signature:"))

# A card whose own number we do not have cannot be matched on one.
ok, why = D.judge_listing(variant(collector_number=""), listing())
check("no collector number of ours means no match", ok, False)
check("and it says so", why, "our_collector_number_missing")

# The set has to agree even when number, character and treatment do. This is
# the One Piece reprint trap: OP03-008 exists in OP03 and in OP06.
ok, why = D.judge_listing(variant(set_name="One Piece Pillars of Strength"), listing())
check("a listing from another set is refused", ok, False)
truthy("and the reason names the product", "product_mismatch" in why)


# --- 3b. promo numbers: ours is the tail of theirs -------------------------
# GemRate files these cards under "One Piece Promos" with a bare number and no
# set code; PriceCharting keeps the full number. Checked against the served
# promo pages rather than described, because the whole question is what the
# provider actually prints.
PROMO_PAGES = sorted(
    (ROOT / "data" / "private" / "pricecharting_session" / "html" / "console").glob(
        "one-piece-promo*.html")
)
truthy("promo console pages are captured", PROMO_PAGES)
promo_rows: dict[str, dict[str, str]] = {}
for page in PROMO_PAGES:
    for row in D.parse_console_rows(page.read_text(encoding="utf-8", errors="replace")):
        promo_rows[row["pid"]] = row

luffy = promo_rows.get("10956514")
truthy("the promo page has the card we are looking for", luffy is not None)
if luffy:
    check("its number is the full one", D.listing_number(luffy), "OP07-109")
    check("bare 109 is that number", D.numbers_agree("109", "OP07-109", "one-piece"), True)
    check("leading zeros do not matter",
          D.numbers_agree("007", "OP01-007", "one-piece"), True)

# Entities are markup, not part of anybody's name.
hody = promo_rows.get("11235848")
truthy("the entity-bearing row is present", hody is not None)
if hody:
    check("HTML entities are decoded", hody["title"], "Hody & Hyouzou P-062")

# The tail rule is a reading of their notation, not a licence to bind: the
# promo page's P-062 really is a different card from our 062.
check("a different tail is still refused",
      D.numbers_agree("109", "OP07-190", "one-piece"), False)
check("Pokemon is not judged by One Piece notation",
      D.numbers_agree("109", "OP07-109", "pokemon"), False)
check("a bare number does not match a bare number of another card",
      D.numbers_agree("109", "", "one-piece"), False)


def promo_variant(**over):
    row = {
        "tcg_code": "one-piece", "collector_number": "109",
        "fp_name": "Monkey D. Luffy",
        "canonical_name": "2024 One Piece Promo Monkey D. Luffy Illustration Box Vol.3 109",
        "set_name": "One Piece Promos", "fp_parallel": "Illustration Box Vol.3",
        "parallel_code": "illustration box vol.3", "printing_code": "",
    }
    row.update(over)
    return row


if luffy:
    ok, why = D.judge_listing(promo_variant(), luffy)
    check(f"the promo card is accepted on its tail ({why})", ok, True)
if hody:
    # Same tail, different card: 062 is our O-Nami, P-062 is theirs.
    ok, why = D.judge_listing(
        promo_variant(collector_number="062", fp_name="O-Nami",
                      fp_parallel="Illustration Box Vol.1",
                      parallel_code="illustration box vol.1"), hody)
    check("a same-tail row of another character is refused", ok, False)
    truthy("and the reason names the character", "character" in why)


# --- 3c. the human red ruling reaches target selection ---------------------
# Three of these were proposed and two went live before validator034 caught it,
# so the check belongs where targets are chosen, not where evidence is written.
RED = D.red_listed_variants()
check("the sheet still rules on thirteen cards", len(RED), 13)
for vid in (1717, 1741, 1464):
    truthy(f"v{vid} is on the red list", vid in RED)


class _Cursor:
    """Records the statement instead of running it."""

    def __init__(self, sink): self.sink = sink
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def execute(self, sql, params=()): self.sink.append((sql, params))
    def fetchall(self): return []


class _Conn:
    def __init__(self): self.calls = []
    def cursor(self): return _Cursor(self.calls)


conn = _Conn()
D.select_targets(conn, "036_x", 1000, "one-piece", 0, "en")
sql, params = conn.calls[-1]
truthy("target selection excludes ids by list", "v.id NOT IN (" in sql)
missing = [vid for vid in RED if vid not in params]
check(f"every red card is excluded ({len(RED) - len(missing)}/{len(RED)})", missing, [])

# Every lane, not this one. The SNK lane's `card_language <> 'en'` filter hides
# all thirteen today only because all thirteen happen to be English -- an
# accident of the data, which is not a ruling and would not survive somebody
# widening that filter.
import snk_identity_discover as S  # noqa: E402

snk_conn = _Conn()
S.select_targets(snk_conn, "036_x", 1000, "one-piece", 0)
snk_sql, snk_params = snk_conn.calls[-1]
truthy("the SNK lane also excludes ids by list", "v.id NOT IN (" in snk_sql)
snk_missing = [vid for vid in RED if vid not in snk_params]
check(f"the SNK lane excludes every red card ({len(RED) - len(snk_missing)}/{len(RED)})",
      snk_missing, [])
check("both lanes read the same derivation", S.R.red_listed_variants(), RED)


# --- 3d. the card's number names a set, and that set has a page too --------
# One Piece reprints a card into a later product without renumbering it, so the
# set the card was pulled from and the set its number names are two different
# pages. Measured 2026-08-09: reading only the first cost 22 of 59 holds.
#
# The number's set is named by Limitless -- `op02-paramount-war` is one string
# carrying both halves, written by the people who publish the product. A map
# built by majority over catalog rows used to answer here instead, and on a
# reprint set_code and set_name in the same row describe different products:
# measured 2026-08-10 it answered ST01 with "Awakening of the New Era" (that is
# OP05) and OP02 with "Two Legends" (that is OP08). Limitless spells a product
# without the "One Piece" prefix, so this is also the check that match_console
# recognises the page from that shorter spelling.
INDEX2 = dict(INDEX)
INDEX2["one-piece-two-legends"] = "one piece two legends"
INDEX2["one-piece-emperors-in-the-new-world"] = "one piece emperors in the new world"

reprint = {"set_name": "One Piece Emperors in the New World", "card_language": "en",
           "collector_number": "OP08-106"}
cands = D.console_candidates(reprint, INDEX2)
check("a reprint is looked for on two pages", len(cands), 2)
# Indexed defensively: a regression here should print its own failure line, not
# take the other sixty checks down with an IndexError.
first = cands[0] if cands else ("", "", "")
second = cands[1] if len(cands) > 1 else ("", "", "")
check("the set it was pulled from comes first", first[0],
      "one-piece-emperors-in-the-new-world")
check("the set its number names comes second", second[0], "one-piece-two-legends")
# Judged against the set whose page it is, or product_agrees refuses every row
# on that page for saying "Two Legends" where our catalog said "Emperors".
check("and it is judged against that page's own set", second[1], "two legends")
check("the record says which reading found it", second[2], "collector_number")

# A card whose number names the set it is already filed under is looked for
# once. The two spellings of that set do not compare equal -- Limitless drops
# the "One Piece" -- so it is the SLUG they both resolve to that collapses
# them, which is the only place they can be known to be the same page.
same = dict(reprint, collector_number="OP09-050")
check("no second page when both readings agree",
      [c[0] for c in D.console_candidates(same, INDEX2)],
      ["one-piece-emperors-in-the-new-world"])
# Widening where we look must not widen what we accept: OP08-106 is Nami and
# the OP08 page's OP08-052 is Portgas D. Ace, on the same page, still refused.
ok, why = D.judge_listing(
    {"tcg_code": "one-piece", "collector_number": "OP08-106", "fp_name": "Nami",
     "canonical_name": "2024 One Piece OP09-Emperors in the New World Nami"
                       " Special Alternate Art 106",
     "set_name": "One Piece Emperors in the New World", "fp_parallel": "Alternate Art",
     "parallel_code": "aa", "printing_code": "aa"},
    listing(title="Portgas.D.Ace [Alternate Art] OP08-052",
            slug="portgas-d-ace-alternate-art-op08-052",
            url="https://www.pricecharting.com/game/one-piece-two-legends/"
                "portgas-d-ace-alternate-art-op08-052"),
    "One Piece Two Legends")
check("another card on the number's own page is still refused", ok, False)


# --- 3e. a page with no bracket is the base print --------------------------
# Measured 2026-08-09 across all 928 exact PriceCharting bindings: seven were a
# parallel card sitting on the base print's page, priced as the cheap card, and
# one of them (OP05 Yamato Special Alternate Art on "Yamato OP01-121") was also
# holding that page against the base card's own variant. The catalog said so
# the whole time in printing_code; only parallel_code was being read.
import rebuild_036 as RB  # noqa: E402

check("a bare heading cannot be a Special Alternate Art card",
      RB._pc_print_signature_ok("", {"parallel_code": "sec", "printing_code": "sp"}), False)
check("a bare heading cannot be an Alternate Art card",
      RB._pc_print_signature_ok("", {"parallel_code": "sr", "printing_code": "aa"}), False)
check("a bare heading is still the base card",
      RB._pc_print_signature_ok("", {"parallel_code": "sec", "printing_code": "base"}), True)
# A catalog that claims no treatment keeps the old behaviour: rarity-only
# parallel vocabulary on a bracket-less page stays acceptable.
check("no claim in the catalog leaves the page unchallenged",
      RB._pc_print_signature_ok("", {"parallel_code": "sec", "printing_code": ""}), True)
check("the bracket path is untouched",
      RB._pc_print_signature_ok("SP", {"parallel_code": "sr-spc", "printing_code": "sp"}), True)
check("and [SP Foil] is still a different product",
      RB._pc_print_signature_ok("SP Foil", {"parallel_code": "sr-spc", "printing_code": "sp"}),
      False)


# --- 3f. the promoting gate reads the number's set too ---------------------
# Both readings have to reach whoever judges the product, not just the lane
# that finds the page: on 2026-08-09 reverify refused three cards this lane had
# just proposed, for saying "Awakening of the New Era" where the page said the
# set the card's own number names.
check("both readings are offered to whoever judges the product",
      RB.set_names_a_card_could_carry(reprint),
      ["One Piece Emperors in the New World", "two legends"])
# Naming the set the card is already filed under is not a second page: this
# offers Limitless's spelling of the same product, and product_agrees judges a
# page against both. The page count is settled a step later, by the slug.
check("a card whose number names its own set offers that set again",
      RB.set_names_a_card_could_carry(same),
      ["One Piece Emperors in the New World", "emperors in the new world"])


# --- 3g. the canonical map has exactly one writer --------------------------
# This lane used to append its proposals straight onto the canonical map. The
# DB write beside it is an upsert on a key, so the DB stayed clean while the
# file grew a second live row for 26 cards; the morning collect lane died on
# "canonical PC map has duplicate active variants" and nobody saw it for a day.
# Two facts keep it dead: the lane writes somewhere else, and the file itself
# still holds one row per variant.
import consolidate_pc_map as CM  # noqa: E402

check("proposals do not go where approved bindings live",
      D.PROPOSAL_MAP_PATH == CM.CANONICAL, False)
truthy("proposals go somewhere the consolidator will look for transport",
       D.PROPOSAL_MAP_PATH in set(
           ROOT.glob("data/runtime/private-source-map/c11_pc_ebay_map_full900_shard*.jsonl")))
if CM.CANONICAL.is_file():
    seen: dict[int, int] = {}
    for line in CM.CANONICAL.read_text(encoding="utf-8-sig").splitlines():
        if line.strip():
            variant_id = int(json.loads(line).get("variant_id") or 0)
            seen[variant_id] = seen.get(variant_id, 0) + 1
    check("the canonical map holds one row per variant",
          sorted(v for v, n in seen.items() if n != 1), [])
    check("and no row without a variant", 0 in seen, False)


# --- 3h. the gate reads the product's page, not the folder's first file ----
# v2026 keeps both captures a lane can leave behind: the product page, and the
# search-results page saved while looking for it. Alphabetical order picks the
# search page, whose canonical URL is a query -- which is why reverify called
# five cards `page_parse_failed: canonical_not_product` on 2026-08-10 while
# their product pages sat beside them.
PAGES_DIR = ROOT / "data" / "private" / "pricecharting_session" / "html" / "full900"
_product_page = PAGES_DIR / "2026_shanks-magazine-op09-001_r.html"
_search_page = PAGES_DIR / "2026_search-products-q-one-piece-Shanks-001-type-prices.html"
if _product_page.is_file() and _search_page.is_file():
    _order = sorted(PAGES_DIR.glob("2026_*.html"))
    check("order still puts the search page ahead of the product's page",
          _order.index(_search_page) < _order.index(_product_page), True)
    # Asserted on what the capture SAYS, not on its filename: a lane may save
    # the same page under the product id (2026_10032135.html appeared on
    # 2026-08-10 beside the slug-named one) and both are the right answer,
    # while the search page -- the one alphabetical order hands over -- parses
    # to `canonical_not_product` and never is.
    _chosen = RB.pc_capture_for_product(PAGES_DIR, 2026, "10032135")
    _identity, _why = RB._pc_page_identity(
        _chosen.read_text(encoding="utf-8", errors="replace") if _chosen else ""
    )
    check("but the product's own page is what gets judged",
          (_identity or {}).get("canonicalUrl", _why),
          "https://www.pricecharting.com/game/one-piece-promo/shanks-magazine-op09-001")
    # No capture is this product's page: hand back a real one anyway so the
    # caller can say WHICH product it found instead of "page=?".
    truthy("an uncaptured product still names what was on disk",
           RB.pc_capture_for_product(PAGES_DIR, 2026, "999999999") is not None)
    check("a variant with no captures at all is missing",
          RB.pc_capture_for_product(PAGES_DIR, 99999999, "10032135"), None)


# --- 4. the page-size constant is the page's, not ours ---------------------
check("console page size matches what the form asks for", D.CONSOLE_PAGE_SIZE, 150)
if PAGE.is_file():
    html = PAGE.read_text(encoding="utf-8", errors="replace")
    truthy("the served page really does hand back that cursor",
           f'name="cursor" value="{D.CONSOLE_PAGE_SIZE}"' in html)


for line in FAILED:
    print(line)
print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
raise SystemExit(1 if FAILED else 0)
