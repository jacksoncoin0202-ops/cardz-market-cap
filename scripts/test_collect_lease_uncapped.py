#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the collect leases lift the runaway-SELECT cap before GET_LOCK and
give a waiter room for a sibling's whole ingest.

2026-08-23 A01: the PC lane waited on the writer lease while SNKRDUNK's real
harvest held it; qualified_pool_operator.db()'s 120 s SELECT cap killed the
GET_LOCK wait itself (errno 3024) and the lane failed with
pc_ebay_contract:OperationalError. A lease connection only waits, never reads,
so the cap has nothing to protect on it. No MySQL needed: db() is faked.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402

UNCAP = "SET SESSION max_execution_time=0"


class FakeCursor:
    def __init__(self, log, acquired):
        self.log, self.acquired = log, acquired

    def execute(self, sql, params=None):
        self.log.append((sql, params))

    def fetchone(self):
        return {"acquired": self.acquired}


class FakeConnection:
    def __init__(self, acquired):
        self.log, self.acquired, self.closed = [], acquired, False

    def cursor(self):
        return FakeCursor(self.log, self.acquired)

    def close(self):
        self.closed = True


def drive(lease, acquired, **kwargs):
    connection = FakeConnection(acquired)
    cc.load_env = lambda: None
    cc.db = lambda: connection
    raised = None
    try:
        with lease(**kwargs):
            pass
    except RuntimeError as exc:
        raised = exc
    return connection, raised


def main() -> int:
    assert cc.DB_WRITER_LEASE_TIMEOUT_SECONDS >= 600, cc.DB_WRITER_LEASE_TIMEOUT_SECONDS
    assert cc.LEASE_SESSION_UNCAP_SQL == UNCAP, cc.LEASE_SESSION_UNCAP_SQL

    conn, raised = drive(cc._db_writer_lease, 1)
    assert raised is None, raised
    sql = [entry[0] for entry in conn.log]
    assert sql[0] == UNCAP, "the writer lease must lift the SELECT cap before it waits: %r" % sql
    assert sql[1].startswith("SELECT GET_LOCK(") and conn.log[1][1] == (
        cc.DB_WRITER_LEASE, cc.DB_WRITER_LEASE_TIMEOUT_SECONDS), conn.log[1]
    assert sql[-1].startswith("SELECT RELEASE_LOCK(") and conn.closed, conn.log
    print("POSITIVE_OK the writer lease lifts the runaway-SELECT cap, then waits %ss for a sibling's ingest"
          % cc.DB_WRITER_LEASE_TIMEOUT_SECONDS)

    conn, raised = drive(cc._db_writer_lease, 1, timeout_seconds=7)
    assert conn.log[1][1] == (cc.DB_WRITER_LEASE, 7), conn.log[1]
    print("POSITIVE_OK an explicit writer-lease timeout still wins")

    conn, raised = drive(cc._db_writer_lease, 0)
    assert raised is not None and "timed out" in str(raised), raised
    assert not any(s.startswith("SELECT RELEASE_LOCK(") for s, _ in conn.log) and conn.closed, conn.log
    print("NEGATIVE_OK a lost writer lease raises, never releases what it did not hold, and closes")

    conn, raised = drive(cc._runtime_state_lease, 1)
    assert raised is None, raised
    sql = [entry[0] for entry in conn.log]
    assert sql[0] == UNCAP, "the runtime-state lease must lift the SELECT cap before it waits: %r" % sql
    assert sql[1].startswith("SELECT GET_LOCK(") and conn.log[1][1] == (cc.RUNTIME_STATE_LEASE,), conn.log[1]
    assert sql[-1].startswith("SELECT RELEASE_LOCK(") and conn.closed, conn.log
    print("POSITIVE_OK the runtime-state lease lifts the cap the same way")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
