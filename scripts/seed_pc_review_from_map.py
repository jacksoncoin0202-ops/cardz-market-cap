#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn a PC-FULL-900 map into manual_review candidates for pc-identity-reverify.

pc_full_shard_runner resolves a card to a PriceCharting product and writes a
map line, but it does not bind: catalog_source_identity rows for PriceCharting
are only ever created by a judged path. pc-identity-reverify is that path --
except it starts from rows that already exist, so a card that never had a
candidate had nowhere to enter.

This seeds the candidate and nothing more. Every row lands as manual_review,
which no acceptance lane reads and the strict source view excludes, so seeding
cannot put a card on the front end. Only pc-identity-reverify can, and only
when the page proves it.

Run:  python -X utf8 scripts/seed_pc_review_from_map.py --map <map.jsonl>
      python -X utf8 pipelines/operator_control.py pc-identity-reverify \
             --map <map.jsonl> --write
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

CONTRACT = "pc-gap-discovery-036-v1"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--credentials-env", dest="credentials_env", type=Path)
    args = parser.parse_args()

    entries: list[dict] = []
    for line in args.map.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        entry = json.loads(line)
        variant_id = int(entry.get("variant_id") or 0)
        product_id = str(entry.get("pc_product_id") or "")
        if variant_id and product_id:
            entries.append({
                "variant_id": variant_id,
                "product_id": product_id,
                "url": str(entry.get("pc_url") or ""),
                "html": str(entry.get("html_path") or entry.get("htmlPath") or ""),
            })

    conn = R.connect(args.credentials_env or R.DAILY_CREDENTIALS_ENV)
    seeded: list[dict] = []
    skipped: list[dict] = []
    try:
        with conn.cursor() as cursor:
            for entry in entries:
                # The table is keyed (source_code, external_entity_id): one PC
                # product belongs to exactly one variant. A product already
                # spoken for is somebody else's binding, never ours to take.
                cursor.execute(
                    "SELECT variant_id, match_status FROM catalog_source_identity"
                    " WHERE source_code='pricecharting' AND external_entity_id=%s",
                    (entry["product_id"],),
                )
                existing = cursor.fetchone()
                if existing:
                    skipped.append({
                        **entry, "reason": "product_already_bound",
                        "boundTo": int(existing["variant_id"]),
                        "status": str(existing["match_status"]),
                    })
                    continue
                cursor.execute(
                    "SELECT 1 FROM catalog_source_identity"
                    " WHERE source_code='pricecharting' AND variant_id=%s",
                    (entry["variant_id"],),
                )
                if cursor.fetchone():
                    skipped.append({**entry, "reason": "variant_already_has_pc"})
                    continue
                seeded.append(entry)

            if args.write and seeded:
                try:
                    for entry in seeded:
                        evidence = {
                            "contract": CONTRACT,
                            "action": "propose",
                            "evidence": {
                                "type": "provider_search_resolution",
                                "canonicalUrl": entry["url"],
                                "path": entry["html"],
                            },
                        }
                        cursor.execute(
                            "INSERT INTO catalog_source_identity (source_code,"
                            " external_entity_id, variant_id, match_status,"
                            " evidence_sha256, bind_evidence_json)"
                            " VALUES ('pricecharting', %s, %s, 'manual_review',"
                            " %s, %s)",
                            (
                                entry["product_id"], entry["variant_id"],
                                R.sha256_bytes(R.canonical_json(evidence)),
                                json.dumps(evidence, ensure_ascii=False, sort_keys=True),
                            ),
                        )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise
    finally:
        conn.close()

    print(json.dumps({
        "seedPcReview": True, "write": bool(args.write), "map": str(args.map),
        "candidates": len(entries), "seeded": len(seeded), "skipped": len(skipped),
        "skippedDetail": skipped[:20],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
