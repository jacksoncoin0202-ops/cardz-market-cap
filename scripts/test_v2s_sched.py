#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Scheduler fixtures for CARDZ Daily Chain V2 (work package v2s-sched).

Covers audit P1-1 (execute_ready refill pump), P2-3 (sliced tick sleep),
P2-1 (health liveness / skip branch) and the operator's 2026-08-24 finding that
a worker longer than the tick budget can never finish (drain mode), plus the
review fixes: the run's `final` boundary the pump must not claim past, the
idle-refill throttle, the green TICK_DRAINING alert, the drain-budget
arithmetic, and the memoised drain liveness read.

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
import subprocess
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
RED = "\U0001f534"


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
    # The idle-refill throttle is switched off here so this check measures the
    # pump alone; check (i-b) below measures the throttle alone.
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
    saved_idle_interval = chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS
    chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = 0.0
    try:
        started = pump_chain.execute_ready()
    finally:
        chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = saved_idle_interval

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

    # ---------------------------------------------------------------- (i-b)
    # Review fix: a bare 2.0 s poll timeout means nothing completed, so nothing
    # new can be claimable except an expired backoff.  Re-running
    # eligible_phases() (seven reads) + claim_ready() (a write transaction)
    # every 2 s for a whole tick is ~1500 write transactions against sixteen
    # heartbeat writers on the same sqlite file.  Completions still refill
    # immediately; only the timeout path is throttled.
    throttle_journal = new_journal("throttle")
    throttle_key = add_source_task(throttle_journal, "lonesrc")
    throttle_chain = new_chain(throttle_journal)
    claim_calls: list[float] = []
    real_claim_batch = throttle_chain._claim_batch
    throttle_origin = real_time.monotonic()

    def counting_claim_batch(limit: int) -> list[dict[str, Any]]:
        claim_calls.append(real_time.monotonic() - throttle_origin)
        return real_claim_batch(limit)

    def lone_execute_claim(row: Any) -> None:
        real_time.sleep(5.0)
        throttle_journal.finish_success(
            str(row["task_key"]), str(row["lease_token"]), {"stage": "fixture"}
        )

    throttle_chain._claim_batch = counting_claim_batch  # type: ignore[method-assign]
    throttle_chain.execute_claim = lone_execute_claim  # type: ignore[method-assign]
    throttle_chain.plan = lambda now: None  # type: ignore[method-assign]
    throttle_chain.eligible_phases = lambda: ("source",)  # type: ignore[method-assign]
    throttle_chain.write_health = lambda **kwargs: None  # type: ignore[method-assign]
    chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = 3.0
    try:
        throttle_chain.execute_ready()
    finally:
        chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = saved_idle_interval
    throttled_window = [t for t in claim_calls if 0.6 < t < 2.9]
    assert not throttled_window, (
        "an idle poll wake re-claimed inside the throttle window: "
        f"{[round(t, 2) for t in claim_calls]}"
    )
    assert len(claim_calls) >= 2, claim_calls
    assert (
        str((throttle_journal.task(throttle_key) or {}).get("status") or "")
        == "COMPLETED"
    )
    print(
        "POSITIVE_OK an idle poll wake claims at most once per throttle window "
        f"(claims at {[round(t, 2) for t in claim_calls]})"
    )

    # ---------------------------------------------------------------- (i-c)
    # Review BLOCKER: run_tick breaks on `now >= schedule['final']` BEFORE it
    # reaches plan()/execute_ready(), so the batch barrier could never claim
    # past the run's final deadline.  The pump refills on its own clock inside
    # one call, so it must carry that boundary itself: past `final`,
    # _work_deadline_monotonic() is already now, and each such claim Popens a
    # real worker only to interrupt it on the first poll -- one interruption
    # each, six of them (DEFAULT_MAX_INTERRUPTIONS) park a core task inside a
    # single execute_ready().
    final_journal = new_journal("final")
    final_slow = add_source_task(final_journal, "slowsrc")
    final_late = add_source_task(final_journal, "latesrc")
    final_chain = new_chain(final_journal)
    # latesrc is on a short backoff: not claimable at t=0, due at t=1.0s, i.e.
    # after the run's final deadline has already gone by.
    sql(
        final_journal,
        "UPDATE chain_task SET status='RETRY',next_retry_at=? WHERE task_key=?",
        (iso(utc_now() + timedelta(seconds=1.0)), final_late),
    )
    final_claims: list[str] = []

    def final_execute_claim(row: Any) -> None:
        final_claims.append(str(row["task_key"]))
        # The run's final deadline passes while the slow sibling is in flight.
        final_chain.schedule["final"] = utc_now() - timedelta(seconds=1)
        real_time.sleep(3.0)
        final_journal.finish_success(
            str(row["task_key"]), str(row["lease_token"]), {"stage": "fixture"}
        )

    final_chain.execute_claim = final_execute_claim  # type: ignore[method-assign]
    final_chain.plan = lambda now: None  # type: ignore[method-assign]
    final_chain.eligible_phases = lambda: ("source",)  # type: ignore[method-assign]
    final_chain.write_health = lambda **kwargs: None  # type: ignore[method-assign]
    chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = 0.0
    try:
        final_started = final_chain.execute_ready()
    finally:
        chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = saved_idle_interval

    assert final_claims == [final_slow], (
        f"the pump claimed past the run's final deadline: {final_claims}"
    )
    assert final_started == 1, f"expected the one pre-final claim, got {final_started}"
    late_row = final_journal.task(final_late) or {}
    assert int(late_row.get("attempts") or 0) == 0, (
        f"latesrc was claimed after final: attempts={late_row.get('attempts')}"
    )
    assert int(late_row.get("interruptions") or 0) == 0, (
        "a claim past final spent an interruption on a worker that could not "
        f"run for one second: interruptions={late_row.get('interruptions')}"
    )
    assert str(late_row.get("status") or "") == "RETRY", late_row.get("status")
    assert not final_chain.may_claim(), "may_claim() stayed open past final"
    # The mechanism, not just the symptom: any row claimed now is born dead.
    born_dead = final_chain._work_deadline_monotonic(
        {"phase": "source", "required_class": "core"}
    )
    assert born_dead <= real_time.monotonic(), (
        f"work deadline past final is {born_dead - real_time.monotonic():.3f}s away"
    )
    print(
        "POSITIVE_OK the pump refuses to claim past the run's final deadline, "
        "so no core task can be parked by instant-kill claims"
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

    liveness_reads: list[str] = []
    real_task_read = drain_journal.task

    def counting_task_read(task_key: str) -> Any:
        liveness_reads.append(task_key)
        return real_task_read(task_key)

    drain_journal.task = counting_task_read  # type: ignore[method-assign]

    healthy_deadline = drain_chain._work_deadline_monotonic(row)
    assert healthy_deadline > drain_chain.deadline_monotonic + 100.0, (
        "a healthy in-flight worker was cut at the tick deadline: "
        f"{healthy_deadline - drain_chain.deadline_monotonic:.1f}s of drain"
    )
    assert healthy_deadline <= drain_chain.drain_deadline_monotonic + 1e-6

    # Review fix: every poll of every in-flight worker asks the same question,
    # and each miss opens a fresh sqlite connection.  Within the cache window
    # the journal is read exactly once.
    for _ in range(4):
        drain_chain._work_deadline_monotonic(row)
    assert len(liveness_reads) == 1, (
        "drain liveness hit the journal on every worker poll: "
        f"{len(liveness_reads)} reads for 5 polls"
    )

    # The drain ceiling is a real bound, not "run forever".
    assert (
        real_time.monotonic() + 3600.0 >= drain_chain.drain_deadline_monotonic
    ), "drain ceiling must be reachable inside an hour"

    # A cache, not a freeze: with the window closed every decision re-reads.
    saved_cache_ttl = chain_module.DRAIN_LIVENESS_CACHE_SECONDS
    chain_module.DRAIN_LIVENESS_CACHE_SECONDS = 0.0
    try:
        before_reads = len(liveness_reads)
        drain_chain._work_deadline_monotonic(row)
        assert len(liveness_reads) == before_reads + 1, liveness_reads

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
    finally:
        chain_module.DRAIN_LIVENESS_CACHE_SECONDS = saved_cache_ttl
        drain_journal.task = real_task_read  # type: ignore[method-assign]
    print(
        "POSITIVE_OK drain keeps a live claim past the tick deadline, reads the "
        "journal once per cache window, and still cuts a stale or lost claim"
    )

    # The drain ceiling never outlives the external Task Scheduler limit.
    saved_limit = os.environ.pop("CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS", None)
    try:
        os.environ["CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS"] = "600"
        capped = new_chain(new_journal("cap"), runtime_seconds=60.0)
        capped_hard = chain_module.tick_hard_limit_seconds()
        assert capped_hard == (
            600.0
            - chain_module.TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
            - chain_module.TICK_INTERRUPT_GRACE_MAX_SECONDS
            - chain_module.TICK_DRAIN_TAIL_RESERVE_SECONDS
        ), capped_hard
        assert (
            capped.drain_deadline_monotonic
            <= capped.tick_started_monotonic + capped_hard + 1e-6
        ), "drain ignored the external scheduler execution-time limit"
        assert capped.drain_deadline_monotonic > capped.deadline_monotonic
        os.environ["CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS"] = "100000"
        roomy = new_chain(new_journal("roomy"), runtime_seconds=60.0)
        assert abs(
            roomy.drain_deadline_monotonic
            - (roomy.deadline_monotonic + chain_module.TICK_DRAIN_CEILING_SECONDS)
        ) < 1e-6, "drain ceiling is not TICK_DRAIN_CEILING_SECONDS past the tick deadline"
    finally:
        if saved_limit is None:
            os.environ.pop("CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS", None)
        else:
            os.environ["CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS"] = saved_limit
    print(
        "POSITIVE_OK the drain ceiling is min(tick deadline + "
        f"{chain_module.TICK_DRAIN_CEILING_SECONDS}s, external execution-time limit)"
    )

    # ---------------------------------------------------------------- (iv-b)
    # Review MAJOR: a drain that ends at the bound still has to kill the worker
    # (60 s grace) and finish the tick (finalise + lifecycle + Telegram + health
    # + summary), and the tick's own clock starts AFTER the launcher.  All of
    # that must fit inside the installed ExecutionTimeLimit, or the drained tick
    # is designed to be hard-killed mid-finalisation.
    budget_chain = new_chain(
        new_journal("budget"),
        runtime_seconds=float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS),
    )
    external_limit = chain_module.tick_external_limit_seconds()
    latest_finish = (
        budget_chain.drain_deadline_monotonic
        + chain_module.TICK_INTERRUPT_GRACE_MAX_SECONDS
        + chain_module.TICK_DRAIN_TAIL_RESERVE_SECONDS
    )
    external_kill = (
        budget_chain.tick_started_monotonic
        + external_limit
        - chain_module.TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
    )
    assert latest_finish <= external_kill + 1e-6, (
        "a drained tick would be hard-killed mid-finalisation: it finishes "
        f"{latest_finish - external_kill:.0f}s after the external limit"
    )
    # ...and R2 (2026-08-24): under the INSTALLED numbers the drain is real.
    # The hard limit used to equal the CLI default, i.e. zero drain; claiming now
    # closes at DEFAULT_MAX_RUNTIME_SECONDS and the drain fills the rest of the
    # same PT55M window.  scripts/test_v2_tick_budget.py holds the .ps1 side.
    assert (
        chain_module.tick_hard_limit_seconds()
        - float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS)
        == float(chain_module.TICK_DRAIN_CEILING_SECONDS)
    ), chain_module.tick_hard_limit_seconds()
    installed_granted = (
        budget_chain.drain_deadline_monotonic - budget_chain.deadline_monotonic
    )
    assert installed_granted >= 600.0, (
        f"the installed tick was granted {installed_granted:.0f}s of drain"
    )
    # A smaller --max-runtime-seconds closes claiming earlier; the ceiling is
    # the same wall clock either way.
    short_tick = new_chain(new_journal("shorttick"), runtime_seconds=1800.0)
    granted = short_tick.drain_deadline_monotonic - short_tick.deadline_monotonic
    assert abs(granted - float(chain_module.TICK_DRAIN_CEILING_SECONDS)) < 1e-6, (
        f"a 1800s tick under the installed limit was granted {granted:.0f}s of drain"
    )
    print(
        "POSITIVE_OK the drain bound leaves room for the interrupt grace and the "
        f"finalisation tail inside the external limit ({external_limit:.0f}s), and "
        f"a 1800s tick still earns {granted:.0f}s of drain"
    )

    # ---------------------------------------------------------------- (iv-c)
    # Review MAJOR: a drain is the healthy path.  The red fallback in
    # _event_message() would page the operator on every good tick, which is
    # crying wolf on the exact alerting surface audit P2-1 exists to make honest.
    drain_event_journal = new_journal("drainevent")
    drain_event_chain = new_chain(drain_event_journal)
    drain_event_chain.report_drain(3)
    drain_events = [
        event
        for event in drain_event_journal.pending_events(RUN_ID)
        if str(event["event_type"]) == "TICK_DRAINING"
    ]
    assert len(drain_events) == 1, drain_events
    drain_payload = json.loads(str(drain_events[0]["payload_json"]))
    assert "errorCode" not in drain_payload, (
        f"a healthy drain carries an errorCode: {drain_payload}"
    )
    drain_message = DailyChainV2._event_message("TICK_DRAINING", drain_payload)
    assert RED not in drain_message, (
        f"TICK_DRAINING renders as a red alert: {drain_message!r}"
    )
    assert "draining" in drain_message, drain_message
    assert str(int(drain_payload["drainCeilingSeconds"])) in drain_message, drain_message
    # The red fallback itself must still be the default for unknown events.
    assert RED in DailyChainV2._event_message("SOMETHING_NEW", {"runId": RUN_ID})
    # Reported once per tick, not once per poll.
    drain_event_chain.report_drain(3)
    assert (
        len(
            [
                event
                for event in drain_event_journal.pending_events(RUN_ID)
                if str(event["event_type"]) == "TICK_DRAINING"
            ]
        )
        == 1
    )
    print(
        "POSITIVE_OK TICK_DRAINING carries no errorCode and renders green, while "
        "an unknown event still falls back to the red alert"
    )

    # ---------------------------------------------------------------- (iv-d)
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

    # Review fix: patch the module-local launcher, not `adapters_module.
    # subprocess.Popen` -- `adapters_module.subprocess` IS the shared subprocess
    # module object, so patching through it replaces Popen process-wide.
    assert hasattr(adapters_module, "_popen"), (
        "daily_chain_v2_adapters must expose a module-local worker launcher so a "
        "fixture never has to mutate the global subprocess module"
    )
    saved_popen = adapters_module._popen
    saved_global_popen = subprocess.Popen
    saved_terminate = adapters_module.terminate_worker_group
    killed: list[int] = []
    adapters_module._popen = lambda *a, **k: FakeProc()  # type: ignore[assignment]
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
        assert subprocess.Popen is saved_global_popen, (
            "the fixture leaked into the global subprocess module"
        )
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
        adapters_module._popen = saved_popen  # type: ignore[assignment]
        adapters_module.terminate_worker_group = saved_terminate  # type: ignore[assignment]
    print(
        "POSITIVE_OK the source worker re-reads its work deadline every poll, so "
        "drain reaches the path that produced the 2026-08-24 interruptions"
    )

    assert TICK_RESERVE_SECONDS == 30
finally:
    cleanup()
