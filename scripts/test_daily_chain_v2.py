#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline failure fixtures for CARDZ Marketcap Daily Chain V2.

No Telegram, browser, MySQL mutation, push, or deploy occurs here.
"""
from __future__ import annotations

import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2 import (  # noqa: E402
    aggregate_source_health,
    degraded_source_codes,
    source_payload,
    source_barrier_ready,
)
from daily_chain_v2_adapters import build_default_registry  # noqa: E402
from daily_chain_v2_contract import (  # noqa: E402
    AdapterRegistry,
    QuoteCandidate,
    QuotePolicyRow,
    SourceResult,
    SourceSpec,
    SourceTask,
    autonomous_proven,
    classify_error,
    classify_provenance,
    daily_generation_sha256,
    identity_disposition,
    list_v2_migrations,
    market_content_sha256,
    publication_decision,
    select_quote,
    sha256,
    v2_schema_capabilities,
)
from daily_chain_v2_db import (  # noqa: E402
    MemoryOutbox,
    build_live_event,
)
from daily_chain_v2_journal import Journal, iso  # noqa: E402


class FakeAdapter:
    def __init__(
        self,
        code: str,
        timeline: list[tuple[str, str, float]],
        lock: threading.Lock,
        *,
        group: str | None = None,
        group_counter: dict[str, int] | None = None,
    ) -> None:
        self.spec = SourceSpec(
            source_code=code,
            capabilities=("quote",),
            transport="fixture",
            concurrency_group=group or f"fixture:{code}",
            max_concurrency=1,
            cadence="daily",
            freshness_sla_minutes=405,
            required_class="extra",
        )
        self.timeline = timeline
        self.lock = lock
        self.group_counter = group_counter

    def plan(self, context: Mapping[str, Any]) -> Sequence[SourceTask]:
        return [SourceTask(
            run_id=str(context["run_id"]),
            business_date=str(context["business_date"]),
            source_code=self.spec.source_code,
            capability="quote",
        )]

    def execute(self, task: SourceTask, context: Mapping[str, Any], heartbeat: Any) -> Mapping[str, Any]:
        del context
        with self.lock:
            self.timeline.append((task.source_code, "start", time.monotonic()))
            if self.group_counter is not None:
                self.group_counter["active"] = self.group_counter.get("active", 0) + 1
                self.group_counter["peak"] = max(
                    self.group_counter.get("peak", 0), self.group_counter["active"]
                )
        heartbeat(worker_pid=None, checkpoint={"fixture": "running"})
        time.sleep(0.08)
        with self.lock:
            if self.group_counter is not None:
                self.group_counter["active"] -= 1
            self.timeline.append((task.source_code, "end", time.monotonic()))
        return {"source": task.source_code}

    def ingest(self, task: SourceTask, execution: Mapping[str, Any], context: Mapping[str, Any]) -> SourceResult:
        del execution, context
        return SourceResult(
            status="completed",
            observed_at="2026-08-20T00:00:00Z",
            checked_at="2026-08-20T00:00:01Z",
            payload_sha256=("a" if task.source_code == "dummy-third" else "b") * 64,
        )


# Registry extensibility and source independence.
default = build_default_registry()
context = {
    "run_id": "cardz-v2:2026-08-20",
    "business_date": "2026-08-20",
    "input_revision": "fixture",
}
planned = default.plan_all(context)
by_source: dict[str, int] = {}
for task in planned:
    by_source[task.source_code] = by_source.get(task.source_code, 0) + 1
assert by_source == {"fx": 1, "gemrate": 4, "pricecharting": 1, "snkrdunk": 1}
pc = default.get("pricecharting").spec
assert pc.concurrency_group == "cdp:9333" and pc.max_concurrency == 1
assert pc.transport == "cdp:9333" and "9222" not in pc.transport

timeline: list[tuple[str, str, float]] = []
timeline_lock = threading.Lock()
dummy = FakeAdapter("dummy-third", timeline, timeline_lock)
registry = AdapterRegistry((dummy,))
dummy_task = registry.plan_all(context)
assert len(dummy_task) == 1 and dummy_task[0].source_code == "dummy-third"
assert source_payload(dummy, dummy_task[0])["kind"] == "source"
assert source_payload(dummy, dummy_task[0])["spec"]["sourceCode"] == "dummy-third"
dummy_execution = dummy.execute(dummy_task[0], {}, lambda **_kwargs: None)
dummy_result = dummy.ingest(dummy_task[0], dummy_execution, {})
dummy_policy = [QuotePolicyRow("fixture-v1", "*", "dummy-third", 5)]
dummy_quote = select_quote(
    "ja",
    [QuoteCandidate("dummy-third", 33, "2026-08-20T00:00:00+00:00", "123")],
    dummy_policy,
)
assert dummy_result.status == "completed"
assert dummy_quote and dummy_quote.selected_source_code == "dummy-third"
print("POSITIVE_OK third source registers, executes, and ingests; select_quote is exercised here as a pure function only")

# Real simultaneous fixture times.  Separate source groups overlap; the CDP
# contract itself remains one owner.
alpha = FakeAdapter("fixture-alpha", timeline, timeline_lock)
beta = FakeAdapter("fixture-beta", timeline, timeline_lock)
parallel = AdapterRegistry((alpha, beta))
parallel_tasks = parallel.plan_all(context)
with ThreadPoolExecutor(max_workers=2) as pool:
    futures = [
        pool.submit(adapter.execute, task, {}, lambda **_kwargs: None)
        for adapter, task in (
            (parallel.get("fixture-alpha"), parallel_tasks[0]),
            (parallel.get("fixture-beta"), parallel_tasks[1]),
        )
    ]
    for future in futures:
        future.result()
starts = {code: stamp for code, event, stamp in timeline if event == "start" and code.startswith("fixture-")}
ends = {code: stamp for code, event, stamp in timeline if event == "end" and code.startswith("fixture-")}
assert starts["fixture-alpha"] < ends["fixture-beta"]
assert starts["fixture-beta"] < ends["fixture-alpha"]
print("POSITIVE_OK independent source execution intervals overlap")

# Journal: one killed attempt reclaims only its unfinished key.  Replanning the
# same task does not duplicate a checkpoint or generation.
with tempfile.TemporaryDirectory() as folder:
    journal = Journal(Path(folder) / "chain.sqlite3")
    journal.initialise()
    schedule_day = "2026-08-20"
    journal.ensure_run(
        business_date=schedule_day,
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    task = SourceTask(
        run_id=f"cardz-v2:{schedule_day}", business_date=schedule_day,
        source_code="dummy-third", capability="quote", shard="0-of-1",
    )
    assert journal.add_task(
        task, phase="source", required_class="extra",
        concurrency_group="fixture", max_concurrency=1, max_attempts=3,
    )
    assert not journal.add_task(
        task, phase="source", required_class="extra",
        concurrency_group="fixture", max_concurrency=1, max_attempts=3,
    )
    claimed = journal.claim_ready(task.run_id, now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert len(claimed) == 1
    first = claimed[0]
    journal.interrupt_claim(
        task.idempotency_key,
        first["lease_token"],
        reason="fixture kill",
        now=datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
    )
    # An interruption now costs backoff, so the resume happens after the first
    # 60*2**1 second window, not one second later.
    assert journal.claim_ready(
        task.run_id, now=datetime(2026, 8, 20, 0, 0, 1, tzinfo=timezone.utc)
    ) == []
    resumed = journal.claim_ready(
        task.run_id, now=datetime(2026, 8, 20, 0, 2, 1, tzinfo=timezone.utc)
    )
    assert len(resumed) == 1 and resumed[0]["attempts"] == 2
    journal.finish_success(task.idempotency_key, resumed[0]["lease_token"], {"observation": 1})
    assert journal.claim_ready(
        task.run_id, now=datetime(2026, 8, 20, 0, 2, 2, tzinfo=timezone.utc)
    ) == []
    assert len(journal.tasks(task.run_id)) == 1
print("POSITIVE_OK interrupted worker resumes one idempotent unfinished task")

# The durable claim gate is the concurrency owner: two tasks in cdp:9333 can
# never be issued together, including after an interrupted lease.
with tempfile.TemporaryDirectory() as folder:
    journal = Journal(Path(folder) / "cdp.sqlite3")
    journal.initialise()
    run = journal.ensure_run(
        business_date="2026-08-20",
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    for code in ("pc-fixture-a", "pc-fixture-b"):
        journal.add_task(
            SourceTask(
                run_id=run["run_id"], business_date="2026-08-20",
                source_code=code, capability="quote",
            ),
            phase="source", required_class="extra",
            concurrency_group="cdp:9333", max_concurrency=1, max_attempts=2,
        )
    first_claim = journal.claim_ready(run["run_id"], now=datetime(2026, 8, 20, tzinfo=timezone.utc))
    assert len(first_claim) == 1
    journal.finish_success(first_claim[0]["task_key"], first_claim[0]["lease_token"], {"ok": 1})
    second_claim = journal.claim_ready(
        run["run_id"], now=datetime(2026, 8, 20, 0, 0, 1, tzinfo=timezone.utc)
    )
    assert len(second_claim) == 1 and second_claim[0]["task_key"] != first_claim[0]["task_key"]
print("POSITIVE_OK cdp:9333 journal group has exactly one owner")

# Candidate identity cut-off is atomic: unfinished optional attempts become
# degraded, while a running claimed attempt is never stolen mid-write.
with tempfile.TemporaryDirectory() as folder:
    journal = Journal(Path(folder) / "cutoff.sqlite3")
    journal.initialise()
    run = journal.ensure_run(
        business_date="2026-08-20",
        source_cutoff_at="2026-08-20T01:15:00+00:00",
        sla_at="2026-08-20T02:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    for code in ("identity-a", "identity-b"):
        journal.add_task(
            SourceTask(
                run_id=run["run_id"], business_date="2026-08-20",
                source_code="system", capability=code,
            ),
            phase="identity", required_class="extra",
            concurrency_group=f"fixture:{code}", max_concurrency=1, max_attempts=3,
        )
    running = journal.claim_ready(
        run["run_id"], phases=("identity",), limit=1,
        now=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert len(running) == 1
    assert journal.degrade_unfinished_phase(
        run["run_id"], "identity", error_code="IDENTITY_CUTOFF", reason="fixture"
    ) == 1
    states = sorted(row["status"] for row in journal.tasks(run["run_id"], phase="identity"))
    assert states == ["DEGRADED", "RUNNING"]
print("POSITIVE_OK identity cutoff freezes pending candidates without stealing a running claim")

# Task Scheduler provenance: only 107 + matching task + scheduler parent can
# contribute to autonomous proof.  110 and direct CLI are manual.
with tempfile.TemporaryDirectory() as folder:
    journal = Journal(Path(folder) / "proof.sqlite3")
    journal.initialise()
    for business_day, record in (("2026-08-21", 101), ("2026-08-24", 102)):
        run = journal.ensure_run(
            business_date=business_day,
            source_cutoff_at=f"{business_day}T01:15:00+00:00",
            sla_at=f"{business_day}T02:00:00+00:00",
            final_at=f"{business_day}T08:00:00+00:00",
        )
        origin, inserted = journal.register_provenance(run["run_id"], {
            "event_id": 107,
            "event_record_id": record,
            "instance_id": f"instance-{record}",
            "task_name": "\\CARDZ-Marketcap-Daily-V2",
            "parent_process": "svchost.exe",
            "event_age_seconds": 1,
        })
        assert origin == "scheduled" and inserted
        assert journal.pending_events(run["run_id"]) == []
        journal.mark_publication(
            run["run_id"], status="PUBLISHED", generation_id=f"gen-{record}",
            generated_at=f"{business_day}T03:00:00Z", content_sha256="a" * 64,
            active_count=10, degraded_sources=[],
        )
    assert journal.run("cardz-v2:2026-08-24")["proven_autonomous"] == 1
    earlier_origin, _ = journal.register_provenance("cardz-v2:2026-08-21", {
        "event_id": 110,
        "event_record_id": 998,
        "instance_id": "historical-day-manual",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "parent_process": "svchost.exe",
        "event_age_seconds": 1,
    })
    assert earlier_origin == "manual"
    assert journal.run("cardz-v2:2026-08-24")["proven_autonomous"] == 0
    origin, inserted = journal.register_provenance("cardz-v2:2026-08-24", {
        "event_id": 110,
        "event_record_id": 999,
        "instance_id": "post-publication-manual",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "parent_process": "svchost.exe",
        "event_age_seconds": 1,
    })
    assert origin == "manual" and inserted
    assert journal.run("cardz-v2:2026-08-24")["proven_autonomous"] == 0

    assert not autonomous_proven(date(2026, 8, 26), [
        {
            "business_date": "2026-08-25", "status": "PUBLISHED_DEGRADED",
            "manual_intervention_count": 0, "scheduled_event_107_count": 1,
        },
        {
            "business_date": "2026-08-26", "status": "PUBLISHED",
            "manual_intervention_count": 0, "scheduled_event_107_count": 1,
        },
    ])

    manual = journal.ensure_run(
        business_date="2026-08-25",
        source_cutoff_at="2026-08-25T01:15:00+00:00",
        sla_at="2026-08-25T02:00:00+00:00",
        final_at="2026-08-25T08:00:00+00:00",
    )
    origin, _ = journal.register_provenance(manual["run_id"], {
        "event_id": 110,
        "event_record_id": 103,
        "instance_id": "manual",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "parent_process": "svchost.exe",
        "event_age_seconds": 1,
    })
    assert origin == "manual"
    assert journal.run(manual["run_id"])["manual_intervention_count"] == 1
    assert classify_provenance({
        "event_id": 0, "event_record_id": 0, "instance_id": "direct-cli",
        "task_name": "", "parent_process": "python.exe", "event_age_seconds": 999,
    }) == "manual"
    assert classify_provenance({
        "event_id": 0, "event_record_id": 0, "instance_id": "",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "parent_process": "svchost.exe", "event_age_seconds": 999999,
    }) == "scheduled"
    repeat = journal.ensure_run(
        business_date="2026-08-27",
        source_cutoff_at="2026-08-27T01:15:00+00:00",
        sla_at="2026-08-27T02:00:00+00:00",
        final_at="2026-08-27T08:00:00+00:00",
    )
    repeat_origin, _ = journal.register_provenance(repeat["run_id"], {
        "event_id": 0, "event_record_id": 0, "instance_id": "",
        "task_name": "\\CARDZ-Marketcap-Daily-V2",
        "parent_process": "svchost.exe", "event_age_seconds": 999999,
    })
    assert repeat_origin == "scheduled"
    assert journal.run(repeat["run_id"])["manual_intervention_count"] == 0
    assert journal.run(repeat["run_id"])["scheduled_event_107_count"] == 0
    assert journal.run(repeat["run_id"])["origin"] == "scheduled"
    assert classify_provenance({
        "event_id": "not-an-int", "event_record_id": {}, "instance_id": "broken",
        "task_name": "\\CARDZ-Marketcap-Daily-V2", "parent_process": "svchost.exe",
        "event_age_seconds": "not-a-number",
    }) == "manual"
    malformed_origin, malformed_inserted = journal.register_provenance(
        manual["run_id"],
        {
            "event_id": "not-an-int", "event_record_id": {},
            "instance_id": "broken", "event_age_seconds": "not-a-number",
        },
    )
    assert malformed_origin == "manual" and malformed_inserted
    assert journal.run(manual["run_id"])["manual_intervention_count"] == 2
print("NEGATIVE_OK event 110/manual cannot count as autonomous proof")

# Retry policy fixtures: terminal contracts never blind-retry, Retry-After wins
# the first delay, and publication retains its five immutable retry delays.
assert classify_error("HTTP 429 Retry-After: 17").delays_seconds[0] == 17
assert classify_error("response status=599").error_code == "TRANSIENT_SOURCE"
# audit P1-4: a rejected key stays terminal, but re-auth and Cloudflare blocks
# clear after a cooldown and now carry the infra ladder instead of a zero-retry
# terminal that only a hand `unpark` could reopen.
assert classify_error("invalid api key", stage="publish").terminal
auth_blocked = classify_error("authentication rejected", stage="publish")
assert auth_blocked.error_code == "AUTH_OR_BLOCKED" and not auth_blocked.terminal
assert classify_error("lock wait timeout exceeded").error_code != "MYSQL_UNAVAILABLE"
assert classify_error("CDP 9333 connection refused").error_code == "CDP_9333_UNAVAILABLE"
publish_retry = classify_error("live generation mismatch", stage="publish")
assert publish_retry.delays_seconds == (120, 300, 600, 1200, 1800)
assert not publish_retry.terminal
print("NEGATIVE_OK terminal and retry branches are deterministic")

# Required versus extra-source publication behavior.
source_rows = [
    {"source_code": "gemrate", "required_class": "core", "status": "COMPLETED"},
    {"source_code": "fx", "required_class": "core", "status": "COMPLETED"},
    {"source_code": "snkrdunk", "required_class": "quote", "status": "COMPLETED"},
    {"source_code": "pricecharting", "required_class": "quote", "status": "TERMINAL"},
]
decision = publication_decision(
    source_rows, gemrate_coverage_complete=True, quote_coverage_complete=True,
    fx_ok=True, accept_ok=True, ranking_ok=True, box_ok=True,
)
assert decision.ready and decision.status == "PUBLISHED_DEGRADED"
blocked = publication_decision(
    [{**source_rows[0], "status": "TERMINAL"}, *source_rows[1:]],
    gemrate_coverage_complete=False, quote_coverage_complete=True,
    fx_ok=True, accept_ok=True, ranking_ok=True, box_ok=True,
)
assert not blocked.ready and "gemrate_coverage" in blocked.blockers
assert source_barrier_ready(
    source_rows,
    now=datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc),
    cutoff=datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc),
)
optional_running = [{**row} for row in source_rows]
optional_running[-1]["status"] = "RUNNING"
assert not source_barrier_ready(
    optional_running,
    now=datetime(2026, 8, 20, 1, 14, tzinfo=timezone.utc),
    cutoff=datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc),
)
assert source_barrier_ready(
    optional_running,
    now=datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc),
    cutoff=datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc),
)
core_running = [{**row} for row in source_rows]
core_running[0]["status"] = "RUNNING"
assert not source_barrier_ready(
    core_running,
    now=datetime(2026, 8, 20, 1, 16, tzinfo=timezone.utc),
    cutoff=datetime(2026, 8, 20, 1, 15, tzinfo=timezone.utc),
)
health = aggregate_source_health(source_rows)
assert degraded_source_codes(health) == ["pricecharting"]
print("POSITIVE_OK extra failure degrades while core failure blocks publication")

# Exact identities activate; ambiguous identities quarantine only that card.
assert identity_disposition(
    canonical_matches=1, series_matches=1, pop=1000, quote_eligible=True,
    image_ready=True, public_fields_ready=True,
) == "activate"
assert identity_disposition(
    canonical_matches=2, series_matches=1, pop=5000, quote_eligible=True,
    image_ready=True, public_fields_ready=True,
) == "quarantined_ambiguous"
assert identity_disposition(
    canonical_matches=1, series_matches=1, pop=999, quote_eligible=True,
    image_ready=True, public_fields_ready=True,
) == "policy_excluded"
print("NEGATIVE_OK ambiguous identity quarantines only that candidate")

# Registry policy preserves PC-first EN/Chinese and SNK-first other languages.
policies = [
    QuotePolicyRow("cardz-route-v1", "en", "pricecharting", 10),
    QuotePolicyRow("cardz-route-v1", "en", "snkrdunk", 20),
    QuotePolicyRow("cardz-route-v1", "zh-tw", "pricecharting", 10),
    QuotePolicyRow("cardz-route-v1", "zh-tw", "snkrdunk", 20),
    QuotePolicyRow("cardz-route-v1", "*", "snkrdunk", 10),
    QuotePolicyRow("cardz-route-v1", "*", "pricecharting", 20),
]
candidates = [
    QuoteCandidate("snkrdunk", 11, "2026-08-20T00:00:00+00:00", "100"),
    QuoteCandidate("pricecharting", 22, "2026-08-20T00:00:00+00:00", "110"),
]
en = select_quote("en", candidates, policies, previous_source_code="snkrdunk")
zh = select_quote("zh_TW", candidates, policies)
ja = select_quote("ja", candidates, policies)
assert en and en.selected_source_code == "pricecharting" and en.source_switched
assert zh and zh.selected_source_code == "pricecharting"
assert ja and ja.selected_source_code == "snkrdunk"
print("POSITIVE_OK language route priorities and source switch remain deterministic")

# Unchanged market content still receives a different daily generation.
content = market_content_sha256([{"variantId": 1, "priceUsd": "100", "pop": 1000}])
generation_a = daily_generation_sha256("2026-08-20", content)
generation_b = daily_generation_sha256("2026-08-21", content)
assert generation_a != generation_b
assert content == market_content_sha256([{"variantId": 1, "priceUsd": "100", "pop": 1000}])
print("POSITIVE_OK unchanged content hash produces a distinct daily generation")

# Live mismatch writes no outbox event; a successful retry remains one event.
snapshot = {
    "generation": {"id": "db3308_deadbeefdeadbeef", "generatedAt": "2026-08-20T03:00:00Z", "effectiveAt": "2026-08-20T02:00:00Z"},
    "currencies": {"rates": {"USD": {"asOf": "2026-08-20T02:00:00Z"}}},
    "top100": [{"id": "cmc_abc", "price": 100}],
    "watchlist": [],
}
same_content_next_day = {
    **snapshot,
    "generation": {"id": "db3308_feedfacefeedface", "generatedAt": "2026-08-21T03:00:00Z", "effectiveAt": "2026-08-21T02:00:00Z"},
    "currencies": {"rates": {"USD": {"asOf": "2026-08-21T02:00:00Z"}}},
}
assert sha256(snapshot) != sha256(same_content_next_day)
outbox = MemoryOutbox()
try:
    build_live_event(
        business_date="2026-08-20", run_id="cardz-v2:2026-08-20",
        snapshot=snapshot,
        health={"status": "ok", "generation": "wrong", "generatedAt": "wrong"},
        active_count=1, content_sha256=content,
        source_health={}, degraded_sources=[],
    )
except RuntimeError:
    pass
else:
    raise AssertionError("live mismatch fixture did not fire")
assert outbox.events == {}
event = build_live_event(
    business_date="2026-08-20", run_id="cardz-v2:2026-08-20",
    snapshot=snapshot,
    health={"status": "ok", "generation": snapshot["generation"]["id"], "generatedAt": snapshot["generation"]["generatedAt"]},
    active_count=1, content_sha256=content,
    source_health={}, degraded_sources=[],
)
assert event["contentSha256"] == content
assert event["artifactContentSha256"] == sha256(snapshot)
assert outbox.insert(event)
assert not outbox.insert(event)
assert len(outbox.events) == 1
print("NEGATIVE_OK live mismatch inserts nothing; retry yields one outbox event")

# Static integration contracts: no fixed source branch in core orchestrator,
# independent PC binding despite SNK, exact scheduler settings, and additive DB.
core_source = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8").lower()
stage_text = (ROOT / "pipelines" / "daily_chain_v2_stage.py").read_text(encoding="utf-8")
migration = (ROOT / "pipelines" / "migrations" / "051_daily_chain_v2_generic_sources.mysql.sql").read_text(encoding="utf-8")
migration_053 = (ROOT / "pipelines" / "migrations" / "053_daily_chain_v2_quote_eligibility_reconstruction.mysql.sql").read_text(encoding="utf-8")
# 053 is still migrated and still gets its own infra stage, but neither name is
# spelled out any more: both lists are derived from the migration files on disk.
assert "053_daily_chain_v2_quote_eligibility_reconstruction.mysql.sql" in list_v2_migrations(ROOT)
assert "schema-053" in v2_schema_capabilities(ROOT)
assert "053_daily_chain_v2_quote_eligibility_reconstruction.mysql.sql" not in stage_text
assert 'capability="schema-053"' not in core_source
assert "INNER JOIN operator_strict_source_identity si" in migration
assert "OR EXISTS" not in migration
assert "CREATE OR REPLACE VIEW operator_eligible_current_quote_revision" in migration_053
assert "OR EXISTS" in migration_053
assert "market_variant_source_state source_state" in migration_053
assert "DROP TABLE" not in migration_053 and "DROP COLUMN" not in migration_053
assert "VALUES ('053')" in migration_053
print("POSITIVE_OK 053 reconstruction is additive and 051 bytes stay strict-join")
for provider_literal in ('"gemrate"', '"snkrdunk"', '"pricecharting"'):
    assert provider_literal not in core_source
assert "runtime adapter is not command-backed" not in core_source
collect_source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
assert 'if not pc_exact_product:' in collect_source
assert 'if not pc_exact_product and not ids.get("snkrdunk")' not in collect_source
assert "GEMRATE_PARALLEL_LEASE_SCOPES" in collect_source
assert 'adapter == "gemrate_pop" and not lease_scope' in collect_source
snk_block = collect_source.split("# SNK is an exact market authority", 1)[1].split(
    "# PriceCharting", 1
)[0]
assert "pricecharting" not in snk_block.casefold()
for table in (
    "market_source_registry", "market_variant_source_state", "market_quote_route_policy",
    "publication_outbox", "publication_delivery",
):
    assert f"CREATE TABLE IF NOT EXISTS {table}" in migration
assert "UNIQUE KEY uq_publication_outbox_business_type" in migration
assert "DROP TABLE" not in migration and "DROP COLUMN" not in migration
launcher = (ROOT / "scripts" / "cardz_daily_v2_launcher.ps1").read_text(encoding="utf-8-sig")
installer = (ROOT / "scripts" / "install_cardz_daily_v2_task.ps1").read_text(encoding="utf-8-sig")
stage = stage_text
db_contract = (ROOT / "pipelines" / "daily_chain_v2_db.py").read_text(encoding="utf-8")
rebuild = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
assert "Id = 107,110" in launcher and "event_record_id" in launcher
assert "-WindowStyle Hidden" in launcher
assert "03:30" in installer and "PT10M" in installer and "PT13H30M" in installer
assert "-WindowStyle Hidden" in installer and "consoleWindowStyle" in installer
assert "IgnoreNew" in installer and "StartWhenAvailable" in installer and "55" in installer
assert "Disable-ScheduledTask" in installer and "Unregister-ScheduledTask" not in installer
assert 'operator_e2e_lease(f"v2-discover:' not in stage
assert "daily-discovery-state-v2-{lane}.json" in stage
adapter_source = (ROOT / "pipelines" / "daily_chain_v2_adapters.py").read_text(encoding="utf-8")
assert "candidate-stock:" in adapter_source and 'phase="candidate-source"' in core_source
assert "recover_daily_accept" in stage
assert "recover_live_event" in stage and "recover_live_event" in core_source
assert "left join market_current_quote_revision q" in db_contract.casefold()
assert "left join market_quote_route_policy rp" in db_contract.casefold()
assert "left join operator_eligible_current_quote_revision" not in db_contract.casefold()
assert '"rankingGenerationSha256": ranking_generation_sha' in rebuild
assert "accept-registered-current-quote-revision-v2" in rebuild
assert 'quote_window_sql = " WHERE p.checked_at >= %s AND p.checked_at < %s"' in rebuild
assert '"identity", "candidate-source", "activation"' in core_source
# audit P2-16: the pending-identities stage and the CANDIDATE_ACTIVATION_CUTOFF
# deferral are now driven for real in scripts/test_daily_chain_v2_loop.py, which
# plans them, claims them and reads the emitted event, so the two source-text
# asserts that used to stand here were deleted instead of duplicated.
assert 'capability="candidate-source-plan"' in core_source
assert "daily_public_release.ps1" not in core_source
assert "journal must live on wsl ext4" in core_source
assert "generation/content lineage mismatch" in stage
release = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
assert "cardz-v2-immutable-publication-v1" in release
assert "V2 release refused: daily acceptance produced no immutable generation change" in release
assert "V2_EXPECTED_GENERATION" in release and "publicTreeSha256" in release
assert "asset_max_attempts=1" in release and "push_max_attempts=1" in release
print("POSITIVE_OK V2 wiring is registry-driven, apply-gated, immutable, and non-destructive")

# ---------------------------------------------------------------------------
# Generic-source contracts.  Adding a data source must stay "registry row +
# migration file + adapter registration": every fixture below fails if the
# orchestrator starts naming a provider, a migration, a lane, or a kind again.
import dataclasses  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import types  # noqa: E402

import daily_chain_v2_db as v2db  # noqa: E402
import daily_chain_v2_stage as v2stage  # noqa: E402
import daily_chain_v2_worker as v2worker  # noqa: E402
from daily_chain_v2 import DailyChainV2  # noqa: E402
from daily_chain_v2_adapters import registered_worker_kinds  # noqa: E402
from daily_chain_v2_contract import (  # noqa: E402
    CONTRACT_SHORTFALL_MARKER,
    CORE_CONTRACT_FALLBACK_KEYS,
    IDENTITY_LANE_FALLBACK,
    contract_shortfall,
    core_contract_keys,
    identity_lanes,
    pop_contract_sources,
    quote_repair_routes,
    rates_contract_sources,
    route_policy_upserts,
)

o2_core = (ROOT / "pipelines" / "daily_chain_v2.py").read_text(encoding="utf-8")
o2_stage = (ROOT / "pipelines" / "daily_chain_v2_stage.py").read_text(encoding="utf-8")
o2_db = (ROOT / "pipelines" / "daily_chain_v2_db.py").read_text(encoding="utf-8")
o2_worker = (ROOT / "pipelines" / "daily_chain_v2_worker.py").read_text(encoding="utf-8")
TODAY_V2_MIGRATIONS = (
    "051_daily_chain_v2_generic_sources.mysql.sql",
    "052_daily_chain_v2_strict_gemrate_product_number.mysql.sql",
    "053_daily_chain_v2_quote_eligibility_reconstruction.mysql.sql",
)

# F-MIGRATE-ONLY: the migrate stage and the infra plan are one sorted glob.
with tempfile.TemporaryDirectory(prefix="v2-o2-migrations-") as folder:
    fake_root = Path(folder)
    fake_migrations = fake_root / "pipelines" / "migrations"
    fake_migrations.mkdir(parents=True)
    for name in TODAY_V2_MIGRATIONS + (
        "050_pc_price_languages_eligible_quote.mysql.sql",  # not a V2 file
        "054_daily_chain_v2_source_lanes.txt",              # not a .mysql.sql
        "054_daily_chain_v2_notes.md",
    ):
        (fake_migrations / name).write_text("-- fixture\n", encoding="utf-8")
    assert list_v2_migrations(fake_root) == TODAY_V2_MIGRATIONS
    assert v2_schema_capabilities(fake_root) == (
        "schema-051", "schema-052", "schema-053",
    )
    baseline_command = v2stage.migrate_command(fake_root)
    assert baseline_command.count("--only") == 3
    assert "050_pc_price_languages_eligible_quote.mysql.sql" not in baseline_command
    # Positive: dropping one new V2 migration file in place grows both lists.
    (fake_migrations / "054_daily_chain_v2_third_source.mysql.sql").write_text(
        "-- fixture\n", encoding="utf-8"
    )
    assert list_v2_migrations(fake_root) == TODAY_V2_MIGRATIONS + (
        "054_daily_chain_v2_third_source.mysql.sql",
    )
    assert v2_schema_capabilities(fake_root)[-1] == "schema-054"
    grown_command = v2stage.migrate_command(fake_root)
    assert grown_command.count("--only") == 4
    assert "054_daily_chain_v2_third_source.mysql.sql" in grown_command
with tempfile.TemporaryDirectory(prefix="v2-o2-nomigrations-") as folder:
    empty_root = Path(folder)
    (empty_root / "pipelines" / "migrations").mkdir(parents=True)
    assert list_v2_migrations(empty_root) == ()
    try:
        v2stage.migrate_command(empty_root)
    except RuntimeError as error:
        assert "no daily-chain V2 migrations" in str(error)
    else:
        raise AssertionError("empty migrations directory did not refuse")
# The real tree: no file name and no schema capability is spelled out any more.
for migration_name in TODAY_V2_MIGRATIONS:
    assert migration_name in list_v2_migrations(ROOT)
    assert migration_name not in o2_stage
for schema_capability in ("schema-051", "schema-052", "schema-053", "schema-054"):
    assert schema_capability in v2_schema_capabilities(ROOT)
    assert f'capability="{schema_capability}"' not in o2_core
print("POSITIVE_OK a new V2 migration file joins migrate --only and the infra plan with no code edit")

# F-CONTRACT-BARRIER: one helper, two call sites, registry-driven.
o2_registry_rows = [
    {
        "source_code": "gemrate", "canonical_source_code": "gemrate",
        "identity_source_code": "gemrate", "required_class": "core",
        "enabled": 1, "capabilities_json": '["pop", "identity"]', "config_json": None,
    },
    {
        "source_code": "fx", "canonical_source_code": "fx",
        "identity_source_code": "fx", "required_class": "core",
        "enabled": 1, "capabilities_json": '["rates"]', "config_json": None,
    },
    {
        "source_code": "snkrdunk", "canonical_source_code": "snkrdunk",
        "identity_source_code": "snkrdunk", "required_class": "quote",
        "enabled": 1, "capabilities_json": '["quote", "identity"]', "config_json": None,
    },
]
o2_third_core = {
    "source_code": "dummy-pop", "canonical_source_code": "dummy-pop",
    "identity_source_code": "dummy-pop", "required_class": "core",
    "enabled": 1, "capabilities_json": '["pop"]',
    "config_json": '{"popCheckpointSourceCode": "dummy_pop_checkpoint"}',
}
assert core_contract_keys(o2_registry_rows) == ("fx", "gemrate", "quotes")
assert core_contract_keys(o2_registry_rows + [o2_third_core]) == (
    "dummy-pop", "fx", "gemrate", "quotes",
)
assert pop_contract_sources(o2_registry_rows + [o2_third_core]) == (
    {
        "sourceCode": "dummy-pop", "identitySourceCode": "dummy-pop",
        "popCheckpointSourceCode": "dummy_pop_checkpoint",
    },
    {
        "sourceCode": "gemrate", "identitySourceCode": "gemrate",
        "popCheckpointSourceCode": "gemrate_pop",
    },
)
assert rates_contract_sources(o2_registry_rows + [o2_third_core]) == ("fx",)
# Negative: a disabled row never joins the barrier and no registry at all keeps
# exactly today's three sections, so the fallback cannot loosen the gate.
assert core_contract_keys(
    o2_registry_rows + [{**o2_third_core, "enabled": 0}]
) == ("fx", "gemrate", "quotes")
assert core_contract_keys(None) == CORE_CONTRACT_FALLBACK_KEYS == ("fx", "gemrate", "quotes")
assert core_contract_keys([]) == ("fx", "gemrate", "quotes")
assert pop_contract_sources([{**o2_third_core, "enabled": 0}]) == ()
assert 'for name in ("gemrate", "quotes", "fx")' not in o2_stage
assert o2_stage.count("core_contract_keys(") >= 1 and o2_db.count("core_contract_keys(") >= 1
o2_shortfall = contract_shortfall(
    {
        "dummy-pop": {"missing": [1, 2]},
        "quotes": {"missing": []},
        "fx": {"covered": 5, "expected": 6},
    },
    ("dummy-pop", "fx", "quotes"),
)
assert o2_shortfall == "dummy-popMissing=2 fx=5/6 quotesMissing=0"
assert CONTRACT_SHORTFALL_MARKER in o2_shortfall
assert '"gemrateMissing="' not in o2_core and '"quoteMissing="' not in o2_core
print("POSITIVE_OK the publication barrier and its shortfall line come from the registry, with today's tuple only as fallback")

# F-NO-DRYRUN: limit/dry_run are declared by the payload or the CLI.
o2_captured: dict[str, Any] = {}


def _o2_fake_collect_command(**kwargs: Any) -> dict[str, Any]:
    o2_captured.clear()
    o2_captured.update(kwargs)
    return {
        "ok": True, "processed": 1, "inserted": 0, "checkpointed": 0,
        "failed": 0, "quarantined": 0, "asOf": "2026-08-20T00:00:00Z",
    }


o2_fake_collect = types.ModuleType("collect_control")
o2_fake_collect.cmd_incr = _o2_fake_collect_command
o2_fake_collect.cmd_stock = _o2_fake_collect_command
o2_fake_collect.REGISTRY_PATH = Path("fixture-registry.jsonl")
o2_fake_collect._jsonl_rows = lambda path: []
o2_previous_collect = sys.modules.get("collect_control")
sys.modules["collect_control"] = o2_fake_collect
try:
    with tempfile.TemporaryDirectory(prefix="v2-o2-worker-") as folder:
        o2_receipt = Path(folder) / "receipt.json"
        o2_task = {"source_code": "dummy-third", "run_id": "cardz-v2:2026-08-20"}

        def o2_payload(**worker: Any) -> dict[str, Any]:
            return {"shard": "all", "worker": {"adapters": ["dummy"], "variantIds": [7], **worker}}

        # Negative: the scheduled payload still runs unbounded and still writes.
        receipt = v2worker.run_collect(o2_task, o2_payload(), o2_receipt)
        assert o2_captured["limit"] is None and o2_captured["dry_run"] is False
        assert receipt["detail"]["limit"] is None and receipt["detail"]["dryRun"] is False
        # Positive: the payload declares a bounded, non-writing run.
        receipt = v2worker.run_collect(o2_task, o2_payload(limit=5, dry_run=True), o2_receipt)
        assert o2_captured["limit"] == 5 and o2_captured["dry_run"] is True
        assert receipt["detail"]["limit"] == 5 and receipt["detail"]["dryRun"] is True
        # Positive: a hand CLI override beats the payload.
        receipt = v2worker.run_collect(
            o2_task, o2_payload(limit=5), o2_receipt, limit=2, dry_run=True
        )
        assert o2_captured["limit"] == 2 and o2_captured["dry_run"] is True
        # Negative: a nonsense limit is refused, never silently coerced.
        try:
            v2worker.run_collect(o2_task, o2_payload(), o2_receipt, limit=0)
        except RuntimeError as error:
            assert "limit must be positive" in str(error)
        else:
            raise AssertionError("limit=0 fixture did not fire")
    assert "--limit" in o2_worker and "--dry-run" in o2_worker
    assert "limit=None,\n        dry_run=False," not in o2_worker
    print("POSITIVE_OK collect limit and dry-run come from the payload or the CLI, defaults unchanged")

    # F-WORKER-KIND: --kind choices are derived, an unknown kind fails loudly.
    assert set(v2worker.registered_kinds()) == {"collect", "fx"}
    assert registered_worker_kinds() == ("collect", "fx")

    class _O2ThirdAdapter:
        spec = SourceSpec(
            source_code="dummy-third",
            capabilities=("quote",),
            transport="fixture",
            concurrency_group="host:dummy-third",
            max_concurrency=1,
            cadence="daily",
            freshness_sla_minutes=405,
            required_class="quote",
        )
        worker_kind = "thirdkind"

    assert registered_worker_kinds(AdapterRegistry((_O2ThirdAdapter(),))) == ("thirdkind",)
    assert v2worker.resolve_worker_runner("collect") is v2worker.run_collect
    try:
        v2worker.resolve_worker_runner("thirdkind")
    except RuntimeError as error:
        assert "unsupported worker kind: 'thirdkind'" in str(error)
        assert "this worker can run" in str(error)
    else:
        raise AssertionError("unknown worker kind fixture did not fire")
    assert 'choices=("collect", "fx")' not in o2_worker
finally:
    if o2_previous_collect is None:
        sys.modules.pop("collect_control", None)
    else:
        sys.modules["collect_control"] = o2_previous_collect
o2_cli = subprocess.run(
    [
        sys.executable, "-X", "utf8",
        str(ROOT / "pipelines" / "daily_chain_v2_worker.py"),
        "--state-db", "fixture.sqlite3", "--task-key", "t",
        "--claim-token", "c", "--receipt", "fixture.json",
        "--kind", "bogus-kind",
    ],
    capture_output=True, text=True, timeout=120,
)
assert o2_cli.returncode == 2 and "invalid choice: 'bogus-kind'" in o2_cli.stderr
assert "'collect'" in o2_cli.stderr and "'fx'" in o2_cli.stderr
print("NEGATIVE_OK worker --kind choices are derived from the adapters; an unknown kind is refused")

# F-IDENTITY-LANE: discovery lanes are declared by the identity sources.
o2_default_specs = [adapter.spec for adapter in build_default_registry().enabled()]
assert identity_lanes(o2_default_specs) == (
    ("browser", "cdp:9333"), ("http", "host:snkrdunk"),
)
o2_lane_spec = SourceSpec(
    source_code="dummy-third",
    capabilities=("identity", "quote"),
    transport="fixture",
    concurrency_group="host:dummy-third",
    max_concurrency=1,
    cadence="daily",
    freshness_sla_minutes=405,
    required_class="quote",
    identity_lane="api",
    route_priority=40,
)
assert identity_lanes(o2_default_specs + [o2_lane_spec]) == (
    ("api", "host:dummy-third"), ("browser", "cdp:9333"), ("http", "host:snkrdunk"),
)
# Negative: a source that declares no lane, or declares one without the identity
# capability, adds nothing; an empty registry keeps today's literal pair.
assert identity_lanes(
    o2_default_specs + [dataclasses.replace(o2_lane_spec, capabilities=("quote",))]
) == (("browser", "cdp:9333"), ("http", "host:snkrdunk"))
assert identity_lanes(
    o2_default_specs + [dataclasses.replace(o2_lane_spec, identity_lane=None)]
) == (("browser", "cdp:9333"), ("http", "host:snkrdunk"))
assert identity_lanes(()) == IDENTITY_LANE_FALLBACK == (
    ("browser", "cdp:9333"), ("http", "host:snkrdunk"),
)
assert identity_lanes(
    [dataclasses.replace(o2_lane_spec, enabled=False)]
) == IDENTITY_LANE_FALLBACK
assert 'for lane in ("http", "browser")' not in o2_core
assert 'choices=("http", "browser")' not in o2_stage
assert v2stage.discovery_lane_names() == ("browser", "http")
o2_migration_054 = (
    ROOT / "pipelines" / "migrations" / "054_daily_chain_v2_source_lanes.mysql.sql"
).read_text(encoding="utf-8")
assert "ADD COLUMN identity_lane" in o2_migration_054
assert "ADD COLUMN route_priority" in o2_migration_054
assert "DROP TABLE" not in o2_migration_054 and "DROP COLUMN" not in o2_migration_054
assert "VALUES ('054')" in o2_migration_054
assert "054_daily_chain_v2_source_lanes.mysql.sql" in list_v2_migrations(ROOT)
print("POSITIVE_OK identity lanes and their concurrency groups are registry declarations, not a literal pair")


# F-ROUTE-POLICY: registering a quote source also grants it a route policy row.
class _O2Cursor:
    def __init__(self, existing_policy: Sequence[str] = ()) -> None:
        self.statements: list[tuple[str, tuple[Any, ...]]] = []
        self._existing_policy = tuple(existing_policy)
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        flat = " ".join(str(sql).split())
        self.statements.append((flat, tuple(params or ())))
        if flat.startswith("SELECT DISTINCT source_code FROM market_quote_route_policy"):
            self._result = [{"source_code": code} for code in self._existing_policy]
        else:
            self._result = []

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._result)

    def statements_matching(self, needle: str) -> list[tuple[str, tuple[Any, ...]]]:
        return [item for item in self.statements if needle in item[0]]


class _O2Connection:
    def __init__(self, cursor: _O2Cursor) -> None:
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    def cursor(self) -> _O2Cursor:
        return self._cursor

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed += 1


o2_quote_spec = SourceSpec(
    source_code="dummy-third",
    capabilities=("quote", "identity"),
    transport="fixture",
    concurrency_group="host:dummy-third",
    max_concurrency=1,
    cadence="daily",
    freshness_sla_minutes=405,
    required_class="quote",
    identity_lane="api",
)
assert route_policy_upserts(
    o2_default_specs + [o2_quote_spec],
    existing_source_codes=("snkrdunk", "pricecharting"),
    activated_at="2026-08-20 00:00:00.000000",
) == (("cardz-route-v2", "*", "dummy-third", 30, 1, 1, "2026-08-20 00:00:00.000000"),)
assert route_policy_upserts(
    [dataclasses.replace(o2_quote_spec, route_priority=7)],
    activated_at="2026-08-20 00:00:00.000000",
)[0][3] == 7
# Negative: an already-routed source is never re-priced, and a disabled or
# non-quote source never reaches the policy table at all.
assert route_policy_upserts(
    o2_default_specs + [o2_quote_spec],
    existing_source_codes=("snkrdunk", "pricecharting", "dummy-third"),
    activated_at="2026-08-20 00:00:00.000000",
) == ()
assert route_policy_upserts(
    [dataclasses.replace(o2_quote_spec, enabled=False)],
    activated_at="2026-08-20 00:00:00.000000",
) == ()
assert route_policy_upserts(
    [dataclasses.replace(o2_quote_spec, capabilities=("pop",))],
    activated_at="2026-08-20 00:00:00.000000",
) == ()
try:
    route_policy_upserts(
        [dataclasses.replace(o2_quote_spec, route_priority=0)],
        activated_at="2026-08-20 00:00:00.000000",
    )
except ValueError as error:
    assert "route_priority must be positive" in str(error)
else:
    raise AssertionError("non-positive route priority fixture did not fire")

o2_db_real, o2_load_env_real = v2db.db, v2db.load_env
try:
    v2db.load_env = lambda: None
    o2_cursor = _O2Cursor(existing_policy=("snkrdunk", "pricecharting"))
    o2_connection = _O2Connection(o2_cursor)
    v2db.db = lambda: o2_connection
    o2_written = v2db.sync_source_registry(o2_default_specs + [o2_quote_spec])
    assert o2_written == {"registered": 5, "routePolicyRows": 1}
    o2_registry_writes = o2_cursor.statements_matching("INSERT INTO market_source_registry")
    assert len(o2_registry_writes) == 5
    o2_snk_params = [item[1] for item in o2_registry_writes if item[1][0] == "snkrdunk"][0]
    assert "http" in o2_snk_params and 10 in o2_snk_params
    o2_policy_writes = o2_cursor.statements_matching("INSERT INTO market_quote_route_policy")
    assert len(o2_policy_writes) == 1
    assert o2_policy_writes[0][1][:6] == ("cardz-route-v2", "*", "dummy-third", 30, 1, 1)
    assert o2_connection.commits == 1 and o2_connection.rollbacks == 0
    # Negative: a second registration of the same set writes no policy row.
    o2_cursor_again = _O2Cursor(
        existing_policy=("snkrdunk", "pricecharting", "dummy-third")
    )
    v2db.db = lambda: _O2Connection(o2_cursor_again)
    assert v2db.sync_source_registry(
        o2_default_specs + [o2_quote_spec]
    ) == {"registered": 5, "routePolicyRows": 0}
    assert o2_cursor_again.statements_matching("INSERT INTO market_quote_route_policy") == []
finally:
    v2db.db, v2db.load_env = o2_db_real, o2_load_env_real
print("POSITIVE_OK the registry stage grants a new quote source its route policy row and never re-prices an existing one")


# F-DEAD-POLICY: select_quote is a production cross-check, not a museum piece.
def o2_route_row(variant_id: int, code: str, priority: int, language: str = "ja") -> dict[str, Any]:
    return {
        "variant_id": variant_id,
        "source_code": code,
        "priority": priority,
        "policy_version": "cardz-route-v1",
        "language_code": language,
        "card_language": "ja",
    }


o2_agree = quote_repair_routes([
    o2_route_row(1, "snkrdunk", 10),
    o2_route_row(1, "pricecharting", 20, language="*"),
])
assert o2_agree == {"routes": {"snkrdunk": [1]}, "rejected": []}
# Positive fire: the SQL join proposes the lower-priority source first, so the
# declared policy refuses the repair instead of quietly using it.
o2_drifted = quote_repair_routes([
    o2_route_row(2, "pricecharting", 20, language="*"),
    o2_route_row(2, "snkrdunk", 10),
])
assert o2_drifted["routes"] == {}
assert o2_drifted["rejected"] == [
    {"variantId": 2, "sqlWinner": "pricecharting", "policyWinner": "snkrdunk"}
]
assert "quote_repair_routes(" in o2_db and "quote_repair_plan(" in o2_core
print("POSITIVE_OK the declared quote policy cross-checks the SQL repair winner and rejects a drifted one")

# F-REPAIR + the policy cross-check, exercised through the real planner.
with tempfile.TemporaryDirectory(prefix="v2-o2-repair-") as folder:
    o2_journal = Journal(Path(folder) / "chain.sqlite3")
    o2_journal.initialise()
    o2_day = date(2026, 8, 20)
    o2_journal.ensure_run(
        business_date=o2_day.isoformat(),
        source_cutoff_at="2026-08-20T04:00:00+00:00",
        sla_at="2026-08-20T05:00:00+00:00",
        final_at="2026-08-20T08:00:00+00:00",
    )
    o2_chain = DailyChainV2(
        journal=o2_journal,
        business_date=o2_day,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
    )

    def o2_events(event_type: str) -> list[dict[str, Any]]:
        return [
            row for row in o2_journal.pending_events(o2_chain.run_id)
            if row["event_type"] == event_type
        ]

    # Positive: a source with no adapter at all is journalled and skipped.
    assert o2_chain._plan_contract_repair(
        source_code="dummy-third", capability="pop", variant_ids=[11, 12]
    ) == 0
    o2_skips = o2_events("REPAIR_SKIPPED_NO_ADAPTER")
    assert len(o2_skips) == 1 and "SOURCE_NOT_REGISTERED" in o2_skips[0]["payload_json"]
    # Positive: a registered source whose adapter cannot repair that capability.
    assert o2_chain._plan_contract_repair(
        source_code="fx", capability="pop", variant_ids=[13]
    ) == 0
    o2_skips = o2_events("REPAIR_SKIPPED_NO_ADAPTER")
    assert len(o2_skips) == 2
    assert any("CAPABILITY_NOT_REPAIRABLE" in row["payload_json"] for row in o2_skips)
    # Negative: the real gemrate POP repair still plans exactly one task.
    assert o2_chain._plan_contract_repair(
        source_code="gemrate", capability="pop", variant_ids=[11, 12]
    ) == 1
    assert len(o2_events("REPAIR_SKIPPED_NO_ADAPTER")) == 2
    assert len(o2_journal.tasks(o2_chain.run_id, phase="source")) == 1

    # The whole planner: an adapter-less POP source and a policy-rejected quote
    # variant leave the stage running instead of raising.
    o2_contract_real = v2db.current_run_contract
    o2_plan_real = v2db.quote_repair_plan
    try:
        v2db.current_run_contract = lambda business_date: {
            "popSources": ["dummy-pop"],
            "dummy-pop": {"missing": [21, 22], "complete": False},
            "quotes": {"missing": [23], "complete": False},
        }
        v2db.quote_repair_plan = lambda variant_ids: {
            "routes": {"dummy-third": [23]},
            "rejected": [
                {"variantId": 23, "sqlWinner": "pricecharting", "policyWinner": "snkrdunk"}
            ],
        }
        assert o2_chain.plan_contract_repair_tasks() == 0
    finally:
        v2db.current_run_contract = o2_contract_real
        v2db.quote_repair_plan = o2_plan_real
    o2_skips = o2_events("REPAIR_SKIPPED_NO_ADAPTER")
    assert len(o2_skips) == 4
    assert sum("dummy-pop" in row["payload_json"] for row in o2_skips) == 1
    o2_rejected = o2_events("QUOTE_ROUTE_POLICY_REJECTED")
    assert len(o2_rejected) == 1
    o2_rejected_payload = json.loads(o2_rejected[0]["payload_json"])
    assert o2_rejected_payload["errorCode"] == "QUOTE_ROUTE_POLICY_MISMATCH"
    assert o2_rejected_payload["rejected"] == [
        {"variantId": 23, "sqlWinner": "pricecharting", "policyWinner": "snkrdunk"}
    ]
    # Nothing new was planned, and the one real task from before is untouched.
    assert len(o2_journal.tasks(o2_chain.run_id, phase="source")) == 1
print("POSITIVE_OK a source without a repair adapter journals REPAIR_SKIPPED_NO_ADAPTER and the stage keeps going")


# F-CONTRACT-BARRIER, end to end: current_run_contract builds one section per
# registry source, and a core source nothing can measure blocks instead of
# silently passing.  Driven by a fake cursor; no MySQL is contacted.
from fx_rates import SUPPORTED_CURRENCIES  # noqa: E402

O2_CONTRACT_TABLES = (
    "market_source_registry", "market_quote_route_policy",
    "market_variant_source_state", "publication_outbox", "publication_delivery",
)


class _O2ContractCursor:
    def __init__(self, registry_rows: Sequence[Mapping[str, Any]], *, active_count: int = 2) -> None:
        self._registry_rows = [dict(row) for row in registry_rows]
        self._active_count = active_count
        self._result: list[dict[str, Any]] = []
        self.pop_queries: list[tuple[str, str]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        flat = " ".join(str(sql).split())
        args = tuple(params or ())
        if "information_schema.tables" in flat:
            self._result = [{"table_name": name} for name in O2_CONTRACT_TABLES]
        elif "FROM market_source_registry ORDER BY source_code" in flat:
            self._result = [dict(row) for row in self._registry_rows]
        elif flat.startswith("SELECT COUNT(*) AS n FROM market_universe_member"):
            self._result = [{"n": self._active_count}]
        elif "current_pop" in flat:
            self.pop_queries.append((str(args[2]), str(args[3])))
            self._result = [
                {"variant_id": index, "current_pop": 1}
                for index in range(1, self._active_count + 1)
            ]
        elif "current_quote" in flat:
            self._result = [
                {"variant_id": index, "current_quote": 1}
                for index in range(1, self._active_count + 1)
            ]
        elif "market_fx_rate_observation" in flat:
            self._result = [{"n": len(SUPPORTED_CURRENCIES) - 1}]
        else:
            raise AssertionError(f"unexpected contract query: {flat[:120]}")

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._result)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._result[0]) if self._result else None


def o2_registry_row(
    code: str, required_class: str, capabilities: str, *, enabled: int = 1
) -> dict[str, Any]:
    return {
        "source_code": code, "canonical_source_code": code,
        "identity_source_code": code, "required_class": required_class,
        "enabled": enabled, "capabilities_json": capabilities, "config_json": None,
    }


O2_TODAY_REGISTRY = [
    o2_registry_row("fx", "core", '["rates"]'),
    o2_registry_row("gemrate", "core", '["pop", "identity"]'),
    o2_registry_row("pricecharting", "quote", '["quote", "identity"]'),
    o2_registry_row("snkrdunk", "quote", '["quote", "identity"]'),
]


def o2_barrier_failures(contract: Mapping[str, Any]) -> list[str]:
    """Exactly the check daily_chain_v2_stage.stage_contract performs."""

    return [
        name for name in core_contract_keys(contract.get("sources"))
        if not bool((contract.get(name) or {}).get("complete"))
    ]


o2_db_real, o2_load_env_real = v2db.db, v2db.load_env
try:
    v2db.load_env = lambda: None
    # Negative: today's registry yields today's three complete sections.
    o2_today_cursor = _O2ContractCursor(O2_TODAY_REGISTRY)
    v2db.db = lambda: _O2Connection(o2_today_cursor)
    o2_today_contract = v2db.current_run_contract("2026-08-20")
    assert o2_today_contract["barrierKeys"] == ["fx", "gemrate", "quotes"]
    assert o2_today_cursor.pop_queries == [("gemrate", "gemrate_pop")]
    assert o2_barrier_failures(o2_today_contract) == []
    assert o2_today_contract["gemrate"]["complete"] is True
    assert o2_today_contract["quotes"]["complete"] is True
    assert o2_today_contract["fx"]["complete"] is True
    # Positive: a third POP source gets its own measured section, queried with
    # its own identity and checkpoint codes, with no orchestrator edit.
    o2_third_cursor = _O2ContractCursor(
        O2_TODAY_REGISTRY + [o2_registry_row("dummy-pop", "core", '["pop"]')]
    )
    v2db.db = lambda: _O2Connection(o2_third_cursor)
    o2_third_contract = v2db.current_run_contract("2026-08-20")
    assert o2_third_contract["barrierKeys"] == ["dummy-pop", "fx", "gemrate", "quotes"]
    assert o2_third_cursor.pop_queries == [
        ("dummy-pop", "dummy-pop_pop"), ("gemrate", "gemrate_pop"),
    ]
    assert o2_third_contract["dummy-pop"]["complete"] is True
    assert o2_barrier_failures(o2_third_contract) == []
    # Positive fire: a core source that declares neither pop nor rates cannot be
    # measured, so it blocks publication rather than passing unnoticed.
    o2_blind_cursor = _O2ContractCursor(
        O2_TODAY_REGISTRY + [o2_registry_row("dummy-core", "core", '["identity"]')]
    )
    v2db.db = lambda: _O2Connection(o2_blind_cursor)
    o2_blind_contract = v2db.current_run_contract("2026-08-20")
    assert o2_blind_contract["dummy-core"]["complete"] is False
    assert "declares no pop or rates" in o2_blind_contract["dummy-core"]["reason"]
    assert o2_barrier_failures(o2_blind_contract) == ["dummy-core"]
    assert CONTRACT_SHORTFALL_MARKER in contract_shortfall(
        o2_blind_contract, ("dummy-core",)
    )
finally:
    v2db.db, v2db.load_env = o2_db_real, o2_load_env_real
print("POSITIVE_OK current_run_contract measures one section per registry source and blocks an unmeasurable core source")
