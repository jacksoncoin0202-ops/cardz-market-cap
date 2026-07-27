#!/usr/bin/env python
"""Re-runnable read-only measurement for task today-prices (#12).

Measures, per ``source_code``, how many rows each observation table gained
during the *local JST calendar day* and what ``observed_date`` those rows carry.

Why the JST boundary matters: the MySQL session clock is UTC while the operator
machine is JST (+09:00). ``CURDATE()`` inside MySQL is therefore one day behind
the operator's "today" for the first 9 hours of every JST day. Every query below
derives the cutoff as *midnight of the current JST day, expressed in UTC*, so the
script keeps reporting the right window on any later day without editing.

All statements go through ``scripts/ro_sql.py`` so the repo read-only gate
applies (no writes, no ``information_schema.TABLE_ROWS``).

Usage::

    set -a && . data/runtime/config/backend.env && set +a
    python -X utf8 docs/evidence/2026-07-27-today-prices/verify_today_prices.py
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
RO_SQL = ROOT / "scripts" / "ro_sql.py"

# Midnight of the current JST day, expressed in the UTC session clock.
JST_DAY_START_UTC = "(TIMESTAMP(DATE(NOW() + INTERVAL 9 HOUR)) - INTERVAL 9 HOUR)"

QUERIES: list[tuple[str, str]] = [
    (
        "clock",
        "SELECT NOW() AS db_now_utc, UTC_DATE() AS utc_today, "
        "DATE(NOW() + INTERVAL 9 HOUR) AS jst_today, "
        f"{JST_DAY_START_UTC} AS jst_day_start_utc",
    ),
    (
        "price_appended_this_jst_day",
        "SELECT source_code, COUNT(*) AS rows_appended, "
        "COUNT(DISTINCT variant_id) AS variants, "
        "MIN(observed_date) AS min_observed_date, MAX(observed_date) AS max_observed_date, "
        "MIN(created_at) AS first_created_at, MAX(created_at) AS last_created_at "
        f"FROM market_price_observation WHERE created_at >= {JST_DAY_START_UTC} "
        "GROUP BY source_code ORDER BY source_code",
    ),
    (
        "source_appended_this_jst_day",
        "SELECT source_code, observation_kind, COUNT(*) AS rows_appended, "
        "MIN(observed_date) AS min_observed_date, MAX(observed_date) AS max_observed_date, "
        "MAX(created_at) AS last_created_at "
        f"FROM market_source_observation WHERE created_at >= {JST_DAY_START_UTC} "
        "GROUP BY source_code, observation_kind ORDER BY source_code, observation_kind",
    ),
    (
        "price_totals_by_source",
        "SELECT source_code, COUNT(*) AS total_rows, COUNT(DISTINCT variant_id) AS variants, "
        "MAX(observed_date) AS max_observed_date, MAX(created_at) AS max_created_at "
        "FROM market_price_observation GROUP BY source_code ORDER BY source_code",
    ),
    (
        "source_totals_by_source",
        "SELECT source_code, COUNT(*) AS total_rows, "
        "MAX(observed_date) AS max_observed_date, MAX(created_at) AS max_created_at "
        "FROM market_source_observation GROUP BY source_code ORDER BY source_code",
    ),
    (
        "price_recent_observed_dates",
        "SELECT source_code, observed_date, COUNT(*) AS rows_on_date "
        "FROM market_price_observation WHERE observed_date >= "
        "(DATE(NOW() + INTERVAL 9 HOUR) - INTERVAL 4 DAY) "
        "GROUP BY source_code, observed_date ORDER BY source_code, observed_date",
    ),
]


def main() -> int:
    failures = 0
    for name, sql in QUERIES:
        print(f"########## {name}")
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(RO_SQL), sql],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        sys.stdout.write(completed.stdout)
        if completed.stderr.strip():
            sys.stdout.write(completed.stderr)
        if completed.returncode != 0:
            failures += 1
            print(f"!! query failed: {name} (exit {completed.returncode})")
        print()
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
