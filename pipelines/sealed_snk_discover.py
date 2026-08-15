#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Discover SNK exact product pages for sealed boxes.

Order (handbook first, no invented API):
  1. Offline match against data/private/snkrdunk_brute/snkrdunk_all.jsonl
     — already harvested via snkrdunk_discover + snkrdunk_bulk.get_master
  2. Leftovers only: documented HTML search /search?keywords=&page=N
     then get_master() to verify name/image

Never auto-accept. Receipt lists bulk-accept commands per group.
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
    compact_note,
    insert_candidate_bind,
    is_kept_box_name,
    norm_lang,
    score_snk_box,
    unbound_skus,
)
from sealed_runtime import OUT_DIR, db, load_env, utc_now, warehouse_sealed  # noqa: E402
from snkrdunk_discover import ITEM_RE, UA  # noqa: E402

HARVEST = ROOT / "data" / "private" / "snkrdunk_brute" / "snkrdunk_all.jsonl"


def load_harvest_boxes() -> list[dict]:
    if not HARVEST.is_file():
        return []
    boxes = []
    with HARVEST.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = row.get("name") or ""
            localized = row.get("localized_name") or ""
            if not is_kept_box_name(f"{name} {localized}"):
                continue
            if not row.get("item_id"):
                continue
            boxes.append(
                {
                    "item_id": int(row["item_id"]),
                    "name": name,
                    "localized": localized,
                    "image": row.get("image_url") or "",
                    "ask": row.get("used_min_price"),
                    "trades": len(row.get("recent_trades") or []),
                    "points": len(row.get("chart_points") or []),
                    "pcid": row.get("product_catalog_id"),
                }
            )
    return boxes


def search_keywords(sku: dict) -> list[str]:
    if norm_lang(sku.get("lang")) == "jp":
        base = (sku.get("name_jp") or sku.get("name_en") or "").strip()
        wave = ""
        if sku.get("print_wave") == "wave1":
            wave = "初版"
        elif sku.get("print_wave") == "wave2":
            wave = "再販"
        return [x for x in (f"{base} BOX", f"{base} {wave} BOX".strip()) if x]
    name = (sku.get("name_en") or "").strip()
    code = (sku.get("set_code") or "").strip()
    return [x for x in (f"{name} booster box", f"{name} {code} EN Box") if x]


def search_ids(session, keyword: str, max_pages: int, delay: float) -> list[int]:
    import time
    import urllib.parse

    import requests

    ids: list[int] = []
    seen: set[int] = set()
    for page in range(1, max_pages + 1):
        url = "https://snkrdunk.com/search?keywords=" + urllib.parse.quote(keyword) + f"&page={page}"
        try:
            resp = session.get(url, timeout=25)
        except requests.RequestException:
            break
        if resp.status_code != 200:
            break
        found = [int(x) for x in dict.fromkeys(ITEM_RE.findall(resp.text))]
        new = [i for i in found if i not in seen]
        for item_id in new:
            seen.add(item_id)
            ids.append(item_id)
        if not found:
            break
        time.sleep(delay)
    return ids


def write_bind(cur, sku: dict, item_id: int, *, name: str, localized: str, image: str, extra: dict) -> str:
    note = compact_note(
        {
            "snkName": name[:120],
            "snkLocalized": localized[:120],
            "imageUrl": (image or "")[:180],
            "nameOverlap": extra.get("overlap"),
            "origin": extra.get("origin"),
        }
    )
    status = insert_candidate_bind(
        cur,
        source="snkrdunk",
        external_id=f"apparels:{item_id}",
        sealed_id=int(sku["id"]),
        url=f"https://snkrdunk.com/apparels/{item_id}",
        note=note,
        origin=str(extra.get("origin") or "snk-discover"),
    )
    if status in ("inserted", "updated"):
        warehouse_sealed(
            cur,
            sealed_id=int(sku["id"]),
            source_code="snkrdunk",
            external_entity_id=f"apparels:{item_id}",
            observation_kind="sealed_snk_discover",
            payload={"name": name, "localizedName": localized, "imageUrl": image, **extra},
            ingest_run_key=f"sealed-snk-discover-{utc_now()[:10]}",
        )
    return status


def match_harvest(skus: list[dict], boxes: list[dict]) -> list[tuple[dict, dict, float]]:
    used: set[int] = set()
    pairs = []
    for sku in skus:
        best = None
        best_score = 0.0
        for box in boxes:
            if box["item_id"] in used:
                continue
            score, reason = score_snk_box(sku, box["name"], box["localized"])
            if reason == "ok" and score > best_score:
                best_score, best = score, box
        if best and best_score >= 0.6:
            used.add(best["item_id"])
            pairs.append((sku, best, best_score))
    return pairs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--skip-search", action="store_true")
    ap.add_argument("--max-pages", type=int, default=2)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    boxes = load_harvest_boxes()
    conn = db()
    items: list[dict[str, Any]] = []
    statuses: dict[str, int] = {}
    try:
        cur = conn.cursor()
        skus = unbound_skus(cur, "snkrdunk")
        if args.limit:
            skus = skus[: args.limit]
        pairs = match_harvest(skus, boxes)
        matched_ids = {int(sku["id"]) for sku, _, _ in pairs}
        for sku, box, score in pairs:
            item = {
                "sku": sku["sku_id"],
                "group": sku["group_code"],
                "itemId": box["item_id"],
                "snkName": box["name"][:80],
                "overlap": score,
                "origin": "harvest",
            }
            if args.dry_run:
                item["status"] = "dry_harvest"
            else:
                item["status"] = write_bind(
                    cur, sku, box["item_id"],
                    name=box["name"], localized=box["localized"], image=box["image"],
                    extra={"overlap": score, "origin": "harvest", "trades": box["trades"], "points": box["points"]},
                )
            statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            items.append(item)
        leftovers = [s for s in skus if int(s["id"]) not in matched_ids]
        if leftovers and not args.skip_search:
            import requests
            from snkrdunk_bulk import SnkrdunkApi

            session = requests.Session()
            session.headers["User-Agent"] = UA
            api = SnkrdunkApi(delay=args.delay)
            for sku in leftovers:
                found = None
                for keyword in search_keywords(sku):
                    for item_id in search_ids(session, keyword, args.max_pages, args.delay)[:8]:
                        try:
                            master = api.get_master(item_id)
                        except Exception:  # noqa: BLE001
                            continue
                        name = str(master.get("name") or "")
                        localized = str(master.get("localizedName") or "")
                        score, reason = score_snk_box(sku, name, localized)
                        if reason == "ok" and score >= 0.6:
                            found = (item_id, name, localized, score, master)
                            break
                    if found:
                        break
                item = {"sku": sku["sku_id"], "group": sku["group_code"], "origin": "search"}
                if not found:
                    item["status"] = "no_match"
                    statuses["no_match"] = statuses.get("no_match", 0) + 1
                    items.append(item)
                    continue
                item_id, name, localized, score, master = found
                image = ((master.get("primaryMedia") or {}).get("imageUrl") or "")
                item.update({"itemId": item_id, "snkName": name[:80], "overlap": score})
                if args.dry_run:
                    item["status"] = "dry_search"
                else:
                    item["status"] = write_bind(
                        cur, sku, item_id, name=name, localized=localized, image=image,
                        extra={"overlap": score, "origin": "search"},
                    )
                statuses[item["status"]] = statuses.get(item["status"], 0) + 1
                items.append(item)
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    by_group: dict[str, int] = {}
    for item in items:
        if item.get("status") in ("inserted", "updated", "dry_harvest", "dry_search"):
            by_group[item["group"]] = by_group.get(item["group"], 0) + 1
    doc = {
        "asOf": utc_now(),
        "action": "sealed-snk-discover",
        "dryRun": args.dry_run,
        "harvestBoxes": len(boxes),
        "attempted": len(items),
        "statuses": statuses,
        "byGroup": by_group,
        "acceptCommands": accept_commands("snkrdunk"),
        "items": items,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "snk-discover-receipt.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in doc.items() if k != "items"}, ensure_ascii=False, indent=2))
    print(f"wrote {out} items={len(items)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
