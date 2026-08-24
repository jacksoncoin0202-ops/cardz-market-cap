#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durability fixtures for CARDZ Daily Chain V2 orchestration.

Every check here proves a guard FIRES on the broken shape and stays quiet on
the healthy one.  No Telegram, browser, MySQL, push, or deploy occurs: the
journal lives in a private temp directory, alerts run in dry-run mode, and the
only processes started are this file's own sleeping fixtures.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
from daily_chain_v2 import (  # noqa: E402
    ADOPT_GRACE_SECONDS,
    DEFAULT_MAX_RUNTIME_SECONDS,
    MAX_RUNTIME_SECONDS_CEILING,
    DailyChainV2,
    build_health_document,
    clamp_manual_window,
    health_path,
    last_scheduled_tick_utc,
    manual_e2e_schedule,
    next_scheduled_tick_utc,
    recovery_disposition,
    send_alert,
    status_brief,
    write_health_document,
)
from daily_chain_v2_contract import SourceTask, classify_error  # noqa: E402
from daily_chain_v2_journal import (  # noqa: E402
    Journal,
    JournalError,
    interrupt_backoff_seconds,
    iso,
    utc_now,
)
from daily_chain_v2_worker import start_heartbeat  # noqa: E402

DAY = date(2026, 8, 20)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-durability-o1-"))
STARTED_PROCESSES: list[subprocess.Popen[Any]] = []


def cleanup() -> None:
    for proc in STARTED_PROCESSES:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def new_journal(name: str) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    return journal


def add_task(journal: Journal, code: str, *, max_attempts: int = 3) -> str:
    task = SourceTask(
        run_id=RUN_ID, business_date=DAY.isoformat(),
        source_code=code, capability="quote",
    )
    journal.add_task(
        task, phase="source", required_class="extra",
        concurrency_group=f"fixture:{code}", max_concurrency=1,
        max_attempts=max_attempts,
    )
    return task.idempotency_key


def sql(journal: Journal, statement: str, params: tuple[Any, ...] = ()) -> None:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.execute(statement, params)
        conn.commit()


def new_chain(journal: Journal) -> DailyChainV2:
    return DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
    )


def events(journal: Journal, event_type: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM chain_event WHERE event_type=? ORDER BY created_at",
            (event_type,),
        ).fetchall()
    return [dict(row) for row in rows]


try:
    # ---------------------------------------------------------------- task 6
    for token in (
        "migration content changed for 051",
        "MIGRATION HASH mismatch",
        "migration checksum does not match recorded bytes",
    ):
        decision = classify_error(token)
        assert decision.terminal and decision.error_code == "TERMINAL_CONTRACT", token
    network = classify_error("HTTP 503 from gemrate mirror")
    assert not network.terminal and network.error_code == "TRANSIENT_SOURCE"
    assert not classify_error("connection timed out").terminal
    print("POSITIVE_OK migration-content faults are terminal while network faults stay retryable")

    # --------------------------------------------------------------- task 10
    started = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)  # 15:00 JST
    window = manual_e2e_schedule(started, business_date=DAY)
    assert window["final"] == last_scheduled_tick_utc(DAY) == datetime(
        2026, 8, 20, 8, 0, tzinfo=timezone.utc
    )
    assert window["start"] < window["source_cutoff"] < window["sla"] < window["final"]
    late = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)  # 21:00 JST
    late_window = manual_e2e_schedule(late, business_date=DAY)
    assert late_window["final"] == late + timedelta(seconds=2700)
    assert late_window["source_cutoff"] < late_window["sla"] < late_window["final"]
    # 04:00 JST: after the 03:30 tick, so the next-tick ceiling is a full day
    # away and an eight hour window survives untouched.
    early = datetime(2026, 8, 19, 19, 0, tzinfo=timezone.utc)  # 04:00 JST
    early_window = manual_e2e_schedule(early, business_date=DAY)
    assert early_window["final"] == early + timedelta(hours=8)  # inside 17:00 JST
    os.environ["CARDZ_V2_LAST_TICK_JST"] = "12:00"
    try:
        assert last_scheduled_tick_utc(DAY) == datetime(
            2026, 8, 20, 3, 0, tzinfo=timezone.utc
        )
        assert clamp_manual_window(
            {
                "start": early,
                "source_cutoff": early + timedelta(hours=4),
                "sla": early + timedelta(hours=5),
                "final": early + timedelta(hours=12),
            },
            business_date=DAY,
        )["final"] == datetime(2026, 8, 20, 3, 0, tzinfo=timezone.utc)
    finally:
        os.environ.pop("CARDZ_V2_LAST_TICK_JST", None)
    print("POSITIVE_OK manual window clamps to the last scheduled tick and never shortens below its floor")

    # ---------------------------------------------------------------- task 4
    journal = new_journal("budget")
    key = add_task(journal, "budget-source", max_attempts=20)
    clock = datetime(2026, 8, 20, tzinfo=timezone.utc)
    delays: list[int] = []
    states: list[str] = []
    for round_number in range(1, 8):
        claimed = journal.claim_ready(RUN_ID, now=clock, max_interruptions=6)
        if not claimed:
            states.append("NOT_CLAIMED")
            break
        state = journal.interrupt_claim(
            key, claimed[0]["lease_token"], reason="fixture interrupt",
            now=clock, max_interruptions=6,
        )
        states.append(state)
        row = journal.task(key)
        assert int(row["interruptions"]) == round_number
        if row["next_retry_at"]:
            due = datetime.fromisoformat(str(row["next_retry_at"]))
            delays.append(int((due - clock).total_seconds()))
        clock += timedelta(seconds=1200)
        if state == "PARKED":
            break
    assert states == ["INTERRUPTED"] * 5 + ["PARKED"], states
    assert delays == [120, 240, 480, 900, 900], delays
    assert interrupt_backoff_seconds(1) == 120 and interrupt_backoff_seconds(9) == 900
    assert journal.task(key)["status"] == "PARKED"
    assert journal.claim_ready(RUN_ID, now=clock + timedelta(hours=6)) == []
    healthy = add_task(journal, "healthy-source", max_attempts=20)
    assert [row["task_key"] for row in journal.claim_ready(RUN_ID, now=clock)] == [healthy]
    print("POSITIVE_OK interruption budget backs off, parks at the limit, and never auto-claims a parked task")

    os.environ["CARDZ_V2_MAX_INTERRUPTIONS"] = "1"
    try:
        env_journal = new_journal("budget-env")
        env_key = add_task(env_journal, "env-source", max_attempts=20)
        env_claim = env_journal.claim_ready(RUN_ID, now=clock)
        assert len(env_claim) == 1
        assert env_journal.interrupt_claim(
            env_key, env_claim[0]["lease_token"], reason="env budget", now=clock
        ) == "PARKED"
    finally:
        os.environ.pop("CARDZ_V2_MAX_INTERRUPTIONS", None)
    print("NEGATIVE_OK CARDZ_V2_MAX_INTERRUPTIONS shrinks the budget instead of being ignored")

    # ---------------------------------------------------------------- task 5
    journal = new_journal("unpark")
    stuck = add_task(journal, "pc-stuck", max_attempts=12)
    sql(
        journal,
        "UPDATE chain_task SET status='TERMINAL',attempts=13,max_attempts=12,"
        "last_error_code='SOURCE_FAILED' WHERE task_key=?",
        (stuck,),
    )
    assert journal.claim_ready(RUN_ID, now=utc_now()) == []
    revived = journal.unpark(stuck, run_id=RUN_ID, reason="operator resume tonight")
    assert revived is not None
    assert revived["status"] == "READY" and revived["previousStatus"] == "TERMINAL"
    assert int(revived["attempts"]) == 13 and int(revived["max_attempts"]) == 14
    assert int(revived["interruptions"]) == 0
    claimed = journal.claim_ready(RUN_ID, now=utc_now())
    assert [row["task_key"] for row in claimed] == [stuck]
    assert int(claimed[0]["attempts"]) == 14
    assert journal.unpark("no-such-task", run_id=RUN_ID) is None
    assert journal.unpark(stuck, run_id=RUN_ID) is None  # RUNNING is not parkable
    listed = [row["task_key"] for row in journal.unparkable_tasks(RUN_ID)]
    assert listed == []
    print("POSITIVE_OK unpark revives a TERMINAL 13/12 task once and refuses non-parkable keys")

    class UnparkArgs:
        list_only = False
        task = stuck
        reason = "operator cli"

    journal.interrupt_claim(
        stuck, claimed[0]["lease_token"], reason="fixture", max_interruptions=1
    )
    assert journal.task(stuck)["status"] == "PARKED"
    assert chain_module.run_unpark(journal, DAY, UnparkArgs()) == 0
    assert journal.task(stuck)["status"] == "READY"
    assert len(events(journal, "TASK_UNPARKED")) == 1
    assert json.loads(events(journal, "TASK_UNPARKED")[0]["payload_json"])[
        "provenance"
    ] == "operator"
    UnparkArgs.task = "missing-key"
    assert chain_module.run_unpark(journal, DAY, UnparkArgs()) == 2
    UnparkArgs.list_only = True
    assert chain_module.run_unpark(journal, DAY, UnparkArgs()) == 0
    print("POSITIVE_OK unpark CLI journals operator provenance, lists, and exits 2 on an unknown task")

    # ------------------------------------------------------------ retire
    journal = new_journal("retire")
    stale = add_task(journal, "pc-stale", max_attempts=8)
    sql(
        journal,
        "UPDATE chain_task SET status='PARKED',attempts=8,max_attempts=8,"
        "last_error_code='WORKER_PARKED' WHERE task_key=?",
        (stale,),
    )
    assert journal.retire("no-such-task", run_id=RUN_ID, reason="x") is None
    retired = journal.retire(stale, run_id=RUN_ID, reason="contract closed by later repairs")
    assert retired is not None and retired["status"] == "SKIPPED"
    assert retired["previousStatus"] == "PARKED"
    assert retired["last_error_code"] == "OPERATOR_RETIRED"
    assert int(retired["attempts"]) == 8 and int(retired["max_attempts"]) == 8
    assert journal.claim_ready(RUN_ID, now=utc_now()) == []
    assert journal.retire(stale, run_id=RUN_ID, reason="twice") is None  # SKIPPED is settled
    assert journal.unpark(stale, run_id=RUN_ID) is None
    assert journal.unparkable_tasks(RUN_ID) == []
    waiting = add_task(journal, "gemrate-repair-waiting", max_attempts=7)
    sql(journal, "UPDATE chain_task SET status='RETRY',attempts=2,lease_token=NULL WHERE task_key=?", (waiting,))
    leased = add_task(journal, "gemrate-repair-running", max_attempts=7)
    sql(journal, "UPDATE chain_task SET status='RETRY',attempts=2,lease_token='live' WHERE task_key=?", (leased,))
    running = add_task(journal, "gemrate-repair-live", max_attempts=7)
    sql(journal, "UPDATE chain_task SET status='RUNNING',attempts=2,lease_token='live' WHERE task_key=?", (running,))
    assert journal.retire(leased, run_id=RUN_ID, reason="never from under a worker") is None
    assert journal.retire(running, run_id=RUN_ID, reason="never from under a worker") is None
    retired_waiting = journal.retire(waiting, run_id=RUN_ID, reason="window bug closed the shortfall")
    assert retired_waiting is not None and retired_waiting["status"] == "SKIPPED"
    assert retired_waiting["previousStatus"] == "RETRY" and int(retired_waiting["attempts"]) == 2
    assert journal.claim_ready(RUN_ID, now=utc_now() + timedelta(days=1)) == [] or all(
        str(row["task_key"]) != waiting for row in journal.claim_ready(RUN_ID, now=utc_now() + timedelta(days=1))
    )
    later = utc_now()
    core_done = {"required_class": "core", "status": "COMPLETED"}
    assert chain_module.source_barrier_ready(
        [core_done, {"required_class": "core", "status": "SKIPPED"}],
        now=later, cutoff=later + timedelta(hours=1),
    ) is True  # operator-retired core repair is settled scheduling
    for still_open in ("RETRY", "INTERRUPTED", "PARKED", "TERMINAL", "RUNNING"):
        assert chain_module.source_barrier_ready(
            [core_done, {"required_class": "core", "status": still_open}],
            now=later, cutoff=later + timedelta(days=1),
        ) is False, still_open  # even past the cutoff a core task must settle
    assert chain_module.source_barrier_ready(
        [core_done, {"required_class": "quote", "status": "PARKED"}],
        now=later, cutoff=later + timedelta(hours=1),
    ) is False
    assert chain_module.source_barrier_ready(
        [core_done, {"required_class": "quote", "status": "SKIPPED"}],
        now=later, cutoff=later + timedelta(hours=1),
    ) is True
    health = chain_module.aggregate_source_health([
        {"source_code": "pricecharting", "required_class": "quote", "status": "COMPLETED",
         "attempts": 1, "last_error_code": None},
        {"source_code": "pricecharting", "required_class": "quote", "status": "SKIPPED",
         "attempts": 8, "last_error_code": "OPERATOR_RETIRED"},
    ])
    assert health["pricecharting"]["status"] == "COMPLETED"
    assert health["pricecharting"]["errors"] == ["OPERATOR_RETIRED"]
    assert chain_module.degraded_source_codes(health) == []
    print("POSITIVE_OK retire settles PARKED and unleased RETRY tasks as SKIPPED, never a leased one, keeps the trail, unblocks the barrier (core included), and stays out of degradedSources")

    stale_cli = add_task(journal, "pc-stale-cli", max_attempts=8)
    sql(
        journal,
        "UPDATE chain_task SET status='TERMINAL',attempts=8,max_attempts=8,"
        "last_error_code='SOURCE_FAILED' WHERE task_key=?",
        (stale_cli,),
    )

    class RetireArgs:
        list_only = False
        task = stale_cli
        reason = "operator cli retire"

    assert chain_module.run_retire(journal, DAY, RetireArgs()) == 0
    assert journal.task(stale_cli)["status"] == "SKIPPED"
    retire_events = events(journal, "TASK_RETIRED")
    assert len(retire_events) == 1
    retire_payload = json.loads(retire_events[0]["payload_json"])
    assert retire_payload["provenance"] == "operator"
    assert retire_payload["reason"] == "operator cli retire"
    assert retire_payload["previousStatus"] == "TERMINAL"
    assert chain_module.run_retire(journal, DAY, RetireArgs()) == 2  # already settled
    RetireArgs.task = "missing-key"
    assert chain_module.run_retire(journal, DAY, RetireArgs()) == 2
    RetireArgs.list_only = True
    assert chain_module.run_retire(journal, DAY, RetireArgs()) == 0
    print("POSITIVE_OK retire CLI journals operator provenance and exits 2 on settled or unknown tasks")

    # ---------------------------------------------------------------- task 3
    assert recovery_disposition(
        {"lease_expires_at": iso(utc_now() - timedelta(seconds=10))},
        now=utc_now(), alive=True,
    ) == "adopt"
    assert recovery_disposition(
        {"lease_expires_at": iso(utc_now() - timedelta(seconds=600))},
        now=utc_now(), alive=True,
    ) == "terminate"
    assert recovery_disposition(
        {"lease_expires_at": iso(utc_now() - timedelta(seconds=10))},
        now=utc_now(), alive=False,
    ) == "interrupt"

    terminated: list[int] = []
    real_terminate = chain_module.terminate_worker_group
    chain_module.terminate_worker_group = lambda pid, **kwargs: terminated.append(int(pid))
    try:
        for label, lease_age, alive, expected_status, expect_kill in (
            ("adopt", 10, True, "RUNNING", False),
            ("terminate", 900, True, "INTERRUPTED", True),
            ("dead", 10, False, "INTERRUPTED", False),
        ):
            journal = new_journal(f"recover-{label}")
            key = add_task(journal, f"recover-{label}", max_attempts=5)
            claim = journal.claim_ready(RUN_ID, now=utc_now())[0]
            worker = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(45)"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            STARTED_PROCESSES.append(worker)
            expired = iso(utc_now() - timedelta(seconds=lease_age))
            sql(journal, "UPDATE chain_task SET lease_expires_at=? WHERE task_key=?", (expired, key))
            sql(
                journal,
                "UPDATE chain_attempt SET worker_pid=? WHERE claim_token=?",
                (worker.pid, claim["lease_token"]),
            )
            chain = new_chain(journal)
            chain._pid_belongs_to_attempt = lambda pid, row, _alive=alive: _alive
            terminated.clear()
            chain.recover_expired()
            assert journal.task(key)["status"] == expected_status, label
            assert bool(terminated) is expect_kill, (label, terminated)
            adopted = events(journal, "TASK_ADOPTED")
            assert bool(adopted) is (label == "adopt"), label
            if label == "adopt":
                assert worker.poll() is None
                payload = json.loads(adopted[0]["payload_json"])
                assert payload["pid"] == worker.pid
            worker.kill()
            worker.wait(timeout=10)
        # The real predicate must reject a pid that is not this attempt's worker.
        chain = new_chain(new_journal("recover-predicate"))
        assert chain._pid_belongs_to_attempt(
            999999, {"task_key": "x", "claim_token": "y", "process_started_at": ""}
        ) is False
    finally:
        chain_module.terminate_worker_group = real_terminate
    print("POSITIVE_OK expired leases adopt a live worker, kill a stale one, and interrupt a dead one")

    # ---------------------------------------------------------------- task 2
    journal = new_journal("heartbeat")
    key = add_task(journal, "heartbeat-source", max_attempts=3)
    claim = journal.claim_ready(RUN_ID, now=utc_now())[0]
    os.environ["CARDZ_V2_HEARTBEAT_SECONDS"] = "1"
    try:
        stop = start_heartbeat(journal, key, claim["lease_token"])
        beats: list[str] = []
        leases: list[str] = []
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline and len(beats) < 3:
            row = journal.task(key)
            if str(row["heartbeat_at"]) not in beats:
                beats.append(str(row["heartbeat_at"]))
                leases.append(str(row["lease_expires_at"]))
            time.sleep(0.2)
        stop()
    finally:
        os.environ.pop("CARDZ_V2_HEARTBEAT_SECONDS", None)
    assert len(beats) >= 3, beats  # claim stamp plus at least two renewals
    assert beats == sorted(beats) and leases == sorted(leases)
    frozen = journal.task(key)["heartbeat_at"]
    time.sleep(2.5)
    assert journal.task(key)["heartbeat_at"] == frozen
    print("POSITIVE_OK worker heartbeat renews the lease at least twice and stops on exit")

    # ---------------------------------------------------------------- task 7
    dry_run_path = WORKSPACE / "alerts.txt"
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    journal = new_journal("alerts")
    chain = new_chain(journal)
    real_stdout = sys.stdout
    try:
        with dry_run_path.open("w", encoding="utf-8") as sink:
            sys.stdout = sink
            send_alert("fixture-key", "fixture text", level="warn", cooldown_min=1)
            chain.journal_event(
                "TICK_CRASHED", "fixture", {"runId": RUN_ID, "errorCode": "RuntimeError"}
            )
            chain.journal_event("TICK_CRASHED", "fixture", {"runId": RUN_ID})  # deduped
            chain.journal_event("TASK_ERROR", "fixture", {"runId": RUN_ID})  # not lifecycle
    finally:
        sys.stdout = real_stdout
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    lines = [line for line in dry_run_path.read_text(encoding="utf-8").splitlines() if line]
    assert lines[0] == "NOTIFY_DRYRUN fixture-key warn fixture text", lines
    assert len(lines) == 2, lines
    assert lines[1].startswith("NOTIFY_DRYRUN v2-tick-crashed:2026-08-20 error"), lines
    assert "TICK_CRASHED" in lines[1]
    assert chain_module.LAST_ALERT and chain_module.LAST_ALERT["key"].startswith(
        "v2-tick-crashed"
    )
    print("POSITIVE_OK lifecycle events alert once without --notify while ordinary events stay silent")

    # ------------------------------------------------------------- tasks 8, 9
    journal = new_journal("health")
    key = add_task(journal, "health-source", max_attempts=4)
    claim = journal.claim_ready(RUN_ID, now=utc_now())[0]
    journal.interrupt_claim(key, claim["lease_token"], reason="fixture", max_interruptions=6)
    parked_key = add_task(journal, "health-parked", max_attempts=4)
    parked_claim = journal.claim_ready(RUN_ID, now=utc_now())[0]
    journal.interrupt_claim(
        parked_key, parked_claim["lease_token"], reason="fixture", max_interruptions=1
    )
    os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "health" / "health.json")
    try:
        document = build_health_document(
            journal, DAY, tick_phase="ended", tick_exit_code=0,
            tick_started_at_utc="2026-08-20T00:00:00.000000+00:00",
            tick_ended_at_utc="2026-08-20T00:00:12.500000+00:00",
        )
        written = write_health_document(document)
        assert written == health_path() and written.exists()
        stored = json.loads(written.read_text(encoding="utf-8"))
        assert stored["schema"] == 1 and isinstance(stored["schema"], int)
        assert stored["business_date"] == "2026-08-20"
        assert stored["run_state"] == "RUNNING"
        assert stored["tick_phase"] == "ended" and stored["tick_exit_code"] == 0
        assert stored["tick_duration_s"] == 12.5
        assert stored["parked"] == [parked_key]
        assert stored["tasks"][key]["interruptions"] == 1
        assert stored["tasks"][key]["state"] == "INTERRUPTED"
        assert stored["tasks"][parked_key]["last_error_code"] == "WORKER_PARKED"
        assert stored["autonomous_proven"] is False
        assert stored["manual_window_until_utc"] is None
        assert isinstance(stored["next_retry_at_utc"], str)
        assert stored["run_id"] == "cardz-v2:2026-08-20" and stored["run_label"] is None
        # Only a live tick knows its own budget; built without one the field is
        # present and null, so the document keeps ONE shape for every reader.
        assert stored["tick_budget"] is None
        # Generation 1 -- the only shape that existed before supersede -- reads
        # exactly as it always did: no suffix on the run id, nothing pending.
        assert stored["supersede_seq"] == 1 and stored["supersede_pending"] is False
        assert set(stored) == {
            "schema", "written_at_utc", "written_at_jst", "business_date",
            "run_id", "run_label",
            "run_state", "tick_phase", "tick_exit_code", "tick_started_at_utc",
            "tick_ended_at_utc", "tick_duration_s", "next_retry_at_utc", "tasks",
            "parked", "manual_window_until_utc", "autonomous_proven", "last_alert",
            "tick_budget", "supersede_seq", "supersede_pending",
        }
        brief = status_brief(journal, DAY)
        assert brief.startswith("2026-08-20 RUNNING tasks=0/2 retry=1 terminal=0"), brief
        assert f"parked={parked_key}" in brief
        assert "manual_until=-" in brief and "autonomous=False" in brief
        assert int(brief.split("health_age=")[1]) >= 0
        sql(
            journal,
            "UPDATE chain_run SET final_at='2026-08-20T09:00:00+00:00' WHERE run_id=?",
            (RUN_ID,),
        )
        manual_doc = build_health_document(journal, DAY, tick_phase="started")
        assert manual_doc["manual_window_until_utc"].startswith("2026-08-20T09:00:00")
        assert "manual_until=2026-08-20T09:00:00" in status_brief(journal, DAY)
    finally:
        os.environ.pop("CARDZ_V2_HEALTH_PATH", None)
    print("POSITIVE_OK health.json carries every schema-1 field and status --brief reads the same data")

    # ---------------------------------------------------------------- task 1
    journal = new_journal("signal")
    key = add_task(journal, "signal-source", max_attempts=4)
    claim = journal.claim_ready(RUN_ID, now=utc_now())[0]
    chain = new_chain(journal)
    chain.own_claims[key] = claim["lease_token"]
    chain.tick_started_at_utc = iso()
    os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "signal-health.json")
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        code = chain.signal_shutdown(int(signal.SIGTERM))
        stored = json.loads(health_path().read_text(encoding="utf-8"))
    finally:
        os.environ.pop("CARDZ_V2_HEALTH_PATH", None)
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    assert code == 143
    signalled = events(journal, "TICK_SIGNALLED")
    assert len(signalled) == 1
    payload = json.loads(signalled[0]["payload_json"])
    assert payload["signal"] == "SIGTERM" and payload["pid"] == os.getpid()
    assert journal.task(key)["status"] == "INTERRUPTED"
    assert chain.own_claims == {}
    assert stored["tick_phase"] == "signalled" and stored["tick_exit_code"] == 143

    crashed_chain = new_chain(journal)
    crashed_chain.tick_started_at_utc = iso()
    os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "crash-health.json")
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        assert crashed_chain.tick_crashed(RuntimeError("boom"), exit_code=1) == 1
        crash_health = json.loads(health_path().read_text(encoding="utf-8"))
    finally:
        os.environ.pop("CARDZ_V2_HEALTH_PATH", None)
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    assert crash_health["tick_phase"] == "crashed" and crash_health["tick_exit_code"] == 1
    assert len(events(journal, "TICK_CRASHED")) == 1
    print("POSITIVE_OK SIGTERM journals TICK_SIGNALLED, releases its own claim, and reports exit 143")

    # ------------------------------------------------------------ tasks 1, 11
    # These two run the real CLI.  --selftest-sleep takes the lock, installs the
    # handlers, writes health, and idles: no planning, no MySQL, no publish.
    live_dir = Path(
        subprocess.run(
            ["mktemp", "-d", "-t", "cardz-v2-o1-tick-XXXXXX"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    )
    try:
        state_db = live_dir / "chain.sqlite3"
        health_file = live_dir / "health.json"
        env = os.environ.copy()
        env.update({
            "CARDZ_V2_HEALTH_PATH": str(health_file),
            "CARDZ_V2_NOTIFY_DRY_RUN": "1",
        })
        command = [
            sys.executable, "-X", "utf8", str(ROOT / "pipelines" / "daily_chain_v2.py"),
            "tick", "--state-db", str(state_db), "--business-date", DAY.isoformat(),
            "--max-runtime-seconds", "120", "--selftest-sleep", "120",
        ]
        tick = subprocess.Popen(
            command, cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        STARTED_PROCESSES.append(tick)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if health_file.exists():
                if json.loads(health_file.read_text(encoding="utf-8"))["tick_phase"] == "started":
                    break
            if tick.poll() is not None:
                raise AssertionError(f"tick exited early: {tick.communicate()}")
            time.sleep(0.2)
        else:
            raise AssertionError("tick never reported tick_phase=started")

        # Task 11: a second tick on the same journal must skip, not double-run.
        # audit P2-1(b): the skip must NOT refresh the running tick's health.json
        # freshness stamp -- that is what made the watchdog's 25-minute rule
        # blind to a tick stuck while holding the flock.  The skip records itself
        # in its own marker file instead.
        before_skip = json.loads(health_file.read_text(encoding="utf-8"))
        second = subprocess.run(
            command[:-2] + ["--selftest-sleep", "1"],
            cwd=str(ROOT), env=env,
            capture_output=True, text=True, timeout=120,
        )
        assert second.returncode == 0, second.stderr[-2000:]
        assert second.stdout.strip().splitlines()[-1] == "TICK_SKIPPED_LOCKED", second.stdout
        after_skip = json.loads(health_file.read_text(encoding="utf-8"))
        assert after_skip["written_at_utc"] == before_skip["written_at_utc"], after_skip
        assert after_skip["tick_phase"] == "started", after_skip
        skipped = json.loads(
            (live_dir / "tick-skipped.json").read_text(encoding="utf-8")
        )
        assert skipped["reason"] == "TICK_SKIPPED_LOCKED" and skipped["schema"] == 1
        assert skipped["business_date"] == DAY.isoformat(), skipped
        assert skipped["last_skipped_at_utc"] > before_skip["written_at_utc"], skipped

        tick.send_signal(signal.SIGTERM)
        tick.wait(timeout=60)
        assert tick.returncode == 143, tick.returncode
        signalled_health = json.loads(health_file.read_text(encoding="utf-8"))
        assert signalled_health["tick_phase"] == "signalled"
        assert signalled_health["tick_exit_code"] == 143
        live_journal = Journal(state_db)
        rows = events(live_journal, "TICK_SIGNALLED")
        assert len(rows) == 1
        assert json.loads(rows[0]["payload_json"])["signal"] == "SIGTERM"

        # The released lock lets the next tick run instead of skipping forever.
        third = subprocess.run(
            command[:-2] + ["--selftest-sleep", "0.1"],
            cwd=str(ROOT), env={**env, "CARDZ_V2_HEALTH_PATH": str(live_dir / "third.json")},
            capture_output=True, text=True, timeout=120,
        )
        assert third.returncode == 0 and "TICK_SKIPPED_LOCKED" not in third.stdout
        assert json.loads(
            (live_dir / "third.json").read_text(encoding="utf-8")
        )["tick_phase"] == "ended"
    finally:
        shutil.rmtree(live_dir, ignore_errors=True)
    print("POSITIVE_OK a signalled tick exits 143 with journal evidence and a held tick lock skips instead of doubling")

    # --------------------------------------------------------------- task 12
    # R2 (2026-08-24): the claim window, not the whole PT55M.  The drain fills
    # the rest; scripts/test_v2_tick_budget.py holds the .ps1 side to the same
    # number and proves the drain is non-zero.
    assert DEFAULT_MAX_RUNTIME_SECONDS == 2100
    assert MAX_RUNTIME_SECONDS_CEILING == 5400
    source = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
    assert '"--max-runtime-seconds", type=int, default=DEFAULT_MAX_RUNTIME_SECONDS' in source
    assert "default=5400" not in source and "= 5400" not in source.replace(
        "MAX_RUNTIME_SECONDS_CEILING = 5400", ""
    )
    assert ADOPT_GRACE_SECONDS == 120
    print("POSITIVE_OK DEFAULT_MAX_RUNTIME_SECONDS is the single 2100 second claim budget")

    # --------------------------------------------------------------- task 13
    # --notify imports notify_hermes inside deliver_events(); it lives in
    # scripts/, which daily_chain_v2 must put on sys.path itself (2026-08-22
    # 19:34 JST: first -Notify tick crashed with ModuleNotFoundError).
    import importlib

    notify_module = importlib.import_module("notify_hermes")
    assert Path(notify_module.__file__).resolve() == (
        ROOT / "scripts" / "notify_hermes.py"
    ).resolve(), notify_module.__file__
    assert callable(getattr(notify_module, "send_message", None))
    print("POSITIVE_OK notify_hermes resolves from scripts/ once daily_chain_v2 is imported")

    # ------------------------------------------------------------- fix C
    # The 2026-08-22 run published with four minutes of manual window left,
    # because the floor was tick start + 600 s.  The floor is now 2700 s, and
    # the next unattended 03:30 JST tick is a hard ceiling above it.
    assert chain_module.MANUAL_WINDOW_MIN_SECONDS == 2700
    assert next_scheduled_tick_utc(
        datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)  # 21:00 JST
    ) == datetime(2026, 8, 20, 18, 30, tzinfo=timezone.utc)
    assert next_scheduled_tick_utc(
        datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)  # 03:00 JST next day
    ) == datetime(2026, 8, 20, 18, 30, tzinfo=timezone.utc)
    assert next_scheduled_tick_utc(  # strictly after: 03:30 JST returns tomorrow
        datetime(2026, 8, 20, 18, 30, tzinfo=timezone.utc)
    ) == datetime(2026, 8, 21, 18, 30, tzinfo=timezone.utc)
    floor_start = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)  # 21:00 JST
    floor_window = manual_e2e_schedule(floor_start, business_date=DAY)
    assert floor_window["final"] == floor_start + timedelta(seconds=2700)
    assert floor_window["final"] - floor_start > timedelta(minutes=10)  # old floor
    ceiling_start = datetime(2026, 8, 20, 18, 0, tzinfo=timezone.utc)  # 03:00 JST
    ceiling_window = manual_e2e_schedule(ceiling_start)
    assert ceiling_window["final"] > ceiling_start
    assert ceiling_window["final"] <= next_scheduled_tick_utc(ceiling_start) - timedelta(
        minutes=5
    )
    assert ceiling_window["final"] - ceiling_start < timedelta(seconds=2700)  # ceiling wins
    assert (
        ceiling_window["start"]
        < ceiling_window["source_cutoff"]
        < ceiling_window["sla"]
        < ceiling_window["final"]
    )
    edge_start = datetime(2026, 8, 20, 18, 20, tzinfo=timezone.utc)  # 03:20 JST
    edge_window = manual_e2e_schedule(edge_start)
    assert edge_window["final"] == edge_start + timedelta(seconds=600)  # absolute floor
    print("POSITIVE_OK manual window floors at 2700s, stops five minutes short of the next tick, and keeps the ten minute absolute bound")

    journal = new_journal("renew-revival")
    assert journal.run(RUN_ID)["status"] == "RUNNING"
    journal.set_run_status(RUN_ID, "FAILED_FINAL")
    revived_run, revived_reopened, revived_previous = journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T12:30:00+00:00",
        sla_at="2026-08-20T12:40:00+00:00",
        final_at="2026-08-20T12:45:00+00:00",
    )
    assert revived_previous == "FAILED_FINAL"
    assert revived_run["status"] == "RUNNING"
    assert journal.run(RUN_ID)["status"] == "RUNNING"
    assert revived_reopened == []
    assert str(revived_run["final_at"]) == "2026-08-20T12:45:00+00:00"

    plain = new_journal("renew-plain")
    plain_run, _plain_reopened, plain_previous = plain.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T12:30:00+00:00",
        sla_at="2026-08-20T12:40:00+00:00",
        final_at="2026-08-20T12:45:00+00:00",
    )
    assert plain_previous == "RUNNING" and plain_run["status"] == "RUNNING"

    published = new_journal("renew-published")
    sql(
        published,
        "UPDATE chain_run SET publication_status='PUBLISHED' WHERE run_id=?",
        (RUN_ID,),
    )
    try:
        published.renew_manual_window(
            RUN_ID,
            source_cutoff_at="2026-08-20T12:30:00+00:00",
            sla_at="2026-08-20T12:40:00+00:00",
            final_at="2026-08-20T12:45:00+00:00",
        )
        raise AssertionError("a published run must never renew its manual window")
    except JournalError:
        pass
    print("POSITIVE_OK explicit renewal revives a FAILED_FINAL run to RUNNING while a published run still refuses")

    # ------------------------------------------------------------- fix A
    import rebuild_036 as connect_module  # noqa: E402

    original_backoff = connect_module._CONNECT_BACKOFF
    connect_module._CONNECT_BACKOFF = (0.0, 0.0, 0.0)
    try:
        flaky_calls: list[int] = []

        def flaky() -> str:
            flaky_calls.append(1)
            if len(flaky_calls) < 3:
                raise connect_module.pymysql.err.OperationalError(
                    2003, "Can't connect to MySQL server on '127.0.0.1' (timed out)"
                )
            return "connection"

        noise = io.StringIO()
        with contextlib.redirect_stderr(noise):
            assert connect_module.connect_with_retry(flaky, label="fixture") == "connection"
        assert len(flaky_calls) == 3, flaky_calls
        logged = [json.loads(line) for line in noise.getvalue().splitlines() if line.strip()]
        assert [entry["attempt"] for entry in logged] == [1, 2], logged
        assert {entry["event"] for entry in logged} == {"DB_CONNECT_RETRY"}
        assert {entry["label"] for entry in logged} == {"fixture"}
        assert "password" not in noise.getvalue().lower()

        denied_calls: list[int] = []

        def denied() -> str:
            denied_calls.append(1)
            raise connect_module.pymysql.err.OperationalError(1045, "Access denied")

        try:
            connect_module.connect_with_retry(denied, label="fixture")
            raise AssertionError("a non connect-phase error must not be retried")
        except connect_module.pymysql.err.OperationalError as error:
            assert int(error.args[0]) == 1045
        assert len(denied_calls) == 1, denied_calls

        dead_calls: list[int] = []

        def dead() -> str:
            dead_calls.append(1)
            raise connect_module.pymysql.err.OperationalError(2003, "timed out")

        with contextlib.redirect_stderr(io.StringIO()):
            try:
                connect_module.connect_with_retry(dead, label="fixture")
                raise AssertionError("an exhausted retry budget must re-raise")
            except connect_module.pymysql.err.OperationalError as error:
                assert int(error.args[0]) == 2003
        assert len(dead_calls) == 3, dead_calls
    finally:
        connect_module._CONNECT_BACKOFF = original_backoff
    assert connect_module._CONNECT_PHASE_ERRNOS == frozenset({2003, 2013})
    print("POSITIVE_OK connect retry replays only the TCP handshake and raises an auth error on the first try")

    # ------------------------------------------------------------- fix B
    long_session_message = DailyChainV2._event_message(
        "DB_LONG_SESSIONS_OBSERVED",
        {
            "runId": RUN_ID, "sessionCount": 2, "maxMinutes": 41,
            "sessions": [{"id": 7, "seconds": 2460, "command": "Query", "sql": "SELECT 1"}],
        },
    )
    assert "\U0001F534" not in long_session_message, long_session_message
    assert "reported only" in long_session_message
    assert "sessions=2" in long_session_message and "maxMinutes=41" in long_session_message
    renewed_message = DailyChainV2._event_message(
        "MANUAL_WINDOW_RENEWED",
        {
            "runId": RUN_ID, "origin": "manual-e2e", "previousStatus": "FAILED_FINAL",
            "finalAt": "2026-08-20T12:45:00+00:00", "reopenedTaskKeys": ["a", "b"],
        },
    )
    assert "\U0001F534" not in renewed_message, renewed_message
    assert "previousStatus=<code>FAILED_FINAL</code>" in renewed_message
    assert "reopened=2" in renewed_message
    # Negative: an event type without a branch still renders the red fallback,
    # so a future event cannot quietly render as good news.
    assert "\U0001F534" in DailyChainV2._event_message("SOMETHING_NEW", {"runId": RUN_ID})
    print("NEGATIVE_OK the two new event types render without a red alert while unknown types still do")

    # ------------------------------------------------------------- fix D
    preflight_source = inspect.getsource(DailyChainV2.report_long_db_sessions)
    assert "KILL" not in preflight_source, "the preflight must never terminate a session"
    assert "report_long_db_sessions" in inspect.getsource(DailyChainV2.initialise)

    class FixtureCursor:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows
            self.calls: list[tuple[str, Any]] = []

        def __enter__(self) -> "FixtureCursor":
            return self

        def __exit__(self, *_exc: Any) -> bool:
            return False

        def execute(self, statement: str, params: Any = None) -> None:
            self.calls.append((statement, params))

        def fetchall(self) -> list[dict[str, Any]]:
            return self.rows

    class FixtureConnection:
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            self.rows = rows
            self.cursors: list[FixtureCursor] = []
            self.closed = False

        def cursor(self) -> FixtureCursor:
            cursor = FixtureCursor(self.rows)
            self.cursors.append(cursor)
            return cursor

        def close(self) -> None:
            self.closed = True

    journal = new_journal("long-sessions")
    chain = new_chain(journal)
    # information_schema answers in upper case on the Windows MySQL build.
    busy = FixtureConnection([{
        "ID": 41, "USER": "cardz", "DB": "cardz_market_cap",
        "COMMAND": "Query", "TIME": 2460, "sql_head": "SELECT COUNT(*) FROM heavy_view",
    }])
    stub = types.ModuleType("qualified_pool_operator")
    stub.load_env = lambda: None  # type: ignore[attr-defined]
    stub.db = lambda: busy  # type: ignore[attr-defined]
    real_module = sys.modules.get("qualified_pool_operator")
    sys.modules["qualified_pool_operator"] = stub
    try:
        chain.report_long_db_sessions()
        observed = events(journal, "DB_LONG_SESSIONS_OBSERVED")
        assert len(observed) == 1, observed
        observed_payload = json.loads(observed[0]["payload_json"])
        assert observed_payload["sessionCount"] == 1
        assert observed_payload["maxMinutes"] == 41
        assert observed_payload["sessions"][0]["id"] == 41
        assert observed_payload["sessions"][0]["seconds"] == 2460
        assert observed_payload["sessions"][0]["command"] == "Query"
        assert busy.closed is True
        statement, params = busy.cursors[0].calls[0]
        assert "information_schema.processlist" in statement
        assert "KILL" not in statement.upper()
        assert params == ("cardz%", 1200)
        assert "reported only" in DailyChainV2._event_message(
            "DB_LONG_SESSIONS_OBSERVED", observed_payload
        )

        # Negative: nothing long running, and a database that refuses the
        # preflight entirely, must both stay silent instead of blocking a tick.
        stub.db = lambda: FixtureConnection([])  # type: ignore[attr-defined]
        chain.report_long_db_sessions()

        def refuse() -> Any:
            raise RuntimeError("mysql is unreachable")

        stub.db = refuse  # type: ignore[attr-defined]
        chain.report_long_db_sessions()
        assert len(events(journal, "DB_LONG_SESSIONS_OBSERVED")) == 1
    finally:
        if real_module is None:
            sys.modules.pop("qualified_pool_operator", None)
        else:
            sys.modules["qualified_pool_operator"] = real_module
    print("POSITIVE_OK the long session preflight journals what it saw, kills nothing, and never blocks a tick")

    # ------------------------------------------------------------- fix E
    rebuild_source = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
    pool_source = (ROOT / "pipelines" / "qualified_pool_operator.py").read_text(encoding="utf-8")
    assert "max_execution_time" not in rebuild_source  # never on the writer path
    assert "SET SESSION max_execution_time" in pool_source
    assert 'os.environ.get("CARDZ_MAX_EXEC_MS", "120000")' in pool_source
    assert "SET PERSIST" not in pool_source and "SET PERSIST" not in rebuild_source
    assert "SET GLOBAL" not in pool_source and "SET GLOBAL" not in rebuild_source
    assert 'label="qualified_pool_operator"' in pool_source
    assert 'label="rebuild_036"' in rebuild_source
    assert pool_source.count("connect_timeout=10") == 1
    print("POSITIVE_OK the execution time cap is session scoped on the read path and absent from the rebuild writer")

    # ------------------------------------------------------------- fx freshness floor
    from daily_chain_v2_worker import fx_freshness_floor

    midnight_0824 = datetime(2026, 8, 23, 15, 0, tzinfo=timezone.utc)  # 2026-08-24 00:00 JST
    early_plan = "2026-08-23T00:38:37.753775+00:00"  # manual window opened 09:38 JST the day before
    fetched = datetime(2026, 8, 23, 0, 38, 54, tzinfo=timezone.utc)
    assert fetched < midnight_0824  # the midnight-only rule refused this fetch (08-24 fx TERMINAL)
    floor = fx_freshness_floor(date(2026, 8, 24), early_plan)
    assert floor == datetime.fromisoformat(early_plan).replace(microsecond=0)
    assert fetched >= floor
    # 2026-08-25: fetchedAt is stamped with timespec="seconds" (fx_rates.iso_utc),
    # so a fresh fetch inside the floor's own second (fetchedAt 04:14:16 vs task
    # created 04:14:16.568775) must not read as stale.
    same_second_plan = "2026-08-24T04:14:16.568775+00:00"
    same_second_fetch = datetime(2026, 8, 24, 4, 14, 16, tzinfo=timezone.utc)
    assert same_second_fetch >= fx_freshness_floor(date(2026, 8, 25), same_second_plan)
    scheduled_plan = "2026-08-23T18:30:05.000000+00:00"
    assert fx_freshness_floor(date(2026, 8, 24), scheduled_plan) == midnight_0824
    assert fx_freshness_floor(date(2026, 8, 24), None) == midnight_0824
    assert fetched < fx_freshness_floor(date(2026, 8, 24), scheduled_plan)  # yesterday's last-good still stale
    worker_source = (ROOT / "pipelines" / "daily_chain_v2_worker.py").read_text(encoding="utf-8")
    assert 'fx_freshness_floor(business_day, task.get("created_at"))' in worker_source
    print("POSITIVE_OK fx freshness floor follows an early manual window and keeps midnight for scheduled runs")

    # ------------------------------------------------------------- business window follows the run start
    import daily_chain_v2_db as chain_db

    saved_env = os.environ.pop(chain_db.RUN_STARTED_AT_ENV, None)
    try:
        midnight_naive = datetime(2026, 8, 23, 15, 0)  # 2026-08-24 00:00 JST, naive UTC
        assert chain_db.business_window_utc("2026-08-24") == (midnight_naive, datetime(2026, 8, 24, 15, 0))
        os.environ[chain_db.RUN_STARTED_AT_ENV] = "2026-08-23T00:38:37.753775+00:00"  # early manual window
        early_start, early_end = chain_db.business_window_utc("2026-08-24")
        assert early_start == datetime(2026, 8, 23, 0, 38, 37, 753775) and early_end == datetime(2026, 8, 24, 15, 0)
        assert datetime(2026, 8, 23, 1, 7, 50) >= early_start  # fx attempt 2 observedAt now inside the window
        os.environ[chain_db.RUN_STARTED_AT_ENV] = "2026-08-23T18:30:05.000000+00:00"  # scheduled tick
        assert chain_db.business_window_utc("2026-08-24")[0] == midnight_naive
        os.environ[chain_db.RUN_STARTED_AT_ENV] = ""
        assert chain_db.business_window_utc("2026-08-24")[0] == midnight_naive
    finally:
        if saved_env is None:
            os.environ.pop(chain_db.RUN_STARTED_AT_ENV, None)
        else:
            os.environ[chain_db.RUN_STARTED_AT_ENV] = saved_env
    orchestrator_source = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
    assert "RUN_STARTED_AT_ENV: export_run_started_at(self.journal.run(self.run_id))" in orchestrator_source
    # audit B5: the orchestrator process itself must carry the env, not only
    # its stage children, because plan_contract_repair_tasks reads the window
    # in-process.
    assert "export_run_started_at(row)" in inspect.getsource(DailyChainV2.initialise)
    assert chain_module.RUN_STARTED_AT_ENV == chain_db.RUN_STARTED_AT_ENV
    print("POSITIVE_OK business window opens at the run start for an early manual window and stays the JST day for scheduled runs")

finally:
    cleanup()
