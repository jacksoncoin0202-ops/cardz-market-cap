#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Triage sealed SKUs with no composed price. Documented-dry needs evidence."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from sealed_runtime import OUT_DIR, db, load_env, utc_now  # noqa: E402


def main() -> int:
    load_env()
    conn = db()
    today = datetime.now(timezone.utc).date()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT p.id, p.sku_id, p.slug, p.group_code, p.status, p.name_en
            FROM catalog_sealed_product p
            WHERE p.status='active'
            ORDER BY p.id
            """
        )
        products = [dict(r) for r in cur.fetchall()]
        cur.execute(
            """
            SELECT sealed_id, MAX(observed_date) AS d
            FROM market_sealed_daily_aggregate
            WHERE composed_price_usd IS NOT NULL
            GROUP BY sealed_id
            """
        )
        priced = {
            int(r["sealed_id"])
            for r in cur.fetchall()
            if r["d"] and (today - r["d"]).days <= 45
        }
        cur.execute(
            """
            SELECT sealed_id, source_code, external_entity_id, canonical_url, note, resolved
            FROM catalog_sealed_source_identity
            WHERE match_status<>'rejected'
            """
        )
        binds: dict[int, list[dict]] = {}
        for row in cur.fetchall():
            binds.setdefault(int(row["sealed_id"]), []).append(dict(row))
        cur.execute(
            """
            SELECT sealed_id, source_code, observation_kind, COUNT(*) AS n
            FROM market_sealed_source_warehouse
            GROUP BY sealed_id, source_code, observation_kind
            """
        )
        warehouse: dict[int, list[str]] = {}
        for row in cur.fetchall():
            warehouse.setdefault(int(row["sealed_id"]), []).append(
                f"{row['source_code']}:{row['observation_kind']}={row['n']}"
            )
        dry = []
        work = []
        for product in products:
            sealed_id = int(product["id"])
            if sealed_id in priced:
                continue
            sources = binds.get(sealed_id) or []
            pc = next((s for s in sources if s["source_code"] == "pricecharting"), None)
            snk = next((s for s in sources if s["source_code"] == "snkrdunk"), None)
            evidence = {
                "sku": product["sku_id"],
                "group": product["group_code"],
                "nameEn": product["name_en"],
                "pc": {
                    "bound": bool(pc),
                    "url": (pc or {}).get("canonical_url"),
                    "resolved": int((pc or {}).get("resolved") or 0),
                },
                "snk": {
                    "bound": bool(snk),
                    "ext": (snk or {}).get("external_entity_id"),
                    "resolved": int((snk or {}).get("resolved") or 0),
                },
                "warehouse": warehouse.get(sealed_id) or [],
            }
            both_checked = bool(pc and snk and int(pc.get("resolved") or 0) and int(snk.get("resolved") or 0))
            if both_checked:
                evidence["status"] = "documented-dry"
                evidence["reason"] = "both_sources_bound_resolved_no_composed_price"
                dry.append(evidence)
            else:
                evidence["status"] = "needs_work"
                evidence["reason"] = "missing_bind_or_unresolved"
                work.append(evidence)
        doc = {
            "asOf": utc_now(),
            "action": "sealed-price-triage",
            "active": len(products),
            "priced": len(priced),
            "unpriced": len(dry) + len(work),
            "documentedDry": len(dry),
            "needsWork": len(work),
            "dry": dry,
            "work": work,
        }
    finally:
        conn.close()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "documented-dry.json"
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in doc.items() if k not in ("dry", "work")}, ensure_ascii=False, indent=2))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
