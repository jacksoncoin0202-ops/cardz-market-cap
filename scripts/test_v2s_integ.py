#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Integration fixtures for the V2 structural-fix packages (2026-08-23).

Only one line in this integration needed a hand-written merge resolution, and
it is a line two work packages both rewrote for different reasons:

  * v2s-sched needs the source worker to RE-READ its work deadline on every
    poll, because tick drain can extend that deadline after the worker started
    (operator finding 2026-08-24, "tick deadline interrupted source worker").
  * v2s-gemrate needs the SIGTERM->SIGKILL grace at that same call site to be
    WORKER_SHUTDOWN_GRACE_SECONDS, because the 5 s default killed
    collect_control before it could ingest the cards its child had already
    fetched (audit item 10).

Each package pins its own half, but nothing pinned the combination, and a merge
that keeps one half silently drops the other.  This check drives the real
CommandSourceAdapter poll loop and asserts both halves at once.

No MySQL, no network, no browser, no process that outlives this file.

Run: python -X utf8 scripts/test_v2s_integ.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time as real_time
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2s-integ-"))
os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "health.json")
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2_adapters as adapters_module  # noqa: E402
from daily_chain_v2_contract import SourceTask  # noqa: E402

RUN_ID = "cardz-v2:2026-08-20"
DAY = date(2026, 8, 20)


class _FakeProc:
    """A worker that never exits on its own, so only the deadline can stop it."""

    pid = 5150
    returncode = 0

    def poll(self) -> int | None:
        return None


def test_source_worker_rereads_its_deadline_and_keeps_the_ingest_grace() -> None:
    adapter = adapters_module.build_default_registry().get("fx")
    reads: list[float] = []

    def moving_deadline() -> float:
        # Far future twice (the drain case), then past: a worker that froze the
        # value at claim time would read it once and die on the first poll.
        reads.append(real_time.monotonic())
        return real_time.monotonic() + (1000.0 if len(reads) < 3 else -1.0)

    killed: list[tuple[int, dict[str, Any]]] = []
    assert hasattr(adapters_module, "_popen"), (
        "daily_chain_v2_adapters must expose a module-local worker launcher so "
        "this fixture never has to mutate the global subprocess module"
    )
    saved_popen = adapters_module._popen
    saved_global_popen = subprocess.Popen
    saved_terminate = adapters_module.terminate_worker_group
    adapters_module._popen = lambda *a, **k: _FakeProc()  # type: ignore[assignment]
    adapters_module.terminate_worker_group = (  # type: ignore[assignment]
        lambda pid, **kwargs: killed.append((int(pid), dict(kwargs)))
    )
    task = SourceTask(
        run_id=RUN_ID,
        business_date=DAY.isoformat(),
        source_code="fx",
        capability="rates",
    )
    context = {
        "state_db": WORKSPACE / "integ.sqlite3",
        "task_key": "integ-task",
        "claim_token": "integ-claim",
        "log_path": WORKSPACE / "integ.log",
        "receipt_path": WORKSPACE / "integ.json",
        "deadline_monotonic": moving_deadline,
    }
    try:
        assert subprocess.Popen is saved_global_popen, (
            "the fixture leaked into the global subprocess module"
        )
        raised = None
        try:
            adapter.execute(task, context, lambda **kwargs: None)
        except adapters_module.WorkerInterrupted as error:
            raised = error
        assert raised is not None, "a callable work deadline never interrupted the worker"
        # Half 1 (v2s-sched): the deadline is re-read, not frozen at claim time.
        assert len(reads) >= 3, (
            f"the source worker froze its work deadline at claim time "
            f"({len(reads)} read(s)); tick drain can never reach it"
        )
        # Half 2 (v2s-gemrate): the interrupt grants the ingest grace, not 5 s.
        assert len(killed) == 1, killed
        pid, kwargs = killed[0]
        assert pid == _FakeProc.pid, killed
        assert "grace_seconds" in kwargs, (
            f"the source worker was interrupted with the 5s default grace, so "
            f"collect_control cannot ingest what its child already fetched: {kwargs}"
        )
        assert kwargs["grace_seconds"] == adapters_module.WORKER_SHUTDOWN_GRACE_SECONDS, kwargs
        assert float(kwargs["grace_seconds"]) >= 30.0, kwargs
    finally:
        adapters_module._popen = saved_popen  # type: ignore[assignment]
        adapters_module.terminate_worker_group = saved_terminate  # type: ignore[assignment]
    print(
        "POSITIVE_OK the source worker re-reads its work deadline every poll AND "
        "still gets the 60s ingest grace when the deadline finally binds"
    )


def main() -> int:
    failures: list[str] = []
    for name, check in sorted(globals().items()):
        if not name.startswith("test_") or not callable(check):
            continue
        try:
            check()
        except AssertionError as error:
            failures.append(f"FAIL {name}: {error}")
        except Exception as error:  # noqa: BLE001 - a crash is a failed check
            failures.append(f"FAIL {name}: {type(error).__name__}: {error}")
    for line in failures:
        print(line)
    print(f"\n{1 - len(failures)}/1 checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
