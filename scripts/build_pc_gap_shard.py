#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a PC-FULL-900 shard for qualified cards that have no price source.

The SNK discovery lane only covers non-English cards -- D4 language primacy
routes English to PriceCharting. English cards with no candidate at all had
nowhere to go, because pc_full_shard_runner works from a shard file and the
shard files that produced the current bindings are not in the repo.

This writes one, seeded with a PriceCharting search URL per card. The runner
already knows what to do with a search page: it extracts the product links and
scores them on name + number + set, so the URL here is a starting point, never
an answer.

Run:  python -X utf8 scripts/build_pc_gap_shard.py --tcg one-piece --min-pop 1000
Then: python -X utf8 pipelines/pc_full_shard_runner.py --shard-file <path>
      (needs headed Chrome on CDP 9333 -- scripts/ensure_chrome_cdp.ps1)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import rebuild_036 as R  # noqa: E402

OUT_ROOT = ROOT / "data/runtime/private-reports/fill/PC-FULL-900"
SEARCH = "https://www.pricecharting.com/search-products?q={}&type=prices"

# PriceCharting files One Piece under its own console names. Searching the
# card alone drags in Pokemon cards with the same character number, so the
# game word goes into every query.
TCG_SEARCH_WORD = {"one-piece": "one piece", "pokemon": "pokemon"}


def targets(conn, generation: str, tcg: str, min_pop: int, language: str) -> list[dict]:
    sql = """
        SELECT rm.variant_id AS vid, rm.latest_psa10_population AS pop,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.name')),
                        v.canonical_name) AS name,
               COALESCE(JSON_UNQUOTE(JSON_EXTRACT(rm.detail_json,'$.fingerprint.parallel')),
                        '') AS treatment,
               v.set_name AS `set`, v.collector_number AS num,
               v.tcg_code AS tcg, v.card_language AS language
          FROM catalog_rebuild_member rm
          JOIN catalog_variant v ON v.id = rm.variant_id
         WHERE rm.generation_id = %s
           AND rm.cohort <> 'non_qualified'
           AND rm.latest_psa10_population >= %s
           AND v.card_language = %s
           AND NOT EXISTS (
                 SELECT 1 FROM catalog_source_identity si
                  WHERE si.variant_id = v.id
                    AND si.source_code IN ('snkrdunk','snk_psa10','pricecharting'))
    """
    params: list = [generation, min_pop, language]
    if tcg:
        sql += " AND v.tcg_code = %s"
        params.append(tcg)
    sql += " ORDER BY rm.latest_psa10_population DESC"
    with conn.cursor() as cursor:
        cursor.execute(sql, tuple(params))
        return [dict(row) for row in cursor.fetchall()]


def search_url(row: dict) -> str:
    words = [
        TCG_SEARCH_WORD.get(str(row["tcg"]), str(row["tcg"]).replace("-", " ")),
        str(row["name"] or ""),
        str(row["num"] or ""),
    ]
    query = " ".join(word for word in words if word).strip()
    return SEARCH.format(urllib.parse.quote_plus(query))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tcg", default="")
    parser.add_argument("--min-pop", type=int, default=1000)
    parser.add_argument("--language", default="en")
    parser.add_argument("--generation")
    parser.add_argument("--name", default="gap")
    parser.add_argument("--credentials-env", dest="credentials_env", type=Path)
    args = parser.parse_args()

    conn = R.connect(args.credentials_env or R.DAILY_CREDENTIALS_ENV)
    try:
        generation = args.generation
        if not generation:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT generation_id FROM catalog_rebuild_member"
                    " ORDER BY computed_at DESC LIMIT 1"
                )
                generation = str(cursor.fetchone()["generation_id"])
        rows = targets(conn, generation, args.tcg, args.min_pop, args.language)
    finally:
        conn.close()

    work = [
        {
            "vid": int(row["vid"]),
            "rank": index,
            "name": str(row["name"] or ""),
            "set": str(row["set"] or ""),
            "num": str(row["num"] or ""),
            "tcg": str(row["tcg"] or ""),
            "language": str(row["language"] or ""),
            "pop": int(row["pop"] or 0),
            "urls": [search_url(row)],
        }
        for index, row in enumerate(rows, 1)
    ]
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    path = OUT_ROOT / f"shard_{args.name}.json"
    path.write_text(json.dumps(work, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({
        "shardFile": str(path), "generation": generation,
        "language": args.language, "tcg": args.tcg, "minPop": args.min_pop,
        "cards": len(work),
        "topPop": [{"vid": w["vid"], "pop": w["pop"], "name": w["name"]} for w in work[:5]],
    }, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
