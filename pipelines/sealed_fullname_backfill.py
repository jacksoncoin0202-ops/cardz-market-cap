#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fill catalog_sealed_product full_name_en / full_name_ja.

Priority (names come only from an accepted source bind; a candidate never names a product):
  1. SNK master name + localizedName (bind note or warehouse / harvest)
  2. PriceCharting VGPC.product name (bind note or cached HTML)
  3. Grammar fallback — never NULL
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_discover_lib import grammar_full_name_en, grammar_full_name_ja  # noqa: E402
from sealed_runtime import HTML_DIR, OUT_DIR, db, ensure_sealed_fullname_columns, load_env, utc_now  # noqa: E402
from pricecharting_page_parse import parse_product_html  # noqa: E402


def _note_json(note: str | None) -> dict:
    try:
        doc = json.loads(note or "{}")
    except json.JSONDecodeError:
        return {}
    return doc if isinstance(doc, dict) else {}


def accepted_binds(cur) -> set[tuple[int, str, str]]:
    """(sealed_id, source, external id) of each accepted source bind. Only these may name a product: a candidate
    is unreviewed (2026-09-23 SNK discover proposed a DIESEL T-shirt for BW1B, which would have become its name)."""
    cur.execute(
        """
        SELECT sealed_id, source_code, external_entity_id, acceptance_status FROM operator_sealed_binding_freeze
        WHERE freeze_kind='source'
        """
    )
    return {
        (int(r["sealed_id"]), str(r["source_code"]), str(r["external_entity_id"]))
        for r in cur.fetchall()
        if r["acceptance_status"] == "accepted"
    }


def load_snk_names(cur, accepted: set[tuple[int, str, str]]) -> dict[int, dict[str, str]]:
    cur.execute(
        """
        SELECT sealed_id, external_entity_id, note FROM catalog_sealed_source_identity
        WHERE source_code='snkrdunk' AND match_status<>'rejected'
        """
    )
    out: dict[int, dict[str, str]] = {}
    for row in cur.fetchall():
        if (int(row["sealed_id"]), "snkrdunk", str(row["external_entity_id"])) not in accepted:
            continue
        doc = _note_json(row["note"])
        name = str(doc.get("snkName") or "").strip()
        localized = str(doc.get("snkLocalized") or "").strip()
        if name or localized:
            out[int(row["sealed_id"])] = {"en": name, "ja": localized, "source": "snkrdunk"}
    return out


def load_pc_names(cur, accepted: set[tuple[int, str, str]]) -> dict[int, dict[str, str]]:
    cur.execute(
        """
        SELECT sealed_id, external_entity_id, note, canonical_url FROM catalog_sealed_source_identity
        WHERE source_code='pricecharting' AND match_status<>'rejected'
        """
    )
    out: dict[int, dict[str, str]] = {}
    for row in cur.fetchall():
        if (int(row["sealed_id"]), "pricecharting", str(row["external_entity_id"])) not in accepted:
            continue
        doc = _note_json(row["note"])
        name = str(doc.get("pcName") or "").strip()
        if not name:
            for path in HTML_DIR.glob(f"{int(row['sealed_id'])}_*.html"):
                parsed = parse_product_html(
                    path.read_text(encoding="utf-8", errors="replace"),
                    source_url=row["canonical_url"] or "",
                )
                if parsed.get("ok"):
                    name = str((parsed.get("product") or {}).get("name") or "").strip()
                    if name:
                        break
        if name:
            out[int(row["sealed_id"])] = {"en": name, "ja": "", "source": "pricecharting"}
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    load_env()
    conn = db()
    updated = 0
    sources = {"snkrdunk": 0, "pricecharting": 0, "grammar": 0}
    try:
        cur = conn.cursor()
        ensure_sealed_fullname_columns(cur)
        conn.commit()
        cur.execute(
            """
            SELECT id, sku_id, game, lang, name_en, name_jp, product_kind, print_wave,
                   full_name_en, full_name_ja
            FROM catalog_sealed_product WHERE status<>'no-box'
            """
        )
        products = [dict(r) for r in cur.fetchall()]
        accepted = accepted_binds(cur)
        snk = load_snk_names(cur, accepted)
        pc = load_pc_names(cur, accepted)
        for product in products:
            sealed_id = int(product["id"])
            en = grammar_full_name_en(product)
            ja = grammar_full_name_ja(product)
            source = "grammar"
            if sealed_id in snk:
                if snk[sealed_id]["en"]:
                    en = snk[sealed_id]["en"]
                if snk[sealed_id]["ja"]:
                    ja = snk[sealed_id]["ja"]
                source = "snkrdunk"
            elif sealed_id in pc and pc[sealed_id]["en"]:
                en = pc[sealed_id]["en"]
                source = "pricecharting"
            sources[source] += 1
            if args.dry_run:
                continue
            cur.execute(
                """
                UPDATE catalog_sealed_product
                SET full_name_en=%s, full_name_ja=%s, full_name_source=%s
                WHERE id=%s
                """,
                (en[:512], ja[:512], source, sealed_id),
            )
            updated += 1
        if not args.dry_run:
            conn.commit()
    finally:
        conn.close()

    doc = {
        "asOf": utc_now(),
        "action": "sealed-fullname-backfill",
        "dryRun": args.dry_run,
        "updated": updated,
        "sources": sources,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "fullname-receipt.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(doc, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
