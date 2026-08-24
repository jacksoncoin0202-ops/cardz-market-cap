#!/usr/bin/env python3
"""Prove an abandoned advisory lease is reaped, and a live one is not.

    python -X utf8 scripts/test_adapter_lease_orphan_reaped.py

2026-08-25 A01: a collect worker died without its FIN reaching the MySQL
container (WSL -> Windows Docker loopback keeps the socket open). Its lease
connection sat Sleep for 85 minutes still holding en_price_ref, pc_ebay_sales
and pc_cdp, and with wait_timeout at the 8-hour default nothing would have
released them before the next day's 03:30 window. The PC lane burned every
retry against "adapter lease already held" -- an error naming a duplicate
collector that did not exist.

Both halves matter and this suite asserts both, because the obvious cure for
half one breaks half two: a lease connection is idle by design for its whole
50-65 minute sweep, so simply shortening wait_timeout would drop live leases
mid-run and put two collectors on the same source. The orphan half therefore
sets the bounded timeout WITHOUT a keepalive (a dead process has no threads),
and the live half runs the real guard.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

IDLE_TIMEOUT = 4          # seconds of idleness the server tolerates in this test
KEEPALIVE = 1.0           # ping cadence for the live-lease half
LIVE_IDLE_PROOF = IDLE_TIMEOUT * 3 + 2   # how long the live lease must survive
LOCK_NAME = "cardz:test:lease-orphan-reaped"

FAILS: list[str] = []
CHECKS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append(name)
    print(("PASS  " if ok else "FAIL  ") + name + (("  " + detail) if detail else ""))
    if not ok:
        FAILS.append(name)


def holder(probe, lock_name: str):
    probe.execute("SELECT IS_USED_LOCK(%s) AS who", (lock_name,))
    row = probe.fetchone() or {}
    value = row.get("who") if isinstance(row, dict) else row[0]
    return None if value is None else int(value)


def wait_until_free(probe, lock_name: str, budget: float) -> float:
    deadline = time.time() + budget
    start = time.time()
    while time.time() < deadline:
        if holder(probe, lock_name) is None:
            return time.time() - start
        time.sleep(0.5)
    return -1.0


def main() -> int:
    os.environ["CARDZ_LEASE_IDLE_TIMEOUT_SECONDS"] = str(IDLE_TIMEOUT)
    os.environ["CARDZ_LEASE_KEEPALIVE_SECONDS"] = str(KEEPALIVE)

    import collect_control as cc
    from qualified_pool_operator import db, load_env

    check("guard_reads_env_idle_timeout", cc.LEASE_IDLE_TIMEOUT_SECONDS == IDLE_TIMEOUT,
          "got=%s" % cc.LEASE_IDLE_TIMEOUT_SECONDS)

    load_env()
    try:
        probe_conn = db()
    except Exception as exc:  # noqa: BLE001
        print("SKIP  no DB reachable: %s" % exc)
        return 0
    probe = probe_conn.cursor()

    # --- Half 1: an abandoned lease session is reaped by the server itself. ---
    # A killed process loses its keepalive thread but not its socket, so the
    # orphan is exactly "bounded wait_timeout, nobody pinging".
    orphan = db()
    ocur = orphan.cursor()
    ocur.execute("SET SESSION wait_timeout=%s", (IDLE_TIMEOUT,))
    ocur.execute("SELECT GET_LOCK(%s, 5) AS acquired", (LOCK_NAME,))
    ocur.fetchall()
    orphan_id = holder(probe, LOCK_NAME)
    check("orphan_lease_starts_held", orphan_id is not None, "conn=%s" % orphan_id)

    freed_after = wait_until_free(probe, LOCK_NAME, IDLE_TIMEOUT + 15)
    check("orphan_lease_reaped_by_server", freed_after >= 0,
          "freed after %.1fs (server wait_timeout=%ds)" % (freed_after, IDLE_TIMEOUT))
    try:
        orphan.close()
    except Exception:  # noqa: BLE001 - already reaped, which is the point
        pass

    # --- Half 2: a live lease survives far past that same idle timeout. ---
    live = db()
    lcur = live.cursor()
    lcur.execute("SELECT GET_LOCK(%s, 5) AS acquired", (LOCK_NAME,))
    lcur.fetchall()
    guard = cc._LeaseSessionGuard(live, label="test-live")
    live_id = holder(probe, LOCK_NAME)
    check("live_lease_starts_held", live_id is not None, "conn=%s" % live_id)

    time.sleep(LIVE_IDLE_PROOF)
    still = holder(probe, LOCK_NAME)
    check("live_lease_survives_keepalive", still == live_id,
          "held %ds with zero queries of its own, holder=%s" % (LIVE_IDLE_PROOF, still))

    guard.stop()
    lcur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
    lcur.fetchall()
    live.close()
    check("live_lease_released_cleanly", holder(probe, LOCK_NAME) is None)

    probe_conn.close()
    print("\n%d checks, %d failed" % (len(CHECKS), len(FAILS)))
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
