#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Durable SQLite journal for CARDZ Daily Chain V2.

Only orchestration metadata is stored here.  Prices, identities, source
payloads, and secrets remain in their existing authorities.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from daily_chain_v2_contract import (
    RetryDecision,
    SourceTask,
    autonomous_proven,
    canonical_json,
    classify_provenance,
    sha256,
)


SCHEMA_VERSION = 1
# PARKED is settled work: it never auto-claims again and only `unpark` may
# revive it.  Downstream barriers must treat it as finished, exactly like
# TERMINAL, or one exhausted task would hold the whole business date open.
TERMINAL_TASK_STATES = frozenset({"COMPLETED", "DEGRADED", "TERMINAL", "SKIPPED", "PARKED"})
SUCCESS_TASK_STATES = frozenset({"COMPLETED", "DEGRADED", "SKIPPED"})
CLAIMABLE_TASK_STATES = ("PENDING", "READY", "RETRY", "INTERRUPTED")
UNPARKABLE_TASK_STATES = ("PARKED", "TERMINAL")
# retire may also settle work that is merely waiting for its next attempt; a
# live lease (RUNNING, or a claim in flight) is never retired from under a
# worker.
RETIRABLE_TASK_STATES = UNPARKABLE_TASK_STATES + ("RETRY", "INTERRUPTED")
RUN_SUCCESS_STATES = frozenset({"PUBLISHED", "PUBLISHED_DEGRADED"})
# audit P1-3: 2026-08-22 recorded MANUAL_WINDOW_RENEWED=8 and published at
# 23:47 JST, 6h47m late, because renewals were unbounded -- FAILED_FINAL was
# decoration, not a gate.  Four renewals is the ceiling; past it the run must
# fail and the next business date starts clean.
MANUAL_WINDOW_MAX_RENEWALS = 4
DEFAULT_MAX_INTERRUPTIONS = 6
INTERRUPT_BACKOFF_BASE_SECONDS = 60
INTERRUPT_BACKOFF_CAP_SECONDS = 900


def default_max_interruptions() -> int:
    raw = os.environ.get("CARDZ_V2_MAX_INTERRUPTIONS", "").strip()
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MAX_INTERRUPTIONS
    return value if value >= 1 else DEFAULT_MAX_INTERRUPTIONS


def interrupt_backoff_seconds(interruptions: int) -> int:
    exponent = max(0, min(int(interruptions), 32))
    return min(
        INTERRUPT_BACKOFF_BASE_SECONDS * (2 ** exponent),
        INTERRUPT_BACKOFF_CAP_SECONDS,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utc_now()).astimezone(timezone.utc).isoformat(timespec="microseconds")


def default_state_path() -> Path:
    configured = os.environ.get("CARDZ_DAILY_V2_STATE_DB", "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "state" / "cardz-marketcap" / "daily-chain-v2.sqlite3"


def later_iso(candidate: str, current: str) -> str:
    """Return whichever of the two ISO timestamps is later, verbatim.

    This is a journal-API guard and nothing more: renew_manual_window takes its
    three deadlines from the caller, and no caller may pull `source_cutoff`,
    `sla` or `final` backwards onto work that is already running.

    It does NOT close audit P1-3's expensive half, and must not be described as
    if it does.  The only production caller (DailyChainV2.initialise ->
    manual_e2e_schedule -> clamp_manual_window) always builds the window from
    `started=now`, and every term of that clamp is non-decreasing in `started`,
    so `source_cutoff = (started + final)/2` strictly increases and this guard
    never binds there.  Each renewal therefore still re-arms a fresh ~22 minute
    source_cutoff onto already-running non-core sources -- the 2026-08-22
    pricecharting attempts of 22.6 / 20.4 / 20.0 / 20.0 minutes.  What bounds
    that shape on the real path today is MANUAL_WINDOW_MAX_RENEWALS (eight
    re-arms become four); the re-arm itself is still open and needs the audit's
    other half (exempt an already-running non-core source from a re-armed
    cutoff), which is a deadline-semantics change landed nowhere yet.

    The winning string is returned unchanged so a renewal never rewrites the
    stored timestamp's formatting.
    """

    try:
        left = datetime.fromisoformat(candidate)
        right = datetime.fromisoformat(current)
    except (TypeError, ValueError):
        return candidate
    if left.tzinfo is None:
        left = left.replace(tzinfo=timezone.utc)
    if right.tzinfo is None:
        right = right.replace(tzinfo=timezone.utc)
    return candidate if left >= right else current


class JournalError(RuntimeError):
    pass


class ClaimLost(JournalError):
    pass


class Journal:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def initialise(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS journal_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS chain_run (
                    run_id TEXT PRIMARY KEY,
                    business_date TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    origin TEXT NOT NULL,
                    scheduled_event_107_count INTEGER NOT NULL DEFAULT 0,
                    manual_intervention_count INTEGER NOT NULL DEFAULT 0,
                    manual_window_renewals INTEGER NOT NULL DEFAULT 0,
                    source_cutoff_at TEXT NOT NULL,
                    sla_at TEXT NOT NULL,
                    final_at TEXT NOT NULL,
                    publication_status TEXT,
                    generation_id TEXT,
                    generated_at TEXT,
                    content_sha256 TEXT,
                    active_count INTEGER,
                    degraded_sources_json TEXT NOT NULL DEFAULT '[]',
                    proven_autonomous INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS chain_task (
                    task_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    source_code TEXT NOT NULL,
                    capability TEXT NOT NULL,
                    required_class TEXT NOT NULL,
                    concurrency_group TEXT NOT NULL,
                    max_concurrency INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    input_revision TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL,
                    interruptions INTEGER NOT NULL DEFAULT 0,
                    next_retry_at TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    heartbeat_at TEXT,
                    last_error_code TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES chain_run(run_id)
                );
                CREATE INDEX IF NOT EXISTS ix_chain_task_ready
                    ON chain_task(run_id, status, next_retry_at, phase);
                CREATE INDEX IF NOT EXISTS ix_chain_task_group
                    ON chain_task(run_id, concurrency_group, status);

                CREATE TABLE IF NOT EXISTS chain_attempt (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_key TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    claim_token TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    heartbeat_at TEXT NOT NULL,
                    finished_at TEXT,
                    worker_pid INTEGER,
                    process_started_at TEXT,
                    command_sha256 TEXT,
                    receipt_json TEXT,
                    error_code TEXT,
                    error_text TEXT,
                    UNIQUE (task_key, attempt_no),
                    FOREIGN KEY (task_key) REFERENCES chain_task(task_key)
                );
                CREATE INDEX IF NOT EXISTS ix_chain_attempt_running
                    ON chain_attempt(status, heartbeat_at);

                CREATE TABLE IF NOT EXISTS chain_event (
                    event_key TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    delivered INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    delivered_at TEXT,
                    FOREIGN KEY (run_id) REFERENCES chain_run(run_id)
                );
                CREATE INDEX IF NOT EXISTS ix_chain_event_pending
                    ON chain_event(run_id, delivered, created_at);
                """
            )
            # Additive migration for journals created before the interruption
            # budget existed.  A live journal must keep its history; the column
            # simply starts every existing task at zero interruptions.
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(chain_task)").fetchall()
            }
            if columns and "interruptions" not in columns:
                conn.execute(
                    "ALTER TABLE chain_task ADD COLUMN interruptions INTEGER NOT NULL DEFAULT 0"
                )
            # Same additive shape for the renewal budget (audit P1-3): a live
            # journal keeps its history and starts this run at zero renewals.
            run_columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(chain_run)").fetchall()
            }
            if run_columns and "manual_window_renewals" not in run_columns:
                conn.execute(
                    "ALTER TABLE chain_run ADD COLUMN manual_window_renewals"
                    " INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                "INSERT INTO journal_meta(key,value) VALUES('schema_version',?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(SCHEMA_VERSION),),
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except Exception:
                conn.rollback()
                raise
            else:
                conn.commit()

    def ensure_run(
        self,
        *,
        business_date: str,
        source_cutoff_at: str,
        sla_at: str,
        final_at: str,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        run_id = run_id or f"cardz-v2:{business_date}"
        now = iso()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO chain_run(
                    run_id,business_date,status,origin,source_cutoff_at,sla_at,final_at,
                    created_at,updated_at
                ) VALUES(?,?,'RUNNING','unknown',?,?,?,?,?)
                ON CONFLICT(business_date) DO NOTHING
                """,
                (run_id, business_date, source_cutoff_at, sla_at, final_at, now, now),
            )
            row = conn.execute(
                "SELECT * FROM chain_run WHERE business_date=?", (business_date,)
            ).fetchone()
        if row is None:
            raise JournalError(f"could not create run for {business_date}")
        if str(row["run_id"]) != run_id:
            # One journal holds one run per date.  A rehearsal label lives in
            # its own journal file; adopting another run's row here would let
            # it inherit finished tasks and publication state silently.
            raise JournalError(
                f"business date {business_date} already belongs to {row['run_id']}"
                f" in this journal; {run_id} needs its own journal"
            )
        return dict(row)

    def renew_manual_window(
        self,
        run_id: str,
        *,
        source_cutoff_at: str,
        sla_at: str,
        final_at: str,
    ) -> tuple[dict[str, Any], list[str], str]:
        """Explicitly extend one manual E2E run and resume only cutoff work.

        Normal ticks never call this method, so reconnecting cannot silently
        manufacture a fresh deadline.  Completed checkpoints stay immutable;
        only candidate work that the expired manual window closed is reopened.

        A run the previous window already marked FAILED_FINAL is revived to
        RUNNING here and only here: run_tick still early-returns on
        FAILED_FINAL, so an explicit operator renewal is the single way back.
        Returns the status the run had before this call.

        Two limits keep a renewal from being free (audit P1-3).  A run gets at
        most MANUAL_WINDOW_MAX_RENEWALS of them -- the next one is refused with
        a journaled MANUAL_WINDOW_RENEWAL_REFUSED and a JournalError, and this
        is the only one of the two that binds on the production path.  The
        second, later_iso, is a defensive API guard: see its docstring for why
        it never fires on a window built by manual_e2e_schedule, and for the
        source_cutoff re-arm that stays open.
        """

        cutoff = datetime.fromisoformat(source_cutoff_at)
        sla = datetime.fromisoformat(sla_at)
        final = datetime.fromisoformat(final_at)
        if not cutoff < sla < final:
            raise ValueError("manual E2E deadlines must be strictly ordered")
        now = iso()
        refused: int | None = None
        previous_status = ""
        reopened: list[str] = []
        updated: Any = None
        with self.transaction() as conn:
            run = conn.execute(
                "SELECT * FROM chain_run WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                raise JournalError(f"run not found: {run_id}")
            if run["publication_status"]:
                raise JournalError("a published run cannot renew its manual E2E window")
            renewals = int(run["manual_window_renewals"] or 0)
            if renewals >= MANUAL_WINDOW_MAX_RENEWALS:
                # Journalled inside the transaction and raised after it commits,
                # so the refusal survives even though the renewal does not.
                conn.execute(
                    """
                    INSERT INTO chain_event(event_key,run_id,event_type,payload_json,created_at)
                    VALUES(?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING
                    """,
                    (
                        f"{run_id}:MANUAL_WINDOW_RENEWAL_REFUSED:{renewals}:{now}",
                        run_id,
                        "MANUAL_WINDOW_RENEWAL_REFUSED",
                        canonical_json({
                            "runId": run_id,
                            "errorCode": "MANUAL_WINDOW_RENEWAL_LIMIT",
                            "renewals": renewals,
                            "maxRenewals": MANUAL_WINDOW_MAX_RENEWALS,
                            "finalAt": str(run["final_at"]),
                            "requestedFinalAt": final_at,
                            "nextRetry": "next business date",
                        }).decode(),
                        now,
                    ),
                )
                refused = renewals
            else:
                source_cutoff_at = later_iso(source_cutoff_at, str(run["source_cutoff_at"]))
                sla_at = later_iso(sla_at, str(run["sla_at"]))
                final_at = later_iso(final_at, str(run["final_at"]))
                previous_status = str(run["status"] or "")
                if previous_status == "FAILED_FINAL":
                    conn.execute(
                        "UPDATE chain_run SET status='RUNNING',updated_at=? WHERE run_id=?",
                        (now, run_id),
                    )
                conn.execute(
                    """
                    UPDATE chain_run
                    SET source_cutoff_at=?,sla_at=?,final_at=?,
                        manual_window_renewals=manual_window_renewals+1,updated_at=?
                    WHERE run_id=?
                    """,
                    (source_cutoff_at, sla_at, final_at, now, run_id),
                )
                rows = conn.execute(
                    """
                    SELECT task_key FROM chain_task
                    WHERE run_id=? AND status='DEGRADED'
                      AND attempts<max_attempts
                      AND (
                        (phase='candidate-source' AND last_error_code='CANDIDATE_SOURCE_CUTOFF')
                        OR
                        (phase='activation' AND last_error_code='CANDIDATE_ACTIVATION_CUTOFF')
                      )
                    ORDER BY created_at,task_key
                    """,
                    (run_id,),
                ).fetchall()
                reopened = [str(row["task_key"]) for row in rows]
                if reopened:
                    placeholders = ",".join("?" for _ in reopened)
                    conn.execute(
                        f"""
                        UPDATE chain_task
                        SET status='INTERRUPTED',next_retry_at=?,lease_token=NULL,
                            lease_expires_at=NULL,interruptions=0,
                            last_error='explicit manual E2E window renewal reopened cutoff work',
                            updated_at=?
                        WHERE task_key IN ({placeholders})
                        """,
                        (now, now, *reopened),
                    )
                updated = conn.execute(
                    "SELECT * FROM chain_run WHERE run_id=?", (run_id,)
                ).fetchone()
        if refused is not None:
            raise JournalError(
                f"manual E2E window for {run_id} was already renewed {refused} times"
                f" (max {MANUAL_WINDOW_MAX_RENEWALS}); let it reach FAILED_FINAL and"
                f" start the next business date instead of extending this one"
            )
        return dict(updated), reopened, previous_status

    def register_provenance(
        self,
        run_id: str,
        receipt: Mapping[str, Any],
    ) -> tuple[str, bool]:
        origin = classify_provenance(receipt)
        try:
            record_id = int(
                receipt.get("event_record_id") or receipt.get("eventRecordId") or 0
            )
        except (TypeError, ValueError, OverflowError):
            record_id = 0
        instance_id = str(receipt.get("instance_id") or receipt.get("instanceId") or "")
        if record_id > 0:
            event_key = f"origin:{record_id}:{instance_id}"
        else:
            event_key = f"origin:manual:{sha256(receipt)}"
        now = iso()
        with self.transaction() as conn:
            inserted = conn.execute(
                """
                INSERT INTO chain_event(
                    event_key,run_id,event_type,payload_json,delivered,created_at,delivered_at
                ) VALUES(?,?,?,?,1,?,?) ON CONFLICT(event_key) DO NOTHING
                """,
                (
                    event_key, run_id, f"origin.{origin}",
                    canonical_json(receipt).decode(), now, now,
                ),
            ).rowcount == 1
            if inserted:
                if origin == "scheduled":
                    try:
                        event_id = int(
                            receipt.get("event_id") or receipt.get("eventId") or 0
                        )
                    except (TypeError, ValueError, OverflowError):
                        event_id = 0
                    bump_107 = 1 if event_id == 107 else 0
                    conn.execute(
                        """
                        UPDATE chain_run
                        SET scheduled_event_107_count=scheduled_event_107_count+?,
                            origin=CASE WHEN manual_intervention_count=0 THEN 'scheduled' ELSE origin END,
                            updated_at=?
                        WHERE run_id=?
                        """,
                        (bump_107, now, run_id),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE chain_run
                        SET manual_intervention_count=manual_intervention_count+1,
                            origin='manual',proven_autonomous=0,updated_at=?
                        WHERE run_id=?
                        """,
                        (now, run_id),
                    )
                # Autonomy is derived evidence, not a sticky badge.  A manual
                # intervention recorded against either day of an already-
                # proven pair must invalidate the later day's proof too.
                proof_rows = [
                    dict(row) for row in conn.execute(
                        """
                        SELECT run_id,business_date,status,
                               manual_intervention_count,scheduled_event_107_count
                        FROM chain_run ORDER BY business_date
                        """
                    ).fetchall()
                ]
                for proof_row in proof_rows:
                    proven = autonomous_proven(
                        date.fromisoformat(str(proof_row["business_date"])),
                        proof_rows,
                    )
                    conn.execute(
                        "UPDATE chain_run SET proven_autonomous=? WHERE run_id=?",
                        (1 if proven else 0, proof_row["run_id"]),
                    )
        return origin, inserted

    def add_task(
        self,
        task: SourceTask,
        *,
        phase: str,
        required_class: str,
        concurrency_group: str,
        max_concurrency: int,
        max_attempts: int,
        payload: Mapping[str, Any] | None = None,
    ) -> bool:
        now = iso()
        body = {
            "runId": task.run_id,
            "businessDate": task.business_date,
            "sourceCode": task.source_code,
            "capability": task.capability,
            "variantId": task.variant_id,
            "externalId": task.external_id,
            "shard": task.shard,
            "inputRevision": task.input_revision,
            **dict(payload or {}),
        }
        with self.transaction() as conn:
            return conn.execute(
                """
                INSERT INTO chain_task(
                    task_key,run_id,phase,source_code,capability,required_class,
                    concurrency_group,max_concurrency,status,input_revision,payload_json,
                    checkpoint_json,max_attempts,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,'PENDING',?,?,?,?,?,?)
                ON CONFLICT(task_key) DO NOTHING
                """,
                (
                    task.idempotency_key, task.run_id, phase, task.source_code,
                    task.capability, required_class, concurrency_group,
                    int(max_concurrency), task.input_revision,
                    canonical_json(body).decode(),
                    canonical_json(dict(task.checkpoint)).decode(),
                    int(max_attempts), now, now,
                ),
            ).rowcount == 1

    def add_raw_task(
        self,
        *,
        run_id: str,
        business_date: str,
        phase: str,
        source_code: str,
        capability: str,
        required_class: str,
        concurrency_group: str,
        max_concurrency: int = 1,
        max_attempts: int = 1,
        input_revision: str = "1",
        payload: Mapping[str, Any] | None = None,
        shard: str = "all",
    ) -> str:
        task = SourceTask(
            run_id=run_id,
            business_date=business_date,
            source_code=source_code,
            capability=capability,
            shard=shard,
            input_revision=input_revision,
        )
        self.add_task(
            task,
            phase=phase,
            required_class=required_class,
            concurrency_group=concurrency_group,
            max_concurrency=max_concurrency,
            max_attempts=max_attempts,
            payload=payload,
        )
        return task.idempotency_key

    def claim_ready(
        self,
        run_id: str,
        *,
        phases: Iterable[str] | None = None,
        lease_seconds: int = 90,
        limit: int = 32,
        now: datetime | None = None,
        max_interruptions: int | None = None,
    ) -> list[dict[str, Any]]:
        clock = now or utc_now()
        now_text = iso(clock)
        phase_list = tuple(phases or ())
        budget = int(
            default_max_interruptions() if max_interruptions is None else max_interruptions
        )
        claimable = ",".join(f"'{state}'" for state in CLAIMABLE_TASK_STATES)
        with self.transaction() as conn:
            # Both budgets are enforced here, not only at failure time: an
            # INTERRUPTED task that already spent its attempts or its
            # interruption budget must never be handed out again.
            where = (
                f"run_id=? AND status IN ({claimable})"
                " AND attempts<max_attempts AND interruptions<?"
                " AND (next_retry_at IS NULL OR next_retry_at<=?)"
            )
            params: list[Any] = [run_id, budget, now_text]
            if phase_list:
                where += f" AND phase IN ({','.join('?' for _ in phase_list)})"
                params.extend(phase_list)
            candidates = conn.execute(
                f"SELECT * FROM chain_task WHERE {where} ORDER BY created_at,task_key",
                params,
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in candidates:
                if len(claimed) >= int(limit):
                    break
                running = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM chain_task
                    WHERE run_id=? AND concurrency_group=? AND status='RUNNING'
                    """,
                    (run_id, row["concurrency_group"]),
                ).fetchone()["n"]
                if int(running) >= int(row["max_concurrency"]):
                    continue
                claim = secrets.token_hex(24)
                attempt = int(row["attempts"]) + 1
                # A refunded contention attempt (finish_failure below) leaves
                # the budget counter below the trail, so the trail numbers
                # itself: attempt_no is UNIQUE per task and must never be
                # reused, while `attempts` counts only what the budget spends.
                attempt_no = int(
                    conn.execute(
                        "SELECT COALESCE(MAX(attempt_no),0) AS n FROM chain_attempt"
                        " WHERE task_key=?",
                        (row["task_key"],),
                    ).fetchone()["n"]
                ) + 1
                expires = iso(clock + timedelta(seconds=int(lease_seconds)))
                changed = conn.execute(
                    """
                    UPDATE chain_task
                    SET status='RUNNING',attempts=?,lease_token=?,lease_expires_at=?,
                        heartbeat_at=?,updated_at=?
                    WHERE task_key=? AND status IN ({claimable})
                    """.format(claimable=claimable),
                    (attempt, claim, expires, now_text, now_text, row["task_key"]),
                ).rowcount
                if changed != 1:
                    continue
                conn.execute(
                    """
                    INSERT INTO chain_attempt(
                        task_key,attempt_no,claim_token,status,started_at,heartbeat_at
                    ) VALUES(?,?,?,'RUNNING',?,?)
                    """,
                    (row["task_key"], attempt_no, claim, now_text, now_text),
                )
                current = conn.execute(
                    "SELECT * FROM chain_task WHERE task_key=?", (row["task_key"],)
                ).fetchone()
                claimed.append(dict(current))
            return claimed

    def validate_claim(self, task_key: str, claim_token: str) -> dict[str, Any]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT t.*,a.attempt_no,a.worker_pid,a.process_started_at
                FROM chain_task t INNER JOIN chain_attempt a
                  ON a.task_key=t.task_key AND a.claim_token=t.lease_token
                WHERE t.task_key=? AND t.lease_token=?
                  AND t.status='RUNNING' AND a.status='RUNNING'
                """,
                (task_key, claim_token),
            ).fetchone()
        if row is None:
            raise ClaimLost(f"V2 task claim is not active: {task_key}")
        return dict(row)

    def heartbeat(
        self,
        task_key: str,
        claim_token: str,
        *,
        lease_seconds: int = 90,
        worker_pid: int | None = None,
        process_started_at: str | None = None,
        command_sha256: str | None = None,
        checkpoint: Any = None,
    ) -> None:
        now = utc_now()
        now_text = iso(now)
        expires = iso(now + timedelta(seconds=int(lease_seconds)))
        with self.transaction() as conn:
            changed = conn.execute(
                """
                UPDATE chain_task SET heartbeat_at=?,lease_expires_at=?,
                    checkpoint_json=CASE WHEN ? IS NULL THEN checkpoint_json ELSE ? END,
                    updated_at=?
                WHERE task_key=? AND lease_token=? AND status='RUNNING'
                """,
                (
                    now_text, expires,
                    None if checkpoint is None else 1,
                    None if checkpoint is None else canonical_json(checkpoint).decode(),
                    now_text, task_key, claim_token,
                ),
            ).rowcount
            if changed != 1:
                raise ClaimLost(f"heartbeat lost V2 task claim: {task_key}")
            conn.execute(
                """
                UPDATE chain_attempt SET heartbeat_at=?,
                    worker_pid=COALESCE(?,worker_pid),
                    process_started_at=COALESCE(?,process_started_at),
                    command_sha256=COALESCE(?,command_sha256)
                WHERE claim_token=? AND status='RUNNING'
                """,
                (now_text, worker_pid, process_started_at, command_sha256, claim_token),
            )

    def finish_success(
        self,
        task_key: str,
        claim_token: str,
        result: Mapping[str, Any],
        *,
        degraded: bool = False,
    ) -> None:
        now = iso()
        status = "DEGRADED" if degraded else "COMPLETED"
        receipt = canonical_json(result).decode()
        with self.transaction() as conn:
            changed = conn.execute(
                """
                UPDATE chain_task SET status=?,result_json=?,lease_token=NULL,
                    lease_expires_at=NULL,next_retry_at=NULL,last_error_code=NULL,
                    last_error=NULL,updated_at=?
                WHERE task_key=? AND lease_token=? AND status='RUNNING'
                """,
                (status, receipt, now, task_key, claim_token),
            ).rowcount
            if changed != 1:
                raise ClaimLost(f"finish lost V2 task claim: {task_key}")
            conn.execute(
                """
                UPDATE chain_attempt SET status=?,finished_at=?,receipt_json=?
                WHERE claim_token=? AND status='RUNNING'
                """,
                (status, now, receipt, claim_token),
            )

    def finish_failure(
        self,
        task_key: str,
        claim_token: str,
        *,
        decision: RetryDecision,
        error_text: str,
        receipt: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> str:
        clock = now or utc_now()
        now_text = iso(clock)
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=? AND lease_token=?",
                (task_key, claim_token),
            ).fetchone()
            if task is None or task["status"] != "RUNNING":
                raise ClaimLost(f"failure lost V2 task claim: {task_key}")
            attempt = int(task["attempts"])
            # Retry ladders belong to an error class, not to every unrelated
            # failure the task has ever seen.  A source parser repair followed
            # by its first MySQL timeout must still receive MySQL attempt 1;
            # otherwise prior SOURCE_FAILED attempts silently exhaust infra
            # recovery before the infra error even occurs.
            previous_same_error = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM chain_attempt
                    WHERE task_key=? AND error_code=?
                    """,
                    (task_key, decision.error_code),
                ).fetchone()["n"]
            )
            error_attempt = previous_same_error + 1
            delay = decision.delay_for_attempt(error_attempt)
            # Contention never ran the work -- the single-flight holder was
            # still working -- so it may not spend the failure budget.
            # claim_ready incremented `attempts` at CLAIM time; this gives that
            # increment back, and the chain_attempt row stays, so the trail is
            # complete while seven REAL failures still park the task.
            attempts_after = attempt - 1 if decision.contention and attempt > 0 else attempt
            exhausted = not decision.contention and attempt >= int(task["max_attempts"])
            terminal = decision.terminal or delay is None or exhausted
            status = "TERMINAL" if terminal else "RETRY"
            next_retry = None if terminal else iso(clock + timedelta(seconds=int(delay)))
            result_json = None if receipt is None else canonical_json(receipt).decode()
            conn.execute(
                """
                UPDATE chain_task SET status=?,result_json=COALESCE(?,result_json),
                    next_retry_at=?,attempts=?,lease_token=NULL,lease_expires_at=NULL,
                    last_error_code=?,last_error=?,updated_at=?
                WHERE task_key=? AND lease_token=?
                """,
                (
                    status, result_json, next_retry, attempts_after, decision.error_code,
                    error_text[-8000:], now_text, task_key, claim_token,
                ),
            )
            conn.execute(
                """
                UPDATE chain_attempt SET status=?,finished_at=?,receipt_json=?,
                    error_code=?,error_text=?
                WHERE claim_token=? AND status='RUNNING'
                """,
                (
                    status, now_text, result_json, decision.error_code,
                    error_text[-8000:], claim_token,
                ),
            )
            return status

    def reopen_retryable_terminal(
        self,
        task_key: str,
        *,
        decision: RetryDecision,
        now: datetime | None = None,
    ) -> bool:
        """Repair a terminal verdict made by the former global-attempt policy."""

        if decision.terminal:
            return False
        clock = now or utc_now()
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=?", (task_key,)
            ).fetchone()
            if (
                task is None
                or task["status"] != "TERMINAL"
                or int(task["attempts"]) >= int(task["max_attempts"])
            ):
                return False
            # A classifier repair must be able to recover a verdict written by
            # the old classifier.  Re-label only the latest failed attempt: its
            # error text is unchanged, while retry accounting now follows the
            # error class that text actually represents.
            previous_code = str(task["last_error_code"] or "")
            if previous_code != decision.error_code:
                latest = conn.execute(
                    """
                    SELECT id FROM chain_attempt
                    WHERE task_key=? AND status='TERMINAL'
                    ORDER BY attempt_no DESC LIMIT 1
                    """,
                    (task_key,),
                ).fetchone()
                if latest is not None:
                    conn.execute(
                        "UPDATE chain_attempt SET error_code=? WHERE id=?",
                        (decision.error_code, int(latest["id"])),
                    )
            same_error_attempts = int(
                conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM chain_attempt
                    WHERE task_key=? AND error_code=?
                    """,
                    (task_key, decision.error_code),
                ).fetchone()["n"]
            )
            delay = decision.delay_for_attempt(same_error_attempts)
            if delay is None:
                return False
            retry_at = iso(clock + timedelta(seconds=int(delay)))
            changed = conn.execute(
                """
                UPDATE chain_task
                SET status='RETRY',next_retry_at=?,last_error_code=?,updated_at=?
                WHERE task_key=? AND status='TERMINAL'
                """,
                (retry_at, decision.error_code, iso(clock), task_key),
            ).rowcount
            return changed == 1

    def ensure_max_attempts(self, task_key: str, minimum: int) -> bool:
        """Raise, never shrink, a durable task's total safety budget."""

        if int(minimum) < 1:
            raise ValueError("minimum attempts must be positive")
        with self.transaction() as conn:
            changed = conn.execute(
                """
                UPDATE chain_task SET max_attempts=?,updated_at=?
                WHERE task_key=? AND max_attempts<?
                """,
                (int(minimum), iso(), task_key, int(minimum)),
            ).rowcount
            return changed == 1

    def reclassify_retry(
        self,
        task_key: str,
        *,
        decision: RetryDecision,
        now: datetime | None = None,
    ) -> bool:
        """Apply a corrected error class and its own retry clock to RETRY work."""

        if decision.terminal:
            return False
        clock = now or utc_now()
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=?", (task_key,)
            ).fetchone()
            if (
                task is None
                or str(task["status"]) != "RETRY"
                or str(task["last_error_code"] or "") == decision.error_code
            ):
                return False
            latest = conn.execute(
                """
                SELECT id FROM chain_attempt
                WHERE task_key=? AND status='RETRY'
                ORDER BY attempt_no DESC LIMIT 1
                """,
                (task_key,),
            ).fetchone()
            if latest is None:
                return False
            conn.execute(
                "UPDATE chain_attempt SET error_code=? WHERE id=?",
                (decision.error_code, int(latest["id"])),
            )
            same_error_attempts = int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM chain_attempt WHERE task_key=? AND error_code=?",
                    (task_key, decision.error_code),
                ).fetchone()["n"]
            )
            delay = decision.delay_for_attempt(same_error_attempts)
            if delay is None:
                return False
            changed = conn.execute(
                """
                UPDATE chain_task SET last_error_code=?,next_retry_at=?,updated_at=?
                WHERE task_key=? AND status='RETRY'
                """,
                (
                    decision.error_code,
                    iso(clock + timedelta(seconds=int(delay))),
                    iso(clock),
                    task_key,
                ),
            ).rowcount
            return changed == 1

    def reopen_successful_tasks_before(
        self,
        run_id: str,
        capabilities: Iterable[str],
        *,
        dependency_updated_at: str,
        reason: str,
    ) -> list[str]:
        """Re-run downstream results that predate a newly completed dependency."""

        selected = tuple(sorted({str(value) for value in capabilities if str(value)}))
        if not selected:
            return []
        now_text = iso()
        placeholders = ",".join("?" for _ in selected)
        with self.transaction() as conn:
            rows = conn.execute(
                f"""
                SELECT task_key FROM chain_task
                WHERE run_id=? AND capability IN ({placeholders})
                  AND status IN ('COMPLETED','DEGRADED','SKIPPED')
                  AND updated_at<?
                ORDER BY created_at,task_key
                """,
                (run_id, *selected, dependency_updated_at),
            ).fetchall()
            task_keys = [str(row["task_key"]) for row in rows]
            if task_keys:
                key_placeholders = ",".join("?" for _ in task_keys)
                conn.execute(
                    f"""
                    UPDATE chain_task
                    SET status='INTERRUPTED',next_retry_at=?,lease_token=NULL,
                        lease_expires_at=NULL,last_error_code='DEPENDENCY_CHANGED',
                        interruptions=0,
                        max_attempts=CASE WHEN attempts>=max_attempts
                            THEN attempts+1 ELSE max_attempts END,
                        last_error=?,updated_at=?
                    WHERE task_key IN ({key_placeholders})
                    """,
                    (
                        now_text,
                        reason[-8000:],
                        now_text,
                        *task_keys,
                    ),
                )
            return task_keys

    def interrupt_claim(
        self,
        task_key: str,
        claim_token: str,
        *,
        reason: str,
        now: datetime | None = None,
        max_interruptions: int | None = None,
    ) -> str:
        """Return the task to the queue under an explicit interruption budget.

        An unbudgeted interruption is an infinite loop: every tick kills the
        same worker, resets the retry clock to now, and the task is claimed
        again forever.  Each interruption therefore costs budget and backs off;
        an exhausted task parks and waits for an operator `unpark`.
        """

        clock = now or utc_now()
        now_text = iso(clock)
        budget = int(
            default_max_interruptions() if max_interruptions is None else max_interruptions
        )
        with self.transaction() as conn:
            task = conn.execute(
                """
                SELECT * FROM chain_task
                WHERE task_key=? AND lease_token=? AND status='RUNNING'
                """,
                (task_key, claim_token),
            ).fetchone()
            if task is None:
                raise ClaimLost(f"interrupt lost V2 task claim: {task_key}")
            interruptions = int(task["interruptions"] or 0) + 1
            attempts = int(task["attempts"] or 0)
            max_attempts = int(task["max_attempts"] or 1)
            exhausted = interruptions >= budget or attempts >= max_attempts
            status = "PARKED" if exhausted else "INTERRUPTED"
            error_code = "WORKER_PARKED" if exhausted else "WORKER_INTERRUPTED"
            next_retry = (
                None if exhausted
                else iso(clock + timedelta(seconds=interrupt_backoff_seconds(interruptions)))
            )
            changed = conn.execute(
                """
                UPDATE chain_task SET status=?,next_retry_at=?,interruptions=?,
                    lease_token=NULL,lease_expires_at=NULL,last_error_code=?,
                    last_error=?,updated_at=?
                WHERE task_key=? AND lease_token=? AND status='RUNNING'
                """,
                (
                    status, next_retry, interruptions, error_code,
                    reason[-8000:], now_text, task_key, claim_token,
                ),
            ).rowcount
            if changed != 1:
                raise ClaimLost(f"interrupt lost V2 task claim: {task_key}")
            conn.execute(
                """
                UPDATE chain_attempt SET status='INTERRUPTED',finished_at=?,
                    error_code=?,error_text=?
                WHERE claim_token=? AND status='RUNNING'
                """,
                (now_text, error_code, reason[-8000:], claim_token),
            )
        return status

    def unpark(
        self,
        task_key: str,
        *,
        run_id: str,
        reason: str = "operator",
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Operator-only revival of settled work; history is kept intact.

        audit P2-14: scoped to one run.  claim_ready only ever claims inside
        the current run_id, so unparking a key pasted from another business
        date used to flip the row to READY and print TASK_UNPARKED for a lane
        no tick would ever pick up.  A foreign key returns None instead.
        """

        clock = now or utc_now()
        now_text = iso(clock)
        states = ",".join(f"'{state}'" for state in UNPARKABLE_TASK_STATES)
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=? AND run_id=?",
                (task_key, run_id),
            ).fetchone()
            if task is None or str(task["status"]) not in set(UNPARKABLE_TASK_STATES):
                return None
            attempts = int(task["attempts"] or 0)
            max_attempts = int(task["max_attempts"] or 1)
            granted = attempts + 1 if attempts >= max_attempts else max_attempts
            changed = conn.execute(
                f"""
                UPDATE chain_task SET status='READY',max_attempts=?,interruptions=0,
                    next_retry_at=?,lease_token=NULL,lease_expires_at=NULL,
                    last_error=?,updated_at=?
                WHERE task_key=? AND run_id=? AND status IN ({states})
                """,
                (
                    granted, now_text, f"unparked by operator: {reason}"[-8000:],
                    now_text, task_key, run_id,
                ),
            ).rowcount
            if changed != 1:
                return None
            row = dict(
                conn.execute(
                    "SELECT * FROM chain_task WHERE task_key=?", (task_key,)
                ).fetchone()
            )
        row["previousStatus"] = str(task["status"])
        row["previousMaxAttempts"] = max_attempts
        row["previousInterruptions"] = int(task["interruptions"] or 0)
        return row

    def retire(
        self,
        task_key: str,
        *,
        run_id: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Operator-only settlement of parked/terminal work that later work made
        unnecessary (a quote repair whose shortfall other repairs already
        closed).  The row becomes SKIPPED so the source barrier and the health
        roll-up treat it as settled; attempts, the attempt trail and the reason
        stay on the row.  Data gates downstream still decide on their own.

        Scoped to one run for the same reason as unpark (audit P2-14): retiring
        a key from another business date would settle a row today's barrier
        never reads, while the operator believes the lane was cleared."""

        clock = now or utc_now()
        now_text = iso(clock)
        states = ",".join(f"'{state}'" for state in RETIRABLE_TASK_STATES)
        with self.transaction() as conn:
            task = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=? AND run_id=?",
                (task_key, run_id),
            ).fetchone()
            if (
                task is None
                or str(task["status"]) not in set(RETIRABLE_TASK_STATES)
                or task["lease_token"]
            ):
                return None
            changed = conn.execute(
                f"""
                UPDATE chain_task SET status='SKIPPED',next_retry_at=NULL,
                    lease_token=NULL,lease_expires_at=NULL,
                    last_error_code='OPERATOR_RETIRED',last_error=?,updated_at=?
                WHERE task_key=? AND run_id=? AND status IN ({states})
                  AND lease_token IS NULL
                """,
                (
                    f"retired by operator: {reason}"[-8000:], now_text,
                    task_key, run_id,
                ),
            ).rowcount
            if changed != 1:
                return None
            row = dict(
                conn.execute(
                    "SELECT * FROM chain_task WHERE task_key=?", (task_key,)
                ).fetchone()
            )
        row["previousStatus"] = str(task["status"])
        return row

    def unparkable_tasks(self, run_id: str) -> list[dict[str, Any]]:
        states = ",".join(f"'{state}'" for state in UNPARKABLE_TASK_STATES)
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM chain_task WHERE run_id=? AND status IN ({states})
                ORDER BY created_at,task_key
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def expired_attempts(self, *, now: datetime | None = None) -> list[dict[str, Any]]:
        now_text = iso(now or utc_now())
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT t.*,a.worker_pid,a.process_started_at,a.command_sha256,
                       a.claim_token,a.attempt_no
                FROM chain_task t INNER JOIN chain_attempt a
                  ON a.task_key=t.task_key AND a.claim_token=t.lease_token
                WHERE t.status='RUNNING' AND a.status='RUNNING'
                  AND t.lease_expires_at<?
                ORDER BY t.lease_expires_at
                """,
                (now_text,),
            ).fetchall()
        return [dict(row) for row in rows]

    def degrade_unfinished_phase(
        self,
        run_id: str,
        phase: str,
        *,
        error_code: str,
        reason: str,
    ) -> int:
        """Close non-running optional work at its deterministic cutoff.

        Running attempts keep their lease and are allowed to finish.  Pending
        or retryable attempts cannot write late results after the activation
        candidate set has been snapshotted for this business date.
        """

        now = iso()
        with self.transaction() as conn:
            return conn.execute(
                """
                UPDATE chain_task
                SET status='DEGRADED',next_retry_at=NULL,lease_token=NULL,
                    lease_expires_at=NULL,last_error_code=?,last_error=?,updated_at=?
                WHERE run_id=? AND phase=?
                  AND required_class<>'core'
                  AND status IN ('PENDING','READY','RETRY','INTERRUPTED')
                """,
                (error_code, reason[-8000:], now, run_id, phase),
            ).rowcount

    def tasks(self, run_id: str, *, phase: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as conn:
            if phase is None:
                rows = conn.execute(
                    "SELECT * FROM chain_task WHERE run_id=? ORDER BY created_at,task_key",
                    (run_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM chain_task WHERE run_id=? AND phase=? ORDER BY task_key",
                    (run_id, phase),
                ).fetchall()
        return [dict(row) for row in rows]

    def task(self, task_key: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM chain_task WHERE task_key=?", (task_key,)
            ).fetchone()
        return None if row is None else dict(row)

    def run(self, run_id: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM chain_run WHERE run_id=?", (run_id,)).fetchone()
        return None if row is None else dict(row)

    def set_run_status(self, run_id: str, status: str) -> None:
        now = iso()
        with self.transaction() as conn:
            changed = conn.execute(
                "UPDATE chain_run SET status=?,updated_at=? WHERE run_id=?",
                (status, now, run_id),
            ).rowcount
            if changed != 1:
                raise JournalError(f"run not found: {run_id}")

    def mark_publication(
        self,
        run_id: str,
        *,
        status: str,
        generation_id: str,
        generated_at: str,
        content_sha256: str,
        active_count: int,
        degraded_sources: Iterable[str],
    ) -> bool:
        if status not in RUN_SUCCESS_STATES:
            raise ValueError(f"invalid publication status: {status}")
        now = iso()
        degraded = sorted(set(str(item) for item in degraded_sources))
        with self.transaction() as conn:
            current = conn.execute(
                "SELECT publication_status,generation_id FROM chain_run WHERE run_id=?",
                (run_id,),
            ).fetchone()
            if current is None:
                raise JournalError(f"run not found: {run_id}")
            if current["publication_status"]:
                if current["generation_id"] != generation_id:
                    raise JournalError("one business date cannot confirm two generations")
                return False
            conn.execute(
                """
                UPDATE chain_run SET status=?,publication_status=?,generation_id=?,
                    generated_at=?,content_sha256=?,active_count=?,degraded_sources_json=?,
                    completed_at=?,updated_at=? WHERE run_id=?
                """,
                (
                    status, status, generation_id, generated_at, content_sha256,
                    int(active_count), canonical_json(degraded).decode(), now, now, run_id,
                ),
            )
            run_row = conn.execute(
                "SELECT business_date FROM chain_run WHERE run_id=?", (run_id,)
            ).fetchone()
            current_date = date.fromisoformat(run_row["business_date"])
            evidence = conn.execute(
                """
                SELECT business_date,status,manual_intervention_count,
                       scheduled_event_107_count
                FROM chain_run WHERE status IN ('PUBLISHED','PUBLISHED_DEGRADED')
                ORDER BY business_date DESC LIMIT 10
                """
            ).fetchall()
            proven = autonomous_proven(current_date, (dict(row) for row in evidence))
            conn.execute(
                "UPDATE chain_run SET proven_autonomous=? WHERE run_id=?",
                (1 if proven else 0, run_id),
            )
            return True

    def add_event(
        self,
        run_id: str,
        event_type: str,
        dedupe_key: str,
        payload: Mapping[str, Any],
    ) -> bool:
        event_key = f"{run_id}:{event_type}:{dedupe_key}"
        with self.transaction() as conn:
            return conn.execute(
                """
                INSERT INTO chain_event(event_key,run_id,event_type,payload_json,created_at)
                VALUES(?,?,?,?,?) ON CONFLICT(event_key) DO NOTHING
                """,
                (event_key, run_id, event_type, canonical_json(payload).decode(), iso()),
            ).rowcount == 1

    def pending_events(self, run_id: str) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM chain_event
                WHERE run_id=? AND delivered=0 AND event_type NOT LIKE 'origin.%'
                ORDER BY created_at,event_key
                """,
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def mark_event_delivered(self, event_key: str) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE chain_event SET delivered=1,delivered_at=? WHERE event_key=?",
                (iso(), event_key),
            )

    def summary(self, run_id: str) -> dict[str, Any]:
        run = self.run(run_id)
        if run is None:
            raise JournalError(f"run not found: {run_id}")
        tasks = self.tasks(run_id)
        counts: dict[str, int] = {}
        for task in tasks:
            counts[task["status"]] = counts.get(task["status"], 0) + 1
        return {
            "run": run,
            "taskCounts": counts,
            "tasks": tasks,
        }
