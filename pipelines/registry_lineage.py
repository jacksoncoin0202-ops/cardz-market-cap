"""Render and validate the CARDZ machine-readable control-plane registry."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

from data_cleaning_rules import DataCleaningRuleError, explain_rule as explain_cleaning_rule, load_and_validate as load_cleaning_rules


ROOT = Path(__file__).resolve().parents[1]


class RegistryError(ValueError):
    """Raised when control-plane metadata cannot be executed or explained."""


def _named(items: object, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise RegistryError(f"{label} must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get(key), str) or not item[key]:
            raise RegistryError(f"{label} entries require {key}")
        if item[key] in result:
            raise RegistryError(f"duplicate {label} identifier: {item[key]}")
        result[item[key]] = item
    return result


def _references(document: Mapping[str, Any], key: str, ids: object, label: str) -> list[dict[str, Any]]:
    available = _named(document.get(key), "id", key)
    if not isinstance(ids, list) or not all(isinstance(item, str) and item for item in ids):
        raise RegistryError(f"{label} requires {key} references")
    unresolved = [item for item in ids if item not in available]
    if unresolved:
        raise RegistryError(f"{label} references unknown {key}: {unresolved}")
    return [available[item] for item in ids]


def _tool_metadata(document: Mapping[str, Any], tool: Mapping[str, Any]) -> dict[str, Any]:
    defaults = document.get("toolDefaults")
    if not isinstance(defaults, dict):
        raise RegistryError("toolDefaults must be an object")
    metadata = dict(defaults)
    metadata.update({key: value for key, value in tool.items() if key in {
        "operatingSystems", "dependencies", "timeoutSeconds", "sideEffects", "manualRefs", "testRefs"
    }})
    return metadata


def _validate_architecture(document: Mapping[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    architecture = document.get("architecture")
    if not isinstance(architecture, Mapping) or architecture.get("entrypoint") != "config/data-routing.json":
        raise RegistryError("architecture must declare config/data-routing.json as its single entrypoint")
    nodes = _named(architecture.get("nodes"), "id", "architecture nodes")
    edges = _named(architecture.get("edges"), "id", "architecture edges")
    tools = _named(document.get("tools"), "id", "tools")
    routes = _named(document.get("routes"), "metric", "routes")
    profiles = _named(document.get("profiles"), "id", "profiles")
    consumers = _named(document.get("consumers"), "id", "consumers")
    allowed_kinds = {
        "authority", "transport", "collector", "normalizer", "validator", "landing",
        "database", "derivation", "export", "consumer", "orchestrator",
        "review_queue", "checkpoint", "code_index",
    }
    reference_sets = {
        "toolIds": tools,
        "metricIds": routes,
        "profileIds": profiles,
        "consumerIds": consumers,
    }
    for node_id, node in nodes.items():
        if node.get("kind") not in allowed_kinds:
            raise RegistryError(f"architecture node {node_id} has invalid kind")
        refs = node.get("refs")
        if not isinstance(refs, Mapping):
            raise RegistryError(f"architecture node {node_id} requires refs")
        for ref_key, available in reference_sets.items():
            values = refs.get(ref_key, [])
            if not isinstance(values, list) or not all(isinstance(value, str) and value for value in values):
                raise RegistryError(f"architecture node {node_id} has invalid {ref_key}")
            unresolved = [value for value in values if value not in available]
            if unresolved:
                raise RegistryError(f"architecture node {node_id} references unknown {ref_key}: {unresolved}")
        database_targets = refs.get("databaseTargets", [])
        if not isinstance(database_targets, list) or not all(
            isinstance(value, str) and value for value in database_targets
        ):
            raise RegistryError(f"architecture node {node_id} has invalid databaseTargets")
    for edge_id, edge in edges.items():
        if edge.get("from") not in nodes or edge.get("to") not in nodes:
            raise RegistryError(f"architecture edge {edge_id} references unknown node")
        if not isinstance(edge.get("relation"), str) or not edge["relation"]:
            raise RegistryError(f"architecture edge {edge_id} requires relation")

    work_items = _named(document.get("workItems"), "id", "work items")
    for task_id, task in work_items.items():
        node_ids = task.get("nodeIds")
        dependencies = task.get("dependsOn")
        if not isinstance(node_ids, list) or not node_ids or any(node_id not in nodes for node_id in node_ids):
            raise RegistryError(f"work item {task_id} references unknown architecture node")
        if not isinstance(dependencies, list) or any(dep not in work_items or dep == task_id for dep in dependencies):
            raise RegistryError(f"work item {task_id} has invalid dependencies")
        for field in ("inputs", "outputs", "acceptance", "ownerFiles"):
            value = task.get(field)
            if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
                raise RegistryError(f"work item {task_id} requires {field}")
        if task.get("priority") not in {"P0", "P1", "P2"}:
            raise RegistryError(f"work item {task_id} has invalid priority")
        if task.get("status") not in {"planned", "in_progress", "blocked", "completed"}:
            raise RegistryError(f"work item {task_id} has invalid status")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(task_id: str) -> None:
        if task_id in visiting:
            raise RegistryError("work item dependency graph contains a cycle")
        if task_id in visited:
            return
        visiting.add(task_id)
        for dependency in work_items[task_id]["dependsOn"]:
            visit(dependency)
        visiting.remove(task_id)
        visited.add(task_id)

    for task_id in work_items:
        visit(task_id)
    return nodes, work_items


def _route_lineage(document: Mapping[str, Any], route: Mapping[str, Any]) -> dict[str, Any]:
    tools = _named(document["tools"], "id", "tools")
    defaults = document.get("routeDefaults")
    if not isinstance(defaults, dict):
        raise RegistryError("routeDefaults must be an object")
    collector_id = route.get("collectorTool")
    normalizer_id = route.get("normalizerTool", defaults.get("normalizerTool"))
    importer_id = route.get("importerTool", defaults.get("importerTool"))
    for label, tool_id in (("collector", collector_id), ("normalizer", normalizer_id), ("importer", importer_id)):
        if not isinstance(tool_id, str) or tool_id not in tools:
            raise RegistryError(f"route {route.get('metric')} has unknown {label} tool: {tool_id}")
    collector = tools[collector_id]
    metadata = _tool_metadata(document, collector)
    manual_refs = route.get("manualRefs", metadata.get("manualRefs"))
    test_refs = route.get("testRefs", metadata.get("testRefs"))
    manuals = _references(document, "manuals", manual_refs, f"route {route.get('metric')}")
    tests = _references(document, "tests", test_refs, f"route {route.get('metric')}")
    return {
        "metric": route["metric"],
        "snapshotFields": route["snapshotFields"],
        "databaseTarget": route["databaseTarget"],
        "importer": importer_id,
        "normalizer": normalizer_id,
        "collector": collector_id,
        "collectorEntrypoint": collector["entrypoint"],
        "transport": route.get("transport") or route.get("primary"),
        "authority": route.get("authority", route.get("primary")),
        "manuals": [{"id": item["id"], "path": item["path"]} for item in manuals],
        "tests": [{"id": item["id"], "path": item["path"]} for item in tests],
    }


def validate_control_plane(document: Mapping[str, Any]) -> None:
    if document.get("schemaVersion") != "3.0":
        raise RegistryError("control-plane schemaVersion must be 3.0")
    rules = document.get("rules")
    if not isinstance(rules, dict):
        raise RegistryError("rules must be an object")
    bands = rules.get("populationBands")
    if not isinstance(bands, dict) or bands.get("formalRanking", {}).get("minimumPsa10Population") != 1000:
        raise RegistryError("formal ranking must require PSA 10 population >=1000")
    if bands.get("preEntryRadar", {}).get("minimumExclusivePsa10Population") != 970 or bands.get("preEntryRadar", {}).get("maximumExclusivePsa10Population") != 1000:
        raise RegistryError("pre-entry radar must be PSA 10 population 971-999")
    if bands.get("discoveryOnly", {}).get("maximumInclusivePsa10Population") != 970:
        raise RegistryError("population <=970 must remain discovery-only")
    storage = document.get("storagePolicy")
    if not isinstance(storage, dict) or storage.get("canonicalRankingStorageLimit") is not None:
        raise RegistryError("canonical ranking storage must not have a hard limit")
    if storage.get("rawPayloadPolicy") != "private_pointer_only":
        raise RegistryError("raw payload policy must remain private_pointer_only")

    cleaning_policy = document.get("dataCleaningPolicy")
    if not isinstance(cleaning_policy, Mapping):
        raise RegistryError("dataCleaningPolicy must be an object")
    rules_path = cleaning_policy.get("rulesPath")
    schema_path = cleaning_policy.get("schemaPath")
    if not isinstance(rules_path, str) or not isinstance(schema_path, str):
        raise RegistryError("dataCleaningPolicy requires rulesPath and schemaPath")
    try:
        cleaning_rules = load_cleaning_rules(ROOT / rules_path, ROOT / schema_path)
    except (DataCleaningRuleError, OSError, json.JSONDecodeError) as error:
        raise RegistryError(f"data-cleaning rules invalid: {error}") from error
    if cleaning_rules["populationBands"]["formalRanking"]["minimumInclusivePsa10Population"] != bands["formalRanking"]["minimumPsa10Population"]:
        raise RegistryError("data-cleaning formal POP gate must match routing registry")
    if cleaning_rules["populationBands"]["preEntryRadar"]["minimumInclusivePsa10Population"] != bands["preEntryRadar"]["minimumExclusivePsa10Population"] + 1:
        raise RegistryError("data-cleaning pre-entry lower bound must match routing registry")
    if cleaning_rules["populationBands"]["preEntryRadar"]["maximumInclusivePsa10Population"] != bands["preEntryRadar"]["maximumExclusivePsa10Population"] - 1:
        raise RegistryError("data-cleaning pre-entry upper bound must match routing registry")

    ranking = document.get("rankingPolicy")
    if not isinstance(ranking, dict) or ranking.get("languagePartitioning") is not False:
        raise RegistryError("ranking policy must keep language metadata without language leaderboards")
    indexes = _named(ranking.get("indexes"), "id", "ranking indexes")
    if set(indexes) != {"tcg", "pokemon", "one-piece"}:
        raise RegistryError("ranking indexes must be tcg, pokemon, and one-piece")
    if any(index.get("storageLimit") is not None for index in indexes.values()):
        raise RegistryError("ranking indexes cannot cap canonical storage")
    if "psa10_population>=1000" not in str(ranking.get("eligibility")):
        raise RegistryError("ranking eligibility must include PSA 10 population >=1000")

    views = _named(document.get("presentationViews"), "id", "presentation views")
    for required in ("top100", "top300", "top350", "top100_plus_200", "reserve50"):
        if required not in views:
            raise RegistryError(f"missing presentation view: {required}")
    if views["top100_plus_200"].get("aliasOf") != "top300":
        raise RegistryError("top100_plus_200 must alias top300")
    if views["reserve50"].get("rankStart") != 301 or views["reserve50"].get("rankEnd") != 350:
        raise RegistryError("reserve50 must describe ranks 301 through 350")

    manuals = _named(document.get("manuals"), "id", "manuals")
    tests = _named(document.get("tests"), "id", "tests")
    for label, items in (("manual", manuals), ("test", tests)):
        for item_id, item in items.items():
            path = item.get("path")
            if not isinstance(path, str) or not (ROOT / path).is_file():
                raise RegistryError(f"{label} {item_id} path does not exist: {path}")
    tools = _named(document.get("tools"), "id", "tools")
    consumers = _named(document.get("consumers"), "id", "consumers")
    profiles = _named(document.get("profiles"), "id", "profiles")
    routes = _named(document.get("routes"), "metric", "routes")
    for metric, route in routes.items():
        fields = route.get("snapshotFields")
        if not isinstance(fields, list) or not fields or not all(isinstance(field, str) and field for field in fields):
            raise RegistryError(f"route {metric} requires snapshotFields")
    for tool_id, tool in tools.items():
        if not isinstance(tool.get("entrypoint"), str) or not tool["entrypoint"]:
            raise RegistryError(f"tool {tool_id} requires entrypoint")
        if not (ROOT / tool["entrypoint"]).is_file():
            raise RegistryError(f"tool {tool_id} entrypoint does not exist: {tool['entrypoint']}")
        for metric in tool.get("routeMetrics", []):
            if metric not in routes:
                raise RegistryError(f"tool {tool_id} references unknown route metric: {metric}")
        for consumer in tool.get("consumers", []):
            if consumer not in consumers:
                raise RegistryError(f"tool {tool_id} references unknown consumer: {consumer}")
        metadata = _tool_metadata(document, tool)
        if metadata.get("operatingSystems") != ["windows", "linux", "aws"]:
            raise RegistryError(f"tool {tool_id} must declare Windows, Linux and AWS support")
        if not isinstance(metadata.get("dependencies"), list) or not metadata["dependencies"]:
            raise RegistryError(f"tool {tool_id} requires dependencies")
        if not isinstance(metadata.get("timeoutSeconds"), int) or metadata["timeoutSeconds"] <= 0:
            raise RegistryError(f"tool {tool_id} requires a positive timeoutSeconds")
        if not isinstance(metadata.get("sideEffects"), str) or not metadata["sideEffects"]:
            raise RegistryError(f"tool {tool_id} requires sideEffects")
        _references(document, "manuals", metadata.get("manualRefs"), f"tool {tool_id}")
        _references(document, "tests", metadata.get("testRefs"), f"tool {tool_id}")
    for metric, route in routes.items():
        lineage = _route_lineage(document, route)
        if metric not in tools[lineage["collector"]].get("routeMetrics", []):
            raise RegistryError(f"route {metric} collector {lineage['collector']} does not declare the metric")
    for profile_id, profile in profiles.items():
        phases = profile.get("phases")
        if not isinstance(phases, list) or not phases:
            raise RegistryError(f"profile {profile_id} requires ordered phases")
        for phase in phases:
            if not isinstance(phase, dict) or not isinstance(phase.get("id"), str):
                raise RegistryError(f"profile {profile_id} has invalid phase")
            for tool in phase.get("tools", []):
                if tool not in tools:
                    raise RegistryError(f"profile {profile_id} references unknown tool: {tool}")
    for required in ("full-backfill", "weekly-candidate-refresh", "daily", "export", "restore"):
        if required not in profiles:
            raise RegistryError(f"missing required execution profile: {required}")
    _validate_architecture(document)


def _control_plane(document: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    architecture_nodes, work_items = _validate_architecture(document)
    return {
        "routes": _named(document["routes"], "metric", "routes"),
        "tools": _named(document["tools"], "id", "tools"),
        "profiles": _named(document["profiles"], "id", "profiles"),
        "views": _named(document["presentationViews"], "id", "presentation views"),
        "consumers": _named(document["consumers"], "id", "consumers"),
        "manuals": _named(document["manuals"], "id", "manuals"),
        "tests": _named(document["tests"], "id", "tests"),
        "architectureNodes": architecture_nodes,
        "workItems": work_items,
    }


def explain_identifier(document: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    """Return one stable explanation payload for a metric, tool, profile, view or consumer."""

    aliases = {
        "market_cap": "psa10_market_cap",
    }
    resolved_identifier = aliases.get(identifier, identifier)
    if resolved_identifier in {"data_cleaning", "data-cleaning"}:
        policy = document["dataCleaningPolicy"]
        return {
            "identifier": identifier,
            "kind": "dataCleaningPolicy",
            "value": policy,
            "rules": explain_cleaning_rule("all", load_cleaning_rules(ROOT / policy["rulesPath"], ROOT / policy["schemaPath"])),
        }
    plane = _control_plane(document)
    for kind, items in plane.items():
        if resolved_identifier in items:
            result = {
                "identifier": identifier,
                "kind": kind[:-1] if kind.endswith("s") else kind,
                "value": items[resolved_identifier],
            }
            if resolved_identifier != identifier:
                result["resolvedIdentifier"] = resolved_identifier
            if kind == "routes":
                result["lineage"] = _route_lineage(document, items[resolved_identifier])
                cleaning_metric = {
                    "psa10_population": "psa10_population",
                    "psa10_reference_price": "psa10_reference_price",
                    "tracked_sales": "tracked_sales",
                }.get(resolved_identifier)
                if cleaning_metric:
                    policy = document["dataCleaningPolicy"]
                    result["dataCleaning"] = explain_cleaning_rule(
                        cleaning_metric,
                        load_cleaning_rules(ROOT / policy["rulesPath"], ROOT / policy["schemaPath"]),
                    )
            elif kind == "architectureNodes":
                architecture = document["architecture"]
                result["incomingEdges"] = [
                    edge for edge in architecture["edges"] if edge["to"] == resolved_identifier
                ]
                result["outgoingEdges"] = [
                    edge for edge in architecture["edges"] if edge["from"] == resolved_identifier
                ]
                result["workItems"] = [
                    task["id"] for task in document["workItems"] if resolved_identifier in task["nodeIds"]
                ]
            elif kind == "workItems":
                result["nodes"] = [
                    plane["architectureNodes"][node_id] for node_id in items[resolved_identifier]["nodeIds"]
                ]
            return result
    matching_routes = [
        route
        for route in document["routes"]
        if any(_field_path_matches(resolved_identifier, field) for field in route.get("snapshotFields", []))
    ]
    if matching_routes:
        metrics = [str(route["metric"]) for route in matching_routes]
        tools = [
            str(tool["id"])
            for tool in document["tools"]
            if any(metric in tool.get("routeMetrics", []) for metric in metrics)
        ]
        return {
            "identifier": identifier,
            "kind": "snapshotField",
            "metrics": metrics,
            "databaseTargets": sorted({str(route["databaseTarget"]) for route in matching_routes}),
            "collectors": sorted({str(route["collector"]) for route in matching_routes}),
            "tools": tools,
            "routes": matching_routes,
            "lineage": [_route_lineage(document, route) for route in matching_routes],
        }
    raise RegistryError(f"unknown registry identifier: {identifier}")


def _field_path_matches(query: str, pattern: str) -> bool:
    """Match a snapshot node or any child/parent node, preserving path boundaries."""

    query_parts = query.split(".")
    pattern_parts = pattern.split(".")
    shared = min(len(query_parts), len(pattern_parts))
    for query_part, pattern_part in zip(query_parts[:shared], pattern_parts[:shared]):
        if pattern_part.startswith("<") and pattern_part.endswith(">"):
            continue
        if query_part != pattern_part:
            return False
    return True


def render_json(document: Mapping[str, Any]) -> str:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def render_markdown(document: Mapping[str, Any]) -> str:
    _control_plane(document)
    lines = [
        "# CARDZ Backend Control Plane",
        "",
        "This file is generated from `config/data-routing.json`. Do not edit it by hand.",
        "",
        "## Architecture entrypoint",
        "",
        f"- Registry: `{document['architecture']['entrypoint']}`.",
        f"- Code index: `{document['architecture']['codeIndex']['tool']}` at `{document['architecture']['codeIndex']['database']}`.",
        "- The code index is read-only evidence. It never overrides authority, routing, storage, ranking, or work-item policy.",
        "",
        "## Storage and ranking policy",
        "",
        f"- Canonical ranking storage limit: `{document['storagePolicy']['canonicalRankingStorageLimit']}` (unbounded).",
        f"- Raw payload policy: `{document['storagePolicy']['rawPayloadPolicy']}`.",
        f"- Data-cleaning rules: `{document['dataCleaningPolicy']['rulesPath']}` (raw preserved privately; MySQL retains pointers plus typed facts).",
        f"- Ranking language partitioning: `{document['rankingPolicy']['languagePartitioning']}`.",
        f"- Formal ranking: PSA 10 POP ≥ `{document['rules']['populationBands']['formalRanking']['minimumPsa10Population']}`.",
        "- Pre-entry radar: PSA 10 POP `971–999`; POP `≤970` remains discovery-only.",
        "",
        "## Tools",
        "",
        "| ID | Phase | Entrypoint | Timeout | OS | Route metrics | Manuals | Tests |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for tool in document["tools"]:
        metadata = _tool_metadata(document, tool)
        lines.append(
            "| {id} | {phase} | `{entrypoint}` | {timeout}s | {operating_systems} | {metrics} | {manuals} | {tests} |".format(
                id=tool["id"],
                phase=tool.get("phase", "—"),
                entrypoint=tool["entrypoint"],
                timeout=metadata["timeoutSeconds"],
                operating_systems=", ".join(metadata["operatingSystems"]),
                metrics=", ".join(tool.get("routeMetrics", [])) or "—",
                manuals=", ".join(metadata["manualRefs"]),
                tests=", ".join(metadata["testRefs"]),
            )
        )
    lines.extend(["", "## Profiles", ""])
    for profile in document["profiles"]:
        lines.append(f"### `{profile['id']}`")
        lines.append("")
        for phase in profile["phases"]:
            lines.append(f"1. `{phase['id']}` — {', '.join(phase['tools'])}.")
        lines.append("")
    lines.extend([
        "## Architecture nodes",
        "",
        "| ID | Kind | Label | Tools | Metrics | DB targets |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for node in document["architecture"]["nodes"]:
        refs = node["refs"]
        lines.append(
            "| {id} | {kind} | {label} | {tools} | {metrics} | {targets} |".format(
                id=node["id"],
                kind=node["kind"],
                label=node["label"],
                tools=", ".join(refs.get("toolIds", [])) or "—",
                metrics=", ".join(refs.get("metricIds", [])) or "—",
                targets="<br>".join(refs.get("databaseTargets", [])) or "—",
            )
        )
    lines.extend([
        "",
        "## Work items",
        "",
        "| ID | Priority | Status | Architecture nodes | Depends on | Owner files |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for task in document["workItems"]:
        lines.append(
            "| {id} | {priority} | {status} | {nodes} | {dependencies} | {owners} |".format(
                id=task["id"],
                priority=task["priority"],
                status=task["status"],
                nodes="<br>".join(task["nodeIds"]),
                dependencies=", ".join(task["dependsOn"]) or "—",
                owners="<br>".join(f"`{path}`" for path in task["ownerFiles"]),
            )
        )
    lines.extend(["## Route lineage", "", "| Snapshot field | Metric | DB target | Importer | Collector | Authority / transport | Manuals | Tests |", "| --- | --- | --- | --- | --- | --- | --- | --- |"])
    for route in document["routes"]:
        lineage = _route_lineage(document, route)
        lines.append(
            "| {fields} | {metric} | `{database}` | {importer} | {collector} | {authority} / {transport} | {manuals} | {tests} |".format(
                fields="<br>".join(lineage["snapshotFields"]),
                metric=lineage["metric"],
                database=lineage["databaseTarget"],
                importer=lineage["importer"],
                collector=lineage["collector"],
                authority=lineage["authority"],
                transport=html.escape(str(lineage["transport"])),
                manuals=", ".join(item["id"] for item in lineage["manuals"]),
                tests=", ".join(item["id"] for item in lineage["tests"]),
            )
        )
    lines.extend(["", "## Manuals", "", "| ID | Path | Purpose |", "| --- | --- | --- |"])
    for manual in document["manuals"]:
        lines.append(f"| {manual['id']} | `{manual['path']}` | {manual.get('purpose', '—')} |")
    lines.extend(["## Presentation views", "", "| ID | Ranks | Meaning |", "| --- | --- | --- |"])
    for view in document["presentationViews"]:
        ranks = view.get("rankRange") or f"{view.get('rankStart', '—')}–{view.get('rankEnd', '—')}"
        lines.append(f"| {view['id']} | {ranks} | {view['description']} |")
    lines.extend(["", "## Consumers", "", "| ID | Type | Reads |", "| --- | --- | --- |"])
    for consumer in document["consumers"]:
        lines.append(f"| {consumer['id']} | {consumer['type']} | {', '.join(consumer.get('reads', [])) or '—'} |")
    return "\n".join(lines) + "\n"


def render_html_graph(document: Mapping[str, Any]) -> str:
    _control_plane(document)
    architecture = document["architecture"]
    positions = {
        "discovery.candidates": (48, 158),
        "authority.gemrate": (280, 158),
        "transport.gemrate-direct": (512, 42),
        "transport.gemrate-public": (512, 158),
        "transport.gemrate-mirror": (512, 274),
        "landing.immutable-receipts": (744, 158),
        "normalizer.identity": (976, 158),
        "crosswalk.provider-alias": (1208, 104),
        "review.identity": (1208, 248),
        "collector.snk-exact": (1440, 104),
        "database.canonical": (1672, 104),
        "validator.eligibility": (1904, 104),
        "derivation.market": (2136, 104),
        "ranking.scopes": (2368, 104),
        "export.views": (2600, 104),
        "export.sanitized-snapshot": (2832, 104),
        "index.codegraph": (48, 462),
        "orchestrator.daily": (280, 462),
        "state.last-good": (512, 462),
    }
    node_width = 192
    node_height = 82
    colors = {
        "authority": "#7c3aed",
        "transport": "#2563eb",
        "collector": "#0f766e",
        "normalizer": "#0891b2",
        "validator": "#b45309",
        "landing": "#475569",
        "database": "#1d4ed8",
        "derivation": "#047857",
        "export": "#7c3aed",
        "orchestrator": "#dc2626",
        "review_queue": "#b45309",
        "checkpoint": "#334155",
        "code_index": "#475569",
    }

    def wrap_label(value: str, width: int = 25) -> list[str]:
        words = value.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines[:2]

    edge_svg = []
    for edge in architecture["edges"]:
        source_x, source_y = positions[edge["from"]]
        target_x, target_y = positions[edge["to"]]
        x1 = source_x + node_width
        y1 = source_y + node_height / 2
        x2 = target_x
        y2 = target_y + node_height / 2
        bend = max(34, abs(x2 - x1) * 0.45)
        edge_svg.append(
            '<path class="flow{optional}" d="M {x1:.0f} {y1:.0f} C {c1:.0f} {y1:.0f}, {c2:.0f} {y2:.0f}, {x2:.0f} {y2:.0f}" '
            'marker-end="url(#arrow)"><title>{title}</title></path>'.format(
                optional=" optional" if not edge.get("required", True) else "",
                x1=x1,
                y1=y1,
                c1=x1 + bend,
                c2=x2 - bend,
                x2=x2,
                y2=y2,
                title=html.escape(str(edge["relation"])),
            )
        )
    node_svg = []
    for node in architecture["nodes"]:
        x, y = positions[node["id"]]
        label_lines = wrap_label(str(node["label"]))
        label = "".join(
            f'<tspan x="{x + 14}" dy="{0 if index == 0 else 18}">{html.escape(line)}</tspan>'
            for index, line in enumerate(label_lines)
        )
        refs = node["refs"]
        ref_values = refs.get("toolIds") or refs.get("metricIds") or refs.get("databaseTargets") or []
        reference = ", ".join(ref_values[:2])
        if len(ref_values) > 2:
            reference += f" +{len(ref_values) - 2}"
        node_svg.append(
            '<g class="node"><rect x="{x}" y="{y}" width="{width}" height="{height}" rx="12" '
            'fill="{color}" stroke="#e2e8f0" stroke-opacity=".28"/>'
            '<text class="node-kind" x="{tx}" y="{ky}">{kind}</text>'
            '<text class="node-label" x="{tx}" y="{ly}">{label}</text>'
            '<text class="node-ref" x="{tx}" y="{ry}">{reference}</text>'
            '<title>{node_id}</title></g>'.format(
                x=x,
                y=y,
                width=node_width,
                height=node_height,
                color=colors[node["kind"]],
                tx=x + 14,
                ky=y + 17,
                ly=y + 38,
                ry=y + 72,
                kind=html.escape(str(node["kind"]).replace("_", " ")),
                label=label,
                reference=html.escape(reference),
                node_id=html.escape(str(node["id"])),
            )
        )

    task_rows = "".join(
        "<tr><td><code>{id}</code></td><td><span class=\"priority {priority}\">{priority}</span></td>"
        "<td><span class=\"status {status}\">{status}</span></td><td>{title}</td><td>{nodes}</td>"
        "<td>{dependencies}</td><td>{owners}</td></tr>".format(
            id=html.escape(str(task["id"])),
            priority=html.escape(str(task["priority"])),
            status=html.escape(str(task["status"])),
            title=html.escape(str(task["title"])),
            nodes="<br>".join(html.escape(str(value)) for value in task["nodeIds"]),
            dependencies=", ".join(html.escape(str(value)) for value in task["dependsOn"]) or "—",
            owners="<br>".join(f"<code>{html.escape(str(value))}</code>" for value in task["ownerFiles"]),
        )
        for task in document["workItems"]
    )
    profile_sections = []
    for profile in document["profiles"]:
        cards = "".join(
            f"<li><strong>{html.escape(phase['id'])}</strong><span>{html.escape(', '.join(phase['tools']))}</span></li>"
            for phase in profile["phases"]
        )
        profile_sections.append(f"<article><h3>{html.escape(profile['id'])}</h3><ol>{cards}</ol></article>")
    lineage_cards = "".join(
        "<li><strong>{metric}</strong><span>{snapshot} → {collector} → {database}</span><small>{authority}</small></li>".format(
            metric=html.escape(str(lineage["metric"])),
            snapshot=html.escape(", ".join(lineage["snapshotFields"])),
            collector=html.escape(str(lineage["collector"])),
            database=html.escape(str(lineage["databaseTarget"])),
            authority=html.escape(str(lineage["authority"])),
        )
        for lineage in (_route_lineage(document, route) for route in document["routes"])
    )
    policy = document["dataCleaningPolicy"]
    cleaning = load_cleaning_rules(ROOT / policy["rulesPath"], ROOT / policy["schemaPath"])
    cleaning_cards = "".join(
        "<li><strong>{id}</strong><span>{purpose}</span><small>{storage}</small></li>".format(
            id=html.escape(str(layer["id"])),
            purpose=html.escape(str(layer["purpose"])),
            storage=html.escape(str(layer["storage"])),
        )
        for layer in cleaning["layers"]
    )
    legend = "".join(
        f'<span><i style="background:{color}"></i>{html.escape(kind.replace("_", " "))}</span>'
        for kind, color in colors.items()
    )
    return """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARDZ Backend Control Plane</title>
<style>
:root{color-scheme:dark;--bg:#020617;--panel:#0f172a;--line:#334155;--text:#e2e8f0;--muted:#94a3b8}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
header,main{width:min(1600px,calc(100% - 32px));margin:auto}header{padding:34px 0 20px}
h1,h2,h3,p{margin-top:0}h1{font-size:clamp(28px,4vw,48px);letter-spacing:-.04em}h2{font-size:22px;margin-bottom:14px}
p,.muted{color:var(--muted)}code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
.entry{border:1px solid var(--line);background:rgba(15,23,42,.72);padding:14px 18px;border-radius:12px}
.diagram-shell{overflow-x:auto;border:1px solid var(--line);border-radius:16px;background-color:#071021;
background-image:linear-gradient(rgba(148,163,184,.055) 1px,transparent 1px),linear-gradient(90deg,rgba(148,163,184,.055) 1px,transparent 1px);background-size:24px 24px}
svg{display:block;width:3072px;height:auto;min-height:610px}.flow{fill:none;stroke:#64748b;stroke-width:2.2;opacity:.88}.flow.optional{stroke-dasharray:8 7;opacity:.58}
.node-kind{fill:#cbd5e1;font-size:10px;text-transform:uppercase;letter-spacing:.11em}.node-label{fill:white;font-size:14px;font-weight:650}.node-ref{fill:#dbeafe;font-size:9px}
.legend{display:flex;flex-wrap:wrap;gap:9px 16px;padding:13px 4px 28px}.legend span{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.legend i{width:10px;height:10px;border-radius:3px}
section{margin:28px 0}.table-shell{overflow:auto;border:1px solid var(--line);border-radius:14px}table{border-collapse:collapse;width:100%;min-width:980px;background:rgba(15,23,42,.62)}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid rgba(51,65,85,.75);vertical-align:top;font-size:13px}th{color:#cbd5e1;background:#111c31;position:sticky;top:0}td{color:#b9c5d6}
.priority,.status{display:inline-block;padding:3px 7px;border-radius:999px;font-size:11px}.priority.P0{background:#7f1d1d;color:#fecaca}.priority.P1{background:#78350f;color:#fde68a}.priority.P2{background:#164e63;color:#a5f3fc}
.status.completed{background:#064e3b;color:#a7f3d0}.status.in_progress{background:#1e3a8a;color:#bfdbfe}.status.planned{background:#334155;color:#e2e8f0}.status.blocked{background:#7f1d1d;color:#fecaca}
.profiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px}.profiles article,.cards li{border:1px solid var(--line);background:rgba(15,23,42,.58);border-radius:12px;padding:14px}
.profiles ol,.cards{list-style:none;padding:0;margin:0;display:grid;gap:8px}.profiles li strong,.profiles li span,.cards strong,.cards span,.cards small{display:block}.profiles li span,.cards span,.cards small{font-size:12px;color:var(--muted);margin-top:4px}
.cards{grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}footer{padding:32px 0 46px;color:var(--muted);font-size:12px}
@media(max-width:700px){header,main{width:min(100% - 20px,1600px)}header{padding-top:24px}th,td{font-size:12px}}
</style></head><body>
<header><h1>CARDZ Backend Control Plane</h1>
<p>One handwritten architecture source; generated diagram, task board, tool catalogue, and lineage.</p>
<div class="entry"><code>config/data-routing.json</code> → generated operator views. CodeGraph is read-only code evidence.</div></header>
<main id="data-lineage-graph">
<section><h2>Architecture</h2><p class="muted">Solid arrows are required flow; dashed arrows are optional transport or explanation paths.</p>
<div class="diagram-shell"><svg viewBox="0 0 3072 610" role="img" aria-labelledby="diagram-title diagram-desc">
<title id="diagram-title">CARDZ backend architecture</title><desc id="diagram-desc">GemRate authority, private receipts, exact identity, SNK pricing, canonical database, ranking and sanitized snapshot flow.</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>
""" + "".join(edge_svg) + "".join(node_svg) + """
</svg></div><div class="legend">""" + legend + """</div></section>
<section><h2>Work items</h2><p class="muted">Bounded tasks are stored beside the architecture nodes they change.</p>
<div class="table-shell"><table><thead><tr><th>ID</th><th>Priority</th><th>Status</th><th>Task</th><th>Nodes</th><th>Depends on</th><th>Owner files</th></tr></thead><tbody>""" + task_rows + """</tbody></table></div></section>
<section><h2>Execution profiles</h2><div class="profiles">""" + "".join(profile_sections) + """</div></section>
<section><h2>Data cleaning layers</h2><ul class="cards">""" + cleaning_cards + """</ul></section>
<section><h2>Metric lineage</h2><ul class="cards">""" + lineage_cards + """</ul></section>
<footer>Generated from config/data-routing.json. Do not edit this file by hand.</footer>
</main></body></html>
"""


def generated_documents(document: Mapping[str, Any]) -> dict[str, str]:
    return {
        "TOOL_REGISTRY.md": render_markdown(document),
        "DATA_LINEAGE.html": render_html_graph(document),
        "DATA_ROUTING.json": render_json(document),
    }


def generate_docs(document: Mapping[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in generated_documents(document).items():
        path = output_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(path)
    return written


def check_docs(document: Mapping[str, Any], output_dir: Path) -> list[str]:
    drift: list[str] = []
    for name, content in generated_documents(document).items():
        path = output_dir / name
        if not path.is_file() or path.read_text(encoding="utf-8") != content:
            drift.append(name)
    return drift
