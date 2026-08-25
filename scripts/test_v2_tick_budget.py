#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tick budget fixtures for CARDZ Daily Chain V2 (batch 2026-08-24, R2 + R3).

Three things are proven here, all of them arithmetic or accounting that the
2026-08-24 operator findings caught being wrong in production:

  R2  Under the INSTALLED numbers (Task Scheduler ExecutionTimeLimit PT55M plus
      the launcher's -MaxRuntimeSeconds) a tick must grant a REAL drain window:
      once claiming closes, work that is still alive keeps running.  The
      installed numbers live in the .ps1 files and in python, so this file
      parses the .ps1 and asserts the three sides agree -- nobody may change
      one side alone.

  R3  The SIGTERM->SIGKILL grace is per worker kind: a collect/gemrate child
      needs ~240 s to finish its current step and get its manifest ingested
      ("never abandon captured payloads"), and the tick's hard limit must
      subtract that same worst case so PT55M is never breached.  The worker
      receipt names an interrupted child.

  A   Attempt accounting: an interruption at the tick budget is not a failure.
      It must not burn max_attempts toward PARKED, while N real failures still
      must, and the interruption budget itself still parks.

No MySQL, no docker, no network, no CDP, no Task Scheduler: the journal is a
private sqlite file in a temp directory and every "child process" is a stub.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import types
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
import daily_chain_v2_adapters as adapters_module  # noqa: E402
import daily_chain_v2_worker as worker_module  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_contract import SourceSpec, SourceTask, classify_error  # noqa: E402
from daily_chain_v2_journal import Journal  # noqa: E402

DAY = date(2026, 8, 20)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-tickbudget-"))
INSTALLER = ROOT / "scripts" / "install_cardz_daily_v2_task.ps1"
LAUNCHER = ROOT / "scripts" / "cardz_daily_v2_launcher.ps1"

FAILURES: list[str] = []


def check(name: str, body: Any) -> None:
    try:
        body()
    except Exception as error:  # noqa: BLE001 - a check reports, it does not abort
        FAILURES.append(name)
        print(f"FAIL {name}: {type(error).__name__}: {error}")
    else:
        print(f"POSITIVE_OK {name}")


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


def add_task(
    journal: Journal,
    code: str,
    *,
    max_attempts: int = 3,
    phase: str = "source",
    payload: dict[str, Any] | None = None,
) -> str:
    task = SourceTask(
        run_id=RUN_ID, business_date=DAY.isoformat(),
        source_code=code, capability="quote",
    )
    journal.add_task(
        task, phase=phase, required_class="extra",
        concurrency_group=f"fixture:{code}", max_concurrency=1,
        max_attempts=max_attempts, payload=payload,
    )
    return task.idempotency_key


def new_chain(journal: Journal, runtime_seconds: float) -> DailyChainV2:
    return DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + runtime_seconds,
    )


def events(journal: Journal, event_type: str) -> list[dict[str, Any]]:
    with sqlite3.connect(str(journal.path)) as conn:
        conn.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in conn.execute(
                "SELECT * FROM chain_event WHERE event_type=? ORDER BY created_at",
                (event_type,),
            ).fetchall()
        ]


# --------------------------------------------------------------------- R2 (1)
def installed_numbers_agree() -> None:
    """The tick budget lives in ONE place; the .ps1 files only repeat it."""

    installer = INSTALLER.read_text(encoding="utf-8")
    launcher = LAUNCHER.read_text(encoding="utf-8")
    installed = re.search(r"(?m)^\$MaxRuntimeSeconds\s*=\s*(\d+)", installer)
    assert installed is not None, "installer no longer declares $MaxRuntimeSeconds"
    launched = re.search(r"(?m)^\s*\[int\]\$MaxRuntimeSeconds\s*=\s*(\d+)", launcher)
    assert launched is not None, "launcher no longer declares [int]$MaxRuntimeSeconds"
    assert int(installed.group(1)) == chain_module.DEFAULT_MAX_RUNTIME_SECONDS, (
        f"installer -MaxRuntimeSeconds {installed.group(1)} != python "
        f"DEFAULT_MAX_RUNTIME_SECONDS {chain_module.DEFAULT_MAX_RUNTIME_SECONDS}"
    )
    assert int(launched.group(1)) == chain_module.DEFAULT_MAX_RUNTIME_SECONDS, (
        f"launcher default -MaxRuntimeSeconds {launched.group(1)} != python "
        f"DEFAULT_MAX_RUNTIME_SECONDS {chain_module.DEFAULT_MAX_RUNTIME_SECONDS}"
    )
    # ...and the external limit python sizes its drain against is the one the
    # installer actually registers.
    minutes = int(chain_module.TICK_EXTERNAL_LIMIT_SECONDS // 60)
    assert chain_module.TICK_EXTERNAL_LIMIT_SECONDS == minutes * 60, (
        "TICK_EXTERNAL_LIMIT_SECONDS is not a whole number of minutes"
    )
    assert "-ExecutionTimeLimit (New-TimeSpan -Minutes $ExecutionMinutes)" in installer, (
        "the shared registration helper no longer applies its explicit limit"
    )
    daily_registration = re.search(
        r"Register-CardzManagedTask\s+`\n"
        r"\s*-Name \$TaskName\s+`\n"
        r"(?:.*\n)*?\s*-ExecutionMinutes (\d+)\s+`",
        installer,
    )
    assert daily_registration is not None, "daily managed-task registration not found"
    assert int(daily_registration.group(1)) == minutes, (
        f"daily task registers {daily_registration.group(1)} minutes, expected {minutes}"
    )
    # The daily task declares its limit twice (plan header + daily action); the
    # watchdog and promo tasks own the other two PT values and are not this one.
    declared = installer.count(f'executionTimeLimit = "PT{minutes}M"')
    assert declared == 2, (
        f"installer declares the daily executionTimeLimit as PT{minutes}M "
        f"{declared} time(s); the plan header and the daily action must agree "
        "with TICK_EXTERNAL_LIMIT_SECONDS"
    )


# --------------------------------------------------------------------- R2 (2)
def drain_window_is_real() -> None:
    """Under the installed numbers the drain must be worth having."""

    journal = new_journal("drain")
    chain = new_chain(journal, float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS))
    granted = chain.drain_deadline_monotonic - chain.deadline_monotonic
    assert granted > 0.0, (
        "the installed tick grants ZERO drain: a worker longer than the claim "
        "window can never finish"
    )
    assert granted >= 600.0, f"the installed tick grants only {granted:.0f}s of drain"
    chain.report_drain(1)
    rows = events(journal, "TICK_DRAINING")
    assert len(rows) == 1, rows
    import json as _json

    payload = _json.loads(rows[0]["payload_json"])
    assert payload["drainSecondsGranted"] >= 600.0, payload
    assert payload["truncatedByExternalLimit"] is False, (
        "the tick's own drain ceiling does not fit inside the external "
        f"ExecutionTimeLimit: {payload}"
    )


# --------------------------------------------------------------------- R2 (3)
def claim_plus_drain_plus_grace_fits() -> None:
    """claim + drain + worst-case interrupt grace + tail <= PT55M."""

    journal = new_journal("budget")
    chain = new_chain(journal, float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS))
    worst_grace = float(chain_module.TICK_INTERRUPT_GRACE_MAX_SECONDS)
    latest_finish = (
        chain.drain_deadline_monotonic
        + worst_grace
        + chain_module.TICK_DRAIN_TAIL_RESERVE_SECONDS
    )
    external_kill = (
        chain.tick_started_monotonic
        + chain_module.tick_external_limit_seconds()
        - chain_module.TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
    )
    assert latest_finish <= external_kill + 1e-6, (
        "a drained tick would be hard-killed mid-finalisation: it finishes "
        f"{latest_finish - external_kill:.0f}s after the external limit"
    )


# --------------------------------------------------------------------- R3 (4)
def hard_limit_reserves_the_worst_grace() -> None:
    """The hard limit subtracts the LONGEST grace any worker kind may take."""

    worst = float(chain_module.TICK_INTERRUPT_GRACE_MAX_SECONDS)
    per_kind = dict(adapters_module.WORKER_SHUTDOWN_GRACE_BY_KIND)
    assert per_kind, "no per-kind interrupt grace is declared"
    assert worst == max(
        [float(adapters_module.WORKER_SHUTDOWN_GRACE_SECONDS)] + [
            float(value) for value in per_kind.values()
        ]
    ), f"the reserved grace {worst} is not the worst case of {per_kind}"
    assert adapters_module.worker_shutdown_grace_seconds("collect") >= 240.0, (
        "the collect/gemrate child does not get the ~240 s it needs to finish "
        "its step and ingest its manifest"
    )
    assert adapters_module.worker_shutdown_grace_seconds("fx") == float(
        adapters_module.WORKER_SHUTDOWN_GRACE_SECONDS
    )
    assert chain_module.tick_hard_limit_seconds() == (
        chain_module.tick_external_limit_seconds()
        - chain_module.TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
        - worst
        - chain_module.TICK_DRAIN_TAIL_RESERVE_SECONDS
    ), chain_module.tick_hard_limit_seconds()


# --------------------------------------------------------------------- R3 (5)
def source_worker_gets_its_kind_grace() -> None:
    """The per-kind grace reaches the real kill path, not just a constant."""

    seen: list[dict[str, Any]] = []

    class _StubProc:
        pid = 4242
        returncode = 0

        def poll(self) -> None:
            return None

    def _fake_terminate(pid: int, *, grace_seconds: float = 5.0) -> None:
        seen.append({"pid": pid, "grace": float(grace_seconds)})

    spec = SourceSpec(
        source_code="fixture-collect",
        capabilities=("quote",),
        transport="worker",
        concurrency_group="fixture",
        max_concurrency=1,
        cadence="daily",
        freshness_sla_minutes=1440,
        required_class="extra",
    )
    task = SourceTask(
        run_id=RUN_ID, business_date=DAY.isoformat(),
        source_code=spec.source_code, capability="quote",
    )
    saved_popen = adapters_module._popen
    saved_terminate = adapters_module.terminate_worker_group
    try:
        adapters_module._popen = lambda *a, **k: _StubProc()  # type: ignore[assignment]
        adapters_module.terminate_worker_group = _fake_terminate  # type: ignore[assignment]
        for kind, expected in (
            ("collect", 240.0),
            ("fx", float(adapters_module.WORKER_SHUTDOWN_GRACE_SECONDS)),
        ):
            adapter = adapters_module.CommandSourceAdapter(
                spec=spec, worker_kind=kind, worker_payload={},
            )
            context = {
                "state_db": WORKSPACE / "unused.sqlite3",
                "task_key": f"fixture:{kind}",
                "claim_token": "fixture-claim",
                "log_path": WORKSPACE / f"{kind}.log",
                "receipt_path": WORKSPACE / f"{kind}.json",
                "deadline_monotonic": lambda: 0.0,
            }
            try:
                adapter.execute(task, context, lambda **kwargs: None)
            except adapters_module.WorkerInterrupted:
                pass
            else:
                raise AssertionError(f"{kind}: a past deadline did not interrupt")
            assert seen and seen[-1]["pid"] == _StubProc.pid, seen
            assert seen[-1]["grace"] == expected, (
                f"worker kind {kind!r} was terminated with grace "
                f"{seen[-1]['grace']}s, expected {expected}s"
            )
    finally:
        adapters_module._popen = saved_popen  # type: ignore[assignment]
        adapters_module.terminate_worker_group = saved_terminate  # type: ignore[assignment]


# --------------------------------------------------------------------- R3 (6)
def a_child_that_stops_inside_the_grace_is_not_killed() -> None:
    """SIGTERM then wait: only a child still alive at the end is SIGKILLed."""

    calls: list[tuple[str, int]] = []
    dies_at = time.monotonic() + 1.0

    def _killpg(pid: int, sig: int) -> None:
        calls.append(("killpg", sig))

    def _kill(pid: int, sig: int) -> None:
        if time.monotonic() >= dies_at:
            raise ProcessLookupError(pid)

    fake_os = types.SimpleNamespace(name="posix", killpg=_killpg, kill=_kill)
    fake_signal = types.SimpleNamespace(SIGTERM=15, SIGKILL=9)
    saved_os = adapters_module.os
    saved_signal = adapters_module.signal
    try:
        adapters_module.os = fake_os  # type: ignore[assignment]
        adapters_module.signal = fake_signal  # type: ignore[assignment]
        adapters_module.terminate_worker_group(4243, grace_seconds=10.0)
        assert [sig for name, sig in calls if name == "killpg"] == [15], (
            f"a child that stopped inside the grace was still SIGKILLed: {calls}"
        )
        calls.clear()
        dies_at2 = time.monotonic() + 3600.0

        def _kill_forever(pid: int, sig: int) -> None:
            if time.monotonic() >= dies_at2:
                raise ProcessLookupError(pid)

        adapters_module.os = types.SimpleNamespace(
            name="posix", killpg=_killpg, kill=_kill_forever
        )
        adapters_module.terminate_worker_group(4244, grace_seconds=0.3)
        assert [sig for name, sig in calls if name == "killpg"] == [15, 9], (
            f"a child that outlived the grace was not SIGKILLed: {calls}"
        )
    finally:
        adapters_module.os = saved_os  # type: ignore[assignment]
        adapters_module.signal = saved_signal  # type: ignore[assignment]


# --------------------------------------------------------------------- R3 (7)
def interrupted_child_is_named_in_the_receipt() -> None:
    """An ingested-but-interrupted collect run says so in its receipt."""

    import collect_control as collect

    assert collect._child_interrupted([
        {"adapter": "gemrate_pop", "childInterrupted": True},
        {"adapter": "snk_price"},
    ]) is True
    assert collect._child_interrupted([{"adapter": "snk_price"}]) is False

    saved = sys.modules.get("collect_control")
    stub = types.SimpleNamespace(
        cmd_incr=lambda **kwargs: {
            "ok": False,
            "childInterrupted": True,
            "failedAdapters": ["gemrate_pop"],
            "truncatedAdapters": [],
            "processed": 604,
            "inserted": 604,
            "checkpointed": 604,
            "results": [
                {
                    "adapter": "gemrate_pop",
                    "ok": False,
                    "error": "gemrate_child_interrupted",
                    "childInterrupted": True,
                }
            ],
        },
        cmd_stock=lambda **kwargs: {"ok": True},
    )
    try:
        sys.modules["collect_control"] = stub  # type: ignore[assignment]
        task = {"source_code": "gemrate", "run_id": RUN_ID}
        payload = {"worker": {"adapters": ["gemrate_pop"], "mode": "incr"}, "shard": "all"}
        try:
            worker_module.run_collect(task, payload, WORKSPACE / "receipt.json")
        except RuntimeError as error:
            failure = error
        else:
            raise AssertionError("an interrupted collect run reported success")
    finally:
        if saved is None:
            sys.modules.pop("collect_control", None)
        else:
            sys.modules["collect_control"] = saved
    receipt = worker_module.failure_receipt({"source_code": "gemrate"}, failure)
    assert receipt["childInterrupted"] is True, (
        f"the receipt does not name the interrupted child: {receipt}"
    )
    assert receipt["status"] == "failed" and receipt["errorCode"] == "RuntimeError", receipt


# ----------------------------------------------------------------------- A (8)
def a_tick_interruption_does_not_burn_an_attempt() -> None:
    """Tick-limit interruptions must not spend the failure budget."""

    journal = new_journal("attempts")
    key = add_task(journal, "long-source", max_attempts=3)
    clock = datetime(2026, 8, 20, tzinfo=timezone.utc)
    for round_number in range(1, 4):
        claimed = journal.claim_ready(RUN_ID, now=clock)
        assert claimed, (
            f"round {round_number}: a task interrupted by the tick budget was "
            "no longer claimable -- the interruption burned max_attempts"
        )
        state = journal.interrupt_claim(
            key, claimed[0]["lease_token"],
            reason="tick deadline interrupted source worker",
            now=clock, tick_limited=True,
        )
        assert state == "INTERRUPTED", (
            f"round {round_number}: tick-limit interruption {round_number} of a "
            f"max_attempts=3 task returned {state}"
        )
        clock += timedelta(seconds=1200)
    row = journal.task(key)
    assert int(row["attempts"]) == 3, row["attempts"]
    assert int(row["interrupted_attempts"]) == 3, dict(row)
    assert int(row["interruptions"]) == 3, row["interruptions"]


# ----------------------------------------------------------------------- A (9)
def real_failures_still_park_and_the_cap_still_holds() -> None:
    """The gate is repaired, never removed."""

    journal = new_journal("cap")
    clock = datetime(2026, 8, 20, tzinfo=timezone.utc)

    # (a) three real failures still exhaust a max_attempts=3 task.
    failing = add_task(journal, "failing-source", max_attempts=3)
    decision = classify_error("boom, a plain provider failure")
    assert not decision.terminal, "fixture error is terminal; pick another"
    states: list[str] = []
    for _ in range(3):
        claimed = journal.claim_ready(RUN_ID, now=clock)
        if not claimed:
            states.append("NOT_CLAIMED")
            break
        states.append(
            journal.finish_failure(
                failing, claimed[0]["lease_token"],
                decision=decision, error_text="boom", now=clock,
            )
        )
        clock += timedelta(hours=1)
    assert states == ["RETRY", "RETRY", "TERMINAL"], states

    # (b) the interruption budget still parks even when every attempt is refunded.
    parking = add_task(journal, "parking-source", max_attempts=99)
    seen: list[str] = []
    for _ in range(8):
        claimed = journal.claim_ready(RUN_ID, now=clock)
        if not claimed:
            seen.append("NOT_CLAIMED")
            break
        seen.append(
            journal.interrupt_claim(
                parking, claimed[0]["lease_token"], reason="tick deadline",
                now=clock, tick_limited=True,
            )
        )
        clock += timedelta(seconds=1200)
        if seen[-1] == "PARKED":
            break
    assert seen[-1] == "PARKED", seen
    assert journal.task(parking)["status"] == "PARKED"


# -------------------------------------------------------------------- R2 (10)
def no_registration_can_outlive_the_external_limit() -> None:
    """The tick is self-safe under ANY -MaxRuntimeSeconds it is handed.

    Review fix 2026-08-24 (blocking): every number above is only sound while
    --max-runtime-seconds equals DEFAULT_MAX_RUNTIME_SECONDS.  The LIVE Task
    Scheduler action bakes its own -MaxRuntimeSeconds into the registered
    argument string, so until somebody re-runs the installer with -Apply the
    tick is still handed 3000 -- and with the R3 grace (240 s instead of 60 s)
    that tick finishes 180 s AFTER PT55M, i.e. Windows hard-kills it
    mid-finalisation and the run loses finalise_live / lifecycle_events /
    write_health / summary.  The tick must clamp its own claim deadline to
    tick_hard_limit_seconds() instead of trusting its registration.
    """

    hard_limit = chain_module.tick_hard_limit_seconds()
    external_kill_offset = (
        chain_module.tick_external_limit_seconds()
        - chain_module.TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
    )
    for runtime in (
        float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS),
        3000.0,  # what the LIVE task still registers until the installer re-runs
        float(chain_module.MAX_RUNTIME_SECONDS_CEILING),
    ):
        chain = new_chain(new_journal(f"reg{int(runtime)}"), runtime)
        claim_overshoot = (
            chain.deadline_monotonic - chain.tick_started_monotonic - hard_limit
        )
        assert claim_overshoot <= 1e-6, (
            f"-MaxRuntimeSeconds {runtime:.0f}: claiming stays open "
            f"{claim_overshoot:.0f}s past the tick hard limit"
        )
        latest_finish = (
            chain.drain_deadline_monotonic
            + chain_module.TICK_INTERRUPT_GRACE_MAX_SECONDS
            + chain_module.TICK_DRAIN_TAIL_RESERVE_SECONDS
        )
        overshoot = latest_finish - (
            chain.tick_started_monotonic + external_kill_offset
        )
        assert overshoot <= 1e-6, (
            f"-MaxRuntimeSeconds {runtime:.0f}: the tick finishes {overshoot:.0f}s "
            "after the external ExecutionTimeLimit and is hard-killed "
            "mid-finalisation"
        )


# ---------------------------------------------------------------------- A (11)
def only_the_tick_budget_refunds_the_attempt() -> None:
    """A business boundary is not a tick interruption.

    Review fix 2026-08-24 (major): _work_deadline_monotonic() mins the tick's
    drain bound with the 10:15 candidate cutoff and the 17:00 final.  Refunding
    the attempt for those too means the work deadline is already in the past on
    the next claim, so the task churns claim -> spawn -> immediate re-interrupt
    for its whole interruption budget (6) instead of settling at max_attempts.
    """

    class _InterruptedAdapter:
        def execute(self, task: Any, context: Any, heartbeat: Any) -> Any:
            raise adapters_module.WorkerInterrupted(
                "tick deadline interrupted source worker pid=4242"
            )

    def fixture(
        name: str,
        *,
        runtime_seconds: float,
        cutoff_seconds: float,
        final_seconds: float,
        phase: str,
        max_attempts: int = 3,
    ) -> tuple[Journal, DailyChainV2, str]:
        journal = new_journal(name)
        chain = new_chain(journal, runtime_seconds)
        now = chain_module.utc_now()
        chain.schedule = {
            "source_cutoff": now + timedelta(seconds=cutoff_seconds),
            "sla": now + timedelta(seconds=final_seconds),
            "final": now + timedelta(seconds=final_seconds),
        }
        chain.registry = {"interrupted-source": _InterruptedAdapter()}
        chain._task_paths = lambda row: (  # type: ignore[method-assign]
            WORKSPACE / f"{name}.log", WORKSPACE / f"{name}.json"
        )
        key = add_task(
            journal, "interrupted-source", max_attempts=max_attempts,
            phase=phase, payload={"kind": "source"},
        )
        return journal, chain, key

    saved_alert = chain_module.send_alert
    try:
        chain_module.send_alert = lambda *a, **k: None  # type: ignore[assignment]

        # (a) the TICK's own budget cut it: the attempt is refunded.
        journal, chain, key = fixture(
            "refund", runtime_seconds=-5.0, cutoff_seconds=7200.0,
            final_seconds=7200.0, phase="source",
        )
        claimed = journal.claim_ready(RUN_ID)
        assert claimed, "the fixture task was not claimable"
        chain.execute_claim(claimed[0])
        row = journal.task(key)
        assert int(row["interrupted_attempts"]) == 1, (
            f"the tick budget did not refund its own interruption: {dict(row)}"
        )

        # (b) the 10:15 candidate cutoff cut it: the attempt is NOT refunded,
        #     and the task settles at max_attempts instead of churning.
        journal, chain, key = fixture(
            "cutoff", runtime_seconds=1800.0, cutoff_seconds=-60.0,
            final_seconds=7200.0, phase="activation", max_attempts=3,
        )
        clock = chain_module.utc_now()
        statuses: list[str] = []
        for _ in range(8):
            claimed = journal.claim_ready(RUN_ID, now=clock)
            if not claimed:
                break
            chain.execute_claim(claimed[0])
            statuses.append(str(journal.task(key)["status"]))
            clock += timedelta(seconds=1200)
        row = journal.task(key)
        assert int(row["interrupted_attempts"]) == 0, (
            "a worker cut by the 10:15 candidate cutoff had its attempt "
            f"refunded: {dict(row)}"
        )
        assert len(statuses) == 3, (
            "a cutoff-bound worker kept re-claiming and re-spawning past "
            f"max_attempts=3: {len(statuses)} rounds {statuses}"
        )
        assert statuses[-1] == "PARKED" and int(row["attempts"]) == 3, dict(row)
    finally:
        chain_module.send_alert = saved_alert  # type: ignore[assignment]


# -------------------------------------------------------------------- R2 (12)
def a_clamped_claim_window_is_never_silent() -> None:
    """The clamp may shorten the operator's tick, but never behind their back.

    Verifier finding 2026-08-24 (major): __init__ caps the claim window at
    tick_hard_limit_seconds() with no print, no journal event and no health
    field, while the CLI still accepts 60..MAX_RUNTIME_SECONDS_CEILING.  A
    hand-run tick -- which no Task Scheduler ExecutionTimeLimit applies to at
    all, so the clamp buys nothing there -- was silently cut to 47 min, and the
    operator watching the 40-90 min PriceCharting full refresh (R5) end to end
    had nothing to read.  The clamp stays (it is what keeps the LIVE 3000 s
    registration inside PT55M), but it must announce itself on stdout, in the
    journal and in health.json, and name the one variable that lifts it.
    """

    hard_limit = chain_module.tick_hard_limit_seconds()

    def health_of(chain: DailyChainV2) -> dict[str, Any]:
        written: list[Any] = []
        saved = chain_module.write_health_document
        try:
            chain_module.write_health_document = (  # type: ignore[assignment]
                lambda document, **_: (written.append(document), Path("health"))[1]
            )
            chain.write_health(tick_phase="started")
        finally:
            chain_module.write_health_document = saved  # type: ignore[assignment]
        assert len(written) == 1, "write_health did not build a document"
        return dict(written[0])

    # (a) the LIVE registration (-MaxRuntimeSeconds 3000) is clamped, loudly.
    journal = new_journal("clamp-said")
    stream = io.StringIO()
    with redirect_stdout(stream):
        chain = new_chain(journal, 3000.0)
    printed = stream.getvalue()
    assert getattr(chain, "claim_window_clamped", None) is True, (
        f"the tick cut the claim window 3000s -> {hard_limit:.0f}s without "
        f"reporting it: stdout={printed!r}"
    )
    assert "3000" in printed and f"{hard_limit:.0f}" in printed, (
        f"the clamp did not print requested vs effective: {printed!r}"
    )
    assert chain_module.TICK_EXTERNAL_LIMIT_ENV in printed, (
        f"the clamp did not name the variable that lifts it: {printed!r}"
    )

    chain.report_long_db_sessions = lambda **_: None  # type: ignore[method-assign]
    chain.initialise(chain_module.load_provenance(None))
    rows = events(journal, "TICK_CLAIM_WINDOW_CLAMPED")
    assert len(rows) == 1, f"the clamp left no journal event: {rows}"
    payload = json.loads(str(rows[0]["payload_json"]))
    assert abs(float(payload["requestedClaimSeconds"]) - 3000.0) <= 1.0, payload
    assert abs(float(payload["effectiveClaimSeconds"]) - hard_limit) <= 1.0, payload
    assert payload["liftWith"] == chain_module.TICK_EXTERNAL_LIMIT_ENV, payload

    budget = health_of(chain).get("tick_budget") or {}
    assert budget.get("claimClamped") is True, budget
    assert float(budget["requestedClaimSeconds"]) > float(budget["claimSeconds"]), budget
    assert float(budget["claimSeconds"]) <= hard_limit + 1e-6, budget
    assert float(budget["drainSeconds"]) >= 0.0, budget
    assert budget["liftWith"] == chain_module.TICK_EXTERNAL_LIMIT_ENV, budget

    # (b) the installed default is honoured: no clamp, no noise, still reported.
    quiet_journal = new_journal("clamp-quiet")
    quiet_stream = io.StringIO()
    with redirect_stdout(quiet_stream):
        quiet = new_chain(
            quiet_journal, float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS)
        )
    assert getattr(quiet, "claim_window_clamped", None) is False, quiet_stream.getvalue()
    assert "TICK_CLAIM_WINDOW_CLAMPED" not in quiet_stream.getvalue(), (
        f"an unclamped tick cried clamp: {quiet_stream.getvalue()!r}"
    )
    quiet.report_long_db_sessions = lambda **_: None  # type: ignore[method-assign]
    quiet.initialise(chain_module.load_provenance(None))
    assert events(quiet_journal, "TICK_CLAIM_WINDOW_CLAMPED") == [], (
        "an unclamped tick journalled a clamp"
    )
    quiet_budget = health_of(quiet).get("tick_budget") or {}
    assert quiet_budget.get("claimClamped") is False, quiet_budget
    assert abs(
        float(quiet_budget["claimSeconds"])
        - float(chain_module.DEFAULT_MAX_RUNTIME_SECONDS)
    ) <= 1.0, quiet_budget

    # (c) the named escape hatch really lifts it, so an operator-launched tick
    #     that must outlive PT55M (R5 full refresh) can have the runtime it asks
    #     for instead of being cut to 47 min.
    saved_env = os.environ.get(chain_module.TICK_EXTERNAL_LIMIT_ENV)
    os.environ[chain_module.TICK_EXTERNAL_LIMIT_ENV] = "7200"
    try:
        lifted = new_chain(
            new_journal("clamp-lifted"),
            float(chain_module.MAX_RUNTIME_SECONDS_CEILING),
        )
        assert getattr(lifted, "claim_window_clamped", None) is False, (
            f"{chain_module.TICK_EXTERNAL_LIMIT_ENV}=7200 did not lift the clamp"
        )
        assert abs(
            (lifted.deadline_monotonic - lifted.tick_started_monotonic)
            - float(chain_module.MAX_RUNTIME_SECONDS_CEILING)
        ) <= 1.0, "the lifted tick did not keep the runtime it asked for"
    finally:
        if saved_env is None:
            os.environ.pop(chain_module.TICK_EXTERNAL_LIMIT_ENV, None)
        else:
            os.environ[chain_module.TICK_EXTERNAL_LIMIT_ENV] = saved_env


def main() -> int:
    try:
        check("installer/launcher/python agree on the tick budget", installed_numbers_agree)
        check("the installed tick grants a real drain window", drain_window_is_real)
        check("claim + drain + grace + tail fits the external limit", claim_plus_drain_plus_grace_fits)
        check("the hard limit reserves the worst-case interrupt grace", hard_limit_reserves_the_worst_grace)
        check("a source worker is terminated with its kind's grace", source_worker_gets_its_kind_grace)
        check("a child that stops inside the grace is not SIGKILLed", a_child_that_stops_inside_the_grace_is_not_killed)
        check("an interrupted child is named in the worker receipt", interrupted_child_is_named_in_the_receipt)
        check("a tick interruption does not burn an attempt", a_tick_interruption_does_not_burn_an_attempt)
        check("real failures still park and the cap still holds", real_failures_still_park_and_the_cap_still_holds)
        check("no registration can outlive the external limit", no_registration_can_outlive_the_external_limit)
        check("only the tick budget refunds the attempt", only_the_tick_budget_refunds_the_attempt)
        check("a clamped claim window is never silent", a_clamped_claim_window_is_never_silent)
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s): {FAILURES}")
        return 1
    print("ALL_OK v2 tick budget: drain window, per-kind grace, attempt accounting")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
