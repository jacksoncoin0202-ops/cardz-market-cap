#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Claim-guarded worker entrypoint for CARDZ Daily Chain V2.

This is the only path that may call per-adapter collection without the legacy
global operator lease.  A live SQLite claim issued by the V2 orchestrator is
mandatory; direct invocation cannot mutate collection state.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from daily_chain_v2_contract import canonical_json, sha256  # noqa: E402
from daily_chain_v2_journal import Journal  # noqa: E402


JST = ZoneInfo("Asia/Tokyo")
DEFAULT_HEARTBEAT_SECONDS = 30.0
WORKER_LEASE_SECONDS = 90


def heartbeat_interval_seconds() -> float:
    raw = os.environ.get("CARDZ_V2_HEARTBEAT_SECONDS", "").strip()
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_HEARTBEAT_SECONDS
    return value if value > 0 else DEFAULT_HEARTBEAT_SECONDS


def start_heartbeat(
    journal: Journal,
    task_key: str,
    claim_token: str,
    *,
    interval_seconds: float | None = None,
    lease_seconds: int = WORKER_LEASE_SECONDS,
) -> Callable[[], None]:
    """Renew this attempt's lease from inside the worker.

    Before this, only the orchestrator polled the child; a stage that outlived
    one tick therefore looked lease-expired and the next tick killed it.  The
    worker now proves its own liveness and stops the renewal on exit.
    """

    interval = float(
        heartbeat_interval_seconds() if interval_seconds is None else interval_seconds
    )
    stopping = threading.Event()

    def loop() -> None:
        while not stopping.wait(interval):
            try:
                journal.heartbeat(
                    task_key,
                    claim_token,
                    lease_seconds=lease_seconds,
                    worker_pid=os.getpid(),
                )
            except Exception:  # a lost claim ends the renewal, never the work
                return

    thread = threading.Thread(
        target=loop, name=f"v2-heartbeat-{task_key[:24]}", daemon=True
    )
    thread.start()

    def stop() -> None:
        stopping.set()
        thread.join(timeout=max(1.0, interval))

    return stop


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(canonical_json(value) + b"\n")
    os.replace(temporary, path)


def parse_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(str(row["payload_json"]))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid journal task payload: {error}") from error
    if not isinstance(payload, dict):
        raise RuntimeError("journal task payload must be an object")
    return payload


def shard_variant_ids(registry: list[dict[str, Any]], adapter: str, shard: str) -> list[int] | None:
    if shard == "all":
        return None
    try:
        index_text, total_text = shard.split("-of-", 1)
        index = int(index_text)
        total = int(total_text)
    except (ValueError, AttributeError) as error:
        raise RuntimeError(f"invalid shard descriptor: {shard}") from error
    if total < 1 or index < 0 or index >= total:
        raise RuntimeError(f"invalid shard descriptor: {shard}")
    return sorted({
        int(row["variantId"])
        for row in registry
        if str(row.get("adapter") or "") == adapter
        and int(row.get("variantId") or 0) > 0
        and int(row["variantId"]) % total == index
    })


def run_collect(
    task: Mapping[str, Any],
    payload: Mapping[str, Any],
    receipt_path: Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    import collect_control as collect

    worker = payload.get("worker")
    if not isinstance(worker, Mapping):
        raise RuntimeError("collect worker payload is missing")
    adapters = [str(value) for value in worker.get("adapters") or []]
    if not adapters:
        raise RuntimeError("collect worker has no adapters")
    source_code = str(task["source_code"])
    shard = str(payload.get("shard") or "all")
    variant_ids: list[int] | None = None
    configured_ids = worker.get("variantIds")
    if configured_ids is not None:
        if shard != "all":
            raise RuntimeError("worker cannot combine explicit variant IDs with a shard")
        variant_ids = sorted({int(value) for value in configured_ids})
        if any(value <= 0 for value in variant_ids):
            raise RuntimeError("worker variant IDs must be positive")
    elif shard != "all":
        if len(adapters) != 1:
            raise RuntimeError("sharded source task must own exactly one legacy adapter")
        registry = collect._jsonl_rows(collect.REGISTRY_PATH)
        variant_ids = shard_variant_ids(registry, adapters[0], shard)
        if not variant_ids:
            now = iso_now()
            return {
                "contract": "cardz-source-result-v2",
                "sourceCode": source_code,
                "status": "completed",
                "observedAt": now,
                "checkedAt": now,
                "payloadSha256": sha256({"source": source_code, "shard": shard, "empty": True}),
                "counts": {"processed": 0, "inserted": 0, "checkpointed": 0},
                "detail": {"shard": shard, "empty": True},
            }
    report_path = receipt_path.with_suffix(".collect.json")
    mode = str(worker.get("mode") or "incr")
    if mode not in {"incr", "stock"}:
        raise RuntimeError(f"unsupported collect worker mode: {mode}")
    # Declared by the task payload (or a hand override), not pinned in code:
    # a bounded smoke run of one adapter no longer needs a source edit.
    effective_limit = worker.get("limit") if limit is None else limit
    if effective_limit is not None:
        effective_limit = int(effective_limit)
        if effective_limit < 1:
            raise RuntimeError("collect worker limit must be positive")
    effective_dry_run = bool(dry_run) or bool(worker.get("dry_run"))
    collect_command = collect.cmd_stock if mode == "stock" else collect.cmd_incr
    report = collect_command(
        adapters=adapters,
        limit=effective_limit,
        dry_run=effective_dry_run,
        delay=float(worker.get("delay") or 0.0),
        workers=int(worker.get("workers") or 24),
        ensure_browser=bool(worker.get("ensureBrowser")),
        pc_resume_report=None,
        pc_sleep=(
            None if worker.get("pcSleep") is None else float(worker["pcSleep"])
        ),
        pc_workers=(
            None if worker.get("pcWorkers") is None else int(worker["pcWorkers"])
        ),
        variant_ids=variant_ids,
        force_network=False,
        rebuild_registry=False,
        report_path=report_path,
        lease_scope=(
            str(worker.get("leaseScope") or "").strip()
            or (shard if shard != "all" else None)
        ),
    )
    if not bool(report.get("ok")):
        failed_detail = []
        for result in report.get("results") or []:
            if bool(result.get("ok")):
                continue
            commands = {}
            for name in ("run", "harvest", "ingest", "derive", "materialize"):
                command = result.get(name)
                if isinstance(command, Mapping):
                    commands[name] = {
                        key: command.get(key)
                        for key in ("exit", "error", "stderrTail", "stdoutTail")
                        if command.get(key) not in (None, "")
                    }
            failed_detail.append(
                {
                    "adapter": result.get("adapter"),
                    "error": result.get("error"),
                    "commands": commands,
                }
            )
        raise RuntimeError(
            f"collect source failed adapters={adapters}"
            f" failed={report.get('failedAdapters')} truncated={report.get('truncatedAdapters')}"
            f" detail={json.dumps(failed_detail, ensure_ascii=False, sort_keys=True)[-6000:]}"
        )
    quarantined = int(report.get("quarantined") or 0)
    status = "degraded" if quarantined else "completed"
    return {
        "contract": "cardz-source-result-v2",
        "sourceCode": source_code,
        "status": status,
        "observedAt": str(report.get("asOf") or iso_now()),
        "checkedAt": iso_now(),
        "payloadSha256": sha256(report),
        "evidenceRef": str(report_path),
        "counts": {
            "processed": int(report.get("processed") or 0),
            "inserted": int(report.get("inserted") or 0),
            "checkpointed": int(report.get("checkpointed") or 0),
            "failed": int(report.get("failed") or 0),
            "quarantined": quarantined,
        },
        "detail": {
            "adapters": adapters,
            "shard": shard,
            "mode": mode,
            "limit": effective_limit,
            "dryRun": effective_dry_run,
            "failedAdapters": report.get("failedAdapters") or [],
            "truncatedAdapters": report.get("truncatedAdapters") or [],
        },
    }


def _run_checked(command: list[str]) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    tail = "\n".join(
        ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip().splitlines()[-20:]
    )
    if proc.returncode != 0:
        raise RuntimeError(f"command exit={proc.returncode}: {tail}")
    parsed: dict[str, Any] | None = None
    for line in reversed((proc.stdout or "").splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            parsed = value
            break
    return parsed or {"exit": 0, "tail": tail[-1000:]}


def run_fx(task: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    worker = payload.get("worker")
    if not isinstance(worker, Mapping):
        raise RuntimeError("FX worker payload is missing")
    timeout_seconds = float(worker.get("timeoutSeconds") or 40)
    collect_result = _run_checked(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "pipelines" / "fx_rates.py"),
            "--timeout-seconds",
            str(timeout_seconds),
            "--retries",
            "3",
        ]
    )
    from fx_rates import DEFAULT_CACHE, validate_snapshot

    snapshot = validate_snapshot(json.loads(DEFAULT_CACHE.read_text(encoding="utf-8")))
    fetched = datetime.fromisoformat(str(snapshot["fetchedAt"]).replace("Z", "+00:00"))
    business_day = date.fromisoformat(str(payload["businessDate"]))
    business_start = datetime.combine(business_day, time.min, tzinfo=JST).astimezone(timezone.utc)
    if fetched < business_start:
        raise RuntimeError(
            f"FX schema contract: stale last-good fetchedAt={fetched.isoformat()}"
            f" businessStart={business_start.isoformat()}"
        )
    load_result = _run_checked(
        [sys.executable, "-X", "utf8", str(ROOT / "pipelines" / "fx_db_load.py")]
    )
    return {
        "contract": "cardz-source-result-v2",
        "sourceCode": str(task["source_code"]),
        "status": "completed",
        "observedAt": str(snapshot["fetchedAt"]),
        "checkedAt": iso_now(),
        "payloadSha256": str(snapshot["payloadSha256"]),
        "counts": {
            "currencies": len(snapshot.get("quotes") or {}),
            "written": int(load_result.get("written") or 0),
        },
        "detail": {"collect": collect_result, "load": load_result},
    }


def _run_fx_kind(
    task: Mapping[str, Any],
    payload: Mapping[str, Any],
    receipt_path: Path,
    *,
    limit: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    del receipt_path, limit, dry_run
    return run_fx(task, payload)


# Extension point: a new worker kind registers a runner here and an adapter
# declares it, instead of a literal list being edited in two places.
WORKER_RUNNERS: dict[str, Callable[..., dict[str, Any]]] = {
    "collect": run_collect,
    "fx": _run_fx_kind,
}


def registered_kinds() -> tuple[str, ...]:
    """Worker kinds the registered adapters ask for, union what we can run."""

    declared: tuple[str, ...] = ()
    try:
        from daily_chain_v2_adapters import registered_worker_kinds

        declared = registered_worker_kinds()
    except Exception:  # noqa: BLE001 - a broken registry must not hide the CLI
        declared = ()
    return tuple(sorted(set(declared) | set(WORKER_RUNNERS)))


def resolve_worker_runner(kind: str) -> Callable[..., dict[str, Any]]:
    runner = WORKER_RUNNERS.get(str(kind or "").strip())
    if runner is None:
        raise RuntimeError(
            f"unsupported worker kind: {kind!r};"
            f" adapters declare {list(registered_kinds())};"
            f" this worker can run {sorted(WORKER_RUNNERS)}"
        )
    return runner


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--task-key", required=True)
    parser.add_argument("--claim-token", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--kind", choices=registered_kinds(), required=True)
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="hand override for the collect limit (task payload key: limit)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="hand override: collect without writing (task payload key: dry_run)",
    )
    args = parser.parse_args()

    journal = Journal(args.state_db.resolve())
    task = journal.validate_claim(args.task_key, args.claim_token)
    payload = parse_payload(task)
    if str(payload.get("workerKind") or "") != args.kind:
        raise RuntimeError("worker kind does not match journal task")
    expected_run = os.environ.get("CARDZ_V2_RUN_ID", "")
    if expected_run and expected_run != str(task["run_id"]):
        raise RuntimeError("worker run environment does not match journal claim")

    runner = resolve_worker_runner(args.kind)
    stop_heartbeat = start_heartbeat(journal, args.task_key, args.claim_token)
    try:
        receipt = runner(
            task,
            payload,
            args.receipt.resolve(),
            limit=args.limit,
            dry_run=bool(args.dry_run),
        )
        atomic_json(args.receipt.resolve(), receipt)
        print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - receipt first, nonzero second
        failed = {
            "contract": "cardz-source-result-v2",
            "sourceCode": str(task["source_code"]),
            # Retry/terminal policy belongs to the orchestrator classifier;
            # a worker receipt describes the attempt and must not pre-judge it.
            "status": "failed",
            "observedAt": iso_now(),
            "checkedAt": iso_now(),
            "errorCode": type(error).__name__,
            "error": str(error),
        }
        atomic_json(args.receipt.resolve(), failed)
        print(json.dumps(failed, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 1
    finally:
        stop_heartbeat()


if __name__ == "__main__":
    raise SystemExit(main())
