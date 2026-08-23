#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""db_runtime.connect_with_retry is the ONE connect execution point.

A06 2026-08-23 [KNOWN, journal]: two MySQL connect-phase failures in one
rehearsal -- schema-052 `(2013) Connection reset by peer` inside the handshake
(db_runtime.connection_from_args, raw pymysql.connect, no retry) and the
en_price_ref checkpoint `InternalError: Packet sequence number wrong - got 1
expected 2` raised by pymysql while still negotiating (qualified_pool_operator
.db(), whose retry only knew errno 2003/2013).  Both cost a task attempt.

Contract:
  * connect-phase failures (2003, 2013, handshake packet-sequence) are retried
    with backoff and journaled to stderr as DB_CONNECT_RETRY
  * anything else (auth, unknown database, other InternalError) raises first time
  * a dead server exhausts every try and raises the last error
  * db_runtime.connection_from_args goes through the helper
  * rebuild_036.connect_with_retry keeps its name and its patchable backoff
  * ratchet: no `pymysql.connect(` in pipelines/ outside a connect_with_retry(

Run: python -X utf8 scripts/test_db_connect_retry.py
"""
from __future__ import annotations

import io
import sys
import types
from contextlib import redirect_stderr
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pymysql  # noqa: E402

import db_runtime  # noqa: E402

FAILED: list[str] = []
CHECKS = 0
ZERO = (0.0, 0.0, 0.0)


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


class Flaky:
    """A connect factory that raises the queued errors first, then connects."""

    def __init__(self, errors, result="connection"):
        self.errors = list(errors)
        self.calls = 0
        self.result = result

    def __call__(self):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def attempt(factory, **kwargs):
    err = io.StringIO()
    try:
        with redirect_stderr(err):
            got = db_runtime.connect_with_retry(factory, label="t", backoff=ZERO, **kwargs)
        return got, None, err.getvalue()
    except pymysql.err.MySQLError as error:
        return None, error, err.getvalue()


def main() -> int:
    # 1. reset during the handshake -> one retry, journaled
    f = Flaky([pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query ([Errno 104] Connection reset by peer)")])
    got, error, log = attempt(f)
    check("2013 retried", (got, error, f.calls), ("connection", None, 2))
    check("retry journaled", '"event": "DB_CONNECT_RETRY"' in log and '"errno": 2013' in log, True)

    # 2. TCP timeout -> retried
    f = Flaky([pymysql.err.OperationalError(2003, "Can't connect to MySQL server on '127.0.0.1' (timed out)")])
    got, error, log = attempt(f)
    check("2003 retried", (got, error, f.calls), ("connection", None, 2))

    # 3. packet-sequence during the handshake (A06 09:09:44Z) -> retried
    f = Flaky([pymysql.err.InternalError("Packet sequence number wrong - got 1 expected 2")])
    got, error, log = attempt(f)
    check("handshake packet-sequence retried", (got, error, f.calls), ("connection", None, 2))
    check("InternalError retry names its class", '"error": "InternalError"' in log, True)

    # 4. auth denied -> first time, no retry
    f = Flaky([pymysql.err.OperationalError(1045, "Access denied for user")])
    got, error, log = attempt(f)
    check("1045 not retried", (got, getattr(error, "args", (None,))[0], f.calls, log), (None, 1045, 1, ""))

    # 5. unknown database -> first time
    f = Flaky([pymysql.err.OperationalError(1049, "Unknown database")])
    got, error, log = attempt(f)
    check("1049 not retried", (got, getattr(error, "args", (None,))[0], f.calls), (None, 1049, 1))

    # 6. an InternalError that is not the handshake one -> first time
    f = Flaky([pymysql.err.InternalError("some other internal error")])
    got, error, log = attempt(f)
    check("other InternalError not retried", (got, type(error).__name__, f.calls), (None, "InternalError", 1))

    # 7. dead server -> every try, then the last error
    f = Flaky([pymysql.err.OperationalError(2003, "a"), pymysql.err.OperationalError(2003, "b"), pymysql.err.OperationalError(2003, "c")])
    got, error, log = attempt(f)
    check("dead server exhausts every try", (got, getattr(error, "args", (None, None))[1], f.calls), (None, "c", 3))
    check("default backoff is 3 tries", len(db_runtime.CONNECT_BACKOFF), 3)
    check("errno set", db_runtime.CONNECT_PHASE_ERRNOS, frozenset({2003, 2013}))

    # 8. connection_from_args goes through the helper
    real_connect = db_runtime.pymysql.connect
    real_backoff = db_runtime.CONNECT_BACKOFF
    calls: list[dict] = []

    def fake_connect(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise pymysql.err.OperationalError(2013, "reset")
        return "CONN"

    db_runtime.pymysql.connect = fake_connect
    db_runtime.CONNECT_BACKOFF = ZERO
    try:
        args = types.SimpleNamespace(host="127.0.0.1", port=3308, user="u", password="pw", database="cardz_market_cap")
        err = io.StringIO()
        with redirect_stderr(err):
            got = db_runtime.connection_from_args(args)
        check("connection_from_args retries the handshake", (got, len(calls)), ("CONN", 2))
        last = calls[-1]
        check(
            "connection_from_args keeps its kwargs",
            (last["connect_timeout"], last["cursorclass"] is pymysql.cursors.DictCursor, last["database"], last["autocommit"]),
            (10, True, "cardz_market_cap", False),
        )
    finally:
        db_runtime.pymysql.connect = real_connect
        db_runtime.CONNECT_BACKOFF = real_backoff

    # 9. rebuild_036 keeps its name and its patchable backoff
    import rebuild_036

    saved = rebuild_036._CONNECT_BACKOFF
    rebuild_036._CONNECT_BACKOFF = ZERO
    try:
        f = Flaky([pymysql.err.OperationalError(2013, "reset")])
        err = io.StringIO()
        with redirect_stderr(err):
            got = rebuild_036.connect_with_retry(f, label="fixture")
        check("rebuild_036.connect_with_retry delegates", (got, f.calls), ("connection", 2))
        check("rebuild_036 errno set unchanged", rebuild_036._CONNECT_PHASE_ERRNOS, frozenset({2003, 2013}))
    finally:
        rebuild_036._CONNECT_BACKOFF = saved

    # 10. ratchet: every pymysql.connect( in pipelines/ sits inside connect_with_retry(
    raw: list[str] = []
    for path in sorted((ROOT / "pipelines").glob("*.py")):
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for index, line in enumerate(lines):
            if "pymysql.connect(" not in line or line.lstrip().startswith("#"):
                continue
            window = "\n".join(lines[max(0, index - 3):index + 1])
            if "connect_with_retry(" not in window:
                raw.append(f"{path.name}:{index + 1}")
    check("no raw pymysql.connect( outside connect_with_retry", raw, [])

    for line in FAILED:
        print(line)
    print(f"{'FAIL' if FAILED else 'OK'} checks={CHECKS} failed={len(FAILED)}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
