#!/usr/bin/env python3
"""Convert the three board CSVs (op100/ptcg100/tcg300 + blockers) into the
single JSON the web app imports at apps/web/src/data/boards.json.

Companion to produce_three_boards.py — run that first to refresh the CSVs,
then this to refresh the web payload.

Run:
    python -X utf8 docs/evidence/2026-07-26-three-boards/build_boards_json.py \
        --out apps/web/src/data/boards.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# The date the CSVs were measured (produce_three_boards.py run date), not "now".
DATA_DATE = "2026-07-26"
EVALUATION_ID = 8
FRESH_FLOOR = "2026-07-24"

BOARDS = (("op100", "op100.csv", 100),
          ("ptcg100", "ptcg100.csv", 100),
          ("tcg300", "tcg300.csv", 300))


def load_rows(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append({
                "rank": int(row["rank"]),
                "variantId": int(row["variant_id"]),
                "tcg": row["tcg"],
                "name": row["name"],
                "set": row["set"],
                "collectorNumber": row["collector_number"],
                "priceUsd": float(row["price_usd"]),
                "psa10Pop": int(row["psa10_pop"]),
                "marketCapUsd": float(row["market_cap_usd"]),
                "priceAsOf": row["price_as_of"],
                "popAsOf": row["pop_as_of"],
                "origin": row["origin"],
                "priceSource": row["price_source"],
            })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    boards = {}
    for key, filename, target in BOARDS:
        rows = load_rows(HERE / filename)
        boards[key] = {"target": target, "count": len(rows), "rows": rows}

    reasons: dict[str, int] = {}
    total = 0
    with (HERE / "blockers.csv").open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            total += 1
            reasons[row["reason"]] = reasons.get(row["reason"], 0) + 1

    payload = {
        "dataDate": DATA_DATE,
        "evaluationId": EVALUATION_ID,
        "freshFloor": FRESH_FLOOR,
        "boards": boards,
        "blockers": {"total": total, "byReason": reasons},
    }

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    sizes = {k: boards[k]["count"] for k in boards}
    print(f"wrote {out}  boards={sizes}  blockers={total} {reasons}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
