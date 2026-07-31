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
UNIVERSE_AUTHORITY_PATH = ROOT / "pipelines" / "universe_authority.py"
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
GEMRATE_WATCHLIST_PATH = ROOT / "pipelines" / "gemrate_brute_harvest.py"
SNK_BULK_PATH = ROOT / "pipelines" / "snkrdunk_bulk.py"
FX_RATES_PATH = ROOT / "pipelines" / "fx_rates.py"
FX_SNAPSHOT_PATH = ROOT / "data" / "runtime" / "private-fx" / "latest.json"
SCRIPT_INVENTORY_PATH = ROOT / "pipelines" / "script_inventory.py"
WAVE1_REPORTS_ROOT = ROOT / "data" / "runtime" / "private-reports" / "wave1"
# Wave-1 contracts are the default read-only sources for A11 control CLI.
# Tests may patch CONTROL_CONTRACT_PATHS; operators may override with --contract-root.
CONTROL_CONTRACT_PATHS: dict[str, Path] = {
    "frontend_consumer_census": (
        WAVE1_REPORTS_ROOT
        / "A01-QC-A01-FRONTEND-CONSUMERS"
        / "frontend-consumer-census.json"
    ),
    "field_lineage": (
        WAVE1_REPORTS_ROOT
        / "A02-QC-A02-PUBLIC-FIELD-LINEAGE"
        / "field-lineage-contract.json"
    ),
    "db_fingerprint_contract": (
        WAVE1_REPORTS_ROOT
        / "A03-QC-A03-CANONICAL-SCHEMA-FINGERPRINT"
        / "db-fingerprint-contract.json"
    ),
    "live_db_state_fingerprint": (
        WAVE1_REPORTS_ROOT
        / "A03-QC-A03-CANONICAL-SCHEMA-FINGERPRINT"
        / "live-db-state-fingerprint.json"
    ),
    "script_lifecycle_registry": (
        WAVE1_REPORTS_ROOT
        / "A04-QC-A04-SCRIPT-LIFECYCLE"
        / "script-lifecycle-registry.json"
    ),
}
BASELINE_MANIFEST_DEFAULT = (
    ROOT
    / "data"
    / "runtime"
    / "private-reports"
    / "baselines"
    / "baseline_qc_20260729_wave0_c1_v1"
    / "baseline-manifest.json"
)
# A virtualenv copied through a Windows-mounted WSL checkout is not portable:
# Windows sees the Linux ``bin/`` layout and WSL symlinks, while Linux cannot
# execute ``Scripts/python.exe``. Keep the bootstrap environments separate so
# either scheduler can prepare its runtime without deleting the other's venv.
VENV_PATH = ROOT / (".venv-backend-windows" if os.name == "nt" else ".venv-backend")
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


def load_universe_authority_module():
    """Load the canonical-universe API without preparing or changing a venv."""

    pipelines_dir = str(UNIVERSE_AUTHORITY_PATH.parent)
    if pipelines_dir not in sys.path:
        sys.path.insert(0, pipelines_dir)
    spec = importlib.util.spec_from_file_location(
        "cardz_universe_authority", UNIVERSE_AUTHORITY_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("canonical universe authority cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_or_print(content: str, output: Path | None) -> None:
    if output is None:
        print(content, end="" if content.endswith("\n") else "\n")
        return
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def posix_under_root(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def resolve_control_contract_paths(contract_root: Path | None = None) -> dict[str, Path]:
    """Return wave-1 control contract paths, optionally remapped under --contract-root."""

    if contract_root is None:
        return dict(CONTROL_CONTRACT_PATHS)
    root = contract_root.resolve()
    remapped: dict[str, Path] = {}
    for key, default_path in CONTROL_CONTRACT_PATHS.items():
        remapped[key] = root / default_path.name
    return remapped


def load_control_json(path: Path) -> dict[str, object]:
    if not path.is_file():
        raise RuntimeError(f"control contract missing: {posix_under_root(path)}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"control contract must be a JSON object: {posix_under_root(path)}")
    return payload


def control_provenance(path: Path, document: Mapping[str, object]) -> dict[str, object]:
    return {
        "path": posix_under_root(path),
        "sha256": sha256_file(path),
        "kind": document.get("kind"),
        "workItemId": document.get("workItemId"),
        "baselineId": document.get("baselineId"),
        "cohortSha256": document.get("cohortSha256"),
        "schemaVersion": document.get("schemaVersion"),
    }


def require_baseline_cohort(
    document: Mapping[str, object],
    *,
    baseline_id: str | None,
    cohort_sha256: str | None,
    label: str,
) -> None:
    """Fail when the operator cites a baseline/cohort that the contract contradicts."""

    if baseline_id:
        contract_baseline = document.get("baselineId")
        if contract_baseline and str(contract_baseline) != baseline_id:
            raise RuntimeError(
                f"{label} baselineId mismatch: contract={contract_baseline} cited={baseline_id}"
            )
    if cohort_sha256:
        contract_cohort = document.get("cohortSha256")
        if contract_cohort and str(contract_cohort) != cohort_sha256:
            raise RuntimeError(
                f"{label} cohortSha256 mismatch: contract={contract_cohort} cited={cohort_sha256}"
            )


def load_script_inventory_module():
    """Load pipelines/script_inventory without preparing a backend venv."""

    pipelines_dir = str(SCRIPT_INVENTORY_PATH.parent)
    if pipelines_dir not in sys.path:
        sys.path.insert(0, pipelines_dir)
    spec = importlib.util.spec_from_file_location("cardz_script_inventory", SCRIPT_INVENTORY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("script inventory module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def explain_control_identifier(
    identifier: str,
    *,
    baseline_id: str | None = None,
    cohort_sha256: str | None = None,
    contract_root: Path | None = None,
) -> dict[str, object] | None:
    """Resolve field:/script:/consumer:/contract: explain extensions from wave-1 JSON.

    Returns None when the identifier is not a control extension (caller falls through
    to the registry explain path).
    """

    paths = resolve_control_contract_paths(contract_root)
    side_effect = {
        "class": "read_only",
        "mutatesDatabase": False,
        "mutatesPointers": False,
        "mutatesTimers": False,
        "writesOnlyWhenOutputFlag": False,
    }

    if identifier.startswith("field:"):
        field_path = identifier[len("field:") :].strip()
        if not field_path:
            raise RuntimeError("explain field: requires a public field path")
        contract_path = paths["field_lineage"]
        document = load_control_json(contract_path)
        require_baseline_cohort(
            document, baseline_id=baseline_id, cohort_sha256=cohort_sha256, label="field-lineage"
        )
        fields = document.get("fields") or []
        if not isinstance(fields, list):
            raise RuntimeError("field-lineage contract fields must be a list")
        exact = [row for row in fields if isinstance(row, dict) and row.get("path") == field_path]
        if not exact:
            prefix = [
                row
                for row in fields
                if isinstance(row, dict) and str(row.get("path") or "").startswith(field_path)
            ]
            return {
                "action": "explain",
                "extension": "field",
                "identifier": identifier,
                "match": "prefix" if prefix else "none",
                "count": len(prefix),
                "fields": prefix,
                "provenance": control_provenance(contract_path, document),
                "citedBaselineId": baseline_id,
                "citedCohortSha256": cohort_sha256,
                "sideEffect": side_effect,
            }
        return {
            "action": "explain",
            "extension": "field",
            "identifier": identifier,
            "match": "exact",
            "count": len(exact),
            "fields": exact,
            "provenance": control_provenance(contract_path, document),
            "citedBaselineId": baseline_id,
            "citedCohortSha256": cohort_sha256,
            "sideEffect": side_effect,
        }

    if identifier.startswith("script:"):
        script_path = identifier[len("script:") :].strip().replace("\\", "/")
        if not script_path:
            raise RuntimeError("explain script: requires a repository-relative executable path")
        contract_path = paths["script_lifecycle_registry"]
        document = load_control_json(contract_path)
        require_baseline_cohort(
            document, baseline_id=baseline_id, cohort_sha256=cohort_sha256, label="script-lifecycle"
        )
        executables = document.get("executables") or []
        if not isinstance(executables, list):
            raise RuntimeError("script-lifecycle executables must be a list")
        matches = [
            row
            for row in executables
            if isinstance(row, dict) and str(row.get("path") or "").replace("\\", "/") == script_path
        ]
        package_scripts = document.get("packageScripts") or []
        package_matches = [
            row
            for row in package_scripts
            if isinstance(row, dict)
            and (
                str(row.get("path") or "").replace("\\", "/") == script_path
                or str(row.get("scriptName") or "") == script_path
            )
        ]
        return {
            "action": "explain",
            "extension": "script",
            "identifier": identifier,
            "match": "exact" if matches or package_matches else "none",
            "count": len(matches) + len(package_matches),
            "executables": matches,
            "packageScripts": package_matches,
            "provenance": control_provenance(contract_path, document),
            "citedBaselineId": baseline_id,
            "citedCohortSha256": cohort_sha256,
            "sideEffect": side_effect,
            "controllerHint": "Use backend.py actions; do not choose scripts by repository search.",
        }

    if identifier.startswith("consumer:"):
        consumer_id = identifier[len("consumer:") :].strip()
        if not consumer_id:
            raise RuntimeError("explain consumer: requires a consumerId")
        contract_path = paths["frontend_consumer_census"]
        document = load_control_json(contract_path)
        require_baseline_cohort(
            document, baseline_id=baseline_id, cohort_sha256=cohort_sha256, label="frontend-consumers"
        )
        pages = [row for row in (document.get("pages") or []) if isinstance(row, dict)]
        apis = [row for row in (document.get("apiRoutes") or []) if isinstance(row, dict)]
        matches = [row for row in pages + apis if row.get("consumerId") == consumer_id]
        return {
            "action": "explain",
            "extension": "consumer",
            "identifier": identifier,
            "match": "exact" if matches else "none",
            "count": len(matches),
            "consumers": matches,
            "provenance": control_provenance(contract_path, document),
            "citedBaselineId": baseline_id,
            "citedCohortSha256": cohort_sha256,
            "sideEffect": side_effect,
        }

    if identifier.startswith("contract:"):
        contract_key = identifier[len("contract:") :].strip()
        key_map = {
            "field-lineage": "field_lineage",
            "field_lineage": "field_lineage",
            "script-lifecycle": "script_lifecycle_registry",
            "script_lifecycle": "script_lifecycle_registry",
            "script-inventory": "script_lifecycle_registry",
            "frontend-consumers": "frontend_consumer_census",
            "frontend_consumers": "frontend_consumer_census",
            "consumer-census": "frontend_consumer_census",
            "db-fingerprint": "db_fingerprint_contract",
            "db_fingerprint": "db_fingerprint_contract",
            "live-db-fingerprint": "live_db_state_fingerprint",
            "live_db_fingerprint": "live_db_state_fingerprint",
        }
        mapped = key_map.get(contract_key)
        if mapped is None:
            raise RuntimeError(
                "explain contract: expects field-lineage|script-lifecycle|frontend-consumers|"
                "db-fingerprint|live-db-fingerprint"
            )
        contract_path = paths[mapped]
        document = load_control_json(contract_path)
        require_baseline_cohort(
            document, baseline_id=baseline_id, cohort_sha256=cohort_sha256, label=contract_key
        )
        summary: dict[str, object] = {
            "action": "explain",
            "extension": "contract",
            "identifier": identifier,
            "provenance": control_provenance(contract_path, document),
            "citedBaselineId": baseline_id,
            "citedCohortSha256": cohort_sha256,
            "sideEffect": side_effect,
            "documentKeys": sorted(document.keys()),
        }
        if mapped == "field_lineage":
            summary["summary"] = document.get("summary")
            summary["fieldCount"] = len(document.get("fields") or [])
        elif mapped == "script_lifecycle_registry":
            summary["counts"] = document.get("counts")
            summary["acceptance"] = document.get("acceptance")
        elif mapped == "frontend_consumer_census":
            summary["acceptance"] = document.get("acceptance")
            summary["pageCount"] = len(document.get("pages") or [])
            summary["apiRouteCount"] = len(document.get("apiRoutes") or [])
        elif mapped == "live_db_state_fingerprint":
            summary["dbStateFingerprint"] = document.get("dbStateFingerprint")
            summary["readOnly"] = document.get("readOnly")
        else:
            summary["contractId"] = document.get("contractId")
            summary["algorithm"] = document.get("algorithm")
        return summary

    return None


def run_audit_scripts(args: argparse.Namespace) -> dict[str, object]:
    """Read-only script lifecycle audit: load wave-1 registry or regenerate in memory."""

    paths = resolve_control_contract_paths(args.contract_root)
    live = bool(getattr(args, "live", False))
    path_filter = (args.identifier or "").strip().replace("\\", "/") or None
    envelope: dict[str, object] = {
        "action": "audit-scripts",
        "mode": "live-regenerate" if live else "wave1-contract",
        "citedBaselineId": args.baseline_id,
        "citedCohortSha256": args.cohort_sha256,
        "sideEffect": {
            "class": "read_only" if args.output is None else "generated_view_only",
            "mutatesDatabase": False,
            "mutatesPointers": False,
            "mutatesTimers": False,
            "writesOnlyWhenOutputFlag": True,
        },
    }

    if live:
        inventory_module = load_script_inventory_module()
        inventory = inventory_module.build_inventory(root=ROOT)
        errors = inventory_module.validate_inventory(inventory)
        document = inventory
        provenance = {
            "source": "pipelines/script_inventory.py:build_inventory",
            "regenerated": True,
            "root": posix_under_root(ROOT),
        }
        if args.output is not None:
            output_root = args.output.resolve()
            hashes = inventory_module.export_artifacts(inventory, output_root)
            envelope["artifactSha256"] = hashes
            envelope["outputRoot"] = posix_under_root(output_root)
    else:
        contract_path = paths["script_lifecycle_registry"]
        document = load_control_json(contract_path)
        require_baseline_cohort(
            document,
            baseline_id=args.baseline_id,
            cohort_sha256=args.cohort_sha256,
            label="audit-scripts",
        )
        errors = []
        acceptance = document.get("acceptance") or {}
        if isinstance(acceptance, dict) and not acceptance.get("pass", True):
            errors.append("wave-1 script-lifecycle acceptance.pass is false")
        provenance = control_provenance(contract_path, document)
        if args.output is not None:
            # Copy-only view: write the loaded contract, never mutate source contracts.
            output_path = args.output.resolve()
            if output_path.suffix.lower() != ".json":
                output_path = output_path / "script-lifecycle-registry.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            text = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
            output_path.write_text(text, encoding="utf-8", newline="\n")
            envelope["output"] = posix_under_root(output_path)
            envelope["outputSha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()

    executables = document.get("executables") or []
    if not isinstance(executables, list):
        raise RuntimeError("script inventory executables must be a list")
    if path_filter:
        executables = [
            row
            for row in executables
            if isinstance(row, dict)
            and (
                str(row.get("path") or "").replace("\\", "/") == path_filter
                or str(row.get("path") or "").replace("\\", "/").endswith("/" + path_filter)
            )
        ]
    envelope["provenance"] = provenance
    envelope["counts"] = document.get("counts")
    envelope["acceptance"] = document.get("acceptance")
    envelope["errors"] = errors
    envelope["filter"] = path_filter
    envelope["matchCount"] = len(executables) if path_filter else (document.get("counts") or {}).get(
        "executables"
    )
    if path_filter:
        envelope["executables"] = executables
    envelope["controllerHint"] = (
        "Domain roles must call backend.py audit-scripts / explain script:<path>; "
        "do not invent scripts by repository search."
    )
    return envelope


def run_card_routes(args: argparse.Namespace) -> dict[str, object]:
    """Per-card / page / API route map from the wave-1 frontend consumer census."""

    paths = resolve_control_contract_paths(args.contract_root)
    contract_path = paths["frontend_consumer_census"]
    document = load_control_json(contract_path)
    require_baseline_cohort(
        document,
        baseline_id=args.baseline_id,
        cohort_sha256=args.cohort_sha256,
        label="card-routes",
    )
    pages = [row for row in (document.get("pages") or []) if isinstance(row, dict)]
    apis = [row for row in (document.get("apiRoutes") or []) if isinstance(row, dict)]
    card_id = (getattr(args, "card_id", None) or "").strip() or None
    if args.identifier and not card_id:
        # Optional positional filter: consumerId or route fragment.
        needle = args.identifier.strip()
        pages = [
            row
            for row in pages
            if needle in str(row.get("consumerId") or "")
            or needle in str(row.get("route") or "")
        ]
        apis = [
            row
            for row in apis
            if needle in str(row.get("consumerId") or "")
            or needle in str(row.get("route") or "")
        ]

    route_rows: list[dict[str, object]] = []
    for row in pages:
        route_rows.append(
            {
                "kind": "page",
                "consumerId": row.get("consumerId"),
                "route": row.get("route"),
                "file": row.get("file"),
                "component": row.get("component"),
                "dataSource": row.get("dataSource"),
                "filters": row.get("filters") or [],
                "sorts": row.get("sorts") or [],
                "limits": row.get("limits") or [],
                "publicFieldsConsumed": row.get("publicFieldsConsumed") or [],
                "perCard": str(row.get("route") or "").find("[id]") >= 0
                or str(row.get("consumerId") or "") == "page.card_detail",
            }
        )
    for row in apis:
        route_rows.append(
            {
                "kind": "api",
                "consumerId": row.get("consumerId"),
                "route": row.get("route"),
                "file": row.get("file"),
                "handler": row.get("handler"),
                "queryParams": row.get("queryParams") or {},
                "filters": row.get("filters") or [],
                "sorts": row.get("sorts") or [],
                "limits": row.get("limits") or [],
                "publicFieldsConsumed": row.get("publicFieldsConsumed") or [],
                "perCard": "card" in str(row.get("route") or "").lower()
                or "card" in str(row.get("consumerId") or "").lower(),
            }
        )

    envelope: dict[str, object] = {
        "action": "card-routes",
        "citedBaselineId": args.baseline_id or document.get("baselineId"),
        "citedCohortSha256": args.cohort_sha256 or document.get("cohortSha256"),
        "cohortOption": "C1",
        "cardId": card_id,
        "count": len(route_rows),
        "routes": route_rows,
        "perCardRouteTemplate": {
            "page": "/card/[id]",
            "note": "Substitute [id] with a C1 qualified card id when domain work resolves ids",
        },
        "acceptance": document.get("acceptance"),
        "provenance": control_provenance(contract_path, document),
        "sideEffect": {
            "class": "read_only" if args.output is None else "generated_view_only",
            "mutatesDatabase": False,
            "mutatesPointers": False,
            "mutatesTimers": False,
            "writesOnlyWhenOutputFlag": True,
        },
        "controllerHint": "Use backend.py card-routes; do not rediscover FE routes by repo search.",
    }
    if card_id:
        envelope["instantiatedCardRoute"] = f"/card/{card_id}"
    return envelope


def run_card_matrix(args: argparse.Namespace) -> dict[str, object]:
    """Per-card dimension matrix skeleton for the 932 C1 cohort from field lineage + baseline."""

    paths = resolve_control_contract_paths(args.contract_root)
    lineage_path = paths["field_lineage"]
    lineage = load_control_json(lineage_path)
    require_baseline_cohort(
        lineage,
        baseline_id=args.baseline_id,
        cohort_sha256=args.cohort_sha256,
        label="card-matrix/field-lineage",
    )

    baseline_path = Path(args.baseline_manifest) if getattr(args, "baseline_manifest", None) else BASELINE_MANIFEST_DEFAULT
    baseline: dict[str, object] | None = None
    baseline_provenance: dict[str, object] | None = None
    qualified_count = 932
    cohort_option = "C1"
    if baseline_path.is_file():
        baseline = load_control_json(baseline_path)
        if args.baseline_id and baseline.get("baselineId") and str(baseline["baselineId"]) != args.baseline_id:
            raise RuntimeError(
                f"baseline-manifest baselineId mismatch: {baseline.get('baselineId')} != {args.baseline_id}"
            )
        cohort = baseline.get("cohort") if isinstance(baseline.get("cohort"), dict) else {}
        if isinstance(cohort, dict):
            if cohort.get("qualifiedCount") is not None:
                qualified_count = int(cohort["qualifiedCount"])  # type: ignore[arg-type]
            if cohort.get("optionId"):
                cohort_option = str(cohort["optionId"])
            cohort_sha = cohort.get("universeCandidateSha256")
            if args.cohort_sha256 and cohort_sha and str(cohort_sha) != args.cohort_sha256:
                raise RuntimeError(
                    f"baseline cohort sha mismatch: {cohort_sha} != {args.cohort_sha256}"
                )
        baseline_provenance = control_provenance(baseline_path, baseline)

    fields = [row for row in (lineage.get("fields") or []) if isinstance(row, dict)]
    card_dimensions = [
        {
            "path": row.get("path"),
            "mappingStatus": row.get("mapping_status"),
            "schemaType": row.get("schema_type"),
            "nullableSemantics": row.get("nullable_semantics"),
            "staleSemantics": row.get("stale_semantics"),
            "derivedSemantics": row.get("derived_semantics"),
            "fallbackSemantics": row.get("fallback_semantics"),
            "authority": row.get("authority"),
        }
        for row in fields
        if str(row.get("path") or "").startswith("cards[]")
    ]
    card_id = (getattr(args, "card_id", None) or "").strip() or None
    if args.identifier and not card_id:
        card_id = args.identifier.strip() or None

    envelope: dict[str, object] = {
        "action": "card-matrix",
        "kind": "cardz-c1-card-dimension-matrix-skeleton",
        "citedBaselineId": args.baseline_id or lineage.get("baselineId"),
        "citedCohortSha256": args.cohort_sha256 or lineage.get("cohortSha256"),
        "cohortOption": cohort_option,
        "qualifiedCount": qualified_count,
        "dimensionCount": len(card_dimensions),
        "dimensions": card_dimensions,
        "cardId": card_id,
        "matrixShape": {
            "rows": "C1 qualified cards (count=qualifiedCount); ids resolved by domain collectors",
            "columns": "cards[] public field paths from field-lineage-contract",
            "cells": "not populated by control CLI; domain roles fill via registered writers",
        },
        "skeletonOnly": True,
        "lineageSummary": lineage.get("summary"),
        "provenance": {
            "fieldLineage": control_provenance(lineage_path, lineage),
            "baseline": baseline_provenance,
        },
        "sideEffect": {
            "class": "read_only" if args.output is None else "generated_view_only",
            "mutatesDatabase": False,
            "mutatesPointers": False,
            "mutatesTimers": False,
            "writesOnlyWhenOutputFlag": True,
        },
        "controllerHint": (
            "Use backend.py card-matrix for the C1 dimension skeleton; "
            "do not invent product goals or fill cells outside owned writers."
        ),
    }
    if card_id:
        envelope["rowSkeleton"] = {
            "cardId": card_id,
            "cells": {str(dim["path"]): None for dim in card_dimensions},
        }
    return envelope


def run_control_action(action: str, args: argparse.Namespace) -> None:
    """Dispatch A11 control CLI read-only / generated-view actions."""

    if action == "audit-scripts":
        payload = run_audit_scripts(args)
    elif action == "card-routes":
        payload = run_card_routes(args)
    elif action == "card-matrix":
        payload = run_card_matrix(args)
    else:
        raise RuntimeError(f"unsupported control action: {action}")

    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if args.output is not None and action in {"card-routes", "card-matrix"}:
        write_or_print(rendered, args.output)
        return
    if args.output is not None and action == "audit-scripts" and getattr(args, "live", False):
        # live mode already exported artifacts under --output; still print envelope.
        print(rendered, end="")
        return
    if args.output is not None and action == "audit-scripts" and not getattr(args, "live", False):
        # contract mode may have written a copy; still print envelope to stdout.
        print(rendered, end="")
        return
    print(rendered, end="")


def run_registry_action(action: str, args: argparse.Namespace) -> None:
    routing = load_data_routing_module()
    document = routing.load_registry()
    if action == "registry":
        write_or_print(routing.render_registry(document, "json"), args.output)
        return
    if action == "explain":
        if not args.identifier:
            raise RuntimeError(
                "explain requires an identifier. Registry: metric/field/tool/profile/view/consumer. "
                "Control extensions: field:<path>, script:<path>, consumer:<id>, "
                "contract:field-lineage|script-lifecycle|frontend-consumers|db-fingerprint|live-db-fingerprint"
            )
        control_payload = explain_control_identifier(
            args.identifier,
            baseline_id=getattr(args, "baseline_id", None),
            cohort_sha256=getattr(args, "cohort_sha256", None),
            contract_root=getattr(args, "contract_root", None),
        )
        if control_payload is not None:
            write_or_print(
                json.dumps(control_payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                args.output,
            )
            return
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


def read_only_runtime_config(*, external: bool) -> dict[str, str]:
    """Resolve canonical MySQL settings without generating or writing config."""

    if external:
        config = {key: os.environ.get(key, "").strip() for key in REQUIRED_EXTERNAL}
        if os.environ.get("CARDZ_DB_SSL_CA"):
            config["CARDZ_DB_SSL_CA"] = os.environ["CARDZ_DB_SSL_CA"].strip()
    else:
        config = {
            "CARDZ_DB_HOST": "127.0.0.1",
            "CARDZ_DB_PORT": "3308",
            "CARDZ_DB_NAME": "cardz_market_cap",
            "CARDZ_DB_USER": "cardz",
            **{
                key: value.strip()
                for key, value in read_env_file(CONFIG_PATH).items()
            },
        }
        for key in (*REQUIRED_EXTERNAL, "CARDZ_DB_SSL_CA"):
            if os.environ.get(key):
                config[key] = os.environ[key].strip()
    missing = [key for key in REQUIRED_EXTERNAL if not config.get(key)]
    if missing:
        raise RuntimeError(
            f"read-only database configuration is incomplete: {', '.join(missing)}"
        )
    if config["CARDZ_DB_NAME"] != "cardz_market_cap":
        raise RuntimeError(
            "CARDZ universe/status commands require canonical cardz_market_cap MySQL"
        )
    return config


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


def run_database_tool(
    python: Path,
    action: str,
    environment: Mapping[str, str],
    *extra_args: str,
) -> None:
    subprocess.run(
        [str(python), "-X", "utf8", str(DB_RUNTIME_PATH), action, *extra_args],
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
    host = os.environ.get("CARDZ_DB_HOST", "").strip().casefold().rstrip(".")
    loopback = host in {"127.0.0.1", "::1", "localhost"}
    if external and mode == "production" and not loopback and not os.environ.get("CARDZ_DB_SSL_CA"):
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


def machine_status(args: argparse.Namespace) -> dict[str, object]:
    """Return a bounded, read-only status envelope for automation."""

    started = time.monotonic()
    active_path = (
        args.active_universe.resolve()
        if args.active_universe is not None
        else ROOT / "data/runtime/private-source-map/tracked-universe.json"
    )
    publish_root = Path(
        os.environ.get(
            "CARDZ_PUBLISH_ROOT",
            str(ROOT / "data/public/publish-staging"),
        )
    ).resolve()
    try:
        config = read_only_runtime_config(external=args.external_db)
        authority = load_universe_authority_module()
        connection = authority.connect_from_values(config, read_only=True)
        try:
            report = authority.machine_status(
                connection,
                active_path=active_path,
                publish_root=publish_root,
            )
        finally:
            connection.close()
    except Exception as error:
        blocker = "database_status_unavailable"
        report = {
            "database": {
                "authority": "canonical_mysql",
                "name": "cardz_market_cap",
                "connected": False,
            },
            "universeIntegrity": {
                "status": "unavailable",
                "matchesCandidate": False,
            },
            "qc": {"status": "unavailable"},
            "pending": {"status": "unavailable"},
            "generation": {
                "status": "unavailable",
                "productionEligible": False,
            },
            "releaseGate": {
                "eligible": False,
                "blockers": [blocker],
                "blockerTypes": {blocker: error.__class__.__name__},
            },
        }
    elapsed_ms = round((time.monotonic() - started) * 1000)
    release_gate = report["releaseGate"]
    blockers = list(release_gate.get("blockers", []))
    if elapsed_ms >= 5000 and "status_latency_target_exceeded" not in blockers:
        blockers.append("status_latency_target_exceeded")
        release_gate["eligible"] = False
        release_gate["blockers"] = blockers
    return {
        "action": "status",
        "status": "ready" if release_gate.get("eligible") else "blocked",
        "readOnly": True,
        "elapsedMs": elapsed_ms,
        "targetMs": 5000,
        **report,
    }


def run_universe_action(action: str, args: argparse.Namespace) -> dict[str, object]:
    """Run an explicit candidate/materialize/promote command against canonical MySQL."""

    config = read_only_runtime_config(external=args.external_db)
    authority = load_universe_authority_module()
    connection = authority.connect_from_values(
        config,
        read_only=action == "universe-build",
    )
    try:
        if action == "universe-build":
            report = authority.write_immutable_candidate(
                authority.build_candidate(connection),
                args.universe_output_root.resolve(),
            )
        else:
            if args.universe_candidate is None:
                raise RuntimeError(f"{action} requires --universe-candidate")
            document = authority.read_candidate(args.universe_candidate.resolve())
            if action == "universe-promote":
                active_path = (
                    args.active_universe.resolve()
                    if args.active_universe is not None
                    else ROOT
                    / "data/runtime/private-source-map/tracked-universe.json"
                )
                if active_path == args.universe_candidate.resolve():
                    raise RuntimeError(
                        "universe candidate and active-universe paths must differ"
                    )
                report = authority.promote_candidate(
                    connection,
                    document,
                    active_path=active_path,
                )
            else:
                report = authority.materialize_candidate(
                    connection,
                    document,
                    promote=False,
                )
    finally:
        connection.close()
    return {"action": action, "status": "complete", **report}


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
        required_presentation_view = "top300_boards"
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
        "--run-profile",
        "full",
        "--refresh-bootstrap-source",
        "--bootstrap-only",
    ]


def shared_engine_daily_command(
    python: Path,
    *,
    mode: str,
    profile: str,
    backend_only: bool = True,
    presentation_view: str | None = None,
    local_only: bool = False,
    refresh_active_universe: bool = False,
    require_gemrate_refresh: bool = False,
    active_universe: Path | None = None,
    gemrate_ids: Path | None = None,
    snk_ids: Path | None = None,
    skip_fx_refresh: bool = False,
    dry_run: bool = False,
    release_profile: str = "relaxed-launch-v1",
) -> list[str]:
    """Build the one shared engine entrypoint for full and incremental runs.

    Full-backfill discovery/candidate phases may precede this command, but the
    business stages (collect → normalize → ingest → derive → QC → publish gate)
    always enter ``pipelines/run_daily.py`` with an explicit ``--run-profile``.
    """

    normalized = str(profile or "").strip().casefold()
    if normalized in {"full", "full-backfill"}:
        run_profile = "full"
    elif normalized in {"incremental", "daily", "due-delta"}:
        run_profile = "incremental"
    else:
        raise ValueError(f"unknown shared engine profile: {profile!r}")
    command = [
        str(python),
        "-X",
        "utf8",
        str(DAILY_RUNTIME_PATH),
        "--mode",
        mode,
        "--run-profile",
        run_profile,
        "--release-profile",
        release_profile,
    ]
    if dry_run:
        command.append("--dry-run")
        return command
    if backend_only:
        command.append("--backend-only")
    elif presentation_view is not None:
        command.extend(["--required-presentation-view", presentation_view])
        if local_only:
            command.append("--local-only")
    if refresh_active_universe:
        command.append("--refresh-active-universe")
    if require_gemrate_refresh:
        command.append("--require-gemrate-refresh")
    if active_universe is not None and gemrate_ids is not None and snk_ids is not None:
        command.extend(
            [
                "--active-universe",
                str(active_universe),
                "--gemrate-ids",
                str(gemrate_ids),
                "--snk-ids",
                str(snk_ids),
            ]
        )
    if skip_fx_refresh:
        command.append("--skip-fx-refresh")
    return command


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
        "--use-canonical-db-identities",
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


def _universe_source_refs(document: Mapping[str, object]) -> set[tuple[str, str]]:
    rows: list[object] = []
    for key in ("cards", "monitoringCandidates"):
        value = document.get(key)
        if value is None and key == "monitoringCandidates":
            continue
        if not isinstance(value, list):
            raise RuntimeError(f"universe {key} is invalid")
        rows.extend(value)
    refs: set[tuple[str, str]] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise RuntimeError("universe contains a non-object member")
        ref = (
            str(row.get("canonicalSourceCode") or "").strip().casefold(),
            str(row.get("canonicalExternalId") or "").strip(),
        )
        if not all(ref) or ref in refs:
            raise RuntimeError("universe contains a missing or duplicate canonical source reference")
        refs.add(ref)
    return refs


def _atomic_replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def promote_additive_universe(
    candidate_universe: Path,
    candidate_gemrate_ids: Path,
    candidate_snk_ids: Path,
) -> dict[str, object]:
    """Promote one validated collection lock without removing current members."""

    sources = (candidate_universe, candidate_gemrate_ids, candidate_snk_ids)
    if any(not path.is_file() for path in sources):
        raise RuntimeError("candidate universe promotion files are incomplete")
    candidate_document = load_json_document(candidate_universe, description="candidate active universe")
    candidate_refs = _universe_source_refs(candidate_document)
    destinations = (
        ROOT / "data/runtime/private-source-map/tracked-universe.json",
        ROOT / "data/runtime/private-source-map/tracked-gemrate-ids.txt",
        ROOT / "data/runtime/private-source-map/tracked-snk-ids.txt",
    )
    current_refs: set[tuple[str, str]] = set()
    if destinations[0].is_file():
        current_document = load_json_document(destinations[0], description="current active universe")
        current_refs = _universe_source_refs(current_document)
        missing = current_refs - candidate_refs
        if missing:
            raise RuntimeError(
                "candidate universe is not additive: "
                f"{len(missing)} current canonical members would be removed"
            )

    payloads = tuple(path.read_bytes() for path in sources)
    lock_sha256 = hashlib.sha256(payloads[0]).hexdigest()
    archive_root = ROOT / "data/runtime/private-source-map/universe-locks" / lock_sha256
    archive_names = ("tracked-universe.json", "tracked-gemrate-ids.txt", "tracked-snk-ids.txt")
    for name, payload in zip(archive_names, payloads, strict=True):
        archived = archive_root / name
        if archived.is_file() and archived.read_bytes() != payload:
            raise RuntimeError(f"immutable universe archive mismatch: {archived}")
        if not archived.is_file():
            _atomic_replace_bytes(archived, payload)

    previous = tuple(path.read_bytes() if path.is_file() else None for path in destinations)
    try:
        for destination, payload in zip(destinations, payloads, strict=True):
            _atomic_replace_bytes(destination, payload)
    except Exception:
        for destination, payload in zip(destinations, previous, strict=True):
            if payload is not None:
                _atomic_replace_bytes(destination, payload)
        raise
    return {
        "lockSha256": lock_sha256,
        "previousMembers": len(current_refs),
        "members": len(candidate_refs),
        "addedMembers": len(candidate_refs - current_refs),
        "archiveRoot": str(archive_root),
    }


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

    Private landing files may be refreshed when a source is incomplete.
    Additive migrations run before the read-only canonical identity overlay;
    the only canonical fact mutation occurs in the final daily import after
    the broad GemRate candidate manifest is classified and the complete
    merged universe has a fresh exact SNK collection.
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
    run_database_tool(python, "migrate", config)
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
                    "--snk-worklist", str(snk_refill_output),
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
    # Final business stages always use the shared engine entrypoint (run_daily)
    # with profile=full — same stages/path as incremental daily, different
    # selector/cursor/range/freshness only.
    daily_command = shared_engine_daily_command(
        python,
        mode=args.mode,
        profile="full",
        backend_only=True,
        require_gemrate_refresh=bool(args.require_gemrate_refresh),
        active_universe=overlay_universe,
        gemrate_ids=overlay_gemrate_ids,
        snk_ids=overlay_snk_ids,
        skip_fx_refresh=overlay_universe is not None,
    )
    subprocess.run(daily_command, check=True, cwd=ROOT, env=environment)
    # run_daily owns the post-import coverage, source-volume, freshness, and
    # passed-evaluation gates.  Re-running the standalone audit here would use
    # the frozen universe/latest unrelated SNK landing instead of this
    # candidate overlay and could reject an already validated attempt.
    universe_promotion = None
    if getattr(args, "promote_universe", False):
        if overlay_universe is None or overlay_gemrate_ids is None or overlay_snk_ids is None:
            raise RuntimeError("universe promotion requires a completed exact candidate overlay")
        universe_promotion = promote_additive_universe(
            overlay_universe,
            overlay_gemrate_ids,
            overlay_snk_ids,
        )
    if args.json:
        print(json.dumps({"action": "full-backfill", "status": "complete", "candidateManifest": str(candidate_manifest_path), "snkWorklist": str(snk_refill_output) if seed_ids else None, "snkRun": str(candidate_snk_run) if candidate_snk_run else None, "overlayUniverse": str(overlay_universe) if overlay_universe else None, "universePromotion": universe_promotion}, sort_keys=True))


def run_qualified_sync(
    python: Path,
    *,
    args: argparse.Namespace,
    config: Mapping[str, str],
    external: bool,
) -> None:
    """One owner for the POP-qualified stock load and later incremental runs."""

    environment = daily_environment(config, external=external, remote_publish=False)
    # eBay PSA10 sold is required alongside SNK. Transport may be a
    # repository-owned sold dump OR PriceCharting product-page export
    # (eBay-derived). Direct eBay sold search is still PerimeterX-blocked.
    default_pc_export = ROOT / "data/runtime/private-source-map/ebay-sold-from-pricecharting.json"
    ebay_input = Path(environment.get("CARDZ_EBAY_SOLD_INPUT", "") or "")
    if not ebay_input.is_file() and default_pc_export.is_file():
        ebay_input = default_pc_export
        environment = {
            **environment,
            "CARDZ_EBAY_SOLD_INPUT": str(ebay_input),
            "CARDZ_EBAY_SOLD_ENABLED": "true",
        }
    if environment.get("CARDZ_EBAY_SOLD_ENABLED", "").casefold() != "true":
        raise RuntimeError(
            "qualified-sync requires CARDZ_EBAY_SOLD_ENABLED=true "
            "(PriceCharting eBay-derived export counts); "
            "SNK and eBay-sold evidence must be collected together"
        )
    if not ebay_input.is_file():
        raise RuntimeError(
            "qualified-sync requires CARDZ_EBAY_SOLD_INPUT or "
            f"{default_pc_export} from pipelines/pricecharting_ebay_export.py"
        )
    subprocess.run(
        [str(python), "-X", "utf8", str(GEMRATE_WATCHLIST_PATH), "--all-sets", "--sync-db"],
        check=True,
        cwd=ROOT,
        env=environment,
    )
    run_full_backfill(python, args=args, config=config, external=external)


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
    parser = argparse.ArgumentParser(
        description="Portable CARDZ backend bootstrap and A11 control CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "control CLI (read-only unless --output):\n"
            "  explain field:<path> | script:<path> | consumer:<id> | contract:<name>\n"
            "  audit-scripts [--live] [--baseline-id ID] [--cohort-sha256 HEX]\n"
            "  card-routes [--card-id ID] [--baseline-id ID] [--cohort-sha256 HEX]\n"
            "  card-matrix [--card-id ID] [--baseline-id ID] [--cohort-sha256 HEX]\n"
            "Domain roles must use these commands instead of repository search for\n"
            "field lineage, script inventory, FE routes, and the C1 card matrix skeleton."
        ),
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=(
            "doctor", "up", "bootstrap", "migrate", "import", "routes", "discovery", "audit", "alerts", "status",
            "daily", "qualified-sync", "full-backfill", "rebuild-db", "export-snapshot", "seed-build", "seed-verify", "seed-restore",
            "universe-build", "universe-materialize", "universe-promote",
            "printing-plan", "printing-materialize",
            "registry", "explain", "graph", "work-items", "generate-docs", "down",
            "audit-scripts", "card-routes", "card-matrix",
        ),
        default="bootstrap",
        help=(
            "backend action. Control plane: explain (with field:/script:/consumer:/contract: "
            "extensions), audit-scripts, card-routes, card-matrix"
        ),
    )
    parser.add_argument(
        "identifier",
        nargs="?",
        help=(
            "explain target (registry id or field:/script:/consumer:/contract: extension); "
            "optional path filter for audit-scripts; optional consumer/route filter for card-routes; "
            "optional card id for card-matrix"
        ),
    )
    parser.add_argument("--external-db", action="store_true", help="Use configured MySQL/RDS and skip Docker Compose")
    parser.add_argument("--wsl-distro", default=os.environ.get("CARDZ_WSL_DISTRO", "Ubuntu"))
    parser.add_argument("--mode", choices=("staging", "production"), default="staging", help="Daily data-run label")
    parser.add_argument("--release-profile", default="relaxed-launch-v1", help="Named public release policy")
    parser.add_argument("--refresh-active-universe", action="store_true")
    parser.add_argument("--require-gemrate-refresh", action="store_true")
    parser.add_argument(
        "--collect-public-candidates",
        action="store_true",
        help="full-backfill only: permit the resumable keyless GemRate public-card collector; disabled by default",
    )
    parser.add_argument(
        "--promote-universe",
        action="store_true",
        help="full-backfill only: atomically promote a validated additive candidate overlay",
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
        choices=("top100", "top300", "top300_boards", "top350", "top100_plus_200", "all_eligible", "reserve50"),
        default="all_eligible",
        help="ranking presentation view required by audit or publication; never changes canonical storage",
    )
    parser.add_argument(
        "--active-universe",
        type=Path,
        help="import/status override, or active consumer target for universe-promote",
    )
    parser.add_argument(
        "--universe-candidate",
        type=Path,
        help="explicit immutable schema-5 candidate for universe materialize/promote",
    )
    parser.add_argument(
        "--universe-output-root",
        type=Path,
        default=ROOT / "data/runtime/private-source-map/universe-candidates",
        help="private immutable output directory for universe-build; never the active file",
    )
    parser.add_argument(
        "--printing-candidate",
        type=Path,
        help="explicit immutable canonical printing candidate",
    )
    parser.add_argument(
        "--printing-plan-sha256",
        help="exact canonical printing plan hash required by materialize",
    )
    parser.add_argument(
        "--qc-run",
        help="explicit canonical DB QC run for printing-plan audit; apply still requires latest authority",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="commit printing-materialize; otherwise validate and roll back",
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
    parser.add_argument("--db-qc-report", type=Path, help="Exact canonical DB QC report bound to export-snapshot")
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
    parser.add_argument(
        "--baseline-id",
        help="Cite immutable baseline id; control commands fail when contracts disagree",
    )
    parser.add_argument(
        "--cohort-sha256",
        help="Cite C1 cohort sha256; control commands fail when contracts disagree",
    )
    parser.add_argument(
        "--contract-root",
        type=Path,
        help="Optional directory of wave-1 contract JSON files (defaults under data/runtime/private-reports/wave1)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="audit-scripts only: regenerate inventory from code via pipelines/script_inventory.py (still no DB writes)",
    )
    parser.add_argument(
        "--card-id",
        help="card-routes / card-matrix: optional single card id for instantiated route or matrix row skeleton",
    )
    parser.add_argument(
        "--baseline-manifest",
        type=Path,
        help="card-matrix only: path to baseline-manifest.json (defaults to C1 wave-0 baseline)",
    )
    args = parser.parse_args()

    if args.action == "rebuild-db" and not args.confirm_rebuild_db:
        raise RuntimeError("rebuild-db requires --confirm-rebuild-db; it migrates and imports but never drops a database")
    if args.action in {"qualified-sync", "full-backfill"} and args.publish:
        raise RuntimeError(f"{args.action} is backend-only; use daily --publish only after a completed backfill")
    if args.promote_universe and args.action not in {"qualified-sync", "full-backfill"}:
        raise RuntimeError("--promote-universe is only valid with qualified-sync or full-backfill")
    if args.local_only and not (args.action == "daily" and args.publish):
        raise RuntimeError("--local-only only qualifies daily --publish")
    if args.active_universe is not None and args.action not in {
        "import",
        "status",
        "universe-promote",
    }:
        raise RuntimeError(
            "--active-universe is only valid with import, status or universe-promote"
        )
    if args.universe_candidate is not None and args.action not in {
        "universe-materialize",
        "universe-promote",
    }:
        raise RuntimeError(
            "--universe-candidate is only valid with universe-materialize or universe-promote"
        )
    printing_only_values = {
        "--printing-candidate": args.printing_candidate,
        "--printing-plan-sha256": args.printing_plan_sha256,
        "--qc-run": args.qc_run,
    }
    invalid_printing_flags = [
        flag
        for flag, value in printing_only_values.items()
        if value is not None
        and args.action not in {"printing-plan", "printing-materialize"}
    ]
    if invalid_printing_flags:
        raise RuntimeError(
            f"{', '.join(invalid_printing_flags)} only valid with printing-plan or printing-materialize"
        )
    if args.apply and args.action != "printing-materialize":
        raise RuntimeError("--apply is only valid with printing-materialize")
    if args.action == "printing-materialize" and (
        args.printing_candidate is None or not args.printing_plan_sha256
    ):
        raise RuntimeError(
            "printing-materialize requires --printing-candidate and --printing-plan-sha256"
        )
    if args.action == "printing-materialize" and (
        len(args.printing_plan_sha256) != 64
        or any(character not in "0123456789abcdef" for character in args.printing_plan_sha256)
    ):
        raise RuntimeError("--printing-plan-sha256 must be 64 lowercase hex characters")
    if args.action == "printing-materialize" and not args.printing_candidate.is_file():
        raise RuntimeError("--printing-candidate must be an existing file")
    if args.action == "printing-plan" and args.printing_candidate is not None:
        raise RuntimeError("--printing-candidate is only valid with printing-materialize")
    if args.action == "printing-plan" and (
        not args.qc_run
        or len(args.qc_run) > 96
        or not args.qc_run[0].isalnum()
        or any(
            character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in args.qc_run
        )
    ):
        raise RuntimeError("printing-plan requires one safe --qc-run")
    if args.action == "printing-materialize" and args.qc_run is not None:
        raise RuntimeError("--qc-run is only valid with printing-plan")
    if args.live and args.action != "audit-scripts":
        raise RuntimeError("--live is only valid with audit-scripts")
    if args.card_id is not None and args.action not in {"card-routes", "card-matrix"}:
        raise RuntimeError("--card-id is only valid with card-routes or card-matrix")
    if args.baseline_manifest is not None and args.action != "card-matrix":
        raise RuntimeError("--baseline-manifest is only valid with card-matrix")
    if args.contract_root is not None and args.action not in {
        "explain",
        "audit-scripts",
        "card-routes",
        "card-matrix",
    }:
        raise RuntimeError(
            "--contract-root is only valid with explain, audit-scripts, card-routes or card-matrix"
        )
    if args.baseline_id is not None and args.action not in {
        "explain",
        "audit-scripts",
        "card-routes",
        "card-matrix",
    }:
        raise RuntimeError(
            "--baseline-id is only valid with explain, audit-scripts, card-routes or card-matrix"
        )
    if args.cohort_sha256 is not None and args.action not in {
        "explain",
        "audit-scripts",
        "card-routes",
        "card-matrix",
    }:
        raise RuntimeError(
            "--cohort-sha256 is only valid with explain, audit-scripts, card-routes or card-matrix"
        )

    if args.action == "doctor":
        print(json.dumps(doctor_report(), sort_keys=True))
        return 0
    if args.action in {"registry", "explain", "graph", "work-items", "generate-docs"}:
        run_registry_action(args.action, args)
        return 0
    if args.action in {"audit-scripts", "card-routes", "card-matrix"}:
        run_control_action(args.action, args)
        return 0
    if args.action in {"seed-build", "seed-verify", "seed-restore"}:
        run_canonical_seed(args.action, args)
        if args.json:
            print(json.dumps({"action": args.action, "status": "complete"}, sort_keys=True))
        return 0
    if args.action == "status":
        report = machine_status(args)
        print(
            json.dumps(
                report,
                ensure_ascii=False,
                sort_keys=True,
                indent=None if args.json else 2,
            )
        )
        return 0
    if args.action in {
        "universe-build",
        "universe-materialize",
        "universe-promote",
    }:
        print(
            json.dumps(
                run_universe_action(args.action, args),
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    if args.action in {"printing-plan", "printing-materialize"}:
        external = (
            args.external_db
            or os.environ.get("CARDZ_DB_MODE", "").casefold() == "external"
        )
        validate_external_transport(external=external, mode=args.mode)
        config = read_only_runtime_config(external=external)
        extra_args: list[str] = []
        if args.action == "printing-plan":
            extra_args.extend(["--qc-run", args.qc_run])
        else:
            extra_args.extend(
                [
                    "--candidate",
                    str(args.printing_candidate.resolve()),
                    "--plan-sha256",
                    args.printing_plan_sha256,
                ]
            )
            if args.apply:
                extra_args.append("--apply")
        run_database_tool(
            Path(sys.executable),
            args.action,
            config,
            *extra_args,
        )
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
            "--release-profile",
            args.release_profile,
        ]
        if args.snapshot_output:
            command.extend(["--output", str(args.snapshot_output.resolve())])
        if args.db_qc_report:
            command.extend(["--db-qc-report", str(args.db_qc_report.resolve())])
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
    if args.action in {"up", "bootstrap", "daily", "qualified-sync", "full-backfill", "rebuild-db"}:
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
    if args.action == "qualified-sync":
        run_qualified_sync(python, args=args, config=config, external=external)
        return 0
    if args.action in {"bootstrap", "migrate", "alerts", "daily", "rebuild-db"}:
        run_database_tool(python, "migrate", config)
    if args.action == "rebuild-db":
        run_database_tool(python, "import", config)
        if args.json:
            print(json.dumps({"action": "rebuild-db", "status": "complete"}, sort_keys=True))
        return 0
    if args.action in {"bootstrap", "import"}:
        import_args = (
            ("--active-universe", str(args.active_universe.resolve()))
            if args.active_universe is not None
            else ()
        )
        run_database_tool(python, "import", config, *import_args)
    if args.action in {"bootstrap", "status"}:
        status_args = (
            ("--active-universe", str(args.active_universe.resolve()))
            if args.active_universe is not None
            else ()
        )
        run_database_tool(python, "status", config, *status_args)
        if args.action == "status" and args.json:
            print(json.dumps({"action": "status", "status": "complete"}, sort_keys=True))
    if args.action == "alerts":
        run_alert_tool(python, config)
        run_database_tool(python, "status", config)
    if args.action == "daily":
        if args.publish and args.presentation_view == "reserve50":
            raise RuntimeError("reserve50 is private and cannot be published")
        # Incremental daily and full-backfill share one engine entrypoint.
        command = shared_engine_daily_command(
            python,
            mode=args.mode,
            profile="incremental",
            backend_only=not args.publish,
            presentation_view=args.presentation_view if args.publish else None,
            local_only=bool(args.local_only),
            refresh_active_universe=bool(args.refresh_active_universe),
            require_gemrate_refresh=bool(args.require_gemrate_refresh),
            release_profile=args.release_profile,
        )
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
