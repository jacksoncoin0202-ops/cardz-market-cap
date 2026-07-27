#!/usr/bin/env python3
"""Produce the three boards the user ordered on 2026-07-26:

  op100.csv   - One Piece Top 100 by market cap
  ptcg100.csv - Pokemon Top 100 by market cap
  tcg300.csv  - overall TCG Top 300 by market cap

Row pool = union of two origins:
  eval8_ready            - market_candidate_daily_snapshot rows, evaluation 8,
                           metric_status='ready'.  Official reference_price_usd
                           x psa10_population, same numbers the site publishes.
  derived_out_of_roster  - variants that are NOT in evaluation 8 at all (out of
                           roster) but have a fresh PSA10-graded price AND a
                           gemrate PSA POP.  market cap here is derived by this
                           script (price x pop) and carries provenance columns.

Derived gates (fail-closed; failures land in blockers.csv, never silently):
  - price source must be PSA10-graded: snk_psa10 or ebay.  snkrdunk raw-listing
    prices are NOT accepted as a PSA10 market-cap numerator.
  - latest PSA10 price date >= FRESH_FLOOR (2026-07-24)
  - gemrate PSA top_grade_population present and > 0

Read-only.  Uses the same db_config as scripts/verify_claims.py.

Run:
    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-26-three-boards/produce_three_boards.py
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
PSA10_PRICE_SOURCES = ("snk_psa10", "ebay")
SOURCE_PRIORITY = {"snk_psa10": 0, "ebay": 1}

EVAL_SQL = """
SELECT s.variant_id,
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

# All price rows sitting on each out-of-eval variant's latest PSA10-source date.
DERIVED_PRICE_SQL = """
SELECT px.variant_id, px.observed_date, px.source_code, px.price_usd, px.id,
       v.tcg_code, v.canonical_name, v.set_name, v.collector_number
FROM market_price_observation px
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_price_observation
      WHERE source_code IN %s
      GROUP BY variant_id) m
  ON m.variant_id = px.variant_id AND m.md = px.observed_date
JOIN catalog_variant v ON v.id = px.variant_id
WHERE px.source_code IN %s
  AND px.variant_id NOT IN (
      SELECT variant_id FROM market_candidate_daily_snapshot
      WHERE evaluation_id = %s)
"""

# Out-of-eval variants that have prices ONLY from non-PSA10 sources.
RAW_ONLY_SQL = """
SELECT px.variant_id, v.tcg_code, v.canonical_name, v.set_name,
       v.collector_number,
       MAX(px.observed_date) AS last_any_price_date,
       GROUP_CONCAT(DISTINCT px.source_code ORDER BY px.source_code) AS sources
FROM market_price_observation px
JOIN catalog_variant v ON v.id = px.variant_id
WHERE px.variant_id NOT IN (
      SELECT variant_id FROM market_candidate_daily_snapshot
      WHERE evaluation_id = %s)
  AND px.variant_id NOT IN (
      SELECT DISTINCT variant_id FROM market_price_observation
      WHERE source_code IN %s)
GROUP BY px.variant_id, v.tcg_code, v.canonical_name, v.set_name,
         v.collector_number
"""

POP_SQL = """
SELECT o.variant_id, o.observed_date, o.top_grade_population, o.id
FROM market_grader_population_observation o
JOIN (SELECT variant_id, MAX(observed_date) AS md
      FROM market_grader_population_observation
      WHERE grader_code='PSA' AND source_code='gemrate'
      GROUP BY variant_id) m
  ON m.variant_id = o.variant_id AND m.md = o.observed_date
WHERE o.grader_code='PSA' AND o.source_code='gemrate'
"""

BOARD_HEADER = [
    "rank", "variant_id", "tcg", "name", "set", "collector_number",
    "price_usd", "psa10_pop", "market_cap_usd",
    "price_as_of", "pop_as_of", "origin", "price_source",
]


def board_row(rank: int, row: dict) -> list:
    return [
        rank, row["variant_id"], row["tcg"], row["name"], row["set"],
        row["collector"], row["price_usd"], row["pop"], row["market_cap"],
        row["price_as_of"], row["pop_as_of"], row["origin"],
        row["price_source"],
    ]


def write_board(path: Path, rows: list[dict], limit: int) -> int:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(BOARD_HEADER)
        for rank, row in enumerate(rows[:limit], start=1):
            writer.writerow(board_row(rank, row))
    return min(len(rows), limit)


def main() -> int:
    import pymysql
    from pymysql.cursors import DictCursor

    config = db_config()
    conn = pymysql.connect(charset="utf8mb4", cursorclass=DictCursor, **config)
    try:
        with conn.cursor() as cur:
            cur.execute(EVAL_SQL, (EVALUATION_ID,))
            eval_rows = cur.fetchall()
            cur.execute(DERIVED_PRICE_SQL,
                        (PSA10_PRICE_SOURCES, PSA10_PRICE_SOURCES,
                         EVALUATION_ID))
            derived_price_rows = cur.fetchall()
            cur.execute(RAW_ONLY_SQL, (EVALUATION_ID, PSA10_PRICE_SOURCES))
            raw_only_rows = cur.fetchall()
            cur.execute(POP_SQL)
            pop_rows = cur.fetchall()
    finally:
        conn.close()

    # Latest gemrate PSA POP per variant (tie-break: max id).
    pop_by_variant: dict[int, dict] = {}
    for row in pop_rows:
        kept = pop_by_variant.get(row["variant_id"])
        if kept is None or row["id"] > kept["id"]:
            pop_by_variant[row["variant_id"]] = row

    pool: list[dict] = []
    for row in eval_rows:
        pool.append({
            "variant_id": row["variant_id"],
            "tcg": row["tcg_code"],
            "name": row["canonical_name"],
            "set": row["set_name"],
            "collector": row["collector_number"],
            "price_usd": row["reference_price_usd"],
            "pop": row["psa10_population"],
            "market_cap": float(row["market_cap_usd"]),
            "price_as_of": row["last_price_date"],
            "pop_as_of": row["last_pop_date"],
            "origin": "eval8_ready",
            "price_source": "eval8_reference",
        })

    # Pick one price row per derived variant: source priority then max id.
    derived_pick: dict[int, dict] = {}
    for row in derived_price_rows:
        kept = derived_pick.get(row["variant_id"])
        if kept is None:
            derived_pick[row["variant_id"]] = row
            continue
        new_key = (SOURCE_PRIORITY[row["source_code"]], -row["id"])
        old_key = (SOURCE_PRIORITY[kept["source_code"]], -kept["id"])
        if new_key < old_key:
            derived_pick[row["variant_id"]] = row

    blockers: list[list] = []
    derived_ready = 0
    for variant_id, row in sorted(derived_pick.items()):
        price_date = str(row["observed_date"])
        pop = pop_by_variant.get(variant_id)
        if row["price_usd"] is None or float(row["price_usd"]) <= 0:
            blockers.append([variant_id, row["tcg_code"],
                             row["canonical_name"], row["set_name"],
                             row["collector_number"], "price_nonpositive",
                             row["source_code"], price_date, None])
            continue
        if price_date < FRESH_FLOOR:
            blockers.append([variant_id, row["tcg_code"],
                             row["canonical_name"], row["set_name"],
                             row["collector_number"], "stale_price",
                             row["source_code"], price_date,
                             pop["observed_date"] if pop else None])
            continue
        if pop is None or not pop["top_grade_population"]:
            blockers.append([variant_id, row["tcg_code"],
                             row["canonical_name"], row["set_name"],
                             row["collector_number"], "no_psa_pop",
                             row["source_code"], price_date, None])
            continue
        derived_ready += 1
        pool.append({
            "variant_id": variant_id,
            "tcg": row["tcg_code"],
            "name": row["canonical_name"],
            "set": row["set_name"],
            "collector": row["collector_number"],
            "price_usd": row["price_usd"],
            "pop": pop["top_grade_population"],
            "market_cap": float(row["price_usd"]) * pop["top_grade_population"],
            "price_as_of": row["observed_date"],
            "pop_as_of": pop["observed_date"],
            "origin": "derived_out_of_roster",
            "price_source": row["source_code"],
        })

    for row in raw_only_rows:
        blockers.append([row["variant_id"], row["tcg_code"],
                         row["canonical_name"], row["set_name"],
                         row["collector_number"], "no_psa10_price_source",
                         row["sources"], str(row["last_any_price_date"]),
                         None])

    pool.sort(key=lambda r: (-r["market_cap"], r["variant_id"]))

    op_rows = [r for r in pool if r["tcg"] == "one-piece"]
    ptcg_rows = [r for r in pool if r["tcg"] == "pokemon"]

    n_op = write_board(HERE / "op100.csv", op_rows, 100)
    n_ptcg = write_board(HERE / "ptcg100.csv", ptcg_rows, 100)
    n_tcg = write_board(HERE / "tcg300.csv", pool, 300)

    blockers_path = HERE / "blockers.csv"
    with blockers_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["variant_id", "tcg", "name", "set",
                         "collector_number", "reason", "price_source",
                         "price_as_of", "pop_as_of"])
        writer.writerows(blockers)

    by_origin_op = {}
    for r in op_rows[:100]:
        by_origin_op[r["origin"]] = by_origin_op.get(r["origin"], 0) + 1
    by_origin_tcg = {}
    for r in pool[:300]:
        by_origin_tcg[r["origin"]] = by_origin_tcg.get(r["origin"], 0) + 1
    reason_counts = {}
    for b in blockers:
        reason_counts[b[5]] = reason_counts.get(b[5], 0) + 1

    print(f"eval8 ready rows            : {len(eval_rows)}")
    print(f"derived candidates (PSA10px): {len(derived_pick)}")
    print(f"derived ready (joined pool) : {derived_ready}")
    print(f"pool total                  : {len(pool)}")
    print(f"op100.csv   : {n_op} rows   origin mix {by_origin_op}")
    print(f"ptcg100.csv : {n_ptcg} rows")
    print(f"tcg300.csv  : {n_tcg} rows  origin mix {by_origin_tcg}")
    print(f"blockers.csv: {len(blockers)} rows  {reason_counts}")
    op_pool_full = len(op_rows)
    print(f"one-piece pool depth {op_pool_full} / pokemon pool depth {len(ptcg_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
