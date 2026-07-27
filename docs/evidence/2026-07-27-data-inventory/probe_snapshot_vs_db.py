#!/usr/bin/env python3
"""量度 live 公開 snapshot 同 DB 之間嘅落差。

呢個係整個盤點嘅決定性量度：`data/public/seed-snapshot.json` 嘅 `opaque_id`
有幾多仲存在於 live `catalog_variant`。如果大部分已死，即係「數據未組裝」
唔係 ingest 問題，而係 snapshot 世代同 catalog 世代錯位。

唯讀。只讀 snapshot 檔 + 三句 SELECT。

    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-data-inventory/probe_snapshot_vs_db.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import db_config  # noqa: E402

import pymysql  # noqa: E402

SNAPSHOT = ROOT / "data" / "public" / "seed-snapshot.json"


def main() -> int:
    doc = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    gen = doc.get("generation") or {}
    print(f"snapshot generatedAt = {gen.get('generatedAt')}")
    print(f"snapshot blockers    = {gen.get('blockers')}")

    top = doc.get("top100") or []
    watch = doc.get("watchlist") or []
    ids = [r["id"] for r in top if r.get("id")]
    ids += [r["id"] for r in watch if isinstance(r, dict) and r.get("id")]

    conn = pymysql.connect(**db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SELECT opaque_id FROM catalog_variant")
            live = {row["opaque_id"] for row in cur.fetchall()}
            cur.execute(
                """SELECT v.opaque_id, l.locale_code FROM catalog_variant v
                   JOIN catalog_variant_locale l ON l.variant_id = v.id
                   WHERE l.market_story IS NOT NULL AND l.market_story <> ''"""
            )
            stories: dict[str, set[str]] = {}
            for row in cur.fetchall():
                stories.setdefault(row["opaque_id"], set()).add(row["locale_code"])
            cur.execute(
                """SELECT v.opaque_id FROM catalog_variant v
                   JOIN market_image_asset a ON a.variant_id = v.id"""
            )
            images = {row["opaque_id"] for row in cur.fetchall()}
            cur.execute(
                """SELECT v.opaque_id FROM catalog_variant v
                   JOIN market_grader_population_observation p ON p.variant_id = v.id
                   WHERE p.grader_code = 'TAG'"""
            )
            tag = {row["opaque_id"] for row in cur.fetchall()}
    finally:
        conn.close()

    alive = [i for i in ids if i in live]
    dead = len(ids) - len(alive)
    print()
    print(f"snapshot 總 id                 = {len(ids)}  (top100 {len(top)} + watchlist {len(watch)})")
    print(f"仲存在於 live catalog_variant  = {len(alive)}")
    print(f"已死 opaque_id                 = {dead}  ({100 * dead // max(len(ids), 1)}%)")
    print(f"live catalog_variant 總數      = {len(live)}")
    print()
    for label, subset in (("top100", [r["id"] for r in top if r.get("id")]),
                          ("watchlist", [r["id"] for r in watch if isinstance(r, dict) and r.get("id")])):
        if not subset:
            continue
        print(
            f"{label:<10} DB 有 en 故事={sum(1 for i in subset if 'en' in stories.get(i, ())):>4}  "
            f"四語齊={sum(1 for i in subset if len(stories.get(i, ())) >= 4):>4}  "
            f"有圖={sum(1 for i in subset if i in images):>4}  "
            f"有 TAG POP={sum(1 for i in subset if i in tag):>4}"
        )

    cov = doc.get("coverage") or {}
    print()
    print("snapshot 自報 coverage:")
    for key in ("localizedStoryCount", "changeReady", "graderPopulationReady",
                "graderPopulationChangeReady", "salesReady", "completeIdentityCount"):
        if key in cov:
            print(f"  {key} = {json.dumps(cov[key], ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
