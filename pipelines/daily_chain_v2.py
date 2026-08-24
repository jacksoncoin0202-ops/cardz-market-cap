#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resumable single-tick orchestrator for CARDZ Marketcap Daily Chain V2."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import asdict
from datetime import date, datetime, time as day_time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

try:  # POSIX-only; the tick itself already refuses to run outside WSL.
    import fcntl
except ImportError:  # pragma: no cover - Windows import of this module
    fcntl = None  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
# notify_hermes lives in scripts/; deliver_events() imports it under --notify.
# 2026-08-22 19:34 JST: the first -Notify tick crashed with ModuleNotFoundError.
sys.path.append(str(ROOT / "scripts"))

from daily_chain_v2_adapters import (  # noqa: E402
    MAX_WORKER_SHUTDOWN_GRACE_SECONDS,
    WORKER_SHUTDOWN_GRACE_SECONDS,
    CommandSourceAdapter,
    WorkerInterrupted,
    build_default_registry,
    task_payload,
    terminate_worker_group,
    worker_shutdown_grace_seconds,
)
from daily_chain_v2_contract import (  # noqa: E402
    CONTRACT_SHORTFALL_MARKER,
    PUBLISH_LOCK_EXIT_CODE,
    PUBLISH_LOCK_MARKER,
    TICK_RESERVE_SECONDS,
    WORK_DEADLINE_ENV,
    SourceTask,
    canonical_json,
    classify_error,
    classify_provenance,
    identity_lanes,
    publish_leg_seconds,
    sha256,
    supersede_seq_of,
    supersede_suffix,
    v2_schema_capabilities,
)
from daily_chain_v2_journal import (  # noqa: E402
    Journal,
    JournalError,
    RUN_SUCCESS_STATES,
    SUCCESS_TASK_STATES,
    TERMINAL_TASK_STATES,
    UNPARKABLE_TASK_STATES,
    default_state_path,
    iso,
    utc_now,
)


JST = ZoneInfo("Asia/Tokyo")
TASK_LEASE_SECONDS = 90
# How many *true* failures park a source task.  One number, every source.
# Review 2026-08-24 (blocking): a per-source "headroom" of + the interruption
# budget was added here for the PriceCharting daily_full sweep, but claim time
# increments ONE shared `attempts` counter and the journal's exhaustion checks
# read that same counter -- so the headroom loosened the true-failure park cap
# from 7 to 13 for that source.  A gate is never loosened to pay for accounting:
# an attempt spent by a tick interruption is refunded by the interruption
# accounting itself (R2), not by widening the failure budget.
SOURCE_MAX_ATTEMPTS = 7

# audit P1-1: execute_ready() is a refilling pump, not a batch barrier.  These
# three numbers are its shape: how many claims may be in flight, how often it
# re-plans and refills while work is still running, and how often a busy tick
# renews health.json for the watchdog.
EXECUTE_MAX_IN_FLIGHT = 16
EXECUTE_POLL_SECONDS = 2.0
EXECUTE_HEALTH_INTERVAL_SECONDS = 30.0
# Review fix: a bare poll timeout must not re-run eligible_phases() (seven
# journal reads) plus claim_ready() (a write transaction) thirty times a minute
# for a whole tick.  A completion always refills immediately -- that is the
# freed slot / opened barrier case.  Only the "a backoff expired while everyone
# is still busy" case waits, and it waits at most this long, against the 14
# minutes the audit measured.
EXECUTE_REFILL_IDLE_INTERVAL_SECONDS = 5.0
# audit P2-3: a wait is sliced so the flock holder keeps writing health and
# reclaiming expired leases; the floor kills the ~20/s spin that happened when
# next_retry_wait() returned 0 while the concurrency group was full.
TICK_SLEEP_SLICE_SECONDS = 60.0
TICK_MIN_IDLE_SLEEP_SECONDS = 1.0
# The external limit is NOT ours to choose: the Task Scheduler action that runs
# this tick carries ExecutionTimeLimit=PT55M (scripts/install_cardz_daily_v2_task.ps1),
# and Windows kills the wscript -> powershell -> wsl.exe tree when it expires.
# The drain bound is measured from THIS process's start, which is later than the
# task's start, and a drain that ends at the bound still has to kill the worker
# and finish the tick.  So the bound is the external limit minus every piece of
# wall clock that is not drain:
#     3300  PT55M
#   -   60  launcher before python exists: ensure_chrome_cdp.ps1 preflight
#           (8 s evict + 20 s revive + curl timeouts) and wsl.exe cold start
#   -  240  TICK_INTERRUPT_GRACE_MAX_SECONDS: the longest grace any worker kind
#           may take between SIGTERM and SIGKILL (R3: a collect/gemrate child
#           finishes its step and ingests its manifest inside it)
#   -  180  tail after the last worker dies: finalise_live + lifecycle_events +
#           deliver_events (NOTIFY_TIMEOUT_SECONDS=20 per send, several events)
#           + write_health + summary
#   = 2820  == tick_hard_limit_seconds()
# R2 (operator finding 2026-08-24): with -MaxRuntimeSeconds 3000 that hard limit
# equalled the claim deadline, so the drain granted ZERO extra seconds -- a
# worker claimed at 2999 s was killed one second later.  Claiming now closes at
# DEFAULT_MAX_RUNTIME_SECONDS = 2100 (35 min) and the drain fills the remaining
# 720 s (12 min) of the SAME PT55M window -- remedy (1) from that finding, no
# ExecutionTimeLimit change.  Read the ceiling honestly: ONE tick gives a live
# worker at most 2100 + 720 = 2820 s of wall clock, which is LESS than the 3000 s
# it had before.  What the drain buys is a guarantee, not a longer tick: every
# newly claimed worker now gets >= 720 s instead of possibly a few seconds.  The
# 55-60 min gemrate harvest and the 40-90 min PriceCharting full refresh (R5)
# still cannot finish inside one tick and MUST resume from their progress
# stamps; the attempt accounting below is what keeps those resumptions from
# parking the task.  The alternative, raising ExecutionTimeLimit in
# scripts/install_cardz_daily_v2_task.ps1 AND setting
# CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS to the new PT value in the launcher's
# environment, must still be done on both sides or it either buys nothing or
# moves the kill outside our control.
# The TICK_DRAINING event reports which of the two bounds truncated a drain.
TICK_EXTERNAL_LIMIT_SECONDS = 3300
# The one variable that lifts every number derived from the external limit,
# named in the clamp report so an operator who wants a longer HAND-RUN tick
# (no ExecutionTimeLimit applies to those) reads how to get it.
TICK_EXTERNAL_LIMIT_ENV = "CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS"
TICK_LAUNCHER_STARTUP_RESERVE_SECONDS = 60
# One source for the grace: the adapters own the per-kind table and the tick
# reserves its worst case, so neither side can move without the other.
TICK_INTERRUPT_GRACE_SECONDS = WORKER_SHUTDOWN_GRACE_SECONDS
TICK_INTERRUPT_GRACE_MAX_SECONDS = MAX_WORKER_SHUTDOWN_GRACE_SECONDS
TICK_DRAIN_TAIL_RESERVE_SECONDS = 180
DRAIN_HEARTBEAT_STALE_SECONDS = TASK_LEASE_SECONDS
# Review fix: drain_deadline_for() asks claim_still_live() on EVERY worker poll
# (1/s per source worker, 1/2 s per stage worker, up to sixteen in flight) and
# each ask opens a fresh sqlite connection.  Heartbeats are written every 10 s
# (source) / 2 s (stage) and the stale window is 90 s, so caching the answer for
# five seconds cannot change a single decision.
DRAIN_LIVENESS_CACHE_SECONDS = 5.0
# One tick must fit inside the scheduler's own ExecutionTimeLimit; 2100 s is
# the single source of that number for both the CLI default and the installer
# (scripts/install_cardz_daily_v2_task.ps1 + scripts/cardz_daily_v2_launcher.ps1
# repeat it as -MaxRuntimeSeconds; scripts/test_v2_tick_budget.py parses both
# and fails if either side drifts).
DEFAULT_MAX_RUNTIME_SECONDS = 2100
MAX_RUNTIME_SECONDS_CEILING = 5400
# Drain (operator finding 2026-08-24).  Once claiming closes the tick keeps
# waiting for work that is still alive.  Derived, never chosen: the ceiling is
# exactly the wall clock left inside the external limit after the claim window,
# the worst-case interrupt grace and the finalisation tail, so the tick never
# asks for drain it cannot have and TICK_DRAINING's truncatedByExternalLimit
# means what it says -- somebody shrank the external limit under us.
TICK_DRAIN_CEILING_SECONDS = (
    TICK_EXTERNAL_LIMIT_SECONDS
    - TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
    - TICK_INTERRUPT_GRACE_MAX_SECONDS
    - TICK_DRAIN_TAIL_RESERVE_SECONDS
    - DEFAULT_MAX_RUNTIME_SECONDS
)
LAST_SCHEDULED_TICK_JST = "17:00"
# 2026-08-22: the manual window was tick start + 600 s, and the run reached
# publish with four minutes of window left.  Forty-five minutes is the floor
# a manual E2E actually needs; the next scheduled tick is still the ceiling.
MANUAL_WINDOW_MIN_SECONDS = 2700
MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS = 600
NEXT_TICK_GUARD_SECONDS = 300
# Same name as daily_chain_v2_db.RUN_STARTED_AT_ENV, declared here so the
# orchestrator can export it without importing the MySQL-backed module at
# start-up.  scripts/test_v2s_gov.py asserts the two never drift apart.
RUN_STARTED_AT_ENV = "CARDZ_V2_RUN_STARTED_AT"
ADOPT_GRACE_SECONDS = 120
HEALTH_SCHEMA = 1
NOTIFY_SCRIPT = ROOT / "scripts" / "notify_hermes.py"
NOTIFY_TIMEOUT_SECONDS = 20
# Lifecycle facts an operator must learn about even when the verbose --notify
# stream is off.  Key/level/cooldown-minutes per journal event type.
ALWAYS_ALERT_EVENTS: dict[str, tuple[str, str, int]] = {
    "live.confirmed": ("v2-run-published", "info", 10),
    "FAILED_FINAL": ("v2-run-failed-terminal", "error", 30),
    "CORE_TASK_PARKED": ("v2-task-parked", "error", 30),
    # audit P1-2: a publish verdict that cannot be retried is the end of the
    # business date's automatic path.  TASK_ERROR is add_event only, so on
    # 2026-08-23 nothing spoke until FAILED_FINAL at 17:00 JST.
    "PUBLISH_TERMINAL": ("v2-publish-terminal", "error", 10),
    "TICK_CRASHED": ("v2-tick-crashed", "error", 10),
    "TICK_SIGNALLED": ("v2-tick-signalled", "warn", 10),
    "TASK_ADOPTED": ("v2-task-adopted", "info", 30),
}
# The morning identity brief ships as its own journal event and _event_message
# returns its already-rendered HTML verbatim.  Deliberately absent from the
# dict above: that path escapes the message and prints every link as markup.
IDENTITY_BRIEF_EVENT = "identity.brief"
RUN_STATE_BY_STATUS = {
    "PUBLISHED": "PUBLISHED",
    "PUBLISHED_DEGRADED": "PUBLISHED",
    "FAILED_FINAL": "FAILED",
    "ABORTED": "FAILED",
    "READY_FOR_CUTOVER": "DONE",
}
RELEASE_SNAPSHOT = Path("/home/jackson0202/cardz-market-cap-release-daily/data/public/seed-snapshot.json")
MYSQL_COMPOSE = Path("/mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap/compose.backend.yaml")
MYSQL_COMPOSE_WINDOWS = r"C:\Users\jackson0202\Documents\Playground\cardz-market-cap\compose.backend.yaml"
MYSQL_COMPOSE_ENV = MYSQL_COMPOSE.parent / "data" / "runtime" / "config" / "backend.env"
DOCKER_CLI = "docker.exe" if os.name != "nt" else "docker"
STAGE_SCRIPT = ROOT / "pipelines" / "daily_chain_v2_stage.py"
TASK_STATUS_RANK = {
    "TERMINAL": 7,
    "RETRY": 6,
    "INTERRUPTED": 5,
    "RUNNING": 4,
    "PENDING": 3,
    "DEGRADED": 2,
    "COMPLETED": 1,
    "SKIPPED": 0,
    # Above TERMINAL on purpose: a parked lane carries the unpark instruction
    # and is the one state an operator must act on.
    "PARKED": 8,
    "READY": 3,
}
MYSQL_RECOVERY_LOCK = threading.Lock()
LAST_ALERT: dict[str, Any] | None = None
TICK_LOCK_HANDLE: Any = None


def jst_schedule(day: date) -> dict[str, datetime]:
    def at(hour: int, minute: int) -> datetime:
        return datetime.combine(day, day_time(hour, minute), tzinfo=JST).astimezone(timezone.utc)

    return {
        "start": at(3, 30),
        "source_cutoff": at(10, 15),
        "sla": at(11, 0),
        "final": at(17, 0),
    }


RUN_LABEL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,7}$")


def run_id_for(
    business_date: date | str,
    run_label: str = "",
    supersede_seq: int = 1,
) -> str:
    """Durable run identity.

    Unlabelled generation 1 (the scheduled path and every ordinary publish)
    stays byte-identical to the historical `cardz-v2:<business_date>`.

    A label names a REHEARSAL of the same business date
    (`cardz-v2:2026-08-23#A01`): own journal file, own runtime directory and
    health document, and it never publishes.

    A supersede seq names a RERUN of the same business date with fresh data
    (`cardz-v2:2026-08-24/2`): same journal, same date, own runtime directory,
    and it keeps full publication rights -- the previous generation was
    archived out of the journal and its outbox row is marked superseded when
    the new one is confirmed.  The two are deliberately different concepts and
    never combine: a rehearsal proves nothing by publishing, so asking for both
    is a bug, not a mode.
    """

    day = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    label = (run_label or "").strip()
    suffix = supersede_suffix(supersede_seq)
    if not label:
        return f"cardz-v2:{day}{suffix}"
    if not RUN_LABEL_RE.match(label):
        raise ValueError(
            f"run label must match {RUN_LABEL_RE.pattern} (e.g. A01), got {label!r}"
        )
    if suffix:
        raise ValueError(
            "a rehearsal label never supersedes a business date;"
            f" drop the label or the supersede generation ({label!r}{suffix})"
        )
    return f"cardz-v2:{day}#{label}"


def runtime_dir_for(
    business_date: date | str,
    run_label: str = "",
    supersede_seq: int = 1,
) -> Path:
    """Per-run runtime directory.  Per-task files are keyed by task_key, which
    is date-scoped, so a rehearsal or a supersede rerun that shared the date's
    directory would overwrite the previous run's logs and receipts.
    """

    day = business_date.isoformat() if isinstance(business_date, date) else str(business_date)
    label = (run_label or "").strip()
    name = f"{day}-{label}" if label else day
    seq = max(1, int(supersede_seq))
    if seq > 1:
        name = f"{name}-S{seq}"
    return ROOT / "data" / "runtime" / "daily-chain-v2" / name


def rehearsal_state_path(base: Path, run_label: str) -> Path:
    """A labelled run journals beside the live journal, never inside it.

    The live journal is the autonomy evidence (chain_run.business_date is
    UNIQUE there by design); a rehearsal must not add a second row for the
    date or disturb `proven_autonomous`.
    """

    label = (run_label or "").strip()
    if not label:
        return base
    run_id_for("2000-01-01", label)  # validates the label
    return base.with_name(f"{base.stem}-{label}{base.suffix}")


AUTO_SUPERSEDE_ENV = "CARDZ_V2_AUTO_SUPERSEDE"
AUTO_SUPERSEDE_REASON = (
    "auto-align: the published run for this business date was created on an"
    " earlier calendar day, so the date is one day ahead of the calendar"
)


def auto_supersede_enabled() -> bool:
    """Phase 2 auto-align is OFF unless the operator turns it on for a tick."""

    return os.environ.get(AUTO_SUPERSEDE_ENV, "").strip().casefold() not in {
        "", "0", "false", "no", "off",
    }


def maybe_auto_supersede(
    journal: Journal,
    business_date: date,
    provenance: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Rerun a stale published business date on a NATURAL tick, at most once.

    Business dates drifted a day ahead of the calendar because early manual
    runs burned slots: a date publishes, the journal's UNIQUE business_date
    plus the tick no-op guard make it dead forever, and the next natural tick
    has to take tomorrow.  When this is enabled, a natural tick that finds
    today's date already published by a run created on an EARLIER calendar day
    archives that run and plans the date again with fresh data.

    Four conditions, all required, and the journal enforces the cap itself:
      * the flag is set (default OFF: with it unset this returns before it has
        read anything, and the tick behaves exactly as it does today);
      * the tick's provenance is scheduled, never an operator's CLI run;
      * the live run for the date is PUBLISHED / PUBLISHED_DEGRADED and was
        created before today (JST);
      * no supersede has happened for that date today, and no automatic one
        ever (`Journal.supersede_run` refuses a second `origin='auto'`).

    An automatic supersede is deliberately NOT a manual intervention: the
    archived run's manual and event-107 counters are carried into the rerun by
    `Journal.ensure_run`, so the date's autonomy evidence survives it.
    """

    if not auto_supersede_enabled():
        return None
    if classify_provenance(provenance) != "scheduled":
        return None
    day_text = business_date.isoformat()
    run = journal.run_for_date(day_text)
    if not run or str(run.get("status") or "") not in RUN_SUCCESS_STATES:
        return None
    today_jst = (now or utc_now()).astimezone(JST).date()
    created_raw = str(run.get("created_at") or "")
    try:
        created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    if created.astimezone(JST).date() >= today_jst:
        return None
    for entry in journal.supersede_history(day_text):
        if str(entry.get("supersedeOrigin")) == "auto":
            return None
        try:
            stamped = datetime.fromisoformat(
                str(entry.get("supersededAt") or "").replace("Z", "+00:00")
            )
        except ValueError:
            continue
        if stamped.tzinfo is None:
            stamped = stamped.replace(tzinfo=timezone.utc)
        if stamped.astimezone(JST).date() == today_jst:
            return None
    return journal.supersede_run(
        day_text,
        reason=AUTO_SUPERSEDE_REASON,
        receipt_dir=runtime_dir_for(business_date, "", supersede_seq_of(str(run["run_id"]))),
        origin="auto",
        now=now,
    )


def last_scheduled_tick_utc(day: date) -> datetime:
    """The final scheduled tick of a business date (17:00 JST by contract)."""

    text = os.environ.get("CARDZ_V2_LAST_TICK_JST", "").strip() or LAST_SCHEDULED_TICK_JST
    try:
        hour_text, minute_text = text.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        hour, minute = 17, 0
    return datetime.combine(day, day_time(hour, minute), tzinfo=JST).astimezone(timezone.utc)


def next_scheduled_tick_utc(after: datetime) -> datetime:
    """First scheduled 03:30-JST start strictly after `after`."""

    jst = timezone(timedelta(hours=9))
    local = after.astimezone(jst)
    hour, minute = (int(part) for part in os.environ.get("CARDZ_V2_FIRST_TICK_JST", "03:30").split(":"))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def clamp_manual_window(
    schedule: Mapping[str, datetime],
    *,
    business_date: date,
    rehearsal: bool = False,
) -> dict[str, datetime]:
    """Bound one manual window between a usable floor and the next tick.

    What actually holds, in precedence order (audit P1-3 measured all four
    corners, so state them instead of the older, false "never outlives the
    day's last scheduled tick"):

    * Floor: at least MANUAL_WINDOW_MIN_SECONDS, because a window that only
      covers part of a publish leg is worse than no window (2026-08-22 got
      600 s and finished with four minutes to spare).  Past 17:00 JST this
      floor is what binds, so a window opened at 18:00 JST runs to 18:45.
    * Ceiling: the next unattended 03:30 JST tick minus NEXT_TICK_GUARD_SECONDS.
      A manual run is never still authoritative when the scheduler restarts,
      and this ceiling outranks the floor.
    * A labelled rehearsal (--run-label) lives in its own journal and never
      publishes, so the business date's 17:00 JST final does not bind it and
      it keeps the full +4h/+5h/+8h shape (A01 opened at 16:05 JST on
      2026-08-23 was cut to 55 minutes by that cap).  Only the ceiling still
      applies: a rehearsal and the unattended run would otherwise share CDP
      9333, SNKRDUNK and MySQL at 03:30 JST.
    * Absolute lower bound: MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS, for a window
      opened inside the guard band.
    * Renewals are capped at MANUAL_WINDOW_MAX_RENEWALS and are forward-only;
      Journal.renew_manual_window owns both rules.

    Do not "fix" the floor by capping at 17:00 JST alone: after 17:00 that
    yields a ten minute window and therefore a five minute source_cutoff,
    which SIGTERMs every non-core source five minutes in.
    """

    values = dict(schedule)
    started = values["start"].astimezone(timezone.utc)
    floor = started + timedelta(seconds=MANUAL_WINDOW_MIN_SECONDS)
    ceiling = next_scheduled_tick_utc(started) - timedelta(seconds=NEXT_TICK_GUARD_SECONDS)
    if rehearsal:
        cap = ceiling
    else:
        cap = min(max(floor, last_scheduled_tick_utc(business_date)), ceiling)
    cap = max(cap, started + timedelta(seconds=MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS))
    if values["final"] <= cap:
        return values
    span = (cap - started).total_seconds()
    values["final"] = cap
    values["source_cutoff"] = started + timedelta(seconds=span * 0.5)
    values["sla"] = started + timedelta(seconds=span * 0.75)
    return values


def manual_e2e_schedule(
    now: datetime | None = None,
    *,
    business_date: date | None = None,
    rehearsal: bool = False,
) -> dict[str, datetime]:
    """One authorized after-hours window; it never changes provenance to scheduled."""

    started = (now or utc_now()).astimezone(timezone.utc)
    day = business_date or started.astimezone(JST).date()
    return clamp_manual_window(
        {
            "start": started,
            "source_cutoff": started + timedelta(hours=4),
            "sla": started + timedelta(hours=5),
            "final": started + timedelta(hours=8),
        },
        business_date=day,
        rehearsal=rehearsal,
    )


def export_run_started_at(run: Mapping[str, Any] | None) -> str:
    """Publish the run's creation time to this process and every child.

    audit B5: business_window_utc widens the coverage window by this value, but
    the orchestrator only injected it into stage subprocesses.  The repair
    planner runs in-process (plan_contract_repair_tasks -> current_run_contract),
    so the planner measured a narrow window while the barrier stage measured the
    wide one -- that mismatch is what minted the pointless 1604-card gemrate
    core repair on 2026-08-24.  One execution point: set it here, inherit it
    everywhere.
    """

    value = str((run or {}).get("created_at") or "")
    os.environ[RUN_STARTED_AT_ENV] = value
    return value


def health_path(run_label: str = "") -> Path:
    configured = os.environ.get("CARDZ_V2_HEALTH_PATH", "").strip()
    if configured:
        base = Path(configured).expanduser()
    else:
        base = ROOT / "data" / "runtime" / "daily-chain-v2" / "health.json"
    label = (run_label or "").strip()
    if not label:
        return base
    # The watchdog reads health.json alone; a rehearsal must not clobber it.
    return base.with_name(f"{base.stem}-{label}{base.suffix}")


def send_alert(
    key: str,
    text: str,
    *,
    level: str = "warn",
    cooldown_min: int = 30,
) -> bool:
    """Best-effort operator alert.  Never raises, never blocks the chain."""

    global LAST_ALERT
    LAST_ALERT = {"key": key, "at_utc": iso()}
    dry_run = os.environ.get("CARDZ_V2_NOTIFY_DRY_RUN", "").strip().casefold()
    if dry_run not in {"", "0", "false", "no"}:
        print(f"NOTIFY_DRYRUN {key} {level} {text}", flush=True)
        return True
    try:
        subprocess.run(
            [
                sys.executable, "-X", "utf8", str(NOTIFY_SCRIPT), "alert",
                "--key", key, "--text", text, "--level", level,
                "--cooldown-min", str(int(cooldown_min)),
            ],
            cwd=str(ROOT),
            capture_output=True,
            timeout=NOTIFY_TIMEOUT_SECONDS,
            check=False,
        )
        return True
    except Exception:  # noqa: BLE001 - notification transport is never fatal
        return False


def run_state_of(run: Mapping[str, Any] | None) -> str:
    if not run:
        return "NONE"
    return RUN_STATE_BY_STATUS.get(str(run.get("status") or ""), "RUNNING")


def manual_window_until(run: Mapping[str, Any] | None, business_date: date) -> str | None:
    """A final deadline that differs from 17:00 JST is an open manual window."""

    if not run or not run.get("final_at"):
        return None
    try:
        final = datetime.fromisoformat(str(run["final_at"]).replace("Z", "+00:00"))
    except ValueError:
        return None
    if final.tzinfo is None:
        final = final.replace(tzinfo=timezone.utc)
    final = final.astimezone(timezone.utc)
    if final == last_scheduled_tick_utc(business_date):
        return None
    return iso(final)


def build_health_document(
    journal: Journal,
    business_date: date,
    *,
    tick_phase: str,
    tick_exit_code: int | None = None,
    tick_started_at_utc: str | None = None,
    tick_ended_at_utc: str | None = None,
    run_label: str = "",
    tick_budget: Mapping[str, Any] | None = None,
    supersede_seq: int = 1,
) -> dict[str, Any]:
    """Schema-1 health contract shared with the watchdog (C1)."""

    run_id = run_id_for(business_date, run_label, supersede_seq)
    try:
        run = journal.run(run_id)
        rows = journal.tasks(run_id) if run else []
    except Exception:  # noqa: BLE001 - health must survive a damaged journal
        run, rows = None, []
    tasks: dict[str, Any] = {}
    parked: list[str] = []
    retries: list[datetime] = []
    for row in rows:
        key = str(row["task_key"])
        status = str(row["status"])
        tasks[key] = {
            "state": status,
            "attempts": int(row.get("attempts") or 0),
            "max_attempts": int(row.get("max_attempts") or 0),
            "interruptions": int(row.get("interruptions") or 0),
            "last_error_code": (
                str(row["last_error_code"]) if row.get("last_error_code") else None
            ),
        }
        if status == "PARKED":
            parked.append(key)
        raw_retry = row.get("next_retry_at")
        if raw_retry and status in {"RETRY", "INTERRUPTED", "READY", "PENDING"}:
            try:
                due = datetime.fromisoformat(str(raw_retry).replace("Z", "+00:00"))
            except ValueError:
                continue
            retries.append(due if due.tzinfo else due.replace(tzinfo=timezone.utc))
    duration: float | None = None
    if tick_started_at_utc and tick_ended_at_utc:
        try:
            started = datetime.fromisoformat(tick_started_at_utc)
            ended = datetime.fromisoformat(tick_ended_at_utc)
            duration = round((ended - started).total_seconds(), 3)
        except ValueError:
            duration = None
    now = utc_now()
    return {
        "schema": HEALTH_SCHEMA,
        "written_at_utc": iso(now),
        "written_at_jst": now.astimezone(JST).isoformat(timespec="microseconds"),
        "business_date": business_date.isoformat(),
        "run_id": run_id,
        "run_label": run_label or None,
        "run_state": run_state_of(run),
        "tick_phase": tick_phase,
        "tick_exit_code": None if tick_exit_code is None else int(tick_exit_code),
        "tick_started_at_utc": tick_started_at_utc,
        "tick_ended_at_utc": tick_ended_at_utc,
        "tick_duration_s": duration,
        # Optional: only a live tick knows its own budget.  Additive, so the
        # watchdog's schema-1 readers are untouched.
        "tick_budget": dict(tick_budget) if tick_budget else None,
        "next_retry_at_utc": iso(min(retries)) if retries else None,
        # Supersede lineage, additive to schema 1.  `supersede_pending` is the
        # watchdog's re-arm signal: a superseded date's PUBLISHED health
        # document is no longer proof that today published, and stays "not done
        # yet" until the superseding generation confirms its own publication.
        "supersede_seq": max(1, int(supersede_seq)),
        "supersede_pending": bool(
            int(supersede_seq) > 1 and not (run or {}).get("publication_status")
        ),
        "tasks": tasks,
        "parked": sorted(parked),
        "manual_window_until_utc": manual_window_until(run, business_date),
        "autonomous_proven": bool(int((run or {}).get("proven_autonomous") or 0)),
        "last_alert": LAST_ALERT,
    }


def write_health_document(document: Mapping[str, Any], *, run_label: str = "") -> Path:
    path = health_path(run_label)
    atomic_json(path, document)
    return path


def report_tick_skipped(business_date: date, run_label: str = "") -> Path:
    """Record TICK_SKIPPED_LOCKED WITHOUT touching health.json (audit P2-1b).

    The skip branch used to rewrite the whole health document, so a tick that
    was stuck while holding the flock had its `written_at_utc` refreshed by the
    next scheduled tick every ten minutes.  The watchdog's 25-minute staleness
    rule (scripts/watchdog_live_release.ps1) could therefore never fire.  A skip
    is its own fact and gets its own file; health.json belongs to the tick that
    owns the lock.

    tick-skipped.json is HUMAN-ONLY forensics and has no automated reader: the
    watchdog opens health.json alone, and nothing else in this repo reads this
    path.  The automated signal for "a tick is stuck under the flock" is exactly
    the health.json staleness this branch stops forging -- do not add a gate
    that depends on this file without giving it a real reader first.
    """

    label = (run_label or "").strip()
    path = health_path(label).with_name(
        f"tick-skipped-{label}.json" if label else "tick-skipped.json"
    )
    atomic_json(
        path,
        {
            "schema": HEALTH_SCHEMA,
            "business_date": business_date.isoformat(),
            "last_skipped_at_utc": iso(),
            "reason": "TICK_SKIPPED_LOCKED",
        },
    )
    return path


def acquire_tick_lock(journal_path: Path) -> tuple[Any, bool]:
    """One tick per journal.  A slow tick must never be doubled by the next."""

    lock_path = journal_path.with_name(journal_path.name + ".tick.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+")
    if fcntl is None:
        return handle, True
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None, False
    return handle, True


def recovery_disposition(
    row: Mapping[str, Any],
    *,
    now: datetime,
    alive: bool,
    grace_seconds: int = ADOPT_GRACE_SECONDS,
) -> str:
    """Decide what an expired lease means: adopt, terminate, or interrupt.

    A lease that expired while its worker is demonstrably alive and recently
    heartbeating is this orchestrator's own long stage, not an orphan.  Killing
    it restarts hours of work every single tick.
    """

    if not alive:
        return "interrupt"
    raw = row.get("lease_expires_at") or row.get("heartbeat_at")
    if not raw:
        return "terminate"
    try:
        expires = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return "terminate"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    late = (now - expires.astimezone(timezone.utc)).total_seconds()
    return "adopt" if late <= float(grace_seconds) else "terminate"


def tick_external_limit_seconds() -> float:
    """The Task Scheduler ExecutionTimeLimit that kills this process tree.

    Not ours to choose: an operator who changes ExecutionTimeLimit in
    scripts/install_cardz_daily_v2_task.ps1 must set this variable to the same
    number, or the tick keeps sizing its drain against the old PT value.
    """

    raw = os.environ.get(TICK_EXTERNAL_LIMIT_ENV, "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return float(TICK_EXTERNAL_LIMIT_SECONDS)
    return value if value > 0 else float(TICK_EXTERNAL_LIMIT_SECONDS)


def tick_hard_limit_seconds() -> float:
    """Latest monotonic instant a drain may end, measured from python start.

    Review fix: this used to be a bare 3240 that left no room for the interrupt
    grace plus the finalisation tail inside PT55M, i.e. a drained tick was
    designed to be hard-killed mid-finalisation.  It is now derived, so the
    arithmetic in the TICK_EXTERNAL_LIMIT_SECONDS comment is the only source of
    the number: external limit - launcher startup - interrupt grace - tail.

    R3: the grace reserved here is the WORST case over every worker kind, not
    the default one.  A collect/gemrate worker may spend 240 s finishing its
    step and ingesting its manifest, and reserving only 60 s would put that kill
    outside PT55M.
    """

    return max(
        0.0,
        tick_external_limit_seconds()
        - TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
        - TICK_INTERRUPT_GRACE_MAX_SECONDS
        - TICK_DRAIN_TAIL_RESERVE_SECONDS,
    )


def adopt_grace_seconds() -> int:
    raw = os.environ.get("CARDZ_V2_ADOPT_GRACE_SECONDS", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return ADOPT_GRACE_SECONDS
    return value if value >= 0 else ADOPT_GRACE_SECONDS


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(canonical_json(value) + b"\n")
    os.replace(temporary, path)


def task_result(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("result_json")
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def source_payload(adapter: Any, task: SourceTask) -> dict[str, Any]:
    """Serialize only orchestration metadata; provider execution stays owned by its adapter."""

    if isinstance(adapter, CommandSourceAdapter):
        return task_payload(adapter, task)
    return {
        "kind": "source",
        "shard": task.shard,
        "spec": {
            "sourceCode": adapter.spec.source_code,
            "capabilities": list(adapter.spec.capabilities),
            "transport": adapter.spec.transport,
            "requiredClass": adapter.spec.required_class,
            "adapterVersion": adapter.spec.adapter_version,
        },
    }


def publication_manifest_path(run_id: str) -> Path:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return ROOT / "data" / "runtime" / "daily-chain-v2" / "publication" / f"{digest}.json"


def aggregate_source_health(tasks: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in tasks:
        grouped.setdefault(str(row["source_code"]), []).append(row)
    result: dict[str, Any] = {}
    for code, rows in sorted(grouped.items()):
        worst = max(rows, key=lambda row: TASK_STATUS_RANK.get(str(row["status"]), 99))
        status = str(worst["status"])
        result[code] = {
            "status": status,
            "requiredClass": str(worst["required_class"]),
            "tasks": len(rows),
            "completed": sum(1 for row in rows if row["status"] == "COMPLETED"),
            "degraded": sum(1 for row in rows if row["status"] == "DEGRADED"),
            "attempts": sum(int(row.get("attempts") or 0) for row in rows),
            "errors": sorted({
                str(row.get("last_error_code")) for row in rows if row.get("last_error_code")
            }),
        }
    return result


def degraded_source_codes(source_health: Mapping[str, Any]) -> list[str]:
    return sorted(
        code for code, row in source_health.items()
        if str(row.get("requiredClass")) != "core" and str(row.get("status")) != "COMPLETED"
    )


def source_barrier_ready(
    tasks: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    cutoff: datetime,
) -> bool:
    if not tasks:
        return False
    # `system` rows in this phase are orchestrator stages parked alongside the
    # sources (the identity census), not sources.  aggregate_source_health
    # already draws exactly this line; counting one here would let an optional
    # housekeeping stage hold the whole business date behind the core barrier,
    # every pass, until 10:15.  Nothing this changes exists before the census:
    # every other `system` stage lives in infra/barrier/identity/publish.
    tasks = [row for row in tasks if str(row.get("source_code", "")) != "system"]
    core = [row for row in tasks if str(row["required_class"]) == "core"]
    # SKIPPED only comes from an operator `retire` with a journaled reason: a
    # core contract-repair whose shortfall closed (08-24: a 1604-card gemrate
    # pop repair planned from a window bug) must not hold the run behind a
    # re-collection nobody needs.  The contract stage downstream still
    # measures the data itself, so this settles scheduling, not the data gate.
    if not core or any(str(row["status"]) not in {"COMPLETED", "SKIPPED"} for row in core):
        return False
    # Quote/extra sources are allowed to keep their own leases and retry in
    # parallel after the deterministic 10:15 snapshot.  A slow optional source
    # must not hold the active universe behind the core barrier until 17:00.
    if now >= cutoff:
        return True
    unsettled = [
        row for row in tasks
        if str(row["required_class"]) != "core"
        and str(row["status"]) not in {"COMPLETED", "DEGRADED", "TERMINAL", "SKIPPED"}
    ]
    return not unsettled


def blocked_core_source_tasks(
    tasks: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Core source rows that settled without succeeding (audit P0-1).

    source_barrier_ready only opens a core row on COMPLETED/SKIPPED, so the
    other three settled states -- TERMINAL, PARKED and DEGRADED -- each hold
    the whole business date, and none of them is ever claimed again
    (reopen_retryable_terminal refuses a terminal decision; CLAIMABLE_TASK_STATES
    contains none of the three).  On 2026-08-24 fx:rates:all went TERMINAL at
    +0.3m and the chain sat silent for 28.9 minutes, because the failure was
    written as TASK_ERROR, which is not an ALWAYS_ALERT_EVENTS type.

    DEGRADED is the quietest and the most reachable of the three: any source
    report with quarantined >= 1 finishes through finish_success(degraded=True)
    (daily_chain_v2_worker.py), so there is no TASK_ERROR to find afterwards --
    and neither unpark nor retire accepts DEGRADED, so the operator has no
    command to clear it either.  Naming the row is the whole fix; the barrier
    itself stays exactly as strict.
    """

    return [
        row for row in tasks
        if str(row["required_class"]) == "core"
        and str(row["status"]) in TERMINAL_TASK_STATES
        and str(row["status"]) not in {"COMPLETED", "SKIPPED"}
    ]


def optional_phase_settled(
    tasks: Sequence[Mapping[str, Any]],
    *,
    now: datetime,
    cutoff: datetime,
) -> bool:
    if not tasks or any(str(row["status"]) == "RUNNING" for row in tasks):
        return False
    if all(str(row["status"]) in SUCCESS_TASK_STATES | {"TERMINAL"} for row in tasks):
        return True
    return now >= cutoff


class DailyChainV2:
    def __init__(
        self,
        *,
        journal: Journal,
        business_date: date,
        allow_publish: bool,
        notify: bool,
        deadline_monotonic: float,
        schedule: Mapping[str, datetime] | None = None,
        run_label: str = "",
        supersede_seq: int = 1,
    ) -> None:
        self.journal = journal
        self.business_date = business_date
        self.day_text = business_date.isoformat()
        self.run_label = (run_label or "").strip()
        # Generation of this business date.  1 is the historical single run;
        # >1 means a previous generation was archived out of the journal and
        # this one reruns the same date with fresh data -- own directory, full
        # publication rights.
        self.supersede_seq = max(1, int(supersede_seq))
        self.run_id = run_id_for(business_date, self.run_label, self.supersede_seq)
        self.allow_publish = allow_publish
        self.notify = notify
        self.deadline_monotonic = deadline_monotonic
        self.runtime_dir = runtime_dir_for(
            business_date, self.run_label, self.supersede_seq
        )
        self.log_dir = self.runtime_dir / "logs"
        self.receipt_dir = self.runtime_dir / "receipts"
        self.registry = build_default_registry()
        self.schedule = dict(schedule or jst_schedule(business_date))
        # Claims this process currently owns, so a signal releases exactly its
        # own work and never steals another tick's live lease.
        self.own_claims: dict[str, str] = {}
        self.tick_started_at_utc: str | None = None
        self._health_written_monotonic = float("-inf")
        self._drain_reported = False
        # Worker threads all ask "is my claim still live?"; the answer is cached
        # per claim for DRAIN_LIVENESS_CACHE_SECONDS (review fix).
        self._live_claim_cache: dict[str, tuple[float, bool]] = {}
        self._live_claim_lock = threading.Lock()
        self.tick_started_monotonic = time.monotonic()
        # Review fix 2026-08-24 (blocking): the drain arithmetic below is only
        # sound while --max-runtime-seconds equals DEFAULT_MAX_RUNTIME_SECONDS.
        # The registered Task Scheduler action bakes its OWN -MaxRuntimeSeconds
        # into the argument string (scripts/install_cardz_daily_v2_task.ps1), so
        # until somebody re-runs that installer the tick is still handed 3000 --
        # and with the R3 grace that tick would finish 180 s AFTER PT55M and be
        # hard-killed mid-finalisation.  The tick is self-safe instead of
        # trusting its registration: claiming never outlives the hard limit,
        # whatever it was handed.  (A hard limit of zero means somebody shrank
        # the external limit below the reserves; leave that tick alone rather
        # than closing claiming before it opened.)
        # Verifier fix 2026-08-24 (major): the clamp used to be silent -- no
        # print, no journal event, no health field -- while the CLI still
        # accepted anything up to MAX_RUNTIME_SECONDS_CEILING.  A hand-run tick
        # has no ExecutionTimeLimit at all, so there the clamp buys nothing and
        # merely cuts the operator's own 40-90 min PriceCharting watch (R5) to
        # 47 minutes.  The clamp stays -- it is what keeps the LIVE 3000 s
        # registration inside PT55M -- but it says so on stdout (launcher log),
        # in the journal (initialise -> TICK_CLAIM_WINDOW_CLAMPED) and in
        # health.json, and it names TICK_EXTERNAL_LIMIT_ENV, which really does
        # lift it for a tick that must outlive PT55M.
        hard_limit = tick_hard_limit_seconds()
        requested_claim_seconds = max(
            0.0, deadline_monotonic - self.tick_started_monotonic
        )
        self.claim_window_requested_seconds = round(requested_claim_seconds, 1)
        self.claim_window_clamped = False
        if hard_limit > 0.0 and requested_claim_seconds > hard_limit:
            deadline_monotonic = self.tick_started_monotonic + hard_limit
            self.deadline_monotonic = deadline_monotonic
            self.claim_window_clamped = True
            print(
                "TICK_CLAIM_WINDOW_CLAMPED"
                f" requested={requested_claim_seconds:.0f}s"
                f" effective={hard_limit:.0f}s"
                f" externalLimit={tick_external_limit_seconds():.0f}s"
                f" lift={TICK_EXTERNAL_LIMIT_ENV}"
                " (a hand-run tick that must outlive PT55M sets that variable;"
                " a scheduled one needs install_cardz_daily_v2_task.ps1 -Apply)",
                flush=True,
            )
        self.claim_window_seconds = round(
            max(0.0, deadline_monotonic - self.tick_started_monotonic), 1
        )
        # Operator finding 2026-08-24 (not in the audit): a worker whose honest
        # duration exceeds the tick budget can never finish.  Task
        # gemrate:contract-repair:pop:all (1604 cards, ~55-60 min) was cut at
        # every tick deadline and restarted from zero, spending the interruption
        # budget until it would PARK.  Past the claiming deadline this tick keeps
        # draining live, heartbeating claims up to the smaller of the drain
        # ceiling and the EXTERNAL Task Scheduler ExecutionTimeLimit.
        self.drain_deadline_monotonic = max(
            deadline_monotonic,
            min(
                deadline_monotonic + TICK_DRAIN_CEILING_SECONDS,
                self.tick_started_monotonic + tick_hard_limit_seconds(),
            ),
        )

    def initialise(
        self,
        provenance: Mapping[str, Any],
        *,
        renew_manual_window: bool = False,
    ) -> dict[str, Any]:
        self.journal.initialise()
        row = self.journal.ensure_run(
            business_date=self.day_text,
            run_id=self.run_id,
            source_cutoff_at=iso(self.schedule["source_cutoff"]),
            sla_at=iso(self.schedule["sla"]),
            final_at=iso(self.schedule["final"]),
        )
        reopened: list[str] = []
        previous_status = ""
        if renew_manual_window:
            row, reopened, previous_status = self.journal.renew_manual_window(
                self.run_id,
                source_cutoff_at=iso(self.schedule["source_cutoff"]),
                sla_at=iso(self.schedule["sla"]),
                final_at=iso(self.schedule["final"]),
            )
        # audit B5: the orchestrator plans contract repairs in-process, so it
        # needs the same widened coverage window its stage children get.  Export
        # once, here, as soon as the run row is known.
        export_run_started_at(row)
        # Deadlines belong to the durable run.  A resumed manual E2E must not
        # receive a fresh eight-hour window merely because another tick began;
        # only the explicit manual renewal path above may change them.
        for key, column in (
            ("source_cutoff", "source_cutoff_at"),
            ("sla", "sla_at"),
            ("final", "final_at"),
        ):
            persisted = datetime.fromisoformat(str(row[column]))
            if persisted.tzinfo is None:
                persisted = persisted.replace(tzinfo=timezone.utc)
            self.schedule[key] = persisted.astimezone(timezone.utc)
        origin, inserted = self.journal.register_provenance(self.run_id, provenance)
        if renew_manual_window:
            self.journal.add_event(
                self.run_id,
                "MANUAL_WINDOW_RENEWED",
                sha256({
                    "sourceCutoffAt": row["source_cutoff_at"],
                    "slaAt": row["sla_at"],
                    "finalAt": row["final_at"],
                    "provenance": provenance,
                })[:16],
                {
                    "runId": self.run_id,
                    "origin": origin,
                    "previousStatus": previous_status,
                    "sourceCutoffAt": row["source_cutoff_at"],
                    "slaAt": row["sla_at"],
                    "finalAt": row["final_at"],
                    "reopenedTaskKeys": reopened,
                },
            )
        if inserted:
            self.journal.add_event(
                self.run_id,
                "RUN_STARTED",
                self.day_text,
                {"runId": self.run_id, "businessDate": self.day_text, "origin": origin},
            )
        if self.claim_window_clamped:
            # Once per run (the key carries the numbers), so a registration that
            # disagrees with the code is read on the channel and in the journal
            # instead of being inferred from a short tick.
            self.journal_event(
                "TICK_CLAIM_WINDOW_CLAMPED",
                f"{self.day_text}:{self.claim_window_requested_seconds:.0f}"
                f":{self.claim_window_seconds:.0f}",
                {
                    "runId": self.run_id,
                    "stage": "orchestrator",
                    "source": "system",
                    "requestedClaimSeconds": self.claim_window_requested_seconds,
                    "effectiveClaimSeconds": self.claim_window_seconds,
                    "tickHardLimitSeconds": round(tick_hard_limit_seconds(), 1),
                    "externalLimitSeconds": tick_external_limit_seconds(),
                    "liftWith": TICK_EXTERNAL_LIMIT_ENV,
                    "nextRetry": "re-run install_cardz_daily_v2_task.ps1 -Apply,"
                    f" or set {TICK_EXTERNAL_LIMIT_ENV} for a hand-run tick",
                },
            )
        self.report_long_db_sessions()
        return row

    def report_long_db_sessions(self, *, minutes: int = 20) -> None:
        """Journal canonical-DB sessions older than `minutes`.  Observation only.

        Nothing is terminated: this tick has no way to tell an operator's
        deliberate long report from a stuck read, and a preflight that ends
        somebody else's transaction is worse than the condition it watches.
        Every failure is swallowed for the same reason -- a preflight must
        never be the thing that stops a tick.
        """

        try:
            from qualified_pool_operator import db, load_env

            load_env()
            connection = db()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, user, db, command, time, LEFT(info, 200) AS sql_head
                        FROM information_schema.processlist
                        WHERE user LIKE %s AND command <> 'Sleep' AND time > %s
                        ORDER BY time DESC
                        """,
                        ("cardz%", int(minutes) * 60),
                    )
                    # information_schema labels come back upper-cased on this
                    # Windows MySQL build whatever the query spells.
                    rows = [
                        {str(key).lower(): value for key, value in dict(row).items()}
                        for row in cursor.fetchall()
                    ]
            finally:
                connection.close()
        except Exception:
            return
        if not rows:
            return
        ids = [int(row.get("id") or 0) for row in rows]
        seconds = [int(row.get("time") or 0) for row in rows]
        self.journal.add_event(
            self.run_id,
            "DB_LONG_SESSIONS_OBSERVED",
            sha256({"ids": ids, "day": self.day_text})[:16],
            {
                "runId": self.run_id,
                "sessionCount": len(rows),
                "maxMinutes": max(seconds) // 60 if seconds else 0,
                "sessions": [
                    {
                        "id": int(row.get("id") or 0),
                        "seconds": int(row.get("time") or 0),
                        "command": str(row.get("command") or ""),
                        "sql": str(row.get("sql_head") or "")[:200],
                    }
                    for row in rows
                ],
            },
        )

    def add_stage(
        self,
        *,
        phase: str,
        capability: str,
        stage_name: str,
        args: Sequence[str] = (),
        required_class: str = "core",
        concurrency_group: str = "db-writer",
        max_attempts: int = 3,
        kind: str = "stage",
    ) -> str:
        revision = sha256(
            {"contract": "daily-chain-v2-stage", "stage": stage_name, "args": list(args), "v": 2}
        )[:16]
        return self.journal.add_raw_task(
            run_id=self.run_id,
            business_date=self.day_text,
            phase=phase,
            source_code="system",
            capability=capability,
            required_class=required_class,
            concurrency_group=concurrency_group,
            max_concurrency=1,
            max_attempts=max_attempts,
            input_revision=revision,
            shard=capability,
            payload={"kind": kind, "stageName": stage_name, "stageArgs": list(args)},
        )

    def identity_lanes(self) -> tuple[tuple[str, str], ...]:
        """Discovery lanes declared by the registered identity sources."""

        return identity_lanes(adapter.spec for adapter in self.registry.enabled())

    def plan_candidate_source_tasks(self, pending_ids: Sequence[int]) -> int:
        """Ask every registered adapter to fan out the same pending identity set."""

        pending = sorted({int(value) for value in pending_ids if int(value) > 0})
        context = {
            "phase": "candidate-source",
            "run_id": self.run_id,
            "business_date": self.day_text,
            "variant_ids": pending,
            "input_revision": sha256({
                "contract": "candidate-source-plan-v2", "variantIds": pending,
            })[:16],
        }
        added = 0
        for adapter in self.registry.enabled():
            for task in adapter.plan(context):
                if task.source_code != adapter.spec.source_code:
                    raise RuntimeError(
                        f"adapter {adapter.spec.source_code} planned foreign candidate task"
                    )
                if self.journal.add_task(
                    task,
                    phase="candidate-source",
                    # Candidate enrichment must never turn a non-active card
                    # into a blocker for the already-active universe.  Once a
                    # card activates, the post-activation core contract makes
                    # its GemRate/quote coverage mandatory atomically.
                    required_class="extra",
                    concurrency_group=adapter.spec.concurrency_group,
                    max_concurrency=adapter.spec.max_concurrency,
                    max_attempts=7,
                    payload=source_payload(adapter, task),
                ):
                    added += 1
        return added

    def _plan_contract_repair(
        self,
        *,
        source_code: str,
        capability: str,
        variant_ids: Sequence[int],
    ) -> int:
        """Create one idempotent, adapter-owned targeted coverage repair."""

        targets = sorted({int(value) for value in variant_ids if int(value) > 0})
        if not targets:
            return 0
        # A registry source with no repair-capable adapter is a normal shape,
        # not a stage failure: record why nothing was planned and let the rest
        # of the repair fan-out continue.
        try:
            adapter = self.registry.get(source_code)
        except KeyError:
            self._repair_skipped(source_code, capability, "SOURCE_NOT_REGISTERED", targets)
            return 0
        if not isinstance(adapter, CommandSourceAdapter):
            self._repair_skipped(source_code, capability, "ADAPTER_NOT_COMMAND_BACKED", targets)
            return 0
        capability_adapters = adapter.capability_adapters or {}
        legacy_adapters = tuple(capability_adapters.get(capability) or ())
        if not legacy_adapters:
            self._repair_skipped(source_code, capability, "CAPABILITY_NOT_REPAIRABLE", targets)
            return 0
        # audit P2-2 (guard only, the identity redesign is a separate change):
        # the revision hash covers variantIds, so every plan pass where the
        # shortfall shrank mints a brand-new task with a brand-new 7-attempt
        # ladder -- 2026-08-22 accumulated four pricecharting and three
        # snkrdunk keys -- and each fresh PENDING row re-closes a source
        # barrier that had already opened.  One unsettled repair per
        # (source, capability) at a time; the live one keeps its targets.
        existing = next(
            (
                row for row in self.journal.tasks(self.run_id, phase="source")
                if str(row["source_code"]) == source_code
                and str(row["capability"]) == f"contract-repair:{capability}"
                and str(row["status"]) not in TERMINAL_TASK_STATES
            ),
            None,
        )
        if existing is not None:
            self.journal_event(
                "SOURCE_CONTRACT_REPAIR_DEFERRED",
                f"{source_code}:{capability}:{existing['task_key']}:{sha256(targets)[:16]}",
                {
                    "runId": self.run_id,
                    "source": source_code,
                    "capability": capability,
                    "errorCode": "REPAIR_ALREADY_IN_FLIGHT",
                    "taskKey": str(existing["task_key"]),
                    "status": str(existing["status"]),
                    "variantIds": targets[:200],
                    "variantCount": len(targets),
                },
            )
            return 0
        revision = sha256({
            "contract": "source-contract-repair-v2",
            "businessDate": self.day_text,
            "adapterVersion": adapter.spec.adapter_version,
            "sourceCode": source_code,
            "capability": capability,
            "variantIds": targets,
        })[:16]
        task = SourceTask(
            run_id=self.run_id,
            business_date=self.day_text,
            source_code=source_code,
            capability=f"contract-repair:{capability}",
            external_id=revision,
            input_revision=revision,
            checkpoint={
                "worker": {
                    "adapters": list(legacy_adapters),
                    "variantIds": targets,
                    "leaseScope": f"{capability}-repair-{revision}",
                }
            },
        )
        if not self.journal.add_task(
            task,
            phase="source",
            required_class=adapter.spec.required_class,
            concurrency_group=adapter.spec.concurrency_group,
            max_concurrency=adapter.spec.max_concurrency,
            max_attempts=7,
            payload=source_payload(adapter, task),
        ):
            return 0
        self.journal.add_event(
            self.run_id,
            "SOURCE_CONTRACT_REPAIR_PLANNED",
            f"{source_code}:{capability}:{revision}",
            {
                "runId": self.run_id,
                "source": source_code,
                "capability": capability,
                "variantIds": targets,
            },
        )
        return 1

    def _repair_skipped(
        self,
        source_code: str,
        capability: str,
        reason: str,
        variant_ids: Sequence[int],
    ) -> None:
        self.journal_event(
            "REPAIR_SKIPPED_NO_ADAPTER",
            f"{source_code}:{capability}:{reason}",
            {
                "runId": self.run_id,
                "source": source_code,
                "capability": capability,
                "errorCode": reason,
                "variantIds": sorted(int(value) for value in variant_ids)[:200],
                "variantCount": len(variant_ids),
            },
        )

    def plan_contract_repair_tasks(self) -> int:
        """Fan out missing POP/quote coverage through registered capabilities."""

        from daily_chain_v2_db import (
            business_window_utc,
            current_run_contract,
            quote_repair_plan,
        )

        # audit B5: the shortfall this reads is measured inside the same window,
        # so a window that has not opened yet cannot report a real shortfall --
        # it reports everything as missing.  Refuse loudly instead of minting a
        # whole-universe repair (2026-08-24: 1604 gemrate pop cards).
        window_start, _window_end = business_window_utc(self.day_text)
        planned_at = utc_now().replace(tzinfo=None)
        if planned_at < window_start:
            raise RuntimeError(
                "contract repair window has not opened yet:"
                f" windowStart={window_start.isoformat()} now={planned_at.isoformat()}"
                f" run={self.run_id}"
                f" {RUN_STARTED_AT_ENV}={os.environ.get(RUN_STARTED_AT_ENV, '') or '<unset>'}"
            )
        contract = current_run_contract(self.day_text)
        added = 0
        # The contract names its own POP sections, so a third pop source is
        # repaired by being registered rather than by being named here.
        for source_code in contract.get("popSources") or ():
            missing = sorted({
                int(value)
                for value in (contract.get(source_code) or {}).get("missing") or []
                if int(value) > 0
            })
            added += self._plan_contract_repair(
                source_code=str(source_code),
                capability="pop",
                variant_ids=missing,
            )
        quote_missing = sorted({
            int(value) for value in (contract.get("quotes") or {}).get("missing") or []
            if int(value) > 0
        })
        plan = quote_repair_plan(quote_missing)
        rejected = plan.get("rejected") or []
        if rejected:
            self.journal_event(
                "QUOTE_ROUTE_POLICY_REJECTED",
                sha256(rejected)[:16],
                {
                    "runId": self.run_id,
                    "source": "quote-route-policy",
                    "errorCode": "QUOTE_ROUTE_POLICY_MISMATCH",
                    "rejected": rejected[:200],
                    "rejectedCount": len(rejected),
                },
            )
        for source_code, variant_ids in (plan.get("routes") or {}).items():
            added += self._plan_contract_repair(
                source_code=source_code,
                capability="quote",
                variant_ids=variant_ids,
            )
        return added

    def plan_quote_repair_tasks(self) -> int:
        """Compatibility entrypoint for callers written before POP repair."""

        return self.plan_contract_repair_tasks()

    def stage_row(self, capability: str) -> dict[str, Any] | None:
        rows = [
            row for row in self.journal.tasks(self.run_id)
            if str(row["source_code"]) == "system" and str(row["capability"]) == capability
        ]
        if len(rows) > 1:
            raise RuntimeError(f"duplicate V2 stage task: {capability}")
        return rows[0] if rows else None

    def stage_complete(self, capability: str) -> bool:
        row = self.stage_row(capability)
        return bool(row and str(row["status"]) in SUCCESS_TASK_STATES)

    def pending_activation_ids(self) -> list[int]:
        row = self.stage_row("pending-identities") or {}
        if str(row.get("status") or "") not in SUCCESS_TASK_STATES:
            return []
        return sorted({
            int(value) for value in task_result(row).get("pendingActivationIds") or []
            if int(value) > 0
        })

    def activation_barrier_open(self, now: datetime) -> bool:
        pending = self.pending_activation_ids()
        if not pending:
            return True
        activation = self.stage_row("candidate-activation") or {}
        status = str(activation.get("status") or "")
        if status == "COMPLETED":
            return True
        # Candidate work is extra to the already-active universe.  Only the
        # deterministic cutoff may release a failed candidate barrier; before
        # it, a terminal attempt remains a recovery target and never licenses
        # accept/BOX/publish.
        return now >= self.schedule["source_cutoff"] and status in TERMINAL_TASK_STATES

    def reconcile_post_activation_dependencies(self) -> list[str]:
        activation = self.stage_row("candidate-activation") or {}
        registry = self.stage_row("collection-registry-post") or {}
        dependency_updated_at = max(
            str(activation.get("updated_at") or ""),
            str(registry.get("updated_at") or ""),
        )
        if not dependency_updated_at:
            return []
        reopened = self.journal.reopen_successful_tasks_before(
            self.run_id,
            ("core-contract-post", "daily-accept", "box", "release", "live-confirm"),
            dependency_updated_at=dependency_updated_at,
            reason="post-activation universe/registry revision superseded this result",
        )
        if reopened:
            self.journal.add_event(
                self.run_id,
                "DEPENDENCY_CHANGED",
                sha256(reopened)[:16],
                {
                    "runId": self.run_id,
                    "source": "candidate-activation",
                    "taskKeys": reopened,
                    "dependencyUpdatedAt": dependency_updated_at,
                },
            )
        return reopened

    def publication_allowed(self) -> bool:
        """May this run plan the release/live-confirm legs at all?

        A supersede rerun keeps every publication right the first generation
        had -- that is the whole point of Route B.  A REHEARSAL never has them,
        and says so here as well as at the CLI: both forks the same runtime
        directory mechanic, so the directory shape must never be what decides.
        """

        return bool(self.allow_publish) and not self.run_label

    def schema_capabilities(self) -> tuple[str, ...]:
        """One infra stage per V2 migration file on disk, in file order."""

        return v2_schema_capabilities(ROOT)

    def plan(self, now: datetime) -> None:
        for capability in self.schema_capabilities():
            if self.stage_row(capability) is None:
                self.add_stage(
                    phase="infra",
                    capability=capability,
                    stage_name="migrate",
                    max_attempts=4,
                )
                return
            if not self.stage_complete(capability):
                return
        if self.stage_row("collection-registry") is None:
            self.add_stage(
                phase="infra", capability="collection-registry", stage_name="registry", max_attempts=4
            )
            return
        if not self.stage_complete("collection-registry"):
            return

        source_tasks = self.journal.tasks(self.run_id, phase="source")
        if not source_tasks:
            context = {
                "run_id": self.run_id,
                "business_date": self.day_text,
                "input_revision": sha256({"businessDate": self.day_text, "contract": "source-plan-v2"})[:16],
            }
            for task in self.registry.plan_all(context):
                adapter = self.registry.get(task.source_code)
                self.journal.add_task(
                    task,
                    phase="source",
                    required_class=adapter.spec.required_class,
                    concurrency_group=adapter.spec.concurrency_group,
                    max_concurrency=adapter.spec.max_concurrency,
                    max_attempts=SOURCE_MAX_ATTEMPTS,
                    payload=source_payload(adapter, task),
                )
            return
        # Identity census.  GemRate's PSA10>=1000 universe file is the input to
        # identity intake; when it ages out the chain keeps binding yesterday's
        # universe and nothing says so.  The stage SELF-GATES on the file's
        # mtime and usually no-ops, so it is planned WITHOUT a return and with
        # no follow-up gate anywhere: a census that fails, or never finishes,
        # must not be able to touch identity, acceptance or publication.
        #
        # It sits in `source` because that is the only phase eligible_phases
        # keeps claimable in every state of the run, published included -- and
        # it must be planned only AFTER the adapters above, because an empty
        # `source` phase is what triggers that planning.
        if self.stage_row("identity-census") is None:
            self.add_stage(
                phase="source",
                capability="identity-census",
                stage_name="identity-census",
                required_class="extra",
                concurrency_group="host:gemrate",
                max_attempts=2,
            )
        repair_contracts = [
            row for row in (
                self.stage_row("core-contract-pre"),
                self.stage_row("core-contract-post"),
            )
            if row
            and str(row.get("status") or "") in {"RETRY", "TERMINAL"}
            # Every coverage section reports "<key>Missing=<n>", so a third
            # source becomes repairable without another literal token here.
            and CONTRACT_SHORTFALL_MARKER in str(row.get("last_error") or "")
        ]
        if repair_contracts:
            try:
                self.plan_contract_repair_tasks()
            except Exception as error:  # noqa: BLE001 - source/DB recovery keeps running
                self.journal.add_event(
                    self.run_id,
                    "SOURCE_CONTRACT_REPAIR_PLAN_ERROR",
                    f"{type(error).__name__}:{str(error)[:240]}",
                    {
                        "runId": self.run_id,
                        "capability": "quote",
                        "errorCode": type(error).__name__,
                        "error": str(error)[-2000:],
                    },
                )
            source_tasks = self.journal.tasks(self.run_id, phase="source")
        # audit P0-1: say it out loud before returning.  CORE_TASK_PARKED is in
        # ALWAYS_ALERT_EVENTS, so this reaches Telegram on its own channel with
        # a 30 minute cooldown and does not depend on --notify being on.  The
        # dedupe key carries the state and alert_scope carries (task, state) as
        # well, so two different blocked core rows -- or one row that goes
        # PARKED after TERMINAL -- each page once instead of collapsing into the
        # first row's per-business-date cooldown.
        for blocked in blocked_core_source_tasks(source_tasks):
            state = str(blocked["status"])
            self.journal_event(
                "CORE_TASK_PARKED",
                f"{blocked['task_key']}:{state}:{int(blocked.get('attempts') or 0)}",
                {
                    "runId": self.run_id,
                    "taskKey": str(blocked["task_key"]),
                    "phase": str(blocked["phase"]),
                    "source": str(blocked["source_code"]),
                    "capability": str(blocked["capability"]),
                    "state": state,
                    "errorCode": str(blocked.get("last_error_code") or "CORE_SOURCE_BLOCKED"),
                    "attempts": int(blocked.get("attempts") or 0),
                    "maxAttempts": int(blocked.get("max_attempts") or 0),
                    "reason": "core source settled without success; the source barrier stays closed",
                    # Only PARKED/TERMINAL have an operator command; telling the
                    # operator to unpark a DEGRADED row sends them at a lever
                    # that is not connected to anything.
                    "nextRetry": (
                        "operator unpark or retire"
                        if state in UNPARKABLE_TASK_STATES
                        else "no operator command clears DEGRADED:"
                        " fix the source and re-run this business date"
                    ),
                },
                alert_scope=f"{blocked['task_key']}:{state}",
            )
        if not source_barrier_ready(source_tasks, now=now, cutoff=self.schedule["source_cutoff"]):
            return

        if self.stage_row("core-contract-pre") is None:
            self.add_stage(
                phase="barrier",
                capability="core-contract-pre",
                stage_name="contract",
                args=(
                    "--run-id", self.run_id,
                    "--business-date", self.day_text,
                    "--label", "pre-identity",
                ),
                max_attempts=7,
            )
            return
        if not self.stage_complete("core-contract-pre"):
            return

        # The 10:15 degrade runs BEFORE the identity stages are gated on.
        # Every identity-phase stage below waits for the one above it, so a
        # stage still retrying at the cutoff would otherwise hold the whole
        # run behind a gate that the cutoff itself is supposed to open.
        if now >= self.schedule["source_cutoff"]:
            closed = self.journal.degrade_unfinished_phase(
                self.run_id,
                "identity",
                error_code="IDENTITY_CUTOFF",
                reason="identity retry window closed at the 10:15 candidate snapshot barrier",
            )
            if closed:
                self.journal.add_event(
                    self.run_id,
                    "TASK_RETRY_STATE",
                    "identity:IDENTITY_CUTOFF:DEGRADED",
                    {
                        "runId": self.run_id,
                        "phase": "identity",
                        "source": "identity",
                        "errorCode": "IDENTITY_CUTOFF",
                        "status": "DEGRADED",
                        "closedTasks": closed,
                        "nextRetry": "next business date",
                    },
                )

        # Identity intake runs before the lanes: last night's operator pastes
        # are applied first, then the cards GemRate says crossed the floor are
        # taken in, so the same tick's lanes already go looking for them.
        # Both gate on TERMINAL_TASK_STATES -- every settled state, PARKED
        # included -- never on stage_complete: a stage that spent its attempts
        # or its interruption budget retires itself instead of parking the
        # whole run behind a gate only an operator `unpark` could ever open.
        if self.stage_row("identity-operator-apply") is None:
            self.add_stage(
                phase="identity",
                capability="identity-operator-apply",
                stage_name="operator-apply",
                required_class="extra",
                concurrency_group="db-writer",
                max_attempts=3,
            )
            return
        operator_apply = self.stage_row("identity-operator-apply") or {}
        if str(operator_apply.get("status") or "") not in TERMINAL_TASK_STATES:
            return
        if self.stage_row("identity-intake") is None:
            self.add_stage(
                phase="identity",
                capability="identity-intake",
                stage_name="intake",
                args=("--business-date", self.day_text),
                required_class="extra",
                concurrency_group="host:gemrate",
                max_attempts=4,
            )
            return
        intake_stage = self.stage_row("identity-intake") or {}
        if str(intake_stage.get("status") or "") not in TERMINAL_TASK_STATES:
            return

        identity = self.journal.tasks(self.run_id, phase="identity")
        # Lane and its transport group are declared by the registered
        # identity sources (SourceSpec.identity_lane / concurrency_group),
        # persisted in market_source_registry by migration 054.  The check is
        # per lane, not "is the identity phase empty": the intake stages above
        # already live in this phase, and an emptiness test would leave every
        # lane unplanned forever.
        planned_lane = False
        for lane, group in self.identity_lanes():
            if self.stage_row(f"identity-{lane}") is None:
                self.add_stage(
                    phase="identity",
                    capability=f"identity-{lane}",
                    stage_name="discover",
                    args=("--lane", lane),
                    required_class="extra",
                    concurrency_group=group,
                    max_attempts=7,
                )
                planned_lane = True
        if planned_lane:
            return
        if not optional_phase_settled(identity, now=now, cutoff=self.schedule["source_cutoff"]):
            return

        # Re-verification runs once the lanes have settled and their transport
        # is free again, on the same registry-declared groups: the browser lane
        # re-proves PC pastes on cdp:9333, the http lane re-proves SNKRDUNK.
        planned_reverify = False
        for lane, group in self.identity_lanes():
            if self.stage_row(f"identity-reverify-{lane}") is None:
                self.add_stage(
                    phase="identity",
                    capability=f"identity-reverify-{lane}",
                    stage_name="reverify",
                    args=("--lane", lane),
                    required_class="extra",
                    concurrency_group=group,
                    max_attempts=7,
                )
                planned_reverify = True
        if planned_reverify:
            return
        for lane, _group in self.identity_lanes():
            reverify = self.stage_row(f"identity-reverify-{lane}") or {}
            if str(reverify.get("status") or "") not in TERMINAL_TASK_STATES:
                return

        if self.stage_row("pending-identities") is None:
            self.add_stage(
                phase="identity",
                capability="pending-identities",
                stage_name="pending",
                required_class="extra",
                max_attempts=3,
            )
            return
        pending_stage = self.stage_row("pending-identities") or {}
        # audit P1-8: these four gates hand-wrote the settled set and left out
        # PARKED, so a parked optional stage blocked every later stage -- daily
        # accept included -- until the operator noticed.  TERMINAL_TASK_STATES is
        # the one definition of "this row will never move again"; the two gates
        # that additionally need the cutoff escape (source_barrier_ready,
        # optional_phase_settled) keep their own, deliberately different, sets.
        if str(pending_stage.get("status") or "") not in TERMINAL_TASK_STATES:
            return

        # The morning identity brief lives in `barrier`, not in `identity`:
        # degrade_unfinished_phase(run_id, "identity", IDENTITY_CUTOFF) would
        # kill the report on exactly the slow morning it is needed.  Planned
        # without a return and without a follow-up gate, so a retrying brief
        # can never hold activation, acceptance, or publication.
        if self.stage_row("identity-brief") is None:
            self.add_stage(
                phase="barrier",
                capability="identity-brief",
                stage_name="brief",
                args=("--business-date", self.day_text),
                required_class="extra",
                concurrency_group="db-writer",
                max_attempts=3,
            )

        pending_result = (
            task_result(pending_stage)
            if str(pending_stage.get("status") or "") in SUCCESS_TASK_STATES
            else {}
        )
        pending_ids = sorted({
            int(value) for value in pending_result.get("pendingActivationIds") or []
        })
        if pending_ids and now >= self.schedule["source_cutoff"]:
            # Candidate work is optional to the already-active universe.  Once
            # the 10:15 snapshot closes, persist one visibly degraded
            # activation task and let the core chain continue; never start a
            # shadow rebuild after the cutoff and hold today's publication.
            pending_count = len(pending_ids)
            if self.stage_row("candidate-activation") is None:
                self.add_stage(
                    phase="activation",
                    capability="candidate-activation",
                    stage_name="activate",
                    args=(
                        "--run-id", self.run_id,
                        "--business-date", self.day_text,
                    ),
                    required_class="extra",
                    max_attempts=12,
                )
            closed = self.journal.degrade_unfinished_phase(
                self.run_id,
                "activation",
                error_code="CANDIDATE_ACTIVATION_CUTOFF",
                reason="candidate activation deferred after the 10:15 snapshot barrier",
            )
            if closed:
                self.journal.add_event(
                    self.run_id,
                    "TASK_RETRY_STATE",
                    "activation:CANDIDATE_ACTIVATION_CUTOFF:DEGRADED",
                    {
                        "runId": self.run_id,
                        "phase": "activation",
                        "source": "candidate-activation",
                        "errorCode": "CANDIDATE_ACTIVATION_CUTOFF",
                        "status": "DEGRADED",
                        "closedTasks": closed,
                        "pendingCandidates": pending_count,
                        "nextRetry": "next business date",
                    },
                )
            if any(
                str(row["status"]) == "RUNNING"
                for row in self.journal.tasks(self.run_id, phase="activation")
            ):
                return
            pending_ids = []
        if pending_ids:
            if self.stage_row("candidate-collection-registry") is None:
                self.add_stage(
                    phase="activation",
                    capability="candidate-collection-registry",
                    stage_name="registry",
                    required_class="extra",
                    max_attempts=4,
                )
                return
            candidate_registry = self.stage_row("candidate-collection-registry") or {}
            if str(candidate_registry.get("status") or "") not in TERMINAL_TASK_STATES:
                return
            if self.stage_row("candidate-source-plan") is None:
                added = (
                    self.plan_candidate_source_tasks(pending_ids)
                    if str(candidate_registry.get("status") or "") in SUCCESS_TASK_STATES
                    else 0
                )
                self.add_stage(
                    phase="activation",
                    capability="candidate-source-plan",
                    stage_name="noop",
                    args=(
                        "--detail-json",
                        json.dumps(
                            {"pendingActivationIds": pending_ids, "tasksAdded": added},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                    required_class="extra",
                    max_attempts=1,
                )
                return
            candidate_plan = self.stage_row("candidate-source-plan") or {}
            if str(candidate_plan.get("status") or "") not in TERMINAL_TASK_STATES:
                return
            if now >= self.schedule["source_cutoff"]:
                closed = self.journal.degrade_unfinished_phase(
                    self.run_id,
                    "candidate-source",
                    error_code="CANDIDATE_SOURCE_CUTOFF",
                    reason="candidate source retry window closed at the 10:15 activation barrier",
                )
                if closed:
                    self.journal.add_event(
                        self.run_id,
                        "TASK_RETRY_STATE",
                        "candidate-source:CANDIDATE_SOURCE_CUTOFF:DEGRADED",
                        {
                            "runId": self.run_id,
                            "phase": "candidate-source",
                            "source": "candidate-source",
                            "errorCode": "CANDIDATE_SOURCE_CUTOFF",
                            "status": "DEGRADED",
                            "closedTasks": closed,
                            "nextRetry": "next business date",
                        },
                    )
            candidate_sources = self.journal.tasks(self.run_id, phase="candidate-source")
            if any(str(row["status"]) not in TERMINAL_TASK_STATES for row in candidate_sources):
                return
            if self.stage_row("candidate-consolidate") is None:
                self.add_stage(
                    phase="activation",
                    capability="candidate-consolidate",
                    stage_name="consolidate",
                    required_class="extra",
                    max_attempts=4,
                )
                return
            consolidate = self.stage_row("candidate-consolidate") or {}
            if str(consolidate.get("status") or "") not in TERMINAL_TASK_STATES:
                return

        if self.stage_row("candidate-activation") is None:
            if pending_ids:
                self.add_stage(
                    phase="activation",
                    capability="candidate-activation",
                    stage_name="activate",
                    args=(
                        "--run-id", self.run_id,
                        "--business-date", self.day_text,
                    ),
                    required_class="extra",
                    max_attempts=12,
                )
            else:
                self.add_stage(
                    phase="barrier",
                    capability="candidate-activation",
                    stage_name="noop",
                    args=("--detail-json", '{"status":"nothing-pending"}'),
                    required_class="extra",
                    max_attempts=1,
                )
            return
        activation = self.stage_row("candidate-activation")
        if activation:
            self.journal.ensure_max_attempts(str(activation["task_key"]), 12)
        if not self.activation_barrier_open(now):
            return

        activation_result = task_result(activation or {})
        activated_ids = sorted({
            int(value) for value in activation_result.get("activated") or []
            if int(value) > 0
        })
        if activated_ids:
            if self.stage_row("collection-registry-post") is None:
                self.add_stage(
                    phase="barrier",
                    capability="collection-registry-post",
                    stage_name="registry",
                    max_attempts=4,
                )
                return
            if not self.stage_complete("collection-registry-post"):
                return
            self.reconcile_post_activation_dependencies()

        # Fix F: a stream this run just bound has no checkpoint yet, and the
        # daily-accept checkpoint gate reads checkpoints, not bindings.  Refill
        # them here, before the gate; the gate itself is unchanged.  Repair is
        # extra, so an exhausted repair (TERMINAL) hands the verdict back to the
        # untouched gate instead of parking the run.
        if self.stage_row("checkpoint-repair") is None:
            self.add_stage(
                phase="barrier",
                capability="checkpoint-repair",
                stage_name="checkpoint-repair",
                required_class="extra",
                concurrency_group="cdp:9333",
                max_attempts=3,
            )
            return
        # audit P1-8: TERMINAL_TASK_STATES, not a hand-written settled set --
        # repair is `extra`, so a PARKED repair must hand the verdict back to
        # the untouched daily-accept gate exactly like an exhausted one, never
        # hold the barrier until an operator unparks it.
        if str((self.stage_row("checkpoint-repair") or {}).get("status") or "") not in TERMINAL_TASK_STATES:
            return

        if self.stage_row("core-contract-post") is None:
            self.add_stage(
                phase="barrier",
                capability="core-contract-post",
                stage_name="contract",
                args=(
                    "--run-id", self.run_id,
                    "--business-date", self.day_text,
                    "--label", "post-activation",
                ),
                max_attempts=7,
            )
            return
        if not self.stage_complete("core-contract-post"):
            return

        if self.stage_row("daily-accept") is None:
            self.add_stage(
                phase="accept", capability="daily-accept", stage_name="accept",
                args=("--run-id", self.run_id, "--business-date", self.day_text),
                max_attempts=4,
            )
            return
        if not self.stage_complete("daily-accept"):
            return

        if self.stage_row("box") is None:
            accept = task_result(self.stage_row("daily-accept") or {})
            accepted_at = str(accept.get("acceptedAt") or "")
            if not accepted_at:
                raise RuntimeError("daily accept receipt has no acceptedAt for BOX")
            self.add_stage(
                phase="box",
                capability="box",
                stage_name="box",
                args=("--run-id", self.run_id, "--accepted-at", accepted_at),
                max_attempts=4,
            )
            return
        if not self.stage_complete("box"):
            return

        if not self.publication_allowed():
            self.journal.set_run_status(self.run_id, "READY_FOR_CUTOVER")
            return
        if self.stage_row("release") is None:
            self.add_stage(
                phase="publish", capability="release", stage_name="release",
                args=("--run-id", self.run_id, "--business-date", self.day_text),
                max_attempts=6, kind="release",
            )
            return
        if not self.stage_complete("release"):
            return

        accept = task_result(self.stage_row("daily-accept") or {})
        expected_generation = str(accept.get("publicGenerationId") or "")
        expected_content_sha256 = str(accept.get("contentSha256") or "")
        active_count = int(accept.get("activeCount") or 0)
        if (
            not expected_generation
            or len(expected_content_sha256) != 64
            or active_count < 1
        ):
            raise RuntimeError("accepted generation receipt is incomplete")
        all_tasks = self.journal.tasks(self.run_id)
        health = aggregate_source_health(
            [row for row in all_tasks if str(row["source_code"]) != "system"]
        )
        for row in all_tasks:
            if str(row["source_code"]) != "system":
                continue
            if str(row["required_class"]) == "core" or str(row["status"]) in {"COMPLETED", "SKIPPED"}:
                continue
            if str(row["phase"]) not in {"identity", "activation"}:
                continue
            code = f"workflow:{row['capability']}"
            health[code] = {
                "status": str(row["status"]),
                "requiredClass": "extra",
                "tasks": 1,
                "completed": 0,
                "degraded": 1,
                "attempts": int(row.get("attempts") or 0),
                "errors": [str(row.get("last_error_code") or "WORKFLOW_INCOMPLETE")],
            }
        health_path = self.runtime_dir / "source-health.json"
        atomic_json(health_path, health)
        degraded = degraded_source_codes(health)
        if self.stage_row("live-confirm") is None:
            args: list[str] = [
                "--run-id", self.run_id,
                "--business-date", self.day_text,
                "--snapshot", str(RELEASE_SNAPSHOT),
                "--expected-generation", expected_generation,
                "--expected-content-sha256", expected_content_sha256,
                "--active-count", str(active_count),
                "--source-health", str(health_path),
                "--confirm-before", iso(self.schedule["final"]),
            ]
            for code in degraded:
                args.extend(("--degraded-source", code))
            self.add_stage(
                phase="publish", capability="live-confirm", stage_name="live-confirm",
                args=args, max_attempts=6,
            )

    def journal_event(
        self,
        event_type: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
        *,
        alert_scope: str = "",
    ) -> bool:
        """Journal one event and alert unconditionally on lifecycle facts.

        `--notify` still gates the verbose progress stream; the lifecycle
        events in ALWAYS_ALERT_EVENTS are the ones an operator cannot afford to
        miss, so they leave through their own best-effort channel the first time
        they are recorded.

        The alert key is per business date, so notify_hermes' cooldown collapses
        every later event of the same type into the first one.  `alert_scope`
        narrows that key for callers where the second event names a different
        thing to go fix -- audit P0-1: two blocked core source rows are two
        rows, not one repeat.
        """

        inserted = self.journal.add_event(self.run_id, event_type, dedupe_key, payload)
        if inserted and event_type in ALWAYS_ALERT_EVENTS:
            key, level, cooldown = ALWAYS_ALERT_EVENTS[event_type]
            send_alert(
                ":".join(part for part in (key, self.day_text, alert_scope) if part),
                self._alert_text(event_type, payload),
                level=level,
                cooldown_min=cooldown,
            )
        return inserted

    def _alert_text(self, event_type: str, payload: Mapping[str, Any]) -> str:
        detail = " ".join(
            f"{key}={payload[key]}"
            for key in (
                # audit P0-1: "state" rides along so the operator can tell a
                # TERMINAL core row from a PARKED or DEGRADED one on the
                # channel, not only in the journal document.
                "taskKey", "state", "signal", "pid", "exitCode", "errorCode",
                "reason", "generation", "activeCount", "interruptions", "attempts",
            )
            if payload.get(key) not in (None, "")
        )
        return f"CARDZ V2 {event_type} run={self.run_id} {detail}".strip()

    def tick_budget(self) -> dict[str, Any]:
        """What this tick was asked for versus what it may actually use.

        health.json carried no deadline data at all, so a claim window cut by
        the hard-limit clamp was invisible to everything that reads the run.
        """

        return {
            "requestedClaimSeconds": self.claim_window_requested_seconds,
            "claimSeconds": self.claim_window_seconds,
            "claimClamped": bool(self.claim_window_clamped),
            "drainSeconds": round(
                max(0.0, self.drain_deadline_monotonic - self.deadline_monotonic), 1
            ),
            "tickHardLimitSeconds": round(tick_hard_limit_seconds(), 1),
            "externalLimitSeconds": tick_external_limit_seconds(),
            "liftWith": TICK_EXTERNAL_LIMIT_ENV,
        }

    def write_health(
        self,
        *,
        tick_phase: str,
        tick_exit_code: int | None = None,
        tick_ended_at_utc: str | None = None,
    ) -> Path | None:
        try:
            return write_health_document(
                build_health_document(
                    self.journal,
                    self.business_date,
                    tick_phase=tick_phase,
                    tick_exit_code=tick_exit_code,
                    tick_started_at_utc=self.tick_started_at_utc,
                    tick_ended_at_utc=tick_ended_at_utc,
                    run_label=self.run_label,
                    tick_budget=self.tick_budget(),
                    supersede_seq=self.supersede_seq,
                ),
                run_label=self.run_label,
            )
        except Exception:  # noqa: BLE001 - health reporting never fails a tick
            return None

    def write_health_liveness(
        self, tick_phase: str, *, min_interval: float = 0.0
    ) -> None:
        """Prove the tick is alive (audit P2-1a), at most once per min_interval."""

        now = time.monotonic()
        if min_interval > 0.0 and (now - self._health_written_monotonic) < min_interval:
            return
        self._health_written_monotonic = now
        self.write_health(tick_phase=tick_phase)

    def release_own_claims(self, reason: str) -> list[str]:
        released: list[str] = []
        for task_key, claim in list(self.own_claims.items()):
            try:
                # A signalled tick is the external ExecutionTimeLimit (or an
                # operator) ending the tick, not the task failing: the attempt
                # is refunded, the interruption budget is not.
                self._interrupt(task_key, claim, reason=reason, tick_limited=True)
            except Exception:  # noqa: BLE001 - shutdown releases best effort
                continue
            released.append(task_key)
        return released

    def _interrupt(
        self, task_key: str, claim: str, *, reason: str, tick_limited: bool = False
    ) -> str:
        status = self.journal.interrupt_claim(
            task_key, claim, reason=reason, tick_limited=tick_limited
        )
        self.own_claims.pop(task_key, None)
        if status == "PARKED":
            row = self.journal.task(task_key) or {}
            self.journal_event(
                "CORE_TASK_PARKED",
                f"{task_key}:{int(row.get('interruptions') or 0)}",
                {
                    "runId": self.run_id,
                    "taskKey": task_key,
                    "phase": row.get("phase"),
                    "source": row.get("source_code"),
                    "errorCode": "WORKER_PARKED",
                    "reason": reason[-500:],
                    "attempts": int(row.get("attempts") or 0),
                    "maxAttempts": int(row.get("max_attempts") or 0),
                    "interruptions": int(row.get("interruptions") or 0),
                    "nextRetry": "operator unpark",
                },
            )
        return status

    def signal_shutdown(self, signum: int) -> int:
        """Journal, release, and report before the process dies (exit 128+n)."""

        try:
            name = signal.Signals(int(signum)).name
        except (ValueError, TypeError):
            name = str(signum)
        pid = os.getpid()
        try:
            self.journal_event(
                "TICK_SIGNALLED",
                f"{name}:{pid}:{iso()}",
                {
                    "runId": self.run_id,
                    "stage": "orchestrator",
                    "source": "system",
                    "signal": name,
                    "pid": pid,
                    "errorCode": "TICK_SIGNALLED",
                    "exitCode": 128 + int(signum),
                    "nextRetry": "next scheduler tick",
                },
            )
        except Exception:  # noqa: BLE001 - journalling must not block the exit
            pass
        try:
            self.release_own_claims(f"tick received {name}")
        except Exception:  # noqa: BLE001
            pass
        self.write_health(
            tick_phase="signalled",
            tick_exit_code=128 + int(signum),
            tick_ended_at_utc=iso(),
        )
        return 128 + int(signum)

    def tick_crashed(self, error: BaseException, *, exit_code: int) -> int:
        try:
            self.journal_event(
                "TICK_CRASHED",
                f"{type(error).__name__}:{exit_code}:{iso()}",
                {
                    "runId": self.run_id,
                    "stage": "orchestrator",
                    "source": "system",
                    "errorCode": type(error).__name__,
                    "error": str(error)[-2000:],
                    "exitCode": exit_code,
                    "nextRetry": "next scheduler tick",
                },
            )
        except Exception:  # noqa: BLE001
            pass
        self.write_health(
            tick_phase="crashed",
            tick_exit_code=exit_code,
            tick_ended_at_utc=iso(),
        )
        return exit_code

    def _task_paths(self, row: Mapping[str, Any]) -> tuple[Path, Path]:
        digest = sha256(str(row["task_key"]))[:16]
        attempt = int(row.get("attempts") or 0)
        return (
            self.log_dir / f"{digest}-attempt-{attempt}.log",
            self.receipt_dir / f"{digest}-attempt-{attempt}.json",
        )

    def claim_still_live(self, row: Mapping[str, Any]) -> bool:
        """Is this exact claim still ours and still heartbeating?

        Drain runs past the tick's own budget, so it may only be granted to work
        whose durable claim is still valid.  A row that was reclaimed, retired,
        or whose heartbeat writer died must be interrupted on the original
        deadline exactly as before.

        Memoised for DRAIN_LIVENESS_CACHE_SECONDS (review fix): every worker
        poll of every in-flight worker asks this once claiming has closed, and
        each miss opens a fresh sqlite connection.
        """

        cache_key = f"{row['task_key']}:{row['lease_token']}"
        ttl = float(DRAIN_LIVENESS_CACHE_SECONDS)
        asked_at = time.monotonic()
        if ttl > 0.0:
            with self._live_claim_lock:
                cached = self._live_claim_cache.get(cache_key)
            if cached is not None and cached[0] > asked_at:
                return cached[1]
        answer = self._read_claim_liveness(row)
        if ttl > 0.0:
            with self._live_claim_lock:
                self._live_claim_cache[cache_key] = (asked_at + ttl, answer)
        return answer

    def _read_claim_liveness(self, row: Mapping[str, Any]) -> bool:
        try:
            current = self.journal.task(str(row["task_key"]))
        except Exception:  # noqa: BLE001 - a damaged journal never earns drain
            return False
        if not current or str(current.get("status") or "") != "RUNNING":
            return False
        if str(current.get("lease_token") or "") != str(row["lease_token"]):
            return False
        raw = current.get("heartbeat_at")
        if not raw:
            return False
        try:
            beat = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return False
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        age = (utc_now() - beat.astimezone(timezone.utc)).total_seconds()
        return age <= DRAIN_HEARTBEAT_STALE_SECONDS

    def drain_deadline_for(self, row: Mapping[str, Any] | None) -> float:
        """The tick's own bound on an already-claimed worker.

        Operator finding 2026-08-24: attempt 1 and attempt 2 of
        gemrate:contract-repair:pop:all were both cut at a tick deadline after
        ~20 and ~48 minutes and both restarted the fetch from zero, so a worker
        with an honest 55-60 min duration could never complete and would burn
        the interruption budget to PARKED.  Once claiming closes, live work
        keeps running to the drain ceiling instead.
        """

        if row is None or self.drain_deadline_monotonic <= self.deadline_monotonic:
            return self.deadline_monotonic
        if not self.claiming_closed():
            # Nothing to decide yet, and no journal read on the hot path.
            return self.deadline_monotonic
        if not self.claim_still_live(row):
            return self.deadline_monotonic
        return self.drain_deadline_monotonic

    def _work_deadline_bound(
        self, row: Mapping[str, Any] | None = None
    ) -> tuple[float, str]:
        """The earliest deadline binding this worker, and WHICH one it is.

        Review fix 2026-08-24: only the tick's own budget ("tick") refunds the
        attempt.  The 10:15 candidate cutoff and the 17:00 final are business
        boundaries: a worker they cut really did fail to deliver, and refunding
        it would let the task re-claim into an already-past work deadline, spawn
        a worker that is interrupted immediately, and churn like that for the
        whole interruption budget instead of settling at max_attempts.
        """

        until_final = max(0.0, (self.schedule["final"] - utc_now()).total_seconds())
        deadlines = [
            (self.drain_deadline_for(row), "tick"),
            (time.monotonic() + until_final, "final"),
        ]
        phase = "" if row is None else str(row.get("phase") or "")
        optional_source_before_cutoff = bool(
            row is not None
            and phase == "source"
            and str(row.get("required_class") or "") != "core"
            and utc_now() < self.schedule["source_cutoff"]
        )
        if phase in {"identity", "candidate-source", "activation"} or optional_source_before_cutoff:
            # Candidate identity is a frozen 10:15 snapshot.  Do not let a
            # worker started just before the boundary write a late candidate
            # and hold activation for the rest of the day.  The same boundary
            # interrupts an optional quote source that began before 10:15; its
            # unfinished claim becomes independently retryable immediately
            # after the downstream barrier opens.
            until_cutoff = max(
                0.0, (self.schedule["source_cutoff"] - utc_now()).total_seconds()
            )
            deadlines.append((time.monotonic() + until_cutoff, "cutoff"))
        return min(deadlines, key=lambda bound: bound[0])

    def _work_deadline_monotonic(self, row: Mapping[str, Any] | None = None) -> float:
        return self._work_deadline_bound(row)[0]

    def _heartbeat(self, row: Mapping[str, Any], checkpoint: Any = None, worker_pid: int | None = None) -> None:
        details = checkpoint if isinstance(checkpoint, Mapping) else {}
        self.journal.heartbeat(
            str(row["task_key"]),
            str(row["lease_token"]),
            lease_seconds=TASK_LEASE_SECONDS,
            worker_pid=worker_pid,
            process_started_at=str(details.get("processStartedAt") or "") or None,
            command_sha256=str(details.get("commandSha256") or "") or None,
            checkpoint=checkpoint,
        )

    def _run_stage_process(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = json.loads(str(row["payload_json"]))
        log_path, receipt_path = self._task_paths(row)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        kind = str(payload.get("kind") or "stage")
        stage_name = str(payload.get("stageName") or "")
        stage_args = [str(value) for value in payload.get("stageArgs") or []]
        if kind == "release":
            accept = task_result(self.stage_row("daily-accept") or {})
            expected_generation = str(accept.get("publicGenerationId") or "")
            if not expected_generation:
                raise RuntimeError("release task has no accepted V2 generation")
            command = [
                "bash", str(ROOT / "scripts" / "daily_public_release.sh"),
                f"--v2-run-id={self.run_id}",
                f"--v2-business-date={self.day_text}",
                f"--v2-expected-generation={expected_generation}",
            ]
        else:
            command = [
                sys.executable, "-X", "utf8", "-u", str(STAGE_SCRIPT),
                "--output", str(receipt_path), stage_name, *stage_args,
            ]
        command_sha = sha256(command)
        work_deadline = self._work_deadline_monotonic(row)
        env = os.environ.copy()
        env.update({
            # Wall-clock twin of the monotonic deadline this parent enforces,
            # so a stage that would go out to the network can decide to do less
            # instead of being killed mid-fetch at the cutoff.
            WORK_DEADLINE_ENV: f"{time.time() + max(0.0, work_deadline - time.monotonic()):.3f}",
            "CARDZ_DAILY_CHAIN_V2": "1",
            "CARDZ_V2_RUN_ID": self.run_id,
            "CARDZ_V2_BUSINESS_DATE": self.day_text,
            # business_window_utc opens the coverage window at the earlier of
            # JST midnight and this run's creation, so an early manual window
            # counts its own observations.
            RUN_STARTED_AT_ENV: export_run_started_at(self.journal.run(self.run_id)),
            "CARDZ_V2_TASK_KEY": str(row["task_key"]),
            "CARDZ_V2_CLAIM_TOKEN": str(row["lease_token"]),
            "CARDZ_V2_STATE_DB": str(self.journal.path),
            # Wall clock a stage must stop by, with TICK_RESERVE_SECONDS already
            # subtracted so the reserve keeps one definition.  A stage that
            # opens the network reads this instead of being killed mid-fetch.
            "CARDZ_V2_STAGE_DEADLINE_EPOCH": repr(
                time.time() + (work_deadline - time.monotonic()) - TICK_RESERVE_SECONDS
            ),
        })
        with log_path.open("ab", buffering=0) as log:
            proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started = self._process_started_marker(proc.pid)
            self._heartbeat(
                row,
                {
                    "state": "stage_running", "pid": proc.pid,
                    "processStartedAt": started, "commandSha256": command_sha,
                },
                proc.pid,
            )
            while proc.poll() is None:
                # Re-read, never freeze: the drain bound is decided against the
                # live claim, so a healthy worker survives the tick deadline and
                # a stale one is still cut on it (operator finding 2026-08-24).
                if time.monotonic() >= self._work_deadline_monotonic(row):
                    # Per-stage grace from the same table the source workers use
                    # (R3); the drain arithmetic reserves its worst case.
                    terminate_worker_group(
                        proc.pid,
                        grace_seconds=worker_shutdown_grace_seconds(
                            stage_name or kind
                        ),
                    )
                    raise WorkerInterrupted(
                        f"tick deadline interrupted stage pid={proc.pid} capability={row['capability']}"
                    )
                time.sleep(2)
                self._heartbeat(
                    row,
                    {
                        "state": "stage_running", "pid": proc.pid,
                        "processStartedAt": started, "commandSha256": command_sha,
                    },
                    proc.pid,
                )
            exit_code = int(proc.returncode or 0)
        if kind == "release":
            if exit_code != 0:
                tail = self._tail(log_path)
                # audit P2-15: exit 75 is the release script saying another
                # publisher holds the flock.  Name it so the classifier reads
                # contention instead of a broken release.
                if exit_code == PUBLISH_LOCK_EXIT_CODE:
                    raise RuntimeError(
                        f"release exit={exit_code}: {PUBLISH_LOCK_MARKER} {tail}"
                    )
                raise RuntimeError(f"release exit={exit_code}: {tail}")
            accept = task_result(self.stage_row("daily-accept") or {})
            manifest_path = publication_manifest_path(self.run_id)
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise RuntimeError(f"immutable publication manifest missing: {error}") from error
            if (
                str(manifest.get("runId") or "") != self.run_id
                or str(manifest.get("businessDate") or "") != self.day_text
                or str(manifest.get("generation") or "") != str(accept.get("publicGenerationId") or "")
            ):
                raise RuntimeError("immutable publication manifest does not match accepted run")
            return {
                "stage": "release",
                "status": "completed",
                "runId": self.run_id,
                "businessDate": self.day_text,
                "publicGenerationId": accept.get("publicGenerationId"),
                "manifestPath": str(manifest_path),
                "manifest": manifest,
                "logPath": str(log_path),
            }
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeError(f"stage receipt missing: {error}; {self._tail(log_path)}") from error
        if exit_code != 0 or str(receipt.get("status") or "") != "completed":
            raise RuntimeError(str(receipt.get("error") or f"stage exit={exit_code}"))
        return receipt

    @staticmethod
    def _process_started_marker(pid: int) -> str | None:
        try:
            return Path(f"/proc/{pid}/stat").read_text(encoding="ascii").split()[21]
        except (OSError, IndexError):
            return None

    @staticmethod
    def _tail(path: Path, limit: int = 4000) -> str:
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""

    def execute_claim(self, row: Mapping[str, Any]) -> None:
        task_key = str(row["task_key"])
        claim = str(row["lease_token"])
        self.own_claims[task_key] = claim
        try:
            payload_preview = json.loads(str(row["payload_json"]))
            if str(payload_preview.get("kind") or "") == "source":
                payload = payload_preview
                task = SourceTask(
                    run_id=self.run_id,
                    business_date=self.day_text,
                    source_code=str(row["source_code"]),
                    capability=str(row["capability"]),
                    variant_id=payload.get("variantId"),
                    external_id=payload.get("externalId"),
                    shard=str(payload.get("shard") or "all"),
                    input_revision=str(row["input_revision"]),
                    checkpoint=(
                        json.loads(str(row.get("checkpoint_json") or "{}"))
                        if row.get("checkpoint_json") else {}
                    ),
                )
                adapter = self.registry.get(task.source_code)
                log_path, receipt_path = self._task_paths(row)
                execution = adapter.execute(
                    task,
                    {
                        "state_db": self.journal.path,
                        "task_key": task_key,
                        "claim_token": claim,
                        "log_path": log_path,
                        "receipt_path": receipt_path,
                        # A callable, not a frozen float: tick drain can extend
                        # this after the worker started (operator finding
                        # 2026-08-24).
                        "deadline_monotonic": lambda: self._work_deadline_monotonic(row),
                    },
                    lambda **kwargs: self._heartbeat(
                        row, kwargs.get("checkpoint"), kwargs.get("worker_pid")
                    ),
                )
                result = adapter.ingest(task, execution, {})
                body = asdict(result)
                self.journal.finish_success(
                    task_key, claim, body, degraded=result.status != "completed"
                )
            else:
                result = self._run_stage_process(row)
                self.journal.finish_success(task_key, claim, result)
        except WorkerInterrupted as error:
            # WorkerInterrupted is raised wherever the tick cut the worker, and
            # that is three different bounds.  Only the tick BUDGET (claim
            # deadline / drain / external limit) refunds the attempt, so a
            # resumable 55-90 min task is not parked by the tick clock; the
            # 10:15 candidate cutoff and the 17:00 final are business boundaries
            # and still burn one (review fix 2026-08-24).  The interruption
            # budget parks the task either way.
            self._interrupt(
                task_key,
                claim,
                reason=str(error),
                tick_limited=self._work_deadline_bound(row)[1] == "tick",
            )
        except Exception as error:  # noqa: BLE001 - classify then checkpoint
            text = f"{type(error).__name__}:{error}"
            decision = classify_error(
                text, stage="publish" if str(row["phase"]) == "publish" else "source"
            )
            status = self.journal.finish_failure(
                task_key,
                claim,
                decision=decision,
                error_text=text,
                retry_not_after=self.publish_retry_not_after(row),
            )
            self.journal.add_event(
                self.run_id,
                "TASK_ERROR",
                f"{task_key}:{decision.error_code}",
                {
                    "runId": self.run_id,
                    "taskKey": task_key,
                    "phase": row["phase"],
                    "source": row["source_code"],
                    "errorCode": decision.error_code,
                    "status": status,
                    "attempt": int(row["attempts"]),
                    "nextRetry": (self.journal.task(task_key) or {}).get("next_retry_at"),
                    "logPath": str(self._task_paths(row)[0]),
                },
            )
            if int(row["attempts"]) > 1 or status == "TERMINAL":
                self.journal.add_event(
                    self.run_id,
                    "TASK_RETRY_STATE",
                    f"{task_key}:{decision.error_code}:{status}:{int(row['attempts'])}",
                    {
                        "runId": self.run_id,
                        "taskKey": task_key,
                        "phase": row["phase"],
                        "source": row["source_code"],
                        "errorCode": decision.error_code,
                        "status": status,
                        "attempt": int(row["attempts"]),
                        "nextRetry": (self.journal.task(task_key) or {}).get("next_retry_at"),
                        "logPath": str(self._task_paths(row)[0]),
                    },
                )
            if str(row["phase"]) == "publish" and status == "TERMINAL":
                # audit P1-2: this is the verdict an operator has to see now.
                # journal_event carries it into ALWAYS_ALERT_EVENTS; the
                # TASK_ERROR row above is add_event only and alerts nobody.
                self.journal_event(
                    "PUBLISH_TERMINAL",
                    f"{task_key}:{decision.error_code}:{int(row['attempts'])}",
                    {
                        "runId": self.run_id,
                        "taskKey": task_key,
                        "phase": row["phase"],
                        "errorCode": decision.error_code,
                        "attempts": int(row["attempts"]),
                        "logPath": str(self._task_paths(row)[0]),
                    },
                )
            if decision.error_code == "MYSQL_UNAVAILABLE" and status == "RETRY":
                self.recover_mysql()
        finally:
            self.own_claims.pop(task_key, None)

    def recover_mysql(self) -> None:
        with MYSQL_RECOVERY_LOCK:
            command = [
                DOCKER_CLI, "compose", "-f",
                MYSQL_COMPOSE_WINDOWS,
                "up", "-d", "db",
            ]
            recovery_env = os.environ.copy()
            try:
                for raw in MYSQL_COMPOSE_ENV.read_text(encoding="utf-8").splitlines():
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.strip()
                    if key.startswith("CARDZ_DB_"):
                        recovery_env[key] = value.strip()
            except OSError:
                return
            try:
                proc = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    check=False,
                    env=recovery_env,
                )
            except (OSError, subprocess.TimeoutExpired):
                return
            if proc.returncode != 0:
                return
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                try:
                    probe = subprocess.run(
                        [
                            DOCKER_CLI, "inspect", "--format",
                            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                            "cardz-market-cap-db-1",
                        ],
                        capture_output=True,
                        text=True,
                        timeout=10,
                        check=False,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    return
                if probe.returncode == 0 and probe.stdout.strip() in {"healthy", "running"}:
                    return
                time.sleep(2)

    def recover_expired(self) -> None:
        for row in self.journal.tasks(self.run_id):
            status = str(row.get("status") or "")
            if status not in {"RETRY", "TERMINAL"}:
                continue
            if str(row.get("capability") or "") == "candidate-activation":
                self.journal.ensure_max_attempts(str(row["task_key"]), 12)
            decision = classify_error(
                str(row.get("last_error") or ""),
                stage="publish" if str(row.get("phase") or "") == "publish" else "source",
            )
            not_after = self.publish_retry_not_after(row)
            if status == "RETRY":
                self.journal.reclassify_retry(
                    str(row["task_key"]), decision=decision, retry_not_after=not_after
                )
                continue
            if self.journal.reopen_retryable_terminal(
                str(row["task_key"]), decision=decision, retry_not_after=not_after
            ):
                self.journal.add_event(
                    self.run_id,
                    "TASK_RETRY_STATE",
                    f"{row['task_key']}:{decision.error_code}:REOPENED",
                    {
                        "runId": self.run_id,
                        "taskKey": row["task_key"],
                        "phase": row["phase"],
                        "source": row["source_code"],
                        "errorCode": decision.error_code,
                        "status": "RETRY",
                        "reason": "reopened under per-error retry accounting",
                    },
                )
        grace = adopt_grace_seconds()
        now = utc_now()
        for row in self.journal.expired_attempts():
            pid = int(row.get("worker_pid") or 0)
            alive = pid > 1 and self._pid_belongs_to_attempt(pid, row)
            disposition = recovery_disposition(
                row, now=now, alive=alive, grace_seconds=grace
            )
            if disposition == "adopt":
                # The worker is this attempt's own live process and its lease
                # only just lapsed.  Adopt the claim instead of restarting it.
                self.journal_event(
                    "TASK_ADOPTED",
                    f"{row['task_key']}:{row['claim_token']}",
                    {
                        "runId": self.run_id,
                        "taskKey": row["task_key"],
                        "phase": row["phase"],
                        "source": row["source_code"],
                        "pid": pid,
                        "errorCode": "LEASE_LAPSED_WORKER_ALIVE",
                        "leaseExpiresAt": row.get("lease_expires_at"),
                        "graceSeconds": grace,
                        "nextRetry": "adopted; worker keeps running",
                    },
                )
                continue
            if disposition == "terminate":
                terminate_worker_group(
                    pid,
                    grace_seconds=(
                        60.0 if str(row.get("capability") or "") == "candidate-activation"
                        else 5.0
                    ),
                )
            try:
                self._interrupt(
                    str(row["task_key"]),
                    str(row["claim_token"]),
                    reason="heartbeat lease expired; exact attempt reclaimed",
                )
            except Exception:
                continue

    def _pid_belongs_to_attempt(self, pid: int, row: Mapping[str, Any]) -> bool:
        marker = self._process_started_marker(pid)
        expected_marker = str(row.get("process_started_at") or "")
        if expected_marker and marker != expected_marker:
            return False
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", "replace"
            )
            environ = Path(f"/proc/{pid}/environ").read_bytes()
        except OSError:
            return False
        task_key = str(row["task_key"])
        claim = str(row["claim_token"])
        return (
            ("daily_chain_v2_worker.py" in cmdline and task_key in cmdline and claim in cmdline)
            or (
                "daily_chain_v2_stage.py" in cmdline
                and f"CARDZ_V2_TASK_KEY={task_key}".encode() in environ
                and f"CARDZ_V2_CLAIM_TOKEN={claim}".encode() in environ
            )
            or (
                "daily_public_release.sh" in cmdline
                and f"CARDZ_V2_TASK_KEY={task_key}".encode() in environ
                and f"CARDZ_V2_CLAIM_TOKEN={claim}".encode() in environ
            )
        )

    def eligible_phases(self) -> tuple[str, ...]:
        run = self.journal.run(self.run_id) or {}
        published = bool(run.get("publication_status"))
        downstream_started = any(
            self.journal.tasks(self.run_id, phase=phase)
            for phase in ("identity", "activation", "accept", "box", "publish")
        )
        if published:
            return ("source",)
        if not self.activation_barrier_open(utc_now()):
            return (
                "infra", "source", "barrier", "identity",
                "candidate-source", "activation",
            )
        critical = any(
            row["status"] not in TERMINAL_TASK_STATES
            for row in self.journal.tasks(self.run_id)
            if str(row["capability"]) in {
                "candidate-collection-registry", "candidate-consolidate",
                "candidate-activation", "daily-accept",
            }
        )
        if downstream_started:
            phases = (
                "infra", "barrier", "identity", "candidate-source",
                "activation", "accept", "box", "publish",
            )
            return phases if critical else ("source", *phases)
        return ("infra", "source", "barrier")

    def next_retry_wait(self, now: datetime) -> float | None:
        waits: list[float] = []
        eligible = set(self.eligible_phases())
        for row in self.journal.tasks(self.run_id):
            if str(row["phase"]) not in eligible:
                continue
            if str(row["status"]) not in {"RETRY", "INTERRUPTED"}:
                continue
            retry_at = row.get("next_retry_at")
            if not retry_at:
                waits.append(0.0)
                continue
            due = datetime.fromisoformat(str(retry_at).replace("Z", "+00:00"))
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            waits.append(max(0.0, (due.astimezone(timezone.utc) - now).total_seconds()))
        return min(waits) if waits else None

    def report_drain(self, in_flight: int) -> None:
        """Say once, per tick, that the tick is past its budget and why.

        Without this the only observable difference between "drained to a clean
        finish" and "the external Task Scheduler limit killed us mid-worker" is
        a missing receipt.  `truncatedByExternalLimit` names the bound the
        operator would have to raise in scripts/install_cardz_daily_v2_task.ps1.

        Review fix: a drain is the HEALTHY path, so the payload carries no
        `errorCode` and _event_message() renders it green.  A red alert here
        would be crying wolf on the one surface audit P2-1 exists to make honest.
        """

        if self._drain_reported or in_flight <= 0:
            return
        self._drain_reported = True
        hard_limit = tick_hard_limit_seconds()
        by_drain = self.deadline_monotonic + TICK_DRAIN_CEILING_SECONDS
        by_scheduler = self.tick_started_monotonic + hard_limit
        self.journal.add_event(
            self.run_id,
            "TICK_DRAINING",
            f"{self.run_id}:{self.tick_started_at_utc or iso()}",
            {
                "runId": self.run_id,
                "stage": "orchestrator",
                "source": "system",
                "inFlight": int(in_flight),
                "drainSecondsGranted": round(
                    max(0.0, self.drain_deadline_monotonic - self.deadline_monotonic), 1
                ),
                "drainCeilingSeconds": TICK_DRAIN_CEILING_SECONDS,
                "externalLimitSeconds": tick_external_limit_seconds(),
                "tickHardLimitSeconds": hard_limit,
                "truncatedByExternalLimit": bool(by_scheduler < by_drain),
                "nextRetry": "in-flight worker keeps running to the drain ceiling",
            },
        )

    def claiming_closed(self) -> bool:
        """Past this instant the tick may drain live work but claims nothing."""

        return time.monotonic() >= self.deadline_monotonic - TICK_RESERVE_SECONDS

    def may_claim(self) -> bool:
        """Both time boundaries that must hold before a row may be claimed.

        Review fix (BLOCKER): run_tick checks `now >= self.schedule['final']`
        and breaks BEFORE it ever reaches plan()/execute_ready(), so the batch
        barrier could never claim past the run's final deadline.  The pump
        refills on its own clock inside one execute_ready() call, so it has to
        carry that boundary itself.  Past `final`, _work_deadline_monotonic()
        already evaluates to now, so every such claim spawns a real worker only
        to interrupt it on its first poll -- one interruption each, and six of
        them (DEFAULT_MAX_INTERRUPTIONS) park a core task inside a single call.
        """

        return not self.claiming_closed() and utc_now() < self.schedule["final"]

    def _claim_batch(self, limit: int) -> list[dict[str, Any]]:
        # claim_ready() already refuses a row whose concurrency group has
        # max_concurrency RUNNING rows, and an in-flight claim IS RUNNING, so
        # refilling here can never double-count a group (audit P1-1).
        if int(limit) <= 0 or not self.may_claim():
            return []
        return self.journal.claim_ready(
            self.run_id,
            phases=self.eligible_phases(),
            lease_seconds=TASK_LEASE_SECONDS,
            limit=int(limit),
        )

    def execute_ready(self) -> int:
        """Claim, run, and re-claim every time a slot frees (audit P1-1).

        The old shape claimed one claim_ready() snapshot and joined the whole
        batch before run_tick could plan again, so a task that failed in 18 s
        waited for the slowest sibling in its batch: 2026-08-24 gemrate shard 3
        died at +0.3m, was due at +1.3m, and only restarted at +15.3m.  Every
        wait slice re-claims; every completion also re-plans, so a freshly
        failed sibling becomes claimable on its own backoff clock.
        """

        rows = self._claim_batch(EXECUTE_MAX_IN_FLIGHT)
        if not rows:
            return 0
        started = len(rows)
        last_idle_claim = time.monotonic()
        with ThreadPoolExecutor(max_workers=EXECUTE_MAX_IN_FLIGHT) as pool:
            pending: set[Future[None]] = {
                pool.submit(self.execute_claim, row) for row in rows
            }
            while pending:
                done, pending = wait(
                    pending,
                    timeout=EXECUTE_POLL_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                for future in done:
                    future.result()
                # audit P2-1(a): the longest stretch of a run is exactly this
                # loop; without a heartbeat here the watchdog's 25-minute
                # staleness rule has no call site while a tick is working.
                self.write_health_liveness(
                    "draining" if self.claiming_closed() else "working",
                    min_interval=EXECUTE_HEALTH_INTERVAL_SECONDS,
                )
                if self.claiming_closed():
                    # Drain: keep waiting for live work, claim nothing new.
                    self.report_drain(len(pending))
                    continue
                if not done:
                    # Review fix: a bare timeout wake means nothing finished, so
                    # no slot freed and no barrier opened.  Only a retry backoff
                    # can have expired, and that is worth at most one claim per
                    # EXECUTE_REFILL_IDLE_INTERVAL_SECONDS instead of one every
                    # poll.  Completions below are still refilled immediately.
                    if (
                        time.monotonic() - last_idle_claim
                        < EXECUTE_REFILL_IDLE_INTERVAL_SECONDS
                    ):
                        continue
                    last_idle_claim = time.monotonic()
                if not self.may_claim():
                    # Past the run's final deadline: drain what is running, and
                    # do not re-plan work that can no longer be claimed.
                    continue
                if done:
                    # A slot freed.  Re-plan before refilling so a barrier that
                    # just opened, or a task that just failed with a 60 s
                    # backoff, is visible to the very next claim.
                    self.plan(utc_now())
                refill = self._claim_batch(EXECUTE_MAX_IN_FLIGHT - len(pending))
                pending |= {pool.submit(self.execute_claim, row) for row in refill}
                started += len(refill)
        return started

    def finalise_live(self) -> None:
        run = self.journal.run(self.run_id) or {}
        if run.get("publication_status"):
            return
        live = self.stage_row("live-confirm")
        if not live:
            return
        result: dict[str, Any] = {}
        if live["status"] in SUCCESS_TASK_STATES:
            result = task_result(live)
        elif int(live.get("attempts") or 0) > 0:
            # MySQL COMMIT can win the race against a killed stage receipt.
            # Recover that already-confirmed, pre-deadline event instead of
            # manufacturing a second event or declaring a false final failure.
            accept = task_result(self.stage_row("daily-accept") or {})
            expected_generation = str(accept.get("publicGenerationId") or "")
            expected_content_sha256 = str(accept.get("contentSha256") or "")
            expected_active_count = int(accept.get("activeCount") or 0)
            if expected_generation:
                try:
                    from daily_chain_v2_db import recover_live_event

                    recovered = recover_live_event(
                        business_date=self.day_text,
                        run_id=self.run_id,
                        expected_generation=expected_generation,
                        expected_content_sha256=expected_content_sha256 or None,
                        expected_active_count=expected_active_count or None,
                    )
                except Exception:
                    recovered = None
                if recovered is not None:
                    result = {"stage": "live-confirm", **recovered}
        event = result.get("event")
        if not isinstance(event, Mapping):
            return
        occurred_at = datetime.fromisoformat(str(event.get("occurredAt") or "").replace("Z", "+00:00"))
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=timezone.utc)
        if occurred_at.astimezone(timezone.utc) >= self.schedule["final"]:
            return
        source_health = event.get("sourceHealth") or {}
        degraded = list(event.get("degradedSources") or [])
        status = "PUBLISHED_DEGRADED" if degraded else "PUBLISHED"
        inserted = self.journal.mark_publication(
            self.run_id,
            status=status,
            generation_id=str(event["generationId"]),
            generated_at=str(event["generatedAt"]),
            content_sha256=str(event["contentSha256"]),
            active_count=int(event["activeCount"]),
            degraded_sources=degraded,
        )
        if inserted:
            self.journal_event(
                "live.confirmed",
                self.day_text,
                {
                    "eventId": int(result["eventId"]),
                    "generation": event["generationId"],
                    "generatedAt": event["generatedAt"],
                    "activeCount": event["activeCount"],
                    "degraded": bool(degraded),
                    "degradedSources": degraded,
                    "sourceHealth": source_health,
                    "liveUrl": event["liveUrl"],
                },
            )

    def publish_retry_not_after(self, row: Mapping[str, Any]) -> datetime | None:
        """The last instant this publish retry may start and still finish its leg.

        R4 2026-08-24: `final` is the business date's 17:00 JST cutoff and
        lifecycle_events() stamps FAILED_FINAL there unconditionally, so a
        publish backoff that lands after `final - <leg>` is an attempt the run
        owns but can never spend.  Capping the ladder is the honest repair; the
        cutoff itself stays exactly where it is.

        The leg is per CAPABILITY, not per phase: `release` bakes, pushes and
        polls the live health endpoint for ten minutes, while `live-confirm`
        makes one health request and inserts one row and stays valid right up
        to `--confirm-before final`.  Charging live-confirm the release leg
        would turn a retryable FE-deploy-lag failure in the last stretch into a
        TERMINAL with attempts unspent -- the same bug in the other direction.
        """

        if str(row.get("phase") or "") != "publish":
            return None
        leg = publish_leg_seconds(row.get("capability"))
        return self.schedule["final"] - timedelta(seconds=leg)

    def lifecycle_events(self, now: datetime) -> None:
        run = self.journal.run(self.run_id) or {}
        if run.get("publication_status"):
            return
        if now >= self.schedule["sla"]:
            tasks = self.journal.tasks(self.run_id)
            current = max(tasks, key=lambda row: row["updated_at"]) if tasks else {}
            active_count = 0
            for capability in ("daily-accept", "core-contract-post", "core-contract-pre"):
                result = task_result(self.stage_row(capability) or {})
                active_count = int(
                    result.get("activeCount")
                    or (result.get("contract") or {}).get("activeCount")
                    or 0
                )
                if active_count:
                    break
            self.journal.add_event(
                self.run_id,
                "SLA_MISSED",
                self.day_text,
                {
                    "runId": self.run_id,
                    "stage": current.get("phase"),
                    "source": current.get("source_code"),
                    "taskKey": current.get("task_key"),
                    "errorCode": current.get("last_error_code"),
                    "nextRetry": current.get("next_retry_at"),
                    "activeCount": active_count,
                    "taskCounts": self.journal.summary(self.run_id)["taskCounts"],
                    "logPath": (
                        str(self._task_paths(current)[0]) if current.get("task_key") else None
                    ),
                },
            )
        if now >= self.schedule["final"]:
            self.journal.set_run_status(self.run_id, "FAILED_FINAL")
            self.journal_event(
                "FAILED_FINAL",
                self.day_text,
                {"runId": self.run_id, "taskCounts": self.journal.summary(self.run_id)["taskCounts"]},
            )

    def deliver_events(self) -> None:
        if not self.notify:
            return
        import notify_hermes

        for event in self.journal.pending_events(self.run_id):
            payload = json.loads(str(event["payload_json"]))
            event_type = str(event["event_type"])
            topic = "22631" if event_type == "live.confirmed" else "2925"
            if event_type == "live.confirmed" and payload.get("eventId"):
                from daily_chain_v2_db import ensure_delivery

                try:
                    ensure_delivery(int(payload["eventId"]), "telegram:22631")
                except Exception as error:
                    self.journal.add_event(
                        self.run_id,
                        "DELIVERY_LEDGER_ERROR",
                        f"telegram:22631:{int(payload['eventId'])}",
                        {
                            "runId": self.run_id,
                            "stage": "publication-delivery",
                            "source": "telegram:22631",
                            "errorCode": type(error).__name__,
                            "nextRetry": "operator ledger repair",
                        },
                    )
                    continue
            message = self._event_message(event_type, payload)
            previous = os.environ.get("CARDZ_TG_THREAD_ID")
            os.environ["CARDZ_TG_THREAD_ID"] = topic
            try:
                try:
                    delivered = bool(notify_hermes.send_message(message))
                except Exception:  # notification transport must not stop market publication
                    delivered = False
            finally:
                if previous is None:
                    os.environ.pop("CARDZ_TG_THREAD_ID", None)
                else:
                    os.environ["CARDZ_TG_THREAD_ID"] = previous
            if delivered:
                self.journal.mark_event_delivered(str(event["event_key"]))
                if event_type == IDENTITY_BRIEF_EVENT and payload.get("seen"):
                    # Only a delivered brief may mark its rows as shown.  A
                    # dropped send that stamped would silence those rows for
                    # the next 14 days, and a stamp before delivery would make
                    # the stage's own retry render a hollow second brief.
                    import identity_brief

                    identity_brief.save_seen(payload["seen"])
                if event_type == "live.confirmed" and payload.get("eventId"):
                    from daily_chain_v2_db import mark_delivery

                    try:
                        mark_delivery(
                            int(payload["eventId"]),
                            "telegram:22631",
                            delivered=True,
                            receipt={"topic": 22631, "deliveredAt": iso()},
                        )
                    except Exception as error:  # Telegram already received it; never duplicate-send
                        self.journal.add_event(
                            self.run_id,
                            "DELIVERY_LEDGER_ERROR",
                            f"telegram:22631:{int(payload['eventId'])}",
                            {
                                "runId": self.run_id,
                                "stage": "publication-delivery",
                                "source": "telegram:22631",
                                "errorCode": type(error).__name__,
                                "nextRetry": "operator ledger repair",
                            },
                        )

    @staticmethod
    def _event_message(event_type: str, payload: Mapping[str, Any]) -> str:
        if event_type == IDENTITY_BRIEF_EVENT:
            # Verbatim: the brief is already rendered Telegram HTML with real
            # <a href> links.  Never route it through _alert_text/html.escape
            # (that is what ALWAYS_ALERT_EVENTS does), or every link in the
            # morning report prints as literal markup.
            return str(payload.get("message") or "")
        if event_type == "RUN_STARTED":
            return (
                "🔵 <b>CARDZ V2 started</b>\n"
                f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
                f"businessDate=<code>{html.escape(str(payload.get('businessDate') or '-'))}</code> "
                f"origin=<code>{html.escape(str(payload.get('origin') or '-'))}</code>"
            )
        if event_type == "live.confirmed":
            source_health = payload.get("sourceHealth") or {}
            source_summary = ", ".join(
                f"{code}:{(row or {}).get('status', '?')}"
                for code, row in sorted(source_health.items())
            )
            return (
                "🚀 <b>CARDZ live.confirmed</b>\n"
                f"generation=<code>{html.escape(str(payload.get('generation') or '-'))}</code>\n"
                f"generatedAt=<code>{html.escape(str(payload.get('generatedAt') or '-'))}</code>\n"
                f"active={int(payload.get('activeCount') or 0)} "
                f"degraded={bool(payload.get('degraded'))}\n"
                f"sources=<code>{html.escape(source_summary or '-')}</code>\n"
                f"{html.escape(str(payload.get('liveUrl') or ''))}"
            )
        if event_type == "DB_LONG_SESSIONS_OBSERVED":
            return (
                "🟠 <b>CARDZ V2 long DB sessions</b>\n"
                f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
                f"sessions={int(payload.get('sessionCount') or 0)} "
                f"maxMinutes={int(payload.get('maxMinutes') or 0)}\n"
                "<i>reported only, nothing was killed</i>"
            )
        if event_type == "TICK_DRAINING":
            # Review fix: draining is the healthy path this package added.  The
            # red fallback below would page the operator on every good tick,
            # exactly the crying-wolf that audit P2-1 set out to remove.
            return (
                "🟢 <b>CARDZ V2 tick draining</b>\n"
                f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
                f"inFlight={int(payload.get('inFlight') or 0)} "
                f"granted={float(payload.get('drainSecondsGranted') or 0.0):.0f}s "
                f"ceiling={int(payload.get('drainCeilingSeconds') or 0)}s\n"
                f"externalLimit={int(float(payload.get('externalLimitSeconds') or 0))}s "
                f"truncated={bool(payload.get('truncatedByExternalLimit'))}\n"
                "<i>past the claiming deadline; live workers keep running</i>"
            )
        if event_type == "TICK_CLAIM_WINDOW_CLAMPED":
            # A registration that disagrees with the code, not a failure: the
            # red fallback below would page the operator for a tick that is
            # doing exactly the safe thing.
            return (
                "🟠 <b>CARDZ V2 tick claim window clamped</b>\n"
                f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
                f"requested={float(payload.get('requestedClaimSeconds') or 0.0):.0f}s "
                f"effective={float(payload.get('effectiveClaimSeconds') or 0.0):.0f}s "
                f"externalLimit={float(payload.get('externalLimitSeconds') or 0.0):.0f}s\n"
                f"lift=<code>{html.escape(str(payload.get('liftWith') or '-'))}</code>\n"
                "<i>claiming was capped to fit ExecutionTimeLimit; live work still drains</i>"
            )
        if event_type == "MANUAL_WINDOW_RENEWED":
            return (
                "🟢 <b>CARDZ V2 manual window renewed</b>\n"
                f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
                f"previousStatus=<code>"
                f"{html.escape(str(payload.get('previousStatus') or '-'))}</code>\n"
                f"finalAt=<code>{html.escape(str(payload.get('finalAt') or '-'))}</code> "
                f"reopened={len(payload.get('reopenedTaskKeys') or [])}"
            )
        return (
            f"🔴 <b>CARDZ V2 {html.escape(event_type)}</b>\n"
            f"run=<code>{html.escape(str(payload.get('runId') or '-'))}</code>\n"
            f"stage=<code>{html.escape(str(payload.get('stage') or payload.get('phase') or '-'))}</code> "
            f"source=<code>{html.escape(str(payload.get('source') or '-'))}</code>\n"
            f"error=<code>{html.escape(str(payload.get('errorCode') or '-'))}</code> "
            f"next=<code>{html.escape(str(payload.get('nextRetry') or '-'))}</code> "
            f"active={int(payload.get('activeCount') or 0)}"
        )

    def run_tick(self) -> dict[str, Any]:
        run = self.journal.run(self.run_id) or {}
        if str(run.get("status") or "") in {
            "PUBLISHED", "PUBLISHED_DEGRADED", "FAILED_FINAL", "ABORTED",
        }:
            return self.journal.summary(self.run_id)
        self.recover_expired()
        while time.monotonic() < self.deadline_monotonic - TICK_RESERVE_SECONDS:
            now = utc_now()
            # audit P2-1(a): a running tick must stamp health.json every pass.
            # Before this, a tick only wrote health at 'started' and 'ended', so
            # the watchdog's 25-minute staleness rule could not see a tick that
            # was stuck for fifty minutes while holding the flock.
            self.write_health_liveness("working")
            self.finalise_live()
            run = self.journal.run(self.run_id) or {}
            if now >= self.schedule["final"]:
                self.lifecycle_events(now)
                self.deliver_events()
                break
            self.plan(now)
            ran = self.execute_ready()
            self.finalise_live()
            self.lifecycle_events(utc_now())
            self.deliver_events()
            if ran == 0:
                wait_seconds = self.next_retry_wait(utc_now())
                remaining = self.deadline_monotonic - time.monotonic() - TICK_RESERVE_SECONDS
                until_final = (self.schedule["final"] - utc_now()).total_seconds()
                if (
                    wait_seconds is None
                    or wait_seconds > remaining
                    or wait_seconds > until_final
                ):
                    break
                # audit P2-3: the break above still decides on the UNCAPPED wait,
                # so idle_wait() must consume that whole wait itself.  Capping
                # only the sleep would re-enter this loop thirty times over the
                # same wall clock instead of once.
                self.idle_wait(max(TICK_MIN_IDLE_SLEEP_SECONDS, wait_seconds + 0.05))
        return self.journal.summary(self.run_id)

    def idle_wait(self, seconds: float) -> None:
        """Wait `seconds`, but in slices that keep the tick observable.

        audit P2-3: `time.sleep(wait_seconds + 0.05)` could sleep 1800 s inside
        one process holding the exclusive tick flock, writing no health and
        never re-running recover_expired().  Each slice is at most
        TICK_SLEEP_SLICE_SECONDS and never runs past the claiming deadline.
        """

        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            limit = min(end, self.deadline_monotonic - TICK_RESERVE_SECONDS)
            now = time.monotonic()
            if now >= limit:
                return
            self.write_health_liveness("waiting")
            time.sleep(min(TICK_SLEEP_SLICE_SECONDS, limit - now))
            self.recover_expired()


def load_provenance(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "event_id": 0,
            "event_record_id": 0,
            "instance_id": "direct-cli",
            "task_name": "",
            "parent_process": Path(sys.executable).name,
            "event_age_seconds": 999999,
            "captured_at": iso(),
        }
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {
            "event_id": 0,
            "event_record_id": 0,
            "instance_id": "invalid-provenance",
            "task_name": "",
            "parent_process": Path(sys.executable).name,
            "event_age_seconds": 999999,
            "captured_at": iso(),
            "error_code": "PROVENANCE_RECEIPT_INVALID",
        }
    if not isinstance(value, dict):
        value = {
            "event_id": 0,
            "event_record_id": 0,
            "instance_id": "invalid-provenance-shape",
            "task_name": "",
            "parent_process": Path(sys.executable).name,
            "event_age_seconds": 999999,
            "captured_at": iso(),
            "error_code": "PROVENANCE_RECEIPT_NOT_OBJECT",
        }
    return value


def status_brief(
    journal: Journal,
    business_date: date,
    run_label: str = "",
    supersede_seq: int = 1,
) -> str:
    """One operator line built from the same data as health.json."""

    health = build_health_document(
        journal, business_date, tick_phase="query", run_label=run_label,
        supersede_seq=supersede_seq,
    )
    tasks = health["tasks"]
    done = sum(
        1 for row in tasks.values() if str(row["state"]) in SUCCESS_TASK_STATES
    )
    retry = sum(1 for row in tasks.values() if str(row["state"]) in {"RETRY", "INTERRUPTED"})
    terminal = sum(1 for row in tasks.values() if str(row["state"]) == "TERMINAL")
    parked = ",".join(health["parked"]) or "-"
    age = "-"
    try:
        written = json.loads(health_path(run_label).read_text(encoding="utf-8")).get("written_at_utc")
        if written:
            stamp = datetime.fromisoformat(str(written).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = str(int((utc_now() - stamp).total_seconds()))
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        age = "-"
    label = (run_label or "").strip()
    seq = int(health["supersede_seq"])
    return (
        f"{health['business_date']}{'#' + label if label else ''}"
        f"{supersede_suffix(seq)} {health['run_state']}"
        f" tasks={done}/{len(tasks)} retry={retry} terminal={terminal}"
        f" parked={parked}"
        f" next_retry={health['next_retry_at_utc'] or '-'}"
        f" manual_until={health['manual_window_until_utc'] or '-'}"
        f" autonomous={bool(health['autonomous_proven'])}"
        f" health_age={age}"
    )


def foreign_run_id(journal: Journal, task_key: str, run_id: str) -> str:
    """The run that owns `task_key`, when it is not the one being operated on."""

    row = journal.task(task_key) or {}
    owner = str(row.get("run_id") or "")
    return owner if owner and owner != run_id else ""


def operator_scope_error(journal: Journal, task_key: str, run_id: str) -> str:
    """audit P2-14: name the other business date instead of "not parkable"."""

    return (
        f"task belongs to another business date: {task_key}"
        f" run={foreign_run_id(journal, task_key, run_id)}"
        f" (this command is scoped to {run_id})"
    )


def run_unpark(journal: Journal, business_date: date, args: Any) -> int:
    """Operator resume path for work the budgets stopped auto-claiming."""

    run_id = run_id_for(
        business_date,
        str(getattr(args, "run_label", "") or ""),
        int(getattr(args, "supersede_seq", 1) or 1),
    )
    if args.list_only:
        rows = journal.unparkable_tasks(run_id)
        for row in rows:
            print(
                f"{row['status']}\t{row['task_key']}\tattempts={row['attempts']}"
                f"/{row['max_attempts']}\tinterruptions={row.get('interruptions') or 0}"
                f"\t{row.get('last_error_code') or '-'}"
            )
        if not rows:
            print("no parked or terminal tasks")
        return 0
    if not args.task:
        print("unpark requires --task <task_key> or --list", file=sys.stderr)
        return 2
    row = journal.unpark(str(args.task), run_id=run_id, reason=str(args.reason))
    if row is None:
        if foreign_run_id(journal, str(args.task), run_id):
            print(operator_scope_error(journal, str(args.task), run_id), file=sys.stderr)
            return 2
        print(f"task is not parkable: {args.task}", file=sys.stderr)
        return 2
    journal.add_event(
        run_id,
        "TASK_UNPARKED",
        f"{row['task_key']}:{row['attempts']}:{iso()}",
        {
            "runId": run_id,
            "taskKey": row["task_key"],
            "phase": row.get("phase"),
            "source": row.get("source_code"),
            "provenance": "operator",
            "reason": str(args.reason),
            "previousStatus": row["previousStatus"],
            "attempts": int(row.get("attempts") or 0),
            "maxAttempts": int(row.get("max_attempts") or 0),
            "previousMaxAttempts": int(row["previousMaxAttempts"]),
            "previousInterruptions": int(row["previousInterruptions"]),
            "nextRetry": row.get("next_retry_at"),
        },
    )
    print(
        f"TASK_UNPARKED {row['task_key']} {row['previousStatus']}->READY"
        f" attempts={row['attempts']}/{row['max_attempts']} interruptions=0"
    )
    return 0


def run_retire(journal: Journal, business_date: date, args: argparse.Namespace) -> int:
    """Operator settlement of a parked/terminal task that later work superseded."""

    run_id = run_id_for(
        business_date,
        str(getattr(args, "run_label", "") or ""),
        int(getattr(args, "supersede_seq", 1) or 1),
    )
    if args.list_only:
        return run_unpark(journal, business_date, args)
    if not args.task:
        print("retire requires --task <task_key> or --list", file=sys.stderr)
        return 2
    row = journal.retire(str(args.task), run_id=run_id, reason=str(args.reason))
    if row is None:
        if foreign_run_id(journal, str(args.task), run_id):
            print(operator_scope_error(journal, str(args.task), run_id), file=sys.stderr)
            return 2
        print(f"task is not parked or terminal: {args.task}", file=sys.stderr)
        return 2
    journal.add_event(
        run_id,
        "TASK_RETIRED",
        f"{row['task_key']}:{row['attempts']}:{iso()}",
        {
            "runId": run_id,
            "taskKey": row["task_key"],
            "phase": row.get("phase"),
            "source": row.get("source_code"),
            "provenance": "operator",
            "reason": str(args.reason),
            "previousStatus": row["previousStatus"],
            "attempts": int(row.get("attempts") or 0),
            "maxAttempts": int(row.get("max_attempts") or 0),
            "status": row["status"],
            "errorCode": row.get("last_error_code"),
        },
    )
    print(
        f"TASK_RETIRED {row['task_key']} {row['previousStatus']}->{row['status']}"
        f" attempts={row['attempts']}/{row['max_attempts']} reason={args.reason}"
    )
    return 0


def run_supersede(journal: Journal, business_date: date, args: argparse.Namespace) -> int:
    """Operator same-day rerun: archive this date's run so it can run again.

    Dry run by default and it prints the whole impact, because the write is
    not reversible from the CLI: the live rows move into the `*_archive`
    tables and only the JSON receipt reconstructs them.

    MySQL is deliberately untouched here.  publication_outbox is a publication
    lock, not run state: the older row for the date is marked superseded by
    `insert_live_event` at the moment the new generation is confirmed, so a
    supersede that never republishes leaves the live site exactly as it is.
    """

    day_text = business_date.isoformat()
    seq = journal.live_supersede_seq(day_text)
    try:
        preview = journal.supersede_preview(
            day_text, receipt_dir=runtime_dir_for(business_date, "", seq)
        )
    except JournalError as error:
        print(f"SUPERSEDE_REFUSED {error}", file=sys.stderr)
        return 2
    impact = {
        **preview,
        "runtimeDir": str(runtime_dir_for(business_date, "", preview["nextSupersedeSeq"])),
        "journalPath": str(journal.path),
        "outbox": {
            "table": "publication_outbox",
            "eventType": "live.confirmed",
            "supersededEventKey": (
                f"live.confirmed:{day_text}{supersede_suffix(preview['supersedeSeq'])}"
                if preview["publicationStatus"] else None
            ),
            "supersededGenerationId": preview["generationId"],
            "newEventKey": (
                f"live.confirmed:{day_text}"
                f"{supersede_suffix(preview['nextSupersedeSeq'])}"
            ),
            "rowsMarkedSupersededAtPublish": 1 if preview["publicationStatus"] else 0,
            "note": "MySQL is written by live-confirm, not by this command",
        },
    }
    if not args.write:
        print(json.dumps({"dryRun": True, **impact}, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    try:
        result = journal.supersede_run(
            day_text,
            reason=str(args.reason),
            receipt_dir=runtime_dir_for(business_date, "", seq),
            origin="manual",
        )
    except JournalError as error:
        print(f"SUPERSEDE_REFUSED {error}", file=sys.stderr)
        return 2
    # The watchdog reads health.json alone and treats today-PUBLISHED as
    # silence-OK.  The document it would still be reading describes a run that
    # no longer exists, so re-stamp it here rather than waiting for the next
    # tick to re-arm the watchdog.
    try:
        write_health_document(
            build_health_document(
                journal,
                business_date,
                tick_phase="superseded",
                supersede_seq=result["nextSupersedeSeq"],
            )
        )
    except Exception:  # noqa: BLE001 - health reporting never fails the command
        pass
    print(json.dumps(
        {"dryRun": False, **impact, **result}, ensure_ascii=False, sort_keys=True, default=str
    ))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    tick = sub.add_parser("tick")
    tick.add_argument("--state-db", type=Path, default=default_state_path())
    tick.add_argument("--provenance", type=Path)
    tick.add_argument("--business-date", type=date.fromisoformat)
    tick.add_argument(
        "--max-runtime-seconds", type=int, default=DEFAULT_MAX_RUNTIME_SECONDS
    )
    tick.add_argument("--allow-publish", action="store_true")
    tick.add_argument("--notify", action="store_true")
    tick.add_argument("--manual-e2e-window", action="store_true")
    tick.add_argument("--renew-manual-e2e-window", action="store_true")
    tick.add_argument("--run-label", default="", help="rehearsal label (A01, A02, ...): same business date, own journal file, own runtime dir/health file, never publishes")
    # Durability fixture only: take the lock, install handlers, write health,
    # then idle.  It never plans, claims, or touches MySQL.
    tick.add_argument("--selftest-sleep", type=float, default=0.0, help=argparse.SUPPRESS)
    status = sub.add_parser("status")
    status.add_argument("--state-db", type=Path, default=default_state_path())
    status.add_argument("--business-date", type=date.fromisoformat)
    status.add_argument("--brief", action="store_true")
    status.add_argument("--run-label", default="")
    unpark = sub.add_parser("unpark")
    unpark.add_argument("--state-db", type=Path, default=default_state_path())
    unpark.add_argument("--business-date", type=date.fromisoformat)
    unpark.add_argument("--task")
    unpark.add_argument("--reason", default="operator unpark")
    unpark.add_argument("--list", dest="list_only", action="store_true")
    unpark.add_argument("--run-label", default="")
    retire = sub.add_parser("retire")
    retire.add_argument("--state-db", type=Path, default=default_state_path())
    retire.add_argument("--business-date", type=date.fromisoformat)
    retire.add_argument("--task")
    retire.add_argument("--reason", required=True)
    retire.add_argument("--list", dest="list_only", action="store_true")
    retire.add_argument("--run-label", default="")
    supersede = sub.add_parser(
        "supersede",
        help="archive this business date's run so the same date can run again"
        " today with fresh data (dry run unless --write)",
    )
    supersede.add_argument("--state-db", type=Path, default=default_state_path())
    supersede.add_argument("--business-date", type=date.fromisoformat)
    supersede.add_argument(
        "--reason", default="operator supersede: same-day rerun with fresh data"
    )
    supersede.add_argument("--write", action="store_true")
    args = parser.parse_args()

    day = args.business_date or datetime.now(JST).date()
    run_label = str(getattr(args, "run_label", "") or "").strip()
    if run_label:
        try:
            run_id_for(day, run_label)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        # Rehearsals never share the live journal (autonomy evidence).
        args.state_db = rehearsal_state_path(args.state_db, run_label)
    journal = Journal(args.state_db.resolve())
    # A superseded business date runs again as `<run_id>/<N>`; a date that was
    # never superseded answers 1 here and every id below stays byte-identical
    # to the historical one.
    supersede_seq = journal.live_supersede_seq(day.isoformat())
    args.supersede_seq = supersede_seq
    run_id = run_id_for(day, run_label, supersede_seq)
    if args.command == "supersede":
        journal.initialise()
        return run_supersede(journal, day, args)

    if args.command == "status":
        journal.initialise()
        row = journal.run(run_id)
        if args.brief:
            print(status_brief(journal, day, run_label, supersede_seq))
            return 0
        if row is None:
            print(json.dumps({"runId": run_id, "status": "NOT_STARTED"}, sort_keys=True))
            return 0
        print(json.dumps(journal.summary(run_id), ensure_ascii=False, sort_keys=True, default=str))
        return 0

    if args.command == "unpark":
        journal.initialise()
        return run_unpark(journal, day, args)

    if args.command == "retire":
        journal.initialise()
        return run_retire(journal, day, args)

    if (
        args.max_runtime_seconds < 60
        or args.max_runtime_seconds > MAX_RUNTIME_SECONDS_CEILING
    ):
        raise SystemExit(
            f"--max-runtime-seconds must be between 60 and {MAX_RUNTIME_SECONDS_CEILING}"
        )
    if run_label and args.allow_publish:
        raise SystemExit(
            "--run-label names a rehearsal and never publishes; drop --run-label"
            " for a real run (MySQL keys publication to the business date)"
        )
    if run_label and (args.manual_e2e_window or args.renew_manual_e2e_window):
        raise SystemExit(
            "--run-label already implies a rehearsal window; it takes neither"
            " --manual-e2e-window nor --renew-manual-e2e-window (open the next label instead)"
        )
    if os.name == "nt":
        raise SystemExit("Daily Chain V2 tick must run inside WSL")
    if str(journal.path).replace("\\", "/").startswith("/mnt/"):
        raise SystemExit("Daily Chain V2 journal must live on WSL ext4, not /mnt")
    if args.manual_e2e_window and not args.allow_publish:
        raise SystemExit("--manual-e2e-window requires explicit --allow-publish")
    provenance = load_provenance(args.provenance.resolve() if args.provenance else None)
    if args.renew_manual_e2e_window and not args.manual_e2e_window:
        raise SystemExit("--renew-manual-e2e-window requires --manual-e2e-window")
    if args.renew_manual_e2e_window and classify_provenance(provenance) != "manual":
        raise SystemExit("--renew-manual-e2e-window requires manual provenance")
    journal.initialise()
    lock_handle, acquired = acquire_tick_lock(journal.path)
    if not acquired:
        # Another tick still owns this journal.  Overlapping ticks double every
        # claim race; skipping is the correct, silent, zero-exit outcome.
        print("TICK_SKIPPED_LOCKED")
        try:
            report_tick_skipped(day, run_label)
        except Exception:  # noqa: BLE001
            pass
        return 0
    global TICK_LOCK_HANDLE
    # Keep the descriptor referenced: closing it would release the flock.
    TICK_LOCK_HANDLE = lock_handle
    # Phase 2 auto-align, default OFF.  Under the tick lock, so two ticks can
    # never archive the same date at once, and before the chain is built,
    # because the archival is what decides this tick's run id and runtime dir.
    # A rehearsal journal has no date to align.
    if not run_label:
        superseded = maybe_auto_supersede(journal, day, provenance)
        if superseded is not None:
            print(
                "AUTO_SUPERSEDED"
                f" run={superseded['runId']} next={superseded['nextRunId']}"
                f" receipt={superseded['receiptPath']}",
                flush=True,
            )
            supersede_seq = journal.live_supersede_seq(day.isoformat())
            args.supersede_seq = supersede_seq
    chain = DailyChainV2(
        journal=journal,
        business_date=day,
        allow_publish=bool(args.allow_publish),
        notify=bool(args.notify),
        deadline_monotonic=time.monotonic() + args.max_runtime_seconds,
        schedule=(
            # A rehearsal opened after hours needs the manual window shape;
            # the scheduled window for the date has usually already closed.
            manual_e2e_schedule(business_date=day, rehearsal=bool(run_label))
            if (args.manual_e2e_window or run_label) else None
        ),
        run_label=run_label,
        supersede_seq=supersede_seq,
    )
    chain.initialise(
        provenance,
        renew_manual_window=bool(args.renew_manual_e2e_window),
    )
    chain.tick_started_at_utc = iso()

    def _on_signal(signum: int, frame: Any) -> None:  # noqa: ANN001 - handler shape
        del frame
        os._exit(chain.signal_shutdown(int(signum)))

    for signal_name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signal_number = getattr(signal, signal_name, None)
        if signal_number is None:
            continue
        try:
            signal.signal(signal_number, _on_signal)
        except (OSError, ValueError):  # non-main thread or unsupported signal
            continue
    chain.write_health(tick_phase="started")

    try:
        if args.selftest_sleep > 0:
            time.sleep(float(args.selftest_sleep))
            summary = {"run": journal.run(chain.run_id), "taskCounts": {}, "tasks": []}
        else:
            summary = chain.run_tick()
    except KeyboardInterrupt:
        raise SystemExit(chain.signal_shutdown(int(signal.SIGINT))) from None
    except SystemExit as error:
        code = error.code if isinstance(error.code, int) else 1
        chain.tick_crashed(error, exit_code=int(code))
        raise
    except BaseException as error:  # noqa: BLE001 - persist orchestrator faults before exit
        error_code = classify_error(str(error)).error_code
        journal.add_event(
            chain.run_id,
            "ORCHESTRATOR_ERROR",
            f"{error_code}:{type(error).__name__}",
            {
                "runId": chain.run_id,
                "stage": "orchestrator",
                "source": "system",
                "errorCode": error_code,
                "error": f"{type(error).__name__}:{error}",
                "nextRetry": "next scheduler tick",
                "logPath": str(chain.log_dir),
            },
        )
        try:
            chain.deliver_events()
        except Exception:
            pass
        chain.tick_crashed(error, exit_code=1)
        raise
    chain.write_health(tick_phase="ended", tick_exit_code=0, tick_ended_at_utc=iso())
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
