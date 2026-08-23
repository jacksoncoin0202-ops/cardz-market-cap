#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fix F: same-run checkpoint repair, offline.

Plan §B.1.  Root-cause rows #1/#2 of 2026-08-22: identity repair bound 50 new
`pc_ebay_sales` streams and 48 `en_price_ref` streams after the collection
registry had already been built, so `daily-accept` hard-failed on
`checkpoint gate failed pc_ebay_sales: streams=1171 missing=50` and on
`en_price_ref ... fresh_pc_pages_unavailable`.  Nothing here touches the gate:
these fixtures prove the repair refills the gate's inputs, in the same run,
before the gate reads them, and that it costs nothing when nothing is missing.

No MySQL, no network, no Chrome.
"""
from __future__ import annotations

import contextlib
import inspect
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control  # noqa: E402
import daily_chain_v2_stage as stage  # noqa: E402
import operator_control  # noqa: E402
from daily_chain_v2 import (  # noqa: E402
    TICK_RESERVE_SECONDS,
    DailyChainV2,
    jst_schedule,
)
from daily_chain_v2_journal import Journal, iso  # noqa: E402

DAY = date(2026, 8, 22)
RUN_ID = f"cardz-v2:{DAY.isoformat()}"
SCHEDULE = jst_schedule(DAY)
WORKSPACE = Path(tempfile.mkdtemp(prefix="cardz-v2-fixf-"))

# One registry row per (adapter, variant, external id) stream, exactly the
# shape collect_control.build_registry writes into collect_registry.jsonl.
REGISTRY = [
    {"adapter": "pc_ebay_sales", "variantId": 1876, "externalId": "6235181"},
    {"adapter": "pc_ebay_sales", "variantId": 1915, "externalId": "10395109"},
    {"adapter": "pc_ebay_sales", "variantId": 2033, "externalId": "8506789"},
    {"adapter": "en_price_ref", "variantId": 1876, "externalId": "6235181"},
    {"adapter": "en_price_ref", "variantId": 1915, "externalId": "10395109"},
    # A third adapter must never be pulled into this repair.
    {"adapter": "gemrate_pop", "variantId": 1876, "externalId": "4d8d7d8945"},
]


def stream_key(variant_id: int, external_id: str) -> str:
    return f"{int(variant_id)}:{external_id}"[:100]


def checkpoint_row(adapter: str, variant_id: int, external_id: str, stamp: str | None) -> dict[str, Any]:
    return {
        "source_code": adapter,
        "stream_key": stream_key(variant_id, external_id),
        "last_effective_at": stamp,
        "last_payload_sha256": "a" * 64,
        "last_run_id": RUN_ID,
    }


class FixtureCursor:
    """Only market_ingest_checkpoint is read; the rest of 3308 is not here."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def execute(self, statement: str, params: Any = None) -> None:
        del params
        self.queries.append(" ".join(str(statement).split()))

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self.rows)


class FixtureConnection:
    def __init__(self, cursor: FixtureCursor) -> None:
        self._cursor = cursor
        self.closed = 0

    def cursor(self) -> FixtureCursor:
        return self._cursor

    def close(self) -> None:
        self.closed += 1


ALL_CHECKPOINTS = [
    checkpoint_row("pc_ebay_sales", 1876, "6235181", "2026-08-22T02:00:00Z"),
    checkpoint_row("pc_ebay_sales", 1915, "10395109", "2026-08-22T02:00:00Z"),
    checkpoint_row("pc_ebay_sales", 2033, "8506789", "2026-08-22T02:00:00Z"),
    checkpoint_row("en_price_ref", 1876, "6235181", "2026-08-22T02:00:00Z"),
    checkpoint_row("en_price_ref", 1915, "10395109", "2026-08-22T02:00:00Z"),
]
# The 08-22 shape: identity repair created v1915 and v2033 after the registry
# stage ran, so pc_ebay_sales is two streams short.  v1876 already collected.
# The v2033 row exists with a NULL last_effective_at: a row is not a checkpoint.
AFTER_IDENTITY_REPAIR = [
    checkpoint_row("pc_ebay_sales", 1876, "6235181", "2026-08-22T02:00:00Z"),
    checkpoint_row("pc_ebay_sales", 2033, "8506789", None),
    checkpoint_row("en_price_ref", 1876, "6235181", "2026-08-22T02:00:00Z"),
    checkpoint_row("en_price_ref", 1915, "10395109", "2026-08-22T02:00:00Z"),
]


# ---------------------------------------------------------------------------
# 1. Missing-stream computation, through the gate's own helper.
# ---------------------------------------------------------------------------
missing = operator_control.missing_checkpoint_streams(
    FixtureCursor(AFTER_IDENTITY_REPAIR),
    registry=REGISTRY,
    adapters=stage.CHECKPOINT_REPAIR_ADAPTERS,
)
assert sorted(missing) == ["en_price_ref", "pc_ebay_sales"], sorted(missing)
assert [row["variantId"] for row in missing["pc_ebay_sales"]] == [1915, 2033]
# Positive fire: the v2033 row EXISTS but carries no last_effective_at, and a
# row without a stamp is what the gate counts as missing.
assert [row["externalId"] for row in missing["pc_ebay_sales"]] == ["10395109", "8506789"]
assert missing["en_price_ref"] == []
# A third registered adapter is out of scope; this stage never collects it.
assert "gemrate_pop" not in missing
# Negative: a fully checkpointed registry leaves nothing to repair.
healthy = operator_control.missing_checkpoint_streams(
    FixtureCursor(ALL_CHECKPOINTS),
    registry=REGISTRY,
    adapters=stage.CHECKPOINT_REPAIR_ADAPTERS,
)
assert healthy == {"pc_ebay_sales": [], "en_price_ref": []}
print("POSITIVE_OK missing-stream computation counts a stamp-less checkpoint row as missing and stays inside the two repair adapters")


# ---------------------------------------------------------------------------
# 2. Stage behaviour: no-op, forced repair, deadline, fail-closed.
# ---------------------------------------------------------------------------
class FirstStockSpy:
    def __init__(self, *, ok: bool = True, fills: FixtureCursor | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.ok = ok
        self.fills = fills

    def __call__(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.fills is not None:
            self.fills.rows = list(ALL_CHECKPOINTS)
        return {
            "action": "first-stock",
            "ok": self.ok,
            "error": None if self.ok else "first-stock adapter=pc_ebay_sales failed",
            "missing": {"pc_ebay_sales": [1915, 2033]},
            "ran": [{"adapter": "pc_ebay_sales", "variantIds": [1915, 2033], "ok": self.ok}],
        }


LEASE_OWNERS: list[str] = []


@contextlib.contextmanager
def fixture_lease(owner: str) -> Any:
    LEASE_OWNERS.append(owner)
    yield


def run_stage(cursor: FixtureCursor, spy: FirstStockSpy) -> tuple[Any, float, FixtureConnection]:
    """Call the real stage with only its outside doors (DB, collector) replaced."""

    connection = FixtureConnection(cursor)
    real = (
        collect_control._jsonl_rows,
        collect_control.load_env,
        collect_control.db,
        collect_control.cmd_first_stock,
        operator_control.operator_e2e_lease,
    )
    collect_control._jsonl_rows = lambda _path: list(REGISTRY)
    collect_control.load_env = lambda: None
    collect_control.db = lambda: connection
    collect_control.cmd_first_stock = spy
    operator_control.operator_e2e_lease = fixture_lease
    LEASE_OWNERS.clear()
    started = time.monotonic()
    try:
        outcome: Any = stage.stage_checkpoint_repair(SimpleNamespace())
    except Exception as error:  # noqa: BLE001 - the fixture asserts on it
        outcome = error
    finally:
        elapsed = time.monotonic() - started
        (
            collect_control._jsonl_rows,
            collect_control.load_env,
            collect_control.db,
            collect_control.cmd_first_stock,
            operator_control.operator_e2e_lease,
        ) = real
    return outcome, elapsed, connection


os.environ.pop(stage.STAGE_DEADLINE_ENV, None)

# 2a. Nothing missing: zero network, zero registry rebuild, and cheap.
noop_spy = FirstStockSpy()
noop_result, noop_elapsed, noop_conn = run_stage(FixtureCursor(ALL_CHECKPOINTS), noop_spy)
assert not isinstance(noop_result, Exception), noop_result
assert noop_spy.calls == [], noop_spy.calls
assert noop_result["network"] is False
assert noop_result["streamsMissingBefore"] == {"pc_ebay_sales": 0, "en_price_ref": 0}
assert noop_result["streamsMissingAfter"] == {"pc_ebay_sales": 0, "en_price_ref": 0}
assert noop_result["repairAdapters"] == [] and noop_result["variantIds"] == {}
assert noop_conn.closed == 1
# Nothing to collect means nothing to serialize against daily-accept either.
assert LEASE_OWNERS == [], LEASE_OWNERS
# "Identity repair produced no new streams" must stay far under two seconds.
assert noop_elapsed < 2.0, noop_elapsed
assert noop_result["elapsedSeconds"] < 2.0, noop_result["elapsedSeconds"]
print(f"POSITIVE_OK an already-checkpointed registry repairs nothing, calls no collector, and returns in {noop_elapsed:.3f}s")

# 2b. Streams created by this run's identity repair: force-network, only them.
forced_cursor = FixtureCursor(list(AFTER_IDENTITY_REPAIR))
forced_spy = FirstStockSpy(fills=forced_cursor)
forced_result, _forced_elapsed, forced_conn = run_stage(forced_cursor, forced_spy)
assert not isinstance(forced_result, Exception), forced_result
assert len(forced_spy.calls) == 1, forced_spy.calls
call = forced_spy.calls[0]
# The 08-22 hand repair was `--force-network`; without it the incr path replays
# a page that is still inside the 36 h SLA and mints no first checkpoint.
assert call["force_network"] is True
# en_price_ref was complete, so it is not collected: exactly the missing streams.
assert call["adapters"] == ["pc_ebay_sales"], call["adapters"]
assert call["dry_run"] is False and call["ensure_browser"] is True
assert forced_result["network"] is True
assert forced_result["streamsMissingBefore"] == {"pc_ebay_sales": 2, "en_price_ref": 0}
assert forced_result["streamsMissingAfter"] == {"pc_ebay_sales": 0, "en_price_ref": 0}
assert forced_result["variantIds"] == {"pc_ebay_sales": [1915, 2033]}
assert forced_result["firstStock"]["ok"] is True
assert forced_conn.closed == 2  # measured before and measured again after
# `first-stock` is in COLLECT_E2E_LEASE_COMMANDS; calling the function instead
# of the CLI must not drop the lease that serializes it against daily-accept.
assert LEASE_OWNERS == ["v2-checkpoint-repair"], LEASE_OWNERS
assert "first-stock" in collect_control.COLLECT_E2E_LEASE_COMMANDS
print("POSITIVE_OK a same-run identity-repair stream is collected with force_network=True and only for the short adapter")

# 2c. Fail-closed: first-stock claims success but the gate input is still short.
short_cursor = FixtureCursor(list(AFTER_IDENTITY_REPAIR))
short_spy = FirstStockSpy()  # fills nothing
short_result, _short_elapsed, _short_conn = run_stage(short_cursor, short_spy)
assert isinstance(short_result, RuntimeError), short_result
assert "left streams without a checkpoint" in str(short_result)
assert "pc_ebay_sales" in str(short_result)
print("NEGATIVE_OK a repair that did not actually checkpoint the streams fails instead of letting the gate meet them")

# 2d. first-stock itself failed: reported verbatim, never swallowed.
failed_cursor = FixtureCursor(list(AFTER_IDENTITY_REPAIR))
failed_spy = FirstStockSpy(ok=False)
failed_result, _failed_elapsed, _failed_conn = run_stage(failed_cursor, failed_spy)
assert isinstance(failed_result, RuntimeError), failed_result
assert "first-stock failed" in str(failed_result)
print("NEGATIVE_OK a failed first-stock surfaces as a stage failure with its own error text")

# 2e. Tick deadline: no budget left means no network at all, retry next tick.
os.environ[stage.STAGE_DEADLINE_ENV] = repr(time.time() - 1.0)
try:
    deadline_spy = FirstStockSpy()
    deadline_result, _deadline_elapsed, _deadline_conn = run_stage(
        FixtureCursor(list(AFTER_IDENTITY_REPAIR)), deadline_spy
    )
    assert isinstance(deadline_result, RuntimeError), deadline_result
    assert "deferred to the next tick" in str(deadline_result)
    assert deadline_spy.calls == [], deadline_spy.calls
    # Positive: the same exhausted deadline never blocks the free no-op path.
    idle_spy = FirstStockSpy()
    idle_result, _idle_elapsed, _idle_conn = run_stage(FixtureCursor(ALL_CHECKPOINTS), idle_spy)
    assert not isinstance(idle_result, Exception), idle_result
    assert idle_result["network"] is False and idle_spy.calls == []
    assert stage.stage_deadline_budget_seconds(now_epoch=time.time()) < 0
finally:
    os.environ.pop(stage.STAGE_DEADLINE_ENV, None)
assert stage.stage_deadline_budget_seconds() is None
print("NEGATIVE_OK an exhausted tick budget defers the network repair and still allows the zero-cost no-op")

# The orchestrator is the only place TICK_RESERVE_SECONDS is subtracted.
runner_source = inspect.getsource(DailyChainV2._run_stage_process)
assert stage.STAGE_DEADLINE_ENV in runner_source
assert "TICK_RESERVE_SECONDS" in runner_source
assert f"TICK_RESERVE_SECONDS = {TICK_RESERVE_SECONDS}" not in inspect.getsource(stage)
assert 'sub.add_parser("checkpoint-repair")' in inspect.getsource(stage.main)
print("POSITIVE_OK the stage deadline is published by the orchestrator with the reserve already subtracted, and the stage has a CLI entry")


# ---------------------------------------------------------------------------
# 3. plan() ordering: after activation, before core-contract-post.
# ---------------------------------------------------------------------------
def plan_capability_order(*, stop_at: str = "daily-accept", rounds: int = 80) -> list[str]:
    """Drive the real planner, completing whatever it plans, and record order."""

    journal = Journal(WORKSPACE / "plan-order.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(SCHEDULE["source_cutoff"]),
        sla_at=iso(SCHEDULE["sla"]),
        final_at=iso(SCHEDULE["final"]),
    )
    chain = DailyChainV2(
        journal=journal,
        business_date=DAY,
        allow_publish=False,
        notify=False,
        deadline_monotonic=time.monotonic() + 600,
        schedule=SCHEDULE,
    )
    now = SCHEDULE["source_cutoff"] - timedelta(hours=1)
    ordered: list[str] = []
    seen: set[str] = set()
    for _ in range(rounds):
        chain.plan(now)
        for row in journal.tasks(RUN_ID):
            capability = str(row["capability"])
            if capability not in seen:
                seen.add(capability)
                ordered.append(capability)
        if stop_at in seen:
            break
        with sqlite3.connect(str(journal.path)) as conn:
            conn.execute(
                "UPDATE chain_task SET status='COMPLETED',result_json='{}',"
                "updated_at=? WHERE run_id=? AND status<>'COMPLETED'",
                (iso(datetime.now(timezone.utc)), RUN_ID),
            )
            conn.commit()
    return ordered


order = plan_capability_order()
for required in ("candidate-activation", "checkpoint-repair", "core-contract-post", "daily-accept"):
    assert required in order, (required, order)
assert order.index("checkpoint-repair") > order.index("candidate-activation"), order
assert order.index("checkpoint-repair") < order.index("core-contract-post"), order
assert order.index("core-contract-post") < order.index("daily-accept"), order
assert order.count("checkpoint-repair") == 1, order
print(f"POSITIVE_OK plan() puts checkpoint-repair after activation and before core-contract-post: {order[-4:]}")

# The repair is extra, on the browser transport, and retried three times.
plan_source = inspect.getsource(DailyChainV2.plan)
repair_block = plan_source.split('capability="checkpoint-repair"', 1)[1].split("return", 1)[0]
assert 'concurrency_group="cdp:9333"' in repair_block, repair_block
assert "max_attempts=3" in repair_block, repair_block
assert 'required_class="extra"' in repair_block, repair_block
# TERMINAL hands the verdict back to the untouched gate instead of parking the
# run: repairing inputs may fail, publishing may not silently proceed on a
# loosened gate.
assert 'SUCCESS_TASK_STATES | {"TERMINAL"}' in plan_source.split(
    'capability="checkpoint-repair"', 1
)[1].split('capability="core-contract-post"', 1)[0]
print("POSITIVE_OK the checkpoint-repair stage is extra, grouped on cdp:9333, capped at 3 attempts, and never parks the run")

shutil.rmtree(WORKSPACE, ignore_errors=True)
print("ALL_OK test_checkpoint_repair_stage")
