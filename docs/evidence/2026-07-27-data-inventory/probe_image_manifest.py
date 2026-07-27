#!/usr/bin/env python3
"""量度 manifests/image-qc.json 嘅公開卡圖，同 catalog_variant.opaque_id 對數。

唯讀。只讀 manifest + 檔案系統 + 一句 SELECT。

    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-data-inventory/probe_image_manifest.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import db_config  # noqa: E402

import pymysql  # noqa: E402

MANIFEST = ROOT / "manifests" / "image-qc.json"
PUBLIC_DIR = ROOT / "data" / "public" / "market-assets"


def main() -> int:
    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    entries = doc.get("records") or []
    print(f"manifest generatedAt    = {doc.get('generatedAt')}")

    total = len(entries)
    kinds = Counter()
    public_allowed = 0
    raw_front = 0
    both = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        kinds[row.get("imageKind") or row.get("image_kind")] += 1
        allowed = bool(row.get("publicAllowed"))
        kind = row.get("imageKind") or row.get("image_kind")
        if allowed:
            public_allowed += 1
        if kind == "raw_front":
            raw_front += 1
        if allowed and kind == "raw_front":
            both.append(row)

    print(f"manifest entries        = {total}")
    print(f"  imageKind breakdown   = {dict(kinds)}")
    print(f"  publicAllowed         = {public_allowed}")
    print(f"  raw_front             = {raw_front}")
    print(f"  publicAllowed+raw_front = {len(both)}")

    # 公開檔案係用 contentSha256 命名，唔係 publicId。
    with_file = [
        r for r in both
        if (PUBLIC_DIR / f"{r.get('contentSha256')}.webp").exists()
    ]
    print(f"  ...and file on disk   = {len(with_file)}")

    public_ids = {r.get("publicId") for r in with_file if r.get("publicId")}
    conn = pymysql.connect(**db_config(), cursorclass=pymysql.cursors.DictCursor)
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute("SELECT opaque_id FROM catalog_variant")
            live = {row["opaque_id"] for row in cur.fetchall()}
    finally:
        conn.close()

    matched = public_ids & live
    print(f"  publicId matches live catalog_variant.opaque_id = {len(matched)}")
    print(f"  ORPHAN (file exists, no live variant)           = {len(public_ids - live)}")
    print(f"  std canvas count on disk (all files)            = {len(list(PUBLIC_DIR.glob('*')))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
