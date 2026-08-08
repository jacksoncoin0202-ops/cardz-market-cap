"""Prove the 036 writer freeze is real (PLAN 036 Gate 0.4).

Connects with the UNMODIFIED backend.env credentials (the account every
legacy writer uses) and attempts an INSERT inside a transaction that is
always rolled back. The freeze is proven only when MySQL rejects the
INSERT with error 1142 (command denied). A successful INSERT means the
freeze is NOT in place and this script exits 1.

Call sites (a check with zero call sites counts as no check):
  1. rebuild-036 stage S0 preflight
  2. rebuild-036-activate (S12) entry
  3. rebuild-036 prune-apply (S13) entry
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
PROBE_SQL = (
    "INSERT INTO market_ingest_run (run_key, source_code, ingest_mode, status, started_at)"
    " VALUES ('036-freeze-probe', 'freeze_probe', 'probe', 'aborted', UTC_TIMESTAMP(6))"
)


def load_backend_env() -> dict[str, str]:
    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    env = load_backend_env()
    connection = pymysql.connect(
        host=env.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(env.get("CARDZ_DB_PORT", "3308")),
        user=env["CARDZ_DB_USER"],
        password=env["CARDZ_DB_PASSWORD"],
        database=env["CARDZ_DB_NAME"],
        charset="utf8mb4",
        autocommit=False,
        connect_timeout=10,
    )
    try:
        with connection.cursor() as cursor:
            try:
                cursor.execute(PROBE_SQL)
            except pymysql.err.OperationalError as error:
                code = error.args[0]
                if code == 1142:
                    print(f"writer freeze PROVEN: INSERT as '{env['CARDZ_DB_USER']}' denied (1142)")
                    return 0
                print(f"writer freeze UNPROVEN: unexpected error {code}: {error}")
                return 1
            print("writer freeze FAILED: INSERT as backend.env account SUCCEEDED (rolled back)")
            return 1
    finally:
        connection.rollback()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
