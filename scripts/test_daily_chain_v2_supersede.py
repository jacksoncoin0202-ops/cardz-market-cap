#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supersede-in-place fixtures for CARDZ Daily Chain V2.

A business date that published is dead forever today: chain_run.business_date
is UNIQUE, Journal.ensure_run is the only writer, and the tick no-op guard
refuses to touch a PUBLISHED date.  Superseding archives that date's run so the
same date can run again with fresh data and publish a new generation.

Every check here proves the guard FIRES on the broken shape and stays quiet on
the healthy one.  No MySQL, no network, no browser: the journals live in a
private temp directory, the outbox is a recording fake, and the census harvest
is an injected callable that is never allowed to be the real subprocess.
"""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import daily_chain_v2 as chain_module  # noqa: E402
import daily_chain_v2_db as chain_db  # noqa: E402
import identity_census_stage as census_module  # noqa: E402
from daily_chain_v2 import (  # noqa: E402
    AUTO_SUPERSEDE_ENV,
    DailyChainV2,
    auto_supersede_enabled,
    build_health_document,
    maybe_auto_supersede,
    run_id_for,
    runtime_dir_for,
    source_barrier_ready,
    status_brief,
)
from daily_chain_v2_contract import (  # noqa: E402
    autonomous_proven,
    classify_provenance,
    list_v2_migrations,
    run_id_business_date,
    run_id_with_supersede_seq,
    supersede_seq_of,
    supersede_suffix,
    v2_schema_capabilities,
)
from daily_chain_v2_journal import (  # noqa: E402
    SUPERSEDE_FAMILIES,
    Journal,
    JournalError,
    iso,
)

DAY = date(2026, 8, 24)
DAY_TEXT = DAY.isoformat()
RUN_ID = f"cardz-v2:{DAY_TEXT}"
JST = timezone(timedelta(hours=9))
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-supersede-"))
SECONDS_PER_DAY = 86400.0


def cleanup() -> None:
    shutil.rmtree(WORKSPACE, ignore_errors=True)


def new_journal(name: str) -> Journal:
    journal = Journal(WORKSPACE / f"{name}.sqlite3")
    journal.initialise()
    return journal


def open_run(journal: Journal, *, run_id: str = RUN_ID, day: str = DAY_TEXT) -> dict[str, Any]:
    return journal.ensure_run(
        business_date=day,
        source_cutoff_at=f"{day}T01:15:00+00:00",
        sla_at=f"{day}T04:00:00+00:00",
        final_at=f"{day}T08:00:00+00:00",
        run_id=run_id,
    )


def plant_task(
    journal: Journal,
    run_id: str,
    *,
    source_code: str,
    capability: str,
    phase: str = "source",
    required_class: str = "core",
    finish: str | None = "COMPLETED",
    day: str = DAY_TEXT,
) -> str:
    key = journal.add_raw_task(
        run_id=run_id,
        business_date=day,
        phase=phase,
        source_code=source_code,
        capability=capability,
        required_class=required_class,
        concurrency_group=f"host:{source_code}",
        max_attempts=3,
        input_revision="rev-1",
        shard="all",
        payload={"kind": "source"},
    )
    if finish is None:
        return key
    claimed = [row for row in journal.claim_ready(run_id) if str(row["task_key"]) == key]
    assert claimed, f"could not claim planted task {key}"
    journal.finish_success(key, str(claimed[0]["lease_token"]), {"ok": True})
    if finish != "COMPLETED":
        with journal.transaction() as conn:
            conn.execute("UPDATE chain_task SET status=? WHERE task_key=?", (finish, key))
    return key


def live_rows(journal: Journal, table: str) -> list[dict[str, Any]]:
    with journal.connect() as conn:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}").fetchall()]


class FakeCursor:
    """Just enough pymysql DictCursor for insert_live_event's three statements."""

    def __init__(self, owner: "FakeConnection") -> None:
        self.owner = owner
        self._result: dict[str, Any] | None = None
        self.lastrowid = 0

    def execute(self, sql: str, params: Any = None) -> None:
        text = " ".join(str(sql).split())
        self.owner.statements.append((text, tuple(params or ())))
        if text.startswith("SELECT id,generation_id FROM publication_outbox"):
            self._result = self.owner.rows.get(str((params or ("",))[0]))
        elif text.startswith("SELECT id,event_key,generation_id FROM publication_outbox"):
            business_date, event_type = params
            self._result = next((
                row for row in self.owner.rows.values()
                if row["business_date"] == business_date
                and row["event_type"] == event_type
                and not row["superseded"]
            ), None)
        elif text.startswith("UPDATE publication_outbox SET superseded=1"):
            business_date, event_type, keep = params
            for key, row in self.owner.rows.items():
                if (
                    row["business_date"] == business_date
                    and row["event_type"] == event_type
                    and key != keep
                    and not row["superseded"]
                ):
                    row["superseded"] = 1
                    self.owner.marked.append(key)
        elif text.startswith("INSERT INTO publication_outbox"):
            self.owner.next_id += 1
            self.lastrowid = self.owner.next_id
            self.owner.rows[str(params[0])] = {
                "id": self.owner.next_id,
                "event_key": str(params[0]),
                "event_type": str(params[1]),
                "business_date": str(params[2]),
                "generation_id": str(params[4]),
                "superseded": 0,
            }

    def fetchone(self) -> dict[str, Any] | None:
        return self._result


class FakeConnection:
    def __init__(self, rows: dict[str, dict[str, Any]] | None = None) -> None:
        self.rows = dict(rows or {})
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self.marked: list[str] = []
        self.next_id = max((row["id"] for row in self.rows.values()), default=0)
        self.committed = 0
        self.rolled_back = 0
        self.closed = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def close(self) -> None:
        self.closed += 1

    def verbs(self) -> list[str]:
        return [text.split(" ", 1)[0] for text, _ in self.statements]


def with_fake_outbox(connection: FakeConnection) -> Any:
    saved_load, saved_db = chain_db.load_env, chain_db.db
    chain_db.load_env = lambda: None
    chain_db.db = lambda: connection

    class Restore:
        def __enter__(self) -> FakeConnection:
            return connection

        def __exit__(self, *_exc: Any) -> bool:
            chain_db.load_env, chain_db.db = saved_load, saved_db
            return False

    return Restore()


def touch_census(path: Path, *, age_days: float, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"gemrateId":"a"}\n', encoding="utf-8")
    stamp = now.timestamp() - age_days * SECONDS_PER_DAY
    os.utime(path, (stamp, stamp))


try:
    # ---------------------------------------------------- archive/delete roundtrip
    journal = new_journal("roundtrip")
    open_run(journal)
    key_done = plant_task(journal, RUN_ID, source_code="gemrate", capability="pop")
    key_open = plant_task(
        journal, RUN_ID, source_code="pricecharting", capability="quote", finish=None
    )
    journal.add_event(RUN_ID, "RUN_STARTED", "1", {"n": 1})
    journal.mark_publication(
        RUN_ID,
        status="PUBLISHED",
        generation_id="gen-one",
        generated_at=iso(),
        content_sha256="a" * 64,
        active_count=1200,
        degraded_sources=[],
    )
    before = {table: live_rows(journal, table) for table in SUPERSEDE_FAMILIES}
    assert all(before[table] for table in SUPERSEDE_FAMILIES), before

    receipt_dir = WORKSPACE / "receipts"
    result = journal.supersede_run(
        DAY_TEXT, reason="fresh gemrate pop", receipt_dir=receipt_dir
    )
    assert result["nextRunId"] == f"{RUN_ID}/2" and result["nextSupersedeSeq"] == 2
    assert Path(result["receiptPath"]).name == f"supersede-{DAY_TEXT}-seq1.json"
    for table in SUPERSEDE_FAMILIES:
        assert live_rows(journal, table) == [], f"{table} still live after supersede"
        archived = live_rows(journal, f"{table}_archive")
        assert len(archived) == len(before[table]), (table, len(archived))
        for original, copy in zip(before[table], archived):
            for column, value in original.items():
                assert copy[column] == value, (table, column, value, copy[column])
            assert copy["supersede_seq"] == 1
            assert copy["supersede_reason"] == "fresh gemrate pop"
            assert copy["superseded_at"] == result["supersededAt"]
        assert result["counts"][table] == len(before[table])
    assert live_rows(journal, "chain_run_archive")[0]["supersede_origin"] == "manual"

    receipt = json.loads(Path(result["receiptPath"]).read_text(encoding="utf-8"))
    assert receipt["contract"] == "cardz-daily-chain-supersede-v1"
    assert receipt["rows"].keys() == set(SUPERSEDE_FAMILIES)
    # Reconstruct: the receipt alone must refill an empty journal.
    rebuilt = new_journal("rebuilt")
    with rebuilt.transaction() as conn:
        for table in ("chain_run", "chain_task", "chain_attempt", "chain_event"):
            for row in receipt["rows"][table]:
                columns = ",".join(row.keys())
                marks = ",".join("?" for _ in row)
                conn.execute(
                    f"INSERT INTO {table}({columns}) VALUES({marks})", tuple(row.values())
                )
    for table in SUPERSEDE_FAMILIES:
        assert live_rows(rebuilt, table) == before[table], table
    print("POSITIVE_OK supersede archives every row family and its receipt rebuilds the run byte-for-byte")

    # ------------------------------------------------- task inheritance hazard
    # task_key is date-scoped ({date}:{source}:{capability}:{shard}:{sha}) and
    # add_task is ON CONFLICT DO NOTHING, so a chain_task row left behind hands
    # the rerun a finished stage it never ran.
    assert journal.task(key_done) is None, "archived task must not stay live"
    assert journal.task(key_open) is None
    next_run = f"{RUN_ID}/2"
    open_run(journal, run_id=next_run)
    replanned = plant_task(journal, next_run, source_code="gemrate", capability="pop", finish=None)
    assert replanned == key_done, "the rerun must reuse the date-scoped task key"
    fresh = journal.task(replanned)
    assert fresh is not None and str(fresh["status"]) == "PENDING", fresh
    assert str(fresh["run_id"]) == next_run, fresh["run_id"]
    assert int(fresh["attempts"]) == 0, fresh["attempts"]
    assert journal.claim_ready(next_run), "the rerun must be able to claim its own work"
    with journal.connect() as conn:
        attempts = [
            dict(row) for row in conn.execute(
                "SELECT * FROM chain_attempt WHERE task_key=? ORDER BY attempt_no",
                (key_done,),
            ).fetchall()
        ]
    # chain_attempt is UNIQUE(task_key,attempt_no) and the trail numbers itself
    # from MAX(attempt_no): a leftover row silently pushes the rerun's first
    # attempt to number 2 and leaves the previous generation's receipt in place.
    assert len(attempts) == 1, attempts
    assert int(attempts[0]["attempt_no"]) == 1, attempts[0]
    print("POSITIVE_OK a superseded date replans its date-scoped tasks from PENDING instead of inheriting them")

    # ----------------------------------------------------- run id parses everywhere
    assert supersede_seq_of(RUN_ID) == 1 and supersede_seq_of(f"{RUN_ID}/2") == 2
    assert supersede_seq_of(f"{RUN_ID}/11") == 11 and supersede_seq_of("") == 1
    assert supersede_suffix(1) == "" and supersede_suffix(3) == "/3"
    assert run_id_with_supersede_seq(f"{RUN_ID}/2", 3) == f"{RUN_ID}/3"
    assert run_id_with_supersede_seq(f"{RUN_ID}/2", 1) == RUN_ID
    assert run_id_business_date(f"{RUN_ID}/2") == DAY_TEXT
    assert run_id_business_date(f"cardz-v2:{DAY_TEXT}#A01") == DAY_TEXT
    assert run_id_for(DAY) == RUN_ID and run_id_for(DAY, "", 1) == RUN_ID
    assert run_id_for(DAY, "", 2) == f"{RUN_ID}/2"
    assert runtime_dir_for(DAY).name == DAY_TEXT
    assert runtime_dir_for(DAY, "", 2).name == f"{DAY_TEXT}-S2"
    assert runtime_dir_for(DAY, "A01").name == f"{DAY_TEXT}-A01"
    try:
        run_id_for(DAY, "A01", 2)
    except ValueError:
        pass
    else:  # a rehearsal that supersedes would publish under a label
        raise AssertionError("a rehearsal label must never carry a supersede generation")
    assert chain_db.live_event_key(DAY_TEXT, RUN_ID) == f"live.confirmed:{DAY_TEXT}"
    assert chain_db.live_event_key(DAY_TEXT, f"{RUN_ID}/2") == f"live.confirmed:{DAY_TEXT}/2"
    assert chain_db.LIVE_EVENT_KEY_RE.fullmatch(f"live.confirmed:{DAY_TEXT}/2")
    assert not chain_db.LIVE_EVENT_KEY_RE.fullmatch(f"live.confirmed:{DAY_TEXT}/0")

    # autonomous_proven keys a dict by business_date: two LIVE rows for one date
    # would silently drop one.  The archive is what keeps that invariant true.
    with journal.connect() as conn:
        per_date = conn.execute(
            "SELECT business_date,COUNT(*) AS n FROM chain_run GROUP BY business_date"
        ).fetchall()
    assert all(int(row["n"]) == 1 for row in per_date), [dict(r) for r in per_date]
    from daily_chain_v2_contract import previous_business_date  # noqa: E402

    yesterday = previous_business_date(DAY).isoformat()
    evidence = [
        {"business_date": DAY_TEXT, "status": "PUBLISHED",
         "manual_intervention_count": 0, "scheduled_event_107_count": 1},
        {"business_date": yesterday, "status": "PUBLISHED",
         "manual_intervention_count": 0, "scheduled_event_107_count": 1},
    ]
    assert autonomous_proven(DAY, evidence) is True
    laundered = [dict(evidence[0], manual_intervention_count=0)] + [
        dict(evidence[0], manual_intervention_count=2), evidence[1]
    ]
    # Same date twice: the LAST row wins the dict, which is exactly why the
    # superseded generation must not stay in chain_run.
    assert autonomous_proven(DAY, laundered) is False
    print("POSITIVE_OK the /N run id parses everywhere and one business date keeps exactly one live run row")

    # ------------------------------------------- accounting survives a supersede
    ledger = new_journal("ledger")
    open_run(ledger)
    with ledger.transaction() as conn:
        conn.execute(
            "UPDATE chain_run SET manual_intervention_count=2,scheduled_event_107_count=1"
            " WHERE run_id=?",
            (RUN_ID,),
        )
    ledger.supersede_run(DAY_TEXT, reason="operator rerun", receipt_dir=receipt_dir)
    reran = open_run(ledger, run_id=f"{RUN_ID}/2")
    # 2 carried + 1 for the operator supersede itself.
    assert int(reran["manual_intervention_count"]) == 3, reran["manual_intervention_count"]
    assert int(reran["scheduled_event_107_count"]) == 1, reran["scheduled_event_107_count"]
    ledger.supersede_run(
        DAY_TEXT, reason="auto align", receipt_dir=receipt_dir, origin="auto"
    )
    auto_reran = open_run(ledger, run_id=f"{RUN_ID}/3")
    # The automatic align adds nothing: it is not a human touching the run.
    assert int(auto_reran["manual_intervention_count"]) == 3, auto_reran
    assert int(auto_reran["scheduled_event_107_count"]) == 1, auto_reran
    history = ledger.supersede_history(DAY_TEXT)
    assert [entry["supersedeSeq"] for entry in history] == [1, 2], history
    assert [entry["supersedeOrigin"] for entry in history] == ["manual", "auto"], history
    assert ledger.live_supersede_seq(DAY_TEXT) == 3
    assert ledger.summary(f"{RUN_ID}/3")["supersede"]["seq"] == 3
    assert ledger.summary(f"{RUN_ID}/3")["supersede"]["archivedCount"] == 2
    assert status_brief(ledger, DAY, "", 3).startswith(f"{DAY_TEXT}/3 ")
    assert status_brief(ledger, DAY, "", 1).startswith(f"{DAY_TEXT} ")
    print("POSITIVE_OK a superseded date carries its manual and event-107 accounting into the rerun")

    # --------------------------------- supersede publishes, a rehearsal never does
    supersede_run_obj = SimpleNamespace(allow_publish=True, run_label="")
    rehearsal_run_obj = SimpleNamespace(allow_publish=True, run_label="A01")
    assert DailyChainV2.publication_allowed(supersede_run_obj) is True
    assert DailyChainV2.publication_allowed(rehearsal_run_obj) is False
    assert DailyChainV2.publication_allowed(
        SimpleNamespace(allow_publish=False, run_label="")
    ) is False
    plan_source = inspect.getsource(DailyChainV2.plan)
    # The publish leg must ask the method, not the raw flag: the flag alone
    # cannot tell a rehearsal from a supersede rerun.
    assert "if not self.publication_allowed():" in plan_source
    assert "if not self.allow_publish:" not in plan_source
    health_seq2 = build_health_document(
        journal, DAY, tick_phase="query", supersede_seq=2
    )
    assert health_seq2["supersede_seq"] == 2
    assert health_seq2["supersede_pending"] is True
    health_seq1 = build_health_document(journal, DAY, tick_phase="query")
    assert health_seq1["supersede_seq"] == 1 and health_seq1["supersede_pending"] is False
    print("POSITIVE_OK a supersede rerun keeps publication rights while a rehearsal is still gated")

    # ---------------------------------------------------- outbox supersede publish
    first_key = f"live.confirmed:{DAY_TEXT}"
    second_key = f"live.confirmed:{DAY_TEXT}/2"
    base_row = {
        "id": 7, "event_key": first_key, "event_type": "live.confirmed",
        "business_date": DAY_TEXT, "generation_id": "gen-one", "superseded": 0,
    }
    event_two = {
        "eventKey": second_key, "eventType": "live.confirmed", "businessDate": DAY_TEXT,
        "runId": f"{RUN_ID}/2", "generationId": "gen-two",
        "generatedAt": "2026-08-24T02:00:00+00:00", "activeCount": 1200,
        "degraded": False, "sourceHealth": {}, "liveUrl": "https://example.invalid",
        "contentSha256": "b" * 64, "occurredAt": "2026-08-24T02:00:01+00:00",
    }
    fake = FakeConnection({first_key: dict(base_row)})
    with with_fake_outbox(fake):
        event_id, inserted = chain_db.insert_live_event(event_two)
    assert inserted is True and event_id == 8, (event_id, inserted)
    assert fake.marked == [first_key], fake.marked
    assert fake.rows[first_key]["superseded"] == 1
    assert fake.rows[second_key]["superseded"] == 0
    assert fake.verbs() == ["SELECT", "SELECT", "UPDATE", "INSERT"], fake.verbs()
    # Same transaction: one commit, after the update AND the insert.
    assert fake.committed == 1 and fake.rolled_back == 0

    # A NON-supersede duplicate for the same date is refused exactly as today.
    dup = dict(event_two, eventKey=first_key, runId=RUN_ID, generationId="gen-other")
    clash = FakeConnection({first_key: dict(base_row)})
    with with_fake_outbox(clash):
        try:
            chain_db.insert_live_event(dup)
        except RuntimeError as error:
            assert "another generation" in str(error), error
        else:
            raise AssertionError("a second generation of one date must still be refused")
    assert clash.verbs() == ["SELECT"], clash.verbs()
    assert clash.rolled_back == 1 and clash.rows[first_key]["superseded"] == 0

    # Replaying the same generation stays idempotent and marks nothing.
    replay = FakeConnection({first_key: dict(base_row)})
    with with_fake_outbox(replay):
        replay_id, replay_inserted = chain_db.insert_live_event(
            dict(event_two, eventKey=first_key, runId=RUN_ID, generationId="gen-one")
        )
    assert (replay_id, replay_inserted) == (7, False)
    assert replay.marked == [] and replay.verbs() == ["SELECT"]

    # A supersede that rebuilt byte-identical output explains the terminal
    # condition before touching the current row; it must not leak IntegrityError
    # after consuming the worker's entire retry budget.
    unchanged = FakeConnection({first_key: dict(base_row)})
    with with_fake_outbox(unchanged):
        try:
            chain_db.insert_live_event(dict(event_two, generationId="gen-one"))
        except RuntimeError as error:
            assert "unchanged generation" in str(error), error
            assert "publication already carries these bytes" in str(error), error
        else:
            raise AssertionError("a byte-identical supersede must fail before UPDATE")
    assert unchanged.verbs() == ["SELECT", "SELECT"], unchanged.verbs()
    assert unchanged.marked == [] and unchanged.rows[first_key]["superseded"] == 0
    assert unchanged.committed == 0 and unchanged.rolled_back == 1

    # A key whose generation disagrees with its run id never reaches MySQL.
    forged = FakeConnection({})
    with with_fake_outbox(forged):
        try:
            chain_db.insert_live_event(dict(event_two, eventKey=first_key))
        except ValueError as error:
            assert "generation" in str(error), error
        else:
            raise AssertionError("a /2 run must not publish under the generation-1 key")
    assert forged.statements == []
    print("POSITIVE_OK supersede publication is atomic and byte-identical reruns fail before replacing live")

    # ------------------------------------------------ auto-supersede is OFF by default
    stale_journal = new_journal("auto")
    open_run(stale_journal)
    created_yesterday = (
        datetime(2026, 8, 24, 3, 0, tzinfo=JST) - timedelta(days=1)
    ).astimezone(timezone.utc).isoformat()
    with stale_journal.transaction() as conn:
        conn.execute(
            "UPDATE chain_run SET status='PUBLISHED',publication_status='PUBLISHED',"
            "created_at=? WHERE run_id=?",
            (created_yesterday, RUN_ID),
        )
    # The real shape classify_provenance calls automatic: Task Scheduler
    # parent, the V2 task name, no fresh event 107 in the lookback.
    scheduled = {
        "parent_process": "svchost.exe",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "event_id": None,
    }
    manual_tick = {"parent_process": "pwsh.exe", "task_name": "", "event_id": 110}
    assert classify_provenance(scheduled) == "scheduled"
    assert classify_provenance(manual_tick) == "manual"
    now_jst = datetime(2026, 8, 24, 4, 0, tzinfo=JST)

    class CountingJournal:
        """Any read at all would mean the flag-off tick is not byte-identical."""

        def __init__(self, inner: Journal) -> None:
            self.inner = inner
            self.calls: list[str] = []

        def __getattr__(self, name: str) -> Any:
            self.calls.append(name)
            return getattr(self.inner, name)

    saved_flag = os.environ.pop(AUTO_SUPERSEDE_ENV, None)
    try:
        counting = CountingJournal(stale_journal)
        assert auto_supersede_enabled() is False
        assert maybe_auto_supersede(counting, DAY, scheduled, now=now_jst) is None
        assert counting.calls == [], counting.calls
        for value in ("0", "false", "off", "no", " "):
            os.environ[AUTO_SUPERSEDE_ENV] = value
            assert auto_supersede_enabled() is False, value
            assert maybe_auto_supersede(counting, DAY, scheduled, now=now_jst) is None
        assert counting.calls == [], counting.calls
        assert stale_journal.run(RUN_ID) is not None

        os.environ[AUTO_SUPERSEDE_ENV] = "1"
        assert auto_supersede_enabled() is True
        # A manual tick never auto-aligns: that is an operator decision.
        assert maybe_auto_supersede(
            stale_journal, DAY, manual_tick, now=now_jst
        ) is None
        assert stale_journal.run(RUN_ID) is not None
        aligned = maybe_auto_supersede(stale_journal, DAY, scheduled, now=now_jst)
        assert aligned is not None and aligned["supersedeOrigin"] == "auto"
        assert aligned["nextRunId"] == f"{RUN_ID}/2"
        assert stale_journal.run(RUN_ID) is None
        # Hard cap: at most one automatic supersede per business date, ever.
        open_run(stale_journal, run_id=f"{RUN_ID}/2")
        with stale_journal.transaction() as conn:
            conn.execute(
                "UPDATE chain_run SET status='PUBLISHED',publication_status='PUBLISHED',"
                "created_at=? WHERE run_id=?",
                (created_yesterday, f"{RUN_ID}/2"),
            )
        assert maybe_auto_supersede(
            stale_journal, DAY, scheduled, now=now_jst + timedelta(days=1)
        ) is None
        try:
            stale_journal.supersede_run(
                DAY_TEXT, reason="second align", receipt_dir=receipt_dir, origin="auto"
            )
        except JournalError as error:
            assert "auto-supersede already used" in str(error), error
        else:
            raise AssertionError("a second automatic supersede of one date must be refused")
        # An operator can still rerun the date by hand.
        manual_again = stale_journal.supersede_run(
            DAY_TEXT, reason="operator override", receipt_dir=receipt_dir
        )
        assert manual_again["nextRunId"] == f"{RUN_ID}/3"
        # A run created TODAY is not one day ahead of the calendar.
        open_run(stale_journal, run_id=f"{RUN_ID}/3")
        with stale_journal.transaction() as conn:
            conn.execute(
                "UPDATE chain_run SET status='PUBLISHED',publication_status='PUBLISHED',"
                "created_at=? WHERE run_id=?",
                (now_jst.astimezone(timezone.utc).isoformat(), f"{RUN_ID}/3"),
            )
        assert maybe_auto_supersede(stale_journal, DAY, scheduled, now=now_jst) is None
    finally:
        if saved_flag is None:
            os.environ.pop(AUTO_SUPERSEDE_ENV, None)
        else:
            os.environ[AUTO_SUPERSEDE_ENV] = saved_flag
    tick_source = inspect.getsource(chain_module.main)
    assert "maybe_auto_supersede(journal, day, provenance)" in tick_source
    print("POSITIVE_OK auto-supersede reads nothing while the flag is off and aligns a stale date at most once")

    # --------------------------------------------------------- supersede refusals
    empty = new_journal("empty")
    for call in (
        lambda: empty.supersede_run(DAY_TEXT, reason="x", receipt_dir=receipt_dir),
        lambda: empty.supersede_preview(DAY_TEXT, receipt_dir=receipt_dir),
    ):
        try:
            call()
        except JournalError as error:
            assert "no live run" in str(error), error
        else:
            raise AssertionError("superseding a date with no live run must be refused")
    open_run(empty)
    for bad_reason in ("", "   "):
        try:
            empty.supersede_run(DAY_TEXT, reason=bad_reason, receipt_dir=receipt_dir)
        except ValueError as error:
            assert "reason" in str(error), error
        else:
            raise AssertionError("a supersede without a reason leaves the date no trail")
    try:
        empty.supersede_run(DAY_TEXT, reason="x", receipt_dir=receipt_dir, origin="sneaky")
    except ValueError as error:
        assert "origin" in str(error), error
    else:
        raise AssertionError("an unknown supersede origin must be refused")
    preview = empty.supersede_preview(DAY_TEXT, receipt_dir=receipt_dir)
    assert preview["nextRunId"] == f"{RUN_ID}/2" and preview["publicationStatus"] is None
    assert empty.run(RUN_ID) is not None, "a preview must write nothing"
    assert empty.live_supersede_seq(DAY_TEXT) == 1
    assert Journal(WORKSPACE / "never-created.sqlite3").live_supersede_seq(DAY_TEXT) == 1
    print("POSITIVE_OK supersede refuses a missing run, a blank reason and an unknown origin, and previews write nothing")

    # ------------------------------------------------------- identity census stage
    census_root = WORKSPACE / "census-root"
    census_file = census_module.census_path(census_root)
    now_census = datetime(2026, 8, 24, 6, 0, tzinfo=timezone.utc)
    calls: list[list[str]] = []

    def record(command: list[str], *, timeout: int, cwd: Path) -> dict[str, Any]:
        calls.append(list(command))
        touch_census(census_file, age_days=0.0, now=now_census)
        return {"exitCode": 0, "outputTail": "harvested"}

    touch_census(census_file, age_days=1.0, now=now_census)
    fresh_receipt = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT, runner=record
    )
    assert calls == [], "a fresh census must not shell out"
    assert fresh_receipt["refreshed"] is False
    assert fresh_receipt["skipReason"] == "census-fresh"
    assert 0.9 < float(fresh_receipt["ageDays"]) < 1.1, fresh_receipt["ageDays"]
    assert "censusNote" not in fresh_receipt
    assert json.loads(Path(fresh_receipt["receiptPath"]).read_text(encoding="utf-8")) == fresh_receipt

    touch_census(census_file, age_days=4.0, now=now_census)
    stale_receipt = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT, runner=record
    )
    assert len(calls) == 1, calls
    assert calls[0][-1] == "--all-sets", calls[0]
    assert calls[0][-2].endswith("gemrate_brute_harvest.py"), calls[0]
    assert stale_receipt["refreshed"] is True and stale_receipt["exitCode"] == 0
    assert float(stale_receipt["ageDays"]) < 0.1, stale_receipt["ageDays"]
    assert "censusNote" not in stale_receipt

    # A harvest that fails, or a census that is missing entirely, costs the day
    # a stale census and nothing else: this stage may never fail a run.
    def explode(command: list[str], *, timeout: int, cwd: Path) -> dict[str, Any]:
        raise RuntimeError("curl_cffi died")

    touch_census(census_file, age_days=9.0, now=now_census)
    failed = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT, runner=explode
    )
    assert failed["refreshed"] is False and "curl_cffi died" in failed["error"]
    assert "NOT evidence" in failed["censusNote"]
    nonzero = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT,
        runner=lambda *_a, **_k: {"exitCode": 3, "outputTail": "blocked"},
    )
    assert nonzero["refreshed"] is False and nonzero["exitCode"] == 3
    assert "censusNote" in nonzero
    missing_root = WORKSPACE / "no-census"
    missing = census_module.run_identity_census(
        root=missing_root, now=now_census, business_date=DAY_TEXT, runner=record
    )
    assert missing["ageDays"] is None and missing["censusMtime"] is None
    assert len(calls) == 2, "a missing census is stale and must be harvested"

    # Tick budget: a half-hour harvest is never started with minutes left.
    touch_census(census_file, age_days=9.0, now=now_census)
    starved = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT,
        budget_seconds=120.0, runner=explode,
    )
    assert starved["skipReason"] == "tick-budget-too-short"
    assert starved["refreshed"] is False and "censusNote" in starved
    roomy = census_module.run_identity_census(
        root=census_root, now=now_census, business_date=DAY_TEXT,
        budget_seconds=census_module.HARVEST_BUDGET_SECONDS + 1, runner=record,
    )
    assert roomy["refreshed"] is True and len(calls) == 3
    assert census_module.CENSUS_MAX_AGE_DAYS == 3.5
    print("POSITIVE_OK the census stage no-ops when fresh, harvests when stale, and never raises when the harvest fails")

    # ------------------------------------------------- census stage is planned, and optional
    census_block_start = plan_source.index('if self.stage_row("identity-census") is None:')
    census_block = plan_source[census_block_start:plan_source.index("repair_contracts = [", census_block_start)]
    assert 'phase="source"' in census_block and 'required_class="extra"' in census_block
    assert 'concurrency_group="host:gemrate"' in census_block
    assert "max_attempts=2" in census_block
    # No `return` in the block: planning the census must not end the pass and
    # starve every stage the same tick would otherwise plan.
    assert "return" not in census_block, census_block
    adapter_at = plan_source.index("payload=source_payload(adapter, task)")
    assert adapter_at < census_block_start, "the census must be planned after the adapters"
    stage_source = (ROOT / "pipelines" / "daily_chain_v2_stage.py").read_text(encoding="utf-8")
    assert 'sub.add_parser("identity-census")' in stage_source
    assert "census.set_defaults(func=stage_identity_census)" in stage_source
    assert "budget_seconds=stage_deadline_budget_seconds()" in stage_source

    cutoff = datetime(2026, 8, 24, 1, 15, tzinfo=timezone.utc)
    before_cutoff = cutoff - timedelta(minutes=30)
    core_done = {"source_code": "gemrate", "required_class": "core", "status": "COMPLETED"}
    census_pending = {
        "source_code": "system", "required_class": "extra", "status": "PENDING",
    }
    assert source_barrier_ready([core_done], now=before_cutoff, cutoff=cutoff) is True
    # The census is an orchestrator stage sitting in the source phase; it must
    # not hold the whole business date behind the core barrier until 10:15.
    assert source_barrier_ready(
        [core_done, census_pending], now=before_cutoff, cutoff=cutoff
    ) is True
    # A real optional SOURCE still does.
    assert source_barrier_ready(
        [core_done, {"source_code": "pricecharting", "required_class": "quote", "status": "PENDING"}],
        now=before_cutoff, cutoff=cutoff,
    ) is False
    assert source_barrier_ready([census_pending], now=before_cutoff, cutoff=cutoff) is False
    print("POSITIVE_OK the census stage is planned as optional source work and cannot hold the core barrier")

    # ---------------------------------------------------------------- migration 059
    migration = ROOT / "pipelines" / "migrations" / "059_daily_chain_v2_publication_supersede.mysql.sql"
    assert migration.is_file(), migration
    assert migration.name in list_v2_migrations(ROOT), list_v2_migrations(ROOT)[-3:]
    assert "schema-059" in v2_schema_capabilities(ROOT)
    sql = migration.read_text(encoding="utf-8")
    assert "ADD COLUMN superseded TINYINT(1) NOT NULL DEFAULT 0" in sql
    assert "DROP INDEX uq_publication_outbox_business_type" in sql
    assert "uq_publication_outbox_business_type_generation" in sql
    assert "(business_date, event_type, generation_id)" in sql
    assert "INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('059')" in sql
    # Idempotent: every ALTER is behind an information_schema probe, because the
    # runner replays a half-applied file.
    import db_runtime  # noqa: E402

    statements = [text.strip() for text in db_runtime.split_sql(sql)]
    alters = [text for text in statements if text.upper().startswith("ALTER TABLE")]
    assert alters == [], "a bare ALTER cannot be replayed: guard it with @ddl/PREPARE"
    assert statements.count("EXECUTE add_outbox_superseded") == 1
    assert sum(1 for text in statements if text.startswith("PREPARE")) == 3
    assert sum(1 for text in statements if text.startswith("DEALLOCATE PREPARE")) == 3
    assert sum(1 for text in statements if "information_schema" in text) == 3
    print("POSITIVE_OK migration 059 widens the outbox lock to the generation, idempotently, and registers itself")

    # ------------------------------------------------------------------- watchdog
    watchdog = (ROOT / "scripts" / "watchdog_live_release.ps1").read_text(encoding="utf-8")
    done_lines = [line for line in watchdog.splitlines() if line.strip().startswith("$done =")]
    assert len(done_lines) == 1, done_lines
    assert "$supersedePending = [bool]$hj.supersede_pending" in watchdog
    # A superseded date's PUBLISHED health document must not silence the
    # watchdog until 17:30 while the replacement run is still working.
    assert "-and (-not $supersedePending)" in done_lines[0], done_lines[0]
    print("POSITIVE_OK the watchdog re-arms on a superseded business date instead of reading it as done")

    assert "function Resolve-V2DayDirectory" in watchdog
    assert '$baseRunId = "cardz-v2:$businessDate"' in watchdog
    assert '"data\\runtime\\daily-chain-v2\\$businessDate-S$seq"' in watchdog
    assert '$v2RunId = [string]$hj.run_id' in watchdog
    assert "$v2Day = Resolve-V2DayDirectory $RepoRoot $todayJst $v2RunId" in watchdog
    assert '$v2Day = Join-Path $RepoRoot "data\\runtime\\daily-chain-v2\\$todayJst"' not in watchdog
    print("POSITIVE_OK the watchdog follows the exact health run lineage into supersede -SN artifacts")

finally:
    cleanup()
