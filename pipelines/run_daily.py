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
    quarantine_unreferenced_assets,
    write_json,
)
from db_runtime import active_universe_lock_hash


ROOT = Path(__file__).resolve().parents[1]
GRADE10_INTEGRATION_ROOT = ROOT / "integrations" / "grade10"
DEFAULT_SOURCE_ROOT = GRADE10_INTEGRATION_ROOT / "data"
DEFAULT_ACQUIRE_SCRIPT = GRADE10_INTEGRATION_ROOT / "run_service.py"
DEFAULT_KADO_ROOT = ROOT / "data" / "private" / "kado"
COVERAGE_AUDIT_PATH = ROOT / "pipelines" / "data_coverage_audit.py"
CANONICAL_SNAPSHOT_PATH = ROOT / "pipelines" / "canonical_public_snapshot.py"


class StaleSourceError(RuntimeError):
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


def run_checked(command: list[str], *, cwd: Path, timeout: int, env: Mapping[str, str] | None = None) -> None:
    subprocess.run(command, cwd=cwd, timeout=timeout, check=True, env=dict(env) if env else None)


def gemrate_daily_command(gemrate_ids: Path, active_universe: Path, source_root: Path) -> list[str]:
    """Build the population-authority refresh command without assuming an API key."""

    return [
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


def post_derive_audit_command(
    snk_run: Path | None,
    *,
    required_presentation_view: str | None = None,
    production: bool | None = None,
) -> list[str]:
    """Audit the current run only, after canonical derive has completed."""

    if snk_run is None:
        raise RuntimeError("post-derive audit requires a current SNK PSA 10 run")
    command = [sys.executable, str(COVERAGE_AUDIT_PATH), "--snk-run", str(snk_run.resolve())]
    if required_presentation_view is None and production:
        required_presentation_view = "top300"
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
    run_checked(
        gemrate_daily_command(gemrate_ids, active_universe, source_root),
        cwd=ROOT,
        timeout=timeout,
        env=os.environ,
    )
    gemrate_live = True

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
    if os.environ.get("CARDZ_EBAY_SOLD_ENABLED", "").casefold() == "true":
        try:
            run_checked(
                [
                    sys.executable,
                    str(ROOT / "pipelines/ebay_sold_data.py"),
                    "--active-universe", str(active_universe),
                    "--out", str(ebay_run),
                ],
                cwd=ROOT,
                timeout=timeout,
                env=os.environ,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
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


def promote_file(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".next")
    shutil.copy2(source, temporary)
    os.replace(temporary, destination)


def finalize_local_candidate_then_publish(
    candidate_snapshot: Path,
    candidate_image_manifest: Path,
    assets_out: Path,
    snapshot_destination: Path,
    image_manifest_destination: Path,
    quarantine_root: Path,
    publish_command: list[str],
    production: bool,
    timeout: int,
) -> int:
    """Finish every fallible local gate before invoking remote promotion."""

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
    run_checked(verify_images, cwd=ROOT, timeout=timeout)

    # The snapshot is the local pointer, so its companion QC evidence is
    # promoted first. Remote candidate/canary/latest operations are strictly
    # later than every local gate and quarantine step.
    promote_file(candidate_image_manifest, image_manifest_destination)
    promote_file(candidate_snapshot, snapshot_destination)
    snapshot_document = read_json(candidate_snapshot)
    quarantined = quarantine_unreferenced_assets(assets_out, snapshot_document, quarantine_root)
    run_checked(publish_command, cwd=ROOT, timeout=timeout)
    return quarantined


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standalone CARDZ daily market-data pipeline")
    parser.add_argument("--mode", choices=("staging", "production"), required=True)
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
        choices=("top100", "top300", "top350", "top100_plus_200", "reserve50"),
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
    parser.add_argument("--assets-out", type=Path, default=ROOT / "data/public/market-assets")
    parser.add_argument("--image-manifest", type=Path, default=ROOT / "manifests/image-qc.json")
    parser.add_argument("--publish-out", type=Path, default=ROOT / "data/runtime/publish-staging")
    parser.add_argument("--r2-bucket")
    parser.add_argument("--lock", type=Path, default=ROOT / "data/runtime/locks/daily.lock")
    args = parser.parse_args()

    if not args.r2_bucket:
        env_name = "CARDZ_PRODUCTION_R2_BUCKET" if args.mode == "production" else "CARDZ_STAGING_R2_BUCKET"
        args.r2_bucket = os.environ.get(env_name)

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
    if not args.backend_only and not args.local_only and not args.r2_bucket:
        raise RuntimeError("remote daily publish requires CARDZ_STAGING_R2_BUCKET or --r2-bucket")
    if not args.backend_only and not args.local_only and not os.environ.get("CARDZ_GENERATION_CANARY_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_GENERATION_CANARY_COMMAND_JSON")
    if not args.backend_only and not args.local_only and not os.environ.get("CARDZ_POINTER_PROMOTE_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_POINTER_PROMOTE_COMMAND_JSON")
    if not args.backend_only and args.required_presentation_view is None:
        args.required_presentation_view = "top300"
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
        pipeline_run_id = datetime.now(timezone.utc).strftime("daily_%Y%m%dT%H%M%S%fZ")

        source_refresh = {"gemrateLive": False, "snkRun": None, "tagRun": None, "tagStatus": "unavailable", "tagCards": None, "crosswalk": None, "activeUniverse": None, "activeUniverseLockId": None, "activeCards": None}
        if not args.skip_market_source_refresh:
            market_run_id = datetime.now(timezone.utc).strftime("sources_%Y%m%d")
            source_refresh = run_market_source_refresh(
                source_root,
                landing_root,
                fx_cache,
                market_run_id,
                args.pipeline_timeout_seconds,
                args.require_gemrate_refresh,
                args.refresh_active_universe,
                args.active_universe.resolve() if args.active_universe else None,
                args.gemrate_ids.resolve() if args.gemrate_ids else None,
                args.snk_ids.resolve() if args.snk_ids else None,
                args.snk_run.resolve() if args.snk_run else None,
            )

        database_synced = False
        alert_evaluated = False
        post_derive_audited = False
        if not args.skip_database_sync:
            backend = str(ROOT / "scripts/backend.py")
            run_checked(
                [sys.executable, backend, "import"],
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
            run_checked(
                [sys.executable, str(ROOT / "pipelines/market_alerts.py")],
                cwd=ROOT,
                timeout=args.pipeline_timeout_seconds,
                env=os.environ,
            )
            alert_evaluated = True
            run_checked(
                [sys.executable, backend, "status"],
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
                    ),
                    cwd=ROOT,
                    timeout=args.pipeline_timeout_seconds,
                    env=os.environ,
                )
                post_derive_audited = True
            elif not args.backend_only:
                raise RuntimeError("snapshot export requires a current SNK PSA 10 audit")

        if args.backend_only:
            active_path = ROOT / "data/runtime/private-source-map/tracked-universe.json"
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
                        "postDeriveAudited": post_derive_audited,
                    },
                    sort_keys=True,
                )
            )
            return 0

        candidate_root = ROOT / "data/runtime/candidates" / pipeline_run_id
        candidate_snapshot = candidate_root / "snapshot.json"
        candidate_image_manifest = candidate_root / "image-qc.json"
        private_mapping_path = candidate_root / "editorial-top100-mapping.json"
        private_gap_path = candidate_root / "public-gate-gap.json"
        candidate_root.mkdir(parents=True, exist_ok=True)

        if not database_synced:
            raise RuntimeError("public candidate requires a successful canonical database sync")
        export_command = [
            sys.executable,
            str(CANONICAL_SNAPSHOT_PATH),
            "--presentation",
            str(args.snapshot.resolve()),
            "--output",
            str(candidate_snapshot),
            "--view",
            str(args.required_presentation_view or "top300"),
        ]
        if args.mode == "production":
            export_command.append("--production")
        run_checked(export_command, cwd=ROOT, timeout=args.pipeline_timeout_seconds)
        if not args.image_manifest.resolve().is_file():
            raise RuntimeError("canonical publish requires the checked image QC manifest")
        shutil.copy2(args.image_manifest.resolve(), candidate_image_manifest)
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
            "--image-manifest",
            str(candidate_image_manifest),
        ]
        if args.mode == "staging":
            publish.append("--allow-demo")
        if args.r2_bucket:
            publish.extend(["--r2-bucket", args.r2_bucket])
        quarantine_root = ROOT / "data/runtime/private-quarantine/legacy-public-assets" / pipeline_run_id
        quarantined = finalize_local_candidate_then_publish(
            candidate_snapshot,
            candidate_image_manifest,
            args.assets_out.resolve(),
            args.snapshot.resolve(),
            args.image_manifest.resolve(),
            quarantine_root,
            publish,
            production=args.mode == "production",
            timeout=args.pipeline_timeout_seconds,
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
                    "postDeriveAudited": post_derive_audited,
                    "remotePublished": bool(args.r2_bucket),
                    "quarantinedAssets": quarantined,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
