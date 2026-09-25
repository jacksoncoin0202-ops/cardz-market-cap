#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bounded non-source stages invoked by the Daily Chain V2 orchestrator."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
# Below this much remaining tick, the browser lane re-proves the pages it
# already has instead of opening a slow Cloudflare fetch it cannot finish.
REVERIFY_FETCH_SECONDS = 600
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import (  # noqa: E402
    IDENTITY_LANE_FALLBACK,
    TICK_RESERVE_SECONDS,
    WORK_DEADLINE_ENV,
    canonical_json,
    classify_error,
    contract_shortfall,
    core_contract_keys,
    daily_generation_sha256,
    identity_lanes,
    list_v2_migrations,
    sha256,
)
from collection_contract import CHECKPOINT_ADAPTERS  # noqa: E402

# Repair every adapter enforced by daily-accept.  Restricting this stage to the
# two browser lanes left newly exact SNK streams permanently outside the graph:
# their source task correctly ignores residual ``stock`` rows, then accept
# retried the same missing checkpoints four times with no task able to mint
# them.  The shared collection contract is the authority, so a future adapter
# cannot enter the gate without entering repair too.
CHECKPOINT_REPAIR_ADAPTERS: tuple[str, ...] = tuple(CHECKPOINT_ADAPTERS)
# Wall-clock epoch this stage must stop by, published by the orchestrator with
# TICK_RESERVE_SECONDS already subtracted, so that reserve keeps exactly one
# definition (daily_chain_v2_contract.TICK_RESERVE_SECONDS).
STAGE_DEADLINE_ENV = "CARDZ_V2_STAGE_DEADLINE_EPOCH"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(canonical_json(value) + b"\n")
    os.replace(temporary, path)


def _run(command: list[str], *, timeout: int) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    if proc.returncode != 0:
        raise RuntimeError(
            f"stage command exit={proc.returncode}:"
            f" {' / '.join(combined.splitlines()[-20:])}"
        )
    return {
        "exitCode": proc.returncode,
        "outputTail": "\n".join(combined.splitlines()[-20:]),
    }


def migrate_command(root: Path) -> list[str]:
    """Migrate exactly the V2 files that exist; adding one edits no code."""

    names = list_v2_migrations(root)
    if not names:
        raise RuntimeError(f"no daily-chain V2 migrations found under {root}")
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(root / "pipelines" / "db_runtime.py"),
        "migrate",
    ]
    for name in names:
        command.extend(["--only", name])
    return command


def stage_migrate(_args: argparse.Namespace) -> dict[str, Any]:
    command = migrate_command(ROOT)
    result = _run(command, timeout=900)
    return {
        "stage": "migrate",
        "migrations": list_v2_migrations(ROOT),
        **result,
    }


def stage_registry(_args: argparse.Namespace) -> dict[str, Any]:
    import collect_control
    from daily_chain_v2_adapters import build_default_registry
    from daily_chain_v2_db import sync_source_registry

    report = collect_control.cmd_status(rebuild_registry=True)
    registry = collect_control._jsonl_rows(collect_control.REGISTRY_PATH)
    if not registry:
        raise RuntimeError("V2 collection registry is empty")
    pc_map = _run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "consolidate_pc_map.py"),
            "--write",
        ],
        timeout=300,
    )
    source_registry = sync_source_registry(
        adapter.spec for adapter in build_default_registry().enabled()
    )
    return {
        "stage": "registry",
        "registryPath": str(collect_control.REGISTRY_PATH),
        "rows": len(registry),
        "counts": report.get("counts") or {},
        "pcMap": pc_map,
        "sourceRegistry": source_registry,
        "payloadSha256": sha256(registry),
    }


def stage_contract(args: argparse.Namespace) -> dict[str, Any]:
    import operator_control
    from daily_chain_v2_db import current_run_contract, sync_variant_source_states

    with operator_control.operator_e2e_lease(f"v2-contract:{args.label}"):
        projected = sync_variant_source_states(args.run_id, args.business_date)
        contract = current_run_contract(args.business_date)
    # Registry-driven barrier: every enabled core source plus the shared quote
    # coverage.  The literal tuple survives only inside core_contract_keys as
    # the no-registry fallback, so a new core source blocks publication by
    # being registered, not by being added to a list here.
    barrier = core_contract_keys(contract.get("sources"))
    failures = [
        name for name in barrier
        if not bool((contract.get(name) or {}).get("complete"))
    ]
    result = {
        "stage": "contract",
        "label": args.label,
        "projection": projected,
        "contract": contract,
        "barrierKeys": list(barrier),
        "complete": not failures,
        "failures": failures,
    }
    if failures:
        raise RuntimeError(
            f"V2 core contract incomplete at {args.label}: {failures};"
            f" {contract_shortfall(contract, barrier)}"
        )
    return result


def _candidate_state_paths(discovery: Any) -> list[Path]:
    return [
        discovery.STATE_PATH,
        discovery.STATE_PATH.with_name("daily-discovery-state-v2-http.json"),
        discovery.STATE_PATH.with_name("daily-discovery-state-v2-browser.json"),
    ]


def _lane_state_path(discovery: Any, lane: str) -> Path:
    return discovery.STATE_PATH.with_name(f"daily-discovery-state-v2-{lane}.json")


def _ensure_lane_state(discovery: Any, lane: str, snapshot: Mapping[str, Any]) -> Path:
    path = _lane_state_path(discovery, lane)
    if path.is_file():
        try:
            discovery._load_state(path, snapshot["generation"])
            return path
        except SystemExit:
            # The DB ledger is the authority; a cursor from an older catalog
            # generation must not block the next natural V2 run.
            pass
    current_ids = sorted(int(row["variant_id"]) for row in snapshot["gapRows"])
    known_ids = current_ids
    if discovery.STATE_PATH.is_file():
        try:
            legacy = discovery._load_state(discovery.STATE_PATH, snapshot["generation"])
            known_ids = legacy["knownGapIds"]
        except SystemExit:
            pass
    discovery._write_state(
        path,
        snapshot["generation"],
        known_ids,
        [],
        f"v2-{lane}-cursor-initialized",
    )
    return path


def _pending_identity_ids(discovery: Any) -> tuple[dict[str, Any], list[int]]:
    snapshot = discovery._runtime_snapshot(None)
    pending: set[int] = set()
    for path in _candidate_state_paths(discovery):
        if path.is_file():
            try:
                state = discovery._load_state(path, snapshot["generation"])
            except SystemExit:
                continue
            pending.update(int(value) for value in state["pendingActivationIds"])
    active = {int(value) for value in snapshot["universeIds"]}
    for row in discovery._load_ledger_rows(None):
        if str(row.get("discovery_status") or "") == "inactive_exact":
            pending.add(int(row["variant_id"]))
    return snapshot, sorted(pending - active)


def stage_discover(args: argparse.Namespace) -> dict[str, Any]:
    import daily_discovery_activation as discovery

    snapshot = discovery._runtime_snapshot(None)
    state = _ensure_lane_state(discovery, args.lane, snapshot)
    previous = os.environ.get(discovery.DAILY_CHAIN_ENV)
    os.environ[discovery.DAILY_CHAIN_ENV] = "1"
    try:
        code = discovery.cmd_daily_discover_activate(
            SimpleNamespace(
                lane=args.lane,
                initialize=False,
                credentials_env=None,
                state_path=state,
            )
        )
    finally:
        if previous is None:
            os.environ.pop(discovery.DAILY_CHAIN_ENV, None)
        else:
            os.environ[discovery.DAILY_CHAIN_ENV] = previous
    if code != 0:
        raise RuntimeError(f"identity discovery lane={args.lane} exited {code}")
    current = json.loads(state.read_text(encoding="utf-8"))
    return {
        "stage": "discover",
        "lane": args.lane,
        "pendingActivationIds": current.get("pendingActivationIds") or [],
        "knownGapIds": current.get("knownGapIds") or [],
        "statePath": str(state),
    }


def stage_pending(_args: argparse.Namespace) -> dict[str, Any]:
    import daily_discovery_activation as discovery

    snapshot, pending = _pending_identity_ids(discovery)
    return {
        "stage": "pending",
        "generation": snapshot["generation"],
        "pendingActivationIds": pending,
        "pendingCount": len(pending),
    }


def _stage_seconds_remaining(now: float | None = None) -> float | None:
    """Seconds left of the orchestrator's own deadline, or None standalone."""

    raw = str(os.environ.get(WORK_DEADLINE_ENV) or "").strip()
    if not raw:
        return None
    try:
        return float(raw) - (time.time() if now is None else now)
    except ValueError:
        return None


def _rebuild_artifacts(prefix: str) -> set[str]:
    folder = ROOT / "data" / "runtime" / "rebuild-036"
    if not folder.is_dir():
        return set()
    return {path.name for path in folder.glob(f"{prefix}*.json")}


def _newest_new_artifact(prefix: str, before: set[str]) -> Path | None:
    folder = ROOT / "data" / "runtime" / "rebuild-036"
    fresh = sorted(
        path for path in folder.glob(f"{prefix}*.json") if path.name not in before
    )
    return fresh[-1] if fresh else None


def stage_operator_apply(args: argparse.Namespace) -> dict[str, Any]:
    """Drain last night's operator pastes through the one bind entry point.

    Every item that receives a verdict leaves the inbox: its verdict is durable
    in its own receipt, and an item that stays would re-spend this stage's
    attempts every morning.  The one exception is EXIT_CHAIN_CHANGED, which
    means "the chain moved this row, run it again" -- that item is left in the
    inbox on purpose and reported as retryable.
    """

    import operator_bind
    import operator_control
    import rebuild_036 as rebuild

    inbox = Path(args.inbox) if args.inbox else (
        ROOT / "data" / "runtime" / "operator" / "bind" / "inbox"
    )
    items = operator_bind.inbox_items(inbox) if inbox.is_dir() else []
    result: dict[str, Any] = {
        "stage": "operator-apply",
        "inbox": str(inbox),
        "seen": len(items),
        "drained": 0,
        "applied": [],
        "refused": [],
        "retryable": [],
    }
    if not items:
        return result

    done = inbox / "done"
    connection = rebuild.connect(rebuild.DAILY_CREDENTIALS_ENV)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time=60000")
        with operator_control.operator_e2e_lease("v2-operator-apply"):
            for path, item in items:
                report = operator_bind.apply_one(
                    variant_id=int(item["variantId"]),
                    url=str(item["url"]),
                    actor=str(item.get("actor") or "chain"),
                    note=item.get("note"),
                    write=True,
                    operator_ruling_slug=item.get("operatorRuling"),
                    freeze=bool(item.get("freeze")),
                    conn=connection,
                    # Same default as the bind-url CLI.  apply_one only writes
                    # its 10-step verdict document when it is given a
                    # receipts_dir, and this stage files the item away right
                    # after -- without this the only durable evidence of a
                    # lease-held DB write would be gone.
                    receipts_dir=ROOT / "data" / "runtime" / "operator" / "bind-url",
                )
                row = {
                    "file": path.name,
                    "variantId": int(item["variantId"]),
                    "sourceCode": report.get("sourceCode"),
                    "verdict": report.get("verdict"),
                    "gate": report.get("gate"),
                    "exitCode": int(report.get("exitCode") or 0),
                    "receiptPath": report.get("receiptPath"),
                }
                if row["exitCode"] == operator_bind.EXIT_CHAIN_CHANGED:
                    result["retryable"].append(row)
                    continue
                done.mkdir(parents=True, exist_ok=True)
                path.replace(done / path.name)
                if row["exitCode"] == operator_bind.EXIT_OK:
                    result["applied"].append(row)
                else:
                    result["refused"].append(row)
    finally:
        connection.close()
    result["drained"] = len(result["applied"]) + len(result["refused"])
    return result


def stage_identity_intake(args: argparse.Namespace) -> dict[str, Any]:
    """Take in the cards GemRate says crossed the population floor.

    `--apply` is reached only here, and gemrate_identity_intake.run() holds the
    v2-identity-intake operator lease around every write.  A stale or missing
    census takes in nothing and says so: this stage may never be read as
    "no new cards" when it simply did not look at fresh numbers.
    """

    import gemrate_identity_intake as intake
    import rebuild_036 as rebuild

    now = datetime.now(timezone.utc)
    business_date = str(args.business_date or now.strftime("%Y-%m-%d"))
    explicit_census = getattr(args, "census", None)
    census_path = Path(explicit_census).resolve() if explicit_census else None
    census = intake.census(census_path, max_age_days=int(args.max_age_days), now=now)
    connection = rebuild.connect(rebuild.DAILY_CREDENTIALS_ENV)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SET SESSION max_execution_time=60000")
        report, code = intake.run(
            connection,
            census_result=census,
            max_seed=int(args.max_seed),
            do_apply=True,
            now=now,
        )
    finally:
        connection.close()
    receipt = intake.write_receipt(report, business_date=business_date)
    interned = list(report.get("interned") or [])
    result: dict[str, Any] = {
        "stage": "intake",
        "generation": report.get("generation"),
        "censusPath": report.get("censusPath"),
        "censusMtime": report.get("censusMtime"),
        "censusStale": bool(report.get("censusStale")),
        "censusMissing": bool(report.get("censusMissing")),
        "buckets": report.get("buckets"),
        "headroom": report.get("headroom"),
        "internedCount": len(interned),
        "internedVariantIds": [
            int(row["variantId"]) for row in interned if row.get("variantId")
        ],
        "deferredByRatchet": len(report.get("deferredByRatchet") or []),
        "needsHuman": len(report.get("needsHuman") or []),
        "receiptPath": str(receipt),
    }
    if result["censusStale"] or result["censusMissing"]:
        # Said out loud in the stage result, because the morning brief reads
        # this and an unread census is not the same fact as an empty one.
        result["censusNote"] = (
            "census is stale or missing: zero cards taken in, this is NOT"
            " evidence that no card crossed the floor"
        )
    if code != 0:
        raise RuntimeError(f"identity intake refused: {report.get('applyRefused')}")
    return result


def stage_identity_reverify(args: argparse.Namespace) -> dict[str, Any]:
    """Re-prove held identity bindings on one lane, through the real gates.

    The acceptance contract is rebuild_036's and nothing here relaxes it.  What
    this stage adds is a budget: the browser lane fetches missing pages only
    while the tick still has room for a slow Cloudflare morning, and neither
    lane starts at all inside the orchestrator's reserve.
    """

    import rebuild_036 as rebuild

    lane = str(args.lane)
    scope = sorted({int(value) for value in (args.variant_id or [])})
    remaining = _stage_seconds_remaining()
    result: dict[str, Any] = {
        "stage": "reverify",
        "lane": lane,
        "variantIds": scope,
        "secondsRemaining": None if remaining is None else round(remaining, 1),
    }
    if remaining is not None and remaining <= TICK_RESERVE_SECONDS:
        # Nothing was attempted, so nothing is claimed.  The next tick owns it.
        return {
            **result, "ran": False, "skipped": "tick-reserve",
            "counts": {}, "held": [], "promotable": 0, "fetchMissing": False,
        }
    fetch_missing = lane == "browser" and (
        remaining is None or remaining > REVERIFY_FETCH_SECONDS
    )
    if lane == "browser":
        prefix = "pc-identity-reverify-"
        namespace = SimpleNamespace(
            credentials_env=None, pages_dir=None, map=None, write=True,
            fetch_missing=fetch_missing,
            variant_ids=scope if scope else None,
        )
        command = rebuild.cmd_pc_identity_reverify
    else:
        prefix = "snk-identity-reverify-"
        namespace = SimpleNamespace(
            credentials_env=None, write=True,
            variant_ids=scope if scope else None,
        )
        command = rebuild.cmd_snk_identity_reverify
    before = _rebuild_artifacts(prefix)
    code = command(namespace)
    if code != 0:
        raise RuntimeError(f"identity reverify lane={lane} exited {code}")
    artifact = _newest_new_artifact(prefix, before)
    if artifact is None:
        raise RuntimeError(f"identity reverify lane={lane} wrote no artifact")
    report = json.loads(artifact.read_text(encoding="utf-8"))
    return {
        **result,
        "ran": True,
        "fetchMissing": fetch_missing,
        "counts": report.get("counts") or {},
        # First class, so the morning brief reads today's holds from this run
        # instead of scraping whatever artifact happens to be newest on disk.
        "held": report.get("held") or [],
        "promotable": int(report.get("promotable") or 0),
        "artifact": str(artifact),
    }


def stage_identity_brief(args: argparse.Namespace) -> dict[str, Any]:
    """Render the morning identity brief and journal it for delivery.

    The rendered HTML travels inside the event payload and
    DailyChainV2._event_message returns it verbatim, so the links survive.

    The dedupe state travels with it and is stamped by deliver_events, never
    here: render() reads `seen` and drops the rows it lists, so a stage that
    stamped before delivery would make its own retry render a hollow brief --
    a new event key, a second message, and on the attempt that died before
    add_event the hollow one would be the only brief the owner ever saw.
    """

    # One journal, two env names: daily_chain_v2.py:2358 exports the run's
    # journal as CARDZ_V2_STATE_DB, while daily_chain_v2_journal.py:79
    # (`default_state_path`) reads CARDZ_DAILY_V2_STATE_DB, which the chain
    # never sets.  identity_brief._journal_identity_phase() goes through
    # default_state_path(), so without this bridge line 2 of the brief reports
    # a DIFFERENT journal -- usually the empty default -- as "身份階段未知".
    # setdefault, so an explicitly configured journal still wins.
    state_db = os.environ.get("CARDZ_V2_STATE_DB", "").strip()
    if state_db:
        os.environ.setdefault("CARDZ_DAILY_V2_STATE_DB", state_db)

    import identity_brief

    now = datetime.now(timezone.utc)
    business_date = str(args.business_date or now.strftime("%Y-%m-%d"))
    seen = identity_brief.load_seen()
    built = identity_brief.build(now=now, business_date=business_date, seen=seen)
    data = built.get("data") or {}
    message = str(built.get("message") or "")

    event_key = ""
    journal_path = os.environ.get("CARDZ_V2_STATE_DB")
    run_id = os.environ.get("CARDZ_V2_RUN_ID")
    if journal_path and run_id:
        from daily_chain_v2_journal import Journal

        event_key = f"{business_date}:{sha256(message)[:16]}"
        Journal(Path(journal_path)).add_event(
            run_id,
            identity_brief.BRIEF_EVENT_TYPE,
            event_key,
            {
                "runId": run_id,
                "businessDate": business_date,
                "generation": data.get("generation"),
                "message": message,
                "seen": built.get("seen") or {},
            },
        )
    return {
        "stage": "brief",
        "businessDate": business_date,
        "generation": data.get("generation"),
        "population": data.get("population"),
        "needsYou": len(data.get("needsYou") or []),
        "messageChars": len(message),
        "eventType": identity_brief.BRIEF_EVENT_TYPE,
        "eventKey": event_key,
    }


def stage_noop(args: argparse.Namespace) -> dict[str, Any]:
    detail = json.loads(args.detail_json)
    if not isinstance(detail, Mapping):
        raise RuntimeError("noop detail must be an object")
    return {"stage": "noop", "detail": dict(detail)}


def stage_consolidate(_args: argparse.Namespace) -> dict[str, Any]:
    result = _run(
        [
            sys.executable, "-X", "utf8",
            str(ROOT / "pipelines" / "consolidate_pc_map.py"), "--write",
        ],
        timeout=900,
    )
    return {"stage": "consolidate", **result}


def stage_deadline_budget_seconds(now_epoch: float | None = None) -> float | None:
    """Seconds of tick budget left, or None when no deadline was published."""

    raw = os.environ.get(STAGE_DEADLINE_ENV, "").strip()
    if not raw:
        return None
    try:
        deadline = float(raw)
    except ValueError:
        return None
    return deadline - (time.time() if now_epoch is None else now_epoch)


def missing_repair_streams(collect_control: Any, operator_control: Any) -> dict[str, list[dict[str, Any]]]:
    """Registry streams with no checkpoint, by the daily-accept gate's rule.

    `operator_control.missing_checkpoint_streams` applies the same
    missing-stream rule as the daily-accept checkpoint gate
    (`operator_control._active_checkpoint_gate`, which re-derives it inline in
    its own `zip(streams, keys)` loop at operator_control.py:1831-1837).  They
    are two separate code paths and nothing forces them to agree, so
    `scripts/test_checkpoint_repair_stage.py` pins them to the same verdict on
    one fixture instead of assuming it.
    """

    registry = collect_control._jsonl_rows(collect_control.REGISTRY_PATH)
    collect_control.load_env()
    conn = collect_control.db()
    try:
        cur = conn.cursor()
        return operator_control.missing_checkpoint_streams(
            cur, registry=registry, adapters=CHECKPOINT_REPAIR_ADAPTERS
        )
    finally:
        conn.close()


def stage_checkpoint_repair(_args: argparse.Namespace) -> dict[str, Any]:
    """Give every gate-enforced stream without a checkpoint its first checkpoint.

    2026-08-22: activation bound 50 new `pc_ebay_sales` streams and 48
    `en_price_ref` streams after the collection registry had already been
    built, so daily-accept hard-failed attempts 1-3 on `checkpoint gate failed
    pc_ebay_sales: streams=1171 missing=50` and attempt 4 on `en_price_ref ...
    fresh_pc_pages_unavailable` (the local PC page was inside the 36 h SLA, so
    the incr path replayed it instead of fetching, and a replay mints no first
    checkpoint).  The operator finished the run by hand with
    `collect_control incr --variant-id ... --force-network`.

    `cmd_first_stock(force_network=True)` is that hand repair as one call:
    `cmd_status(rebuild_registry=True)` -> `consolidate_pc_map --write` ->
    `cmd_stock(variant_ids=<only the missing streams>, force_network=True)`,
    where force_network is exactly what skips the SLA replay.  The checkpoint
    gate is not touched: a stream that still has no checkpoint after this
    stage still fails it.
    """

    import collect_control
    import operator_control

    started = time.monotonic()
    missing_before = missing_repair_streams(collect_control, operator_control)
    counts_before = {
        adapter: len(missing_before.get(adapter) or ())
        for adapter in CHECKPOINT_REPAIR_ADAPTERS
    }
    repair_adapters = [
        adapter for adapter in CHECKPOINT_REPAIR_ADAPTERS if counts_before[adapter]
    ]
    result: dict[str, Any] = {
        "stage": "checkpoint-repair",
        "adapters": list(CHECKPOINT_REPAIR_ADAPTERS),
        "streamsMissingBefore": counts_before,
        "streamsMissingAfter": dict(counts_before),
        "repairAdapters": repair_adapters,
        "variantIds": {
            adapter: sorted({int(row["variantId"]) for row in missing_before.get(adapter) or ()})
            for adapter in repair_adapters
        },
        "network": False,
    }
    if not repair_adapters:
        # Nothing was bound after the registry ran; do not open Chrome, do not
        # rebuild the registry, do not consolidate the map.
        result["note"] = "every registry stream already has a checkpoint"
        result["elapsedSeconds"] = round(time.monotonic() - started, 3)
        return result

    budget = stage_deadline_budget_seconds()
    if budget is not None and budget <= 0:
        raise RuntimeError(
            "checkpoint repair deferred to the next tick with no network: "
            f"budgetSeconds={budget:.1f} missing={counts_before}"
        )
    result["network"] = True
    result["budgetSeconds"] = None if budget is None else round(budget, 1)
    # force_network is the operator catch-up flag that morning/nightly must not
    # use for a whole-universe fetch; `cmd_first_stock` is its one sanctioned
    # automated caller and `collect_control.assert_force_network_scope` holds it
    # to the checkpoint-less streams.
    #
    # `first-stock` is in COLLECT_E2E_LEASE_COMMANDS: adapter leases alone do
    # not serialize a mutating collect against daily-accept, and calling the
    # function instead of the CLI must not drop that lease.
    with operator_control.operator_e2e_lease("v2-checkpoint-repair"):
        first_stock = collect_control.cmd_first_stock(
            adapters=list(repair_adapters),
            limit=None,
            dry_run=False,
            delay=0.0,
            workers=24,
            ensure_browser=True,
            pc_resume_report=None,
            pc_sleep=None,
            pc_workers=None,
            force_network=True,
        )
        result["firstStock"] = {
            "ok": bool(first_stock.get("ok")),
            "error": first_stock.get("error"),
            "errorClass": first_stock.get("errorClass"),
            "missing": first_stock.get("missing"),
            "ran": first_stock.get("ran"),
        }
        counts_after = {
            adapter: len(rows or ())
            for adapter, rows in missing_repair_streams(
                collect_control, operator_control
            ).items()
        }
    result["streamsMissingAfter"] = counts_after
    result["elapsedSeconds"] = round(time.monotonic() - started, 3)
    if not first_stock.get("ok"):
        # review 2026-08-24 (major): the 9333 single-flight probe refuses this
        # repair while the lane's OWN child is still sweeping.  That is
        # contention, not a repair that failed -- and this stage rides
        # max_attempts=3 on the 60s/120s ladder, so all three attempts land
        # inside the child's 360 s stamp window and the stage is
        # deterministically killed by a child that is doing its job.  Defer to
        # the next tick, in the same shape as the exhausted-budget deferral
        # above; the error code keeps it off the failure ladder.
        if (
            str(first_stock.get("errorClass") or "")
            == collect_control.PC_CHILD_ALREADY_RUNNING_CLASS
        ):
            raise RuntimeError(
                "checkpoint repair deferred to the next tick: "
                "errorCode=PC_CHILD_ALREADY_RUNNING the 9333 child is still sweeping; "
                f"missing={counts_before}"
            )
        raise RuntimeError(
            f"checkpoint repair first-stock failed: {first_stock.get('error')};"
            f" before={counts_before} after={counts_after}"
        )
    still_missing = {
        adapter: count for adapter, count in counts_after.items() if count
    }
    if still_missing:
        raise RuntimeError(
            "checkpoint repair left streams without a checkpoint: "
            f"{still_missing}; before={counts_before}"
        )
    return result


def _activation_eligibility(
    discovery: Any,
    *,
    generation: str,
    pending: list[int],
    business_date: str,
) -> tuple[list[int], list[dict[str, Any]]]:
    """Return append-eligible candidates without changing the active universe.

    A discovery cursor is only a request to evaluate a card.  It is not proof
    that the card now satisfies the public product contract.  Re-check the
    canonical DB and the content-addressed image trio immediately before the
    append-only lock transaction.
    """

    if not pending:
        return [], []
    from daily_chain_v2_db import business_window_utc

    start, end = business_window_utc(business_date)
    placeholders = ",".join(["%s"] * len(pending))
    connection = discovery.R.connect(discovery.R.DAILY_CREDENTIALS_ENV)
    facts: dict[int, dict[str, Any]] = {}
    quote_variants: set[int] = set()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT rm.variant_id,rm.cohort,rm.identity_pending,
                       rm.latest_psa10_population,
                       ledger.discovery_status,ledger.blocker_code,
                       printing.canonical_printing_sha256,
                       image.canonical_image_content_sha256,
                       MAX(CASE
                         WHEN checkpoint.last_effective_at>=%s
                          AND checkpoint.last_effective_at<%s
                          AND checkpoint.last_payload_sha256 REGEXP '^[0-9a-f]{{64}}$'
                         THEN 1 ELSE 0 END) AS current_gemrate_receipt
                  FROM catalog_rebuild_member rm
                  LEFT JOIN market_identity_discovery_ledger ledger
                    ON ledger.variant_id=rm.variant_id
                  LEFT JOIN catalog_printing_identity printing
                    ON printing.variant_id=rm.variant_id
                  LEFT JOIN operator_canonical_image_projection image
                    ON image.variant_id=rm.variant_id
                  LEFT JOIN operator_strict_source_identity gemrate
                    ON gemrate.variant_id=rm.variant_id
                   AND gemrate.source_code='gemrate'
                  LEFT JOIN market_ingest_checkpoint checkpoint
                    ON checkpoint.source_code='gemrate_pop'
                   AND checkpoint.stream_key=CONCAT(
                         rm.variant_id,':',gemrate.external_entity_id
                       )
                 WHERE rm.generation_id=%s
                   AND rm.variant_id IN ({placeholders})
                 GROUP BY rm.variant_id,rm.cohort,rm.identity_pending,
                          rm.latest_psa10_population,ledger.discovery_status,
                          ledger.blocker_code,printing.canonical_printing_sha256,
                          image.canonical_image_content_sha256
                """,
                (start, end, generation, *pending),
            )
            facts = {int(row["variant_id"]): dict(row) for row in cursor.fetchall()}
            cursor.execute(
                f"""
                SELECT DISTINCT quote.variant_id
                  FROM market_current_quote_revision quote
                  INNER JOIN catalog_printing_identity printing
                    ON printing.variant_id=quote.variant_id
                  INNER JOIN market_source_registry registry
                    ON registry.source_code=quote.source_code
                   AND registry.enabled=1
                   AND JSON_CONTAINS(
                         registry.capabilities_json,JSON_QUOTE('quote'),'$'
                       )=1
                  INNER JOIN operator_strict_source_identity identity
                    ON identity.variant_id=quote.variant_id
                   AND identity.source_code=registry.identity_source_code
                   AND identity.external_entity_id=quote.source_external_entity_id
                 WHERE quote.variant_id IN ({placeholders})
                   AND quote.checked_at>=%s AND quote.checked_at<%s
                   AND quote.price_usd>0
                   AND quote.payload_sha256 REGEXP '^[0-9a-f]{{64}}$'
                   AND quote.quote_lineage_sha256 REGEXP '^[0-9a-f]{{64}}$'
                   AND (quote.reconstruction_kind IS NULL
                        OR quote.reconstruction_kind IN (
                          'bootstrap_from_observation',''
                        ))
                   AND EXISTS (
                     SELECT 1 FROM market_quote_route_policy route
                      WHERE route.source_code=quote.source_code
                        AND route.is_active=1 AND route.is_eligible=1
                        AND route.language_code IN (
                          LOWER(REPLACE(printing.card_language,'_','-')),'*'
                        )
                   )
                """,
                (*pending, start, end),
            )
            quote_variants = {int(row["variant_id"]) for row in cursor.fetchall()}
    finally:
        connection.close()

    eligible: list[int] = []
    deferred: list[dict[str, Any]] = []
    asset_dir = ROOT / "data" / "public" / "market-assets"
    for variant_id in pending:
        row = facts.get(variant_id)
        reasons: list[str] = []
        if row is None:
            reasons.append("missing_rebuild_member")
        else:
            if str(row.get("cohort") or "") != "product_ready":
                reasons.append(f"cohort:{row.get('cohort') or 'missing'}")
            if int(row.get("identity_pending") or 0) != 0:
                reasons.append("identity_pending")
            if str(row.get("discovery_status") or "") != "inactive_exact":
                reasons.append(
                    f"identity:{row.get('discovery_status') or 'missing'}"
                )
            if (
                int(row.get("latest_psa10_population") or 0)
                < int(discovery.R.DISCOVERY_GAP_POP)
            ):
                reasons.append(
                    f"pop_below_{int(discovery.R.DISCOVERY_GAP_POP)}"
                )
            if int(row.get("current_gemrate_receipt") or 0) != 1:
                reasons.append("missing_current_gemrate_receipt")
            if not str(row.get("canonical_printing_sha256") or ""):
                reasons.append("missing_canonical_printing")
            image_sha = str(row.get("canonical_image_content_sha256") or "")
            if len(image_sha) != 64:
                reasons.append("missing_canonical_image")
            elif any(
                not (asset_dir / filename).is_file()
                for filename in (
                    f"{image_sha}.webp",
                    f"{image_sha}_200.webp",
                    f"{image_sha}_600.webp",
                )
            ):
                reasons.append("missing_public_image_trio")
            if variant_id not in quote_variants:
                reasons.append("missing_current_eligible_quote")
        if reasons:
            deferred.append({
                "variantId": variant_id,
                "reasons": reasons,
                "blockerCode": None if row is None else row.get("blocker_code"),
            })
        else:
            eligible.append(variant_id)
    return eligible, deferred


def _append_active_universe(
    discovery: Any,
    *,
    run_id: str,
    business_date: str,
    generation: str,
    variant_ids: list[int],
) -> dict[str, Any]:
    """Atomically create current = previous members UNION eligible candidates."""

    if not variant_ids:
        return {"baseLockId": None, "lockId": None, "memberCount": None}
    connection = discovery.R.connect(discovery.R.DAILY_CREDENTIALS_ENV)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id,lock_sha256,member_count,policy_json"
                " FROM market_universe_lock WHERE is_current=1"
                " ORDER BY id DESC FOR UPDATE"
            )
            locks = [dict(row) for row in cursor.fetchall()]
            if len(locks) != 1:
                raise RuntimeError(
                    "append activation requires exactly one current universe lock:"
                    f" {[row.get('id') for row in locks]}"
                )
            base = locks[0]
            base_lock_id = int(base["id"])
            cursor.execute(
                "SELECT COUNT(*) AS members,COUNT(DISTINCT variant_id) AS variants"
                " FROM market_universe_member WHERE universe_lock_id=%s",
                (base_lock_id,),
            )
            counts = cursor.fetchone()
            base_members = int(counts["members"])
            if (
                base_members != int(counts["variants"])
                or base_members != int(base["member_count"])
            ):
                raise RuntimeError("current universe lock membership is inconsistent")
            cursor.execute(
                "SELECT variant_id FROM market_universe_member"
                " WHERE universe_lock_id=%s ORDER BY variant_id",
                (base_lock_id,),
            )
            base_ids = [int(row["variant_id"]) for row in cursor.fetchall()]
            new_ids = sorted(set(variant_ids) - set(base_ids))
            member_ids = sorted(set(base_ids) | set(new_ids))
            if not new_ids:
                return {
                    "baseLockId": base_lock_id,
                    "lockId": base_lock_id,
                    "memberCount": base_members,
                    "newVariantIds": [],
                }
            lock_doc = {
                "contract": "cardz-daily-chain-v2-append-only-universe-v1",
                "runId": run_id,
                "businessDate": business_date,
                "generation": generation,
                "baseLockSha256": str(base["lock_sha256"]),
                "memberVariantIds": member_ids,
                "activatedVariantIds": new_ids,
            }
            lock_sha = sha256(lock_doc)
            policy_json = base["policy_json"]
            if not isinstance(policy_json, str):
                policy_json = canonical_json(policy_json).decode("utf-8")
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            cursor.execute(
                "INSERT INTO market_universe_lock"
                " (lock_sha256,effective_at,policy_json,member_count,is_current)"
                " VALUES (%s,%s,%s,%s,0)"
                " ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id),"
                " effective_at=VALUES(effective_at),member_count=VALUES(member_count)",
                (lock_sha, now, policy_json, len(member_ids)),
            )
            lock_id = int(cursor.lastrowid)
            cursor.execute(
                "INSERT INTO market_universe_member"
                " (universe_lock_id,variant_id,segment_code,member_role,market_rank,"
                "  watch_position,watch_score,selection_signals_json)"
                " SELECT %s,variant_id,segment_code,member_role,market_rank,"
                "  watch_position,watch_score,selection_signals_json"
                " FROM market_universe_member WHERE universe_lock_id=%s"
                " ON DUPLICATE KEY UPDATE segment_code=VALUES(segment_code),"
                " member_role=VALUES(member_role),market_rank=VALUES(market_rank),"
                " watch_position=VALUES(watch_position),watch_score=VALUES(watch_score),"
                " selection_signals_json=VALUES(selection_signals_json)",
                (lock_id, base_lock_id),
            )
            signals = canonical_json({
                "origin": "daily_chain_v2_append_only",
                "runId": run_id,
                "businessDate": business_date,
                "generation": generation,
            }).decode("utf-8")
            for variant_id in new_ids:
                cursor.execute(
                    "INSERT INTO market_universe_member"
                    " (universe_lock_id,variant_id,segment_code,member_role,"
                    "  market_rank,selection_signals_json)"
                    " VALUES (%s,%s,'tracked','candidate',NULL,%s)"
                    " ON DUPLICATE KEY UPDATE segment_code=VALUES(segment_code),"
                    " member_role=VALUES(member_role),"
                    " selection_signals_json=VALUES(selection_signals_json)",
                    (lock_id, variant_id, signals),
                )
            cursor.execute(
                "SELECT COUNT(*) AS members,COUNT(DISTINCT variant_id) AS variants"
                " FROM market_universe_member WHERE universe_lock_id=%s",
                (lock_id,),
            )
            final_counts = cursor.fetchone()
            if (
                int(final_counts["members"]) != len(member_ids)
                or int(final_counts["variants"]) != len(member_ids)
            ):
                raise RuntimeError("append-only universe lock membership drifted")
            cursor.execute(
                "UPDATE market_universe_lock SET is_current=0"
                " WHERE is_current=1 AND id<>%s",
                (lock_id,),
            )
            cursor.execute(
                "UPDATE market_universe_lock SET is_current=1 WHERE id=%s",
                (lock_id,),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("append-only universe lock vanished before promotion")
        connection.commit()
        return {
            "baseLockId": base_lock_id,
            "lockId": lock_id,
            "lockSha256": lock_sha,
            "memberCount": len(member_ids),
            "newVariantIds": new_ids,
        }
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def stage_activate(args: argparse.Namespace) -> dict[str, Any]:
    import daily_discovery_activation as discovery
    import operator_control
    import rebuild_036

    if not str(args.run_id or "").strip() or not str(args.business_date or "").strip():
        raise RuntimeError("V2 candidate activation requires run and business date")
    snapshot, pending = _pending_identity_ids(discovery)
    state_paths = _candidate_state_paths(discovery)
    if not pending:
        if rebuild_036.FREEZE_WINDOW.is_file():
            with operator_control.operator_e2e_lease("v2-recover-empty-activation"):
                rebuild_036.cmd_unfreeze(SimpleNamespace(confirm=True))
        return {
            "stage": "activate",
            "activationStatus": "nothing-pending",
            "activated": [],
            "generation": snapshot["generation"],
        }
    with operator_control.operator_e2e_lease("v2-activate-pending"):
        # A hard-killed prior activation may have left the database frozen.
        # The V2 task itself remains enabled, so this claimed retry can restore
        # normal grants before re-running the same pending identity set.
        if rebuild_036.FREEZE_WINDOW.is_file():
            rebuild_036.cmd_unfreeze(SimpleNamespace(confirm=True))
        eligible, deferred = _activation_eligibility(
            discovery,
            generation=snapshot["generation"],
            pending=pending,
            business_date=args.business_date,
        )
        append = _append_active_universe(
            discovery,
            run_id=args.run_id,
            business_date=args.business_date,
            generation=snapshot["generation"],
            variant_ids=eligible,
        )
        after = discovery._runtime_snapshot(None)
        active = set(int(value) for value in after["universeIds"])
        missing = sorted(set(eligible) - active)
        if missing:
            raise RuntimeError(f"atomic activation returned without variants: {missing}")
        final_gaps = sorted(int(row["variant_id"]) for row in after["gapRows"])
        for state_path in state_paths:
            if state_path == discovery.STATE_PATH or state_path.is_file():
                discovery._write_state(
                    state_path,
                    after["generation"],
                    final_gaps,
                    [],
                    "v2-append-only-activation-evaluated",
                )
        status = "activated" if eligible else "deferred-ineligible"
    return {
        "stage": "activate",
        "activationStatus": status,
        "activated": eligible,
        "deferred": deferred,
        "append": append,
        "generation": after["generation"],
    }


def stage_accept(args: argparse.Namespace) -> dict[str, Any]:
    import operator_control
    import rebuild_036
    from daily_chain_v2_db import post_accept_contract, recover_daily_accept

    recovered = recover_daily_accept(args.run_id, args.business_date)
    if recovered is not None:
        if str(recovered.get("generationSha256") or "") != daily_generation_sha256(
            args.business_date, str(recovered.get("contentSha256") or ""),
        ):
            raise RuntimeError("recovered daily accept generation/content lineage mismatch")
        return {
            "stage": "accept",
            "recoveredFromCommittedLineage": True,
            **recovered,
        }

    receipt_dir = ROOT / "data" / "runtime" / "rebuild-036"
    before = {path.name for path in receipt_dir.glob("daily-accept-*.json")}
    with operator_control.operator_e2e_lease("v2-daily-accept"):
        code = rebuild_036.cmd_daily_accept(SimpleNamespace(credentials_env=None))
    if code != 0:
        raise RuntimeError(f"daily accept exited {code}")
    candidates = sorted(
        (path for path in receipt_dir.glob("daily-accept-*.json") if path.name not in before),
        key=lambda path: path.stat().st_mtime_ns,
    )
    if not candidates:
        raise RuntimeError("daily accept completed without a new receipt")
    receipt_path = candidates[-1]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    canonical = receipt.get("canonical")
    if not isinstance(canonical, Mapping):
        raise RuntimeError("daily accept receipt has no canonical result")
    generation_sha = str(canonical.get("rankingGenerationSha256") or "")
    content_sha = str(canonical.get("contentSha256") or "")
    if len(generation_sha) != 64 or len(content_sha) != 64:
        raise RuntimeError("daily accept V2 generation/content digest is missing")
    derived_generation = daily_generation_sha256(args.business_date, content_sha)
    if generation_sha != derived_generation:
        raise RuntimeError(
            "daily accept V2 generation/content lineage mismatch:"
            f" expected={derived_generation} actual={generation_sha}"
        )
    members = int(receipt.get("members") or 0)
    accepted = int(canonical.get("accepted") or 0)
    ranked = int(canonical.get("ranked") or 0)
    awaiting = int(canonical.get("awaitingFreshPrice") or 0)
    if members < 1 or accepted != members or ranked != members or awaiting != 0:
        raise RuntimeError(
            "daily accept V2 coverage mismatch:"
            f" members={members} accepted={accepted} ranked={ranked} awaiting={awaiting}"
        )
    post_contract = post_accept_contract(args.business_date, generation_sha, args.run_id)
    if not post_contract["selectionComplete"] or not post_contract["generationComplete"]:
        raise RuntimeError(f"daily accept V2 post-contract incomplete: {post_contract}")
    return {
        "stage": "accept",
        "generationSha256": generation_sha,
        "publicGenerationId": f"db3308_{generation_sha[:16]}",
        "contentSha256": content_sha,
        "activeCount": members,
        "acceptedAt": receipt.get("acceptedAt"),
        "receiptPath": str(receipt_path),
        "canonical": dict(canonical),
        "postContract": post_contract,
    }


def stage_box(args: argparse.Namespace) -> dict[str, Any]:
    import operator_control
    import sealed_daily

    with operator_control.operator_e2e_lease("v2-box"):
        # daddy 2026-09-24: cards pulled, then boxes.  Box prices are pulled
        # here, incr, PC (9333) then SNK, under this stage's lease:
        # `sealed_daily.py refresh` takes the lease itself and would be refused.
        # A red pull does not hold the card release; compose/export still run
        # and `boxPullRed` names it on the stage receipt.
        pulls = [sealed_daily.pull(sys.executable, "incr", adapter) for adapter in sealed_daily.PULL_ADAPTERS]
        compose = _run(
            [sys.executable, "-X", "utf8", str(ROOT / "pipelines" / "sealed_daily.py"), "compose"],
            timeout=1800,
        )
        output = ROOT / "data" / "public" / "box-subset.json"
        export = _run(
            [
                sys.executable, "-X", "utf8",
                str(ROOT / "pipelines" / "sealed_daily.py"), "export",
                "--output", str(output),
            ],
            timeout=1800,
        )
    document = json.loads(output.read_text(encoding="utf-8"))
    box_as_of = datetime.fromisoformat(str(document.get("asOf") or "").replace("Z", "+00:00"))
    accepted_at = datetime.fromisoformat(str(args.accepted_at).replace("Z", "+00:00"))
    if box_as_of.tzinfo is None:
        box_as_of = box_as_of.replace(tzinfo=timezone.utc)
    if accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    if box_as_of < accepted_at:
        raise RuntimeError(
            f"BOX projection predates current V2 acceptance: box={box_as_of} accept={accepted_at}"
        )
    return {
        "stage": "box",
        "runId": args.run_id,
        "acceptedAt": args.accepted_at,
        "boxAsOf": document.get("asOf"),
        "output": str(output),
        "payloadSha256": sha256(document),
        "boxPull": pulls,
        "boxPullRed": [f"{step['adapter']}: {step['red']}" for step in pulls if "red" in step],
        "compose": compose,
        "export": export,
    }


def stage_live_confirm(args: argparse.Namespace) -> dict[str, Any]:
    from daily_chain_v2_db import (
        build_live_event,
        fetch_live_health,
        insert_live_event,
        read_snapshot,
        recover_live_event,
    )

    snapshot = read_snapshot(args.snapshot.resolve())
    derived_generation = daily_generation_sha256(
        args.business_date, args.expected_content_sha256,
    )
    if args.expected_generation != f"db3308_{derived_generation[:16]}":
        raise RuntimeError("live-confirm generation/content lineage mismatch")
    generation = snapshot.get("generation") or {}
    if str(generation.get("id") or "") != args.expected_generation:
        raise RuntimeError(
            f"release snapshot generation mismatch: expected={args.expected_generation}"
            f" actual={generation.get('id')}"
        )
    source_health = json.loads(args.source_health.resolve().read_text(encoding="utf-8"))
    if not isinstance(source_health, Mapping):
        raise RuntimeError("source health receipt is not an object")
    health = fetch_live_health()
    event = build_live_event(
        business_date=args.business_date,
        run_id=args.run_id,
        snapshot=snapshot,
        health=health,
        active_count=args.active_count,
        content_sha256=args.expected_content_sha256,
        source_health=source_health,
        degraded_sources=args.degraded_source,
    )
    recovered = recover_live_event(
        business_date=args.business_date,
        run_id=args.run_id,
        expected_generation=args.expected_generation,
        expected_content_sha256=args.expected_content_sha256,
        expected_active_count=args.active_count,
    )
    if recovered is not None:
        return {
            "stage": "live-confirm",
            "recoveredFromCommittedOutbox": True,
            **recovered,
            "health": {
                "status": health.get("status"),
                "generation": health.get("generation"),
                "generatedAt": health.get("generatedAt"),
            },
        }
    confirm_before = datetime.fromisoformat(str(args.confirm_before).replace("Z", "+00:00"))
    if confirm_before.tzinfo is None:
        confirm_before = confirm_before.replace(tzinfo=timezone.utc)
    occurred_at = datetime.fromisoformat(str(event["occurredAt"]).replace("Z", "+00:00"))
    if occurred_at.astimezone(timezone.utc) >= confirm_before.astimezone(timezone.utc):
        raise RuntimeError("publication deadline passed before live.confirmed outbox insert")
    event_id, inserted = insert_live_event(event)
    return {
        "stage": "live-confirm",
        "eventId": event_id,
        "inserted": inserted,
        "event": event,
        "health": {
            "status": health.get("status"),
            "generation": health.get("generation"),
            "generatedAt": health.get("generatedAt"),
        },
    }


def stage_identity_census(args: argparse.Namespace) -> dict[str, Any]:
    """Refresh the business date's GemRate census before any source work."""

    from identity_census_stage import run_identity_census

    result = run_identity_census(
        business_date=args.business_date,
        budget_seconds=stage_deadline_budget_seconds(),
    )
    if result.get("refreshed") is not True:
        if result.get("skipReason") == "tick-budget-too-short":
            # No harvest started. Refund the claim and yield to a fresh tick;
            # the dated refreshed=true receipt still owns the source gate.
            raise RuntimeError("errorCode=CENSUS_TICK_BUDGET_DEFERRED: tick-budget-too-short")
        reason = str(
            result.get("error")
            or result.get("skipReason")
            or "all-set harvest did not produce a complete census"
        )
        if result.get("outputTail"):
            # The per-attempt log survives later overwrites of the dated receipt.
            print(json.dumps({"stage": "identity-census", "error": reason,
                              "outputTail": result["outputTail"]}, ensure_ascii=False), file=sys.stderr)
        raise RuntimeError(f"INCOMPLETE_CENSUS: {reason}")
    return result


def stage_identity_completeness(args: argparse.Namespace) -> dict[str, Any]:
    """Recompute the qualified GemRate universe and its read-only DB gap queue."""

    from gemrate_completeness_daily import run_daily_completeness

    result = run_daily_completeness(
        business_date=args.business_date,
        budget_seconds=stage_deadline_budget_seconds(),
    )
    inventory_status = str(result.pop("status", "") or "UNKNOWN")
    if result.get("latestAdvanced") is not True:
        # A short tick is a defer/retry decision, not a successful stage.  The
        # previous implementation returned its domain status in the wrapper's
        # reserved `status` field, so even a fully written inventory exited 0
        # and was then misread as SOURCE_FAILED three times.
        raise RuntimeError(f"IDENTITY_COMPLETENESS_DEFERRED: {inventory_status}")
    result["inventoryStatus"] = inventory_status
    return result


def discovery_lane_names() -> tuple[str, ...]:
    """Lane names the registered identity sources declare, not a literal pair."""

    try:
        from daily_chain_v2_adapters import build_default_registry

        specs = [adapter.spec for adapter in build_default_registry().enabled()]
    except Exception:  # noqa: BLE001 - a broken registry must not hide the CLI
        specs = []
    return tuple(lane for lane, _group in (identity_lanes(specs) or IDENTITY_LANE_FALLBACK))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    sub = parser.add_subparsers(dest="stage", required=True)
    sub.add_parser("migrate").set_defaults(func=stage_migrate)
    sub.add_parser("registry").set_defaults(func=stage_registry)
    contract = sub.add_parser("contract")
    contract.add_argument("--run-id", required=True)
    contract.add_argument("--business-date", required=True)
    contract.add_argument("--label", required=True)
    contract.set_defaults(func=stage_contract)
    discover = sub.add_parser("discover")
    discover.add_argument("--lane", choices=discovery_lane_names(), required=True)
    discover.set_defaults(func=stage_discover)
    sub.add_parser("pending").set_defaults(func=stage_pending)
    operator_apply = sub.add_parser("operator-apply")
    operator_apply.add_argument("--inbox", type=Path, default=None)
    operator_apply.set_defaults(func=stage_operator_apply)
    intake = sub.add_parser("intake")
    intake.add_argument(
        "--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE")
    )
    intake.add_argument("--max-age-days", type=int, default=7)
    intake.add_argument("--max-seed", type=int, default=25)
    intake.add_argument(
        "--census",
        type=Path,
        default=None,
        help="explicit merged GemRate qualified census produced by identity-completeness",
    )
    intake.set_defaults(func=stage_identity_intake)
    reverify = sub.add_parser("reverify")
    reverify.add_argument("--lane", choices=discovery_lane_names(), required=True)
    reverify.add_argument(
        "--variant-id", type=int, action="append", default=[],
        help="scope the lane; omitted means every held binding it owns",
    )
    reverify.set_defaults(func=stage_identity_reverify)
    brief = sub.add_parser("brief")
    brief.add_argument(
        "--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE")
    )
    brief.set_defaults(func=stage_identity_brief)
    noop = sub.add_parser("noop")
    noop.add_argument("--detail-json", required=True)
    noop.set_defaults(func=stage_noop)
    sub.add_parser("consolidate").set_defaults(func=stage_consolidate)
    sub.add_parser("checkpoint-repair").set_defaults(func=stage_checkpoint_repair)
    activate = sub.add_parser("activate")
    # Defaults keep an already-journalled pre-upgrade task resumable; new task
    # payloads also carry these values explicitly.
    activate.add_argument("--run-id", default=os.environ.get("CARDZ_V2_RUN_ID"))
    activate.add_argument(
        "--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE")
    )
    activate.set_defaults(func=stage_activate)
    accept = sub.add_parser("accept")
    accept.add_argument("--run-id", required=True)
    accept.add_argument("--business-date", required=True)
    accept.set_defaults(func=stage_accept)
    box = sub.add_parser("box")
    box.add_argument("--run-id", required=True)
    box.add_argument("--accepted-at", required=True)
    box.set_defaults(func=stage_box)
    live = sub.add_parser("live-confirm")
    live.add_argument("--run-id", required=True)
    live.add_argument("--business-date", required=True)
    live.add_argument("--snapshot", type=Path, required=True)
    live.add_argument("--expected-generation", required=True)
    live.add_argument("--expected-content-sha256", required=True)
    live.add_argument("--active-count", type=int, required=True)
    live.add_argument("--source-health", type=Path, required=True)
    live.add_argument("--confirm-before", required=True)
    live.add_argument("--degraded-source", action="append", default=[])
    live.set_defaults(func=stage_live_confirm)
    census = sub.add_parser("identity-census")
    census.add_argument(
        "--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE")
    )
    census.set_defaults(func=stage_identity_census)
    completeness = sub.add_parser("identity-completeness")
    completeness.add_argument(
        "--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE")
    )
    completeness.set_defaults(func=stage_identity_completeness)
    args = parser.parse_args()
    os.environ["CARDZ_DAILY_CHAIN_V2"] = "1"
    if hasattr(args, "run_id"):
        os.environ["CARDZ_V2_RUN_ID"] = str(args.run_id)
    if hasattr(args, "business_date"):
        os.environ["CARDZ_V2_BUSINESS_DATE"] = str(args.business_date)
    def interrupted(signum: int, _frame: Any) -> None:
        raise InterruptedError(f"stage interrupted by signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        result = args.func(args)
        receipt = {
            "contract": "cardz-daily-chain-stage-v2",
            "status": "completed",
            "checkedAt": iso_now(),
            **result,
        }
        atomic_json(args.output.resolve(), receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except (Exception, SystemExit) as error:  # durable error receipt first
        receipt = {
            "contract": "cardz-daily-chain-stage-v2",
            "stage": args.stage,
            "status": "terminal",
            "checkedAt": iso_now(),
            # Keep the retry identity across the process/receipt boundary.
            # A generic RuntimeError prefix hides the inner no-work marker.
            "errorCode": classify_error(str(error)).error_code,
            "error": str(error),
            "traceback": traceback.format_exc()[-8000:],
        }
        atomic_json(args.output.resolve(), receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
