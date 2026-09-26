#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R5 2026-09-25: one definition of "settled" for core source rows.

A core row that finished DEGRADED (quarantined items or an empty shard) used to
hold the source barrier for the whole business date with no operator command
to clear it.  is_settled() now counts it settled, because the data gate is the
core contract right behind the barrier, which measures every core source
itself and fails closed:

(a) a DEGRADED gemrate shard opens the barrier, core-contract-pre is planned,
    and with the contract satisfied the run moves on to the identity stages;
(b) the real stage_contract still raises with the shortfall marker when the
    gemrate pop coverage or the fx rates are short, and plan() does not pass a
    core-contract-pre that failed;
(c) 2026-09-26: a gemrate shard that Cloudflare's block settled DEGRADED hands
    the contract stage the approved 3-day pop fallback, end to end through the
    real stage CLI, and the repair planner measures the same window.

sqlite journal plus a fake MySQL cursor: no database, no network, no workers.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
from contextlib import closing
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
os.environ["CARDZ_V2_NOTIFY_DRY_RUN"] = "1"

import daily_chain_v2 as v2core  # noqa: E402
import daily_chain_v2_db as v2db  # noqa: E402
import daily_chain_v2_stage as v2stage  # noqa: E402
import operator_control  # noqa: E402
from daily_chain_v2 import (  # noqa: E402
    DailyChainV2,
    aggregate_source_health,
    blocked_core_source_tasks,
    degraded_source_codes,
    is_settled,
    jst_schedule,
    source_barrier_ready,
)
from daily_chain_v2_contract import (  # noqa: E402
    CONTRACT_SHORTFALL_MARKER,
    GEMRATE_BLOCKED_ERROR_CODE,
    POP_BLOCKED_FALLBACK_DAYS,
    SourceTask,
    canonical_json,
)
from daily_chain_v2_journal import Journal, iso  # noqa: E402
from fx_rates import SUPPORTED_CURRENCIES  # noqa: E402

DAY = date(2026, 8, 20)
SCHEDULE = jst_schedule(DAY)
BEFORE_CUTOFF = SCHEDULE["source_cutoff"] - timedelta(hours=1)
CLAIMABLE = {"PENDING", "READY", "RETRY", "INTERRUPTED"}
failures: list[str] = []


def check(name: str, condition: bool, detail: Any = "") -> None:
    if condition:
        print(f"POSITIVE_OK {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


# --------------------------------------------------------------- the helper
for status, expected in (
    ("COMPLETED", True), ("SKIPPED", True), ("DEGRADED", True),
    ("TERMINAL", False), ("PARKED", False), ("RETRY", False),
    ("RUNNING", False), ("PENDING", False), ("INTERRUPTED", False),
):
    check(f"core {status} settled={expected}",
          is_settled({"required_class": "core", "status": status}) is expected)
for status, expected in (
    ("COMPLETED", True), ("DEGRADED", True), ("TERMINAL", True), ("SKIPPED", True),
    ("PARKED", False), ("RETRY", False), ("RUNNING", False), ("PENDING", False),
):
    check(f"quote {status} settled={expected}",
          is_settled({"required_class": "quote", "status": status}) is expected)

core_degraded = {"source_code": "gemrate", "required_class": "core", "status": "DEGRADED"}
core_done = {"source_code": "fx", "required_class": "core", "status": "COMPLETED"}
check("a DEGRADED core row opens the barrier before the cutoff",
      source_barrier_ready([core_degraded, core_done], now=BEFORE_CUTOFF,
                           cutoff=SCHEDULE["source_cutoff"]) is True)
check("a DEGRADED core row is not reported as blocked",
      blocked_core_source_tasks([core_degraded, core_done]) == [])
for stopped in ("TERMINAL", "PARKED"):
    row = {"source_code": "fx", "required_class": "core", "status": stopped}
    check(f"a {stopped} core row still holds the barrier after the cutoff",
          source_barrier_ready([core_degraded, row], now=SCHEDULE["final"],
                               cutoff=SCHEDULE["source_cutoff"]) is False)
    check(f"a {stopped} core row is still reported as blocked",
          blocked_core_source_tasks([core_degraded, row]) == [row])

# Review fix 2026-09-25: the DEGRADED core source that now reaches publish is
# named in degradedSources, so the run is PUBLISHED_DEGRADED, not PUBLISHED.
# A core source retired to SKIPPED stays out, as it always did.
quote_done = {"source_code": "snkrdunk", "required_class": "quote", "status": "COMPLETED"}
gemrate_done = {**core_degraded, "status": "COMPLETED"}
check("a DEGRADED core source is named in degradedSources",
      degraded_source_codes(aggregate_source_health(
          [gemrate_done, core_degraded, core_done, quote_done])) == ["gemrate"])
check("a fully COMPLETED core source is not named",
      degraded_source_codes(aggregate_source_health([gemrate_done, core_done, quote_done])) == [])
check("a core source retired to SKIPPED is not named",
      degraded_source_codes(aggregate_source_health(
          [gemrate_done, {**core_done, "status": "SKIPPED"}, quote_done])) == [])


# ------------------------------------------------------ fake contract MySQL
CONTRACT_TABLES = (
    "market_source_registry", "market_quote_route_policy",
    "market_variant_source_state", "publication_outbox", "publication_delivery",
)


def registry_row(code: str, required_class: str, capabilities: str) -> dict[str, Any]:
    return {
        "source_code": code, "canonical_source_code": code, "identity_source_code": code,
        "required_class": required_class, "enabled": 1,
        "capabilities_json": capabilities, "config_json": None,
    }


REGISTRY = [
    registry_row("fx", "core", '["rates"]'),
    registry_row("gemrate", "core", '["pop", "identity"]'),
    registry_row("pricecharting", "quote", '["quote", "identity"]'),
    registry_row("snkrdunk", "quote", '["quote", "identity"]'),
]


class ContractCursor:
    """Answers current_run_contract's queries for `active` members."""

    def __init__(self, *, active: int = 3, pop_missing: Sequence[int] = (), fx_short: int = 0) -> None:
        self.active = active
        self.pop_missing = set(pop_missing)
        self.fx_short = fx_short
        self._result: list[dict[str, Any]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        flat = " ".join(str(sql).split())
        if "information_schema.tables" in flat:
            self._result = [{"table_name": name} for name in CONTRACT_TABLES]
        elif "FROM market_source_registry ORDER BY source_code" in flat:
            self._result = [dict(row) for row in REGISTRY]
        elif flat.startswith("SELECT COUNT(*) AS n FROM market_universe_member"):
            self._result = [{"n": self.active}]
        elif "current_pop" in flat:
            self._result = [
                {"variant_id": index, "current_pop": 0 if index in self.pop_missing else 1}
                for index in range(1, self.active + 1)
            ]
        elif "current_quote" in flat:
            self._result = [
                {"variant_id": index, "current_quote": 1} for index in range(1, self.active + 1)
            ]
        elif "market_fx_rate_observation" in flat:
            self._result = [{"n": len(SUPPORTED_CURRENCIES) - 1 - self.fx_short}]
        else:
            raise AssertionError(f"unexpected contract query: {flat[:120]}")

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._result)

    def fetchone(self) -> dict[str, Any] | None:
        return dict(self._result[0]) if self._result else None


class FakeConnection:
    def __init__(self, cursor: ContractCursor) -> None:
        self._cursor = cursor

    def cursor(self) -> ContractCursor:
        return self._cursor

    def close(self) -> None:
        pass


def run_contract(cursor: ContractCursor) -> tuple[dict[str, Any] | None, str]:
    """The real stage_contract, with only its MySQL edges faked."""

    saved = (v2db.db, v2db.load_env, v2db.sync_variant_source_states,
             operator_control.operator_e2e_lease)
    v2db.db = lambda: FakeConnection(cursor)
    v2db.load_env = lambda: None
    v2db.sync_variant_source_states = lambda run_id, business_date: {"projected": 0}
    operator_control.operator_e2e_lease = lambda owner: contextlib.nullcontext()
    try:
        result = v2stage.stage_contract(argparse.Namespace(
            run_id=f"cardz-v2:{DAY.isoformat()}", business_date=DAY.isoformat(), label="pre-identity",
        ))
        return result, ""
    except RuntimeError as error:
        return None, str(error)
    finally:
        (v2db.db, v2db.load_env, v2db.sync_variant_source_states,
         operator_control.operator_e2e_lease) = saved


# (b) the contract still fails closed on insufficient coverage.
satisfied, error = run_contract(ContractCursor())
check("(b) a full contract passes", bool(satisfied and satisfied["complete"]), error)
check("(b) the barrier keys are every core source plus quotes",
      bool(satisfied) and satisfied["barrierKeys"] == ["fx", "gemrate", "quotes"],
      satisfied and satisfied["barrierKeys"])
_, gemrate_error = run_contract(ContractCursor(pop_missing=(2,)))
check("(b) a gemrate pop shortfall fails the contract with the repair marker",
      "['gemrate']" in gemrate_error and f"gemrate{CONTRACT_SHORTFALL_MARKER}1" in gemrate_error,
      gemrate_error)
_, fx_error = run_contract(ContractCursor(fx_short=1))
check("(b) a missing fx currency fails the contract",
      "['fx']" in fx_error and f"fx={len(SUPPORTED_CURRENCIES) - 2}/{len(SUPPORTED_CURRENCIES) - 1}" in fx_error,
      fx_error)


# ------------------------------------------------------------- the planner
def new_chain(folder: Path) -> tuple[Journal, DailyChainV2]:
    journal = Journal(folder / "chain.sqlite3")
    journal.initialise()
    journal.ensure_run(
        business_date=DAY.isoformat(),
        source_cutoff_at=iso(SCHEDULE["source_cutoff"]),
        sla_at=iso(SCHEDULE["sla"]),
        final_at=iso(SCHEDULE["final"]),
    )
    chain = DailyChainV2(
        journal=journal, business_date=DAY, allow_publish=False, notify=False,
        deadline_monotonic=time.monotonic() + 600, schedule=SCHEDULE,
    )
    chain.census_fallback_path = None
    return journal, chain


def set_status(journal: Journal, task_key: str, status: str, error: str = "") -> None:
    with closing(sqlite3.connect(str(journal.path))) as conn:
        conn.execute(
            "UPDATE chain_task SET status=?,last_error=?,updated_at=? WHERE task_key=?",
            (status, error or None, iso(), task_key),
        )
        conn.commit()


def settle(journal: Journal, chain: DailyChainV2, force: Mapping[str, str]) -> None:
    for row in journal.tasks(chain.run_id):
        if str(row["status"]) not in CLAIMABLE:
            continue
        status = force.get(str(row["task_key"])) or force.get(str(row["capability"]))
        set_status(journal, str(row["task_key"]), status or "COMPLETED")


def drive(journal: Journal, chain: DailyChainV2, *, until: str, hold: Sequence[str] = (),
          rounds: int = 60) -> bool:
    for _ in range(rounds):
        chain.plan(BEFORE_CUTOFF)
        if chain.stage_row(until) is not None:
            return True
        for row in journal.tasks(chain.run_id):
            if str(row["capability"]) in hold:
                continue
            if str(row["status"]) in CLAIMABLE:
                set_status(journal, str(row["task_key"]), "COMPLETED")
    return False


def providers(journal: Journal, chain: DailyChainV2) -> list[dict[str, Any]]:
    return [
        row for row in journal.tasks(chain.run_id, phase="source")
        if str(row["source_code"]) != "system"
    ]


def parked_events(journal: Journal, chain: DailyChainV2) -> list[dict[str, Any]]:
    return [
        row for row in journal.pending_events(chain.run_id)
        if str(row["event_type"]) == "CORE_TASK_PARKED"
    ]


with tempfile.TemporaryDirectory(prefix="v2-r5-") as raw:
    journal, chain = new_chain(Path(raw))
    # Plan up to the provider tasks, without settling them yet.
    for _ in range(40):
        chain.plan(BEFORE_CUTOFF)
        if providers(journal, chain):
            break
        settle(journal, chain, {})
    gemrate = [row for row in providers(journal, chain) if str(row["source_code"]) == "gemrate"]
    check("(a) gemrate is a core provider with shards", len(gemrate) >= 2
          and all(str(row["required_class"]) == "core" for row in gemrate), gemrate)
    # One gemrate shard finishes DEGRADED; every other provider row completes.
    degraded_key = str(gemrate[0]["task_key"]) if gemrate else ""
    settle(journal, chain, {degraded_key: "DEGRADED"})
    check("(a) the fixture really holds a DEGRADED core row",
          str((journal.task(degraded_key) or {}).get("status")) == "DEGRADED")
    chain.plan(BEFORE_CUTOFF)
    check("(a) a DEGRADED core shard opens the barrier: core-contract-pre is planned",
          chain.stage_row("core-contract-pre") is not None)
    check("(a) and it pages nobody", parked_events(journal, chain) == [])
    # With the contract satisfied the run moves on to the identity stages.
    contract_row = chain.stage_row("core-contract-pre") or {}
    satisfied, error = run_contract(ContractCursor())
    if contract_row and satisfied:
        set_status(journal, str(contract_row["task_key"]), "COMPLETED")
    check("(a) with the contract satisfied the identity stages follow",
          drive(journal, chain, until="identity-operator-apply"), error)

with tempfile.TemporaryDirectory(prefix="v2-r5-short-") as raw:
    journal, chain = new_chain(Path(raw))
    drive(journal, chain, until="core-contract-pre")
    contract_row = chain.stage_row("core-contract-pre") or {}
    _, shortfall = run_contract(ContractCursor(pop_missing=(1, 3)))
    set_status(journal, str(contract_row.get("task_key")), "RETRY", shortfall)
    chain.plan_contract_repair_tasks = Mock(return_value=1)
    for _ in range(3):
        chain.plan(BEFORE_CUTOFF)
    check("(b) a short contract plans the repair",
          chain.plan_contract_repair_tasks.called, shortfall)
    check("(b) a short contract never lets the identity stages start",
          chain.stage_row("identity-operator-apply") is None)
    set_status(journal, str(contract_row.get("task_key")), "TERMINAL", shortfall)
    chain.plan(BEFORE_CUTOFF)
    check("(b) a contract that ran out of attempts still holds the run",
          chain.stage_row("identity-operator-apply") is None)


# ------------------------------------------------ (c) GemRate block fallback
class BlockedPopCursor(ContractCursor):
    """Variant 2's last GemRate pop is two JST days old; 1 and 3 popped today."""

    def __init__(self) -> None:
        super().__init__()
        start, _end = v2db.business_window_utc(DAY.isoformat())
        self.stale_at = start - timedelta(days=2)

    def execute(self, sql: str, params: Any = ()) -> None:
        if "current_pop" not in " ".join(str(sql).split()):
            super().execute(sql, params)
            return
        window_start, window_end = tuple(params)[:2]
        self._result = [
            {"variant_id": index,
             "current_pop": int(index != 2 or window_start <= self.stale_at < window_end)}
            for index in range(1, self.active + 1)
        ]


def set_result(journal: Journal, task_key: str, result_json: str) -> None:
    with closing(sqlite3.connect(str(journal.path))) as conn:
        conn.execute("UPDATE chain_task SET result_json=? WHERE task_key=?", (result_json, task_key))
        conn.commit()


def degraded_gemrate_chain(folder: Path, error_code: str | None) -> tuple[Journal, DailyChainV2]:
    """One gemrate shard settles DEGRADED through the real adapter ingest."""

    journal, chain = new_chain(folder)
    for _ in range(40):
        chain.plan(BEFORE_CUTOFF)
        if providers(journal, chain):
            break
        settle(journal, chain, {})
    gemrate = [row for row in providers(journal, chain) if str(row["source_code"]) == "gemrate"]
    key = str(gemrate[0]["task_key"]) if gemrate else ""
    settle(journal, chain, {key: "DEGRADED"})
    receipt = {"contract": "cardz-source-result-v2", "sourceCode": "gemrate", "status": "degraded",
               "errorCode": error_code, "counts": {"processed": 2, "failed": 1}}
    result = chain.registry.get("gemrate").ingest(
        SourceTask(run_id=chain.run_id, business_date=DAY.isoformat(),
                   source_code="gemrate", capability="pop"),
        {"receipt": receipt, "exitCode": 0}, {},
    )
    set_result(journal, key, canonical_json(asdict(result)).decode())
    chain.plan(BEFORE_CUTOFF)
    return journal, chain


class CommandCaptured(Exception):
    pass


def contract_command(chain: DailyChainV2, folder: Path) -> list[str]:
    """The command _run_stage_process builds for core-contract-pre; nothing spawns."""

    captured: list[list[str]] = []

    def fake_popen(command: Sequence[str], **_kwargs: Any) -> Any:
        captured.append([str(part) for part in command])
        raise CommandCaptured()

    saved = (v2core.subprocess, chain.log_dir, chain.receipt_dir,
             os.environ.get(v2core.RUN_STARTED_AT_ENV))
    v2core.subprocess = types.SimpleNamespace(
        Popen=fake_popen, DEVNULL=subprocess.DEVNULL, STDOUT=subprocess.STDOUT,
    )
    chain.log_dir, chain.receipt_dir = folder / "logs", folder / "receipts"
    try:
        chain._run_stage_process(chain.stage_row("core-contract-pre") or {})
    except CommandCaptured:
        pass
    finally:
        v2core.subprocess, chain.log_dir, chain.receipt_dir = saved[:3]
        if saved[3] is None:
            os.environ.pop(v2core.RUN_STARTED_AT_ENV, None)
        else:
            os.environ[v2core.RUN_STARTED_AT_ENV] = saved[3]
    return captured[0] if captured else []


def run_stage_main(command: Sequence[str], cursor: ContractCursor) -> tuple[int, dict[str, Any]]:
    """The real stage CLI on exactly that command line, MySQL edges faked."""

    output = Path(command[command.index("--output") + 1])
    env_keys = ("CARDZ_DAILY_CHAIN_V2", "CARDZ_V2_RUN_ID", "CARDZ_V2_BUSINESS_DATE")
    saved_env = {key: os.environ.get(key) for key in env_keys}
    saved_signals = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
    saved = (sys.argv, v2db.db, v2db.load_env, v2db.sync_variant_source_states,
             operator_control.operator_e2e_lease)
    sys.argv = list(command[4:])
    v2db.db = lambda: FakeConnection(cursor)
    v2db.load_env = lambda: None
    v2db.sync_variant_source_states = lambda run_id, business_date: {"projected": 0}
    operator_control.operator_e2e_lease = lambda owner: contextlib.nullcontext()
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = v2stage.main()
    finally:
        (sys.argv, v2db.db, v2db.load_env, v2db.sync_variant_source_states,
         operator_control.operator_e2e_lease) = saved
        for sig, handler in saved_signals.items():
            signal.signal(sig, handler)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    receipt = json.loads(output.read_text(encoding="utf-8")) if output.is_file() else {}
    return rc, receipt


def planner_fallback(chain: DailyChainV2) -> list[dict[str, int]]:
    seen: list[dict[str, int]] = []

    def fake_contract(business_date: str, pop_fallback_days: Mapping[str, int] | None = None):
        seen.append(dict(pop_fallback_days or {}))
        return {"popSources": [], "quotes": {"missing": []}}

    saved = v2db.current_run_contract
    v2db.current_run_contract = fake_contract
    try:
        chain.plan_contract_repair_tasks()
    finally:
        v2db.current_run_contract = saved
    return seen


with tempfile.TemporaryDirectory(prefix="v2-block-") as raw:
    journal, chain = degraded_gemrate_chain(Path(raw), GEMRATE_BLOCKED_ERROR_CODE)
    check("(c) a blocked gemrate shard still opens the barrier",
          chain.stage_row("core-contract-pre") is not None)
    command = contract_command(chain, Path(raw))
    check("(c) the contract stage is handed the approved fallback",
          command[-2:] == ["--pop-fallback", f"gemrate={POP_BLOCKED_FALLBACK_DAYS}"], command)
    rc, receipt = run_stage_main(command, BlockedPopCursor())
    # A terminal receipt's "contract" is the receipt name, not the run contract.
    run_contract = receipt.get("contract")
    gemrate_section = (run_contract.get("gemrate") if isinstance(run_contract, Mapping) else None) or {}
    check("(c) the real stage takes the two-day-old pop and completes",
          rc == 0 and receipt.get("status") == "completed", receipt.get("error"))
    check("(c) the receipt names the fallback and how much stands on it",
          receipt.get("popFallback") == {"gemrate": POP_BLOCKED_FALLBACK_DAYS}
          and gemrate_section.get("staleCovered") == 1
          and gemrate_section.get("sameDayCovered") == 2, gemrate_section)
    check("(c) the repair planner measures the same window",
          planner_fallback(chain) == [{"gemrate": POP_BLOCKED_FALLBACK_DAYS}])

with tempfile.TemporaryDirectory(prefix="v2-block-plain-") as raw:
    journal, chain = degraded_gemrate_chain(Path(raw), None)
    command = contract_command(chain, Path(raw))
    check("(c) a plain DEGRADED gemrate shard earns no fallback",
          bool(command) and "--pop-fallback" not in command, command)
    rc, receipt = run_stage_main(command, BlockedPopCursor())
    check("(c) so the same two-day-old pop still fails the contract",
          rc == 1 and receipt.get("status") == "terminal"
          and f"gemrate{CONTRACT_SHORTFALL_MARKER}1" in str(receipt.get("error")),
          receipt.get("error"))
    check("(c) and the planner sees no fallback either", planner_fallback(chain) == [{}])

if failures:
    print(f"FAILED {len(failures)}: {failures}")
    raise SystemExit(1)
print("ALL_OK test_v2_restructure_settled")
