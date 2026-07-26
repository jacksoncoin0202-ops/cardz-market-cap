#!/usr/bin/env python3
"""Cross-platform CARDZ backend bootstrap and replay entrypoint.

The script uses only the Python standard library until the project virtual
environment is ready. It runs unchanged on Windows and Linux, can use Docker
Compose for the bundled MySQL service, or connect directly to a managed MySQL
database such as Amazon RDS with ``--external-db``.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, Mapping, Sequence


if sys.version_info < (3, 10):
    print("CARDZ backend requires Python 3.10 or newer", file=sys.stderr)
    raise SystemExit(1)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "runtime" / "config" / "backend.env"
SECRETS_PATH = ROOT / "data" / "runtime" / "config" / "gemrate.env"
COMPOSE_PATH = ROOT / "compose.backend.yaml"
REQUIREMENTS_PATH = ROOT / "pipelines" / "requirements.txt"
DB_RUNTIME_PATH = ROOT / "pipelines" / "db_runtime.py"
ALERT_RUNTIME_PATH = ROOT / "pipelines" / "market_alerts.py"
DISCOVERY_RUNTIME_PATH = ROOT / "pipelines" / "discovery_refresh.py"
COVERAGE_AUDIT_PATH = ROOT / "pipelines" / "data_coverage_audit.py"
DATA_ROUTING_PATH = ROOT / "pipelines" / "data_routing.py"
DAILY_RUNTIME_PATH = ROOT / "pipelines" / "run_daily.py"
BOOTSTRAP_ARCHIVE_PATH = ROOT / "scripts" / "bootstrap_archive.py"
CANONICAL_SEED_PATH = ROOT / "scripts" / "canonical_seed.py"
SEED_RESTORE_PATH = ROOT / "scripts" / "seed_restore.py"
CANONICAL_PUBLIC_SNAPSHOT_PATH = ROOT / "pipelines" / "canonical_public_snapshot.py"
CROSSWALK_PATH = ROOT / "pipelines" / "source_crosswalk.py"
TRACKED_UNIVERSE_PATH = ROOT / "pipelines" / "tracked_universe.py"
CANDIDATE_BACKFILL_PATH = ROOT / "pipelines" / "gemrate_candidate_backfill.py"
SNK_BULK_PATH = ROOT / "pipelines" / "snkrdunk_bulk.py"
FX_RATES_PATH = ROOT / "pipelines" / "fx_rates.py"
FX_SNAPSHOT_PATH = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
VENV_PATH = ROOT / ".venv-backend"
REQUIRED_EXTERNAL = ("CARDZ_DB_HOST", "CARDZ_DB_PORT", "CARDZ_DB_NAME", "CARDZ_DB_USER", "CARDZ_DB_PASSWORD")


def load_data_routing_module():
    """Load the standard-library registry API without creating the backend venv."""

    pipelines_dir = str(DATA_ROUTING_PATH.parent)
    if pipelines_dir not in sys.path:
        sys.path.insert(0, pipelines_dir)
    spec = importlib.util.spec_from_file_location("cardz_data_routing", DATA_ROUTING_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("data-routing control plane cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_or_print(content: str, output: Path | None) -> None:
    if output is None:
        print(content, end="" if content.endswith("\n") else "\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")


def run_registry_action(action: str, args: argparse.Namespace) -> None:
    routing = load_data_routing_module()
    document = routing.load_registry()
    if action == "registry":
        write_or_print(routing.render_registry(document, "json"), args.output)
        return
    if action == "explain":
        if not args.identifier:
            raise RuntimeError("explain requires a metric, field, tool, profile, view or consumer identifier")
        payload = routing.explain_identifier(args.identifier)
        write_or_print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", args.output)
        return
    if action == "graph":
        write_or_print(routing.render_registry(document, args.format), args.output)
        return
    if action == "work-items":
        items = list(document["workItems"])
        if args.status:
            items = [item for item in items if item["status"] == args.status]
        if args.priority:
            items = [item for item in items if item["priority"] == args.priority]
        payload = {
            "architectureEntrypoint": document["architecture"]["entrypoint"],
            "count": len(items),
            "items": items,
        }
        write_or_print(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", args.output)
        return
    if action == "generate-docs":
        output_dir = (args.output or (ROOT / "docs" / "generated")).resolve()
        if args.check:
            drift = routing.check_registry_docs(output_dir=output_dir)
            print(json.dumps({"status": "valid" if not drift else "drift", "drift": drift}, sort_keys=True))
            if drift:
                raise RuntimeError(f"generated registry documentation is stale: {', '.join(drift)}")
            return
        written = routing.generate_registry_docs(output_dir=output_dir)
        print(json.dumps({"status": "generated", "files": [str(path) for path in written]}, sort_keys=True))
        return
    raise RuntimeError(f"unsupported registry action: {action}")


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key and key.replace("_", "").isalnum():
            values[key] = value
    return values


def write_local_config(path: Path, values: Mapping[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(f"{key}={values[key]}" for key in sorted(values)) + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    try:
        temporary.chmod(0o600)
    except OSError:
        pass
    os.replace(temporary, path)


def runtime_config(*, external: bool) -> tuple[dict[str, str], bool]:
    from_file = read_env_file(CONFIG_PATH)
    if external:
        config = {key: os.environ.get(key, "") for key in REQUIRED_EXTERNAL}
        missing = [key for key in REQUIRED_EXTERNAL if not config[key]]
        if missing:
            raise RuntimeError(f"managed database configuration is incomplete: {', '.join(missing)}")
        return config, False

    config = {
        "CARDZ_DB_HOST": "127.0.0.1",
        "CARDZ_DB_PORT": "3308",
        "CARDZ_DB_NAME": "cardz_market_cap",
        "CARDZ_DB_USER": "cardz",
        **from_file,
    }
    for key in (*REQUIRED_EXTERNAL, "CARDZ_DB_ROOT_PASSWORD"):
        if os.environ.get(key):
            config[key] = os.environ[key]
    generated = dict(from_file)
    for key in ("CARDZ_DB_NAME", "CARDZ_DB_USER", "CARDZ_DB_PORT"):
        generated.setdefault(key, config[key])
    for key in ("CARDZ_DB_PASSWORD", "CARDZ_DB_ROOT_PASSWORD"):
        if not config.get(key):
            config[key] = secrets.token_urlsafe(36)
            generated[key] = config[key]
    if not CONFIG_PATH.is_file() or generated != from_file:
        write_local_config(CONFIG_PATH, generated)
    return config, True


def quiet_ok(command: Sequence[str], *, timeout: int = 8) -> bool:
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


class DockerRuntime:
    def __init__(self, *, env_file: bool, environment: Mapping[str, str], wsl_distro: str) -> None:
        self.environment = {**os.environ, **environment}
        self.env_file = env_file
        self.wsl_distro = wsl_distro
        if quiet_ok(["docker", "info"]):
            self.prefix = ["docker"]
            self.use_wsl = False
        elif os.name == "nt" and quiet_ok(["wsl.exe", "-d", wsl_distro, "--", "docker", "info"]):
            self.prefix = ["wsl.exe", "-d", wsl_distro, "--", "docker"]
            self.use_wsl = True
        else:
            raise RuntimeError("Docker is unavailable; use --external-db for a managed MySQL database")

    def docker_path(self, path: Path) -> str:
        resolved = str(path.resolve())
        if not self.use_wsl:
            return resolved
        completed = subprocess.run(
            ["wsl.exe", "-d", self.wsl_distro, "--", "wslpath", "-a", resolved.replace("\\", "/")],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def compose(self, *arguments: str, capture: bool = False) -> str:
        command = [*self.prefix, "compose"]
        if self.env_file:
            command.extend(["--env-file", self.docker_path(CONFIG_PATH)])
        command.extend(["-f", self.docker_path(COMPOSE_PATH), *arguments])
        completed = subprocess.run(
            command,
            check=True,
            env=self.environment,
            capture_output=capture,
            text=capture,
        )
        return completed.stdout.strip() if capture else ""

    def wait_for_database(self, *, timeout: int = 180) -> None:
        container_id = self.compose("ps", "-q", "db", capture=True)
        if not container_id:
            raise RuntimeError("CARDZ database container did not start")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            completed = subprocess.run(
                [*self.prefix, "inspect", "--format", "{{.State.Health.Status}}", container_id],
                check=False,
                capture_output=True,
                text=True,
            )
            if completed.returncode == 0 and completed.stdout.strip() == "healthy":
                return
            time.sleep(3)
        raise RuntimeError("CARDZ database did not become healthy before timeout")


def venv_python() -> Path:
    return VENV_PATH / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_python_environment() -> Path:
    python = venv_python()
    if python.is_file() and not quiet_ok([str(python), "--version"]):
        shutil.rmtree(VENV_PATH)
    if not python.is_file():
        subprocess.run([sys.executable, "-m", "venv", str(VENV_PATH)], check=True)
    expected = hashlib.sha256(REQUIREMENTS_PATH.read_bytes()).hexdigest()
    marker = VENV_PATH / ".requirements.sha256"
    installed = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if installed != expected:
        subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS_PATH)],
            check=True,
        )
        marker.write_text(expected, encoding="utf-8")
    return python


def run_database_tool(python: Path, action: str, environment: Mapping[str, str]) -> None:
    subprocess.run(
        [str(python), "-X", "utf8", str(DB_RUNTIME_PATH), action],
        check=True,
        cwd=ROOT,
        env={**os.environ, **environment},
    )


def run_alert_tool(python: Path, environment: Mapping[str, str]) -> None:
    subprocess.run(
        [str(python), "-X", "utf8", str(ALERT_RUNTIME_PATH)],
        check=True,
        cwd=ROOT,
        env={**os.environ, **environment},
    )


def run_discovery_tool(python: Path) -> None:
    subprocess.run(
        [str(python), "-X", "utf8", str(DISCOVERY_RUNTIME_PATH)],
        check=True,
        cwd=ROOT,
    )


def run_data_routing_tool(python: Path) -> None:
    subprocess.run(
        [str(python), "-X", "utf8", str(DATA_ROUTING_PATH)],
        check=True,
        cwd=ROOT,
    )


def latest_snk_run() -> Path:
    candidates = sorted(
        (ROOT / "data/runtime/private-source-runs").glob("*/snk-psa10.jsonl"),
        key=lambda path: (path.stat().st_mtime_ns, str(path)),
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("no SNK PSA 10 source run is available")
    return candidates[0]


def run_coverage_audit(
    python: Path,
    *,
    snk_run: Path | None,
    required_presentation_view: str | None,
) -> None:
    command = [
        str(python),
        "-X",
        "utf8",
        str(COVERAGE_AUDIT_PATH),
        "--snk-run",
        str((snk_run or latest_snk_run()).resolve()),
    ]
    if required_presentation_view:
        command.extend(["--require-presentation-view", required_presentation_view])
    subprocess.run(command, check=True, cwd=ROOT)


def restore_bootstrap_archive(path: Path, *, overwrite: bool) -> None:
    command = [
        sys.executable,
        "-X",
        "utf8",
        str(BOOTSTRAP_ARCHIVE_PATH),
        "restore",
        "--archive",
        str(path.resolve()),
        "--target",
        str(ROOT),
    ]
    if overwrite:
        command.append("--overwrite")
    subprocess.run(command, check=True, cwd=ROOT)


def validate_external_transport(*, external: bool, mode: str) -> None:
    if external and mode == "production" and not os.environ.get("CARDZ_DB_SSL_CA"):
        raise RuntimeError("production managed database runs require CARDZ_DB_SSL_CA")


def daily_environment(config: Mapping[str, str], *, external: bool, remote_publish: bool = False) -> dict[str, str]:
    environment = {**os.environ, **config, "CARDZ_DB_MODE": "external" if external else "local"}
    # Source credentials live outside backend.env so they never round-trip through
    # write_local_config. Without this the scheduled run starts with no
    # GEMRATE_API_KEY, gemrate_source.py daily reports direct=disabled, and all
    # 1468 tracked cards fall through to the Playwright public-card-page crawl,
    # which cannot finish inside --pipeline-timeout-seconds.
    for key, value in read_env_file(SECRETS_PATH).items():
        environment.setdefault(key, value)
    if not remote_publish:
        # Only an explicit remote publish may resolve a bucket. run_daily.py reads
        # CARDZ_*_R2_BUCKET itself, so an inherited value would silently promote a
        # backend-only run to R2, and would hard-fail --local-only outright.
        environment.pop("CARDZ_STAGING_R2_BUCKET", None)
        environment.pop("CARDZ_PRODUCTION_R2_BUCKET", None)
    return environment


def doctor_report() -> dict[str, object]:
    """Return a read-only local readiness report without loading configuration."""

    scripts = {
        "database": DB_RUNTIME_PATH.is_file(),
        "daily": DAILY_RUNTIME_PATH.is_file(),
        "discovery": DISCOVERY_RUNTIME_PATH.is_file(),
        "routing": DATA_ROUTING_PATH.is_file(),
        "coverageAudit": COVERAGE_AUDIT_PATH.is_file(),
        "bootstrapArchive": BOOTSTRAP_ARCHIVE_PATH.is_file(),
        "runDaily": DAILY_RUNTIME_PATH.is_file(),
    }
    return {
        "action": "doctor",
        "python": sys.version.split()[0],
        "scripts": scripts,
        "configPresent": CONFIG_PATH.is_file(),
        "venvPresent": venv_python().is_file(),
        "ready": all(scripts.values()),
    }


def daily_audit_command(
    python: Path,
    snk_run: Path | None,
    *,
    required_presentation_view: str | None = None,
    production: bool | None = None,
) -> list[str]:
    """Build the post-derive audit command; never substitute an older run."""

    if snk_run is None:
        raise RuntimeError("post-derive audit requires a current SNK PSA 10 run")
    command = [str(python), "-X", "utf8", str(COVERAGE_AUDIT_PATH), "--snk-run", str(snk_run.resolve())]
    if required_presentation_view is None and production:
        required_presentation_view = "top300"
    if required_presentation_view:
        command.extend(["--require-presentation-view", required_presentation_view])
    return command


def full_backfill_bootstrap_command(python: Path, mode: str) -> list[str]:
    """Build the private-only bootstrap phase for a full backfill.

    This deliberately stops before MySQL ingestion.  Candidate population and
    exact SNK mapping must pass their gates before canonical facts can change.
    """

    return [
        str(python),
        "-X",
        "utf8",
        str(DAILY_RUNTIME_PATH),
        "--mode",
        mode,
        "--refresh-bootstrap-source",
        "--bootstrap-only",
    ]


def candidate_backfill_command(
    python: Path,
    *,
    output: Path,
    collect_public: bool,
) -> list[str]:
    """Build a resumable candidate classification command without secrets."""

    command = [
        str(python),
        "-X",
        "utf8",
        str(CANDIDATE_BACKFILL_PATH),
        "--active-universe",
        str(ROOT / "data/runtime/private-source-map/tracked-universe.json"),
        "--out",
        str(output),
        "--resume",
    ]
    if collect_public:
        command.append("--collect-public")
    return command


def load_json_document(path: Path, *, description: str) -> dict[str, object]:
    try:
        document = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise RuntimeError(f"{description} is missing or invalid: {path}") from error
    if not isinstance(document, dict):
        raise RuntimeError(f"{description} is invalid: {path}")
    return document


def require_candidate_backfill_ready(manifest: Mapping[str, object]) -> list[int]:
    """Return exact tracking seeds while separating source failure from gaps.

    A candidate ``review`` or ``unavailable`` row is classified retry evidence,
    not a collector failure. It must not block unrelated validated facts from
    canonical ingest. Only an incomplete source transport blocks this batch.
    """

    if manifest.get("classificationComplete") is not True:
        raise RuntimeError("candidate GemRate classification is incomplete; canonical DB was not changed")
    collection = manifest.get("keylessPublicCollection")
    if isinstance(collection, Mapping) and collection.get("enabled") is True and collection.get("partial") is True:
        raise RuntimeError("candidate GemRate source collection is partial; canonical DB was not changed")
    rows = manifest.get("candidates")
    if not isinstance(rows, list) or not rows:
        raise RuntimeError("candidate GemRate backfill contains no candidates")
    seed_ids: set[int] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        tracking_status = str(row.get("trackingStatus") or "")
        if tracking_status not in {"eligible", "pre_entry_radar"}:
            continue
        if row.get("status") not in {"resolved", "below-threshold"}:
            continue
        if row.get("identityStatus") != "exact_confirmed":
            continue
        snk_item_id = row.get("snkItemId")
        if not isinstance(snk_item_id, int) or isinstance(snk_item_id, bool) or snk_item_id <= 0:
            continue
        seed_ids.add(snk_item_id)
    return sorted(seed_ids)


def snk_refill_command(
    python: Path,
    *,
    candidate_manifest: Path,
    output: Path,
    seed_ids: Sequence[int],
) -> list[str]:
    if not seed_ids:
        raise RuntimeError("SNK exact refill requires at least one exact seed")
    return [
        str(python),
        "-X",
        "utf8",
        str(SNK_BULK_PATH),
        "--price-refill-candidates",
        str(candidate_manifest),
        "--price-refill-out",
        str(output),
        "--price-refill-seeds",
        *(str(item_id) for item_id in seed_ids),
    ]


def snk_history_command(
    python: Path,
    *,
    ids_file: Path,
    output: Path,
    run_id: str,
) -> list[str]:
    return [
        str(python),
        "-X",
        "utf8",
        str(ROOT / "pipelines" / "snk_market_data.py"),
        "--ids-file",
        str(ids_file),
        "--condition",
        "trading_card_single_psa10",
        "--run-id",
        run_id,
        "--out",
        str(output),
    ]


def fx_refresh_command(python: Path, *, output: Path) -> list[str]:
    return [
        str(python),
        "-X",
        "utf8",
        str(FX_RATES_PATH),
        "--output",
        str(output),
    ]


def require_snk_refill_ready(worklist: Mapping[str, object], seed_ids: Iterable[int]) -> list[int]:
    """Return every candidate resolved by the exact SNK worklist.

    ``seed_ids`` only bootstrap the bounded SNK catalogue crawl.  They are not
    the requested candidate set: the whole point of the refill is to discover
    new SNK IDs for GemRate candidates that did not already have one.
    """

    counts = worklist.get("counts")
    if not isinstance(counts, Mapping):
        raise RuntimeError("SNK exact refill worklist has no counts")
    resolved_ids = worklist.get("resolvedItemIds")
    if not isinstance(resolved_ids, list):
        raise RuntimeError("SNK exact refill has no resolved item list")
    if not any(isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0 for item_id in seed_ids):
        raise RuntimeError("SNK exact refill has no valid discovery seed")
    rows = worklist.get("cards")
    if not isinstance(rows, list):
        raise RuntimeError("SNK exact refill has no candidate rows")
    row_ids = {
        row.get("snkItemId")
        for row in rows
        if isinstance(row, Mapping) and row.get("status") == "resolved"
    }
    normalized = {
        item_id
        for item_id in resolved_ids
        if isinstance(item_id, int) and not isinstance(item_id, bool) and item_id > 0
    }
    if normalized != row_ids:
        raise RuntimeError("SNK exact refill resolved IDs do not match candidate rows")
    return sorted(normalized)


def run_full_backfill(
    python: Path,
    *,
    args: argparse.Namespace,
    config: Mapping[str, str],
    external: bool,
) -> None:
    """Run a resumable, fail-closed full-history intake profile.

    Private landing files may be refreshed when a source is incomplete.  The
    only canonical DB mutation occurs in the final daily import after the
    broad GemRate candidate manifest is classified and the complete merged
    universe has a fresh exact SNK collection.
    """

    run_data_routing_tool(python)
    environment = daily_environment(config, external=external, remote_publish=False)
    subprocess.run(
        full_backfill_bootstrap_command(python, args.mode),
        check=True,
        cwd=ROOT,
        env=environment,
    )
    subprocess.run(
        [str(python), "-X", "utf8", str(CROSSWALK_PATH)],
        check=True,
        cwd=ROOT,
    )
    subprocess.run(
        [str(python), "-X", "utf8", str(TRACKED_UNIVERSE_PATH)],
        check=True,
        cwd=ROOT,
    )
    candidate_output = args.full_backfill_candidate_out.resolve()
    subprocess.run(
        candidate_backfill_command(
            python,
            output=candidate_output,
            collect_public=args.collect_public_candidates,
        ),
        check=True,
        cwd=ROOT,
        env=environment,
    )
    candidate_manifest_path = candidate_output / "manifest.json"
    candidate_manifest = load_json_document(candidate_manifest_path, description="candidate GemRate backfill manifest")
    seed_ids = require_candidate_backfill_ready(candidate_manifest)
    snk_refill_output = args.full_backfill_snk_refill_out.resolve()
    candidate_snk_run: Path | None = None
    overlay_universe: Path | None = None
    overlay_gemrate_ids: Path | None = None
    overlay_snk_ids: Path | None = None
    if seed_ids:
        subprocess.run(
            fx_refresh_command(python, output=FX_SNAPSHOT_PATH),
            check=True,
            cwd=ROOT,
            env=environment,
        )
        subprocess.run(
            snk_refill_command(
                python,
                candidate_manifest=candidate_manifest_path,
                output=snk_refill_output,
                seed_ids=seed_ids,
            ),
            check=True,
            cwd=ROOT,
            env=environment,
        )
        snk_worklist = load_json_document(snk_refill_output, description="SNK exact refill worklist")
        resolved_ids = require_snk_refill_ready(snk_worklist, seed_ids)
        if resolved_ids:
            run_token = f"full_backfill_{int(time.time())}"
            run_root = ROOT / "data/runtime/private-source-runs" / run_token
            candidate_ids = run_root / "snk-candidate-ids.txt"
            candidate_ids.parent.mkdir(parents=True, exist_ok=True)
            candidate_ids.write_text("\n".join(str(item_id) for item_id in resolved_ids) + "\n", encoding="ascii")
            candidate_snk_run = run_root / "snk-psa10.jsonl"
            subprocess.run(
                snk_history_command(
                    python,
                    ids_file=candidate_ids,
                    output=candidate_snk_run,
                    run_id=run_token,
                ),
                check=True,
                cwd=ROOT,
                env=environment,
            )
            map_root = ROOT / "data/runtime/private-source-map"
            overlay_universe = map_root / "full-backfill-active-universe.json"
            overlay_gemrate_ids = map_root / "full-backfill-gemrate-ids.txt"
            overlay_snk_ids = map_root / "full-backfill-snk-ids.txt"
            subprocess.run(
                [
                    str(python), "-X", "utf8", str(TRACKED_UNIVERSE_PATH),
                    "--candidate-manifest", str(candidate_manifest_path),
                    "--snk-run", str(candidate_snk_run),
                    "--fx-snapshot", str(FX_SNAPSHOT_PATH),
                    "--out", str(overlay_universe),
                    "--gemrate-ids-out", str(overlay_gemrate_ids),
                    "--snk-ids-out", str(overlay_snk_ids),
                ],
                check=True,
                cwd=ROOT,
                env=environment,
            )

    # The collector and exact-worklist transport must complete, but classified
    # unavailable/review candidates remain private retry evidence.  Resolved
    # rows are merged into an isolated universe before normal canonical ingest.
    run_database_tool(python, "migrate", config)
    daily_command = [str(python), "-X", "utf8", str(DAILY_RUNTIME_PATH), "--mode", args.mode, "--backend-only"]
    if overlay_universe is not None and overlay_gemrate_ids is not None and overlay_snk_ids is not None:
        daily_command.extend(
            [
                "--active-universe", str(overlay_universe),
                "--gemrate-ids", str(overlay_gemrate_ids),
                "--snk-ids", str(overlay_snk_ids),
            ]
        )
        daily_command.append("--skip-fx-refresh")
    if args.require_gemrate_refresh:
        daily_command.append("--require-gemrate-refresh")
    subprocess.run(daily_command, check=True, cwd=ROOT, env=environment)
    run_coverage_audit(
        python,
        required_presentation_view=args.presentation_view,
    )
    if args.json:
        print(json.dumps({"action": "full-backfill", "status": "complete", "candidateManifest": str(candidate_manifest_path), "snkWorklist": str(snk_refill_output) if seed_ids else None, "snkRun": str(candidate_snk_run) if candidate_snk_run else None, "overlayUniverse": str(overlay_universe) if overlay_universe else None}, sort_keys=True))


def run_canonical_seed(action: str, args: argparse.Namespace) -> None:
    if args.seed_archive is None:
        raise RuntimeError(f"{action} requires --seed-archive")
    python = ensure_python_environment()
    if action == "seed-build":
        command = [str(python), "-X", "utf8", str(CANONICAL_SEED_PATH), "build", "--output", str(args.seed_archive.resolve())]
        if args.restore_overwrite:
            command.append("--overwrite")
    elif action == "seed-verify":
        command = [str(python), "-X", "utf8", str(CANONICAL_SEED_PATH), "verify", "--seed", str(args.seed_archive.resolve())]
    else:
        if not args.allow_empty_db:
            raise RuntimeError("seed-restore requires --allow-empty-db and refuses non-empty databases")
        command = [
            str(python), "-X", "utf8", str(SEED_RESTORE_PATH), "restore",
            "--seed", str(args.seed_archive.resolve()), "--allow-empty-db",
        ]
    subprocess.run(command, check=True, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Portable CARDZ backend bootstrap")
    parser.add_argument(
        "action",
        nargs="?",
        choices=(
            "doctor", "up", "bootstrap", "migrate", "import", "routes", "discovery", "audit", "alerts", "status",
            "daily", "full-backfill", "rebuild-db", "export-snapshot", "seed-build", "seed-verify", "seed-restore",
            "registry", "explain", "graph", "work-items", "generate-docs", "down",
        ),
        default="bootstrap",
    )
    parser.add_argument("identifier", nargs="?", help="Registry identifier used by the explain action")
    parser.add_argument("--external-db", action="store_true", help="Use configured MySQL/RDS and skip Docker Compose")
    parser.add_argument("--wsl-distro", default=os.environ.get("CARDZ_WSL_DISTRO", "Ubuntu"))
    parser.add_argument("--mode", choices=("staging", "production"), default="staging", help="Daily data-run label")
    parser.add_argument("--refresh-active-universe", action="store_true")
    parser.add_argument("--require-gemrate-refresh", action="store_true")
    parser.add_argument(
        "--collect-public-candidates",
        action="store_true",
        help="full-backfill only: permit the resumable keyless GemRate public-card collector; disabled by default",
    )
    parser.add_argument(
        "--full-backfill-candidate-out",
        type=Path,
        default=ROOT / "data/runtime/private-source-map/gemrate-candidate-backfill",
        help="private resumable GemRate candidate manifest directory used by full-backfill",
    )
    parser.add_argument(
        "--full-backfill-snk-refill-out",
        type=Path,
        default=ROOT / "data/runtime/private-source-map/snk-price-refill.json",
        help="private exact SNK worklist written by full-backfill",
    )
    parser.add_argument("--snk-run", type=Path, help="SNK PSA 10 JSONL used by the coverage audit")
    parser.add_argument("--require-global-top350", action="store_true", help="Fail unless all three tracked Top 350 indexes are verified")
    parser.add_argument(
        "--presentation-view",
        choices=("top100", "top300", "top350", "top100_plus_200", "reserve50"),
        default="top300",
        help="ranking presentation view required by audit or publication; never changes canonical storage",
    )
    parser.add_argument("--json", action="store_true", help="Emit a compact JSON completion envelope when the action succeeds")
    parser.add_argument("--format", choices=("json", "markdown", "html"), default="html", help="Registry graph output format")
    parser.add_argument("--status", choices=("planned", "in_progress", "blocked", "completed"), help="Filter work-items by status")
    parser.add_argument("--priority", choices=("P0", "P1", "P2"), help="Filter work-items by priority")
    parser.add_argument("--output", type=Path, help="Registry/graph output file or generated-doc directory")
    parser.add_argument("--check", action="store_true", help="Check generated registry documents for drift")
    parser.add_argument("--publish", action="store_true", help="Allow daily to enter the separately configured publish path")
    parser.add_argument(
        "--local-only",
        action="store_true",
        help="Publish into the local public tree only; never resolves an R2 bucket or promotes a remote pointer",
    )
    parser.add_argument("--confirm-rebuild-db", action="store_true", help="Confirm canonical migration plus import; this command never drops a database")
    parser.add_argument("--seed-archive", type=Path, help="Bootstrap seed archive used by seed-build, seed-verify or seed-restore")
    parser.add_argument("--seed-target", type=Path, help="Empty target root accepted by seed-restore")
    parser.add_argument("--allow-empty-db", action="store_true", help="Confirm that seed-restore may write only to an empty database")
    parser.add_argument("--snapshot-output", type=Path, help="Canonical public snapshot output path")
    parser.add_argument(
        "--bootstrap-archive",
        type=Path,
        help="Verify and restore an active-only archive before bootstrap",
    )
    parser.add_argument(
        "--restore-overwrite",
        action="store_true",
        help="Allow archive restore to replace existing runtime bootstrap files",
    )
    args = parser.parse_args()

    if args.action == "rebuild-db" and not args.confirm_rebuild_db:
        raise RuntimeError("rebuild-db requires --confirm-rebuild-db; it migrates and imports but never drops a database")
    if args.action == "full-backfill" and args.publish:
        raise RuntimeError("full-backfill is backend-only; use daily --publish only after a completed backfill")
    if args.local_only and not (args.action == "daily" and args.publish):
        raise RuntimeError("--local-only only qualifies daily --publish")
    if args.action == "doctor":
        print(json.dumps(doctor_report(), sort_keys=True))
        return 0
    if args.action in {"registry", "explain", "graph", "work-items", "generate-docs"}:
        run_registry_action(args.action, args)
        return 0
    if args.action in {"seed-build", "seed-verify", "seed-restore"}:
        run_canonical_seed(args.action, args)
        if args.json:
            print(json.dumps({"action": args.action, "status": "complete"}, sort_keys=True))
        return 0

    if args.bootstrap_archive:
        if args.action != "bootstrap":
            raise RuntimeError("--bootstrap-archive is only valid with the bootstrap action")
        restore_bootstrap_archive(args.bootstrap_archive, overwrite=args.restore_overwrite)

    if args.action == "discovery":
        python = ensure_python_environment()
        run_data_routing_tool(python)
        run_discovery_tool(python)
        return 0
    if args.action == "routes":
        python = ensure_python_environment()
        run_data_routing_tool(python)
        return 0
    if args.action == "audit":
        python = ensure_python_environment()
        run_coverage_audit(
            python,
            snk_run=args.snk_run,
            required_presentation_view=("top350" if args.require_global_top350 else args.presentation_view),
        )
        return 0
    if args.action == "export-snapshot":
        python = ensure_python_environment()
        external = args.external_db or os.environ.get("CARDZ_DB_MODE", "").casefold() == "external"
        validate_external_transport(external=external, mode=args.mode)
        config, _ = runtime_config(external=external)
        if args.presentation_view == "reserve50":
            raise RuntimeError("reserve50 is private and cannot be exported as a public snapshot")
        command = [
            str(python),
            "-X",
            "utf8",
            str(CANONICAL_PUBLIC_SNAPSHOT_PATH),
            "--view",
            args.presentation_view,
        ]
        if args.snapshot_output:
            command.extend(["--output", str(args.snapshot_output.resolve())])
        if args.mode == "production":
            command.append("--production")
        subprocess.run(command, check=True, cwd=ROOT, env={**os.environ, **config})
        if args.json:
            print(json.dumps({"action": "export-snapshot", "status": "complete"}, sort_keys=True))
        return 0

    external = args.external_db or os.environ.get("CARDZ_DB_MODE", "").casefold() == "external"
    validate_external_transport(external=external, mode=args.mode)
    config, has_env_file = runtime_config(external=external)
    docker: DockerRuntime | None = None
    if not external:
        docker = DockerRuntime(env_file=has_env_file, environment=config, wsl_distro=args.wsl_distro)

    if args.action == "down":
        if docker is None:
            raise RuntimeError("down is unavailable with --external-db")
        docker.compose("down")
        return 0
    if args.action in {"up", "bootstrap", "daily", "full-backfill", "rebuild-db"}:
        if docker is None:
            if args.action == "up":
                raise RuntimeError("up is unavailable with --external-db")
        else:
            docker.compose("up", "-d", "db")
            docker.wait_for_database()
        if args.action == "up":
            return 0

    python = ensure_python_environment()
    if args.action == "daily":
        # Discovery validates source routes and prepares the broad radar. Strict
        # price/population/ranking coverage belongs after this run collects and
        # derives its current evidence inside run_daily.py.
        run_data_routing_tool(python)
        run_discovery_tool(python)
    if args.action == "full-backfill":
        run_full_backfill(python, args=args, config=config, external=external)
        return 0
    if args.action in {"bootstrap", "migrate", "alerts", "daily", "rebuild-db"}:
        run_database_tool(python, "migrate", config)
    if args.action == "rebuild-db":
        run_database_tool(python, "import", config)
        if args.json:
            print(json.dumps({"action": "rebuild-db", "status": "complete"}, sort_keys=True))
        return 0
    if args.action in {"bootstrap", "import"}:
        run_database_tool(python, "import", config)
    if args.action in {"bootstrap", "status"}:
        run_database_tool(python, "status", config)
        if args.action == "status" and args.json:
            print(json.dumps({"action": "status", "status": "complete"}, sort_keys=True))
    if args.action == "alerts":
        run_alert_tool(python, config)
        run_database_tool(python, "status", config)
    if args.action == "daily":
        if args.publish and args.presentation_view == "reserve50":
            raise RuntimeError("reserve50 is private and cannot be published")
        command = [str(python), "-X", "utf8", str(DAILY_RUNTIME_PATH), "--mode", args.mode, "--backend-only"]
        if args.publish:
            command.remove("--backend-only")
            command.extend(["--required-presentation-view", args.presentation_view])
            if args.local_only:
                command.append("--local-only")
        if args.refresh_active_universe:
            command.append("--refresh-active-universe")
        if args.require_gemrate_refresh:
            command.append("--require-gemrate-refresh")
        environment = daily_environment(config, external=external, remote_publish=args.publish and not args.local_only)
        subprocess.run(command, check=True, cwd=ROOT, env=environment)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        print(f"backend command failed with exit code {error.returncode}", file=sys.stderr)
        raise SystemExit(error.returncode) from None
    except (RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
