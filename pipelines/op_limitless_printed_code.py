#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the PRINTED set code of a One Piece card from Limitless.

The gap this closes, measured on 2026-08-10
-------------------------------------------
Every one of the 123 One Piece cards sitting at `qualified_market_pending` was
held for the same reason: no exact PriceCharting or SNKRDUNK binding. Both
discovery lanes had found the right provider listing and then thrown it away
with `hard_conflict:set_code`. They were comparing against the wrong number.

GemRate names the PRODUCT a card was sold in. The card prints the code of the
set it FIRST appeared in. An alternate-art Kaido pulled from the OP05 booster
is filed by GemRate under "OP05-Awakening of the New Era 044" and prints
`OP04-044`; SNKRDUNK and PriceCharting both list it as OP04-044, so a rule that
demands `op05` refuses the only listing that could ever be this card. The
catalog carries no column with the printed code -- verified: the GemRate slug
is redundant with set_name, the raw payload never prints it, and snkrdunk
carries it only for cards that already shipped.

Limitless publishes exactly the missing fact. A product page lists every card
in that product under its printed code -- `/cards/jp/OP05` carries `OP04-044`
and `OP02-120` beside the OP05-xxx cards -- so the product page IS the reprint
map, and it is public, server-rendered, and needs no browser.

What this writes, and what it deliberately does not
---------------------------------------------------
Output is a policy file, `data/policy/op-printed-codes.json`, read by
`op_identity_rules.printed_set_codes`. It is an INPUT CORRECTION, not a new
binding and not a relaxed gate: the discovery lanes still have to agree on
character, number, language, treatment, tcg and mirror before anything binds.
All this changes is which set code counts as "ours".

It refuses rather than guesses. A number that appears under two prefixes in the
same product (OP05 carries both `OP05-044` and the `OP04-044` reprint) is only
resolved when the card NAME settles it, read from the English list view; a card
whose product is not on Limitless, or whose number is not in that product, is
held with a reason and no entry is written.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import rebuild_036 as R
from op_identity_rules import SET_CODE_RE

ROOT = R.ROOT
CACHE_DIR = ROOT / "data" / "private" / "limitless"
POLICY_PATH = ROOT / "data" / "policy" / "op-printed-codes.json"
BASE = "https://onepiece.limitlesstcg.com"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like"
    " Gecko) Chrome/126.0.0.0 Safari/537.36"
)
MISSING = "__missing__"

ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
CELL_RE = re.compile(
    r'<a[^>]*href="/cards/(?:(?:jp|en)/)?([A-Za-z0-9]+-\d+)(?:\?v=\d+)?"[^>]*>(.*?)</a>',
    re.S,
)
INDEX_RE = re.compile(
    r'href="(/cards/(?:jp/)?[a-z0-9][a-zA-Z0-9\-]*)"[^>]*>(.*?)</a>', re.S
)


def progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def norm(text: str) -> str:
    """Compare names the way a human reads them, not the way they are typed.

    Limitless writes "Monkey.D.Luffy", GemRate writes "Monkey D. Luffy", and
    the answer to "is this the same character" must not depend on the dots.
    """

    text = unicodedata.normalize("NFKC", html.unescape(text or "")).casefold()
    return re.sub(r"[^a-z0-9]+", "", text)


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------
def fetch(path: str, *, refresh: bool = False) -> str | None:
    """GET a Limitless page, through an on-disk cache.

    A 404 is cached too. Limitless has ~140 products and this lane re-runs
    every generation; re-asking for a page that does not exist is the kind of
    politeness cost that turns into a rate limit.
    """

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = CACHE_DIR / (re.sub(r"[^A-Za-z0-9]+", "_", path) + ".html")
    if key.is_file() and not refresh:
        body = key.read_text(encoding="utf-8", errors="replace")
        return None if body == MISSING else body
    request = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 -- a missing product is data
        progress(f"  fetch miss {path}: {error}")
        key.write_text(MISSING, encoding="utf-8")
        return None
    key.write_text(body, encoding="utf-8")
    time.sleep(0.35)
    return body


def list_rows(path: str) -> list[tuple[str, str]]:
    """[(printed code, card name)] from a product's list view."""

    body = fetch(f"{path}?display=list")
    if body is None:
        return []
    out: list[tuple[str, str]] = []
    for block in ROW_RE.findall(body):
        cells = CELL_RE.findall(block)
        if len(cells) < 2:
            continue
        name = html.unescape(re.sub(r"<[^>]+>", "", cells[1][1])).strip()
        out.append((cells[0][0].upper(), name))
    return out


def index_products(path: str, *, lang_prefixed: bool) -> dict[str, list[str]]:
    """slug -> the labels Limitless prints beside it (code, name, date, size)."""

    body = fetch(path)
    if body is None:
        return {}
    groups: dict[str, list[str]] = {}
    for slug, rest in INDEX_RE.findall(body):
        if slug in ("/cards/promos", "/cards/advanced"):
            continue
        if lang_prefixed != slug.startswith("/cards/jp/"):
            continue
        label = html.unescape(re.sub(r"<[^>]+>", "", rest)).strip()
        if label:
            groups.setdefault(slug, []).append(label)
    return groups


# --------------------------------------------------------------------------
# catalogue
# --------------------------------------------------------------------------
class Catalogue:
    """Every Limitless product, by the two keys GemRate might name it with.

    GemRate spells the set as a code ("OP05-Awakening of the New Era") about as
    often as it spells only the name ("One Piece Carrying On His Will"), and for
    the promo products it only ever spells the name. Both keys are built here so
    a caller never has to know which form it holds.
    """

    def __init__(self) -> None:
        self.by_code: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        self.products: dict[str, dict[str, Any]] = {}
        self.english_name: dict[str, str] = {}

    def load(self) -> None:
        for slug, labels in index_products("cards/jp", lang_prefixed=True).items():
            code = (labels[0] if labels else "").upper()
            name = labels[1] if len(labels) > 1 else ""
            key = slug.rsplit("/", 1)[1]
            self.products[key] = {
                "slug": key, "code": code, "name": name, "kind": "set",
            }
            if code:
                self.by_code[code] = key
            if name:
                self.by_name[norm(name)] = key
        for slug, labels in index_products("cards/promos", lang_prefixed=False).items():
            key = slug.rsplit("/", 1)[1]
            name = labels[0] if labels else ""
            self.products[key] = {
                "slug": key, "code": "", "name": name, "kind": "promo",
            }
            if name:
                self.by_name.setdefault(norm(name), key)
        progress(f"[limitless] {len(self.products)} products indexed")

    def resolve_product(self, set_name: str) -> tuple[str, str]:
        """Which Limitless product is this GemRate set name? (slug, how)

        Tried in order of how much is being assumed. The code is exact. The
        name is exact once the words GemRate adds to every row are removed --
        but they are removed one layer at a time, because "Japanese" is noise
        in "One Piece Japanese OP05-…" and load-bearing in the product actually
        called "Japanese 3rd Anniversary Set".
        """

        match = SET_CODE_RE.search((set_name or "").upper())
        if match and match.group(1).upper() in self.by_code:
            return self.by_code[match.group(1).upper()], "code"
        keys = [norm(set_name or "")]
        for pattern in (r"(?i)\bone piece\b",
                        r"(?i)\bone piece\b|\bcard game\b",
                        r"(?i)\bone piece\b|\bcard game\b|\bjapanese\b|\benglish version\b"):
            keys.append(norm(re.sub(pattern, " ", set_name or "")))
        for key in keys:
            if key and key in self.by_name:
                return self.by_name[key], "name"
        # "One Piece Carrying On His Will OP-13" and "…-Best Selection Vol.4 -"
        # both carry the product name with something welded on either side.
        best, chosen = "", ""
        for key in keys:
            for name_key, slug in self.by_name.items():
                if len(name_key) >= 8 and name_key in key and len(name_key) > len(best):
                    best, chosen = name_key, slug
        if best:
            return chosen, "name_contains"
        return "", ""

    def promo_slugs(self) -> list[str]:
        return [slug for slug, meta in self.products.items() if meta["kind"] == "promo"]

    def rows(self, slug: str, lang: str) -> list[tuple[str, str]]:
        meta = self.products.get(slug) or {}
        if meta.get("kind") == "promo":
            return list_rows(f"cards/{slug}")
        code = meta.get("code") or slug.upper()
        return list_rows(f"cards/{lang}/{code}")

    def name_of(self, printed: str) -> str:
        """The English card name for a printed code, learned lazily.

        The Japanese list view prints カイドウ, which no rule here can compare
        against "Kaido"; the English view of the same product prints both the
        reprints and their English names, and the codes are shared between the
        two views. So the English view is the dictionary.
        """

        if printed in self.english_name:
            return self.english_name[printed]
        prefix = printed.rpartition("-")[0]
        slug = self.by_code.get(prefix.upper())
        if slug:
            for code, name in self.rows(slug, "en"):
                self.english_name.setdefault(code, name)
        return self.english_name.get(printed, "")


# --------------------------------------------------------------------------
# resolution
# --------------------------------------------------------------------------
def collector_number(raw: str) -> str:
    """The printed number alone, zero-padded the way Limitless prints it.

    Some rows carry a whole code in the number column ("EB02-010"); the number
    is the part after the dash, and the prefix in there is exactly the claim
    this module exists to re-derive, so it is dropped rather than trusted.
    """

    text = str(raw or "").strip()
    embedded = re.fullmatch(r"([A-Za-z]+\d*)-(\d+)", text)
    if embedded:
        text = embedded.group(2)
    return text.zfill(3) if text.isdigit() else text


def _name_matches(theirs: str, ours_normalised: str) -> bool:
    """Does their card name appear in our card label?

    The emptiness guard is the whole point. `norm` keeps only [a-z0-9], so a
    Japanese name ("モーリー") normalises to the empty string, and "" is a
    substring of everything: without this check every Japanese row matched
    every card and 21 resolvable cards were reported as ambiguous instead.
    Two characters is the shortest real One Piece card name.
    """

    key = norm(theirs)
    return len(key) >= 2 and key in ours_normalised


def promo_scan(catalogue: Catalogue, entry: dict[str, Any]) -> dict[str, Any]:
    """Last resort for the cards GemRate files under one word: "Promos".

    Nineteen of the gap cards name no product at all -- GemRate buckets every
    event pack, magazine insert and campaign card into "One Piece Promos". No
    product page can be looked up for them, so instead every promo product on
    Limitless is read and the card is looked for by number AND name together.
    Weaker evidence than "the product GemRate named contains this number", so
    it is recorded as such and it still refuses on more than one survivor.
    """

    number = entry["collectorNumber"]
    ours = norm(entry["canonicalName"])
    found: dict[str, set[str]] = {}
    for slug in catalogue.promo_slugs():
        for printed, name in catalogue.rows(slug, "en"):
            if printed.rpartition("-")[2] != number:
                continue
            if _name_matches(name, ours) or _name_matches(catalogue.name_of(printed), ours):
                found.setdefault(printed, set()).add(slug)
    if not found:
        entry["reason"] = "product_not_on_limitless"
        return entry
    if len(found) > 1:
        entry["reason"] = "ambiguous_promo:" + ",".join(sorted(found))
        return entry
    printed = next(iter(found))
    entry["printedCode"] = printed
    entry["product"] = sorted(found[printed])[0]
    entry["productMatchedBy"] = "promo_scan"
    entry["provenBy"] = "promo_scan"
    entry["reprint"] = True
    entry["evidenceUrl"] = f"{BASE}/cards/{entry['product']}?display=list"
    return entry


def resolve(catalogue: Catalogue, row: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "variantId": int(row["id"]),
        "cardLanguage": row.get("card_language") or "",
        "setName": row.get("set_name") or "",
        "collectorNumber": collector_number(row.get("collector_number") or ""),
        "canonicalName": row.get("canonical_name") or "",
    }
    lang = {"ja": "jp", "en": "en"}.get(entry["cardLanguage"])
    if lang is None:
        entry["reason"] = f"language_not_on_limitless:{entry['cardLanguage']}"
        return entry
    slug, how = catalogue.resolve_product(entry["setName"])
    if not slug:
        return promo_scan(catalogue, entry)
    entry["product"] = slug
    entry["productMatchedBy"] = how
    rows = catalogue.rows(slug, lang) or catalogue.rows(slug, "en")
    if not rows:
        entry["reason"] = f"product_page_empty:{slug}"
        return entry
    number = entry["collectorNumber"]
    hits = sorted({
        (printed, name) for printed, name in rows
        if printed.rpartition("-")[2] == number
    })
    if not hits:
        entry["reason"] = f"number_not_in_product:{slug}/{number}"
        return entry
    prefixes = {printed.rpartition("-")[0] for printed, _ in hits}
    if len(prefixes) == 1:
        entry["printedCode"] = f"{sorted(prefixes)[0]}-{number}"
        entry["provenBy"] = "sole_number_in_product"
    else:
        ours = norm(entry["canonicalName"])
        named = {
            printed for printed, name in hits
            if _name_matches(name, ours) or _name_matches(catalogue.name_of(printed), ours)
        }
        if len(named) != 1:
            entry["reason"] = "ambiguous:" + ",".join(
                f"{printed}({name})" for printed, name in hits)
            return entry
        entry["printedCode"] = sorted(named)[0]
        entry["provenBy"] = "card_name"
    product_code = (catalogue.products.get(slug) or {}).get("code", "")
    entry["reprint"] = entry["printedCode"].rpartition("-")[0] != product_code.upper()
    entry["evidenceUrl"] = (
        f"{BASE}/cards/{slug}?display=list"
        if (catalogue.products.get(slug) or {}).get("kind") == "promo"
        else f"{BASE}/cards/{lang}/{product_code}?display=list"
    )
    return entry


# --------------------------------------------------------------------------
# targets
# --------------------------------------------------------------------------
def latest_generation(conn: Any) -> str:
    with conn.cursor() as cursor:
        cursor.execute(
            "SELECT generation_id FROM catalog_rebuild_member"
            " ORDER BY computed_at DESC LIMIT 1"
        )
        row = cursor.fetchone()
    if not row:
        raise SystemExit("no catalog_rebuild_member rows: nothing to resolve against")
    return str(row["generation_id"])


def select_targets(conn: Any, generation: str, only_gap: bool) -> list[dict[str, Any]]:
    sql = """
        SELECT v.id, v.card_language, v.set_name, v.collector_number,
               v.canonical_name, rm.latest_psa10_population AS pop
          FROM catalog_rebuild_member rm
          JOIN catalog_variant v ON v.id = rm.variant_id
         WHERE rm.generation_id = %s
           AND rm.cohort <> 'non_qualified'
           AND v.tcg_code = 'one-piece'
    """
    if only_gap:
        sql += """
           AND NOT EXISTS (
                 SELECT 1 FROM catalog_source_identity si
                  WHERE si.variant_id = v.id
                    AND si.source_code IN ('snkrdunk', 'snk_psa10', 'pricecharting')
                    AND si.match_status = 'exact'
               )
        """
    sql += " ORDER BY rm.latest_psa10_population DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, (generation,))
        return [dict(row) for row in cursor.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--generation", default="")
    parser.add_argument("--all", action="store_true",
                        help="every qualified One Piece card, not only the gap")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--credentials-env", default="")
    args = parser.parse_args()

    conn = R.connect(Path(args.credentials_env) if args.credentials_env
                     else R.DAILY_CREDENTIALS_ENV)
    generation = args.generation or latest_generation(conn)
    targets = select_targets(conn, generation, only_gap=not args.all)
    if args.limit:
        targets = targets[: args.limit]
    progress(f"[limitless] {len(targets)} target cards, generation {generation}")

    catalogue = Catalogue()
    catalogue.load()

    resolved: list[dict[str, Any]] = []
    held: list[dict[str, Any]] = []
    for index, row in enumerate(targets, 1):
        entry = resolve(catalogue, row)
        if entry.get("printedCode"):
            resolved.append(entry)
        else:
            held.append(entry)
        if index % 25 == 0:
            progress(f"  [{index}/{len(targets)}] resolved={len(resolved)} held={len(held)}")

    reasons: dict[str, int] = {}
    for entry in held:
        key = str(entry.get("reason", "")).split(":", 1)[0]
        reasons[key] = reasons.get(key, 0) + 1
    report = {
        "opLimitlessPrintedCode": True,
        "generation": generation,
        "write": bool(args.write),
        "counts": {
            "targets": len(targets),
            "resolved": len(resolved),
            "reprints": sum(1 for e in resolved if e.get("reprint")),
            "held": len(held),
            "heldReasons": dict(sorted(reasons.items(), key=lambda kv: -kv[1])),
        },
        "resolved": resolved,
        "held": held,
    }

    if args.write:
        POLICY_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing: dict[str, Any] = {}
        if POLICY_PATH.is_file():
            existing = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
        # Two tiers, kept apart on purpose. `codes` is what the identity rules
        # are allowed to believe: the product GemRate itself named contained
        # this number, so the printed code is that product page's own claim.
        # `advisory` is the promo scan -- a card looked up across every promo
        # product by number and name because GemRate named no product at all.
        # That is a good lead for a human and NOT evidence a gate may lean on:
        # for a promo, an agreeing set code makes the binder skip the
        # product-name comparison (snk_identity_discover.py:351), which is the
        # only check a promo has left.
        codes = dict(existing.get("codes") or {})
        advisory = dict(existing.get("advisory") or {})
        for entry in resolved:
            record = {
                "printedCode": entry["printedCode"],
                "product": entry["product"],
                "provenBy": entry["provenBy"],
                "reprint": bool(entry.get("reprint")),
                "evidenceUrl": entry["evidenceUrl"],
                "setName": entry["setName"],
                "collectorNumber": entry["collectorNumber"],
                "canonicalName": entry["canonicalName"],
            }
            target = advisory if entry["provenBy"] == "promo_scan" else codes
            target[str(entry["variantId"])] = record
        payload = {
            "contract": "op-printed-code-v1",
            "source": "onepiece.limitlesstcg.com",
            "updatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "codes": dict(sorted(codes.items(), key=lambda kv: int(kv[0]))),
            "advisory": dict(sorted(advisory.items(), key=lambda kv: int(kv[0]))),
        }
        POLICY_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )
        report["policyPath"] = POLICY_PATH.relative_to(ROOT).as_posix()
        report["policyEntries"] = len(payload["codes"])
        report["policyAdvisoryEntries"] = len(payload["advisory"])

    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
