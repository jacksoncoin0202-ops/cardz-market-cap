#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resumable single-tick orchestrator for CARDZ Marketcap Daily Chain V2."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
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
    CommandSourceAdapter,
    WorkerInterrupted,
    build_default_registry,
    task_payload,
    terminate_worker_group,
)
from daily_chain_v2_contract import (  # noqa: E402
    CONTRACT_SHORTFALL_MARKER,
    SourceTask,
    canonical_json,
    classify_error,
    classify_provenance,
    identity_lanes,
    sha256,
    v2_schema_capabilities,
)
from daily_chain_v2_journal import (  # noqa: E402
    Journal,
    SUCCESS_TASK_STATES,
    TERMINAL_TASK_STATES,
    default_state_path,
    iso,
    utc_now,
)


JST = ZoneInfo("Asia/Tokyo")
TASK_LEASE_SECONDS = 90
TICK_RESERVE_SECONDS = 30
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
# Drain (operator finding 2026-08-24).  Once claiming closes the tick keeps
# waiting for work that is still alive.  2400 s: the longest honest single
# worker measured is gemrate contract-repair over 1604 cards at ~55-60 min, and
# a task claimed at the very start of a 3000 s tick already owns ~49.5 min of
# it, so 40 min of drain covers that worker even when it was claimed 20 minutes
# late, while still bounding the flock hold at max_runtime + 40 min.
TICK_DRAIN_CEILING_SECONDS = 2400
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
#   -   60  TICK_INTERRUPT_GRACE_SECONDS: terminate_worker_group() at the bound
#   -  180  tail after the last worker dies: finalise_live + lifecycle_events +
#           deliver_events (NOTIFY_TIMEOUT_SECONDS=20 per send, several events)
#           + write_health + summary
#   = 3000  == DEFAULT_MAX_RUNTIME_SECONDS
# i.e. under the INSTALLED PT55M + -MaxRuntimeSeconds 3000 the drain grants zero
# extra seconds and nothing regresses against today's margin.  Two ways to buy
# real drain, neither of which this file may take on its own:
#   (1) run the tick with a smaller --max-runtime-seconds; claiming closes
#       earlier and drain fills the rest of the same 3000 s.  No installer
#       change, available today.
#   (2) raise ExecutionTimeLimit in scripts/install_cardz_daily_v2_task.ps1 AND
#       set CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS to the new PT value in the
#       launcher's environment.  Raising one without the other either buys
#       nothing or moves the kill outside our control.
# The TICK_DRAINING event reports which of the two bounds truncated a drain.
TICK_EXTERNAL_LIMIT_SECONDS = 3300
TICK_LAUNCHER_STARTUP_RESERVE_SECONDS = 60
TICK_INTERRUPT_GRACE_SECONDS = 60.0
TICK_DRAIN_TAIL_RESERVE_SECONDS = 180
DRAIN_HEARTBEAT_STALE_SECONDS = TASK_LEASE_SECONDS
# Review fix: drain_deadline_for() asks claim_still_live() on EVERY worker poll
# (1/s per source worker, 1/2 s per stage worker, up to sixteen in flight) and
# each ask opens a fresh sqlite connection.  Heartbeats are written every 10 s
# (source) / 2 s (stage) and the stale window is 90 s, so caching the answer for
# five seconds cannot change a single decision.
DRAIN_LIVENESS_CACHE_SECONDS = 5.0
# One tick must fit inside the scheduler's own ExecutionTimeLimit; 3000 s is
# the single source of that number for both the CLI default and the installer.
DEFAULT_MAX_RUNTIME_SECONDS = 3000
MAX_RUNTIME_SECONDS_CEILING = 5400
LAST_SCHEDULED_TICK_JST = "17:00"
# 2026-08-22: the manual window was tick start + 600 s, and the run reached
# publish with four minutes of window left.  Forty-five minutes is the floor
# a manual E2E actually needs; the next scheduled tick is still the ceiling.
MANUAL_WINDOW_MIN_SECONDS = 2700
MANUAL_WINDOW_ABSOLUTE_MIN_SECONDS = 600
NEXT_TICK_GUARD_SECONDS = 300
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
    "TICK_CRASHED": ("v2-tick-crashed", "error", 10),
    "TICK_SIGNALLED": ("v2-tick-signalled", "warn", 10),
    "TASK_ADOPTED": ("v2-task-adopted", "info", 30),
}
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
) -> dict[str, datetime]:
    """No manual window may outlive the day's last scheduled tick.

    An unclamped +8h renewal at 16:00 JST keeps a manual run authoritative
    deep into the next unattended cycle; the operator window is capped at
    17:00 JST.  It is also never shorter than MANUAL_WINDOW_MIN_SECONDS of
    remaining work, and never long enough to still be authoritative when the
    next unattended 03:30 JST tick starts -- that ceiling outranks the floor,
    with ten minutes kept as the absolute lower bound.
    """

    values = dict(schedule)
    started = values["start"].astimezone(timezone.utc)
    floor = started + timedelta(seconds=MANUAL_WINDOW_MIN_SECONDS)
    cap = max(floor, last_scheduled_tick_utc(business_date))
    ceiling = next_scheduled_tick_utc(started) - timedelta(seconds=NEXT_TICK_GUARD_SECONDS)
    cap = min(cap, ceiling)
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
    )


def health_path() -> Path:
    configured = os.environ.get("CARDZ_V2_HEALTH_PATH", "").strip()
    if configured:
        return Path(configured).expanduser()
    return ROOT / "data" / "runtime" / "daily-chain-v2" / "health.json"


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
) -> dict[str, Any]:
    """Schema-1 health contract shared with the watchdog (C1)."""

    run_id = f"cardz-v2:{business_date.isoformat()}"
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
        "run_state": run_state_of(run),
        "tick_phase": tick_phase,
        "tick_exit_code": None if tick_exit_code is None else int(tick_exit_code),
        "tick_started_at_utc": tick_started_at_utc,
        "tick_ended_at_utc": tick_ended_at_utc,
        "tick_duration_s": duration,
        "next_retry_at_utc": iso(min(retries)) if retries else None,
        "tasks": tasks,
        "parked": sorted(parked),
        "manual_window_until_utc": manual_window_until(run, business_date),
        "autonomous_proven": bool(int((run or {}).get("proven_autonomous") or 0)),
        "last_alert": LAST_ALERT,
    }


def write_health_document(document: Mapping[str, Any]) -> Path:
    path = health_path()
    atomic_json(path, document)
    return path


def report_tick_skipped(business_date: date) -> Path:
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

    path = health_path().with_name("tick-skipped.json")
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

    raw = os.environ.get("CARDZ_V2_TICK_EXTERNAL_LIMIT_SECONDS", "").strip()
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
    """

    return max(
        0.0,
        tick_external_limit_seconds()
        - TICK_LAUNCHER_STARTUP_RESERVE_SECONDS
        - TICK_INTERRUPT_GRACE_SECONDS
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
    ) -> None:
        self.journal = journal
        self.business_date = business_date
        self.day_text = business_date.isoformat()
        self.run_id = f"cardz-v2:{self.day_text}"
        self.allow_publish = allow_publish
        self.notify = notify
        self.deadline_monotonic = deadline_monotonic
        self.runtime_dir = ROOT / "data" / "runtime" / "daily-chain-v2" / self.day_text
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

        from daily_chain_v2_db import current_run_contract, quote_repair_plan

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
                    max_attempts=7,
                    payload=source_payload(adapter, task),
                )
            return
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

        identity = self.journal.tasks(self.run_id, phase="identity")
        if not identity:
            # Lane and its transport group are declared by the registered
            # identity sources (SourceSpec.identity_lane / concurrency_group),
            # persisted in market_source_registry by migration 054.
            for lane, group in self.identity_lanes():
                self.add_stage(
                    phase="identity",
                    capability=f"identity-{lane}",
                    stage_name="discover",
                    args=("--lane", lane),
                    required_class="extra",
                    concurrency_group=group,
                    max_attempts=7,
                )
            return
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
            identity = self.journal.tasks(self.run_id, phase="identity")
        if not optional_phase_settled(identity, now=now, cutoff=self.schedule["source_cutoff"]):
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
        if str(pending_stage.get("status") or "") not in SUCCESS_TASK_STATES | {"TERMINAL"}:
            return

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
            if str(candidate_registry.get("status") or "") not in SUCCESS_TASK_STATES | {"TERMINAL"}:
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
            if str(candidate_plan.get("status") or "") not in SUCCESS_TASK_STATES | {"TERMINAL"}:
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
            if str(consolidate.get("status") or "") not in SUCCESS_TASK_STATES | {"TERMINAL"}:
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

        if not self.allow_publish:
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
    ) -> bool:
        """Journal one event and alert unconditionally on lifecycle facts.

        `--notify` still gates the verbose progress stream; the six lifecycle
        events below are the ones an operator cannot afford to miss, so they
        leave through their own best-effort channel the first time they are
        recorded.
        """

        inserted = self.journal.add_event(self.run_id, event_type, dedupe_key, payload)
        if inserted and event_type in ALWAYS_ALERT_EVENTS:
            key, level, cooldown = ALWAYS_ALERT_EVENTS[event_type]
            send_alert(
                f"{key}:{self.day_text}",
                self._alert_text(event_type, payload),
                level=level,
                cooldown_min=cooldown,
            )
        return inserted

    def _alert_text(self, event_type: str, payload: Mapping[str, Any]) -> str:
        detail = " ".join(
            f"{key}={payload[key]}"
            for key in (
                "taskKey", "signal", "pid", "exitCode", "errorCode", "reason",
                "generation", "activeCount", "interruptions", "attempts",
            )
            if payload.get(key) not in (None, "")
        )
        return f"CARDZ V2 {event_type} run={self.run_id} {detail}".strip()

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
                )
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
                self._interrupt(task_key, claim, reason=reason)
            except Exception:  # noqa: BLE001 - shutdown releases best effort
                continue
            released.append(task_key)
        return released

    def _interrupt(self, task_key: str, claim: str, *, reason: str) -> str:
        status = self.journal.interrupt_claim(task_key, claim, reason=reason)
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

    def _work_deadline_monotonic(self, row: Mapping[str, Any] | None = None) -> float:
        until_final = max(0.0, (self.schedule["final"] - utc_now()).total_seconds())
        deadlines = [self.drain_deadline_for(row), time.monotonic() + until_final]
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
            deadlines.append(time.monotonic() + until_cutoff)
        return min(deadlines)

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
        env = os.environ.copy()
        env.update({
            "CARDZ_DAILY_CHAIN_V2": "1",
            "CARDZ_V2_RUN_ID": self.run_id,
            "CARDZ_V2_BUSINESS_DATE": self.day_text,
            # business_window_utc opens the coverage window at the earlier of
            # JST midnight and this run's creation, so an early manual window
            # counts its own observations.
            "CARDZ_V2_RUN_STARTED_AT": str((self.journal.run(self.run_id) or {}).get("created_at") or ""),
            "CARDZ_V2_TASK_KEY": str(row["task_key"]),
            "CARDZ_V2_CLAIM_TOKEN": str(row["lease_token"]),
            "CARDZ_V2_STATE_DB": str(self.journal.path),
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
                    # Same 60 s the drain arithmetic reserves; one source.
                    terminate_worker_group(
                        proc.pid, grace_seconds=TICK_INTERRUPT_GRACE_SECONDS
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
            self._interrupt(task_key, claim, reason=str(error))
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
            if status == "RETRY":
                self.journal.reclassify_retry(
                    str(row["task_key"]), decision=decision
                )
                continue
            if self.journal.reopen_retryable_terminal(
                str(row["task_key"]), decision=decision
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


def status_brief(journal: Journal, business_date: date) -> str:
    """One operator line built from the same data as health.json."""

    health = build_health_document(journal, business_date, tick_phase="query")
    tasks = health["tasks"]
    done = sum(
        1 for row in tasks.values() if str(row["state"]) in SUCCESS_TASK_STATES
    )
    retry = sum(1 for row in tasks.values() if str(row["state"]) in {"RETRY", "INTERRUPTED"})
    terminal = sum(1 for row in tasks.values() if str(row["state"]) == "TERMINAL")
    parked = ",".join(health["parked"]) or "-"
    age = "-"
    try:
        written = json.loads(health_path().read_text(encoding="utf-8")).get("written_at_utc")
        if written:
            stamp = datetime.fromisoformat(str(written).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            age = str(int((utc_now() - stamp).total_seconds()))
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        age = "-"
    return (
        f"{health['business_date']} {health['run_state']}"
        f" tasks={done}/{len(tasks)} retry={retry} terminal={terminal}"
        f" parked={parked}"
        f" next_retry={health['next_retry_at_utc'] or '-'}"
        f" manual_until={health['manual_window_until_utc'] or '-'}"
        f" autonomous={bool(health['autonomous_proven'])}"
        f" health_age={age}"
    )


def run_unpark(journal: Journal, business_date: date, args: Any) -> int:
    """Operator resume path for work the budgets stopped auto-claiming."""

    run_id = f"cardz-v2:{business_date.isoformat()}"
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
    row = journal.unpark(str(args.task), reason=str(args.reason))
    if row is None:
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

    run_id = f"cardz-v2:{business_date.isoformat()}"
    if args.list_only:
        return run_unpark(journal, business_date, args)
    if not args.task:
        print("retire requires --task <task_key> or --list", file=sys.stderr)
        return 2
    row = journal.retire(str(args.task), reason=str(args.reason))
    if row is None:
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
    # Durability fixture only: take the lock, install handlers, write health,
    # then idle.  It never plans, claims, or touches MySQL.
    tick.add_argument("--selftest-sleep", type=float, default=0.0, help=argparse.SUPPRESS)
    status = sub.add_parser("status")
    status.add_argument("--state-db", type=Path, default=default_state_path())
    status.add_argument("--business-date", type=date.fromisoformat)
    status.add_argument("--brief", action="store_true")
    unpark = sub.add_parser("unpark")
    unpark.add_argument("--state-db", type=Path, default=default_state_path())
    unpark.add_argument("--business-date", type=date.fromisoformat)
    unpark.add_argument("--task")
    unpark.add_argument("--reason", default="operator unpark")
    unpark.add_argument("--list", dest="list_only", action="store_true")
    retire = sub.add_parser("retire")
    retire.add_argument("--state-db", type=Path, default=default_state_path())
    retire.add_argument("--business-date", type=date.fromisoformat)
    retire.add_argument("--task")
    retire.add_argument("--reason", required=True)
    retire.add_argument("--list", dest="list_only", action="store_true")
    args = parser.parse_args()

    day = args.business_date or datetime.now(JST).date()
    journal = Journal(args.state_db.resolve())
    run_id = f"cardz-v2:{day.isoformat()}"
    if args.command == "status":
        journal.initialise()
        row = journal.run(run_id)
        if args.brief:
            print(status_brief(journal, day))
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
            report_tick_skipped(day)
        except Exception:  # noqa: BLE001
            pass
        return 0
    global TICK_LOCK_HANDLE
    # Keep the descriptor referenced: closing it would release the flock.
    TICK_LOCK_HANDLE = lock_handle
    chain = DailyChainV2(
        journal=journal,
        business_date=day,
        allow_publish=bool(args.allow_publish),
        notify=bool(args.notify),
        deadline_monotonic=time.monotonic() + args.max_runtime_seconds,
        schedule=(
            manual_e2e_schedule(business_date=day) if args.manual_e2e_window else None
        ),
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
