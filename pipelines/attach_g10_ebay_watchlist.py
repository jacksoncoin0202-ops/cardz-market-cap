#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""G10 altxyz (eBay) → 940 watchlist via gemrate_id.

Unmatched G10 altxyz dirs that share gemrate with a watchlist card get
catalog_source_identity(source_code=ebay). Then re-run g10_ebay_ingest.

  python -X utf8 pipelines/attach_g10_ebay_watchlist.py
  python -X utf8 pipelines/attach_g10_ebay_watchlist.py --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
G10_DEFAULT = ROOT.parent / "grade10-scraper" / "data" / "cards"
GEM_RE = re.compile(r"gemrate_id=([0-9a-fA-F]{40})")


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().replace("\r", ""))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql

    load_env()
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ["CARDZ_DB_USER"],
        password=os.environ["CARDZ_DB_PASSWORD"],
        database=os.environ["CARDZ_DB_NAME"],
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def extract_gemrate(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    m = GEM_RE.search(text)
    if m:
        return m.group(1).lower()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        for k in ("gemrate_id", "gemrateId"):
            v = data.get(k)
            if isinstance(v, str) and re.fullmatch(r"[0-9a-fA-F]{40}", v):
                return v.lower()
        for k in ("source", "url"):
            v = data.get(k)
            if isinstance(v, str):
                m = GEM_RE.search(v)
                if m:
                    return m.group(1).lower()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--g10-root", type=Path, default=G10_DEFAULT)
    args = ap.parse_args()

    alt = args.g10_root / "altxyz"
    if not alt.is_dir():
        print(json.dumps({"error": f"missing {alt}"}))
        return 2

    # gemrate -> external dir id
    g10: dict[str, str] = {}
    for d in alt.iterdir():
        if not d.is_dir():
            continue
        gem = None
        for fname in ("populations.json", "asset_info.json", "meta.json"):
            gem = extract_gemrate(d / fname)
            if gem:
                break
        if gem:
            g10[gem] = d.name

    conn = db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT w.variant_id, LOWER(w.gemrate_id) AS gem, w.card_name, w.collector_number
        FROM market_gemrate_psa10_watchlist w
        WHERE w.gemrate_id IS NOT NULL AND w.gemrate_id <> ''
        """
    )
    watch = list(cur.fetchall())
    cur.execute(
        """
        SELECT external_entity_id, variant_id
        FROM catalog_source_identity
        WHERE source_code='ebay'
        """
    )
    existing = {str(r["external_entity_id"]): int(r["variant_id"]) for r in cur.fetchall()}

    planned: list[dict[str, Any]] = []
    for w in watch:
        gem = (w.get("gem") or "").lower()
        if gem not in g10:
            continue
        ext = g10[gem]
        vid = int(w["variant_id"])
        prev = existing.get(ext)
        if prev == vid:
            continue
        planned.append(
            {
                "variantId": vid,
                "externalId": ext,
                "gemrateId": gem,
                "name": w.get("card_name"),
                "collector": w.get("collector_number"),
                "prevVariantId": prev,
            }
        )

    written = 0
    if args.write and planned:
        for row in planned:
            evidence = hashlib.sha256(
                f"ebay:{row['externalId']}:{row['variantId']}:g10_gemrate".encode()
            ).hexdigest()
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                    (source_code, external_entity_id, variant_id, match_status, evidence_sha256)
                VALUES ('ebay', %s, %s, 'exact', %s)
                ON DUPLICATE KEY UPDATE
                    variant_id=VALUES(variant_id),
                    match_status='exact',
                    evidence_sha256=VALUES(evidence_sha256),
                    updated_at=CURRENT_TIMESTAMP
                """,
                (row["externalId"], row["variantId"], evidence),
            )
            written += 1
            # move any existing ebay sales from previous variant
            if row["prevVariantId"] and row["prevVariantId"] != row["variantId"]:
                cur.execute(
                    """
                    UPDATE market_sale_observation
                    SET variant_id=%s
                    WHERE source_code='ebay'
                      AND external_entity_id=%s
                      AND variant_id=%s
                    """,
                    (row["variantId"], row["externalId"], row["prevVariantId"]),
                )
        conn.commit()

    # coverage
    cur.execute(
        """
        SELECT COUNT(DISTINCT w.variant_id) c
        FROM market_gemrate_psa10_watchlist w
        JOIN catalog_source_identity s ON s.variant_id=w.variant_id AND s.source_code='ebay'
        """
    )
    ebay_on_watch = int(cur.fetchone()["c"])
    conn.close()

    summary = {
        "write": args.write,
        "g10AltxyzWithGemrate": len(g10),
        "plannedAttaches": len(planned),
        "written": written,
        "ebayIdentityOnWatchlist": ebay_on_watch,
        "sample": planned[:15],
        "next": "python -X utf8 pipelines/g10_ebay_ingest.py --write --grades PSA_10",
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
