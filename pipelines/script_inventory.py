#!/usr/bin/env python3
"""Machine-complete CARDZ script lifecycle inventory and call graph.

Classifies every executable surface under the A04 read set, denies execution of
``temp/**`` and ``docs/evidence/**``, and asserts each protected writer has
exactly one controller. Inventory is derived from the repository tree plus
``config/data-routing.json`` — never from chat memory or dated reports.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROUTES = ROOT / "config" / "data-routing.json"

EXECUTABLE_SUFFIXES = frozenset(
    {".py", ".mjs", ".js", ".cjs", ".ps1", ".sh", ".bash", ".bat", ".cmd"}
)
SKIP_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".git",
        ".venv",
        ".venv-backend",
        ".venv-backend-windows",
        "dist",
        "coverage",
        ".next",
        "graphify-out",
    }
)

# Roots scanned for classified executables (A04 read set + package scripts).
SCOPED_ROOTS = (
    "scripts",
    "pipelines",
    "integrations",
    "deploy",
    "temp",
    "docs/evidence",
    "apps/web/scripts",
)

# Package manifests whose npm scripts become call-graph edges.
PACKAGE_JSON_PATHS = (
    "package.json",
    "apps/web/package.json",
    "packages/market-data/package.json",
)

DENIED_PREFIXES = (
    "temp/",
    "docs/evidence/",
)

# Protected writer surfaces: exactly one production controller each.
# Controllers are the sole authorized orchestration entry for production use;
# direct CLI on the surface remains possible but is not the controller.
PROTECTED_WRITERS: dict[str, dict[str, Any]] = {
    "canonical_database": {
        "surface": "pipelines/db_runtime.py",
        "controller": "scripts/backend.py",
        "toolIds": ["canonical-db", "printing-plan", "printing-materialize"],
        "nodeIds": ["database.canonical"],
        "writeClass": "canonical_db_apply",
        "notes": "Backend CLI is the sole production controller for migrate/import/printing apply.",
    },
    "universe_materialize": {
        "surface": "pipelines/universe_authority.py",
        "controller": "scripts/backend.py",
        "toolIds": ["universe-authority"],
        "nodeIds": ["authority.universe"],
        "writeClass": "universe_lock_materialize",
        "notes": "Materialize is explicit via backend; build/status are read-only.",
    },
    "snapshot_assembly": {
        "surface": "pipelines/canonical_public_snapshot.py",
        "controller": "pipelines/run_daily.py",
        "toolIds": ["snapshot-export"],
        "nodeIds": ["export.sanitized-snapshot"],
        "writeClass": "sanitized_snapshot_export",
        "notes": "Daily orchestrator owns production snapshot assembly after QC gates.",
    },
    "official_publisher_pointer": {
        "surface": "pipelines/publish-snapshot.mjs",
        "controller": "pipelines/run_daily.py",
        "toolIds": ["official-publisher"],
        "nodeIds": ["publisher.official", "state.last-good"],
        "writeClass": "generation_and_pointer_promotion",
        "notes": "Only official-publisher may advance generation/pointer; run_daily is the controller.",
    },
    "pointer_promote_helper": {
        "surface": "pipelines/promote-staging-pointer.mjs",
        "controller": "pipelines/publish-snapshot.mjs",
        "toolIds": ["official-publisher"],
        "nodeIds": ["publisher.official", "state.last-good"],
        "writeClass": "pointer_promotion_helper",
        "notes": "Helper invoked only under official publisher promote command contract.",
    },
    "daily_orchestrator": {
        "surface": "pipelines/run_daily.py",
        "controller": "scripts/backend.py",
        "toolIds": [],
        "nodeIds": ["orchestrator.daily"],
        "writeClass": "daily_control_flow",
        "notes": "backend.py daily/full-backfill/qualified-sync is the sole production controller.",
    },
    "project_state_render": {
        "surface": "scripts/render_project_state.py",
        "controller": "scripts/backend.py",
        "toolIds": ["project-state-render"],
        "nodeIds": ["status.machine"],
        "writeClass": "project_state_markdown",
        "notes": "Rewrites PROJECT_STATE.md only; no DB/pointer/timer mutation.",
    },
    "scheduler_timer_units": {
        "surface": "deploy/systemd",
        "controller": "deploy/systemd/README.md",
        "toolIds": [],
        "nodeIds": ["orchestrator.daily"],
        "writeClass": "timer_unit_definition",
        "notes": "Timer/unit files are ops-owned; code must not enable timers without approval gate.",
        "surfaceIsDirectory": True,
    },
}

MAIN_GUARD_RE = re.compile(
    r"""if\s+__name__\s*==\s*['"]__main__['"]""",
    re.MULTILINE,
)
PATH_TOKEN_RE = re.compile(
    r"""(?P<path>(?:scripts|pipelines|integrations|deploy|apps/web/scripts)/[A-Za-z0-9_./\\-]+\.(?:py|mjs|js|cjs|ps1|sh|bat|cmd))""",
    re.IGNORECASE,
)


def posix_rel(path: Path, root: Path = ROOT) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def load_registry(path: Path = DEFAULT_ROUTES) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document.get("tools"), list):
        raise ValueError("data-routing tools must be an array")
    return document


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_skipped_dir(path: Path) -> bool:
    return any(part in SKIP_DIR_NAMES for part in path.parts)


def is_execution_denied(rel: str) -> bool:
    normalized = rel.replace("\\", "/").lstrip("./")
    return any(normalized == prefix.rstrip("/") or normalized.startswith(prefix) for prefix in DENIED_PREFIXES)


def has_main_guard(path: Path) -> bool:
    if path.suffix.lower() != ".py":
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return bool(MAIN_GUARD_RE.search(text))


def iter_executable_files(root: Path = ROOT) -> list[Path]:
    found: list[Path] = []
    for relative in SCOPED_ROOTS:
        base = root / relative
        if not base.exists():
            continue
        if base.is_file():
            if base.suffix.lower() in EXECUTABLE_SUFFIXES:
                found.append(base.resolve())
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            if is_skipped_dir(path):
                continue
            if path.suffix.lower() not in EXECUTABLE_SUFFIXES:
                continue
            # SQL migrations and markdown are not executables; already filtered by suffix.
            found.append(path.resolve())
    # Stable unique order
    unique = sorted({p for p in found}, key=lambda item: posix_rel(item, root).lower())
    return unique


def tool_entrypoint_index(registry: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    index: dict[str, list[dict[str, Any]]] = {}
    for tool in registry.get("tools") or []:
        if not isinstance(tool, Mapping):
            continue
        entry = str(tool.get("entrypoint") or "").replace("\\", "/")
        if not entry:
            continue
        index.setdefault(entry, []).append(dict(tool))
    return index


def profile_tool_usage(registry: Mapping[str, Any]) -> dict[str, list[str]]:
    usage: dict[str, list[str]] = {}
    for profile in registry.get("profiles") or []:
        if not isinstance(profile, Mapping):
            continue
        profile_id = str(profile.get("id") or "")
        for phase in profile.get("phases") or []:
            if not isinstance(phase, Mapping):
                continue
            phase_id = str(phase.get("id") or "")
            for tool_id in phase.get("tools") or []:
                usage.setdefault(str(tool_id), []).append(f"{profile_id}:{phase_id}")
    return usage


def load_package_scripts(root: Path = ROOT) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for relative in PACKAGE_JSON_PATHS:
        path = root / relative
        if not path.is_file():
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        scripts = document.get("scripts") or {}
        if not isinstance(scripts, Mapping):
            continue
        for name, command in scripts.items():
            records.append(
                {
                    "packageJson": relative.replace("\\", "/"),
                    "scriptName": str(name),
                    "command": str(command),
                    "path": f"{relative}#scripts.{name}",
                    "classification": "package_script",
                    "executionPolicy": "allowed_via_npm",
                    "lifecycle": "active",
                }
            )
    return records


def extract_path_references(text: str) -> set[str]:
    hits: set[str] = set()
    for match in PATH_TOKEN_RE.finditer(text.replace("\\", "/")):
        token = match.group("path").replace("\\", "/")
        # normalize accidental double slashes
        while "//" in token:
            token = token.replace("//", "/")
        hits.add(token)
    return hits


def build_static_call_edges(
    *,
    root: Path,
    executable_rels: Sequence[str],
) -> list[dict[str, str]]:
    """Evidence-grade static path edges (rg-style). Never invents missing targets."""

    known = set(executable_rels)
    # Also allow directory targets for scheduler units
    edges: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    # Controllers and wrappers that own invocation edges
    scan_paths = [
        root / "scripts" / "backend.py",
        root / "pipelines" / "run_daily.py",
        root / "pipelines" / "run_daily.ps1",
        root / "pipelines" / "publish-snapshot.mjs",
        root / "pipelines" / "promote-staging-pointer.mjs",
        root / "scripts" / "run-python.mjs",
        *sorted((root / "deploy").rglob("*") if (root / "deploy").exists() else []),
        *sorted((root / "scripts").glob("*.ps1")),
        *sorted((root / "scripts").glob("*.sh")),
        *sorted((root / "pipelines").glob("*.ps1")),
        *sorted((root / "pipelines").glob("*.mjs")),
    ]
    for path in scan_paths:
        if not path.is_file() or is_skipped_dir(path):
            continue
        if path.suffix.lower() not in EXECUTABLE_SUFFIXES and path.suffix.lower() not in {".service", ".timer", ".md", ".json"}:
            # still read deploy unit files
            if path.suffix.lower() not in {".service", ".timer", ".md", ".json", ".xml"}:
                continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        caller = posix_rel(path, root)
        for target in sorted(extract_path_references(text)):
            if target == caller:
                continue
            if target not in known and not (root / target).exists():
                continue
            key = (caller, target)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                {
                    "from": caller,
                    "to": target,
                    "kind": "static_path_reference",
                    "evidence": "source_text",
                }
            )
    return edges


def registry_call_edges(registry: Mapping[str, Any]) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for tool in registry.get("tools") or []:
        if not isinstance(tool, Mapping):
            continue
        tool_id = str(tool.get("id") or "")
        entry = str(tool.get("entrypoint") or "").replace("\\", "/")
        if not tool_id or not entry:
            continue
        edges.append(
            {
                "from": f"tool:{tool_id}",
                "to": entry,
                "kind": "registry_entrypoint",
                "evidence": "config/data-routing.json",
            }
        )
    usage = profile_tool_usage(registry)
    for tool_id, phases in usage.items():
        for phase in phases:
            edges.append(
                {
                    "from": f"profile:{phase}",
                    "to": f"tool:{tool_id}",
                    "kind": "profile_phase_tool",
                    "evidence": "config/data-routing.json",
                }
            )
    # Control-plane orchestrator edges
    edges.extend(
        [
            {
                "from": "scripts/backend.py",
                "to": "pipelines/run_daily.py",
                "kind": "orchestrator_invoke",
                "evidence": "scripts/backend.py DAILY_RUNTIME_PATH",
            },
            {
                "from": "deploy/systemd/run-cardz-daily.sh",
                "to": "scripts/backend.py",
                "kind": "scheduler_invoke",
                "evidence": "deploy/systemd/run-cardz-daily.sh",
            },
            {
                "from": "pipelines/run_daily.ps1",
                "to": "pipelines/run_daily.py",
                "kind": "wrapper_invoke",
                "evidence": "pipelines/run_daily.ps1",
            },
            {
                "from": "package.json#scripts.snapshot:publish",
                "to": "pipelines/publish-snapshot.mjs",
                "kind": "npm_script_invoke",
                "evidence": "package.json",
            },
        ]
    )
    return edges


def package_script_edges(package_scripts: Sequence[Mapping[str, Any]], root: Path = ROOT) -> list[dict[str, str]]:
    edges: list[dict[str, str]] = []
    for record in package_scripts:
        command = str(record.get("command") or "")
        caller = str(record.get("path") or "")
        for token in extract_path_references(command):
            if (root / token).exists() or token.startswith(("scripts/", "pipelines/", "apps/")):
                edges.append(
                    {
                        "from": caller,
                        "to": token,
                        "kind": "npm_script_path",
                        "evidence": str(record.get("packageJson") or "package.json"),
                    }
                )
        # node/python bare script references: "node pipelines/foo.mjs"
        for match in re.finditer(
            r"""(?:node|python(?:3)?|tsx|npm\s+run)\s+(?:-X\s+utf8\s+)?(?P<p>[\w./\\-]+\.(?:py|mjs|js|cjs))""",
            command,
        ):
            target = match.group("p").replace("\\", "/")
            edges.append(
                {
                    "from": caller,
                    "to": target,
                    "kind": "npm_script_command",
                    "evidence": str(record.get("packageJson") or "package.json"),
                }
            )
    return edges


def classify_path(
    rel: str,
    *,
    path: Path,
    tool_index: Mapping[str, list[dict[str, Any]]],
    protected_by_surface: Mapping[str, str],
) -> dict[str, Any]:
    rel_n = rel.replace("\\", "/")
    if is_execution_denied(rel_n):
        zone = "temp" if rel_n.startswith("temp/") else "docs/evidence"
        return {
            "path": rel_n,
            "classification": "execution_denied",
            "executionPolicy": "denied",
            "lifecycle": "non_executable_zone",
            "zone": zone,
            "reason": f"{zone}/** is non-executable; agents must not run these paths",
            "hasMainGuard": has_main_guard(path) if path.suffix.lower() == ".py" else None,
            "toolIds": [],
            "roles": ["denied_executable_candidate"],
        }

    tools = tool_index.get(rel_n, [])
    tool_ids = [str(tool.get("id")) for tool in tools if tool.get("id")]
    roles: list[str] = []
    classification = "operator_utility"
    lifecycle = "active"
    execution_policy = "allowed_manual"

    if rel_n.startswith("deploy/"):
        classification = "scheduler_or_deploy_wrapper"
        execution_policy = "ops_controlled"
        roles.append("scheduler_edge")
        if rel_n.endswith((".timer", ".service")) or "/systemd/" in rel_n:
            roles.append("timer_or_unit")
    elif rel_n in {"scripts/backend.py"}:
        classification = "control_plane_cli"
        execution_policy = "allowed_controller"
        roles.extend(["orchestrator", "registered_tool_entrypoint"])
        lifecycle = "active_control_plane"
    elif rel_n in {"pipelines/run_daily.py"}:
        classification = "daily_orchestrator"
        execution_policy = "allowed_via_controller"
        roles.extend(["orchestrator", "protected_writer_surface"])
        lifecycle = "active_control_plane"
    elif tool_ids:
        classification = "registered_tool_entrypoint"
        execution_policy = "allowed_registered_tool"
        roles.append("registered_tool_entrypoint")
        lifecycle = "active_control_plane"
    elif rel_n.startswith("pipelines/") and path.suffix.lower() == ".py":
        if has_main_guard(path):
            classification = "pipeline_cli"
            execution_policy = "allowed_manual"
            roles.append("pipeline_cli")
        else:
            classification = "pipeline_library"
            execution_policy = "import_only"
            roles.append("library_module")
            lifecycle = "library"
    elif rel_n.startswith("scripts/"):
        if path.suffix.lower() in {".ps1", ".sh"}:
            classification = "operator_wrapper"
            execution_policy = "allowed_wrapper"
            roles.append("wrapper")
        elif path.suffix.lower() in {".mjs", ".js", ".cjs"}:
            classification = "operator_node_utility"
            execution_policy = "allowed_manual"
            roles.append("node_utility")
        elif has_main_guard(path):
            classification = "operator_cli"
            execution_policy = "allowed_manual"
            roles.append("operator_cli")
        else:
            classification = "operator_library"
            execution_policy = "import_only"
            roles.append("library_module")
            lifecycle = "library"
    elif rel_n.startswith("integrations/"):
        if tool_ids:
            classification = "registered_tool_entrypoint"
            execution_policy = "allowed_registered_tool"
        elif has_main_guard(path) or path.suffix.lower() in {".bat", ".ps1", ".sh"}:
            classification = "integration_entrypoint"
            execution_policy = "allowed_manual"
            roles.append("integration")
        else:
            classification = "integration_library"
            execution_policy = "import_only"
            roles.append("library_module")
            lifecycle = "library"
    elif rel_n.startswith("apps/web/scripts/"):
        classification = "web_build_script"
        execution_policy = "allowed_via_npm"
        roles.append("web_build")

    writer_id = protected_by_surface.get(rel_n)
    if writer_id:
        roles.append("protected_writer_surface")

    # Controllers
    controller_of = [
        writer_id
        for writer_id, meta in PROTECTED_WRITERS.items()
        if str(meta.get("controller") or "").replace("\\", "/") == rel_n
    ]
    if controller_of:
        roles.append("protected_writer_controller")

    return {
        "path": rel_n,
        "classification": classification,
        "executionPolicy": execution_policy,
        "lifecycle": lifecycle,
        "hasMainGuard": has_main_guard(path) if path.suffix.lower() == ".py" else None,
        "toolIds": tool_ids,
        "roles": sorted(set(roles)),
        "protectedWriterId": writer_id,
        "controlsWriterIds": controller_of,
        "suffix": path.suffix.lower(),
    }


def build_protected_writer_map(
    *,
    tool_index: Mapping[str, list[dict[str, Any]]],
    inventory_by_path: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    writers: dict[str, Any] = {}
    controller_counts: dict[str, int] = {}
    violations: list[str] = []

    for writer_id, meta in PROTECTED_WRITERS.items():
        surface = str(meta["surface"]).replace("\\", "/")
        controller = str(meta["controller"]).replace("\\", "/")
        surface_is_dir = bool(meta.get("surfaceIsDirectory"))
        surface_exists = (ROOT / surface).exists()
        controller_exists = (ROOT / controller).exists()
        tool_ids = list(meta.get("toolIds") or [])
        record = {
            "writerId": writer_id,
            "surface": surface,
            "controller": controller,
            "controllerCount": 1,
            "toolIds": tool_ids,
            "nodeIds": list(meta.get("nodeIds") or []),
            "writeClass": meta.get("writeClass"),
            "notes": meta.get("notes"),
            "surfaceExists": surface_exists,
            "controllerExists": controller_exists,
            "surfaceIsDirectory": surface_is_dir,
            "surfaceClassification": None
            if surface_is_dir
            else (inventory_by_path.get(surface) or {}).get("classification"),
            "controllerClassification": (inventory_by_path.get(controller) or {}).get("classification")
            if controller.endswith(tuple(EXECUTABLE_SUFFIXES))
            else "ops_document_or_directory",
        }
        writers[writer_id] = record
        controller_counts[writer_id] = 1
        if not surface_exists:
            violations.append(f"{writer_id}: missing surface {surface}")
        if not controller_exists:
            violations.append(f"{writer_id}: missing controller {controller}")
        # Exactly one controller by construction; re-check for empty/blank
        if not controller:
            violations.append(f"{writer_id}: controller is empty")
            controller_counts[writer_id] = 0

    # Ensure no two controllers claim the same writer id (map key uniqueness)
    if len(writers) != len(PROTECTED_WRITERS):
        violations.append("writer map size drift")

    # Each protected surface path maps to at most one writer id (except shared helper notes)
    surface_owners: dict[str, list[str]] = {}
    for writer_id, meta in writers.items():
        surface_owners.setdefault(str(meta["surface"]), []).append(writer_id)
    # allow directory surfaces only once
    for surface, owners in surface_owners.items():
        if len(owners) > 1:
            violations.append(f"surface {surface} has multiple writer ids: {owners}")

    multi = {wid: count for wid, count in controller_counts.items() if count != 1}
    if multi:
        violations.append(f"controller count != 1: {multi}")

    return {
        "schemaVersion": "1.0",
        "protectedWriters": writers,
        "acceptance": {
            "eachProtectedWriterHasExactlyOneController": not multi and not any(
                "controller" in item for item in violations
            ),
            "controllerCounts": controller_counts,
            "violations": violations,
        },
    }


def build_scheduler_map(root: Path = ROOT) -> dict[str, Any]:
    edges: list[dict[str, Any]] = []
    deploy = root / "deploy"
    if deploy.exists():
        for path in sorted(deploy.rglob("*")):
            if not path.is_file() or is_skipped_dir(path):
                continue
            rel = posix_rel(path, root)
            suffix = path.suffix.lower()
            kind = "deploy_file"
            if suffix == ".timer":
                kind = "systemd_timer"
            elif suffix == ".service":
                kind = "systemd_service"
            elif suffix in {".sh", ".ps1", ".bat", ".cmd"}:
                kind = "scheduler_wrapper"
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                text = ""
            targets = sorted(extract_path_references(text))
            # Common backend invocations without full path prefix
            if "backend.py" in text:
                targets = sorted(set(targets) | {"scripts/backend.py"})
            if "run_daily.py" in text:
                targets = sorted(set(targets) | {"pipelines/run_daily.py"})
            edges.append(
                {
                    "path": rel,
                    "kind": kind,
                    "invokes": targets,
                    "executionPolicy": "ops_controlled",
                }
            )
    # Windows task installers
    for path in [
        root / "pipelines" / "run_daily.ps1",
        root / "pipelines" / "daily-scheduler.example.json",
        root / "integrations" / "grade10" / "run_daily.bat",
    ]:
        if not path.is_file():
            continue
        rel = posix_rel(path, root)
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""
        edges.append(
            {
                "path": rel,
                "kind": "local_scheduler_or_example",
                "invokes": sorted(extract_path_references(text) | (
                    {"pipelines/run_daily.py"} if "run_daily" in text else set()
                ) | (
                    {"integrations/grade10/run_service.py"} if "run_service" in text else set()
                )),
                "executionPolicy": "ops_controlled",
            }
        )
    return {"schemaVersion": "1.0", "schedulerEdges": edges}


def build_inventory(root: Path = ROOT, registry_path: Path = DEFAULT_ROUTES) -> dict[str, Any]:
    registry = load_registry(registry_path)
    tool_index = tool_entrypoint_index(registry)
    usage = profile_tool_usage(registry)

    protected_by_surface = {
        str(meta["surface"]).replace("\\", "/"): writer_id
        for writer_id, meta in PROTECTED_WRITERS.items()
        if not meta.get("surfaceIsDirectory")
    }

    files = iter_executable_files(root)
    entries: list[dict[str, Any]] = []
    for path in files:
        rel = posix_rel(path, root)
        entry = classify_path(
            rel,
            path=path,
            tool_index=tool_index,
            protected_by_surface=protected_by_surface,
        )
        # Attach profile usage when registered
        profiles: list[str] = []
        for tool_id in entry.get("toolIds") or []:
            profiles.extend(usage.get(tool_id) or [])
        entry["profilePhases"] = sorted(set(profiles))
        entries.append(entry)

    package_scripts = load_package_scripts(root)
    inventory_by_path = {str(item["path"]): item for item in entries}

    # Every registry tool entrypoint must be present and classified
    missing_tool_entrypoints: list[str] = []
    for entrypoint, tools in tool_index.items():
        if entrypoint not in inventory_by_path:
            # Tool entrypoint outside SCOPED_ROOTS — still classify it
            path = root / entrypoint
            if path.is_file():
                entry = classify_path(
                    entrypoint,
                    path=path,
                    tool_index=tool_index,
                    protected_by_surface=protected_by_surface,
                )
                entries.append(entry)
                inventory_by_path[entrypoint] = entry
            else:
                missing_tool_entrypoints.append(entrypoint)

    # Re-sort after possible additions
    entries.sort(key=lambda item: str(item["path"]).lower())
    inventory_by_path = {str(item["path"]): item for item in entries}

    executable_rels = [str(item["path"]) for item in entries]
    call_edges = (
        registry_call_edges(registry)
        + package_script_edges(package_scripts, root)
        + build_static_call_edges(root=root, executable_rels=executable_rels)
    )
    # de-dupe edges
    deduped: list[dict[str, str]] = []
    seen_edges: set[tuple[str, str, str]] = set()
    for edge in call_edges:
        key = (edge.get("from", ""), edge.get("to", ""), edge.get("kind", ""))
        if key in seen_edges:
            continue
        seen_edges.add(key)
        deduped.append(edge)
    deduped.sort(key=lambda item: (item["from"], item["to"], item["kind"]))

    protected = build_protected_writer_map(
        tool_index=tool_index,
        inventory_by_path=inventory_by_path,
    )
    scheduler = build_scheduler_map(root)

    unclassified = [
        item["path"]
        for item in entries
        if not item.get("classification")
    ]
    denied = [item for item in entries if item.get("classification") == "execution_denied"]
    denied_not_policy = [
        item["path"]
        for item in denied
        if item.get("executionPolicy") != "denied"
    ]
    temp_or_evidence_allowed = [
        item["path"]
        for item in entries
        if is_execution_denied(str(item["path"])) and item.get("executionPolicy") != "denied"
    ]

    controller_ok = bool(protected["acceptance"]["eachProtectedWriterHasExactlyOneController"])
    controller_counts = protected["acceptance"]["controllerCounts"]
    multi_controller = {k: v for k, v in controller_counts.items() if v != 1}

    acceptance = {
        "unclassifiedExecutablesEqualZero": len(unclassified) == 0,
        "unclassified": unclassified,
        "tempAndEvidenceExecutionDenied": len(temp_or_evidence_allowed) == 0 and len(denied_not_policy) == 0,
        "tempOrEvidenceNotDenied": temp_or_evidence_allowed,
        "eachProtectedWriterHasExactlyOneController": controller_ok and not multi_controller,
        "multiControllerWriters": multi_controller,
        "missingToolEntrypoints": missing_tool_entrypoints,
        "protectedWriterViolations": list(protected["acceptance"]["violations"]),
        "pass": False,
    }
    acceptance["pass"] = (
        acceptance["unclassifiedExecutablesEqualZero"]
        and acceptance["tempAndEvidenceExecutionDenied"]
        and acceptance["eachProtectedWriterHasExactlyOneController"]
        and not missing_tool_entrypoints
        and not protected["acceptance"]["violations"]
    )

    counts = {
        "executables": len(entries),
        "packageScripts": len(package_scripts),
        "executionDenied": len(denied),
        "registeredToolEntrypoints": sum(1 for item in entries if item.get("toolIds")),
        "callEdges": len(deduped),
        "protectedWriters": len(PROTECTED_WRITERS),
        "byClassification": {},
    }
    by_class: dict[str, int] = {}
    for item in entries:
        key = str(item.get("classification") or "unclassified")
        by_class[key] = by_class.get(key, 0) + 1
    counts["byClassification"] = dict(sorted(by_class.items()))

    return {
        "schemaVersion": "1.0",
        "kind": "cardz-script-lifecycle-registry",
        "workItemId": "QC-A04-SCRIPT-LIFECYCLE",
        "agentId": "A04",
        "root": str(root.resolve()),
        "routingPath": posix_rel(registry_path, root) if registry_path.is_relative_to(root) else str(registry_path),
        "counts": counts,
        "acceptance": acceptance,
        "executables": entries,
        "packageScripts": package_scripts,
        "callGraph": {
            "schemaVersion": "1.0",
            "edges": deduped,
        },
        "protectedWriters": protected,
        "scheduler": scheduler,
        "deniedPrefixes": list(DENIED_PREFIXES),
        "scopedRoots": list(SCOPED_ROOTS),
    }


def write_json(path: Path, document: Mapping[str, Any]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")
    return sha256_text(text)


def export_artifacts(inventory: Mapping[str, Any], output_root: Path) -> dict[str, str]:
    output_root.mkdir(parents=True, exist_ok=True)
    lifecycle_path = output_root / "script-lifecycle-registry.json"
    call_graph_path = output_root / "call-graph.json"
    protected_path = output_root / "protected-writer-map.json"

    lifecycle_doc = {
        "schemaVersion": inventory["schemaVersion"],
        "kind": inventory["kind"],
        "workItemId": inventory["workItemId"],
        "agentId": inventory["agentId"],
        "counts": inventory["counts"],
        "acceptance": inventory["acceptance"],
        "deniedPrefixes": inventory["deniedPrefixes"],
        "scopedRoots": inventory["scopedRoots"],
        "executables": inventory["executables"],
        "packageScripts": inventory["packageScripts"],
        "scheduler": inventory["scheduler"],
        "callGraphEmbedded": True,
    }
    call_graph_doc = inventory["callGraph"]
    protected_doc = inventory["protectedWriters"]

    hashes = {
        "script-lifecycle-registry.json": write_json(lifecycle_path, lifecycle_doc),
        "call-graph.json": write_json(call_graph_path, call_graph_doc),
        "protected-writer-map.json": write_json(protected_path, protected_doc),
    }
    return hashes


def validate_inventory(inventory: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    acceptance = inventory.get("acceptance") or {}
    if not acceptance.get("unclassifiedExecutablesEqualZero"):
        errors.append(f"unclassified executables: {acceptance.get('unclassified')}")
    if not acceptance.get("tempAndEvidenceExecutionDenied"):
        errors.append(f"temp/evidence not denied: {acceptance.get('tempOrEvidenceNotDenied')}")
    if not acceptance.get("eachProtectedWriterHasExactlyOneController"):
        errors.append(
            f"protected writer controller violations: {acceptance.get('multiControllerWriters')} "
            f"{acceptance.get('protectedWriterViolations')}"
        )
    missing = acceptance.get("missingToolEntrypoints") or []
    if missing:
        errors.append(f"missing tool entrypoints: {missing}")
    for item in inventory.get("executables") or []:
        if not item.get("classification"):
            errors.append(f"missing classification: {item.get('path')}")
        if is_execution_denied(str(item.get("path") or "")) and item.get("executionPolicy") != "denied":
            errors.append(f"denied zone not denied: {item.get('path')}")
    writers = (inventory.get("protectedWriters") or {}).get("protectedWriters") or {}
    for writer_id, meta in writers.items():
        if int(meta.get("controllerCount") or 0) != 1:
            errors.append(f"{writer_id} controllerCount != 1")
        if not meta.get("controller"):
            errors.append(f"{writer_id} missing controller")
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CARDZ script lifecycle inventory")
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root",
    )
    parser.add_argument(
        "--routes",
        type=Path,
        default=DEFAULT_ROUTES,
        help="path to data-routing.json",
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=None,
        help="write lifecycle/call-graph/protected-writer JSON artifacts",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print full inventory JSON to stdout",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when acceptance fails",
    )
    args = parser.parse_args(argv)

    inventory = build_inventory(root=args.root.resolve(), registry_path=args.routes.resolve())
    errors = validate_inventory(inventory)

    artifact_hashes: dict[str, str] = {}
    if args.output_root is not None:
        artifact_hashes = export_artifacts(inventory, args.output_root.resolve())

    if args.json:
        print(json.dumps(inventory, indent=2, sort_keys=True, ensure_ascii=False))
    else:
        summary = {
            "counts": inventory["counts"],
            "acceptance": inventory["acceptance"],
            "artifactSha256": artifact_hashes,
            "errors": errors,
        }
        print(json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False))

    if args.check and errors:
        for error in errors:
            print(f"ERROR: {error}", file=__import__("sys").stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
