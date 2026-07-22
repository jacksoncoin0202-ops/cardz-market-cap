#!/usr/bin/env python3
"""Fail-closed CARDZ daily G10 intake and snapshot publisher.

Staging may derive a sanitized candidate directly from the immutable private
landing. Production requires the separately owned JLP MySQL runner; this file
never creates or treats SQLite as a production authority.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from g10_ingest import (
    build_daily_observations,
    freeze_landing,
    iso_utc,
    parse_effective_at,
    read_json,
)
from g10_public_snapshot import (
    build_snapshot,
    candidate_rows,
    load_ingest_at,
    load_price_effective_at,
    quarantine_unreferenced_assets,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_ROOT = ROOT.parent / "grade10-scraper" / "data"
DEFAULT_KADO_ROOT = ROOT.parent / "kado-dump"


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


def run_private_acquisition(script: Path, source_root: Path, timeout: int) -> None:
    resolved = script.resolve()
    source_repo = source_root.resolve().parent
    if resolved.parent != source_repo or resolved.name.casefold() != "grade10_scraper.py":
        raise RuntimeError("private acquisition must explicitly select the external grade10_scraper.py producer")
    # Images are part of the daily acquisition contract. The source collector
    # re-fetches them and keeps unchanged bytes in place; a changed image gets
    # a new content hash during raw-front QC and publication.
    run_checked([sys.executable, str(resolved)], cwd=source_repo, timeout=timeout)


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


def landing_mode(landing_root: Path) -> str:
    return "incremental" if any((landing_root / "g10" / "full").glob("*/manifest.json")) else "full"


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


def run_production_authority(
    runner: Path,
    batch_path: Path,
    candidate_snapshot: Path,
    candidate_image_manifest: Path,
    assets_out: Path,
    dsn_env_name: str,
    timeout: int,
) -> None:
    if not os.environ.get(dsn_env_name):
        raise RuntimeError(f"production requires JLP MySQL DSN in environment variable {dsn_env_name}")
    resolved = runner.resolve()
    if not resolved.is_file():
        raise RuntimeError(f"production JLP runner does not exist: {resolved}")
    command = [sys.executable, str(resolved)] if resolved.suffix.casefold() == ".py" else [str(resolved)]
    command.extend(
        [
            "--batch", str(batch_path),
            "--snapshot", str(candidate_snapshot),
            "--image-manifest", str(candidate_image_manifest),
            "--assets-out", str(assets_out),
        ]
    )
    run_checked(command, cwd=resolved.parent, timeout=timeout, env=os.environ)


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
    parser = argparse.ArgumentParser(description="Run the CARDZ G10 full/incremental data pipeline")
    parser.add_argument("--mode", choices=("staging", "production"), required=True)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    parser.add_argument("--kado-root", type=Path, default=DEFAULT_KADO_ROOT)
    parser.add_argument("--private-acquire-script", type=Path)
    parser.add_argument("--skip-source-refresh", action="store_true")
    parser.add_argument("--acquire-timeout-seconds", type=int, default=5_400)
    parser.add_argument("--pipeline-timeout-seconds", type=int, default=7_200)
    parser.add_argument("--expected-card-count", type=int, default=600)
    parser.add_argument("--source-max-age-hours", type=float, default=30)
    parser.add_argument("--price-max-age-hours", type=float, default=48)
    parser.add_argument("--allow-stale-demo", action="store_true")
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--landing-root", type=Path, default=ROOT / "data/runtime/private-landing")
    parser.add_argument("--snapshot", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--assets-out", type=Path, default=ROOT / "data/public/market-assets")
    parser.add_argument("--image-manifest", type=Path, default=ROOT / "manifests/image-qc.json")
    parser.add_argument("--publish-out", type=Path, default=ROOT / "data/runtime/publish-staging")
    parser.add_argument("--r2-bucket")
    parser.add_argument("--production-runner", type=Path)
    parser.add_argument("--mysql-dsn-env", default="CARDZ_JLP_MYSQL_DSN")
    parser.add_argument("--lock", type=Path, default=ROOT / "data/runtime/locks/daily.lock")
    args = parser.parse_args()

    if not args.r2_bucket:
        env_name = "CARDZ_PRODUCTION_R2_BUCKET" if args.mode == "production" else "CARDZ_STAGING_R2_BUCKET"
        args.r2_bucket = os.environ.get(env_name)

    if args.skip_source_refresh and args.private_acquire_script:
        raise RuntimeError("choose either explicit private acquisition or --skip-source-refresh")
    if not args.skip_source_refresh and not args.private_acquire_script:
        raise RuntimeError("private acquisition is not configured; pass --private-acquire-script explicitly")
    if args.local_only and args.r2_bucket:
        raise RuntimeError("--local-only cannot be combined with an R2 bucket")
    if not args.local_only and not args.r2_bucket:
        raise RuntimeError("remote daily publish requires CARDZ_STAGING_R2_BUCKET or --r2-bucket")
    if not args.local_only and not os.environ.get("CARDZ_GENERATION_CANARY_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_GENERATION_CANARY_COMMAND_JSON")
    if not args.local_only and not os.environ.get("CARDZ_POINTER_PROMOTE_COMMAND_JSON"):
        raise RuntimeError("remote daily publish requires CARDZ_POINTER_PROMOTE_COMMAND_JSON")
    if args.allow_stale_demo and (args.mode != "staging" or not args.local_only):
        raise RuntimeError("stale data is permitted only for an explicit local staging preview")
    if args.mode == "production" and (args.production_runner is None or args.local_only):
        raise RuntimeError("production requires the JLP MySQL runner and remote R2 publish")

    source_root = args.source_root.resolve()
    if not source_root.is_dir():
        raise RuntimeError(f"source data root does not exist: {source_root}")

    with singleton_lock(args.lock.resolve()):
        if not args.skip_source_refresh:
            run_private_acquisition(args.private_acquire_script, source_root, args.acquire_timeout_seconds)
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

        price_age = datetime.now(timezone.utc) - price_effective_at
        if price_age > timedelta(hours=args.price_max_age_hours) and not args.allow_stale_demo:
            raise RuntimeError(
                f"price reference is unavailable ({price_age.total_seconds() / 3600:.1f}h > {args.price_max_age_hours:g}h); latest pointer preserved"
            )

        landing_root = args.landing_root.resolve()
        mode = landing_mode(landing_root)
        manifest_path, landing_manifest, replayed = freeze_landing(
            source_root,
            landing_root,
            mode,
            ingest_at,
            archive_payload=True,
            observation_effective_at=price_effective_at,
        )
        batch_path = write_canonical_batch(manifest_path, source_root, price_effective_at, landing_manifest)
        candidate_root = ROOT / "data/runtime/candidates" / str(landing_manifest["runId"])
        candidate_snapshot = candidate_root / "snapshot.json"
        candidate_image_manifest = candidate_root / "image-qc.json"
        private_mapping_path = candidate_root / "editorial-top100-mapping.json"
        private_gap_path = candidate_root / "public-gate-gap.json"
        candidate_root.mkdir(parents=True, exist_ok=True)

        if args.mode == "production":
            run_production_authority(
                args.production_runner,
                batch_path,
                candidate_snapshot,
                candidate_image_manifest,
                args.assets_out.resolve(),
                args.mysql_dsn_env,
                args.pipeline_timeout_seconds,
            )
            if not candidate_snapshot.is_file():
                raise RuntimeError("JLP production runner did not emit the canonical public snapshot")
            if not candidate_image_manifest.is_file():
                raise RuntimeError("JLP production runner did not emit the image QC manifest")
        else:
            private_mapping: list[dict[str, Any]] = []
            private_gap_report: dict[str, Any] = {}
            snapshot, image_manifest, _ = build_snapshot(
                source_root,
                args.kado_root.resolve(),
                args.assets_out.resolve(),
                landing_root=landing_root,
                private_mapping=private_mapping,
                private_gap_report=private_gap_report,
            )
            write_json(candidate_snapshot, snapshot)
            write_json(candidate_image_manifest, image_manifest)
            write_json(private_mapping_path, {"schemaVersion": 1, "top100": private_mapping[:100]})
            write_json(private_gap_path, private_gap_report)

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
        quarantine_root = ROOT / "data/runtime/private-quarantine/legacy-public-assets" / str(landing_manifest["runId"])
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
                    "runId": landing_manifest["runId"],
                    "landingMode": mode,
                    "landingReplayed": replayed,
                    "changedFiles": landing_manifest["changedFileCount"],
                    "cardCount": card_count,
                    "ingestAt": iso_utc(ingest_at),
                    "priceEffectiveAt": iso_utc(price_effective_at),
                    "remotePublished": bool(args.r2_bucket),
                    "quarantinedAssets": quarantined,
                },
                sort_keys=True,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
