#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover PriceCharting sealed boxes from listing pages — never per-SKU search.

Locked grammar (reversed 2026-08-14, CDP 9333):
  /category/{one-piece|pokemon}-cards
    → /console/{set}  #games_table (150 rows, POST cursor=150 for more cards)
    → /game/{set}/{slug}   slug usually booster-box
  Table Ungraded (span.js-price) is today's market point.
  search-products?q=booster+box is capped at 100 — not a completeness path.
  exclude-hardware = Include Sealed / Exclude Sealed.

Phases:
  1. Walk category → booster-like consoles → box rows (+ ungraded)
  2. Match inventory to catalog SKUs → candidate bind + market observation

Never auto-accept. Search is not implemented on purpose.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import (  # noqa: E402
    accept_commands,
    is_pc_console_page,
    parse_pc_category_consoles,
    parse_pc_games_table,
    pc_own_count,
)
from sealed_pc_inventory_ingest import ingest_inventory  # noqa: E402
from sealed_runtime import HTML_DIR, OUT_DIR, load_env, utc_now  # noqa: E402

CACHE = ROOT / "data" / "runtime" / "sealed" / "pc_pattern"
CATEGORY_URLS = (
    "https://www.pricecharting.com/category/one-piece-cards",
    "https://www.pricecharting.com/category/pokemon-cards",
)
INV_OUT = OUT_DIR / "pc-console-inventory.json"


def _cache_console(slug: str, suffix: str = "") -> Path:
    safe = re.sub(r"[^a-z0-9]+", "-", slug.lower())[:80]
    extra = f"_{suffix}" if suffix else ""
    return CACHE / f"console_{safe}{extra}.html"


def _fetch(url: str, out: Path, timeout_s: int) -> str:
    if out.is_file() and out.stat().st_size > 20000:
        html = out.read_text(encoding="utf-8", errors="replace")
        if "just a moment" not in html.lower():
            return html
    import pricecharting_cf_session as cf

    out.parent.mkdir(parents=True, exist_ok=True)
    cf.cmd_fetch(url, out, headless=True, timeout_s=timeout_s)
    return out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""


def _load_cached_console(slug: str) -> str:
    for path in (_cache_console(slug), _cache_console(slug, "name")):
        if path.is_file() and path.stat().st_size > 20000:
            html = path.read_text(encoding="utf-8", errors="replace")
            if is_pc_console_page(html, slug):
                return html
    if HTML_DIR.is_dir():
        for path in HTML_DIR.glob("*.html"):
            try:
                html = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if is_pc_console_page(html, slug):
                return html
    return ""


def walk_inventory(*, timeout_s: int, refresh: bool) -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    consoles: list[dict] = []
    for i, url in enumerate(("one_piece", "pokemon")):
        html = _fetch(CATEGORY_URLS[i], CACHE / f"cat_{url}.html", timeout_s)
        consoles.extend(parse_pc_category_consoles(html))
    walk = [c for c in consoles if c["kind"] != "skip"]
    boxes: list[dict] = []
    stats: list[dict] = []
    for console in walk:
        slug = console["slug"]
        html = "" if refresh else _load_cached_console(slug)
        if not html:
            html = _fetch(console["url"], _cache_console(slug), timeout_s)
        rows = parse_pc_games_table(html)
        box_rows = [r for r in rows if r["kind"] == "box"]
        if not box_rows:
            html = _fetch(console["url"] + "?sort=name", _cache_console(slug, "name"), timeout_s)
            rows = parse_pc_games_table(html)
            box_rows = [r for r in rows if r["kind"] == "box"]
        for row in box_rows:
            boxes.append({**row, "game": console["game"], "lang": console["lang"], "consoleTitle": console["title"]})
        stats.append(
            {
                "slug": slug,
                "ownCount": pc_own_count(html),
                "parsed": len(rows),
                "boxes": len(box_rows),
                "priced": sum(1 for r in box_rows if r["hasPrice"]),
            }
        )
    doc = {
        "asOf": utc_now(),
        "action": "sealed-pc-console-walk",
        "consolesListed": len(consoles),
        "walked": len(walk),
        "boxesFound": len(boxes),
        "boxesPriced": sum(1 for b in boxes if b.get("hasPrice")),
        "consoleStats": stats,
        "boxes": boxes,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INV_OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return doc


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=int, default=90)
    ap.add_argument("--refresh", action="store_true", help="re-fetch consoles even if cached")
    ap.add_argument("--skip-walk", action="store_true", help="reuse existing inventory JSON")
    ap.add_argument("--skip-ingest", action="store_true", help="walk only, do not write binds")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--inventory", type=Path, default=None)
    args = ap.parse_args()

    load_env()
    inventory_path = args.inventory
    walk_doc: dict[str, Any] | None = None
    if not args.skip_walk:
        walk_doc = walk_inventory(timeout_s=args.timeout, refresh=args.refresh)
        inventory_path = INV_OUT
        print(json.dumps({k: walk_doc[k] for k in walk_doc if k not in ("boxes", "consoleStats")}, ensure_ascii=False, indent=2))
        print(f"wrote {INV_OUT} boxes={walk_doc['boxesFound']}")
    if inventory_path is None:
        inventory_path = INV_OUT if INV_OUT.is_file() else CACHE / "console-inventory.json"
    if args.skip_ingest:
        return 0
    if not inventory_path.is_file():
        print(f"missing inventory {inventory_path}", file=sys.stderr)
        return 2
    ingest = ingest_inventory(inventory_path, dry_run=args.dry_run)
    receipt = {
        "asOf": utc_now(),
        "action": "sealed-pc-discover",
        "walk": None if walk_doc is None else {k: walk_doc[k] for k in walk_doc if k not in ("boxes", "consoleStats")},
        "ingest": {k: v for k, v in ingest.items() if k != "items"},
        "acceptCommands": accept_commands("pricecharting"),
        "items": ingest["items"],
    }
    out = OUT_DIR / "pc-discover-receipt.json"
    out.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in receipt.items() if k != "items"}, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {out} items={len(ingest['items'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
