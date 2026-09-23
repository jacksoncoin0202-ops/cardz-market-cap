#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wash walked PriceCharting console inventory into candidate binds + market prices.

Does not auto-accept freezes. Table Ungraded becomes today's PC market point, only for the item a SKU's accepted
source freeze names.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import (  # noqa: E402
    accept_commands,
    compact_note,
    console_key,
    insert_candidate_bind,
    match_pc_inventory,
    parse_pc_usd,
)
from sealed_runtime import (  # noqa: E402
    OUT_DIR,
    db,
    load_env,
    utc_now,
    upsert_sealed_price,
    warehouse_sealed,
)

DEFAULT_INV = ROOT / "data" / "runtime" / "sealed" / "pc_pattern" / "console-inventory.json"
WAVE_RANK = {"wave1": 0, "1st": 0, "std": 1, "": 1, "wave2": 2, "unlimited": 2}


def load_boxes(path: Path) -> dict[str, list[dict]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    by_console: dict[str, list[dict]] = defaultdict(list)
    for box in doc.get("boxes") or []:
        slug = console_key(box.get("console") or "")
        if slug:
            by_console[slug].append(box)
    return by_console


def wave_rank(sku: dict) -> int:
    return WAVE_RANK.get(str(sku.get("print_wave") or "std"), 1)


def ingest_inventory(path: Path, *, dry_run: bool = False) -> dict[str, Any]:
    boxes_by_console = load_boxes(path)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run_key = f"sealed-pc-inventory-{today}"
    conn = db()
    items: list[dict[str, Any]] = []
    statuses: dict[str, int] = {}
    priced = 0
    used_pids: set[str] = set()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.id, p.sku_id, p.game, p.lang, p.group_code, p.set_code, p.name_en,
                   p.print_wave, p.product_kind, p.status,
                   (SELECT h.url FROM catalog_sealed_source_hint h
                     WHERE h.sealed_id=p.id AND h.source_code='pricecharting'
                       AND (h.url LIKE '%/console/%' OR h.url LIKE '%/game/%')
                     LIMIT 1) AS hint_url,
                   (SELECT i.canonical_url FROM catalog_sealed_source_identity i
                     WHERE i.sealed_id=p.id AND i.source_code='pricecharting'
                       AND i.match_status<>'rejected' LIMIT 1) AS pc_url
            FROM catalog_sealed_product p
            WHERE p.status<>'no-box'
            ORDER BY p.id
            """
        )
        skus = sorted((dict(r) for r in cur.fetchall()), key=lambda s: (wave_rank(s), int(s["id"])))
        for sku in skus:
            item: dict[str, Any] = {"sku": sku["sku_id"], "group": sku["group_code"]}
            picked = match_pc_inventory(sku, boxes_by_console, sku.get("hint_url") or sku.get("pc_url") or "")
            pid = str((picked or {}).get("pid") or "")
            if picked and pid and pid in used_pids:
                picked = None
            if not picked:
                item["status"] = "no_match"
                statuses["no_match"] = statuses.get("no_match", 0) + 1
                items.append(item)
                continue
            if pid:
                used_pids.add(pid)
            url = str(picked.get("href") or "")
            ext = url.split("pricecharting.com/game/", 1)[-1].strip("/") if "/game/" in url else ""
            usd = parse_pc_usd(str(picked.get("ungraded") or ""))
            item.update({"url": url, "ext": ext, "ungraded": picked.get("ungraded"), "usd": usd})
            if dry_run:
                item["status"] = "dry_match"
                statuses["dry_match"] = statuses.get("dry_match", 0) + 1
                items.append(item)
                continue
            status = insert_candidate_bind(
                cur,
                source="pricecharting",
                external_id=ext[:191],
                sealed_id=int(sku["id"]),
                url=url,
                note=compact_note(
                    {
                        "pcUrl": url,
                        "origin": "console-inventory",
                        "pid": picked.get("pid"),
                        "ungraded": picked.get("ungraded"),
                    }
                ),
                origin="console-inventory",
            )
            # Price only the item a human accepted for this SKU; a candidate is unreviewed. 2026-09-23 JU EN, frozen
            # on the 1st edition Jungle box, got today's price from its unlimited-box candidate.
            cur.execute(
                """
                SELECT external_entity_id FROM operator_sealed_binding_freeze
                WHERE sealed_id=%s AND freeze_kind='source' AND source_code='pricecharting' AND acceptance_status='accepted'
                """,
                (int(sku["id"]),),
            )
            frozen = cur.fetchone()
            write_price = bool(frozen and unquote(str(frozen["external_entity_id"])) == unquote(ext))
            if write_price:
                warehouse_sealed(
                    cur,
                    sealed_id=int(sku["id"]),
                    source_code="pricecharting",
                    external_entity_id=ext[:191],
                    observation_kind="sealed_pc_console_table",
                    payload={
                        "url": url,
                        "pid": picked.get("pid"),
                        "title": picked.get("title"),
                        "ungraded": picked.get("ungraded"),
                        "usd": usd,
                    },
                    ingest_run_key=run_key,
                )
                if usd is not None:
                    upsert_sealed_price(
                        cur,
                        sealed_id=int(sku["id"]),
                        source_code="pricecharting",
                        price_kind="market",
                        observed_date=today,
                        native_price=usd,
                        native_currency="USD",
                        price_usd=usd,
                        external_entity_id=ext,
                        source_url=url,
                        ingest_run_key=run_key,
                    )
                    priced += 1
                    item["priced"] = True
            item["status"] = status
            statuses[status] = statuses.get(status, 0) + 1
            items.append(item)
        if not dry_run:
            conn.commit()
    finally:
        conn.close()

    return {
        "asOf": utc_now(),
        "action": "sealed-pc-inventory-ingest",
        "dryRun": dry_run,
        "attempted": len(items),
        "statuses": statuses,
        "priced": priced,
        "acceptCommands": accept_commands("pricecharting"),
        "items": items,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", type=Path, default=DEFAULT_INV)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.inventory.is_file():
        print(f"missing inventory {args.inventory}", file=sys.stderr)
        return 2
    load_env()
    doc = ingest_inventory(args.inventory, dry_run=args.dry_run)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "pc-inventory-ingest-receipt.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in doc.items() if k != "items"}, ensure_ascii=False, indent=2))
    print(f"wrote {out} items={len(doc['items'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
