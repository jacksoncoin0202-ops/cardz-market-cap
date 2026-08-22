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
    market_content_sha256,
    publication_decision,
    select_quote,
    sha256,
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
print("POSITIVE_OK third source registers, executes, ingests, and joins policy without core changes")

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
    resumed = journal.claim_ready(
        task.run_id, now=datetime(2026, 8, 20, 0, 0, 1, tzinfo=timezone.utc)
    )
    assert len(resumed) == 1 and resumed[0]["attempts"] == 2
    journal.finish_success(task.idempotency_key, resumed[0]["lease_token"], {"observation": 1})
    assert journal.claim_ready(
        task.run_id, now=datetime(2026, 8, 20, 0, 0, 2, tzinfo=timezone.utc)
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
assert classify_error("authentication rejected", stage="publish").terminal
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
assert "053_daily_chain_v2_quote_eligibility_reconstruction.mysql.sql" in stage_text
assert 'capability="schema-053"' in core_source
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
assert "candidate_activation_cutoff" in core_source
assert 'capability="pending-identities"' in core_source
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
