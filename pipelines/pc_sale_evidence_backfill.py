#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Give the PriceCharting sale rows already in the DB their eBay evidence back,
then delete the rows that are provably the same sale twice.

Two things happened before migration 042:

  1. Both writers folded the eBay item id, URL and title into a hash and threw
     the originals away, so no row could show which listing it was.
  2. The two writers disagreed on how to spell the price inside the fingerprint
     preimage (six places vs two), so one eBay listing produced two fingerprints
     and UNIQUE KEY (source_code, external_entity_id, transaction_fingerprint)
     let both rows in. Measured 2026-08-11: 65,845 rows against 36,101 distinct
     sales in the saved HTML.

Both are recoverable from the PriceCharting HTML already on this box, without a
single new request: for every sale row on every saved page this script computes
BOTH fingerprint spellings, so it can look up any existing DB row by either one
and hand it back its listing.

Twin rule, deliberately narrow: two rows are the same sale only when they carry
the same eBay item id AND the same product, day and price. That is one physical
eBay listing -- not a heuristic. Rows whose page is no longer on disk keep their
NULL evidence and are never touched; guessing there would be worse than the gap.

Nothing is deleted without an artifact naming every id, kept and deleted.

Usage:
  python -X utf8 pipelines/pc_sale_evidence_backfill.py             # dry run
  python -X utf8 pipelines/pc_sale_evidence_backfill.py --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from pc_sale_identity import pc_sale_fingerprint, pc_sale_price_text  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402

HTML_ROOT = ROOT / "data/private/pricecharting_session/html"
ARTIFACT_DIR = ROOT / "data/runtime/rebuild-036/pc-sale-evidence"
SOURCE_CODE = "pricecharting"


def legacy_two_place_fingerprint(product_id: str, date_text: str, price, itm: str) -> str:
    """rebuild_036's retired preimage, kept only so its rows stay findable."""
    unit = Decimal(str(price)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return hashlib.sha256(
        f"pc|{product_id}|psa|10|{date_text}|{unit}|{itm}".encode("utf-8")
    ).hexdigest()


def load_env() -> None:
    env = ROOT / "data/runtime/config/backend.env"
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().replace("\r", ""))
    os.environ.setdefault("CARDZ_DB_HOST", "127.0.0.1")


def db():
    import pymysql
    from db_runtime import connect_with_retry

    load_env()
    return connect_with_retry(
        lambda: pymysql.connect(
            host=os.environ["CARDZ_DB_HOST"],
            port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
            user=os.environ["CARDZ_DB_USER"],
            password=os.environ["CARDZ_DB_PASSWORD"],
            database=os.environ["CARDZ_DB_NAME"],
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        ),
        label="pc_sale_evidence_backfill",
    )


def scan_local_pages(stats: Counter) -> dict[str, dict[str, Any]]:
    """fingerprint (either spelling) -> the listing behind it."""

    evidence: dict[str, dict[str, Any]] = {}
    for path in sorted(HTML_ROOT.rglob("*.html")):
        stats["pages_seen"] += 1
        parsed = parse_product_html(path.read_text(encoding="utf-8", errors="replace"))
        if not parsed.get("ok"):
            stats["pages_parse_fail"] += 1
            continue
        product_id = (parsed.get("product") or {}).get("id")
        if not product_id:
            stats["pages_no_product"] += 1
            continue
        rows = ((parsed.get("psa10") or {}).get("completed_sales") or {}).get("rows") or []
        if not rows:
            stats["pages_no_sales"] += 1
            continue
        stats["pages_with_sales"] += 1
        for row in rows:
            itm = str(row.get("ebay_itm") or "").strip()
            date_text = str(row.get("date") or "").strip()
            price = row.get("price_usd")
            if not itm or not date_text or not isinstance(price, (int, float)) or price <= 0:
                stats["rows_unusable"] += 1
                continue
            stats["rows_seen"] += 1
            canonical = pc_sale_fingerprint(product_id, date_text, price, itm)
            record = {
                "canonical_fingerprint": canonical,
                "external_entity_id": str(int(product_id)),
                "listing_item_id": itm[:32],
                "listing_url": str(row.get("ebay_url") or "")[:512],
                "listing_title": str(row.get("title") or "")[:255],
                "sold_date": date_text,
                "unit_price_usd": pc_sale_price_text(price),
            }
            for fingerprint in (
                canonical,
                legacy_two_place_fingerprint(str(int(product_id)), date_text, price, itm),
            ):
                evidence[fingerprint] = record
    stats["distinct_sales"] = len({r["canonical_fingerprint"] for r in evidence.values()})
    stats["fingerprints_indexed"] = len(evidence)
    return evidence


def load_db_rows(cur) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT id, variant_id, external_entity_id, transaction_fingerprint,
               CAST(sold_at AS DATE) AS sold_date, unit_price_usd, run_id
        FROM market_sale_observation
        WHERE source_code = %s
        ORDER BY id
        """,
        (SOURCE_CODE,),
    )
    return list(cur.fetchall())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="apply; default is a dry run")
    args = parser.parse_args()

    stats: Counter = Counter()
    evidence = scan_local_pages(stats)

    conn = db()
    try:
        with conn.cursor() as cur:
            rows = load_db_rows(cur)
    finally:
        pass
    stats["db_rows"] = len(rows)

    # --- evidence backfill -------------------------------------------------
    updates: list[tuple[str, str, str, int]] = []
    matched: dict[int, dict[str, Any]] = {}
    for row in rows:
        record = evidence.get(str(row["transaction_fingerprint"]))
        if record is None:
            stats["rows_without_local_page"] += 1
            continue
        matched[int(row["id"])] = record
        updates.append(
            (
                record["listing_item_id"],
                record["listing_url"],
                record["listing_title"],
                int(row["id"]),
            )
        )
    stats["rows_with_evidence"] = len(updates)

    # --- proven twins ------------------------------------------------------
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        record = matched.get(int(row["id"]))
        if record is None:
            continue
        groups[(
            str(row["external_entity_id"]),
            record["listing_item_id"],
            str(row["sold_date"]),
            str(row["unit_price_usd"]),
        )].append(row)

    deletions: list[dict[str, Any]] = []
    recanonicalised: list[dict[str, Any]] = []
    for key, members in groups.items():
        if len(members) == 1:
            record = matched[int(members[0]["id"])]
            if str(members[0]["transaction_fingerprint"]) != record["canonical_fingerprint"]:
                recanonicalised.append({
                    "id": int(members[0]["id"]),
                    "from": str(members[0]["transaction_fingerprint"]),
                    "to": record["canonical_fingerprint"],
                })
            continue
        canonical = matched[int(members[0]["id"])]["canonical_fingerprint"]
        keepers = [r for r in members if str(r["transaction_fingerprint"]) == canonical]
        keeper = keepers[0] if keepers else min(members, key=lambda r: int(r["id"]))
        if not keepers:
            recanonicalised.append({
                "id": int(keeper["id"]),
                "from": str(keeper["transaction_fingerprint"]),
                "to": canonical,
            })
        for row in members:
            if int(row["id"]) == int(keeper["id"]):
                continue
            deletions.append({
                "id": int(row["id"]),
                "keptId": int(keeper["id"]),
                "variantId": int(row["variant_id"]),
                "pcProductId": key[0],
                "ebayItemId": key[1],
                "soldDate": key[2],
                "unitPriceUsd": key[3],
                "deletedFingerprint": str(row["transaction_fingerprint"]),
                "keptFingerprint": canonical,
                "deletedRunId": int(row["run_id"]) if row["run_id"] is not None else None,
            })
    stats["groups"] = len(groups)
    stats["rows_to_delete"] = len(deletions)
    stats["rows_to_recanonicalise"] = len(recanonicalised)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    artifact = ARTIFACT_DIR / f"pc-sale-dedupe-{stamp}.jsonl"
    header = {
        "contract": "pc-sale-dedupe-v1",
        "generatedAt": stamp,
        "applied": bool(args.write),
        "stats": dict(sorted(stats.items())),
    }
    with artifact.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(header, sort_keys=True) + "\n")
        for entry in deletions:
            handle.write(json.dumps({"op": "delete", **entry}, sort_keys=True) + "\n")
        for entry in recanonicalised:
            handle.write(json.dumps({"op": "recanonicalise", **entry}, sort_keys=True) + "\n")

    if args.write:
        with conn.cursor() as cur:
            for offset in range(0, len(updates), 500):
                cur.executemany(
                    "UPDATE market_sale_observation"
                    " SET listing_item_id=%s, listing_url=%s, listing_title=%s"
                    " WHERE id=%s",
                    updates[offset:offset + 500],
                )
            # Acceptance first: uq_metric_history_source points at the sale row,
            # so an orphan here would keep the sale counted on the board after
            # the sale row itself is gone.
            doomed = [entry["id"] for entry in deletions]
            for offset in range(0, len(doomed), 500):
                chunk = doomed[offset:offset + 500]
                placeholders = ",".join(["%s"] * len(chunk))
                cur.execute(
                    "DELETE FROM market_metric_history_acceptance"
                    " WHERE source_record_type='market_sale_observation'"
                    f" AND source_record_id IN ({placeholders})",
                    chunk,
                )
                stats["acceptance_rows_deleted"] += cur.rowcount
                cur.execute(
                    f"DELETE FROM market_sale_observation WHERE id IN ({placeholders})",
                    chunk,
                )
                stats["sale_rows_deleted"] += cur.rowcount
            for offset in range(0, len(recanonicalised), 500):
                cur.executemany(
                    "UPDATE market_sale_observation"
                    " SET transaction_fingerprint=%s WHERE id=%s",
                    [
                        (entry["to"], entry["id"])
                        for entry in recanonicalised[offset:offset + 500]
                    ],
                )
        conn.commit()
    conn.close()

    print(json.dumps({
        "applied": bool(args.write),
        "artifact": str(artifact.relative_to(ROOT).as_posix()),
        **{k: v for k, v in sorted(stats.items())},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
