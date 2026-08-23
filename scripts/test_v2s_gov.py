#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Orchestrator-governance fixtures for CARDZ Daily Chain V2 (work package GOV).

Covers the audit items in docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md:
P0-1 (core source settles silently), P1-8 (PARKED missing from four settled
state literals), P1-3 (manual window renewal re-arms source_cutoff and never
runs out), B5 (the run-start env only reached stage subprocesses), P2-14
(unpark/retire ignore run_id) and the narrow P2-2 duplicate-repair guard.

Every check drives real journal rows or real orchestrator code.  Nothing here
touches MySQL, Telegram, a browser, or the release tree: the journal is a
private temp SQLite file and alerts run in dry-run mode.  Checks are
independent so one broken guard reports itself instead of hiding the rest.
"""
from __future__ import annotations

import contextlib
import inspect
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
import daily_chain_v2_db as chain_db  # noqa: E402
import daily_chain_v2_journal as journal_module  # noqa: E402
from daily_chain_v2 import DailyChainV2, manual_e2e_schedule  # noqa: E402
from daily_chain_v2_contract import SourceTask  # noqa: E402
from daily_chain_v2_journal import (  # noqa: E402
    SUCCESS_TASK_STATES,
    TERMINAL_TASK_STATES,
    Journal,
    JournalError,
    utc_now,
)

JST = ZoneInfo("Asia/Tokyo")
DAY = date(2026, 8, 20)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
# The run's own schedule: source_cutoff 01:15Z, sla 02:00Z, final 08:00Z.
BEFORE_CUTOFF = datetime(2026, 8, 20, 0, 30, tzinfo=timezone.utc)
AFTER_CUTOFF = datetime(2026, 8, 20, 6, 0, tzinfo=timezone.utc)
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-gov-"))

CHECKS: list[tuple[str, Callable[[], None]]] = []


def check(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        CHECKS.append((name, fn))
        return fn

    return register


def new_journal(name: str, *, business_date: date = DAY) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=business_date.isoformat(),
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    return journal


def new_chain(journal: Journal, *, business_date: date = DAY) -> DailyChainV2:
    return DailyChainV2(
        journal=journal,
        business_date=business_date,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
    )


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


def add_source_task(
    journal: Journal,
    source_code: str,
    capability: str,
    *,
    required_class: str = "core",
    status: str = "PENDING",
) -> str:
    task = SourceTask(
        run_id=RUN_ID,
        business_date=DAY.isoformat(),
        source_code=source_code,
        capability=capability,
    )
    journal.add_task(
        task,
        phase="source",
        required_class=required_class,
        concurrency_group=f"fixture:{source_code}",
        max_concurrency=1,
        max_attempts=7,
    )
    if status != "PENDING":
        sql(
            journal,
            "UPDATE chain_task SET status=? WHERE task_key=?",
            (status, task.idempotency_key),
        )
    return task.idempotency_key


def settle_system_stages(
    journal: Journal, chain: DailyChainV2, force: dict[str, str] | None = None
) -> None:
    """Mark every unstarted orchestrator stage row settled, as a tick would."""

    force = force or {}
    for row in journal.tasks(chain.run_id):
        if str(row["source_code"]) != "system":
            continue
        if str(row["status"]) not in {"PENDING", "READY"}:
            continue
        sql(
            journal,
            "UPDATE chain_task SET status=?,result_json='{}' WHERE task_key=?",
            (force.get(str(row["capability"]), "COMPLETED"), str(row["task_key"])),
        )


def drive_plan(
    journal: Journal,
    chain: DailyChainV2,
    now: datetime,
    *,
    force: dict[str, str] | None = None,
    until: str | None = None,
    rounds: int = 90,
) -> dict[str, str]:
    """Run plan() to a fixed point, settling each stage it adds."""

    for _ in range(rounds):
        chain.plan(now)
        if until is not None and chain.stage_row(until) is not None:
            break
        settle_system_stages(journal, chain, force)
    return {
        str(row["capability"]): str(row["status"])
        for row in journal.tasks(chain.run_id)
        if str(row["source_code"]) == "system"
    }


# ---------------------------------------------------------------- audit P0-1
@check("P0-1 a core source in TERMINAL or PARKED alerts instead of blocking in silence")
def core_blocked_speaks() -> None:
    journal = new_journal("core-blocked")
    chain = new_chain(journal)
    add_source_task(journal, "gemrate", "pop", status="COMPLETED")
    blocked = add_source_task(journal, "fx", "rates", status="TERMINAL")
    sink = io.StringIO()
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        with contextlib.redirect_stdout(sink):
            drive_plan(journal, chain, BEFORE_CUTOFF, rounds=40)
        parked = events(journal, "CORE_TASK_PARKED")
        assert len(parked) == 1, [row["event_key"] for row in parked]
        payload = json.loads(parked[0]["payload_json"])
        assert payload["taskKey"] == blocked, payload
        assert payload["state"] == "TERMINAL", payload
        assert payload["source"] == "fx", payload
        # The alert leaves on the ALWAYS_ALERT_EVENTS channel: this chain was
        # built with notify=False and the alert still fired.
        assert chain.notify is False
        alert_lines = [
            line for line in sink.getvalue().splitlines()
            if line.startswith("NOTIFY_DRYRUN v2-task-parked:")
        ]
        assert len(alert_lines) == 1, alert_lines
        # The state must reach the channel, not only the journal document:
        # _alert_text builds its detail from a fixed key list.
        assert "state=TERMINAL" in alert_lines[0], alert_lines[0]
        assert blocked in alert_lines[0], alert_lines[0]
        # The same row later PARKED is a new fact, not a duplicate.
        sql(
            journal,
            "UPDATE chain_task SET status='PARKED' WHERE task_key=?",
            (blocked,),
        )
        with contextlib.redirect_stdout(sink):
            chain.plan(BEFORE_CUTOFF)
        states = {
            json.loads(row["payload_json"])["state"]
            for row in events(journal, "CORE_TASK_PARKED")
        }
        assert states == {"TERMINAL", "PARKED"}, states
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    # Negative: a healthy core row and a settled non-core row say nothing.
    quiet_journal = new_journal("core-quiet")
    quiet_chain = new_chain(quiet_journal)
    add_source_task(quiet_journal, "gemrate", "pop", status="COMPLETED")
    add_source_task(
        quiet_journal, "pricecharting", "quote",
        required_class="quote", status="TERMINAL",
    )
    drive_plan(quiet_journal, quiet_chain, AFTER_CUTOFF, until="core-contract-pre")
    assert events(quiet_journal, "CORE_TASK_PARKED") == []
    assert chain_module.blocked_core_source_tasks([]) == []


# ---------------------------------------------------------------- audit P0-1
@check("P0-1 a DEGRADED core source speaks too, and is not sent at a dead lever")
def degraded_core_blocked_speaks() -> None:
    journal = new_journal("core-degraded")
    chain = new_chain(journal)
    # daily_chain_v2_worker.py finishes any report with quarantined >= 1 through
    # finish_success(degraded=True), so a core row reaches DEGRADED without a
    # single TASK_ERROR being written -- the quietest of the three blocked
    # states, and the barrier stays shut on it exactly like TERMINAL.
    blocked = add_source_task(journal, "gemrate", "pop", status="DEGRADED")
    sink = io.StringIO()
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        with contextlib.redirect_stdout(sink):
            drive_plan(journal, chain, BEFORE_CUTOFF, rounds=40)
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    parked = events(journal, "CORE_TASK_PARKED")
    assert len(parked) == 1, [row["event_key"] for row in parked]
    payload = json.loads(parked[0]["payload_json"])
    assert payload["state"] == "DEGRADED", payload
    assert payload["taskKey"] == blocked, payload
    assert "state=DEGRADED" in sink.getvalue(), sink.getvalue()
    # DEGRADED is in neither UNPARKABLE_TASK_STATES nor RETIRABLE_TASK_STATES,
    # so "operator unpark or retire" would point at a lever that is not
    # connected to anything.
    assert journal.unpark(blocked, run_id=RUN_ID, reason="fixture") is None
    assert journal.retire(blocked, run_id=RUN_ID, reason="fixture") is None
    assert "unpark" not in payload["nextRetry"], payload
    assert str(journal.task(blocked)["status"]) == "DEGRADED"
    # The barrier is untouched: DEGRADED never opened it, before or after.
    assert chain_module.source_barrier_ready(
        [{"required_class": "core", "status": "DEGRADED"}],
        now=AFTER_CUTOFF,
        cutoff=BEFORE_CUTOFF,
    ) is False


# ---------------------------------------------------------------- audit P0-1
@check("P0-1 two blocked core rows page twice instead of sharing one cooldown key")
def blocked_core_rows_page_separately() -> None:
    journal = new_journal("core-two-blocked")
    chain = new_chain(journal)
    terminal = add_source_task(journal, "fx", "rates", status="TERMINAL")
    degraded = add_source_task(journal, "gemrate", "pop", status="DEGRADED")
    sink = io.StringIO()
    os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"
    try:
        with contextlib.redirect_stdout(sink):
            drive_plan(journal, chain, BEFORE_CUTOFF, rounds=40)
    finally:
        os.environ.pop("CARDZ_V2_NOTIFY_DRY_RUN", None)
    # notify_hermes dedupes on the alert key, so two rows sharing one key mean
    # the operator learns about one of them and clears it while the other keeps
    # the barrier shut.  Dry-run prints every call, so the key is what we pin.
    keys = {
        line.split(" ", 2)[1] for line in sink.getvalue().splitlines()
        if line.startswith("NOTIFY_DRYRUN v2-task-parked:")
    }
    assert len(keys) == 2, keys
    assert any(terminal in key for key in keys), keys
    assert any(degraded in key for key in keys), keys
    assert all(key.startswith(f"v2-task-parked:{DAY.isoformat()}:") for key in keys), keys
    # An event type with no scope keeps the old per-business-date key.
    assert chain_module.ALWAYS_ALERT_EVENTS["FAILED_FINAL"][0] == "v2-run-failed-terminal"
    assert "alert_scope" in inspect.signature(DailyChainV2.journal_event).parameters


# ------------------------------------------------------------- audit P1-8 (a)
@check("P1-8 no hand-written settled-state literal survives inside plan()")
def settled_state_literals_do_not_drift() -> None:
    assert TERMINAL_TASK_STATES == SUCCESS_TASK_STATES | {"TERMINAL", "PARKED"}
    plan_source = inspect.getsource(DailyChainV2.plan)
    assert "SUCCESS_TASK_STATES | {" not in plan_source
    # The four gates the audit named, plus the four the identity/Fix-F land
    # added on top (operator-apply, intake, per-lane reverify, checkpoint-repair)
    # -- all spelled by the constant, none by a hand-written literal.
    assert plan_source.count('or "") not in TERMINAL_TASK_STATES') == 8, plan_source.count(
        'or "") not in TERMINAL_TASK_STATES'
    )
    orchestrator = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
    # f9c3e054: the reserve has exactly one definition, in the contract module.
    # The orchestrator imports it and must never redefine it -- a same-valued
    # duplicate is invisible to every attribute comparison in the suite.
    assert "\nTICK_RESERVE_SECONDS = " not in orchestrator, "orchestrator redefines TICK_RESERVE_SECONDS"
    assert "    TICK_RESERVE_SECONDS,\n" in orchestrator, "orchestrator must import TICK_RESERVE_SECONDS"
    # The one remaining hand-written settled set is the cutoff-escape barrier,
    # whose semantics are deliberately different and are pinned by
    # scripts/test_daily_chain_v2_durability.py.
    assert orchestrator.count('SUCCESS_TASK_STATES | {"TERMINAL"}') == 1
    assert 'SUCCESS_TASK_STATES | {"TERMINAL"}' in inspect.getsource(
        chain_module.optional_phase_settled
    )
    assert chain_module.source_barrier_ready(
        [
            {"required_class": "core", "status": "COMPLETED"},
            {"required_class": "quote", "status": "PARKED"},
        ],
        now=AFTER_CUTOFF,
        cutoff=AFTER_CUTOFF + timedelta(hours=1),
    ) is False  # audit P1-8 trap: the pre-cutoff barrier semantics stay put


# ------------------------------------------------------------- audit P1-8 (b)
@check("P1-8 a PARKED pending-identities stage no longer deadlocks plan() all day")
def parked_pending_identities_reaches_daily_accept() -> None:
    journal = new_journal("parked-pending")
    chain = new_chain(journal)
    add_source_task(journal, "gemrate", "pop", status="COMPLETED")
    stages = drive_plan(
        journal,
        chain,
        AFTER_CUTOFF,
        force={"pending-identities": "PARKED"},
        until="daily-accept",
    )
    assert stages.get("pending-identities") == "PARKED", stages
    assert "daily-accept" in stages, sorted(stages)


# ------------------------------------------------------------- audit P1-3 (0)
@check("P1-3 the four measured manual-window clock points keep a usable source deadline")
def manual_window_clock_points() -> None:
    business_date = date(2026, 8, 22)
    expected = {16: "17:00", 18: "18:45", 21: "21:45", 23: "23:45"}
    for hour, final_jst in expected.items():
        started = datetime.combine(
            business_date, day_time(hour, 0), tzinfo=JST
        ).astimezone(timezone.utc)
        window = manual_e2e_schedule(started, business_date=business_date)
        assert window["final"].astimezone(JST).strftime("%H:%M") == final_jst, (
            hour, window["final"].astimezone(JST).isoformat()
        )
        assert (
            window["start"]
            < window["source_cutoff"]
            < window["sla"]
            < window["final"]
        ), hour
        # audit trap 6: capping at 17:00 JST alone yields a ten minute window
        # and therefore a five minute source_cutoff after 17:00.
        assert window["source_cutoff"] - window["start"] >= timedelta(minutes=22), hour
        assert window["final"] - window["start"] >= timedelta(
            seconds=chain_module.MANUAL_WINDOW_MIN_SECONDS
        ) or window["final"] <= chain_module.next_scheduled_tick_utc(
            window["start"]
        ) - timedelta(seconds=chain_module.NEXT_TICK_GUARD_SECONDS), hour


# ------------------------------------------------------------- audit P1-3 (a)
@check("P1-3 the journal API refuses an out-of-order caller's backward deadlines")
def renewal_is_forward_only() -> None:
    # Scope note: these are hand-written ISO strings, i.e. the out-of-order
    # caller later_iso exists to defend against.  manual_e2e_schedule cannot
    # produce them -- see production_renewals_hit_the_cap for what actually
    # happens on the real path, and the open re-arm the audit still wants.
    journal = new_journal("renew-forward")
    first, _reopened, _previous = journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T06:22:30+00:00",
        sla_at="2026-08-20T06:33:45+00:00",
        final_at="2026-08-20T06:45:00+00:00",
    )
    # final must not be pulled back from the run's persisted 08:00Z.
    assert str(first["final_at"]) == "2026-08-20T08:00:00+00:00", dict(first)
    assert str(first["source_cutoff_at"]) == "2026-08-20T06:22:30+00:00", dict(first)
    second, _r2, _p2 = journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T06:10:00+00:00",
        sla_at="2026-08-20T06:20:00+00:00",
        final_at="2026-08-20T06:30:00+00:00",
    )
    # A backward source_cutoff would land on sources that are already running.
    # This is the API guard only; it is NOT the 2026-08-22 pricecharting
    # killer, which re-armed the cutoff *forward* every 40 minutes.
    assert str(second["source_cutoff_at"]) == "2026-08-20T06:22:30+00:00", dict(second)
    assert str(second["sla_at"]) == "2026-08-20T06:33:45+00:00", dict(second)
    assert str(second["final_at"]) == "2026-08-20T08:00:00+00:00", dict(second)
    assert int(second["manual_window_renewals"]) == 2, dict(second)
    # Forward is still allowed.
    third, _r3, _p3 = journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T09:00:00+00:00",
        sla_at="2026-08-20T09:20:00+00:00",
        final_at="2026-08-20T09:40:00+00:00",
    )
    assert str(third["source_cutoff_at"]) == "2026-08-20T09:00:00+00:00", dict(third)
    assert str(third["final_at"]) == "2026-08-20T09:40:00+00:00", dict(third)


# ------------------------------------------------------------- audit P1-3 (a)
@check("P1-3 a backward renewal never shortens a running source's deadline")
def renewal_with_running_source() -> None:
    # Same scope note as above: hand-written backward deadlines, journal API
    # level.  The forward re-arm this run would really suffer from is open.
    journal = new_journal("renew-running")
    key = add_source_task(journal, "pricecharting", "quote", required_class="quote")
    claimed = journal.claim_ready(RUN_ID, now=utc_now())
    assert [str(row["task_key"]) for row in claimed] == [key], claimed
    assert str(journal.task(key)["status"]) == "RUNNING"
    journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T06:22:30+00:00",
        sla_at="2026-08-20T06:33:45+00:00",
        final_at="2026-08-20T06:45:00+00:00",
    )
    before = str(journal.run(RUN_ID)["source_cutoff_at"])
    journal.renew_manual_window(
        RUN_ID,
        source_cutoff_at="2026-08-20T06:05:00+00:00",
        sla_at="2026-08-20T06:15:00+00:00",
        final_at="2026-08-20T06:25:00+00:00",
    )
    assert str(journal.run(RUN_ID)["source_cutoff_at"]) == before, before
    assert str(journal.task(key)["status"]) == "RUNNING"


# ------------------------------------------------------------- audit P1-3 (b)
@check("P1-3 renewals run out, so FAILED_FINAL is a gate and not decoration")
def renewals_are_capped() -> None:
    cap = journal_module.MANUAL_WINDOW_MAX_RENEWALS
    assert cap >= 1
    journal = new_journal("renew-cap")
    for index in range(cap):
        hour = 9 + index
        journal.renew_manual_window(
            RUN_ID,
            source_cutoff_at=f"2026-08-20T{hour:02d}:10:00+00:00",
            sla_at=f"2026-08-20T{hour:02d}:20:00+00:00",
            final_at=f"2026-08-20T{hour:02d}:30:00+00:00",
        )
    run = journal.run(RUN_ID)
    assert int(run["manual_window_renewals"]) == cap, dict(run)
    final_before = str(run["final_at"])
    try:
        journal.renew_manual_window(
            RUN_ID,
            source_cutoff_at="2026-08-20T20:10:00+00:00",
            sla_at="2026-08-20T20:20:00+00:00",
            final_at="2026-08-20T20:30:00+00:00",
        )
    except JournalError as error:
        message = str(error)
        assert "already renewed" in message, message
        assert f"max {cap}" in message, message
    else:
        raise AssertionError(f"renewal {cap + 1} must be refused")
    refusals = events(journal, "MANUAL_WINDOW_RENEWAL_REFUSED")
    assert len(refusals) == 1, refusals
    payload = json.loads(refusals[0]["payload_json"])
    assert payload["errorCode"] == "MANUAL_WINDOW_RENEWAL_LIMIT", payload
    assert payload["maxRenewals"] == cap, payload
    after = journal.run(RUN_ID)
    assert str(after["final_at"]) == final_before, dict(after)
    assert int(after["manual_window_renewals"]) == cap, dict(after)


# ------------------------------------------------------------- audit P1-3 (c)
@check("P1-3 on the real path the renewal cap binds and later_iso does not")
def production_renewals_hit_the_cap() -> None:
    """Replay the 2026-08-22 shape through the only production caller.

    DailyChainV2.initialise renews with manual_e2e_schedule(business_date=day),
    never with hand-written timestamps, so this is the input the incident
    actually had: a 16:00 JST start renewed every 40 minutes, eight times.
    """

    business_date = date(2026, 8, 22)
    run_id = f"cardz-v2:{business_date.isoformat()}"
    started = datetime.combine(business_date, day_time(16, 0), tzinfo=JST)
    opening = manual_e2e_schedule(
        started.astimezone(timezone.utc), business_date=business_date
    )
    journal = Journal(WORKSPACE / "renew-production.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=business_date.isoformat(),
        source_cutoff_at=opening["source_cutoff"].isoformat(),
        sla_at=opening["sla"].isoformat(),
        final_at=opening["final"].isoformat(),
    )

    accepted: list[tuple[datetime, datetime]] = []
    refusals = 0
    clamped_by_later_iso = 0
    for index in range(1, 9):  # 2026-08-22 recorded MANUAL_WINDOW_RENEWED=8
        moment = (started + timedelta(minutes=40 * index)).astimezone(timezone.utc)
        window = manual_e2e_schedule(moment, business_date=business_date)
        try:
            run, _reopened, _previous = journal.renew_manual_window(
                run_id,
                source_cutoff_at=window["source_cutoff"].isoformat(),
                sla_at=window["sla"].isoformat(),
                final_at=window["final"].isoformat(),
            )
        except JournalError as error:
            refusals += 1
            assert "already renewed" in str(error), str(error)
            continue
        stored = datetime.fromisoformat(str(run["source_cutoff_at"]))
        clamped_by_later_iso += int(stored != window["source_cutoff"])
        accepted.append((moment, stored))

    cap = journal_module.MANUAL_WINDOW_MAX_RENEWALS
    assert len(accepted) == cap, accepted
    assert refusals == 8 - cap, refusals
    # The one measured mitigation: the eight re-arms of 2026-08-22 become four.
    assert len(accepted) < 8
    cutoffs = [stored for _moment, stored in accepted]
    assert cutoffs == sorted(cutoffs) and len(set(cutoffs)) == len(cutoffs), cutoffs
    # OPEN (audit P1-3, reported in notDone): every accepted renewal still
    # re-arms a fresh ~22 minute source_cutoff onto whatever non-core source is
    # already running -- the 22.6 / 20.4 / 20.0 / 20.0 minute pricecharting
    # attempts.  later_iso cannot see it, because clamp_manual_window is
    # non-decreasing in `started`.  When the re-arm is finally fixed this
    # assertion is the one that must change.
    assert clamped_by_later_iso == 0, clamped_by_later_iso
    for moment, stored in accepted:
        runway = stored - moment
        assert timedelta(minutes=20) <= runway <= timedelta(minutes=25), (moment, runway)


# ---------------------------------------------------------------- audit B5 (1)
@check("B5 the orchestrator process itself carries CARDZ_V2_RUN_STARTED_AT")
def orchestrator_exports_run_start() -> None:
    assert chain_module.RUN_STARTED_AT_ENV == chain_db.RUN_STARTED_AT_ENV
    saved = os.environ.pop(chain_module.RUN_STARTED_AT_ENV, None)
    try:
        journal = new_journal("run-env")
        created = str(journal.run(RUN_ID)["created_at"])
        assert chain_module.RUN_STARTED_AT_ENV not in os.environ
        assert chain_module.export_run_started_at(journal.run(RUN_ID)) == created
        assert os.environ[chain_module.RUN_STARTED_AT_ENV] == created
        # ... and the widened window is what the in-process planner then reads.
        os.environ[chain_module.RUN_STARTED_AT_ENV] = "2026-08-23T00:38:37.753775+00:00"
        assert chain_db.business_window_utc("2026-08-24")[0] == datetime(
            2026, 8, 23, 0, 38, 37, 753775
        )
        assert chain_module.export_run_started_at(None) == ""
    finally:
        if saved is None:
            os.environ.pop(chain_module.RUN_STARTED_AT_ENV, None)
        else:
            os.environ[chain_module.RUN_STARTED_AT_ENV] = saved
    # One execution point, used by initialise() and by the stage env block.
    assert "export_run_started_at(row)" in inspect.getsource(DailyChainV2.initialise)
    stage_source = inspect.getsource(DailyChainV2._run_stage_process)
    assert "RUN_STARTED_AT_ENV: export_run_started_at(" in stage_source
    assert '"CARDZ_V2_RUN_STARTED_AT":' not in stage_source


# ---------------------------------------------------------------- audit B5 (2)
@check("B5 repair planning refuses a coverage window that has not opened yet")
def repair_planner_refuses_future_window() -> None:
    repair_source = inspect.getsource(DailyChainV2.plan_contract_repair_tasks)
    assert repair_source.index("business_window_utc(") < repair_source.index(
        "current_run_contract(self.day_text)"
    ), "the window check must run before the MySQL contract read"
    saved = os.environ.pop(chain_module.RUN_STARTED_AT_ENV, None)
    try:
        os.environ[chain_module.RUN_STARTED_AT_ENV] = ""
        future = date(2099, 1, 1)
        journal = new_journal("repair-window", business_date=future)
        chain = new_chain(journal, business_date=future)
        try:
            chain.plan_contract_repair_tasks()
        except RuntimeError as error:
            message = str(error)
            assert "window has not opened yet" in message, message
            assert chain_module.RUN_STARTED_AT_ENV in message, message
        else:
            raise AssertionError("a future coverage window must refuse to plan repairs")
        # Negative: a real business date is already open, so the guard is quiet.
        window_start, _end = chain_db.business_window_utc(DAY.isoformat())
        assert utc_now().replace(tzinfo=None) >= window_start
    finally:
        if saved is None:
            os.environ.pop(chain_module.RUN_STARTED_AT_ENV, None)
        else:
            os.environ[chain_module.RUN_STARTED_AT_ENV] = saved


# --------------------------------------------------------------- audit P2-14
@check("P2-14 unpark and retire refuse a task key from another business date")
def operator_commands_are_run_scoped() -> None:
    journal = new_journal("run-scope")
    other_day = date(2026, 8, 19)
    other_run = f"cardz-v2:{other_day.isoformat()}"
    parked = add_source_task(journal, "pricecharting", "quote", required_class="quote")
    sql(journal, "UPDATE chain_task SET status='PARKED' WHERE task_key=?", (parked,))
    retirable = add_source_task(journal, "snkrdunk", "quote", required_class="quote")
    sql(journal, "UPDATE chain_task SET status='TERMINAL' WHERE task_key=?", (retirable,))

    assert journal.unpark(parked, run_id=other_run, reason="wrong day") is None
    assert str(journal.task(parked)["status"]) == "PARKED"
    assert journal.retire(retirable, run_id=other_run, reason="wrong day") is None
    assert str(journal.task(retirable)["status"]) == "TERMINAL"

    class UnparkArgs:
        list_only = False
        task = parked
        reason = "operator cli"

    class RetireArgs:
        list_only = False
        task = retirable
        reason = "operator cli"

    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
        assert chain_module.run_unpark(journal, other_day, UnparkArgs()) == 2
        assert chain_module.run_retire(journal, other_day, RetireArgs()) == 2
    text = stderr.getvalue()
    assert text.count("task belongs to another business date") == 2, text
    assert RUN_ID in text and other_run in text, text
    assert str(journal.task(parked)["status"]) == "PARKED"
    assert str(journal.task(retirable)["status"]) == "TERMINAL"
    assert events(journal, "TASK_UNPARKED") == []
    assert events(journal, "TASK_RETIRED") == []

    # The right business date still works, and an unknown key still says so.
    with contextlib.redirect_stdout(io.StringIO()):
        assert chain_module.run_unpark(journal, DAY, UnparkArgs()) == 0
        assert chain_module.run_retire(journal, DAY, RetireArgs()) == 0
    assert str(journal.task(parked)["status"]) == "READY"
    assert str(journal.task(retirable)["status"]) == "SKIPPED"
    UnparkArgs.task = "missing-key"
    missing = io.StringIO()
    with contextlib.redirect_stderr(missing):
        assert chain_module.run_unpark(journal, DAY, UnparkArgs()) == 2
    assert "task is not parkable" in missing.getvalue(), missing.getvalue()


# ---------------------------------------------------------------- audit P2-2
@check("P2-2 one unsettled contract repair per source+capability, the rest defer")
def contract_repair_does_not_pile_up() -> None:
    journal = new_journal("repair-dedupe")
    chain = new_chain(journal)
    assert chain._plan_contract_repair(
        source_code="gemrate", capability="pop", variant_ids=[11, 22, 33]
    ) == 1
    # The shortfall shrinks on the next plan pass; the identity hash changes,
    # so the unguarded code minted a second task and a second attempt ladder.
    assert chain._plan_contract_repair(
        source_code="gemrate", capability="pop", variant_ids=[11, 22]
    ) == 0
    repairs = [
        row for row in journal.tasks(RUN_ID, phase="source")
        if str(row["capability"]) == "contract-repair:pop"
    ]
    assert len(repairs) == 1, [str(row["task_key"]) for row in repairs]
    deferred = events(journal, "SOURCE_CONTRACT_REPAIR_DEFERRED")
    assert len(deferred) == 1, deferred
    payload = json.loads(deferred[0]["payload_json"])
    assert payload["taskKey"] == str(repairs[0]["task_key"]), payload
    assert payload["errorCode"] == "REPAIR_ALREADY_IN_FLIGHT", payload
    assert payload["variantIds"] == [11, 22], payload
    # Once the live repair settles, the next shortfall may mint a new one.
    sql(
        journal,
        "UPDATE chain_task SET status='SKIPPED' WHERE task_key=?",
        (str(repairs[0]["task_key"]),),
    )
    assert chain._plan_contract_repair(
        source_code="gemrate", capability="pop", variant_ids=[11, 22]
    ) == 1
    assert len([
        row for row in journal.tasks(RUN_ID, phase="source")
        if str(row["capability"]) == "contract-repair:pop"
    ]) == 2


def main() -> int:
    failures: list[str] = []
    try:
        for name, fn in CHECKS:
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
                print(f"POSITIVE_OK {name}", flush=True)
    finally:
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    print(f"\n{len(CHECKS) - len(failures)}/{len(CHECKS)} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
