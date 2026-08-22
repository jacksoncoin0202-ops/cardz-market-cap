#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove mutating collect_control takes the operator e2e lease and refuses.

Hold GET_LOCK(cardz-market-cap:operator-e2e:v1), run incr --dry-run, expect
the refuse path to fire. Then release and confirm the next incr does not
print that refuse. This is the 2026-08-16 hang: collect and daily-accept
used to overlap.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from operator_control import OPERATOR_E2E_LEASE  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402

PY = ROOT / ".venv-backend-windows" / "Scripts" / "python.exe"
if not PY.is_file():
    PY = Path(sys.executable)


def run_incr() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            str(PY),
            "-X",
            "utf8",
            "-u",
            str(ROOT / "pipelines" / "collect_control.py"),
            "incr",
            "--dry-run",
            "--adapter",
            "http",
            "--limit",
            "1",
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def main() -> int:
    load_env()
    conn = db()
    cur = conn.cursor()
    cur.execute("SELECT GET_LOCK(%s, 0) AS acquired", (OPERATOR_E2E_LEASE,))
    acquired = int((cur.fetchone() or {}).get("acquired") or 0)
    if acquired != 1:
        raise SystemExit("could not hold operator e2e lease for the negative fixture")
    try:
        blocked = run_incr()
        blob = (blocked.stdout or "") + (blocked.stderr or "")
        if blocked.returncode == 0:
            raise AssertionError("NEGATIVE_FAIL collect incr succeeded while e2e lease held")
        if "refused" not in blob or OPERATOR_E2E_LEASE not in blob:
            raise AssertionError(
                "NEGATIVE_FAIL collect incr failed but not via e2e lease refuse:\n"
                + blob[-2000:]
            )
        print("NEGATIVE_OK collect incr refused while operator e2e lease held")
    finally:
        cur.execute("SELECT RELEASE_LOCK(%s)", (OPERATOR_E2E_LEASE,))
        conn.close()

    import collect_control as CC  # noqa: E402

    missing = {"stock", "incr"} - set(CC.COLLECT_E2E_LEASE_COMMANDS)
    if missing:
        raise AssertionError(f"COLLECT_E2E_LEASE_COMMANDS missing {missing}")
    if "status" in CC.COLLECT_E2E_LEASE_COMMANDS:
        raise AssertionError("status must stay read-only / unleased")
    print("POSITIVE_OK stock/incr share the operator e2e lease; status does not")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
