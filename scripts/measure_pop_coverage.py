"""Per-grader POP delta coverage over the ranked universe.

Imports the production functions from canonical_public_snapshot so the
before/after numbers are computed by identical logic (same anchors, same
1d/7d/30d tolerances). Read-only: never writes a snapshot.

Usage: python -X utf8 scripts/measure_pop_coverage.py [label]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from canonical_public_snapshot import (  # noqa: E402
    GRADERS,
    POPULATION_WINDOW_TOLERANCE,
    WINDOWS,
    latest_populations,
    observed_day,
    population_change_windows,
    population_series,
)
from db_runtime import add_connection_args, connection_from_args  # noqa: E402


def open_connection():
    parser = argparse.ArgumentParser()
    add_connection_args(parser)
    return connection_from_args(parser.parse_args([]))


def fetchall(conn, sql, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def main() -> int:
    label = sys.argv[1] if len(sys.argv) > 1 else "before"
    conn = open_connection()
    snap = fetchall(
        conn,
        """
        SELECT id, effective_date FROM market_index_snapshot
        WHERE index_code='tcg-combined' ORDER BY effective_date DESC, id DESC LIMIT 1
        """,
    )[0]
    rows = fetchall(
        conn,
        """
        SELECT c.rank_position, v.id AS variant_id
        FROM market_index_constituent c JOIN catalog_variant v ON v.id=c.variant_id
        WHERE c.index_snapshot_id=%s ORDER BY c.rank_position
        """,
        (snap["id"],),
    )
    variant_ids = [int(r["variant_id"]) for r in rows]
    total = len(variant_ids)
    print(f"[{label}] snapshot id={snap['id']} eff={snap['effective_date']} ranked={total}")
    print(f"[{label}] tolerances {POPULATION_WINDOW_TOLERANCE}")

    series = population_series(conn, variant_ids)
    populations = latest_populations(conn, variant_ids)

    ready: dict[tuple[str, str], int] = {}
    statuses: dict[tuple[str, str], dict[str, int]] = {}
    for vid in variant_ids:
        for grader in GRADERS:
            observed = populations.get((vid, grader))
            top_value = observed["top_grade_population"] if observed else None
            top_value = int(top_value) if top_value is not None else None
            windows = population_change_windows(
                series.get((vid, grader)) or {},
                top_value,
                observed_day(observed),
                "ready" if observed else "unavailable",
            )
            for window in WINDOWS:
                cell = windows[window]
                bucket = statuses.setdefault((grader, window), {})
                bucket[cell["status"]] = bucket.get(cell["status"], 0) + 1
                if cell.get("value") is not None:
                    ready[(grader, window)] = ready.get((grader, window), 0) + 1

    print()
    print(f"=== {label.upper()}: cards with a real delta value, of {total} ranked ===")
    header = f"{'grader':<8}" + "".join(f"{w:>16}" for w in WINDOWS)
    print(header)
    print("-" * len(header))
    for grader in GRADERS:
        line = f"{grader:<8}"
        for window in WINDOWS:
            n = ready.get((grader, window), 0)
            line += f"{n:>7} ({n / total * 100:4.1f}%)"
        print(line)

    out = ROOT / "temp" / f"pop_coverage_{label}.json"
    out.write_text(json.dumps({
        "label": label,
        "snapshotId": snap["id"],
        "effectiveDate": str(snap["effective_date"]),
        "rankedTotal": total,
        "tolerances": POPULATION_WINDOW_TOLERANCE,
        "ready": {f"{g}|{w}": ready.get((g, w), 0) for g in GRADERS for w in WINDOWS},
        "statuses": {f"{g}|{w}": statuses.get((g, w), {}) for g in GRADERS for w in WINDOWS},
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[out] {out}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
