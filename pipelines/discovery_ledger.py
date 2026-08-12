#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full-catalog identity discovery ledger (043).

Every catalog variant gets an explicit discovery_status. Known gaps and inactive
records may not sit outside a cursor forever.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from rebuild_036 import connect, DEFAULT_CREDENTIALS_ENV, DAILY_CREDENTIALS_ENV


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def rebuild_ledger(cursor: Any) -> dict[str, Any]:
    now = _now()
    # Current-universe membership is only is_current=1.
    cursor.execute(
        """
        SELECT v.id AS variant_id,
               CASE WHEN cur.variant_id IS NULL THEN 'inactive' ELSE 'active' END
                 AS catalog_status,
               pi.card_language,
               COALESCE(pc.exact_n, 0) AS pc_exact_n,
               COALESCE(pc.nonexact_n, 0) AS pc_nonexact_n,
               pc.exact_id AS pc_exact,
               COALESCE(snk.exact_n, 0) AS snk_exact_n,
               COALESCE(snk.nonexact_n, 0) AS snk_nonexact_n,
               snk.exact_id AS snk_exact
        FROM catalog_variant v
        LEFT JOIN catalog_printing_identity pi ON pi.variant_id=v.id
        LEFT JOIN (
          SELECT am.variant_id
          FROM market_universe_member am
          INNER JOIN market_universe_lock ul
            ON ul.id=am.universe_lock_id AND ul.is_current=1
        ) cur ON cur.variant_id=v.id
        LEFT JOIN (
          SELECT variant_id,
                 SUM(match_status='exact') AS exact_n,
                 SUM(match_status<>'exact') AS nonexact_n,
                 MAX(CASE WHEN match_status='exact' THEN external_entity_id END) AS exact_id
          FROM catalog_source_identity
          WHERE source_code='pricecharting'
          GROUP BY variant_id
        ) pc ON pc.variant_id=v.id
        LEFT JOIN (
          SELECT variant_id,
                 SUM(match_status='exact') AS exact_n,
                 SUM(match_status<>'exact') AS nonexact_n,
                 MAX(CASE WHEN match_status='exact' THEN external_entity_id END) AS exact_id
          FROM catalog_source_identity
          WHERE source_code IN ('snkrdunk','snk','snk_psa10')
          GROUP BY variant_id
        ) snk ON snk.variant_id=v.id
        """
    )
    rows = list(cursor.fetchall())
    counts: dict[str, int] = {}
    for raw in rows:
        variant_id = int(raw["variant_id"])
        catalog_status = str(raw["catalog_status"])
        language = str(raw.get("card_language") or "")
        pc_exact_n = int(raw["pc_exact_n"] or 0)
        snk_exact_n = int(raw["snk_exact_n"] or 0)
        pc_nonexact_n = int(raw["pc_nonexact_n"] or 0)
        snk_nonexact_n = int(raw["snk_nonexact_n"] or 0)

        if pc_exact_n > 1 or snk_exact_n > 1:
            discovery = "identity_ambiguous"
            blocker = "multiple_exact_bindings"
            pc_status = (
                "ambiguous" if pc_exact_n > 1 else ("exact" if pc_exact_n == 1 else "missing")
            )
            snk_status = (
                "ambiguous" if snk_exact_n > 1 else ("exact" if snk_exact_n == 1 else "missing")
            )
        elif pc_exact_n == 1 or snk_exact_n == 1:
            discovery = "active_exact"
            blocker = None if catalog_status == "active" else "inactive_with_exact_binding"
            pc_status = "exact" if pc_exact_n == 1 else ("nonexact" if pc_nonexact_n else "missing")
            snk_status = (
                "exact" if snk_exact_n == 1 else ("nonexact" if snk_nonexact_n else "missing")
            )
        elif pc_nonexact_n or snk_nonexact_n:
            discovery = "identity_ambiguous"
            blocker = "nonexact_only"
            pc_status = "nonexact" if pc_nonexact_n else "missing"
            snk_status = "nonexact" if snk_nonexact_n else "missing"
        elif catalog_status == "inactive":
            discovery = "inactive_unresolved"
            blocker = "inactive_no_exact_binding"
            pc_status = "missing"
            snk_status = "missing"
        else:
            discovery = "source_not_found"
            blocker = (
                "en_needs_pricecharting_or_snk"
                if language == "en"
                else "non_en_needs_snk"
            )
            pc_status = "missing"
            snk_status = "missing"

        detail = {
            "cardLanguage": language or None,
            "pcExactId": raw.get("pc_exact"),
            "snkExactId": raw.get("snk_exact"),
            "pcExactCount": pc_exact_n,
            "snkExactCount": snk_exact_n,
        }
        cursor.execute(
            """
            INSERT INTO market_identity_discovery_ledger
              (variant_id, catalog_status, discovery_status, pc_status, snk_status,
               blocker_code, detail_json, last_reviewed_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              catalog_status=VALUES(catalog_status),
              discovery_status=VALUES(discovery_status),
              pc_status=VALUES(pc_status),
              snk_status=VALUES(snk_status),
              blocker_code=VALUES(blocker_code),
              detail_json=VALUES(detail_json),
              last_reviewed_at=VALUES(last_reviewed_at),
              updated_at=VALUES(updated_at)
            """,
            (
                variant_id,
                catalog_status,
                discovery,
                pc_status,
                snk_status,
                blocker,
                json.dumps(detail, ensure_ascii=False, sort_keys=True),
                now,
                now,
            ),
        )
        counts[discovery] = counts.get(discovery, 0) + 1
        counts[f"catalog:{catalog_status}"] = counts.get(f"catalog:{catalog_status}", 0) + 1

    cursor.execute("SELECT COUNT(*) AS n FROM market_identity_discovery_ledger")
    total = int((cursor.fetchone() or {}).get("n") or 0)
    cursor.execute("SELECT COUNT(*) AS n FROM catalog_variant")
    catalog = int((cursor.fetchone() or {}).get("n") or 0)
    if total != catalog:
        raise RuntimeError(
            f"discovery ledger incomplete: ledger={total} catalog={catalog}"
        )
    return {"catalog": catalog, "ledger": total, "byStatus": counts}


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild full identity discovery ledger")
    parser.add_argument(
        "--credentials-env",
        type=Path,
        default=DAILY_CREDENTIALS_ENV
        if DAILY_CREDENTIALS_ENV.exists()
        else DEFAULT_CREDENTIALS_ENV,
    )
    args = parser.parse_args()
    conn = connect(args.credentials_env)
    try:
        with conn.cursor() as cur:
            report = rebuild_ledger(cur)
        conn.commit()
    finally:
        conn.close()
    print(json.dumps({"ok": True, **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
