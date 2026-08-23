#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Drive the real V2 tick loop against in-process fake workers (audit P2-16).

scripts/test_daily_chain_v2.py and scripts/test_daily_chain_v2_durability.py
never call run_tick()/execute_ready()/execute_claim()/plan(): they read
daily_chain_v2.py as text and assert on substrings.  Bucket C of
docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md says that makes every behavioural
change in buckets A/B a blind change, so this file is the one fixture that
actually runs the loop:

  * one temp SQLite journal per check.  No MySQL, no data/, no live tree:
    CARDZ_V2_HEALTH_PATH is redirected into the temp workspace BEFORE
    daily_chain_v2 is imported, and every chain's runtime/log/receipt dirs are
    overridden too.
  * a fake adapter registry whose workers run IN PROCESS -- no subprocess, no
    network, no browser.  Only CommandSourceAdapter.execute() and
    DailyChainV2._run_stage_process() are replaced; plan()/ingest(), the
    classifier, the retry ladder, the journal, the barriers, the refilling
    pump and the drain arithmetic are all production code.
  * short deadline_monotonic budgets, so the claiming deadline and the drain
    happen in seconds instead of in an hour.

Every check asserts the chain SPEAKS or moves FASTER.  Nothing here may loosen
a gate; the settled-state sets are read from the journal module, never
re-declared.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-loop-"))
HEALTH_DIR = WORKSPACE / "health"
HEALTH_DIR.mkdir(parents=True, exist_ok=True)
# Before the import on purpose.  health_path() reads the variable per call, but
# a mistake here would write into data/runtime/daily-chain-v2/, which the live
# tree publishes from.
os.environ["CARDZ_V2_HEALTH_PATH"] = str(HEALTH_DIR / "health.json")
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2 as chain_module  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_adapters import (  # noqa: E402
    CommandSourceAdapter,
    WorkerInterrupted,
    resolve_deadline,
)
from daily_chain_v2_contract import AdapterRegistry, SourceSpec  # noqa: E402
from daily_chain_v2_journal import (  # noqa: E402
    SUCCESS_TASK_STATES,
    TERMINAL_TASK_STATES,
    Journal,
    iso,
    utc_now,
)

DAY = date(2026, 8, 21)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
# classify_error() reads the retry-after capture out of this text and returns
# TRANSIENT_SOURCE with a REAL one second first delay, which is what makes a
# ten second pump fixture possible without touching a production constant.
RETRY_AFTER_1S = "HTTP 429 from fixture source; retry-after: 1"
TERMINAL_TEXT = "contract mismatch: fixture core source"
TRANSIENT_60S = "HTTP 503 from fixture stage"
ACCEPT_RECEIPT: dict[str, Any] = {
    "stage": "accept",
    "status": "completed",
    "publicGenerationId": "fixture-generation-1",
    "contentSha256": "a1" * 32,
    "activeCount": 3,
}
PENDING_IDS = [101, 102]

CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((name, fn))
        return fn

    return register


# ---------------------------------------------------------------------------
# Fixture doubles.  Both keep the shape of the code they replace: heartbeat,
# then poll the LIVE work deadline every cycle (never freeze it at claim time),
# then hand back a receipt the production ingest path has to validate.
# ---------------------------------------------------------------------------
class FixtureSource(CommandSourceAdapter):
    """A registered source whose worker is a function, not a subprocess.

    plan() and ingest() are inherited, so the journal payload, the receipt
    contract and _worker_error_text() -> classify_error() all run for real.
    """

    def __init__(
        self,
        spec: SourceSpec,
        *,
        duration: float = 0.0,
        failures: int = 0,
        error_text: str = RETRY_AFTER_1S,
        poll_seconds: float = 0.1,
    ) -> None:
        super().__init__(spec=spec, worker_kind="fixture", worker_payload={"fixture": True})
        self.duration = float(duration)
        self.failures = int(failures)
        self.error_text = error_text
        self.poll_seconds = float(poll_seconds)
        self.attempts = 0
        self.runs: list[dict[str, Any]] = []
        # (monotonic when asked, deadline the orchestrator answered).
        self.deadline_reads: list[tuple[float, float]] = []

    def execute(
        self,
        task: Any,
        context: Mapping[str, Any],
        heartbeat: Any,
    ) -> Mapping[str, Any]:
        self.attempts += 1
        attempt = self.attempts
        started = time.monotonic()
        ends_at = started + self.duration
        # worker_pid stays None on purpose: recover_expired() and
        # terminate_worker_group() take a recorded pid seriously, and this
        # "worker" is a thread inside the test process.
        heartbeat(worker_pid=None, checkpoint={"state": "worker_started", "attempt": attempt})
        while True:
            now = time.monotonic()
            deadline = resolve_deadline(context["deadline_monotonic"])
            self.deadline_reads.append((now, deadline))
            if now >= deadline:
                raise WorkerInterrupted(
                    f"tick deadline interrupted fixture worker task={context['task_key']}"
                )
            if now >= ends_at:
                break
            heartbeat(
                worker_pid=None,
                checkpoint={"state": "worker_running", "attempt": attempt},
            )
            time.sleep(min(self.poll_seconds, max(0.001, ends_at - now)))
        failed = attempt <= self.failures
        self.runs.append(
            {
                "attempt": attempt,
                "started": started,
                "ended": time.monotonic(),
                "failed": failed,
            }
        )
        receipt: dict[str, Any] = {
            "sourceCode": task.source_code,
            "status": "terminal" if failed else "completed",
            "observedAt": iso(),
            "checkedAt": iso(),
            "counts": {"rows": 0 if failed else 1},
        }
        if failed:
            receipt["error"] = self.error_text
        return {
            "exitCode": 1 if failed else 0,
            "receipt": receipt,
            "receiptPath": str(context["receipt_path"]),
            "logPath": str(context["log_path"]),
        }


class FixtureStages:
    """In-process replacement for DailyChainV2._run_stage_process.

    Mirrors the real method: heartbeat once, then check the live work deadline
    exactly like the subprocess poll loop does, so an identity stage past the
    10:15 cutoff is interrupted here for the same reason it is interrupted in
    production.
    """

    def __init__(
        self,
        chain: DailyChainV2,
        *,
        receipts: Mapping[str, Mapping[str, Any]] | None = None,
        errors: Mapping[str, str] | None = None,
        interrupts: Mapping[str, int] | None = None,
    ) -> None:
        self.chain = chain
        self.receipts = dict(receipts or {})
        self.errors = dict(errors or {})
        # capability -> how many of its claims are cut the way a tick deadline
        # cuts them (_run_stage_process raises WorkerInterrupted from its poll).
        self.interrupts = dict(interrupts or {})
        self.calls: list[str] = []

    def __call__(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.loads(str(row["payload_json"]))
        if str(payload.get("kind") or "stage") == "release":
            raise AssertionError("fixture must never reach the release stage")
        capability = str(row["capability"])
        self.calls.append(capability)
        self.chain._heartbeat(row, {"state": "stage_running"}, None)
        if time.monotonic() >= self.chain._work_deadline_monotonic(row):
            raise WorkerInterrupted(
                f"tick deadline interrupted stage capability={capability}"
            )
        remaining = int(self.interrupts.get(capability) or 0)
        if remaining > 0:
            self.interrupts[capability] = remaining - 1
            raise WorkerInterrupted(
                f"tick deadline interrupted stage capability={capability}"
            )
        if capability in self.errors:
            raise RuntimeError(self.errors[capability])
        receipt = dict(self.receipts.get(capability) or {})
        receipt.setdefault("stage", str(payload.get("stageName") or capability))
        receipt.setdefault("status", "completed")
        return receipt


# ---------------------------------------------------------------------------
# Fixture plumbing.
# ---------------------------------------------------------------------------
def spec(
    source_code: str,
    *,
    required_class: str = "quote",
    capabilities: tuple[str, ...] = ("quote",),
) -> SourceSpec:
    return SourceSpec(
        source_code=source_code,
        capabilities=capabilities,
        transport="fixture",
        concurrency_group=f"fixture:{source_code}",
        max_concurrency=1,
        cadence="daily",
        freshness_sla_minutes=405,
        required_class=required_class,
        adapter_version="1",
    )


def schedule_for(cutoff_offset_seconds: float) -> dict[str, datetime]:
    base = utc_now()
    return {
        "start": base - timedelta(hours=1),
        "source_cutoff": base + timedelta(seconds=float(cutoff_offset_seconds)),
        "sla": base + timedelta(hours=6),
        "final": base + timedelta(hours=8),
    }


def new_journal(name: str, *, cutoff_offset_seconds: float = 3600.0) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    schedule = schedule_for(cutoff_offset_seconds)
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(schedule["source_cutoff"]),
        sla_at=iso(schedule["sla"]),
        final_at=iso(schedule["final"]),
    )
    return journal


def new_chain(
    journal: Journal,
    name: str,
    *,
    adapters: Sequence[Any] = (),
    runtime_seconds: float = 120.0,
    cutoff_offset_seconds: float = 3600.0,
    stage_receipts: Mapping[str, Mapping[str, Any]] | None = None,
    stage_errors: Mapping[str, str] | None = None,
    stage_interrupts: Mapping[str, int] | None = None,
) -> tuple[DailyChainV2, FixtureStages]:
    work = WORKSPACE / name
    (work / "logs").mkdir(parents=True, exist_ok=True)
    (work / "receipts").mkdir(parents=True, exist_ok=True)
    chain = DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + float(runtime_seconds),
        schedule=schedule_for(cutoff_offset_seconds),
    )
    chain.runtime_dir = work
    chain.log_dir = work / "logs"
    chain.receipt_dir = work / "receipts"
    chain.registry = AdapterRegistry(adapters)
    chain.tick_started_at_utc = iso(utc_now())
    stages = FixtureStages(
        chain,
        receipts=stage_receipts,
        errors=stage_errors,
        interrupts=stage_interrupts,
    )
    chain._run_stage_process = stages
    return chain, stages


def count_plan_calls(chain: DailyChainV2) -> dict[str, int]:
    """Count real plan() passes without changing what plan() does."""

    counter = {"calls": 0}
    inner = chain.plan

    def counted(now: datetime) -> None:
        counter["calls"] += 1
        inner(now)

    chain.plan = counted  # type: ignore[method-assign]
    return counter


def sql(journal: Journal, statement: str, params: tuple[Any, ...] = ()) -> None:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.execute(statement, params)
        conn.commit()


def events(journal: Journal, event_type: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM chain_event WHERE event_type=? ORDER BY created_at,event_key",
            (event_type,),
        ).fetchall()
    return [dict(row) for row in rows]


def attempts_of(journal: Journal, task_key: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM chain_attempt WHERE task_key=? ORDER BY attempt_no",
            (task_key,),
        ).fetchall()
    return [dict(row) for row in rows]


def source_row(journal: Journal, source_code: str) -> dict[str, Any]:
    rows = [
        row
        for row in journal.tasks(RUN_ID, phase="source")
        if str(row["source_code"]) == source_code
    ]
    assert len(rows) == 1, f"expected one {source_code} task, got {len(rows)}"
    return rows[0]


def stage_row(journal: Journal, capability: str) -> dict[str, Any]:
    rows = [row for row in journal.tasks(RUN_ID) if str(row["capability"]) == capability]
    assert len(rows) == 1, f"expected one {capability} stage, got {len(rows)}"
    return rows[0]


def run_quiet(fn: Callable[[], Any]) -> str:
    """Run `fn` capturing stdout, so NOTIFY_DRYRUN lines can be counted."""

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        fn()
    return buffer.getvalue()


def dryrun_alerts(output: str, key_prefix: str) -> list[str]:
    return [
        line
        for line in output.splitlines()
        if line.startswith(f"NOTIFY_DRYRUN {key_prefix}")
    ]


# ---------------------------------------------------------------------------
# audit P1-1: the pump refills on completion instead of joining a batch.
# ---------------------------------------------------------------------------
@check("P1-1 a fast failure is re-claimed on its own backoff while a slow sibling still runs")
def pump_reclaims_failed_task_before_slow_sibling() -> None:
    journal = new_journal("pump")
    fast = FixtureSource(spec("fastsrc"), duration=0.2, failures=1)
    # The medium sibling is load-bearing: its completion is what proves the
    # refill is COMPLETION driven.  EXECUTE_REFILL_IDLE_INTERVAL_SECONDS (5.0)
    # keeps its production value, so an idle-wake refill could not have
    # re-claimed the fast task this early.
    medium = FixtureSource(spec("medsrc"), duration=2.0)
    slow = FixtureSource(spec("slowsrc"), duration=4.5)
    chain, stages = new_chain(
        journal, "pump", adapters=(fast, medium, slow), runtime_seconds=120.0
    )
    run_quiet(chain.run_tick)

    assert chain_module.EXECUTE_REFILL_IDLE_INTERVAL_SECONDS == 5.0
    assert len(fast.runs) == 2, f"fast source ran {len(fast.runs)} times"
    assert fast.runs[0]["failed"] is True and fast.runs[1]["failed"] is False
    fail_ended = fast.runs[0]["ended"]
    retry_started = fast.runs[1]["started"]
    slow_ended = slow.runs[0]["ended"]
    # The real ladder decided the wait: classify_error() read `retry-after: 1`.
    assert retry_started - fail_ended >= 1.0, retry_started - fail_ended
    # The whole point of P1-1: it did NOT wait for the slowest sibling.
    assert retry_started < slow_ended - 1.0, (retry_started, slow_ended)
    assert medium.runs[0]["ended"] <= retry_started + 0.5

    row = source_row(journal, "fastsrc")
    assert str(row["status"]) == "COMPLETED", row["status"]
    assert int(row["attempts"]) == 2
    codes = [str(item["error_code"] or "") for item in attempts_of(journal, str(row["task_key"]))]
    assert codes[0] == "TRANSIENT_SOURCE", codes
    for source_code in ("medsrc", "slowsrc"):
        assert str(source_row(journal, source_code)["status"]) == "COMPLETED"
    assert stages.calls, "infra stages never ran"


# ---------------------------------------------------------------------------
# operator finding 2026-08-24: drain past the claiming deadline.
# ---------------------------------------------------------------------------
@check("drain a healthy in-flight worker past the tick deadline without spending an interruption")
def healthy_worker_drains_past_the_tick_deadline() -> None:
    # A 30 s reserve would need a 35+ s fixture for the worker to outlive the
    # tick deadline; the reserve itself is pinned by scripts/test_v2s_sched.py,
    # so it is asserted here and restored below.
    assert chain_module.TICK_RESERVE_SECONDS == 30
    chain_module.TICK_RESERVE_SECONDS = 1.0
    try:
        journal = new_journal("drain")
        worker = FixtureSource(
            spec("drainsrc", required_class="core", capabilities=("pop",)),
            duration=5.0,
            poll_seconds=0.2,
        )
        chain, _ = new_chain(journal, "drain", adapters=(worker,), runtime_seconds=3.0)
        tick_deadline = chain.deadline_monotonic
        claiming_closes_at = tick_deadline - chain_module.TICK_RESERVE_SECONDS
        run_quiet(chain.run_tick)
    finally:
        chain_module.TICK_RESERVE_SECONDS = 30

    row = source_row(journal, "drainsrc")
    assert str(row["status"]) == "COMPLETED", row["status"]
    assert int(row["interruptions"]) == 0, row["interruptions"]
    assert int(row["attempts"]) == 1, row["attempts"]

    before = [value for at, value in worker.deadline_reads if at < claiming_closes_at]
    after = [(at, value) for at, value in worker.deadline_reads if at > tick_deadline]
    assert before and max(before) <= tick_deadline + 1e-6, max(before or [0.0])
    assert after, "the worker never polled past the tick deadline"
    granted = min(value for _, value in after)
    assert granted > tick_deadline + 600, granted - tick_deadline

    drain_events = events(journal, "TICK_DRAINING")
    assert len(drain_events) == 1, len(drain_events)
    payload = json.loads(str(drain_events[0]["payload_json"]))
    assert int(payload["inFlight"]) == 1, payload
    assert float(payload["drainSecondsGranted"]) > 0.0, payload
    assert payload["truncatedByExternalLimit"] is False, payload
    assert "errorCode" not in payload, payload


# ---------------------------------------------------------------------------
# audit P0-1: a core source that settles without success says so.
# ---------------------------------------------------------------------------
@check("P0-1 a core source settled TERMINAL pages once per state, not once per plan pass")
def blocked_core_source_pages_once_per_state() -> None:
    journal = new_journal("core-blocked")
    core = FixtureSource(
        spec("corefx", required_class="core", capabilities=("rates",)),
        failures=1,
        error_text=TERMINAL_TEXT,
    )
    # Keeps the tick turning for several plan() passes, so a per-pass alert
    # would be visible; otherwise irrelevant to the assertion.
    noisy = FixtureSource(spec("noisyq"), failures=1, error_text=RETRY_AFTER_1S)
    chain, _ = new_chain(journal, "core-blocked", adapters=(core, noisy), runtime_seconds=60.0)
    planned = count_plan_calls(chain)
    output = run_quiet(chain.run_tick)

    core_row = source_row(journal, "corefx")
    assert str(core_row["status"]) == "TERMINAL", core_row["status"]
    assert str(core_row["last_error_code"]) == "TERMINAL_CONTRACT", core_row["last_error_code"]
    assert planned["calls"] >= 4, planned
    parked = events(journal, "CORE_TASK_PARKED")
    assert len(parked) == 1, [json.loads(str(row["payload_json"])) for row in parked]
    payload = json.loads(str(parked[0]["payload_json"]))
    assert payload["state"] == "TERMINAL", payload
    assert payload["taskKey"] == str(core_row["task_key"]), payload
    assert "unpark" in str(payload["nextRetry"]), payload
    alerts = dryrun_alerts(output, "v2-task-parked:")
    assert len(alerts) == 1, alerts
    # The barrier itself stays exactly as strict: naming the row is the fix.
    assert str(journal.run(RUN_ID)["status"]) not in {"READY_FOR_CUTOVER", "PUBLISHED"}
    assert journal.tasks(RUN_ID, phase="accept") == []

    # A TERMINAL row that later PARKS is a second thing to go fix, so it pages
    # again instead of collapsing into the first row's cooldown (alert_scope).
    sql(
        journal,
        "UPDATE chain_task SET status='PARKED' WHERE task_key=?",
        (str(core_row["task_key"]),),
    )
    second = run_quiet(lambda: chain.plan(utc_now()))
    states = [
        json.loads(str(row["payload_json"]))["state"]
        for row in events(journal, "CORE_TASK_PARKED")
    ]
    assert states == ["TERMINAL", "PARKED"], states
    assert len(dryrun_alerts(second, "v2-task-parked:")) == 1, second


# ---------------------------------------------------------------------------
# audit P1-8: PARKED belongs in the settled set the later gates read.
# ---------------------------------------------------------------------------
@check("P1-8 a PARKED pending-identities stage past the cutoff still reaches daily-accept")
def parked_pending_identities_still_reaches_daily_accept() -> None:
    # The pre-fix literal was SUCCESS_TASK_STATES | {"TERMINAL"}; this check is
    # only meaningful while PARKED is outside it and inside TERMINAL_TASK_STATES.
    assert "PARKED" in TERMINAL_TASK_STATES
    assert "PARKED" not in SUCCESS_TASK_STATES | {"TERMINAL"}
    previous = os.environ.get("CARDZ_V2_MAX_INTERRUPTIONS")
    # The production route to PARKED is interrupt_claim() exhausting the
    # interruption budget, so the fixture cuts the stage the way a tick
    # deadline cuts it (WorkerInterrupted out of the stage poll) with a budget
    # of one.  The run is past the 10:15 cutoff, which is when a parked
    # optional stage used to hold the whole business date.
    os.environ["CARDZ_V2_MAX_INTERRUPTIONS"] = "1"
    try:
        journal = new_journal("parked-pending", cutoff_offset_seconds=-60.0)
        core = FixtureSource(spec("corefx", required_class="core", capabilities=("pop",)))
        chain, stages = new_chain(
            journal,
            "parked-pending",
            adapters=(core,),
            runtime_seconds=120.0,
            cutoff_offset_seconds=-60.0,
            stage_receipts={"daily-accept": {**ACCEPT_RECEIPT, "acceptedAt": iso()}},
            stage_interrupts={"pending-identities": 1},
        )
        run_quiet(chain.run_tick)
    finally:
        if previous is None:
            os.environ.pop("CARDZ_V2_MAX_INTERRUPTIONS", None)
        else:
            os.environ["CARDZ_V2_MAX_INTERRUPTIONS"] = previous

    pending = stage_row(journal, "pending-identities")
    assert str(pending["status"]) == "PARKED", pending["status"]
    assert int(pending["interruptions"]) == 1, pending["interruptions"]
    assert str(pending["last_error_code"]) == "WORKER_PARKED", pending["last_error_code"]
    assert "pending-identities" in stages.calls
    assert str(source_row(journal, "corefx")["status"]) == "COMPLETED"
    for capability in (
        "core-contract-pre",
        "candidate-activation",
        "core-contract-post",
        "daily-accept",
        "box",
    ):
        row = stage_row(journal, capability)
        assert str(row["status"]) == "COMPLETED", (capability, row["status"])
    assert str(journal.run(RUN_ID)["status"]) == "READY_FOR_CUTOVER"


# ---------------------------------------------------------------------------
# the 10:15 boundary defers candidate activation instead of holding today.
# ---------------------------------------------------------------------------
@check("candidate activation past the cutoff degrades visibly and daily-accept still lands")
def pending_identities_past_cutoff_defers_activation() -> None:
    journal = new_journal("cutoff-defer")
    core = FixtureSource(spec("corefx", required_class="core", capabilities=("pop",)))
    # Tick A runs before the 10:15 boundary: pending identities land, and the
    # candidate registry stage stalls on a 60 s transient so the tick ends with
    # the candidate lane still open.
    chain_a, _ = new_chain(
        journal,
        "cutoff-defer-a",
        adapters=(core,),
        runtime_seconds=chain_module.TICK_RESERVE_SECONDS + 5.0,
        cutoff_offset_seconds=3600.0,
        stage_receipts={"pending-identities": {"pendingActivationIds": list(PENDING_IDS)}},
        stage_errors={"candidate-collection-registry": TRANSIENT_60S},
    )
    run_quiet(chain_a.run_tick)
    pending = stage_row(journal, "pending-identities")
    assert str(pending["status"]) == "COMPLETED", pending["status"]
    assert json.loads(str(pending["result_json"]))["pendingActivationIds"] == PENDING_IDS
    assert str(stage_row(journal, "candidate-collection-registry")["status"]) == "RETRY"

    # Tick B stands for the next scheduled tick, after the boundary.  utc_now()
    # is real, so the fixture moves the boundary instead of the clock.
    chain_b, _ = new_chain(
        journal,
        "cutoff-defer-b",
        adapters=(core,),
        runtime_seconds=120.0,
        cutoff_offset_seconds=-60.0,
        stage_receipts={"daily-accept": {**ACCEPT_RECEIPT, "acceptedAt": iso()}},
    )
    run_quiet(chain_b.run_tick)

    activation = stage_row(journal, "candidate-activation")
    assert str(activation["status"]) == "DEGRADED", activation["status"]
    assert str(activation["last_error_code"]) == "CANDIDATE_ACTIVATION_CUTOFF"
    deferred = [
        json.loads(str(row["payload_json"]))
        for row in events(journal, "TASK_RETRY_STATE")
        if json.loads(str(row["payload_json"])).get("errorCode") == "CANDIDATE_ACTIVATION_CUTOFF"
    ]
    assert len(deferred) == 1, deferred
    assert deferred[0]["phase"] == "activation", deferred[0]
    assert int(deferred[0]["pendingCandidates"]) == len(PENDING_IDS), deferred[0]
    assert int(deferred[0]["closedTasks"]) >= 1, deferred[0]
    assert str(stage_row(journal, "daily-accept")["status"]) == "COMPLETED"
    assert str(stage_row(journal, "box")["status"]) == "COMPLETED"
    assert str(journal.run(RUN_ID)["status"]) == "READY_FOR_CUTOVER"


# ---------------------------------------------------------------------------
# audit P2-1(b): a skipped tick must not forge health freshness.
# ---------------------------------------------------------------------------
@check("P2-1 the skip-locked branch writes its own file and never refreshes health.json")
def skip_locked_does_not_refresh_health_freshness() -> None:
    journal = new_journal("skip-locked")
    chain, _ = new_chain(journal, "skip-locked", runtime_seconds=120.0)
    run_quiet(chain.run_tick)

    health = chain_module.health_path()
    first = json.loads(health.read_text(encoding="utf-8"))
    assert first["tick_phase"] in {"working", "waiting"}, first["tick_phase"]
    before_bytes = health.read_bytes()

    handle, acquired = chain_module.acquire_tick_lock(journal.path)
    assert acquired, "the fixture failed to take the tick lock"
    try:
        if chain_module.fcntl is not None:
            # A second open file description conflicts with the first flock even
            # inside one process: this is the CLI's "another tick holds it" branch.
            blocked_handle, blocked = chain_module.acquire_tick_lock(journal.path)
            assert blocked is False and blocked_handle is None
        skipped_path = chain_module.report_tick_skipped(DAY)
    finally:
        handle.close()

    assert health.read_bytes() == before_bytes, "a skipped tick refreshed health.json"
    skipped = json.loads(skipped_path.read_text(encoding="utf-8"))
    assert skipped_path.name == "tick-skipped.json", skipped_path
    assert skipped["reason"] == "TICK_SKIPPED_LOCKED", skipped
    assert skipped["business_date"] == DAY.isoformat(), skipped

    # Control: the working path DOES refresh it, so the assertion above is not
    # passing merely because health.json is unwritable here.
    chain.write_health_liveness("working")
    second = json.loads(health.read_text(encoding="utf-8"))
    assert second["written_at_utc"] > first["written_at_utc"], (
        first["written_at_utc"],
        second["written_at_utc"],
    )


def main() -> int:
    failures: list[str] = []
    try:
        for name, fn in CHECKS:
            started = time.monotonic()
            try:
                fn()
            except BaseException as error:  # noqa: BLE001 - report every check
                failures.append(name)
                print(f"FAIL {name}", flush=True)
                print(
                    "".join(traceback.format_exception(error)).rstrip(),
                    file=sys.stderr,
                    flush=True,
                )
            else:
                print(
                    f"POSITIVE_OK {name} ({time.monotonic() - started:.1f}s)",
                    flush=True,
                )
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
