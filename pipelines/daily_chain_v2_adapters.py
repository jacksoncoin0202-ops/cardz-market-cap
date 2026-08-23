#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Registered source adapters for CARDZ Daily Chain V2."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from daily_chain_v2_contract import (
    AdapterRegistry,
    Heartbeat,
    SourceResult,
    SourceSpec,
    SourceTask,
    canonical_json,
    sha256,
)


ROOT = Path(__file__).resolve().parents[1]


class WorkerInterrupted(RuntimeError):
    pass


# audit item 10: seconds between SIGTERM and SIGKILL for a source worker group.
# Long enough for collect_control to ingest the cards its child already fetched
# and declared; matches the stage subprocess path in daily_chain_v2.py.
WORKER_SHUTDOWN_GRACE_SECONDS = 60.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _process_started_at(pid: int) -> str | None:
    path = Path(f"/proc/{pid}/stat")
    try:
        # Field 22 is the kernel start time.  Record the raw value: recovery
        # uses it only to reject a recycled PID, not as a wall-clock timestamp.
        return path.read_text(encoding="ascii").split()[21]
    except (OSError, IndexError):
        return None


def run_started_at(state_db: Path, run_id: str) -> str:
    """audit P2-12: the run's creation time, for windows anchored to the run.

    ``CARDZ_V2_RUN_STARTED_AT`` is injected into stage subprocesses by the
    orchestrator but never into its own environment, so a source worker (and
    therefore collect_control) never saw it and fell back to JST midnight.
    Read it straight off the journal instead of relying on inheritance.
    """
    import sqlite3

    try:
        conn = sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5.0)
    except sqlite3.Error:
        return ""
    try:
        row = conn.execute(
            "SELECT created_at FROM chain_run WHERE run_id=?", (run_id,)
        ).fetchone()
    except sqlite3.Error:
        return ""
    finally:
        conn.close()
    return "" if row is None else str(row[0] or "")


def terminate_worker_group(pid: int, *, grace_seconds: float = 5.0) -> None:
    if pid <= 1:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                timeout=max(1, int(grace_seconds)),
                check=False,
            )
            return
        os.killpg(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.1)
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        return


@dataclass
class CommandSourceAdapter:
    spec: SourceSpec
    worker_kind: str
    worker_payload: Mapping[str, Any]
    shards: tuple[str, ...] = ("all",)
    capability_adapters: Mapping[str, tuple[str, ...]] | None = None

    def plan(self, context: Mapping[str, Any]) -> Sequence[SourceTask]:
        if str(context.get("phase") or "daily") == "candidate-source":
            if self.worker_kind != "collect":
                return []
            from collect_control import REGISTRY_PATH, _jsonl_rows

            pending = {
                int(value) for value in context.get("variant_ids") or [] if int(value) > 0
            }
            registry_rows = _jsonl_rows(REGISTRY_PATH)
            tasks: list[SourceTask] = []
            for legacy_adapter in [
                str(value) for value in self.worker_payload.get("adapters") or []
            ]:
                variant_ids = sorted({
                    int(row["variantId"])
                    for row in registry_rows
                    if str(row.get("adapter") or "") == legacy_adapter
                    and int(row.get("variantId") or 0) in pending
                })
                if not variant_ids:
                    continue
                revision = sha256({
                    "contract": "candidate-source-v2",
                    "adapterVersion": self.spec.adapter_version,
                    "legacyAdapter": legacy_adapter,
                    "variantIds": variant_ids,
                })[:16]
                tasks.append(SourceTask(
                    run_id=str(context["run_id"]),
                    business_date=str(context["business_date"]),
                    source_code=self.spec.source_code,
                    capability=f"candidate-stock:{legacy_adapter}",
                    external_id=legacy_adapter,
                    input_revision=revision,
                    checkpoint={
                        "worker": {
                            "adapters": [legacy_adapter],
                            "mode": "stock",
                            "variantIds": variant_ids,
                        }
                    },
                ))
            return tasks
        return [
            SourceTask(
                run_id=str(context["run_id"]),
                business_date=str(context["business_date"]),
                source_code=self.spec.source_code,
                capability="+".join(self.spec.capabilities),
                shard=shard,
                input_revision=f"{self.spec.adapter_version}:{context['input_revision']}",
            )
            for shard in self.shards
        ]

    def execute(
        self,
        task: SourceTask,
        context: Mapping[str, Any],
        heartbeat: Heartbeat,
    ) -> Mapping[str, Any]:
        state_db = Path(str(context["state_db"]))
        task_key = str(context["task_key"])
        claim_token = str(context["claim_token"])
        log_path = Path(str(context["log_path"]))
        receipt_path = Path(str(context["receipt_path"]))
        deadline_monotonic = float(context["deadline_monotonic"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable,
            "-X",
            "utf8",
            "-u",
            str(ROOT / "pipelines" / "daily_chain_v2_worker.py"),
            "--state-db",
            str(state_db),
            "--task-key",
            task_key,
            "--claim-token",
            claim_token,
            "--receipt",
            str(receipt_path),
            "--kind",
            self.worker_kind,
        ]
        command_sha = sha256(command)
        env = os.environ.copy()
        env.update(
            {
                "CARDZ_DAILY_CHAIN_V2": "1",
                "CARDZ_V2_RUN_ID": task.run_id,
                "CARDZ_V2_BUSINESS_DATE": task.business_date,
                # audit P2-12: source workers need the same run anchor the
                # stage subprocesses already get, or every window they compute
                # (gemrate manifest reuse included) snaps back to JST midnight.
                "CARDZ_V2_RUN_STARTED_AT": (
                    os.environ.get("CARDZ_V2_RUN_STARTED_AT", "").strip()
                    or run_started_at(state_db, task.run_id)
                ),
                "CARDZ_V2_TASK_KEY": task_key,
                "CARDZ_V2_STATE_DB": str(state_db),
            }
        )
        with log_path.open("ab", buffering=0) as log:
            log.write(
                canonical_json(
                    {
                        "event": "worker-start",
                        "taskKey": task_key,
                        "sourceCode": task.source_code,
                        "shard": task.shard,
                        "at": _iso_now(),
                    }
                )
                + b"\n"
            )
            proc = subprocess.Popen(
                command,
                cwd=str(ROOT),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            started_marker = _process_started_at(proc.pid)
            heartbeat(
                worker_pid=proc.pid,
                checkpoint={"state": "worker_started", "pid": proc.pid},
            )
            last_heartbeat = 0.0
            while proc.poll() is None:
                now = time.monotonic()
                if now >= deadline_monotonic:
                    # audit item 10: the default 5s grace killed collect_control
                    # while its child still held hundreds of fetched cards, so
                    # nothing was ingested and the retry restarted from zero.
                    # The stage path already grants 60s for the same reason.
                    terminate_worker_group(
                        proc.pid, grace_seconds=WORKER_SHUTDOWN_GRACE_SECONDS
                    )
                    raise WorkerInterrupted(
                        f"tick deadline interrupted source worker pid={proc.pid} task={task_key}"
                    )
                if now - last_heartbeat >= 10.0:
                    heartbeat(
                        worker_pid=proc.pid,
                        checkpoint={
                            "state": "worker_running",
                            "pid": proc.pid,
                            "processStartedAt": started_marker,
                            "commandSha256": command_sha,
                        },
                    )
                    last_heartbeat = now
                time.sleep(1.0)
            exit_code = int(proc.returncode or 0)
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            receipt = {
                "status": "terminal",
                "errorCode": "WORKER_RECEIPT_MISSING",
                "error": f"{type(error).__name__}:{error}",
            }
        return {
            "exitCode": exit_code,
            "receipt": receipt,
            "receiptPath": str(receipt_path),
            "logPath": str(log_path),
            "workerPid": proc.pid,
            "processStartedAt": started_marker,
            "commandSha256": command_sha,
        }

    def ingest(
        self,
        task: SourceTask,
        execution: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> SourceResult:
        del context
        receipt = execution.get("receipt")
        if not isinstance(receipt, Mapping):
            raise RuntimeError("worker receipt is not an object")
        if int(execution.get("exitCode") or 0) != 0:
            raise RuntimeError(
                str(receipt.get("error") or receipt.get("errorCode") or "source worker failed")
            )
        if str(receipt.get("sourceCode") or "") != task.source_code:
            raise RuntimeError(
                f"worker receipt source mismatch: expected={task.source_code}"
                f" got={receipt.get('sourceCode')}"
            )
        payload_digest = str(receipt.get("payloadSha256") or "") or sha256(receipt)
        status = str(receipt.get("status") or "completed").lower()
        if status not in {"completed", "degraded", "quarantined"}:
            raise RuntimeError(str(receipt.get("error") or f"worker status={status}"))
        counts = receipt.get("counts") if isinstance(receipt.get("counts"), Mapping) else {}
        return SourceResult(
            status=status,
            observed_at=str(receipt.get("observedAt") or _iso_now()),
            checked_at=str(receipt.get("checkedAt") or _iso_now()),
            payload_sha256=payload_digest,
            evidence_ref=str(execution.get("receiptPath") or ""),
            error_code=str(receipt.get("errorCode") or "") or None,
            counts={str(key): int(value) for key, value in counts.items()},
            detail={
                "logPath": execution.get("logPath"),
                "shard": task.shard,
                "workerKind": self.worker_kind,
            },
        )


def build_default_registry() -> AdapterRegistry:
    """Initial V2 providers.  Adding another source only registers an adapter."""

    return AdapterRegistry(
        (
            CommandSourceAdapter(
                SourceSpec(
                    source_code="gemrate",
                    capabilities=("pop", "identity"),
                    transport="http+headed-browser",
                    concurrency_group="host:gemrate",
                    max_concurrency=4,
                    cadence="daily",
                    freshness_sla_minutes=405,
                    required_class="core",
                    adapter_version="2",
                ),
                worker_kind="collect",
                # audit P1-5: --workers was hardcoded to 1 in collect_control,
                # which made gemrate the source-phase critical path at 2.7x the
                # second-slowest source. 2 is the measured starting point; the
                # collector fails closed above GEMRATE_MAX_WORKERS because this
                # spec already runs max_concurrency=4 shards (2 x 4 = 8 Chromes).
                worker_payload={
                    "adapters": ["gemrate_pop"],
                    "ensureBrowser": False,
                    "gemrateWorkers": 2,
                },
                shards=("0-of-4", "1-of-4", "2-of-4", "3-of-4"),
                capability_adapters={"pop": ("gemrate_pop",)},
            ),
            CommandSourceAdapter(
                SourceSpec(
                    source_code="snkrdunk",
                    capabilities=("quote", "price", "trades", "image", "identity"),
                    transport="http",
                    concurrency_group="host:snkrdunk",
                    max_concurrency=1,
                    cadence="daily",
                    freshness_sla_minutes=405,
                    required_class="quote",
                    adapter_version="2",
                    identity_lane="http",
                    route_priority=10,
                ),
                worker_kind="collect",
                worker_payload={
                    "adapters": ["snk_trades", "snk_price", "snk_en_image"],
                    "ensureBrowser": False,
                    "workers": 24,
                },
                capability_adapters={"quote": ("snk_price",)},
            ),
            CommandSourceAdapter(
                SourceSpec(
                    source_code="pricecharting",
                    capabilities=("quote", "price", "sales", "identity"),
                    transport="cdp:9333",
                    concurrency_group="cdp:9333",
                    max_concurrency=1,
                    cadence="daily",
                    freshness_sla_minutes=405,
                    required_class="quote",
                    adapter_version="2",
                    identity_lane="browser",
                    route_priority=20,
                ),
                worker_kind="collect",
                worker_payload={
                    "adapters": ["pc_ebay_sales", "en_price_ref"],
                    "ensureBrowser": True,
                    "pcWorkers": 2,
                    "pcSleep": 3.0,
                },
                capability_adapters={"quote": ("en_price_ref",)},
            ),
            CommandSourceAdapter(
                SourceSpec(
                    source_code="fx",
                    capabilities=("rates",),
                    transport="http",
                    concurrency_group="host:fx",
                    max_concurrency=1,
                    cadence="daily",
                    freshness_sla_minutes=405,
                    required_class="core",
                    adapter_version="2",
                ),
                worker_kind="fx",
                worker_payload={"timeoutSeconds": 40},
            ),
        )
    )


def registered_worker_kinds(registry: AdapterRegistry | None = None) -> tuple[str, ...]:
    """Worker kinds the registered adapters actually ask for.

    The worker CLI derives its ``--kind`` choices from this, so registering an
    adapter with a new worker kind never needs a literal list edited.
    """

    try:
        resolved = build_default_registry() if registry is None else registry
        kinds = {
            str(getattr(adapter, "worker_kind", "") or "").strip()
            for adapter in resolved.enabled()
        }
    except Exception:  # noqa: BLE001 - a broken registry must not hide the CLI
        kinds = set()
    kinds.discard("")
    return tuple(sorted(kinds)) or ("collect", "fx")


def task_payload(adapter: CommandSourceAdapter, task: SourceTask) -> dict[str, Any]:
    worker = dict(adapter.worker_payload)
    override = task.checkpoint.get("worker") if isinstance(task.checkpoint, Mapping) else None
    if isinstance(override, Mapping):
        worker.update(dict(override))
    return {
        "kind": "source",
        "workerKind": adapter.worker_kind,
        "worker": worker,
        "shard": task.shard,
        "spec": {
            "sourceCode": adapter.spec.source_code,
            "capabilities": list(adapter.spec.capabilities),
            "transport": adapter.spec.transport,
            "requiredClass": adapter.spec.required_class,
            "adapterVersion": adapter.spec.adapter_version,
        },
    }
