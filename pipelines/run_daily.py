#!/usr/bin/env python3
"""Fail-closed standalone CARDZ daily market-data intake and publisher.

G10 is the frozen 600-card universe and bounded last-good source. Exact
GemRate population plus SNK PSA 10 price history are refreshed by this one
parent job. JLP is a possible future adapter, not a current runtime dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from fx_rates import (
    DEFAULT_CACHE as DEFAULT_FX_CACHE,
    DEFAULT_ENDPOINT as DEFAULT_FX_ENDPOINT,
    collect_or_reuse as collect_or_reuse_fx,
    validate_snapshot as validate_fx_snapshot,
)
from g10_ingest import (
    build_daily_observations,
    freeze_landing,
    iso_utc,
    parse_effective_at,
    read_json,
)
from g10_public_snapshot import (
    candidate_rows,
    load_ingest_at,
    load_price_effective_at,
    write_json,
)
from db_runtime import active_universe_lock_hash
from run_receipts import (
    assert_zero_mutation_side_effects,
    compute_input_fingerprint,
    engine_profile,
    plan_engine_run,
    profiles_share_engine,
    shared_business_stages,
    write_input_fingerprint,
    write_stage_receipt,
)
try:
    from .failure_ledger import record_failure, record_resolution
except ImportError:
    from failure_ledger import record_failure, record_resolution


ROOT = Path(__file__).resolve().parents[1]
GRADE10_INTEGRATION_ROOT = ROOT / "integrations" / "grade10"
DEFAULT_SOURCE_ROOT = GRADE10_INTEGRATION_ROOT / "data"
DEFAULT_ACQUIRE_SCRIPT = GRADE10_INTEGRATION_ROOT / "run_service.py"
DEFAULT_KADO_ROOT = ROOT / "data" / "private" / "kado"
COVERAGE_AUDIT_PATH = ROOT / "pipelines" / "data_coverage_audit.py"
CANONICAL_DB_QC_PATH = ROOT / "pipelines" / "canonical_db_qc.py"
QC_FAILURE_SYNC_PATH = ROOT / "pipelines" / "qc_failure_sync.py"
FAILURE_LEDGER_PATH = ROOT / "pipelines" / "failure_ledger.py"
CANONICAL_SNAPSHOT_PATH = ROOT / "pipelines" / "canonical_public_snapshot.py"
PUBLIC_SNAPSHOT_QC_PATH = ROOT / "pipelines" / "public_snapshot_qc.py"
DEFAULT_ACTIVE_UNIVERSE = ROOT / "data/runtime/private-source-map/tracked-universe.json"
RETRY_WORKLIST_ROOT = ROOT / "data/runtime/private-reports/retry-worklists"
PRESENTATION_VIEWS = ("top100", "top300", "top350", "top100_plus_200", "top300_boards", "all_eligible")

# 揀 5%：checked-in 嘅每個 snapshot 版本都係 100 + 260 = 360 張，即係正常 churn 實測係 0，
# 收緊個閘喺實務上唔會嘈。唯一見過嘅變動就係要攔嗰單 360 → 192（-46.7%）。5% 喺現行
# 192 張 catalog 上面 ≈ 9 張，夠位俾真係落榜嘅卡走，但因為呢條鏈係單向棘輪（跌咗嘅卡
# 張圖即刻俾 quarantine 搬走，返唔到轉頭），閘一鬆就變成靜靜雞連續縮水：25% 嘅話兩個
# run 就可以合法地劈一半。寧願要人手明確批准一次，好過默許滑落。
DEFAULT_MAX_CATALOG_SHRINK_PCT = 5.0


class StaleSourceError(RuntimeError):
    pass


class CatalogShrinkError(RuntimeError):
    pass


@contextmanager
def singleton_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        handle.close()
        raise RuntimeError("another CARDZ daily pipeline run is active") from error
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def run_checked(
    command: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: Mapping[str, str] | None = None,
    allowed_returncodes: tuple[int, ...] = (),
) -> int:
    command_hash = hashlib.sha256(
        json.dumps(command, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    executable = Path(command[1] if len(command) > 1 and "python" in Path(command[0]).name.lower() else command[0])
    item_key = f"{executable.name}:{command_hash}"
    try:
        subprocess.run(command, cwd=cwd, timeout=timeout, check=True, env=dict(env) if env else None)
    except subprocess.TimeoutExpired:
        record_failure(
            source="daily",
            stage="subprocess",
            script=__file__,
            item_key=item_key,
            reason_code="timeout",
            message="registered daily subprocess timed out",
            retryable=True,
            context={"executable": executable.name, "timeoutSeconds": timeout},
            next_action="agent_review_then_retry",
            error_type="TimeoutExpired",
        )
        raise
    except subprocess.CalledProcessError as error:
        if error.returncode in allowed_returncodes:
            return error.returncode
        record_failure(
            source="daily",
            stage="subprocess",
            script=__file__,
            item_key=item_key,
            reason_code="nonzero_exit",
            message="registered daily subprocess returned non-zero",
            retryable=True,
            context={"executable": executable.name, "returnCode": error.returncode},
            next_action="agent_review_then_retry",
            error_type="CalledProcessError",
        )
        raise
    record_resolution(
        source="daily",
        stage="subprocess",
        script=__file__,
        item_key=item_key,
        resolution="subprocess_completed",
        context={"executable": executable.name},
    )
    return 0


def source_attempt_id(started_at: datetime) -> str:
    """One immutable source attempt per invocation, grouped by UTC logical date."""

    return started_at.astimezone(timezone.utc).strftime("sources_%Y%m%dT%H%M%S%fZ")


def enforce_gemrate_refresh(*, required: bool, refreshed: bool) -> None:
    if required and not refreshed:
        raise RuntimeError("required GemRate refresh did not complete; canonical import and pointer stay unchanged")


def requires_remote_publish(
    *,
    backend_only: bool,
    bootstrap_only: bool,
    local_only: bool,
) -> bool:
    """Return whether this invocation enters the remote publication contract."""

    return not backend_only and not bootstrap_only and not local_only


def shared_engine_entrypoint() -> str:
    """Single runtime entrypoint used by both full and incremental profiles."""

    return "pipelines/run_daily.py"


def resolve_run_profile(profile: str | None, *, landing_hint: str | None = None) -> str:
    """Map CLI/registry labels onto the shared engine profile ids."""

    if profile:
        normalized = profile.strip().casefold()
        aliases = {
            "full": "full",
            "full-backfill": "full",
            "incremental": "incremental",
            "daily": "incremental",
            "due-delta": "incremental",
        }
        if normalized not in aliases:
            raise ValueError(f"unknown run profile: {profile!r}")
        return aliases[normalized]
    if landing_hint == "full":
        return "full"
    return "incremental"


def build_engine_input_material(
    *,
    profile_id: str,
    active_universe: Path | None,
    mode: str,
    backend_only: bool,
) -> dict[str, Any]:
    """Selector-scoped material hashed for same-input no-op detection."""

    profile = engine_profile(profile_id)
    return {
        "profileId": profile["profileId"],
        "selector": profile["selector"],
        "cursorPolicy": profile["cursorPolicy"],
        "rangePolicy": profile["rangePolicy"],
        "freshnessPolicy": profile["freshnessPolicy"],
        "mode": mode,
        "backendOnly": bool(backend_only),
        "activeUniverse": str(active_universe.resolve()) if active_universe else None,
        "sharedStages": list(shared_business_stages()),
        "entrypoint": shared_engine_entrypoint(),
    }


def backend_database_command(backend: Path, action: str, active_universe: Path) -> list[str]:
    return [
        sys.executable,
        str(backend),
        action,
        "--active-universe",
        str(active_universe.resolve()),
    ]


def canonical_db_qc_command(
    run_id: str, release_profile: str = "relaxed-launch-v1"
) -> list[str]:
    """Bind the public path to one immutable, read-only canonical DB QC receipt."""

    return [
        sys.executable,
        str(CANONICAL_DB_QC_PATH),
        "--run-id",
        run_id,
        "--release-profile",
        release_profile,
    ]


def canonical_db_qc_failure_commands(report: Path, run_id: str) -> list[list[str]]:
    """Persist canonical QC blockers and refresh the agent retry worklists."""

    report = report.resolve()
    return [
        [
            sys.executable,
            str(QC_FAILURE_SYNC_PATH),
            "--report",
            str(report),
            "--write",
            "--summary-only",
        ],
        *[
            [
                sys.executable,
                str(FAILURE_LEDGER_PATH),
                "export-retry",
                "--source",
                "canonical_db_qc",
                "--script",
                "pipelines/qc_failure_sync.py",
                "--stage",
                lane,
                "--out",
                str((RETRY_WORKLIST_ROOT / f"{run_id}-{lane}.json").resolve()),
            ]
            for lane in ("psa10_price", "sales")
        ],
    ]


def price_sales_gate_evaluation_command(
    report: Path,
    expected_candidate_sha256: str,
    result_out: Path,
) -> list[str]:
    """Export the exact price+sales-passed cohort before any image work."""

    expected = expected_candidate_sha256.strip().casefold()
    if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
        raise ValueError("canonical QC candidate SHA-256 is invalid")
    return [
        sys.executable,
        str(ROOT / "pipelines/market_alerts.py"),
        "--dry-run",
        "--universe-qc-report",
        str(report.resolve()),
        "--expected-universe-candidate-sha256",
        expected,
        "--price-sales-only",
        "--result-out",
        str(result_out.resolve()),
    ]


def market_alert_evaluation_command(result_out: Path) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "pipelines/market_alerts.py"),
        "--result-out",
        str(result_out.resolve()),
    ]


def mark_market_evaluation_passed_command(evaluation_id: int) -> list[str]:
    if evaluation_id <= 0:
        raise ValueError("evaluation ID must be a positive integer")
    return [
        sys.executable,
        str(ROOT / "pipelines/market_alerts.py"),
        "--mark-passed-evaluation-id",
        str(evaluation_id),
    ]


def load_evaluation_id(result_path: Path) -> int:
    document = read_json(result_path.resolve())
    evaluation_id = document.get("evaluationId")
    if not isinstance(evaluation_id, int) or isinstance(evaluation_id, bool) or evaluation_id <= 0:
        raise RuntimeError("market alert evaluation receipt has no valid evaluationId")
    return evaluation_id


def gemrate_daily_command(
    gemrate_ids: Path,
    active_universe: Path,
    source_root: Path,
    *,
    timeout: int | None = None,
) -> list[str]:
    """Build the population-authority refresh command without assuming an API key."""

    command = [
        sys.executable,
        str(ROOT / "pipelines/gemrate_source.py"),
        "daily",
        "--ids-file",
        str(gemrate_ids),
        "--identity-file",
        str(active_universe),
        "--mirror-root",
        str(source_root),
        "--speed",
        "medium",
    ]
    if timeout:
        # Give the browser pass half the step's timeout. It must return partial
        # rather than be killed on the wall: a killed process loses the direct
        # and mirror results it already staged, a partial one keeps them.
        command += ["--website-budget-seconds", str(max(60, timeout // 2))]
    return command


def post_derive_audit_command(
    snk_run: Path | None,
    *,
    required_presentation_view: str | None = None,
    active_universe: Path | None = None,
    production: bool | None = None,
) -> list[str]:
    """Audit the current run only, after canonical derive has completed."""

    if snk_run is None:
        raise RuntimeError("post-derive audit requires a current SNK PSA 10 run")
    command = [sys.executable, str(COVERAGE_AUDIT_PATH), "--snk-run", str(snk_run.resolve())]
    command.append("--discovery-complete")
    if active_universe is not None:
        command.extend(["--active-universe", str(active_universe.resolve())])
    if required_presentation_view is None:
        required_presentation_view = "top300_boards"
    if required_presentation_view:
        command.extend(["--require-presentation-view", required_presentation_view])
    return command


def run_private_acquisition(script: Path, source_root: Path, timeout: int, expected_cards: int) -> None:
    resolved = script.resolve()
    source_repo = source_root.resolve().parent
    if resolved.parent != source_repo:
        raise RuntimeError("private acquisition script and data root must share one integration directory")
    # Images are part of the daily acquisition contract. The source collector
    # re-fetches them and keeps unchanged bytes in place; a changed image gets
    # a new content hash during raw-front QC and publication.
    if resolved.name.casefold() == "run_service.py":
        command = [
            sys.executable,
            str(resolved),
            "collect",
            "--scope",
            "full",
            "--expected-cards",
            str(expected_cards),
            "--timeout-seconds",
            str(timeout),
        ]
    elif resolved.name.casefold() == "grade10_scraper.py":
        command = [sys.executable, str(resolved)]
    else:
        raise RuntimeError("private acquisition must select run_service.py or grade10_scraper.py")
    run_checked(command, cwd=source_repo, timeout=timeout)


def validate_source(source_root: Path, expected_cards: int, max_age_hours: float) -> tuple[datetime, datetime, int]:
    state_path = source_root / "_state" / "last_run.json"
    if not state_path.is_file():
        raise RuntimeError(f"missing source run state: {state_path}")
    state = read_json(state_path)
    if not isinstance(state, Mapping):
        raise RuntimeError("source run state is invalid")
    ingest_at = parse_effective_at(str(state.get("lastRun") or ""))
    stated_count = state.get("cardCount")
    rows = candidate_rows(source_root)
    if stated_count != expected_cards or len(rows) != expected_cards:
        raise RuntimeError(
            f"source completeness gate failed: state={stated_count!r}, deduped={len(rows)}, expected={expected_cards}"
        )
    age = datetime.now(timezone.utc) - ingest_at
    if age > timedelta(hours=max_age_hours):
        raise StaleSourceError(f"source run is stale ({age.total_seconds() / 3600:.1f}h > {max_age_hours:g}h)")
    return ingest_at, load_price_effective_at(source_root), len(rows)


def bootstrap_history_exists(landing_root: Path) -> bool:
    return any((landing_root / "g10").rglob("canonical-batch.json"))


def landing_mode(landing_root: Path) -> str:
    return "incremental" if any((landing_root / "g10" / "full").glob("*/manifest.json")) else "full"


def latest_tag_catalog(
    runs_root: Path,
    *,
    current_run: Path,
    as_of: datetime,
    max_age_hours: float = 72,
) -> tuple[Path, date, datetime] | None:
    candidates: list[tuple[datetime, date, Path]] = []
    for manifest_path in runs_root.glob("sources_*/tag-populations.manifest.json"):
        if manifest_path.parent.resolve() == current_run.resolve():
            continue
        catalog = manifest_path.with_name("tag-catalog.jsonl")
        if not catalog.is_file():
            continue
        try:
            if catalog.stat().st_size == 0:
                continue
            manifest = read_json(manifest_path)
            if not isinstance(manifest, Mapping):
                continue
            observed = date.fromisoformat(str(manifest.get("observedDate") or ""))
            captured_at = parse_effective_at(str(manifest.get("capturedAt") or ""))
            expected_hash = str(manifest.get("catalogSha256") or "")
            actual_hash = hashlib.sha256(catalog.read_bytes()).hexdigest()
            if not expected_hash or actual_hash != expected_hash:
                continue
        except (OSError, TypeError, ValueError):
            continue
        if observed > as_of.date() or captured_at.date() < observed:
            continue
        age = as_of - captured_at
        if age < timedelta(0) or age > timedelta(hours=max_age_hours):
            continue
        candidates.append((captured_at, observed, catalog))
    if not candidates:
        return None
    captured_at, observed, catalog = max(candidates, key=lambda value: (value[0], value[1], value[2].as_posix()))
    return catalog, observed, captured_at


def write_canonical_batch(manifest_path: Path, source_root: Path, effective_at: datetime, manifest: Mapping[str, Any]) -> Path:
    fetched_at = parse_effective_at(str(manifest["effectiveAt"]))
    observations, rejected = build_daily_observations(source_root, effective_at, fetched_at)
    batch_path = manifest_path.parent / "canonical-batch.json"
    batch = {
        "schemaVersion": "2.0.0",
        "runId": manifest["runId"],
        "mode": manifest["mode"],
        "effectiveAt": iso_utc(effective_at),
        "fetchedAt": manifest["effectiveAt"],
        "payloadSha256": manifest["payloadSha256"],
        "observations": observations,
        "rejected": rejected,
    }
    if batch_path.is_file():
        if read_json(batch_path) != batch:
            raise RuntimeError(f"immutable canonical batch mismatch: {batch_path}")
    else:
        write_json(batch_path, batch)
    return batch_path


def run_market_source_refresh(
    source_root: Path,
    landing_root: Path,
    fx_cache: Path,
    run_id: str,
    effective_at: datetime,
    timeout: int,
    require_gemrate: bool,
    refresh_active_universe: bool,
    active_universe_path: Path | None = None,
    gemrate_ids_path: Path | None = None,
    snk_ids_path: Path | None = None,
    snk_run_override: Path | None = None,
) -> dict[str, Any]:
    """Refresh exact private sources and append one immutable source batch."""

    map_root = ROOT / "data/runtime/private-source-map"
    crosswalk = map_root / "source-crosswalk.json"
    active_universe = (active_universe_path or map_root / "tracked-universe.json").resolve()
    gemrate_ids = (gemrate_ids_path or map_root / "tracked-gemrate-ids.txt").resolve()
    snk_ids = (snk_ids_path or map_root / "tracked-snk-ids.txt").resolve()
    if refresh_active_universe and active_universe_path is not None:
        raise RuntimeError("--refresh-active-universe cannot overwrite an explicit --active-universe")
    if refresh_active_universe or not active_universe.is_file():
        run_checked(
            [
                sys.executable,
                str(ROOT / "pipelines/source_crosswalk.py"),
                "--source-root", str(source_root),
                "--out", str(crosswalk),
            ],
            cwd=ROOT,
            timeout=timeout,
        )
        run_checked(
            [
                sys.executable,
                str(ROOT / "pipelines/tracked_universe.py"),
                "--landing-root", str(landing_root),
                "--out", str(active_universe),
                "--gemrate-ids-out", str(gemrate_ids),
                "--snk-ids-out", str(snk_ids),
                "--source-root", str(source_root),
                "--crosswalk", str(crosswalk),
            ],
            cwd=ROOT,
            timeout=timeout,
        )
    if not gemrate_ids.is_file() or not snk_ids.is_file():
        raise RuntimeError("tracked-universe provider worklists are missing")
    tracked_document = read_json(active_universe)
    tracked_hash = active_universe_lock_hash(tracked_document)
    if len(tracked_hash) != 64:
        raise RuntimeError("tracked-universe payload hash is invalid")
    source_run_id = f"{run_id}_{tracked_hash[:12]}"
    run_root = ROOT / "data/runtime/private-source-runs" / source_run_id
    snk_run = (snk_run_override or run_root / "snk-psa10.jsonl").resolve()
    ebay_run = run_root / "ebay-psa10-sold.jsonl"
    tag_catalog = run_root / "tag-catalog.jsonl"
    tag_run = run_root / "tag-populations.jsonl"
    tag_manifest = run_root / "tag-populations.manifest.json"
    tag_review = run_root / "tag-populations.review.json"
    tag_status_path = run_root / "tag-source-status.json"

    # GemRate is the population authority, while the transport may be the
    # direct API, page-initiated public card-page JSON/DOM fallback, or the
    # exact Grade10 mirror.
    # A missing trial key must never skip population refresh silently.
    #
    # A partial refresh is not a reason to abandon the day. The route manifest
    # declares psa10_population failureMode=exclude_from_ranking with
    # staleHours=168, and gemrate_source.py already leaves the durable POP
    # cache untouched when it cannot resolve every tracked card. Aborting here
    # instead threw away the whole chain -- prices, index snapshot, publish --
    # so a single unreachable card froze the public snapshot indefinitely
    # (observed 2026-07-24 -> 07-26: three days with no index row). Record the
    # degraded state on the run and carry on with last-good population.
    try:
        run_checked(
            gemrate_daily_command(gemrate_ids, active_universe, source_root, timeout=timeout),
            cwd=ROOT,
            timeout=timeout,
            env=os.environ,
        )
        gemrate_live = True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        gemrate_live = False
        print(
            f"[daily] population refresh degraded ({type(error).__name__}); "
            "continuing on last-good population, gemrateLive=false",
            file=sys.stderr,
        )
    enforce_gemrate_refresh(required=require_gemrate, refreshed=gemrate_live)

    # TAG is auxiliary grader-population coverage. A live schema defect must
    # not block the primary SNK price run. Reuse only a bounded last-good
    # catalog and preserve its original observed date; otherwise omit TAG for
    # this batch and leave the database's prior observation untouched.
    tag_status = "ready"
    tag_input: Path | None = tag_run
    if tag_run.is_file() and tag_manifest.is_file() and tag_review.is_file() and tag_status_path.is_file():
        try:
            status_document = read_json(tag_status_path)
        except (OSError, TypeError, ValueError):
            status_document = {}
        if isinstance(status_document, Mapping):
            tag_status = str(status_document.get("status") or "unavailable")
        else:
            tag_status = "unavailable"
        if tag_status not in {"ready", "last_good"}:
            tag_status = "unavailable"
            tag_input = None
    else:
        try:
            run_checked(
                [
                    sys.executable,
                    str(ROOT / "pipelines/tag_daily_capture.py"),
                    "--active-universe", str(active_universe),
                    "--catalog-out", str(tag_catalog),
                    "--out", str(tag_run),
                    "--manifest-out", str(tag_manifest),
                    "--review-out", str(tag_review),
                ],
                cwd=ROOT,
                timeout=timeout,
                env=os.environ,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            fallback = latest_tag_catalog(
                run_root.parent,
                current_run=run_root,
                as_of=datetime.now(timezone.utc),
            )
            if fallback is None:
                tag_status = "unavailable"
                tag_input = None
            else:
                fallback_catalog, fallback_date, fallback_captured_at = fallback
                try:
                    run_checked(
                        [
                            sys.executable,
                            str(ROOT / "pipelines/tag_daily_capture.py"),
                            "--active-universe", str(active_universe),
                            "--from-file", str(fallback_catalog),
                            "--observed-date", fallback_date.isoformat(),
                            "--captured-at", iso_utc(fallback_captured_at),
                            "--out", str(tag_run),
                            "--manifest-out", str(tag_manifest),
                            "--review-out", str(tag_review),
                        ],
                        cwd=ROOT,
                        timeout=timeout,
                        env=os.environ,
                    )
                except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
                    tag_status = "unavailable"
                    tag_input = None
                else:
                    tag_status = "last_good"
        write_json(
            tag_status_path,
            {
                "status": tag_status,
                "recordedAt": iso_utc(datetime.now(timezone.utc)),
                "observedDate": (
                    read_json(tag_manifest).get("observedDate") if tag_manifest.is_file() else None
                ),
            },
        )

    if snk_run_override is None:
        run_checked(
            [
                sys.executable,
                str(ROOT / "pipelines/snk_market_data.py"),
                "--ids-file", str(snk_ids),
                "--condition", "trading_card_single_psa10",
                "--run-id", source_run_id,
                "--out", str(snk_run),
            ],
            cwd=ROOT,
            timeout=timeout,
        )
    elif not snk_run.is_file():
        raise RuntimeError(f"explicit SNK run does not exist: {snk_run}")
    ebay_status = "disabled"
    # eBay PSA10 sold evidence: repository-owned file OR PriceCharting export
    # (eBay-derived). Direct eBay sold search remains PerimeterX-blocked.
    # 預設開；CARDZ_EBAY_SOLD_ENABLED=false 先至關。PC export 係獨立 job 產生,
    # 佢一停 daily 就會靜靜雞日日食舊檔 — 所以食之前一定要驗鮮度。
    if os.environ.get("CARDZ_EBAY_SOLD_ENABLED", "true").casefold() != "false":
        ebay_env = dict(os.environ)
        ebay_input = Path(ebay_env.get("CARDZ_EBAY_SOLD_INPUT", "") or "")
        default_pc_export = ROOT / "data/runtime/private-source-map/ebay-sold-from-pricecharting.json"
        if (not ebay_input.is_file()) and default_pc_export.is_file():
            ebay_env["CARDZ_EBAY_SOLD_INPUT"] = str(default_pc_export)
            ebay_input = default_pc_export
        try:
            if not ebay_input.is_file():
                raise RuntimeError(
                    "eBay sold enabled but no CARDZ_EBAY_SOLD_INPUT / "
                    "pricecharting export present"
                )
            ebay_input_age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                ebay_input.stat().st_mtime, tz=timezone.utc
            )
            if ebay_input_age > timedelta(hours=48):
                ebay_status = "stale_input"
                print(
                    f"eBay sold input is stale ({ebay_input_age.total_seconds() / 3600:.1f}h > 48h): "
                    f"{ebay_input}; skipping eBay normalize for this run",
                    file=sys.stderr,
                )
                raise RuntimeError("stale eBay sold input")
            run_checked(
                [
                    sys.executable,
                    str(ROOT / "pipelines/ebay_sold_data.py"),
                    "--active-universe", str(active_universe),
                    "--input", str(ebay_input),
                    "--out", str(ebay_run),
                ],
                cwd=ROOT,
                timeout=timeout,
                env=ebay_env,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, RuntimeError):
            if ebay_status != "stale_input":
                ebay_status = "unavailable"
            ebay_run.unlink(missing_ok=True)
        else:
            ebay_status = "ready"
    market_sync_command = [
        sys.executable,
        str(ROOT / "pipelines/market_source_sync.py"),
        "--active-universe", str(active_universe),
        "--snk-run", str(snk_run),
        "--fx-snapshot", str(fx_cache),
        "--landing-root", str(landing_root),
        "--run-id", source_run_id,
        "--effective-at", iso_utc(effective_at),
    ]
    if tag_input is not None:
        market_sync_command.extend(["--tag-run", str(tag_input)])
    if ebay_status == "ready":
        market_sync_command.extend(["--ebay-run", str(ebay_run)])
    run_checked(market_sync_command, cwd=ROOT, timeout=timeout)
    active_document = read_json(active_universe)
    tag_document = read_json(tag_manifest) if tag_manifest.is_file() else {}
    return {
        "gemrateLive": gemrate_live,
        "snkRun": str(snk_run),
        "ebayRun": str(ebay_run) if ebay_status == "ready" else None,
        "ebayStatus": ebay_status,
        "tagRun": str(tag_input) if tag_input is not None else None,
        "tagStatus": tag_status,
        "tagCards": tag_document.get("counts", {}).get("matched") if isinstance(tag_document, Mapping) else None,
        "crosswalk": str(crosswalk),
        "activeUniverse": str(active_universe),
        "activeUniverseLockId": tracked_hash,
        "activeCards": active_document.get("counts", {}).get("uniqueCards") if isinstance(active_document, Mapping) else None,
    }


def published_card_count(snapshot: Mapping[str, Any]) -> int:
    """已發佈卡數：同 `verify_images.referenced_asset_names` 睇同一批卡（top100 + watchlist）。"""

    return len(snapshot.get("top100") or []) + len(snapshot.get("watchlist") or [])


def assert_catalog_not_shrinking(
    candidate_snapshot: Path,
    published_snapshot: Path,
    max_shrink_pct: float,
) -> None:
    """Compare with the pointed last-good generation before advancing latest.json."""

    if not published_snapshot.is_file():
        return  # 未有基準（首次發佈）：冇嘢可以比較，唔應該攔。
    before = published_card_count(read_json(published_snapshot))
    if before <= 0:
        return
    after = published_card_count(read_json(candidate_snapshot))
    if after >= before:
        return
    shrink_pct = (before - after) / before * 100
    if shrink_pct <= max_shrink_pct:
        return
    raise CatalogShrinkError(
        f"catalog shrink gate: published cards drop from {before} to {after} "
        f"(-{shrink_pct:.1f}%), over the -{max_shrink_pct:.1f}% limit. "
        f"The runtime pointer is unchanged. Investigate the export first; if this "
        f"drop is genuinely correct, approve it explicitly for this one run with "
        f"--max-catalog-shrink-pct {math.ceil(shrink_pct)}"
    )


def runtime_snapshot_from_pointer(publish_root: Path) -> Path | None:
    """Resolve the last promoted immutable generation without trusting path traversal."""

    root = publish_root.resolve()
    pointer_path = root / "latest.json"
    if not pointer_path.is_file():
        return None
    pointer = read_json(pointer_path)
    snapshot_key = str(pointer.get("snapshotKey") or "") if isinstance(pointer, Mapping) else ""
    if not snapshot_key:
        raise RuntimeError(f"runtime snapshot pointer has no snapshotKey: {pointer_path}")
    snapshot = (root / Path(snapshot_key)).resolve()
    if not snapshot.is_relative_to(root):
        raise RuntimeError(f"runtime snapshot pointer escapes its generation root: {pointer_path}")
    if not snapshot.is_file():
        raise RuntimeError(f"runtime snapshot generation is missing: {snapshot}")
    return snapshot


def finalize_local_candidate_then_publish(
    candidate_snapshot: Path,
    candidate_image_manifest: Path,
    assets_out: Path,
    published_snapshot: Path | None,
    publish_command: list[str],
    production: bool,
    timeout: int,
    max_catalog_shrink_pct: float = DEFAULT_MAX_CATALOG_SHRINK_PCT,
) -> int:
    """Validate a candidate, then let the versioned publisher advance the sole pointer."""

    if not candidate_image_manifest.is_file():
        raise RuntimeError("candidate image QC manifest is missing")
    verify_images = [
        sys.executable,
        str(ROOT / "pipelines" / "verify_images.py"),
        "--snapshot", str(candidate_snapshot),
        "--assets", str(assets_out),
        "--manifest", str(candidate_image_manifest),
    ]
    if production:
        verify_images.append("--strict-semantic")
    # 第一 pass 容許未引用檔：assets 目錄係 content-addressed 累積落嚟，每次卡圖
    # 重算都會留低舊 sha，所以「有孤兒檔」係 quarantine 未行之前嘅正常狀態。
    # 用嚴格 pass 做第一道閘會令成條鏈死鎖——閘因為啲檔而 fail，而清走啲檔嗰步
    # 喺閘之後，永遠去唔到。呢一 pass 嘅職責係喺發佈之前確認 snapshot 本身
    # 完好（每張卡圖存在、hash 啱、尺寸啱、有 QC 記錄）。
    run_checked([*verify_images, "--allow-unreferenced"], cwd=ROOT, timeout=timeout)
    if published_snapshot is not None:
        assert_catalog_not_shrinking(candidate_snapshot, published_snapshot, max_catalog_shrink_pct)

    # publish-snapshot.mjs 先寫 immutable generation + generation-scoped assets，
    # 驗完整包，最後先 atomic replace latest.json。呢度唔再覆寫 tracked demo seed、
    # QC manifest 或 quarantine 共用 asset tree；失敗時上一個 pointer 原封不動。
    run_checked(publish_command, cwd=ROOT, timeout=timeout)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standalone CARDZ daily market-data pipeline")
    parser.add_argument("--mode", choices=("staging", "production"), required=True)
    parser.add_argument("--release-profile", default="relaxed-launch-v1")
    parser.add_argument(
        "--run-profile",
        choices=("full", "incremental", "full-backfill", "daily", "due-delta"),
        help="shared engine profile; full and incremental share stages/path and differ only in selector/cursor/range/freshness",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="plan the shared engine only; never lock, apply DB, publish, or touch timers/pointers",
    )
    parser.add_argument(
        "--resume-receipt-root",
        type=Path,
        help="existing stage-receipt root for retry resume / same-input no-op (dry-run or live)",
    )
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--kado-root", type=Path, default=DEFAULT_KADO_ROOT)
    parser.add_argument("--private-acquire-script", type=Path, default=DEFAULT_ACQUIRE_SCRIPT)
    parser.add_argument("--refresh-bootstrap-source", action="store_true",
                        help="run the broad G10 discovery/bootstrap refresh before the bounded daily collectors")
    parser.add_argument(
        "--bootstrap-only",
        action="store_true",
        help="refresh and freeze the immutable G10 bootstrap landing only; never sync MySQL or publish",
    )
    parser.add_argument("--acquire-timeout-seconds", type=int, default=5_400)
    parser.add_argument("--pipeline-timeout-seconds", type=int, default=7_200)
    parser.add_argument("--expected-card-count", type=int, default=600)
    parser.add_argument("--source-max-age-hours", type=float, default=720,
                        help="identity-universe freshness; daily price freshness is gated per card")
    parser.add_argument("--price-max-age-hours", type=float, default=48)
    parser.add_argument("--allow-stale-demo", action="store_true")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument(
        "--backend-only",
        action="store_true",
        help="collect the locked universe and sync MySQL without building or publishing the web snapshot",
    )
    parser.add_argument("--fx-cache", type=Path, default=DEFAULT_FX_CACHE)
    parser.add_argument("--fx-endpoint", default=os.environ.get("CARDZ_FX_ENDPOINT", DEFAULT_FX_ENDPOINT))
    parser.add_argument("--fx-timeout-seconds", type=float, default=20)
    parser.add_argument("--fx-retries", type=int, default=3)
    parser.add_argument("--fx-last-good-hours", type=float, default=72)
    parser.add_argument("--skip-fx-refresh", action="store_true")
    parser.add_argument("--skip-market-source-refresh", action="store_true")
    parser.add_argument(
        "--skip-database-sync",
        action="store_true",
        help="local staging recovery only; production must import and verify the canonical database",
    )
    parser.add_argument("--require-gemrate-refresh", action="store_true")
    parser.add_argument(
        "--required-presentation-view",
        choices=PRESENTATION_VIEWS,
        help="after collection and derive, require this export view before publication",
    )
    parser.add_argument(
        "--refresh-active-universe",
        action="store_true",
        help="explicitly replace the frozen Top 100 + candidate lock; daily runs reuse it by default",
    )
    parser.add_argument("--active-universe", type=Path, help="private active-universe overlay used for one controlled backend run")
    parser.add_argument("--gemrate-ids", type=Path, help="private GemRate ID worklist paired with --active-universe")
    parser.add_argument("--snk-ids", type=Path, help="private SNK ID worklist paired with --active-universe")
    parser.add_argument("--snk-run", type=Path, help="completed exact PSA 10 SNK run to reuse instead of recollecting")
    parser.add_argument("--landing-root", type=Path, default=ROOT / "data/runtime/private-landing")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument(
        "--max-catalog-shrink-pct",
        type=float,
        default=DEFAULT_MAX_CATALOG_SHRINK_PCT,
        help="how far the published card count may fall below the live snapshot before the run "
        "stops instead of promoting and quarantining; raise it to approve a real shrink",
    )
    parser.add_argument("--assets-out", type=Path, default=ROOT / "data/public/market-assets")
    parser.add_argument("--image-manifest", type=Path, default=ROOT / "manifests/image-qc.json")
    parser.add_argument("--publish-out", type=Path, default=ROOT / "data/runtime/publish-staging")
    parser.add_argument("--r2-bucket")
    parser.add_argument("--lock", type=Path, default=ROOT / "data/runtime/locks/daily.lock")
    args = parser.parse_args()

    if not args.r2_bucket:
        env_name = "CARDZ_PRODUCTION_R2_BUCKET" if args.mode == "production" else "CARDZ_STAGING_R2_BUCKET"
        args.r2_bucket = os.environ.get(env_name)

    run_profile = resolve_run_profile(args.run_profile)
    if not profiles_share_engine("full", "incremental"):
        raise RuntimeError("full and incremental must share one engine entrypoint, path, and stages")

    if args.dry_run:
        # Dry-run never acquires the daily lock, never writes pointers/timers,
        # and never enters collectors. It only emits the shared-engine plan.
        material = build_engine_input_material(
            profile_id=run_profile,
            active_universe=args.active_universe.resolve() if args.active_universe else None,
            mode=args.mode,
            backend_only=bool(args.backend_only),
        )
        fingerprint = compute_input_fingerprint(material)
        plan = plan_engine_run(
            run_profile,
            receipt_root=args.resume_receipt_root.resolve() if args.resume_receipt_root else None,
            input_fingerprint=fingerprint,
            resume=args.resume_receipt_root is not None,
            dry_run=True,
            allow_publish=False,
            allow_timer=False,
            allow_database_apply=False,
        )
        assert_zero_mutation_side_effects(plan)
        print(
            json.dumps(
                {
                    "status": plan["status"],
                    "mode": args.mode,
                    "runProfile": run_profile,
                    "inputFingerprint": fingerprint,
                    "enginePlan": plan,
                    "pointerWrite": False,
                    "timerEnable": False,
                },
                sort_keys=True,
            )
        )
        return 0

    if args.backend_only and args.r2_bucket:
        raise RuntimeError("--backend-only cannot be combined with an R2 bucket")
    if args.backend_only and args.skip_database_sync:
        raise RuntimeError("--backend-only requires database sync")
    if args.bootstrap_only and not args.refresh_bootstrap_source:
        raise RuntimeError("--bootstrap-only requires --refresh-bootstrap-source")
    if args.bootstrap_only and args.skip_database_sync:
        raise RuntimeError("--bootstrap-only owns no database sync; remove --skip-database-sync")
    explicit_universe_paths = (args.active_universe, args.gemrate_ids, args.snk_ids)
    if any(explicit_universe_paths) and not all(explicit_universe_paths):
        raise RuntimeError("--active-universe, --gemrate-ids and --snk-ids must be supplied together")
    if args.snk_run is not None and not all(explicit_universe_paths):
        raise RuntimeError("--snk-run requires an explicit active-universe overlay")
    if args.local_only and args.r2_bucket:
        raise RuntimeError("--local-only cannot be combined with an R2 bucket")
    remote_publish = requires_remote_publish(
        backend_only=args.backend_only,
        bootstrap_only=args.bootstrap_only,
        local_only=args.local_only,
    )
    if remote_publish and not args.r2_bucket:
        raise RuntimeError("remote daily publish requires CARDZ_STAGING_R2_BUCKET or --r2-bucket")
    if remote_publish and not os.environ.get("CARDZ_GENERATION_CANARY_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_GENERATION_CANARY_COMMAND_JSON")
    if remote_publish and not os.environ.get("CARDZ_POINTER_PROMOTE_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_POINTER_PROMOTE_COMMAND_JSON")
    if remote_publish and args.required_presentation_view is None:
        args.required_presentation_view = "top300_boards"
    if args.allow_stale_demo and (args.mode != "staging" or not args.local_only):
        raise RuntimeError("stale data is permitted only for an explicit local staging preview")
    if args.skip_database_sync and (args.mode != "staging" or not args.local_only):
        raise RuntimeError("database sync can be skipped only for an explicit local staging run")
    source_root = args.source_root.resolve()

    with singleton_lock(args.lock.resolve()):
        fx_cache = args.fx_cache.resolve()
        fx_reused = False
        if args.skip_fx_refresh:
            if not fx_cache.is_file():
                raise RuntimeError(f"FX refresh was skipped but no last-good snapshot exists: {fx_cache}")
            fx_snapshot = validate_fx_snapshot(read_json(fx_cache))
        else:
            try:
                fx_snapshot, fx_reused = collect_or_reuse_fx(
                    fx_cache,
                    endpoint=args.fx_endpoint,
                    timeout_seconds=args.fx_timeout_seconds,
                    retries=args.fx_retries,
                    last_good_hours=args.fx_last_good_hours,
                )
            except Exception:
                if not args.allow_stale_demo:
                    raise
                fx_snapshot = None

        landing_root = args.landing_root.resolve()
        bootstrap_exists = bootstrap_history_exists(landing_root)
        if not bootstrap_exists and not args.refresh_bootstrap_source:
            raise RuntimeError(
                "private bootstrap history is missing; restore an active-only bootstrap archive "
                "or explicitly run --refresh-bootstrap-source"
            )

        ingest_at: datetime | None = None
        price_effective_at: datetime | None = None
        card_count: int | None = None
        source_required = not args.backend_only or args.refresh_bootstrap_source or args.refresh_active_universe
        if source_required and not source_root.is_dir():
            raise RuntimeError(f"source data root does not exist: {source_root}")
        if args.refresh_bootstrap_source:
            run_private_acquisition(
                args.private_acquire_script,
                source_root,
                args.acquire_timeout_seconds,
                args.expected_card_count,
            )
        if args.refresh_bootstrap_source or args.refresh_active_universe or not args.backend_only:
            try:
                ingest_at, price_effective_at, card_count = validate_source(
                    source_root, args.expected_card_count, args.source_max_age_hours
                )
            except StaleSourceError:
                if not args.allow_stale_demo:
                    raise
                ingest_at = load_ingest_at(source_root)
                price_effective_at = load_price_effective_at(source_root)
                card_count = len(candidate_rows(source_root))
                if card_count != args.expected_card_count:
                    raise

        if (
            args.skip_market_source_refresh
            and price_effective_at is not None
            and datetime.now(timezone.utc) - price_effective_at > timedelta(hours=args.price_max_age_hours)
            and not args.allow_stale_demo
        ):
            age_hours = (datetime.now(timezone.utc) - price_effective_at).total_seconds() / 3600
            raise RuntimeError(
                f"price reference is unavailable ({age_hours:.1f}h > {args.price_max_age_hours:g}h); latest pointer preserved"
            )

        if args.refresh_bootstrap_source or not bootstrap_exists:
            if ingest_at is None or price_effective_at is None:
                raise RuntimeError("bootstrap refresh requires a validated discovery source")
            mode = landing_mode(landing_root)
            manifest_path, landing_manifest, replayed = freeze_landing(
                source_root,
                landing_root,
                mode,
                ingest_at,
                archive_payload=True,
                observation_effective_at=price_effective_at,
            )
            write_canonical_batch(manifest_path, source_root, price_effective_at, landing_manifest)
            bootstrap_run_id = str(landing_manifest["runId"])
            changed_files = int(landing_manifest["changedFileCount"])
        else:
            mode = "reused-bootstrap"
            replayed = True
            bootstrap_run_id = None
            changed_files = 0
        if args.bootstrap_only:
            print(
                json.dumps(
                    {
                        "status": "bootstrap-ready",
                        "mode": args.mode,
                        "bootstrapRunId": bootstrap_run_id,
                        "landingMode": mode,
                        "landingReplayed": replayed,
                        "changedFiles": changed_files,
                        "cardCount": card_count,
                        "ingestAt": iso_utc(ingest_at),
                        "priceEffectiveAt": iso_utc(price_effective_at),
                    },
                    sort_keys=True,
                )
            )
            return 0
        attempt_started_at = datetime.now(timezone.utc)
        pipeline_run_id = attempt_started_at.strftime("daily_%Y%m%dT%H%M%S%fZ")
        stage_receipt_root = (
            landing_root / "daily-attempts" / pipeline_run_id / "stages"
        )
        evaluation_receipt = (
            landing_root / "daily-attempts" / pipeline_run_id / "market-alert-evaluation.json"
        )
        # Bind the attempt to one shared-engine input fingerprint so retry
        # resume and same-input no-op can be proven from receipts alone.
        engine_material = build_engine_input_material(
            profile_id=run_profile,
            active_universe=args.active_universe.resolve() if args.active_universe else None,
            mode=args.mode,
            backend_only=bool(args.backend_only),
        )
        engine_fingerprint = compute_input_fingerprint(engine_material)
        write_input_fingerprint(
            stage_receipt_root,
            run_id=pipeline_run_id,
            profile_id=run_profile,
            fingerprint=engine_fingerprint,
            material=engine_material,
        )

        source_refresh = {"gemrateLive": False, "snkRun": None, "tagRun": None, "tagStatus": "unavailable", "tagCards": None, "crosswalk": None, "activeUniverse": None, "activeUniverseLockId": None, "activeCards": None}
        if not args.skip_market_source_refresh:
            # The parent run ID is shared across acquisition, DB, derive, QC,
            # and publication. Provider receipts remain nested evidence.
            market_run_id = pipeline_run_id
            source_refresh = run_market_source_refresh(
                source_root,
                landing_root,
                fx_cache,
                market_run_id,
                attempt_started_at,
                args.pipeline_timeout_seconds,
                args.require_gemrate_refresh,
                args.refresh_active_universe,
                args.active_universe.resolve() if args.active_universe else None,
                args.gemrate_ids.resolve() if args.gemrate_ids else None,
                args.snk_ids.resolve() if args.snk_ids else None,
                args.snk_run.resolve() if args.snk_run else None,
            )

        active_path = (
            Path(str(source_refresh["activeUniverse"])).resolve()
            if source_refresh.get("activeUniverse")
            else (args.active_universe.resolve() if args.active_universe else DEFAULT_ACTIVE_UNIVERSE.resolve())
        )
        acquired_outputs = [
            Path(str(value))
            for value in (
                source_refresh.get("snkRun"),
                source_refresh.get("tagRun"),
                source_refresh.get("crosswalk"),
                active_path,
            )
            if value
        ]
        write_stage_receipt(
            stage_receipt_root,
            run_id=pipeline_run_id,
            stage="ACQUIRED",
            inputs=[fx_cache],
            outputs=acquired_outputs,
            counts={
                "activeCards": int(source_refresh.get("activeCards") or 0),
                "changedFiles": int(changed_files),
            },
        )
        write_stage_receipt(
            stage_receipt_root,
            run_id=pipeline_run_id,
            stage="VERIFIED",
            inputs=acquired_outputs,
            outputs=[active_path],
            counts={"activeCards": int(source_refresh.get("activeCards") or 0)},
        )
        database_synced = False
        alert_evaluated = False
        post_derive_audited = False
        evaluation_id: int | None = None
        evaluation_passed = False
        if not args.skip_database_sync:
            backend = str(ROOT / "scripts/backend.py")
            run_checked(
                backend_database_command(Path(backend), "import", active_path),
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
            # FX 快取上面已經收咗，但 `canonical_public_snapshot.currency_block()` 係讀
            # DB 表，唔係讀嗰個檔。冇呢一步，六隻非 USD 貨幣喺出街 snapshot 永遠係
            # unavailable，前端一撳就成版錢銀空白。
            if fx_snapshot is not None:
                run_checked(
                    [sys.executable, str(ROOT / "pipelines/fx_db_load.py"), "--snapshot", str(fx_cache)],
                    cwd=ROOT,
                    timeout=args.pipeline_timeout_seconds,
                    env=os.environ,
                )
            run_checked(
                market_alert_evaluation_command(evaluation_receipt),
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
            evaluation_id = load_evaluation_id(evaluation_receipt)
            alert_evaluated = True
            run_checked(
                backend_database_command(Path(backend), "status", active_path),
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
            database_synced = True
            snk_run = source_refresh.get("snkRun")
            if snk_run:
                run_checked(
                    post_derive_audit_command(
                        Path(str(snk_run)),
                        required_presentation_view=args.required_presentation_view,
                        active_universe=active_path,
                    ),
                    cwd=ROOT,
                    timeout=args.pipeline_timeout_seconds,
                    env=os.environ,
                )
                post_derive_audited = True
                run_checked(
                    mark_market_evaluation_passed_command(evaluation_id),
                    cwd=ROOT,
                    timeout=args.pipeline_timeout_seconds,
                    env=os.environ,
                )
                evaluation_passed = True
            elif not args.backend_only:
                raise RuntimeError("snapshot export requires a current SNK PSA 10 audit")

        if database_synced:
            write_stage_receipt(
                stage_receipt_root,
                run_id=pipeline_run_id,
                stage="INGESTED",
                inputs=[active_path],
                counts={"activeCards": int(source_refresh.get("activeCards") or 0)},
            )
            write_stage_receipt(
                stage_receipt_root,
                run_id=pipeline_run_id,
                stage="DERIVED",
                inputs=[active_path],
                outputs=[evaluation_receipt],
                counts={"evaluationId": int(evaluation_id or 0)},
            )

        if args.backend_only:
            active_document = read_json(active_path)
            print(
                json.dumps(
                    {
                        "status": "backend-ready",
                        "mode": args.mode,
                        "runId": pipeline_run_id,
                        "bootstrapRunId": bootstrap_run_id,
                        "landingMode": mode,
                        "landingReplayed": replayed,
                        "changedFiles": changed_files,
                        "fxFetchedAt": fx_snapshot.get("fetchedAt") if fx_snapshot else None,
                        "fxLastGoodReused": fx_reused,
                        "gemrateLive": source_refresh["gemrateLive"],
                        "snkLive": bool(source_refresh["snkRun"]),
                        "tagLive": bool(source_refresh["tagRun"]),
                        "tagStatus": source_refresh["tagStatus"],
                        "tagCards": source_refresh["tagCards"],
                        "activeCards": active_document.get("counts", {}).get("uniqueCards"),
                        "activeUniverseLockId": active_universe_lock_hash(active_document),
                        "databaseSynced": database_synced,
                        "alertEvaluated": alert_evaluated,
                        "evaluationId": evaluation_id,
                        "evaluationPassed": evaluation_passed,
                        "postDeriveAudited": post_derive_audited,
                    },
                    sort_keys=True,
                )
            )
            return 0

        candidate_root = ROOT / "data/runtime/candidates" / pipeline_run_id
        raw_candidate_snapshot = candidate_root / "snapshot.candidate.json"
        candidate_snapshot = candidate_root / "snapshot.json"
        candidate_image_manifest = candidate_root / "image-qc.json"
        candidate_qc_audit = candidate_root / "public-qc-audit.json"
        candidate_qc_receipt = candidate_root / "public-qc-receipt.json"
        private_mapping_path = candidate_root / "editorial-top100-mapping.json"
        private_gap_path = candidate_root / "public-gate-gap.json"
        candidate_root.mkdir(parents=True, exist_ok=True)

        if not database_synced:
            raise RuntimeError("public candidate requires a successful canonical database sync")
        canonical_db_qc_root = (
            ROOT / "data/runtime/private-reports/canonical-db-qc" / pipeline_run_id
        )
        canonical_db_qc_report = canonical_db_qc_root / "report.json"
        canonical_db_qc_receipt = canonical_db_qc_root / "receipt.json"
        price_sales_gate_receipt = canonical_db_qc_root / "price-sales-gate.json"
        # This command uses a consistent read-only MySQL snapshot and applies
        # the named release profile.  Strict remains all-pool fail-closed;
        # relaxed launch is card-scoped and only global integrity blockers stop
        # the cohort.
        canonical_db_qc_exit = run_checked(
            canonical_db_qc_command(pipeline_run_id, args.release_profile),
            cwd=ROOT,
            timeout=args.pipeline_timeout_seconds,
            env=os.environ,
            allowed_returncodes=(1,),
        )
        if not canonical_db_qc_report.is_file() or not canonical_db_qc_receipt.is_file():
            raise RuntimeError("canonical DB QC did not produce immutable report and receipt")
        canonical_qc_document = read_json(canonical_db_qc_report)
        canonical_qc_universe = canonical_qc_document.get("universe")
        if not isinstance(canonical_qc_universe, Mapping):
            raise RuntimeError("canonical DB QC report has no universe contract")
        canonical_qc_candidate_sha256 = str(
            canonical_qc_universe.get("candidateSha256") or ""
        )
        # The legacy market-alerts dry-run has a fixed ten-sale predicate.  It
        # is useful strict-audit evidence, but must not silently re-tighten the
        # relaxed profile after canonical DB QC has accepted its five-sale
        # card-scoped cohort.
        if args.release_profile == "strict-v1":
            run_checked(
                price_sales_gate_evaluation_command(
                    canonical_db_qc_report,
                    canonical_qc_candidate_sha256,
                    price_sales_gate_receipt,
                ),
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
        for command in canonical_db_qc_failure_commands(
            canonical_db_qc_report,
            pipeline_run_id,
        ):
            run_checked(
                command,
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
        if canonical_db_qc_exit == 1:
            raise RuntimeError(
                "canonical DB QC blocked; retry worklists exported and public pointer unchanged"
            )
        published_snapshot = runtime_snapshot_from_pointer(args.publish_out.resolve())
        presentation_snapshot = published_snapshot or args.snapshot.resolve()
        export_command = [
            sys.executable,
            str(CANONICAL_SNAPSHOT_PATH),
            "--presentation",
            str(presentation_snapshot),
            "--output",
            str(raw_candidate_snapshot),
            "--view",
            str(args.required_presentation_view or "all_eligible"),
            "--release-profile",
            args.release_profile,
            "--db-qc-report",
            str(canonical_db_qc_report),
        ]
        run_checked(export_command, cwd=ROOT, timeout=args.pipeline_timeout_seconds)
        if not args.image_manifest.resolve().is_file():
            raise RuntimeError("canonical publish requires the checked image QC manifest")
        # The source QC file is evidence.  Every candidate owns an exact copy;
        # self-healing may append metadata-only records to that copy but must
        # never overwrite the canonical manifest before the candidate passes.
        shutil.copy2(args.image_manifest.resolve(), candidate_image_manifest)
        # 卡圖自愈（2026-07-25 用戶規矩）：新入列嘅卡即日統一成梵高標準
        # 429x600 透明畫布 + RGBA 原生圓角，就地用現有 asset 修，唔重下載。
        run_checked(
            [
                sys.executable,
                "-X",
                "utf8",
                str(ROOT / "pipelines" / "ensure_std_card_images.py"),
                str(raw_candidate_snapshot),
                "--write",
                "--manifest",
                str(candidate_image_manifest),
                "--pointer",
                str(args.publish_out.resolve() / "latest.json"),
            ],
            cwd=ROOT,
            timeout=args.pipeline_timeout_seconds,
        )
        # Audit writes its immutable failed-candidate evidence before returning
        # non-zero.  With no human/vision-confirmed card (or no 30d PSA10 sale)
        # this stops here and the last-good pointer remains byte-identical.
        run_checked(
            [
                sys.executable,
                "-X",
                "utf8",
                str(PUBLIC_SNAPSHOT_QC_PATH),
                "audit",
                "--snapshot",
                str(raw_candidate_snapshot),
                "--manifest",
                str(candidate_image_manifest),
                "--assets",
                str(args.assets_out.resolve()),
                "--output",
                str(candidate_qc_audit),
                "--run-id",
                pipeline_run_id,
                "--db-qc-receipt",
                str(canonical_db_qc_receipt),
                "--db-qc-report",
                str(canonical_db_qc_report),
                "--release-profile",
                args.release_profile,
            ],
            cwd=ROOT,
            timeout=args.pipeline_timeout_seconds,
        )
        run_checked(
            [
                sys.executable,
                "-X",
                "utf8",
                str(PUBLIC_SNAPSHOT_QC_PATH),
                "finalize",
                "--snapshot",
                str(raw_candidate_snapshot),
                "--audit",
                str(candidate_qc_audit),
                "--output",
                str(candidate_snapshot),
                "--receipt",
                str(candidate_qc_receipt),
                "--assets",
                str(args.assets_out.resolve()),
                "--db-qc-receipt",
                str(canonical_db_qc_receipt),
                "--release-profile",
                args.release_profile,
            ],
            cwd=ROOT,
            timeout=args.pipeline_timeout_seconds,
        )
        qc_audit_document = read_json(candidate_qc_audit)
        rejection_counts = qc_audit_document.get("rejectionCounts")
        write_stage_receipt(
            stage_receipt_root,
            run_id=pipeline_run_id,
            stage="QC_PASSED",
            inputs=[
                canonical_db_qc_receipt,
                raw_candidate_snapshot,
                candidate_image_manifest,
            ],
            outputs=[candidate_qc_audit, candidate_qc_receipt, candidate_snapshot],
            counts={
                "candidateCards": int(qc_audit_document.get("candidateCount") or 0),
                "verifiedTopCount": int(qc_audit_document.get("verifiedCount") or 0),
            },
            rejection_reasons=(
                {
                    str(key): int(value)
                    for key, value in rejection_counts.items()
                    if isinstance(value, int)
                }
                if isinstance(rejection_counts, Mapping)
                else {}
            ),
        )
        write_json(private_mapping_path, {"schemaVersion": 1, "top100": []})
        write_json(private_gap_path, {"schemaVersion": 1, "source": "canonical-db", "gaps": []})

        npm = "npm.cmd" if os.name == "nt" else "npm"
        run_checked(
            [npm, "run", "build", "--workspace", "@cardz/market-data"],
            cwd=ROOT,
            timeout=args.pipeline_timeout_seconds,
        )
        publish = [
            "node",
            "pipelines/publish-snapshot.mjs",
            "--snapshot",
            str(candidate_snapshot),
            "--out",
            str(args.publish_out.resolve()),
            "--assets-root",
            str(args.assets_out.resolve()),
            "--qc-receipt",
            str(candidate_qc_receipt),
            "--db-qc-receipt",
            str(canonical_db_qc_receipt),
        ]
        if args.r2_bucket:
            publish.extend(["--r2-bucket", args.r2_bucket])
        quarantined = finalize_local_candidate_then_publish(
            candidate_snapshot,
            candidate_image_manifest,
            args.assets_out.resolve(),
            published_snapshot,
            publish,
            # A staging canary uses the same strict generation contract as
            # production.  Only its destination differs.
            production=True,
            timeout=args.pipeline_timeout_seconds,
            max_catalog_shrink_pct=args.max_catalog_shrink_pct,
        )
        write_stage_receipt(
            stage_receipt_root,
            run_id=pipeline_run_id,
            stage="PUBLISHED",
            inputs=[candidate_snapshot, candidate_qc_receipt, canonical_db_qc_receipt],
            outputs=[args.publish_out.resolve() / "latest.json"],
            counts={
                "publishedCards": published_card_count(read_json(candidate_snapshot)),
            },
        )
        print(
            json.dumps(
                {
                    "status": "published",
                    "mode": args.mode,
                    "runId": pipeline_run_id,
                    "bootstrapRunId": bootstrap_run_id,
                    "landingMode": mode,
                    "landingReplayed": replayed,
                    "changedFiles": changed_files,
                    "cardCount": card_count,
                    "ingestAt": iso_utc(ingest_at),
                    "priceEffectiveAt": iso_utc(price_effective_at),
                    "fxFetchedAt": fx_snapshot.get("fetchedAt") if fx_snapshot else None,
                    "fxLastGoodReused": fx_reused,
                    "gemrateLive": source_refresh["gemrateLive"],
                    "snkLive": bool(source_refresh["snkRun"]),
                    "tagLive": bool(source_refresh["tagRun"]),
                    "tagCards": source_refresh["tagCards"],
                    "activeCards": source_refresh["activeCards"],
                    "activeUniverseLockId": source_refresh["activeUniverseLockId"],
                    "databaseSynced": database_synced,
                    "alertEvaluated": alert_evaluated,
                    "evaluationId": evaluation_id,
                    "evaluationPassed": evaluation_passed,
                    "postDeriveAudited": post_derive_audited,
                    "remotePublished": bool(args.r2_bucket),
                    "quarantinedAssets": quarantined,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
    except Exception as error:
        record_failure(
            source="daily",
            stage="orchestrate",
            script=__file__,
            item_key="shared-engine",
            reason_code="pipeline_aborted",
            message="daily pipeline aborted before successful completion",
            retryable=True,
            next_action="agent_review_then_retry",
            error_type=type(error).__name__,
        )
        raise
    record_resolution(
        source="daily",
        stage="orchestrate",
        script=__file__,
        item_key="shared-engine",
        resolution="pipeline_completed",
    )
    raise SystemExit(exit_code)
