#!/usr/bin/env python3
"""Immutable stage receipts and the shared full/incremental runtime engine.

Full and incremental profiles share one business-stage DAG and the same
collector → normalizer → ingestor → QC → publisher path. Only selector,
cursor, range, and freshness differ between profiles.

Locks, stage receipts, retry resume, and same-input no-op semantics live here
so ``pipelines/run_daily.py`` and ``scripts/backend.py`` stay thin adapters.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


STAGES = ("ACQUIRED", "VERIFIED", "INGESTED", "DERIVED", "QC_PASSED", "PUBLISHED")

# One engine path for both profiles. Collectors/normalizers/ingestors/QC/
# publisher stages are shared; profiles only change selector/cursor/range/
# freshness before each shared stage runs.
SHARED_ENGINE_PATH = (
    "collectors",
    "normalizers",
    "ingestors",
    "qc",
    "publisher",
)

ENGINE_ID = "cardz.shared_daily_engine"
ENGINE_ENTRYPOINT = "pipelines/run_daily.py"
INPUT_FINGERPRINT_NAME = "00-INPUT.json"

# Profile diffs are intentionally limited to selector/cursor/range/freshness.
ENGINE_PROFILES: dict[str, dict[str, Any]] = {
    "full": {
        "profileId": "full",
        "registryProfileId": "full-backfill",
        "backendAction": "full-backfill",
        "entrypoint": ENGINE_ENTRYPOINT,
        "sharedPath": list(SHARED_ENGINE_PATH),
        "stages": list(STAGES),
        "selector": "full_universe_or_candidate_overlay",
        "cursorPolicy": "from_origin_or_explicit_worklist",
        "rangePolicy": "all_selected_ids",
        "freshnessPolicy": "ignore_freshness_for_history_backfill",
        "defaultBackendOnly": True,
    },
    "incremental": {
        "profileId": "incremental",
        "registryProfileId": "daily",
        "backendAction": "daily",
        "entrypoint": ENGINE_ENTRYPOINT,
        "sharedPath": list(SHARED_ENGINE_PATH),
        "stages": list(STAGES),
        "selector": "due_delta_tracked_universe",
        "cursorPolicy": "resume_last_good_receipt",
        "rangePolicy": "stale_or_missing_only",
        "freshnessPolicy": "respect_price_and_population_max_age",
        "defaultBackendOnly": True,
    },
}

PROFILE_DIFF_KEYS = ("selector", "cursorPolicy", "rangePolicy", "freshnessPolicy")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_evidence(paths: Iterable[Path]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for path in sorted({item.resolve() for item in paths}, key=str):
        if not path.is_file():
            continue
        evidence.append(
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    return evidence


def receipt_bytes(value: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        + b"\n"
    )


def stage_receipt_path(receipt_root: Path, stage: str) -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown daily stage: {stage}")
    return receipt_root.resolve() / f"{STAGES.index(stage) + 1:02d}-{stage}.json"


def shared_business_stages() -> tuple[str, ...]:
    """Business stages shared by full and incremental profiles."""

    return STAGES


def engine_profile(profile_id: str) -> dict[str, Any]:
    key = str(profile_id or "").strip().casefold()
    if key not in ENGINE_PROFILES:
        raise ValueError(f"unknown engine profile: {profile_id!r}; expected full|incremental")
    return dict(ENGINE_PROFILES[key])


def profile_selector_diff(left: str = "full", right: str = "incremental") -> dict[str, Any]:
    """Return only the fields that may differ between full and incremental."""

    a = engine_profile(left)
    b = engine_profile(right)
    return {
        key: {"full" if left == "full" else left: a[key], "incremental" if right == "incremental" else right: b[key]}
        for key in PROFILE_DIFF_KEYS
    }


def profiles_share_engine(left: str = "full", right: str = "incremental") -> bool:
    a = engine_profile(left)
    b = engine_profile(right)
    return (
        a["entrypoint"] == b["entrypoint"] == ENGINE_ENTRYPOINT
        and a["sharedPath"] == b["sharedPath"] == list(SHARED_ENGINE_PATH)
        and a["stages"] == b["stages"] == list(STAGES)
    )


def compute_input_fingerprint(material: Mapping[str, Any]) -> str:
    """Stable content hash of the engine input set (selector material only)."""

    payload = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_input_fingerprint(
    receipt_root: Path,
    *,
    run_id: str,
    profile_id: str,
    fingerprint: str,
    material: Mapping[str, Any] | None = None,
) -> Path:
    root = receipt_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = root / INPUT_FINGERPRINT_NAME
    document = {
        "schemaVersion": 1,
        "engineId": ENGINE_ID,
        "runId": run_id,
        "profileId": engine_profile(profile_id)["profileId"],
        "inputFingerprint": fingerprint,
        "material": dict(material or {}),
    }
    payload = receipt_bytes(document)
    if destination.is_file():
        if destination.read_bytes() != payload:
            raise RuntimeError(f"immutable engine input fingerprint mismatch: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def load_input_fingerprint(receipt_root: Path) -> str | None:
    path = receipt_root.resolve() / INPUT_FINGERPRINT_NAME
    if not path.is_file():
        return None
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise RuntimeError(f"engine input fingerprint is invalid: {path}")
    fingerprint = document.get("inputFingerprint")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise RuntimeError(f"engine input fingerprint is invalid: {path}")
    return fingerprint


def load_completed_stages(receipt_root: Path) -> list[str]:
    """Return the contiguous passed prefix of stages under ``receipt_root``."""

    root = receipt_root.resolve()
    completed: list[str] = []
    for stage in STAGES:
        path = stage_receipt_path(root, stage)
        if not path.is_file():
            break
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, Mapping) or document.get("status") != "passed":
            break
        if document.get("stage") != stage:
            raise RuntimeError(f"stage receipt stage mismatch: {path}")
        completed.append(stage)
    return completed


def next_resume_stage(receipt_root: Path) -> str | None:
    """First incomplete stage, or ``None`` when the run is fully complete."""

    completed = load_completed_stages(receipt_root)
    if len(completed) >= len(STAGES):
        return None
    return STAGES[len(completed)]


def stages_to_execute(receipt_root: Path | None, *, resume: bool = True) -> list[str]:
    """Stages still required. Resume never re-runs completed predecessors."""

    if receipt_root is None or not resume:
        return list(STAGES)
    completed = load_completed_stages(receipt_root)
    return list(STAGES[len(completed) :])


def same_input_is_noop(
    receipt_root: Path,
    *,
    input_fingerprint: str,
) -> bool:
    """True when the same input already completed every business stage."""

    stored = load_input_fingerprint(receipt_root)
    if stored is None or stored != input_fingerprint:
        return False
    return load_completed_stages(receipt_root) == list(STAGES)


def plan_engine_run(
    profile_id: str,
    *,
    receipt_root: Path | None = None,
    input_fingerprint: str | None = None,
    resume: bool = True,
    dry_run: bool = True,
    allow_publish: bool = False,
    allow_timer: bool = False,
    allow_database_apply: bool = False,
) -> dict[str, Any]:
    """Plan one full or incremental run on the shared engine.

    Dry-run / test mode never enables pointer writes, timer mutation, or DB
    apply. Partial retry resumes at the first incomplete stage without
    replaying completed upstream stages (and therefore without advancing their
    cursors again).
    """

    if allow_timer:
        raise RuntimeError("engine forbids timer_enable; production timers stay operator-gated")
    profile = engine_profile(profile_id)
    completed: list[str] = []
    if receipt_root is not None and resume:
        if input_fingerprint is not None:
            stored = load_input_fingerprint(receipt_root)
            if stored is not None and stored != input_fingerprint:
                raise RuntimeError(
                    "partial retry refuses to resume under a different input fingerprint; "
                    "downstream cursors stay unchanged"
                )
            if same_input_is_noop(receipt_root, input_fingerprint=input_fingerprint):
                return {
                    "engineId": ENGINE_ID,
                    "profile": profile,
                    "status": "noop",
                    "reason": "same_input_already_complete",
                    "stagesCompleted": list(STAGES),
                    "stagesToRun": [],
                    "resumeFrom": None,
                    "dryRun": bool(dry_run),
                    "sideEffects": {
                        "pointer_write": False,
                        "timer_enable": False,
                        "database_apply": False,
                        "publish": False,
                    },
                    "selectorDiffOnly": PROFILE_DIFF_KEYS,
                }
        completed = load_completed_stages(receipt_root)
    stages = stages_to_execute(receipt_root, resume=resume)
    # Publish is a separate approval gate. Dry-run and backend-only never plan it.
    publish_planned = bool(allow_publish) and not dry_run and "PUBLISHED" in stages
    if not publish_planned and "PUBLISHED" in stages and (dry_run or not allow_publish):
        # Keep stage visible for documentation but mark it gated.
        gated_stages = [stage for stage in stages if stage != "PUBLISHED"]
    else:
        gated_stages = list(stages)
    status = "dry_run_plan" if dry_run else ("resume" if completed else "run")
    if not stages:
        status = "noop"
    return {
        "engineId": ENGINE_ID,
        "profile": profile,
        "status": status,
        "stagesCompleted": completed,
        "stagesToRun": gated_stages,
        "stagesGated": (
            ["PUBLISHED"] if ("PUBLISHED" in stages and "PUBLISHED" not in gated_stages) else []
        ),
        "resumeFrom": stages[0] if stages else None,
        "dryRun": bool(dry_run),
        "sideEffects": {
            "pointer_write": bool(publish_planned),
            "timer_enable": False,
            "database_apply": bool(allow_database_apply) and not dry_run and bool(gated_stages),
            "publish": bool(publish_planned),
        },
        "selectorDiffOnly": PROFILE_DIFF_KEYS,
        "sharedWith": {
            "entrypoint": ENGINE_ENTRYPOINT,
            "path": list(SHARED_ENGINE_PATH),
            "stages": list(STAGES),
        },
    }


def assert_zero_mutation_side_effects(plan: Mapping[str, Any]) -> None:
    """Fail closed if a dry-run/test plan claims pointer or timer mutation."""

    side = plan.get("sideEffects")
    if not isinstance(side, Mapping):
        raise RuntimeError("engine plan missing sideEffects")
    if side.get("pointer_write"):
        raise RuntimeError("dry-run/test plan must not enable pointer_write")
    if side.get("timer_enable"):
        raise RuntimeError("dry-run/test plan must not enable timer_enable")
    if plan.get("dryRun") and side.get("database_apply"):
        raise RuntimeError("dry-run plan must not enable database_apply")
    if plan.get("dryRun") and side.get("publish"):
        raise RuntimeError("dry-run plan must not enable publish")


def write_stage_receipt(
    receipt_root: Path,
    *,
    run_id: str,
    stage: str,
    inputs: Iterable[Path] = (),
    outputs: Iterable[Path] = (),
    counts: Mapping[str, int] | None = None,
    rejection_reasons: Mapping[str, int] | None = None,
    error_count: int = 0,
    completed_at: datetime | None = None,
) -> Path:
    if stage not in STAGES:
        raise ValueError(f"unknown daily stage: {stage}")
    if not run_id.strip():
        raise ValueError("daily stage receipt requires run_id")
    if error_count < 0:
        raise ValueError("daily stage error_count cannot be negative")
    root = receipt_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    index = STAGES.index(stage)
    if index and not (root / f"{index:02d}-{STAGES[index - 1]}.json").is_file():
        raise RuntimeError(f"{stage} cannot be recorded before {STAGES[index - 1]}")
    document = {
        "schemaVersion": 1,
        "runId": run_id,
        "stage": stage,
        "status": "passed" if error_count == 0 else "failed",
        "completedAt": (completed_at or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "inputs": file_evidence(inputs),
        "outputs": file_evidence(outputs),
        "counts": dict(sorted((counts or {}).items())),
        "rejectionReasons": dict(sorted((rejection_reasons or {}).items())),
        "errorCount": error_count,
    }
    destination = root / f"{index + 1:02d}-{stage}.json"
    payload = receipt_bytes(document)
    if destination.is_file():
        if destination.read_bytes() != payload:
            raise RuntimeError(f"immutable daily stage receipt mismatch: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination
