#!/usr/bin/env python3
"""Derive the true market-cap ranking (Top 300 request) from evaluation 8.

Produces, next to this script:
  top300.csv  - every eval-8 row with metric_status='ready', ranked by
                market_cap_usd desc.  This is the deepest *honest* ranking the
                data supports today; if it is shorter than 300 rows that is a
                price-coverage fact, not a bug.
  gap33.csv   - roster cards whose PSA/gemrate POP is stale (< 2026-07-24) or
                has never been observed.  These are the only cards the POP
                sweep still owes.

Read-only.  Uses the same db_config as scripts/verify_claims.py.

Run:
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-26-top300-derivation/produce_top300.py
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import db_config  # noqa: E402

EVALUATION_ID = 8
FRESH_FLOOR = "2026-07-24"

TOP_SQL = """
SELECT s.variant_id, s.shadow_rank, s.eligible_rank,
       v.tcg_code, v.canonical_name, v.set_name, v.collector_number,
       s.reference_price_usd, s.psa10_population, s.market_cap_usd,
       pop.last_pop_date, px.last_price_date
FROM market_candidate_daily_snapshot s
JOIN catalog_variant v ON v.id = s.variant_id
LEFT JOIN (SELECT variant_id, MAX(observed_date) AS last_pop_date
           FROM market_grader_population_observation
           WHERE grader_code='PSA' AND source_code='gemrate'
           GROUP BY variant_id) pop ON pop.variant_id = s.variant_id
LEFT JOIN (SELECT variant_id, MAX(observed_date) AS last_price_date
           FROM market_price_observation
           GROUP BY variant_id) px ON px.variant_id = s.variant_id
WHERE s.evaluation_id = %s AND s.metric_status = 'ready'
ORDER BY s.market_cap_usd DESC, s.variant_id
"""

GAP_SQL = """
SELECT s.variant_id, v.tcg_code, v.canonical_name, v.set_name, v.collector_number,
       s.metric_status, pop.last_pop_date,
       gi.gemrate_id
FROM market_candidate_daily_snapshot s
JOIN catalog_variant v ON v.id = s.variant_id
LEFT JOIN (SELECT variant_id, MAX(observed_date) AS last_pop_date
           FROM market_grader_population_observation
           WHERE grader_code='PSA' AND source_code='gemrate'
           GROUP BY variant_id) pop ON pop.variant_id = s.variant_id
LEFT JOIN (SELECT variant_id, MIN(external_entity_id) AS gemrate_id
           FROM catalog_source_identity
           WHERE source_code='gemrate'
           GROUP BY variant_id) gi ON gi.variant_id = s.variant_id
WHERE s.evaluation_id = %s
  AND (pop.last_pop_date IS NULL OR pop.last_pop_date < %s)
ORDER BY (pop.last_pop_date IS NULL) DESC, v.set_name, v.collector_number
"""


def main() -> int:
    import pymysql
    from pymysql.cursors import DictCursor

    config = db_config()
    conn = pymysql.connect(charset="utf8mb4", cursorclass=DictCursor, **config)
    try:
        with conn.cursor() as cur:
            cur.execute(TOP_SQL, (EVALUATION_ID,))
            top_rows = cur.fetchall()
            cur.execute(GAP_SQL, (EVALUATION_ID, FRESH_FLOOR))
            gap_rows = cur.fetchall()
    finally:
        conn.close()

    top_path = HERE / "top300.csv"
    with top_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "rank", "variant_id", "tcg", "name", "set", "collector_number",
            "price_usd", "psa10_pop", "market_cap_usd",
            "pop_last_date", "price_last_date", "shadow_rank", "eligible_rank",
        ])
        for rank, row in enumerate(top_rows, start=1):
            writer.writerow([
                rank, row["variant_id"], row["tcg_code"], row["canonical_name"],
                row["set_name"], row["collector_number"],
                row["reference_price_usd"], row["psa10_population"],
                row["market_cap_usd"], row["last_pop_date"],
                row["last_price_date"], row["shadow_rank"], row["eligible_rank"],
            ])

    gap_path = HERE / "gap33.csv"
    never = sum(1 for r in gap_rows if r["last_pop_date"] is None)
    with gap_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "variant_id", "tcg", "name", "set", "collector_number",
            "gap_kind", "pop_last_date", "eval_metric_status", "gemrate_id",
        ])
        for row in gap_rows:
            writer.writerow([
                row["variant_id"], row["tcg_code"], row["canonical_name"],
                row["set_name"], row["collector_number"],
                "never" if row["last_pop_date"] is None else "stale",
                row["last_pop_date"], row["metric_status"], row["gemrate_id"],
            ])

    print(f"ranked rows (metric_status=ready): {len(top_rows)} -> {top_path.name}")
    print(f"pop gap rows (<{FRESH_FLOOR} or never): {len(gap_rows)} "
          f"(never={never}, stale={len(gap_rows) - never}) -> {gap_path.name}")
    missing_gemrate_id = [r["variant_id"] for r in gap_rows if not r["gemrate_id"]]
    print(f"gap rows without a gemrate identity mapping: {len(missing_gemrate_id)} "
          f"{missing_gemrate_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
