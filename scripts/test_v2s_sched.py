#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scheduler fixtures for CARDZ Daily Chain V2 (work package v2s-sched).

Covers audit P1-1 (execute_ready refill pump), P2-3 (sliced tick sleep),
P2-1 (health liveness / skip branch) and the operator's 2026-08-24 finding that
a worker longer than the tick budget can never finish (drain mode).

Every check drives the real orchestrator objects: a private temp sqlite journal,
fake claims, no MySQL, no browser, no notification, no process that outlives
this file.  health.json is redirected into the temp workspace before the first
orchestrator call so the live runtime document is never touched.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time as real_time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2s-sched-"))
os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "health.json")
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2 as chain_module  # noqa: E402
import daily_chain_v2_adapters as adapters_module  # noqa: E402
from daily_chain_v2 import (  # noqa: E402
    TICK_RESERVE_SECONDS,
    DailyChainV2,
    build_health_document,
    health_path,
    write_health_document,
)
from daily_chain_v2_contract import RetryDecision, SourceTask  # noqa: E402
from daily_chain_v2_journal import Journal, iso, utc_now  # noqa: E402

DAY = date(2026, 8, 24)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"


def cleanup() -> None:
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def new_journal(name: str) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    now = utc_now()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(now + timedelta(hours=6)),
        sla_at=iso(now + timedelta(hours=7)),
        final_at=iso(now + timedelta(hours=12)),
    )
    return journal


def new_chain(journal: Journal, *, runtime_seconds: float = 600.0) -> DailyChainV2:
    now = utc_now()
    return DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=real_time.monotonic() + runtime_seconds,
        schedule={
            "source_cutoff": now + timedelta(hours=6),
            "sla": now + timedelta(hours=7),
            "final": now + timedelta(hours=12),
        },
    )


def add_source_task(
    journal: Journal,
    code: str,
    *,
    required_class: str = "core",
    max_attempts: int = 3,
) -> str:
    task = SourceTask(
        run_id=RUN_ID,
        business_date=DAY.isoformat(),
        source_code=code,
        capability="quote",
    )
    journal.add_task(
        task,
        phase="source",
        required_class=required_class,
        concurrency_group=f"fixture:{code}",
        max_concurrency=1,
        max_attempts=max_attempts,
        payload={"kind": "fixture"},
    )
    return task.idempotency_key


def sql(journal: Journal, statement: str, params: tuple[Any, ...] = ()) -> None:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.execute(statement, params)
        conn.commit()


class FakeClock:
    """Monotonic clock that only advances when the code under test sleeps."""

    def __init__(self, start: float = 10_000.0) -> None:
        self.now = float(start)
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))
        self.now += float(seconds)

    def __getattr__(self, name: str) -> Any:  # every other time.* stays real
        return getattr(real_time, name)


try:
    # ------------------------------------------------------------------ (i)
    # audit P1-1: a task that fails in 0.2 s with a 1 s backoff must be
    # re-claimed while a slow sibling is still running.  Measured harm on
    # 2026-08-24: gemrate shard 3 failed at +0.3m, was due at +1.3m, actually
    # restarted at +15.3m -- the batch barrier made it wait for the slowest
    # sibling in the same claim_ready() snapshot.
    pump_journal = new_journal("pump")
    fast_key = add_source_task(pump_journal, "fastsrc")
    slow_key = add_source_task(pump_journal, "slowsrc")
    pump_chain = new_chain(pump_journal)
    SLOW_SECONDS = 5.0
    starts: list[tuple[str, int, float]] = []
    plan_calls: list[Any] = []
    origin = real_time.monotonic()

    def fake_execute_claim(row: Any) -> None:
        key = str(row["task_key"])
        claim = str(row["lease_token"])
        attempt = int(row["attempts"])
        starts.append((key, attempt, real_time.monotonic() - origin))
        if key == slow_key:
            real_time.sleep(SLOW_SECONDS)
            pump_journal.finish_success(key, claim, {"stage": "fixture"})
            return
        real_time.sleep(0.2)
        if attempt == 1:
            pump_journal.finish_failure(
                key,
                claim,
                decision=RetryDecision("SOURCE_FAILED", False, (1,)),
                error_text="fixture transient",
            )
            return
        pump_journal.finish_success(key, claim, {"stage": "fixture"})

    pump_chain.execute_claim = fake_execute_claim  # type: ignore[method-assign]
    pump_chain.plan = lambda now: plan_calls.append(now)  # type: ignore[method-assign]
    pump_chain.eligible_phases = lambda: ("source",)  # type: ignore[method-assign]
    pump_chain.write_health = lambda **kwargs: None  # type: ignore[method-assign]
    started = pump_chain.execute_ready()

    retries = [row for row in starts if row[0] == fast_key and row[1] == 2]
    assert retries, (
        "execute_ready never re-claimed the failed task inside one call: "
        f"{starts}"
    )
    assert retries[0][2] < SLOW_SECONDS - 1.0, (
        f"retry waited for the slow sibling: started at {retries[0][2]:.2f}s "
        f"with a {SLOW_SECONDS}s sibling still running"
    )
    assert plan_calls, "the pump never gave plan() a chance between refills"
    assert started == 3, f"expected 2 claims + 1 refill, got {started}"
    assert (
        str((pump_journal.task(fast_key) or {}).get("status") or "") == "COMPLETED"
    )
    assert (
        str((pump_journal.task(slow_key) or {}).get("status") or "") == "COMPLETED"
    )
    print(
        "POSITIVE_OK execute_ready refills: a failed task retries on its own "
        f"backoff ({retries[0][2]:.2f}s) instead of the slow sibling's {SLOW_SECONDS}s"
    )

    # ----------------------------------------------------------------- (ii)
    # audit P2-3: one time.sleep(1800) held the exclusive tick flock for half an
    # hour without health or recover_expired().  The wait must be sliced.
    sleep_journal = new_journal("sleep")
    sleep_chain = new_chain(sleep_journal)
    clock = FakeClock()
    sleep_chain.deadline_monotonic = clock.now + 3000.0
    health_writes: list[str] = []
    recover_calls: list[float] = []
    retry_waits = [1800.0, None]

    sleep_chain.finalise_live = lambda: None  # type: ignore[method-assign]
    sleep_chain.plan = lambda now: None  # type: ignore[method-assign]
    sleep_chain.execute_ready = lambda: 0  # type: ignore[method-assign]
    sleep_chain.lifecycle_events = lambda now: None  # type: ignore[method-assign]
    sleep_chain.deliver_events = lambda: None  # type: ignore[method-assign]
    sleep_chain.recover_expired = lambda: recover_calls.append(clock.now)  # type: ignore[method-assign]
    sleep_chain.next_retry_wait = lambda now: (  # type: ignore[method-assign]
        retry_waits.pop(0) if retry_waits else None
    )
    sleep_chain.write_health = lambda **kwargs: health_writes.append(  # type: ignore[method-assign]
        str(kwargs.get("tick_phase") or "")
    )

    saved_time = chain_module.time
    chain_module.time = clock  # type: ignore[assignment]
    try:
        sleep_chain.run_tick()
    finally:
        chain_module.time = saved_time

    assert clock.slept, "run_tick never waited"
    assert max(clock.slept) <= 60.0 + 1e-6, (
        f"tick slept {max(clock.slept)}s in one piece; slices must be <=60s"
    )
    assert len(clock.slept) >= 30, (
        f"a 1800s wait was executed as {len(clock.slept)} slice(s): {clock.slept[:4]}"
    )
    assert abs(sum(clock.slept) - 1800.05) < 1e-6, (
        f"slicing changed the total wall clock: {sum(clock.slept)}"
    )
    waiting = [phase for phase in health_writes if phase == "waiting"]
    assert len(waiting) == len(clock.slept), (
        f"health written {len(waiting)} times for {len(clock.slept)} slices"
    )
    assert "working" in health_writes, (
        "a running tick never wrote health with tick_phase='working' (audit P2-1a)"
    )
    assert len(recover_calls) >= len(clock.slept), (
        f"recover_expired ran {len(recover_calls)} times for {len(clock.slept)} slices"
    )
    print(
        f"POSITIVE_OK a 1800s tick wait runs as {len(clock.slept)} slices "
        f"(max {max(clock.slept)}s) with health + recover_expired per slice"
    )

    # Lower bound: next_retry_wait 0.0 with a full concurrency group must not
    # spin ~20x/s through plan()+claim_ready().
    spin_journal = new_journal("spin")
    spin_chain = new_chain(spin_journal)
    spin_clock = FakeClock()
    spin_chain.deadline_monotonic = spin_clock.now + 3000.0
    spin_waits: list[float | None] = [0.0, None]
    spin_chain.finalise_live = lambda: None  # type: ignore[method-assign]
    spin_chain.plan = lambda now: None  # type: ignore[method-assign]
    spin_chain.execute_ready = lambda: 0  # type: ignore[method-assign]
    spin_chain.lifecycle_events = lambda now: None  # type: ignore[method-assign]
    spin_chain.deliver_events = lambda: None  # type: ignore[method-assign]
    spin_chain.recover_expired = lambda: None  # type: ignore[method-assign]
    spin_chain.write_health = lambda **kwargs: None  # type: ignore[method-assign]
    spin_chain.next_retry_wait = lambda now: (  # type: ignore[method-assign]
        spin_waits.pop(0) if spin_waits else None
    )
    saved_time = chain_module.time
    chain_module.time = spin_clock  # type: ignore[assignment]
    try:
        spin_chain.run_tick()
    finally:
        chain_module.time = saved_time
    assert spin_clock.slept == [1.0], (
        f"idle floor is {spin_clock.slept}; a zero wait must sleep 1.0s, not 0.05s"
    )
    print("POSITIVE_OK a zero next_retry_wait sleeps the 1.0s floor, not 0.05s")

    # ---------------------------------------------------------------- (iii)
    # audit P2-1(b): TICK_SKIPPED_LOCKED must not refresh health.json's
    # written_at_utc, or the watchdog's 25-minute staleness rule
    # (scripts/watchdog_live_release.ps1) can never see a stuck tick that is
    # still holding the flock.
    skip_journal = new_journal("skip")
    os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "skip-health.json")
    write_health_document(build_health_document(skip_journal, DAY, tick_phase="working"))
    before = json.loads(health_path().read_text(encoding="utf-8"))
    real_time.sleep(0.02)
    marker_path = chain_module.report_tick_skipped(DAY)
    after = json.loads(health_path().read_text(encoding="utf-8"))
    assert after["written_at_utc"] == before["written_at_utc"], (
        "the skip branch refreshed health.json written_at_utc: "
        f"{before['written_at_utc']} -> {after['written_at_utc']}"
    )
    assert after["tick_phase"] == "working", after["tick_phase"]
    marker = json.loads(Path(marker_path).read_text(encoding="utf-8"))
    assert marker["last_skipped_at_utc"] > before["written_at_utc"], marker
    assert marker["reason"] == "TICK_SKIPPED_LOCKED", marker
    assert marker["business_date"] == DAY.isoformat(), marker
    assert Path(marker_path).parent == health_path().parent, marker_path
    assert Path(marker_path) != health_path(), marker_path
    os.environ["CARDZ_V2_HEALTH_PATH"] = str(WORKSPACE / "health.json")
    print(
        "POSITIVE_OK TICK_SKIPPED_LOCKED records its own marker and leaves "
        "health.json's freshness stamp alone"
    )

    # ----------------------------------------------------------------- (iv)
    # Operator finding 2026-08-24: task gemrate:contract-repair:pop:all needs
    # ~55-60 min; every tick deadline interrupted it at ~48 min and restarted
    # the fetch from zero, spending the interruption budget until PARKED.
    # Drain: past the claiming deadline a live, freshly heartbeating claim keeps
    # running to the drain ceiling; a stale one is still interrupted on time.
    drain_journal = new_journal("drain")
    drain_key = add_source_task(drain_journal, "drainsrc")
    drain_chain = new_chain(drain_journal)
    # Claiming is already closed: this is exactly the moment the old code
    # interrupted a healthy worker.
    drain_chain.deadline_monotonic = real_time.monotonic() - 1.0
    drain_chain.drain_deadline_monotonic = drain_chain.deadline_monotonic + 2400.0
    claimed = drain_journal.claim_ready(RUN_ID, phases=("source",), limit=4)
    assert len(claimed) == 1, claimed
    row = claimed[0]

    healthy_deadline = drain_chain._work_deadline_monotonic(row)
    assert healthy_deadline > drain_chain.deadline_monotonic + 100.0, (
        "a healthy in-flight worker was cut at the tick deadline: "
        f"{healthy_deadline - drain_chain.deadline_monotonic:.1f}s of drain"
    )
    assert healthy_deadline <= drain_chain.drain_deadline_monotonic + 1e-6

    # The drain ceiling is a real bound, not "run forever".
    assert (
        real_time.monotonic() + 3600.0 >= drain_chain.drain_deadline_monotonic
    ), "drain ceiling must be reachable inside an hour"

    sql(
        drain_journal,
        "UPDATE chain_task SET heartbeat_at=? WHERE task_key=?",
        (iso(utc_now() - timedelta(seconds=600)), drain_key),
    )
    stale_deadline = drain_chain._work_deadline_monotonic(row)
    assert stale_deadline == drain_chain.deadline_monotonic, (
        f"a stale heartbeat earned {stale_deadline - drain_chain.deadline_monotonic:.1f}s "
        "of drain; only live work may drain"
    )

    # A claim that is no longer ours must not drain either.
    sql(
        drain_journal,
        "UPDATE chain_task SET heartbeat_at=?,lease_token='someone-else' WHERE task_key=?",
        (iso(utc_now()), drain_key),
    )
    lost_deadline = drain_chain._work_deadline_monotonic(row)
    assert lost_deadline == drain_chain.deadline_monotonic, (
        "a lost claim drained past the tick deadline"
    )
    print(
        "POSITIVE_OK drain keeps a live claim past the tick deadline and still "
        "interrupts a stale or lost one on time"
    )

    # The drain ceiling never outlives the external Task Scheduler limit.
    saved_limit = os.environ.pop("CARDZ_V2_TICK_HARD_LIMIT_SECONDS", None)
    try:
        os.environ["CARDZ_V2_TICK_HARD_LIMIT_SECONDS"] = "120"
        capped = new_chain(new_journal("cap"), runtime_seconds=60.0)
        assert (
            capped.drain_deadline_monotonic
            <= capped.tick_started_monotonic + 120.0 + 1e-6
        ), "drain ignored the external scheduler execution-time limit"
        assert capped.drain_deadline_monotonic >= capped.deadline_monotonic
        os.environ["CARDZ_V2_TICK_HARD_LIMIT_SECONDS"] = "100000"
        roomy = new_chain(new_journal("roomy"), runtime_seconds=60.0)
        assert abs(
            roomy.drain_deadline_monotonic
            - (roomy.deadline_monotonic + chain_module.TICK_DRAIN_CEILING_SECONDS)
        ) < 1e-6, "drain ceiling is not TICK_DRAIN_CEILING_SECONDS past the tick deadline"
    finally:
        if saved_limit is None:
            os.environ.pop("CARDZ_V2_TICK_HARD_LIMIT_SECONDS", None)
        else:
            os.environ["CARDZ_V2_TICK_HARD_LIMIT_SECONDS"] = saved_limit
    print(
        "POSITIVE_OK the drain ceiling is min(tick deadline + "
        f"{chain_module.TICK_DRAIN_CEILING_SECONDS}s, external execution-time limit)"
    )

    # ---------------------------------------------------------------- (iv-b)
    # The source worker poll loop must RE-READ its deadline, otherwise drain is
    # invisible to the one code path the 2026-08-24 evidence came from
    # ("tick deadline interrupted source worker").
    adapter = adapters_module.build_default_registry().get("fx")
    deadline_reads: list[float] = []

    def moving_deadline() -> float:
        deadline_reads.append(real_time.monotonic())
        return real_time.monotonic() + (1000.0 if len(deadline_reads) < 3 else -1.0)

    class FakeProc:
        pid = 4242
        returncode = 0

        def poll(self) -> int | None:
            return None

    saved_popen = adapters_module.subprocess.Popen
    saved_terminate = adapters_module.terminate_worker_group
    killed: list[int] = []
    adapters_module.subprocess.Popen = lambda *a, **k: FakeProc()  # type: ignore[assignment]
    adapters_module.terminate_worker_group = lambda pid, **k: killed.append(int(pid))  # type: ignore[assignment]
    worker_task = SourceTask(
        run_id=RUN_ID, business_date=DAY.isoformat(),
        source_code="fx", capability="rates",
    )
    worker_context = {
        "state_db": WORKSPACE / "adapter.sqlite3",
        "task_key": "fixture-task",
        "claim_token": "fixture-claim",
        "log_path": WORKSPACE / "adapter.log",
        "receipt_path": WORKSPACE / "adapter.json",
        "deadline_monotonic": moving_deadline,
    }
    try:
        raised = None
        try:
            adapter.execute(worker_task, worker_context, lambda **kwargs: None)
        except adapters_module.WorkerInterrupted as error:
            raised = error
        assert raised is not None, "a callable work deadline never interrupted the worker"
        assert len(deadline_reads) >= 3, (
            f"the worker froze its deadline at claim time ({len(deadline_reads)} read(s))"
        )
        assert killed == [FakeProc.pid], killed
        # The old float contract must keep working unchanged.
        killed.clear()
        raised = None
        try:
            adapter.execute(
                worker_task,
                {**worker_context, "deadline_monotonic": real_time.monotonic() - 1.0},
                lambda **kwargs: None,
            )
        except adapters_module.WorkerInterrupted as error:
            raised = error
        assert raised is not None and killed == [FakeProc.pid]
    finally:
        adapters_module.subprocess.Popen = saved_popen  # type: ignore[assignment]
        adapters_module.terminate_worker_group = saved_terminate  # type: ignore[assignment]
    print(
        "POSITIVE_OK the source worker re-reads its work deadline every poll, so "
        "drain reaches the path that produced the 2026-08-24 interruptions"
    )

    assert TICK_RESERVE_SECONDS == 30
finally:
    cleanup()
