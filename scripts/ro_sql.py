#!/usr/bin/env python3
"""Run read-only SQL against the market DB and print the rows.

Promoted out of `temp/` on 2026-07-26.  It was the workhorse behind that day's
audit - every corrected number in CLAUDE.md came through it - and it was one
`rm temp/*` away from having to be rewritten by the next agent.

The read-only guard is NOT redefined here.  It is imported from
`verify_claims.py` so that there is exactly one answer in this repo to the
question "is this statement safe to run".  Two copies drift, and the copy that
drifts is the one that lets a write through.

Usage:

    set -a && . data/runtime/config/backend.env && set +a

    python -X utf8 scripts/ro_sql.py "SELECT COUNT(*) FROM catalog_variant"
    python -X utf8 scripts/ro_sql.py --file temp/probe.sql
    echo "SHOW TABLES" | python -X utf8 scripts/ro_sql.py

A `--file` may hold several statements separated by a line containing `;--END`.
Each is guarded independently.

Exit codes: 0 = every statement ran, 1 = something was blocked or errored,
2 = could not connect.  It does not report success after a failed statement -
the version this replaced returned 0 unconditionally, which is how a broken
probe gets mistaken for an empty result.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from verify_claims import check_read_only, db_config, scrub  # noqa: E402

SEPARATOR = ";--END"
ROW_LIMIT = 200


def split_statements(text: str) -> list[str]:
    return [chunk.strip() for chunk in text.split(SEPARATOR) if chunk.strip()]


def label_for(raw: str) -> str:
    """First comment line makes a good heading; fall back to the statement."""
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            return stripped.lstrip("- ").strip() or stripped
    return " ".join(raw.split())[:80]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only SQL runner.")
    parser.add_argument("sql", nargs="?", help="statement to run")
    parser.add_argument("--file", type=Path, help="file of statements split by ';--END'")
    parser.add_argument("--json", action="store_true", help="emit rows as a JSON array")
    parser.add_argument("--limit", type=int, default=ROW_LIMIT, help="rows printed per statement")
    args = parser.parse_args()

    if args.file:
        raw_text = args.file.read_text(encoding="utf-8")
    elif args.sql:
        raw_text = args.sql
    elif not sys.stdin.isatty():
        raw_text = sys.stdin.read()
    else:
        parser.error("give a statement, --file, or pipe SQL on stdin")

    statements = split_statements(raw_text)
    if not statements:
        print("nothing to run", file=sys.stderr)
        return 1

    try:
        import pymysql
        from pymysql.cursors import DictCursor
    except ImportError:
        print("pymysql is not installed", file=sys.stderr)
        return 2

    config = db_config()
    password = config.get("password", "")
    try:
        conn = pymysql.connect(charset="utf8mb4", cursorclass=DictCursor, **config)
    except Exception as exc:  # noqa: BLE001 - surfaced, scrubbed, then given up on
        print(f"cannot connect: {scrub(str(exc), password)}", file=sys.stderr)
        return 2

    failures = 0
    payload: list[dict] = []
    try:
        cursor = conn.cursor()
        for raw in statements:
            problem = check_read_only(raw)
            if problem:
                print(f"!! BLOCKED: {problem}", file=sys.stderr)
                print(f"   {' '.join(raw.split())[:120]}", file=sys.stderr)
                failures += 1
                continue

            label = label_for(raw)
            try:
                cursor.execute(raw.rstrip().rstrip(";"))
                rows = cursor.fetchall()
            except Exception as exc:  # noqa: BLE001 - one bad probe should not stop the rest
                print(f"!! ERROR {type(exc).__name__}: {scrub(str(exc), password)}", file=sys.stderr)
                print(f"   {label}", file=sys.stderr)
                failures += 1
                continue

            if args.json:
                payload.append({"statement": " ".join(raw.split()), "rows": rows})
                continue

            print(f"\n=== {label}")
            if not rows:
                print("   (no rows)")
                continue
            for row in rows[: args.limit]:
                print("   " + json.dumps(row, ensure_ascii=False, default=str))
            if len(rows) > args.limit:
                print(f"   ... {len(rows)} rows total")
    finally:
        conn.close()

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, default=str, indent=2))

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
