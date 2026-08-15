#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Verify sealed source-identity candidates against the live source pages.

Candidates come from catalog.json real page URLs + Kimi verified links
(match_status='candidate', resolved=0). This tool:

  pricecharting : fetch /game/... HTML via the CF session, parse VGPC,
                  confirm the page is a real product with an Ungraded series,
                  write a match-hint note. 404 -> match_status='rejected'.
  snkrdunk      : GET product master via the free JSON API, record
                  name/localizedName/productCatalogId into the note.

Resolution never auto-accepts: a human freezes with
  operator_control.py sealed-accept-binding --sku ... --kind source --source-code ...

Usage:
  python -X utf8 pipelines/sealed_bind_resolve.py --source snkrdunk --limit 10
  python -X utf8 pipelines/sealed_bind_resolve.py --source pricecharting --limit 5
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_runtime import HTML_DIR, OUT_DIR, db, load_env, sha, utc_now  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402


def _tokens(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if len(t) >= 3}


def name_overlap(product_name: str, candidate: str) -> float:
    a = _tokens(product_name)
    b = _tokens(candidate)
    if not a:
        return 0.0
    return round(len(a & b) / len(a), 2)


def load_candidates(cur, source: str, *, include_resolved: bool, limit: int | None) -> list[dict]:
    cur.execute(
        """
        SELECT i.source_code, i.external_entity_id, i.sealed_id, i.canonical_url, i.resolved,
               i.match_status, p.sku_id, p.slug, p.group_code, p.lang, p.name_en, p.name_jp, p.set_code
        FROM catalog_sealed_source_identity i
        JOIN catalog_sealed_product p ON p.id = i.sealed_id
        WHERE i.source_code=%s AND i.match_status='candidate'
        ORDER BY i.sealed_id
        """,
        (source,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    if not include_resolved:
        rows = [r for r in rows if not int(r["resolved"] or 0)]
    if limit:
        rows = rows[:limit]
    return rows


def mark(cur, row: dict, *, resolved: int, match_status: str, note: str) -> None:
    cur.execute(
        """
        UPDATE catalog_sealed_source_identity
        SET resolved=%s, match_status=%s, note=%s
        WHERE source_code=%s AND external_entity_id=%s
        """,
        (resolved, match_status, note[:500], row["source_code"], row["external_entity_id"]),
    )


def resolve_pricecharting(cur, rows: list[dict], *, timeout_s: int) -> list[dict]:
    import pricecharting_cf_session as cf  # deferred: needs playwright

    results = []
    for row in rows:
        url = row["canonical_url"] or f"https://www.pricecharting.com/game/{row['external_entity_id']}"
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
        out = HTML_DIR / f"{row['sealed_id']}_{digest}.html"
        item: dict[str, Any] = {"sku": row["sku_id"], "url": url}
        try:
            code = cf.cmd_fetch(url, out, headless=True, timeout_s=timeout_s)
        except Exception as exc:  # noqa: BLE001
            item.update({"status": "fetch_error", "error": f"{type(exc).__name__}:{exc}"})
            results.append(item)
            continue
        if code == 4:
            mark(cur, row, resolved=1, match_status="rejected", note="pc 404 terminal")
            item["status"] = "rejected_404"
            results.append(item)
            continue
        if code != 0:
            item["status"] = "cf_blocked"
            results.append(item)
            continue
        parsed = parse_product_html(out.read_text(encoding="utf-8", errors="replace"), source_url=url)
        if not parsed.get("ok"):
            item["status"] = "parse_failed"
            results.append(item)
            continue
        product = parsed.get("product") or {}
        used = (parsed.get("chart") or {}).get("used") or {}
        jp_expected = row["lang"] == "jp"
        jp_page = "japanese" in url.lower()
        overlap = name_overlap(row["name_en"], str(product.get("name") or "") + " " + url)
        note = json.dumps(
            {
                "pcProductId": product.get("id"),
                "pcName": product.get("name"),
                "usedPoints": used.get("points"),
                "usedLastUsd": used.get("last_usd"),
                "langOk": jp_expected == jp_page,
                "nameOverlap": overlap,
            },
            ensure_ascii=False,
        )
        mark(cur, row, resolved=1, match_status="candidate", note=note)
        item.update({"status": "resolved", "usedPoints": used.get("points"), "langOk": jp_expected == jp_page, "nameOverlap": overlap})
        results.append(item)
    return results


def resolve_snkrdunk(cur, rows: list[dict]) -> list[dict]:
    from snkrdunk_bulk import SnkrdunkApi  # deferred: needs requests

    api = SnkrdunkApi(delay=1.5)
    results = []
    for row in rows:
        ext = row["external_entity_id"]
        match = re.match(r"^(?:trading-cards|apparel-groups|apparels):(\d+)$", ext)
        item: dict[str, Any] = {"sku": row["sku_id"], "ext": ext}
        if not match:
            item["status"] = "unsupported_ext"
            results.append(item)
            continue
        item_id = int(match.group(1))
        try:
            master = api.get_master(item_id)
        except Exception as exc:  # noqa: BLE001
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status == 404:
                mark(cur, row, resolved=1, match_status="rejected", note="snk master 404")
                item["status"] = "rejected_404"
            else:
                item.update({"status": "fetch_error", "error": f"{type(exc).__name__}:{exc}"})
            results.append(item)
            continue
        name = str(master.get("name") or "")
        localized = str(master.get("localizedName") or "")
        combined = f"{name} {localized}".lower()
        card_keywords = ("pokemon", "one piece", "card", "カード", "ポケモン", "トレカ", "box")
        if not any(keyword in combined for keyword in card_keywords):
            mark(cur, row, resolved=1, match_status="rejected",
                 note=f"snk master is not a TCG product: {name[:120]}")
            item.update({"status": "rejected_not_tcg", "snkName": name[:80]})
            results.append(item)
            continue
        note = json.dumps(
            {
                "snkName": name[:120],
                "snkLocalized": localized[:120],
                "productCatalogId": master.get("productCatalogId"),
                "usedMinPrice": master.get("usedMinPrice"),
                "imageUrl": ((master.get("primaryMedia") or {}).get("imageUrl") or "")[:180],
                "nameOverlap": name_overlap(row["name_en"], f"{name} {localized}"),
            },
            ensure_ascii=False,
        )
        mark(cur, row, resolved=1, match_status="candidate", note=note)
        item.update({"status": "resolved", "snkName": name[:80]})
        results.append(item)
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["pricecharting", "snkrdunk", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--include-resolved", action="store_true")
    ap.add_argument("--timeout", type=int, default=90)
    args = ap.parse_args()

    load_env()
    conn = db()
    report: dict[str, Any] = {"asOf": utc_now(), "action": "sealed-bind-resolve", "sources": {}}
    try:
        cur = conn.cursor()
        sources = ["snkrdunk", "pricecharting"] if args.source == "all" else [args.source]
        for source in sources:
            rows = load_candidates(cur, source, include_resolved=args.include_resolved, limit=args.limit)
            if source == "pricecharting":
                results = resolve_pricecharting(cur, rows, timeout_s=args.timeout)
            else:
                results = resolve_snkrdunk(cur, rows)
            conn.commit()
            statuses: dict[str, int] = {}
            for item in results:
                statuses[item["status"]] = statuses.get(item["status"], 0) + 1
            report["sources"][source] = {"attempted": len(rows), "statuses": statuses, "items": results}
    finally:
        conn.close()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "bind-resolve-receipt.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
