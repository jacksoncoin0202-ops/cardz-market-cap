"""Render and validate the CARDZ machine-readable control-plane registry."""

from __future__ import annotations

import html
import json
import posixpath
import re
from pathlib import Path
from typing import Any, Mapping

from data_cleaning_rules import DataCleaningRuleError, explain_rule as explain_cleaning_rule, load_and_validate as load_cleaning_rules


ROOT = Path(__file__).resolve().parents[1]


class RegistryError(ValueError):
    """Raised when control-plane metadata cannot be executed or explained."""


EXPECTED_CLAIM_POLICIES = {
    "machine_generated",
    "stable_contract_only",
    "runtime_verification_required",
    "historical_snapshot",
}
EXPECTED_INFORMATION_CLASSES = {
    "timeless_invariant": {
        "purpose": "Identity, authority, dependency order, write ownership and stop conditions that must not contain current business facts.",
        "mutability": "architecture_change_only",
        "allowedContent": [
            "authority rules",
            "role boundaries",
            "dependency DAG",
            "write claims",
            "feedback protocol",
        ],
        "forbiddenContent": [
            "live counts",
            "current prices",
            "current completion claims",
            "installed adapter availability",
            "dated handoff state",
        ],
    },
    "versioned_contract": {
        "purpose": "Schemas, formulas, reason codes and tool interfaces that remain exact for one declared version or hash.",
        "mutability": "versioned_change_control",
        "allowedContent": [
            "typed schemas",
            "migration contracts",
            "formula definitions",
            "CLI contracts",
            "QC predicates",
        ],
        "forbiddenContent": [
            "unverified runtime values",
            "silent fallback",
            "chat-only decisions",
        ],
    },
    "volatile_material": {
        "purpose": "Current counts, prices, statuses, source availability, plugin availability and fingerprints used only as input material.",
        "mutability": "registered_writer_or_fresh_read",
        "allowedContent": [
            "baseline manifests",
            "PROJECT_STATE",
            "work-item status",
            "source receipts",
            "runtime capability discovery",
        ],
        "forbiddenContent": [
            "architecture authority",
            "unversioned field semantics",
            "implicit permission",
        ],
    },
    "evidence": {
        "purpose": "Immutable observations, test output, hashes, decisions and receipts that prove or challenge a result.",
        "mutability": "append_only_or_superseding_receipt",
        "allowedContent": [
            "raw test output",
            "before and after hashes",
            "reason codes",
            "change requests",
            "QC receipts",
        ],
        "forbiddenContent": [
            "silent overwrite",
            "authority expansion",
            "unstated provenance",
        ],
    },
}
EXPECTED_DISPATCHER_INITIAL_DOCUMENTS = [
    "agent-entry",
    "document-authority-view",
    "agent-execution-funnel",
]
EXPECTED_WORKER_INITIAL_DOCUMENTS = [
    "agent-entry",
    "document-authority-view",
    "assigned_role_pack_only",
]
EXPECTED_PROGRESSIVE_LEVELS = [
    {
        "id": "L0",
        "label": "Invariant kernel",
        "informationClassId": "timeless_invariant",
        "loadRule": "always load AGENTS and document authority only",
    },
    {
        "id": "L1",
        "label": "Role route",
        "informationClassId": "timeless_invariant",
        "loadRule": "dispatcher loads funnel; worker loads exactly docs/generated/roles/<AGENT_ID>.md",
    },
    {
        "id": "L2",
        "label": "Work contract",
        "informationClassId": "versioned_contract",
        "loadRule": "load exactly backend explain <WORK_ITEM_ID> and its dependency receipts",
    },
    {
        "id": "L3",
        "label": "Department context",
        "informationClassId": "versioned_contract",
        "loadRule": "load only the role pack required documents and triggered capability routes",
    },
    {
        "id": "L4",
        "label": "Runtime material",
        "informationClassId": "volatile_material",
        "loadRule": "load only prompt-named baseline, cohort, current-state slice and input artifacts",
    },
    {
        "id": "L5",
        "label": "Evidence and feedback",
        "informationClassId": "evidence",
        "loadRule": "return exact outputs, tests, receipts, blockers and change requests",
    },
]
EXPECTED_SECTION_CLASSIFICATIONS = [
    {
        "selector": "architecture|documentAuthority|agentExecution.policy|agentExecution.roles|agentExecution.waves|agentExecution.writeClaims|agentExecution.capabilityRoutes|agentExecution.delegationPolicy|agentExecution.feedbackProtocol",
        "informationClassId": "timeless_invariant",
        "consumerRule": "may enter stable architecture and generated role packs",
    },
    {
        "selector": "rules|schemas|routes|tools|profiles|presentationViews|dataCleaningPolicy",
        "informationClassId": "versioned_contract",
        "consumerRule": "load only by referenced version, hash, node, field or tool",
    },
    {
        "selector": "workItems.status|agentExecution.assignments|PROJECT_STATE|runtime capability availability|current source values",
        "informationClassId": "volatile_material",
        "consumerRule": "dispatcher or assigned worker only; never copy into durable role contracts",
    },
    {
        "selector": "baseline manifests|source receipts|test output|QC receipts|feedback records",
        "informationClassId": "evidence",
        "consumerRule": "content-addressed exact inputs and append-only or superseding outputs",
    },
]
EXPECTED_FORBIDDEN_BULK_LOADS = [
    "all role packs",
    "all manuals",
    "all historical reports",
    "entire docs tree",
    "entire skill catalog",
    "repo-wide source before backend explain and scoped search",
]
EXPECTED_VOLATILE_MATERIAL_RULE = (
    "volatile values may be read or updated only through registered role-owned "
    "tools and evidence; they never become architecture by being copied into prose"
)
EXPECTED_DOCUMENT_CLASS_MEMBERS = {
    "timeless_invariant": {
        "agent-entry",
        "claude-compatibility-entry",
        "architecture-registry",
        "document-authority-view",
        "agent-execution-funnel",
        "agent-role-a01",
        "agent-role-a02",
        "agent-role-a03",
        "agent-role-a04",
        "agent-role-a05",
        "agent-role-a06",
        "agent-role-a07",
        "agent-role-a08",
        "agent-role-a09",
        "agent-role-a10",
        "agent-role-a11",
        "agent-role-a12",
        "agent-execution-architecture",
    },
    "versioned_contract": {
        "repository-overview",
        "tool-registry-view",
        "data-lineage-view",
        "backend-runbook",
        "data-contract",
        "canonical-printing",
        "data-cleaning-rules",
        "gemrate-source",
        "snk-manual",
        "product-positioning",
        "security-policy",
        "editorial-review",
    },
    "volatile_material": {
        "operational-state",
        "data-routing-view",
    },
    "evidence": {
        "grade10-operator",
        "provider-api-index",
        "pricecharting-manual",
        "tcgplayer-manual",
        "source-qc-patterns",
        "design-system",
        "us-price-source-rules",
        "ingest-verify-gate-legacy",
        "recall-verify-ops-legacy",
        "fill-loop-lessons-legacy",
        "file-claims-legacy",
        "project-map-legacy",
        "agent-pipeline-index-legacy",
        "frontend-handshake-legacy",
        "page-data-requirements-legacy",
        "data-routing-legacy",
        "data-source-inventory-legacy",
        "incremental-handoff-legacy",
        "architecture-chain-legacy",
        "handoff-legacy",
        "data-gaps-legacy",
        "practical-ops-legacy",
        "deploy-skill-legacy",
    },
}
EXPECTED_DOCUMENT_CLASS_SHAPES = {
    "timeless_invariant": {
        ("agent_entry", "active", "stable_contract_only"),
        ("machine_contract", "active", "stable_contract_only"),
        ("generated_view", "generated", "machine_generated"),
    },
    "versioned_contract": {
        ("reference", "active", "stable_contract_only"),
        ("machine_contract", "active", "stable_contract_only"),
        ("manual", "active", "runtime_verification_required"),
        ("generated_view", "generated", "machine_generated"),
    },
    "volatile_material": {
        ("machine_state", "generated", "machine_generated"),
    },
    "evidence": {
        ("evidence", "historical", "historical_snapshot"),
        ("evidence", "quarantine", "historical_snapshot"),
        ("manual", "reference_only", "historical_snapshot"),
        ("manual", "quarantine", "historical_snapshot"),
        ("reference", "reference_only", "historical_snapshot"),
        ("reference", "quarantine", "historical_snapshot"),
        ("reference", "superseded", "historical_snapshot"),
        ("reference", "delete_candidate", "historical_snapshot"),
    },
}
EXPECTED_RUNTIME_VERIFICATION = {
    "backend-runbook": {
        "toolIds": ["route-contract", "machine-status"],
        "tools": {
            "route-contract": {
                "entrypoint": "pipelines/data_routing.py",
                "command": ["python", "pipelines/data_routing.py"],
                "sideEffectClass": "read_only_or_generated_view_only",
                "evidenceOutputClass": "fresh_tool_output",
                "sideEffects": (
                    "read-only registry inspection by default; optional output and "
                    "generated-doc modes write only explicit generated-view targets"
                ),
            },
            "machine-status": {
                "entrypoint": "scripts/backend.py",
                "command": ["python", "scripts/backend.py", "status", "--json"],
                "sideEffectClass": "read_only_or_generated_view_only",
                "evidenceOutputClass": "fresh_tool_output",
                "sideEffects": (
                    "strictly read-only; never creates config, virtualenv or dependencies"
                ),
            },
        },
        "contract": {
            "domain": "backend_control_plane",
            "evidenceClass": "fresh_tool_output",
            "freshnessRule": (
                "verify inside the assigned work item; copied status is never current"
            ),
            "sideEffectClass": "read_only_or_generated_view_only",
            "roleIds": ["A11"],
        },
    },
    "gemrate-source": {
        "toolIds": ["gemrate-population"],
        "tools": {
            "gemrate-population": {
                "entrypoint": "pipelines/gemrate_source.py",
                "command": ["python", "pipelines/gemrate_source.py", "daily"],
                "sideEffectClass": "private_source_cache_and_receipts_only",
                "evidenceOutputClass": "content_addressed_private_source_receipt",
                "sideEffects": (
                    "writes only private GemRate run receipts and promotable private "
                    "source cache; never writes canonical DB, public snapshot, pointer, "
                    "timer, or deployment state"
                ),
            },
        },
        "contract": {
            "domain": "gemrate_population",
            "evidenceClass": "content_addressed_private_source_receipt",
            "freshnessRule": (
                "use the work-item as-of window and exact printing identity"
            ),
            "sideEffectClass": "private_source_cache_and_receipts_only",
            "roleIds": ["A05"],
        },
    },
    "snk-manual": {
        "toolIds": ["snk-reference-price"],
        "tools": {
            "snk-reference-price": {
                "entrypoint": "pipelines/snk_market_data.py",
                "command": ["python", "pipelines/snk_market_data.py"],
                "sideEffectClass": "private_source_cache_and_receipts_only",
                "evidenceOutputClass": "content_addressed_private_source_receipt",
                "sideEffects": (
                    "writes only caller-scoped private SNK JSONL, resumable state, "
                    "and report; never writes canonical DB, public snapshot, pointer, "
                    "timer, or deployment state"
                ),
            },
        },
        "contract": {
            "domain": "snk_price_history_sales",
            "evidenceClass": "content_addressed_private_source_receipt",
            "freshnessRule": (
                "use the work-item as-of window and exact printing identity"
            ),
            "sideEffectClass": "private_source_cache_and_receipts_only",
            "roleIds": ["A06"],
        },
    },
}
EXPECTED_DELEGATION_SCALARS = {
    "defaultChildMode": "read_only_same_role",
    "authorityInheritance": "children inherit the parent baseline, role, blocked actions and no additional authority",
    "crossRolePolicy": "return a structured dependency request to MAIN; do not silently spawn a cross-role writer",
}
EXPECTED_CHILD_WRITE_REQUIREMENTS = {
    "MAIN-approved non-overlapping subclaim",
    "same immutable baseline",
    "exact child output root",
    "parent-owned integration",
    "independent acceptance command",
}
EXPECTED_CHILD_PROMPT_FIELDS = {
    "parent_agent_id",
    "agent_id",
    "wave_id",
    "role_stage",
    "role_mode",
    "work_item_id",
    "baseline_id",
    "baseline_manifest",
    "baseline_manifest_sha256",
    "cohort_sha256",
    "routing_sha256",
    "db_state_fingerprint",
    "mode",
    "write_claim_id",
    "allowed_tool_ids",
    "read_set",
    "write_set",
    "generated_set",
    "forbidden",
    "input_artifacts",
    "dependency_receipts",
    "output_root",
    "acceptance",
    "handoff_to",
}
EXPECTED_HANDOFF_FIELDS = {
    "parent_agent_id",
    "agent_id",
    "wave_id",
    "role_stage",
    "role_mode",
    "work_item_id",
    "baseline_id",
    "baseline_manifest_sha256",
    "cohort_sha256",
    "routing_sha256",
    "db_state_fingerprint",
    "write_claim_id",
    "scope",
    "read_set",
    "write_set",
    "generated_set",
    "input_artifacts",
    "output_artifacts_with_sha256",
    "dependency_receipts",
    "commands_run",
    "test_output",
    "raw_test_output",
    "acceptance_verdict",
    "zero_mutation_proof",
    "risks",
    "blockers",
    "feedback_records",
    "next_owner",
    "intent_fidelity",
    "yagni",
}
EXPECTED_TOOL_EXECUTION_POLICY = (
    "a registered tool is necessary but not sufficient; execution requires the "
    "assigned work-item node, matching role stage, exact baseline, declared "
    "side-effect envelope and explicit allowed tool IDs"
)
EXPECTED_FEEDBACK_STATES = [
    "OBSERVED",
    "PROPOSED",
    "ACCEPTED",
    "REJECTED",
    "SUPERSEDED",
]
EXPECTED_FEEDBACK_FIELDS = {
    "feedback_id",
    "agent_id",
    "work_item_id",
    "baseline_id",
    "category",
    "blocked_rule_or_contract",
    "evidence",
    "impact",
    "proposed_owner",
    "proposed_change",
    "can_continue_read_only",
}
EXPECTED_FEEDBACK_CATEGORIES = {
    "FACT_UPDATE": {
        "agentAction": "continue only inside the owned candidate or registered writer path",
        "decisionOwner": "owning role",
        "architectureChange": False,
    },
    "IMPLEMENTATION_DEFECT": {
        "agentAction": "fix only inside the existing write claim and acceptance contract",
        "decisionOwner": "owning role",
        "architectureChange": False,
    },
    "CROSS_ROLE_DEPENDENCY": {
        "agentAction": "pause the dependent write, continue independent read-only work, and route to MAIN",
        "decisionOwner": "MAIN",
        "architectureChange": False,
    },
    "CONTRACT_GAP": {
        "agentAction": "stop the affected decision and submit a versioned contract proposal",
        "decisionOwner": "MAIN",
        "architectureChange": True,
    },
    "INVARIANT_CONFLICT": {
        "agentAction": "stop the affected work; do not bypass or reinterpret the invariant",
        "decisionOwner": "DADDY_AND_MAIN",
        "architectureChange": True,
    },
    "SECURITY_OR_PRIVACY": {
        "agentAction": "stop the affected path and return redacted evidence only",
        "decisionOwner": "MAIN",
        "architectureChange": False,
    },
}
EXPECTED_SOFT_FACT_RULE = (
    "numbers and current statuses may change through a registered writer with "
    "provenance, freshness and read-back evidence; never edit architecture to update a number"
)
EXPECTED_HARD_INVARIANT_RULE = (
    "a role cannot change an invariant; accepted invariant changes require DADDY "
    "approval, MAIN integration, registry versioning, regeneration and reverse tests"
)
EXPECTED_ROLE_STAGES = {
    "A01",
    "A02",
    "A03",
    "A04",
    "A05",
    "A06",
    "A07",
    "A08",
    "A09",
    "A10",
    "A11.bootstrap",
    "A11.control",
    "A11.runtime",
    "A12",
}


def _durable_content_violations(text: str) -> list[str]:
    """Return obvious volatile snapshots forbidden in stable Markdown contracts."""

    violations: list[str] = []
    if re.search(r"^#{1,6}\s+.*\b20\d{2}-\d{2}-\d{2}\b", text, re.IGNORECASE | re.MULTILINE):
        violations.append("dated heading")
    if re.search(r"^#{1,6}\s+.*\bcurrent evidence\b", text, re.IGNORECASE | re.MULTILINE):
        violations.append("current-evidence heading")
    if re.search(r"\b[a-f0-9]{64}\b", text, re.IGNORECASE):
        violations.append("literal content hash")
    if re.search(r"\bas\s+of\s+20\d{2}-\d{2}-\d{2}\b", text, re.IGNORECASE):
        violations.append("dated as-of statement")
    if re.search(
        r"^\s*(?:[-*]\s*)?(?:current|live)\s+(?:production\s+)?"
        r"(?:count|status|cards?|rows?|entries|coverage)\s*[:=]\s*"
        r"(?:\d[\d,]*|complete|ready|green|blocked)\b",
        text,
        re.IGNORECASE | re.MULTILINE,
    ):
        violations.append("current value statement")
    if re.search(
        r"\bcurrent\s+(?:database|production|dataset|cohort|snapshot|run|pack)\b"
        r"[^\r\n]{0,80}\b(?:has|contains|count|status|is)\b"
        r"[^\r\n]{0,40}\b(?:\d[\d,]*|complete|ready|green|blocked)\b",
        text,
        re.IGNORECASE,
    ):
        violations.append("current snapshot statement")
    return violations


def _safe_repo_path(value: object, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise RegistryError(f"{label} requires a repository-relative path")
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as error:
        raise RegistryError(f"{label} escapes the repository: {value}") from error
    return path


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
        "operatingSystems",
        "dependencies",
        "timeoutSeconds",
        "sideEffects",
        "sideEffectClass",
        "evidenceOutputClass",
        "manualRefs",
        "testRefs",
    }})
    return metadata


def _managed_markdown_paths(authority: Mapping[str, Any]) -> list[str]:
    discovered: set[str] = set()
    for value in authority.get("managedRoots", []):
        path = _safe_repo_path(value, "documentAuthority managed root")
        if path.is_file() and path.suffix.casefold() == ".md":
            discovered.add(path.relative_to(ROOT).as_posix())
        elif path.is_dir():
            discovered.update(
                candidate.relative_to(ROOT).as_posix()
                for candidate in path.rglob("*.md")
                if candidate.is_file()
            )
    return sorted(discovered)


def _write_claim_scope(value: str) -> tuple[str, bool]:
    """Return a normalized claim base and whether it covers every descendant."""

    normalized = value.replace("\\", "/").strip().removeprefix("./").rstrip("/").casefold()
    recursive = normalized.endswith("/**")
    if recursive:
        normalized = normalized[:-3].rstrip("/")
    if (
        not normalized
        or normalized.startswith("/")
        or ":" in normalized.split("/", 1)[0]
        or ".." in normalized.split("/")
        or any(marker in normalized for marker in ("*", "?", "[", "]"))
    ):
        raise RegistryError(
            f"write claim path must be a repository-relative file or a /** subtree: {value}"
        )
    return normalized, recursive


def _write_claim_paths_overlap(left: str, right: str) -> bool:
    left_base, left_recursive = _write_claim_scope(left)
    right_base, right_recursive = _write_claim_scope(right)

    def contains(parent: str, child: str) -> bool:
        return child == parent or child.startswith(parent + "/")

    if left_base == right_base:
        return True
    if left_recursive and contains(left_base, right_base):
        return True
    if right_recursive and contains(right_base, left_base):
        return True
    return False


def _validate_document_authority(
    document: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    authority = document.get("documentAuthority")
    if not isinstance(authority, Mapping):
        raise RegistryError("documentAuthority must be an object")
    if authority.get("defaultUnregisteredStatus") != "reference_only":
        raise RegistryError("unregistered documents must default to reference_only")
    if authority.get("generatedRoot") != "docs/generated":
        raise RegistryError("generated document root must be docs/generated")
    statuses = authority.get("statusEnum")
    kinds = authority.get("kindEnum")
    claim_policies = authority.get("claimPolicyEnum")
    if not isinstance(statuses, list) or not all(isinstance(value, str) and value for value in statuses):
        raise RegistryError("documentAuthority statusEnum must be a non-empty string array")
    if not isinstance(kinds, list) or not all(isinstance(value, str) and value for value in kinds):
        raise RegistryError("documentAuthority kindEnum must be a non-empty string array")
    if not isinstance(claim_policies, list) or not all(
        isinstance(value, str) and value for value in claim_policies
    ):
        raise RegistryError("documentAuthority claimPolicyEnum must be a non-empty string array")
    if len(claim_policies) != len(set(claim_policies)) or set(claim_policies) != EXPECTED_CLAIM_POLICIES:
        raise RegistryError("documentAuthority claimPolicyEnum has unsupported semantics")
    documents = _named(authority.get("documents"), "id", "documents")
    tools = _named(document.get("tools"), "id", "tools")
    paths: dict[str, str] = {}
    domain_owners: dict[str, str] = {}
    blocked_statuses = {
        "reference_only",
        "superseded",
        "historical",
        "quarantine",
        "delete_candidate",
    }
    for document_id, item in documents.items():
        path_value = item.get("path")
        path = _safe_repo_path(path_value, f"document {document_id}")
        normalized_path = path.relative_to(ROOT).as_posix()
        if normalized_path in paths:
            raise RegistryError(
                f"documents {paths[normalized_path]} and {document_id} share path {normalized_path}"
            )
        paths[normalized_path] = document_id
        status = item.get("status")
        kind = item.get("kind")
        claim_policy = item.get("claimPolicy")
        if status not in statuses:
            raise RegistryError(f"document {document_id} has invalid status")
        if kind not in kinds:
            raise RegistryError(f"document {document_id} has invalid kind")
        if claim_policy not in claim_policies:
            raise RegistryError(f"document {document_id} has invalid claimPolicy")
        verification_tool_ids = item.get("runtimeVerificationToolIds")
        verification_contract = item.get("runtimeVerificationContract")
        if claim_policy == "runtime_verification_required":
            if kind != "manual" or status != "active":
                raise RegistryError(
                    f"document {document_id} runtime verification is only valid for an active manual"
                )
            expected_verification = EXPECTED_RUNTIME_VERIFICATION.get(document_id)
            if expected_verification is None:
                raise RegistryError(
                    f"document {document_id} has no approved runtime verification contract"
                )
            if (
                verification_tool_ids != expected_verification["toolIds"]
                or any(value not in tools for value in verification_tool_ids)
            ):
                raise RegistryError(
                    f"document {document_id} runtimeVerificationToolIds changed"
                )
            if verification_contract != expected_verification["contract"]:
                raise RegistryError(
                    f"document {document_id} runtimeVerificationContract changed"
                )
            for tool_id in verification_tool_ids:
                tool = tools[tool_id]
                metadata = _tool_metadata(document, tool)
                actual_tool_contract = {
                    "entrypoint": tool.get("entrypoint"),
                    "command": tool.get("command"),
                    "sideEffectClass": metadata.get("sideEffectClass"),
                    "evidenceOutputClass": metadata.get("evidenceOutputClass"),
                    "sideEffects": metadata.get("sideEffects"),
                }
                if actual_tool_contract != expected_verification["tools"][tool_id]:
                    raise RegistryError(
                        f"runtime verifier tool {tool_id} implementation contract changed"
                    )
                if (
                    actual_tool_contract["sideEffectClass"]
                    != verification_contract["sideEffectClass"]
                    or actual_tool_contract["evidenceOutputClass"]
                    != verification_contract["evidenceClass"]
                ):
                    raise RegistryError(
                        f"runtime verifier tool {tool_id} contradicts document "
                        f"{document_id} evidence or side-effect contract"
                    )
                if document_id not in _tool_metadata(document, tools[tool_id]).get(
                    "manualRefs", []
                ):
                    raise RegistryError(
                        f"document {document_id} verification tool {tool_id} does not reference the manual"
                    )
        elif verification_tool_ids is not None or verification_contract is not None:
            raise RegistryError(
                f"document {document_id} may not declare runtime verification fields"
            )
        if not isinstance(item.get("instructional"), bool) or not isinstance(
            item.get("executionAllowed"), bool
        ):
            raise RegistryError(
                f"document {document_id} requires instructional and executionAllowed booleans"
            )
        domains = item.get("authorityDomains")
        if not isinstance(domains, list) or not all(isinstance(value, str) and value for value in domains):
            raise RegistryError(f"document {document_id} requires authorityDomains")
        if status in blocked_statuses and (item["instructional"] or item["executionAllowed"]):
            raise RegistryError(
                f"document {document_id} cannot direct execution while status is {status}"
            )
        if status in {"active", "generated"}:
            for domain in domains:
                previous = domain_owners.get(domain)
                if previous is not None:
                    raise RegistryError(
                        f"authority domain {domain} has multiple active documents: {previous}, {document_id}"
                    )
                domain_owners[domain] = document_id
        if status == "generated":
            generator = item.get("generatorToolId")
            if not isinstance(generator, str) or generator not in tools:
                raise RegistryError(
                    f"generated document {document_id} requires a registered generatorToolId"
                )
            generated_root = _safe_repo_path(authority["generatedRoot"], "generatedRoot")
            if kind != "machine_state":
                try:
                    path.relative_to(generated_root)
                except ValueError as error:
                    raise RegistryError(
                        f"generated document {document_id} must live under docs/generated"
                    ) from error
        elif not path.is_file():
            raise RegistryError(f"document {document_id} path does not exist: {path_value}")
        if (
            status == "active"
            and claim_policy == "stable_contract_only"
            and path.suffix.casefold() == ".md"
        ):
            violations = _durable_content_violations(path.read_text(encoding="utf-8"))
            if violations:
                raise RegistryError(
                    f"stable document {document_id} contains volatile snapshot markers: {violations}"
                )
        replacement = item.get("supersededBy")
        if status == "superseded":
            if not isinstance(replacement, str) or replacement not in documents or replacement == document_id:
                raise RegistryError(f"superseded document {document_id} requires a valid supersededBy")
        elif replacement is not None:
            raise RegistryError(f"document {document_id} may not declare supersededBy")

    for document_id in documents:
        visited: set[str] = set()
        current = document_id
        while documents[current].get("supersededBy") is not None:
            if current in visited:
                raise RegistryError("document supersession graph contains a cycle")
            visited.add(current)
            current = documents[current]["supersededBy"]

    for document_id in EXPECTED_RUNTIME_VERIFICATION:
        if (
            document_id not in documents
            or documents[document_id].get("claimPolicy")
            != "runtime_verification_required"
        ):
            raise RegistryError(
                f"approved runtime manual {document_id} is missing its verification contract"
            )

    entry_sequence = authority.get("entrySequence")
    if not isinstance(entry_sequence, list) or not entry_sequence:
        raise RegistryError("documentAuthority entrySequence must be non-empty")
    for document_id in entry_sequence:
        if document_id not in documents:
            raise RegistryError(f"entrySequence references unknown document: {document_id}")
        if documents[document_id]["status"] not in {"active", "generated"}:
            raise RegistryError(f"entrySequence document {document_id} is not active or generated")

    archive_roots = authority.get("archiveRoots")
    excluded_roots = authority.get("excludedExecutionRoots")
    if not isinstance(archive_roots, list) or not isinstance(excluded_roots, list):
        raise RegistryError("documentAuthority requires archiveRoots and excludedExecutionRoots")
    if any(root not in excluded_roots for root in archive_roots):
        raise RegistryError("every archive root must also be excluded from execution")
    for root in excluded_roots:
        _safe_repo_path(root, "excluded execution root")

    discovered = _managed_markdown_paths(authority)
    unregistered = sorted(set(discovered) - set(paths))
    return documents, unregistered


def _document_class_map(
    disclosure: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    information_classes: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    bindings = _named(
        disclosure.get("documentClassBindings"),
        "informationClassId",
        "document class bindings",
    )
    if set(bindings) != set(information_classes):
        raise RegistryError(
            "documentClassBindings must define every information class exactly once"
        )
    result: dict[str, str] = {}
    for class_id, binding in bindings.items():
        document_ids = binding.get("documentIds")
        if (
            not isinstance(document_ids, list)
            or not document_ids
            or not all(isinstance(value, str) and value for value in document_ids)
            or len(document_ids) != len(set(document_ids))
        ):
            raise RegistryError(
                f"document class binding {class_id} requires unique documentIds"
            )
        unknown = sorted(set(document_ids) - set(documents))
        if unknown:
            raise RegistryError(
                f"document class binding {class_id} references unknown documents: {unknown}"
            )
        for document_id in document_ids:
            previous = result.get(document_id)
            if previous is not None:
                raise RegistryError(
                    f"document {document_id} has multiple information classes: "
                    f"{previous}, {class_id}"
                )
            result[document_id] = class_id
    missing = sorted(set(documents) - set(result))
    if missing:
        raise RegistryError(
            f"registered documents require one information class: {missing}"
        )
    for class_id, binding in bindings.items():
        if set(binding["documentIds"]) != EXPECTED_DOCUMENT_CLASS_MEMBERS[class_id]:
            raise RegistryError(
                f"document class binding {class_id} has changed document membership"
            )
    return result


def _validate_agent_execution(
    document: Mapping[str, Any],
    documents: Mapping[str, Mapping[str, Any]],
    architecture_nodes: Mapping[str, Mapping[str, Any]],
    work_items: Mapping[str, Mapping[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    execution = document.get("agentExecution")
    if not isinstance(execution, Mapping):
        raise RegistryError("agentExecution must be an object")
    policy = execution.get("policy")
    if not isinstance(policy, Mapping):
        raise RegistryError("agentExecution policy must be an object")
    if policy.get("integratorRoleId") != "MAIN":
        raise RegistryError("MAIN must remain the shared control-plane integrator")
    if policy.get("releaseAuthority") != "mainHermes":
        raise RegistryError("agent roles cannot own production release authority")
    if policy.get("defaultWritePolicy") != "deny":
        raise RegistryError("agentExecution defaultWritePolicy must be deny")
    if policy.get("generatedDocsWritePolicy") != "generator_only":
        raise RegistryError("generated docs must remain generator-only")
    if policy.get("toolExecutionPolicy") != EXPECTED_TOOL_EXECUTION_POLICY:
        raise RegistryError("agentExecution tool execution policy must remain exact")
    required_handoff = policy.get("requiredHandoffFields")
    if (
        not isinstance(required_handoff, list)
        or len(required_handoff) != len(set(required_handoff))
        or set(required_handoff) != EXPECTED_HANDOFF_FIELDS
    ):
        raise RegistryError("agentExecution handoff fields must remain exact")

    information_classes = _named(
        execution.get("informationClasses"),
        "id",
        "information classes",
    )
    expected_information_classes = set(EXPECTED_INFORMATION_CLASSES)
    if set(information_classes) != expected_information_classes:
        raise RegistryError(
            "agentExecution informationClasses must separate timeless invariants, "
            "versioned contracts, volatile material and evidence"
        )
    for class_id, item in information_classes.items():
        expected = EXPECTED_INFORMATION_CLASSES[class_id]
        for field, expected_value in expected.items():
            if item.get(field) != expected_value:
                raise RegistryError(
                    f"information class {class_id} has changed {field} semantics"
                )

    disclosure = execution.get("progressiveDisclosure")
    if not isinstance(disclosure, Mapping):
        raise RegistryError("agentExecution requires progressiveDisclosure")
    if disclosure.get("dispatcherInitialDocumentIds") != EXPECTED_DISPATCHER_INITIAL_DOCUMENTS:
        raise RegistryError(
            "progressiveDisclosure dispatcher initial documents must remain exact"
        )
    if disclosure.get("workerInitialDocumentIds") != EXPECTED_WORKER_INITIAL_DOCUMENTS:
        raise RegistryError(
            "progressiveDisclosure worker initial documents must remain exact"
        )
    if disclosure.get("principle") != "load the minimum exact context needed for the current decision":
        raise RegistryError("progressiveDisclosure principle must remain exact")
    if disclosure.get("levels") != EXPECTED_PROGRESSIVE_LEVELS:
        raise RegistryError("progressiveDisclosure level semantics must remain exact")
    for document_id in disclosure["dispatcherInitialDocumentIds"]:
        if document_id not in documents or documents[document_id]["status"] not in {
            "active",
            "generated",
        }:
            raise RegistryError(
                f"progressiveDisclosure dispatcher entry is not active: {document_id}"
            )
    worker_sentinels = {"assigned_role_pack_only"}
    for document_id in disclosure["workerInitialDocumentIds"]:
        if document_id in worker_sentinels:
            continue
        if document_id not in documents or documents[document_id]["status"] not in {
            "active",
            "generated",
        }:
            raise RegistryError(
                f"progressiveDisclosure worker entry is not active: {document_id}"
            )
    levels = _named(disclosure.get("levels"), "id", "progressive disclosure levels")
    expected_levels = [f"L{number}" for number in range(6)]
    if list(levels) != expected_levels:
        raise RegistryError("progressiveDisclosure levels must be ordered L0 through L5")
    for level_id, level in levels.items():
        if level.get("informationClassId") not in information_classes:
            raise RegistryError(
                f"progressive disclosure level {level_id} references unknown information class"
            )
        for field in ("label", "loadRule"):
            if not isinstance(level.get(field), str) or not level[field]:
                raise RegistryError(f"progressive disclosure level {level_id} requires {field}")
    section_classifications = disclosure.get("sectionClassifications")
    if not isinstance(section_classifications, list) or not section_classifications:
        raise RegistryError("progressiveDisclosure requires sectionClassifications")
    if section_classifications != EXPECTED_SECTION_CLASSIFICATIONS:
        raise RegistryError(
            "progressiveDisclosure section classification semantics must remain exact"
        )
    classified_information: set[str] = set()
    for item in section_classifications:
        if not isinstance(item, Mapping):
            raise RegistryError("progressiveDisclosure section classification must be an object")
        selector = item.get("selector")
        class_id = item.get("informationClassId")
        consumer_rule = item.get("consumerRule")
        if not isinstance(selector, str) or not selector:
            raise RegistryError("progressiveDisclosure section classification requires selector")
        if class_id not in information_classes:
            raise RegistryError(
                f"progressiveDisclosure section {selector} references unknown information class"
            )
        if not isinstance(consumer_rule, str) or not consumer_rule:
            raise RegistryError(
                f"progressiveDisclosure section {selector} requires consumerRule"
            )
        classified_information.add(class_id)
    if classified_information != expected_information_classes:
        raise RegistryError(
            "progressiveDisclosure section classifications must cover every information class"
        )
    document_classes = _document_class_map(
        disclosure,
        documents,
        information_classes,
    )
    blocked_statuses = {
        "reference_only",
        "superseded",
        "historical",
        "quarantine",
        "delete_candidate",
    }
    for document_id, item in documents.items():
        class_id = document_classes[document_id]
        status = item["status"]
        kind = item["kind"]
        claim_policy = item["claimPolicy"]
        shape = (kind, status, claim_policy)
        if shape not in EXPECTED_DOCUMENT_CLASS_SHAPES[class_id]:
            raise RegistryError(
                f"document {document_id} has invalid shape for {class_id}: {shape}"
            )
        if kind == "agent_entry" and class_id != "timeless_invariant":
            raise RegistryError(f"agent entry {document_id} must be timeless")
        if kind == "machine_state" and class_id != "volatile_material":
            raise RegistryError(f"machine state {document_id} must be volatile material")
        if status in blocked_statuses or claim_policy == "historical_snapshot":
            if class_id != "evidence":
                raise RegistryError(f"blocked document {document_id} must be evidence")
            if (
                item["instructional"]
                or item["executionAllowed"]
                or item["authorityDomains"]
            ):
                raise RegistryError(
                    f"evidence document {document_id} cannot carry execution authority"
                )
        if claim_policy == "runtime_verification_required" and class_id != "versioned_contract":
            raise RegistryError(
                f"runtime-verified document {document_id} must be a versioned contract"
            )
        if item["path"].startswith("docs/generated/roles/") and class_id != "timeless_invariant":
            raise RegistryError(f"role pack {document_id} must be timeless")
        if class_id == "volatile_material":
            if (
                item["instructional"]
                or item["executionAllowed"]
                or not item.get("generatorToolId")
            ):
                raise RegistryError(
                    f"volatile document {document_id} must be generated and non-instructional"
                )
    for document_id in document["documentAuthority"]["entrySequence"]:
        if document_classes[document_id] != "timeless_invariant":
            raise RegistryError(
                f"entry sequence document {document_id} must be timeless"
            )
    if document["documentAuthority"]["entrySequence"] != EXPECTED_DISPATCHER_INITIAL_DOCUMENTS:
        raise RegistryError(
            "documentAuthority entry sequence must match the exact dispatcher initial set"
        )
    for document_id in disclosure["dispatcherInitialDocumentIds"]:
        if document_classes[document_id] != "timeless_invariant":
            raise RegistryError(
                f"dispatcher initial document {document_id} must be timeless"
            )
    for document_id in disclosure["workerInitialDocumentIds"]:
        if document_id == "assigned_role_pack_only":
            continue
        if document_classes[document_id] != "timeless_invariant":
            raise RegistryError(
                f"worker initial document {document_id} must be timeless"
            )
    if disclosure.get("forbiddenBulkLoads") != EXPECTED_FORBIDDEN_BULK_LOADS:
        raise RegistryError("progressiveDisclosure forbidden bulk loads must remain exact")
    if disclosure.get("volatileMaterialRule") != EXPECTED_VOLATILE_MATERIAL_RULE:
        raise RegistryError("progressiveDisclosure volatile material rule must remain exact")

    capabilities = _named(
        execution.get("capabilityRoutes"),
        "id",
        "capability routes",
    )
    for capability_id, capability in capabilities.items():
        for field in (
            "loadWhen",
            "availabilityPolicy",
            "missingAdapterPolicy",
            "outputRule",
        ):
            if not isinstance(capability.get(field), str) or not capability[field]:
                raise RegistryError(f"capability route {capability_id} requires {field}")
        if capability["availabilityPolicy"] != "runtime_discovery":
            raise RegistryError(
                f"capability route {capability_id} must treat availability as runtime material"
            )
        adapters = capability.get("adapterCandidates")
        if not isinstance(adapters, list) or not adapters or not all(
            isinstance(value, str) and value for value in adapters
        ):
            raise RegistryError(f"capability route {capability_id} requires adapterCandidates")

    delegation = execution.get("delegationPolicy")
    if not isinstance(delegation, Mapping):
        raise RegistryError("agentExecution requires delegationPolicy")
    for field, expected_value in EXPECTED_DELEGATION_SCALARS.items():
        if delegation.get(field) != expected_value:
            raise RegistryError(f"delegationPolicy has changed {field} semantics")
    child_write_requirements = delegation.get("childWriteRequirements")
    if (
        not isinstance(child_write_requirements, list)
        or len(child_write_requirements) != len(set(child_write_requirements))
        or set(child_write_requirements) != EXPECTED_CHILD_WRITE_REQUIREMENTS
    ):
        raise RegistryError("delegationPolicy child write requirements must remain exact")
    child_prompt_fields = delegation.get("requiredChildPromptFields")
    if (
        not isinstance(child_prompt_fields, list)
        or len(child_prompt_fields) != len(set(child_prompt_fields))
        or set(child_prompt_fields) != EXPECTED_CHILD_PROMPT_FIELDS
    ):
        raise RegistryError("delegationPolicy child prompt fields must remain exact")

    feedback = execution.get("feedbackProtocol")
    if not isinstance(feedback, Mapping) or feedback.get("noSilentFallback") is not True:
        raise RegistryError("feedbackProtocol must prohibit silent fallback")
    if feedback.get("states") != EXPECTED_FEEDBACK_STATES:
        raise RegistryError("feedbackProtocol states must remain exact")
    required_feedback_fields = feedback.get("requiredFields")
    if (
        not isinstance(required_feedback_fields, list)
        or len(required_feedback_fields) != len(set(required_feedback_fields))
        or set(required_feedback_fields) != EXPECTED_FEEDBACK_FIELDS
    ):
        raise RegistryError("feedbackProtocol required fields must remain exact")
    feedback_categories = _named(
        feedback.get("categories"),
        "id",
        "feedback categories",
    )
    if set(feedback_categories) != set(EXPECTED_FEEDBACK_CATEGORIES):
        raise RegistryError("feedbackProtocol categories are incomplete")
    for category_id, category in feedback_categories.items():
        expected = EXPECTED_FEEDBACK_CATEGORIES[category_id]
        for field, expected_value in expected.items():
            if category.get(field) != expected_value:
                raise RegistryError(
                    f"feedback category {category_id} has changed {field} semantics"
                )
    if feedback.get("softFactRule") != EXPECTED_SOFT_FACT_RULE:
        raise RegistryError("feedbackProtocol soft fact semantics must remain exact")
    if feedback.get("hardInvariantRule") != EXPECTED_HARD_INVARIANT_RULE:
        raise RegistryError("feedbackProtocol hard invariant semantics must remain exact")

    roles = _named(execution.get("roles"), "id", "agent roles")
    expected_roles = {f"A{number:02d}" for number in range(1, 13)}
    if set(roles) != expected_roles:
        raise RegistryError("agentExecution must define exactly roles A01 through A12")
    claims = _named(execution.get("writeClaims"), "id", "write claims")
    claim_paths: list[tuple[str, str]] = []
    valid_role_owners = expected_roles | {"MAIN"}
    for claim_id, claim in claims.items():
        owner = claim.get("ownerRoleId")
        if owner not in valid_role_owners:
            raise RegistryError(f"write claim {claim_id} has unknown owner role")
        paths = claim.get("paths")
        if not isinstance(paths, list) or not paths or not all(
            isinstance(value, str) and value for value in paths
        ):
            raise RegistryError(f"write claim {claim_id} requires paths")
        for value in paths:
            _write_claim_scope(value)
            for previous_value, previous_claim in claim_paths:
                if not _write_claim_paths_overlap(previous_value, value):
                    continue
                if previous_value.replace("\\", "/").casefold() == value.replace("\\", "/").casefold():
                    raise RegistryError(
                        f"write claims {previous_claim} and {claim_id} overlap exact path {value}"
                    )
                if previous_claim != claim_id:
                    raise RegistryError(
                        f"write claims {previous_claim} and {claim_id} overlap path scopes "
                        f"{previous_value} and {value}"
                    )
            claim_paths.append((value, claim_id))
        if owner == "A12" and claim.get("mode") != "read_only_production":
            raise RegistryError("A12 may only own a read_only_production claim")

    for role_id, role in roles.items():
        for field in (
            "label",
            "layer",
            "mode",
            "mission",
        ):
            if not isinstance(role.get(field), str) or not role[field]:
                raise RegistryError(f"agent role {role_id} requires {field}")
        node_ids = role.get("nodeIds")
        if not isinstance(node_ids, list) or not node_ids or any(
            value not in architecture_nodes for value in node_ids
        ):
            raise RegistryError(f"agent role {role_id} references unknown architecture node")
        required_documents = role.get("requiredDocumentIds")
        if not isinstance(required_documents, list) or not required_documents or any(
            value not in documents for value in required_documents
        ):
            raise RegistryError(f"agent role {role_id} references unknown document")
        role_document_id = role.get("roleDocumentId")
        expected_role_path = f"docs/generated/roles/{role_id}.md"
        if (
            role_document_id not in documents
            or documents[role_document_id]["path"] != expected_role_path
            or documents[role_document_id]["status"] != "generated"
            or documents[role_document_id]["executionAllowed"] is not True
        ):
            raise RegistryError(f"agent role {role_id} requires its generated role pack")
        blocked_required = [
            value
            for value in required_documents
            if documents[value]["status"] not in {"active", "generated"}
        ]
        if blocked_required:
            raise RegistryError(
                f"agent role {role_id} requires blocked documents: {blocked_required}"
            )
        non_durable_required = [
            value
            for value in required_documents
            if document_classes[value] not in {
                "timeless_invariant",
                "versioned_contract",
            }
        ]
        if non_durable_required:
            raise RegistryError(
                f"agent role {role_id} requires non-durable documents: "
                f"{non_durable_required}"
            )
        if document_classes[role_document_id] != "timeless_invariant":
            raise RegistryError(f"agent role {role_id} role pack must be timeless")
        capability_ids = role.get("capabilityRouteIds")
        if not isinstance(capability_ids, list) or not capability_ids or any(
            value not in capabilities for value in capability_ids
        ):
            raise RegistryError(f"agent role {role_id} references unknown capability route")
        read_paths = role.get("readPaths")
        if not isinstance(read_paths, list) or not read_paths or not all(
            isinstance(value, str) and value for value in read_paths
        ):
            raise RegistryError(f"agent role {role_id} requires readPaths")
        claim_ids = role.get("writeClaimIds")
        if not isinstance(claim_ids, list) or not all(value in claims for value in claim_ids):
            raise RegistryError(f"agent role {role_id} references unknown write claim")
        if any(claims[value]["ownerRoleId"] != role_id for value in claim_ids):
            raise RegistryError(f"agent role {role_id} references a claim owned by another role")
        for field in ("blockedActions", "acceptance", "handoffTo"):
            values = role.get(field)
            if not isinstance(values, list) or not values or not all(
                isinstance(value, str) and value for value in values
            ):
                raise RegistryError(f"agent role {role_id} requires {field}")
        if any(value not in expected_roles | {"MAIN"} for value in role["handoffTo"]):
            raise RegistryError(f"agent role {role_id} has unknown handoff target")

    for document_id, expected_verification in EXPECTED_RUNTIME_VERIFICATION.items():
        expected_role_ids = set(expected_verification["contract"]["roleIds"])
        reachable_role_ids = {
            role_id
            for role_id, role in roles.items()
            if document_id in role["requiredDocumentIds"]
        }
        if reachable_role_ids != expected_role_ids:
            raise RegistryError(
                f"runtime verification manual {document_id} has changed role reachability: "
                f"{sorted(reachable_role_ids)}"
            )

    waves = _named(execution.get("waves"), "id", "agent waves")
    wave_stages: set[str] = set()
    for wave_id, wave in waves.items():
        stages = wave.get("roleStages")
        dependencies = wave.get("dependsOn")
        if not isinstance(stages, list) or not stages:
            raise RegistryError(f"agent wave {wave_id} requires roleStages")
        for stage in stages:
            if not isinstance(stage, str) or stage.split(".", 1)[0] not in roles:
                raise RegistryError(f"agent wave {wave_id} references unknown role stage: {stage}")
            if stage in wave_stages:
                raise RegistryError(f"agent role stage appears in multiple waves: {stage}")
            wave_stages.add(stage)
        if not isinstance(dependencies, list) or any(
            value not in waves or value == wave_id for value in dependencies
        ):
            raise RegistryError(f"agent wave {wave_id} has invalid dependencies")
    if wave_stages != EXPECTED_ROLE_STAGES:
        raise RegistryError(
            "agent waves must define the exact A01-A12 role-stage universe"
        )
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit_wave(wave_id: str) -> None:
        if wave_id in visiting:
            raise RegistryError("agent wave dependency graph contains a cycle")
        if wave_id in visited:
            return
        visiting.add(wave_id)
        for dependency in waves[wave_id]["dependsOn"]:
            visit_wave(dependency)
        visiting.remove(wave_id)
        visited.add(wave_id)

    for wave_id in waves:
        visit_wave(wave_id)

    assignments = _named(execution.get("assignments"), "workItemId", "agent assignments")
    stage_assignments: dict[str, list[str]] = {
        stage: [] for stage in wave_stages
    }
    for work_item_id, assignment in assignments.items():
        if work_item_id not in work_items:
            raise RegistryError(f"assignment references unknown work item: {work_item_id}")
        owner = assignment.get("ownerRoleId")
        contributors = assignment.get("contributorRoleIds")
        if owner not in valid_role_owners:
            raise RegistryError(f"assignment {work_item_id} has unknown owner")
        if not isinstance(contributors, list) or any(
            value not in expected_roles or value == owner for value in contributors
        ):
            raise RegistryError(f"assignment {work_item_id} has invalid contributors")
        role_stage = assignment.get("roleStage")
        if role_stage is not None:
            if (
                not isinstance(role_stage, str)
                or role_stage not in wave_stages
                or role_stage.split(".", 1)[0] != owner
            ):
                raise RegistryError(
                    f"assignment {work_item_id} has invalid roleStage: {role_stage}"
                )
            stage_assignments[role_stage].append(work_item_id)
    open_work_items = {
        item_id
        for item_id, item in work_items.items()
        if item["status"] in {"planned", "in_progress"}
    }
    missing_assignments = sorted(open_work_items - set(assignments))
    if missing_assignments:
        raise RegistryError(f"open work items require exactly one owner: {missing_assignments}")
    for role_stage, stage_work_items in stage_assignments.items():
        if len(stage_work_items) != 1:
            raise RegistryError(
                f"agent wave stages require exactly one work-item assignment: {role_stage}"
            )
        stage_work_item_id = stage_work_items[0]
        if work_items[stage_work_item_id]["status"] not in {
            "planned",
            "in_progress",
        }:
            raise RegistryError(
                f"agent wave stage {role_stage} requires one open work item"
            )
    return roles, claims, waves, assignments


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
    if document.get("schemaVersion") != "3.3":
        raise RegistryError("control-plane schemaVersion must be 3.3")
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
    for required in ("top100", "top300", "top300_boards", "top350", "top100_plus_200", "reserve50"):
        if required not in views:
            raise RegistryError(f"missing presentation view: {required}")
    if views["top100_plus_200"].get("aliasOf") != "top300":
        raise RegistryError("top100_plus_200 must alias top300")
    if views["reserve50"].get("rankStart") != 301 or views["reserve50"].get("rankEnd") != 350:
        raise RegistryError("reserve50 must describe ranks 301 through 350")

    documents, _ = _validate_document_authority(document)
    manuals = _named(document.get("manuals"), "id", "manuals")
    tests = _named(document.get("tests"), "id", "tests")
    for label, items in (("manual", manuals), ("test", tests)):
        for item_id, item in items.items():
            path = item.get("path")
            if not isinstance(path, str) or not (ROOT / path).is_file():
                raise RegistryError(f"{label} {item_id} path does not exist: {path}")
            if label == "manual":
                document_id = item.get("documentId")
                if document_id not in documents:
                    raise RegistryError(f"manual {item_id} references unknown document: {document_id}")
                if documents[document_id]["path"] != path:
                    raise RegistryError(f"manual {item_id} path disagrees with document {document_id}")
                if documents[document_id]["status"] in {
                    "superseded",
                    "historical",
                    "quarantine",
                    "delete_candidate",
                }:
                    raise RegistryError(
                        f"manual {item_id} cannot reference {documents[document_id]['status']} document"
                    )
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
        tool_manuals = _references(
            document,
            "manuals",
            metadata.get("manualRefs"),
            f"tool {tool_id}",
        )
        blocked_manuals = [
            manual["id"]
            for manual in tool_manuals
            if documents[manual["documentId"]]["status"] not in {"active", "generated"}
        ]
        if blocked_manuals:
            raise RegistryError(
                f"tool {tool_id} references non-executable manuals: {blocked_manuals}"
            )
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
    architecture_nodes, work_items = _validate_architecture(document)
    _validate_agent_execution(document, documents, architecture_nodes, work_items)


def _control_plane(document: Mapping[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    architecture_nodes, work_items = _validate_architecture(document)
    documents, _ = _validate_document_authority(document)
    roles, claims, waves, assignments = _validate_agent_execution(
        document,
        documents,
        architecture_nodes,
        work_items,
    )
    execution = document["agentExecution"]
    information_classes = _named(
        execution["informationClasses"],
        "id",
        "information classes",
    )
    document_classes = _document_class_map(
        execution["progressiveDisclosure"],
        documents,
        information_classes,
    )
    explained_documents = {
        document_id: {
            **item,
            "informationClassId": document_classes[document_id],
        }
        for document_id, item in documents.items()
    }
    return {
        "routes": _named(document["routes"], "metric", "routes"),
        "tools": _named(document["tools"], "id", "tools"),
        "profiles": _named(document["profiles"], "id", "profiles"),
        "views": _named(document["presentationViews"], "id", "presentation views"),
        "consumers": _named(document["consumers"], "id", "consumers"),
        "manuals": _named(document["manuals"], "id", "manuals"),
        "tests": _named(document["tests"], "id", "tests"),
        "documents": explained_documents,
        "agentRoles": roles,
        "capabilityRoutes": _named(
            execution["capabilityRoutes"],
            "id",
            "capability routes",
        ),
        "informationClasses": information_classes,
        "contextLevels": _named(
            execution["progressiveDisclosure"]["levels"],
            "id",
            "progressive disclosure levels",
        ),
        "feedbackCategories": _named(
            execution["feedbackProtocol"]["categories"],
            "id",
            "feedback categories",
        ),
        "writeClaims": claims,
        "agentWaves": waves,
        "architectureNodes": architecture_nodes,
        "workItems": work_items,
        "agentAssignments": assignments,
    }


def explain_identifier(document: Mapping[str, Any], identifier: str) -> dict[str, Any]:
    """Return one stable explanation payload for a metric, tool, profile, view or consumer."""

    if identifier.startswith("document:"):
        document_id = identifier.removeprefix("document:")
        plane = _control_plane(document)
        if not document_id or document_id not in plane["documents"]:
            raise RegistryError(f"unknown registry document: {document_id}")
        return {
            "identifier": identifier,
            "resolvedIdentifier": document_id,
            "kind": "document",
            "value": plane["documents"][document_id],
        }

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
            singular_kinds = {
                "informationClasses": "informationClass",
                "feedbackCategories": "feedbackCategory",
            }
            result = {
                "identifier": identifier,
                "kind": singular_kinds.get(
                    kind,
                    kind[:-1] if kind.endswith("s") else kind,
                ),
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
            elif kind == "agentRoles":
                role = items[resolved_identifier]
                result["roleDocument"] = plane["documents"][role["roleDocumentId"]]
                result["capabilities"] = [
                    plane["capabilityRoutes"][capability_id]
                    for capability_id in role["capabilityRouteIds"]
                ]
                result["assignments"] = [
                    assignment
                    for assignment in plane["agentAssignments"].values()
                    if assignment["ownerRoleId"] == resolved_identifier
                ]
            elif kind == "workItems":
                result["nodes"] = [
                    plane["architectureNodes"][node_id] for node_id in items[resolved_identifier]["nodeIds"]
                ]
                assignment = plane["agentAssignments"].get(resolved_identifier)
                if assignment is not None:
                    result["assignment"] = assignment
                    result["ownerRole"] = plane["agentRoles"][assignment["ownerRoleId"]]
                result["dependencies"] = [
                    plane["workItems"][dependency]
                    for dependency in items[resolved_identifier]["dependsOn"]
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
        "| ID | Priority | Architecture nodes | Depends on | Owner files |",
        "| --- | --- | --- | --- | --- |",
    ])
    for task in document["workItems"]:
        lines.append(
            "| {id} | {priority} | {nodes} | {dependencies} | {owners} |".format(
                id=task["id"],
                priority=task["priority"],
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
    documents, _ = _validate_document_authority(document)
    lines.extend([
        "",
        "## Manuals",
        "",
        "| ID | Path | Document status | Execution from prose | Purpose |",
        "| --- | --- | --- | --- | --- |",
    ])
    for manual in document["manuals"]:
        authority = documents[manual["documentId"]]
        lines.append(
            "| {id} | `{path}` | `{status}` | {execution} | {purpose} |".format(
                id=manual["id"],
                path=manual["path"],
                status=authority["status"],
                execution="allowed" if authority["executionAllowed"] else "denied",
                purpose=manual.get("purpose", "—"),
            )
        )
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
        "validator.canonical-db-qc": (1904, 390),
        "authority.universe": (1904, 248),
        "validator.eligibility": (1904, 104),
        "derivation.market": (2136, 104),
        "ranking.scopes": (2368, 104),
        "export.views": (2600, 104),
        "export.sanitized-snapshot": (2832, 104),
        "validator.public-qc": (3064, 104),
        "review.media": (3296, 248),
        "publisher.official": (3296, 104),
        "index.codegraph": (48, 462),
        "orchestrator.daily": (280, 462),
        "state.last-good": (512, 462),
        "status.machine": (976, 462),
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
        "<td>{title}</td><td>{nodes}</td>"
        "<td>{dependencies}</td><td>{owners}</td></tr>".format(
            id=html.escape(str(task["id"])),
            priority=html.escape(str(task["priority"])),
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
svg{display:block;width:3552px;height:auto;min-height:610px}.flow{fill:none;stroke:#64748b;stroke-width:2.2;opacity:.88}.flow.optional{stroke-dasharray:8 7;opacity:.58}
.node-kind{fill:#cbd5e1;font-size:10px;text-transform:uppercase;letter-spacing:.11em}.node-label{fill:white;font-size:14px;font-weight:650}.node-ref{fill:#dbeafe;font-size:9px}
.legend{display:flex;flex-wrap:wrap;gap:9px 16px;padding:13px 4px 28px}.legend span{display:flex;align-items:center;gap:7px;color:var(--muted);font-size:12px}.legend i{width:10px;height:10px;border-radius:3px}
section{margin:28px 0}.table-shell{overflow:auto;border:1px solid var(--line);border-radius:14px}table{border-collapse:collapse;width:100%;min-width:980px;background:rgba(15,23,42,.62)}
th,td{text-align:left;padding:11px 12px;border-bottom:1px solid rgba(51,65,85,.75);vertical-align:top;font-size:13px}th{color:#cbd5e1;background:#111c31;position:sticky;top:0}td{color:#b9c5d6}
.priority{display:inline-block;padding:3px 7px;border-radius:999px;font-size:11px}.priority.P0{background:#7f1d1d;color:#fecaca}.priority.P1{background:#78350f;color:#fde68a}.priority.P2{background:#164e63;color:#a5f3fc}
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
<div class="diagram-shell"><svg viewBox="0 0 3552 610" role="img" aria-labelledby="diagram-title diagram-desc">
<title id="diagram-title">CARDZ backend architecture</title><desc id="diagram-desc">GemRate authority, private receipts, exact identity, SNK pricing, canonical database, ranking and sanitized snapshot flow.</desc>
<defs><marker id="arrow" viewBox="0 0 10 10" refX="8.2" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="#94a3b8"/></marker></defs>
""" + "".join(edge_svg) + "".join(node_svg) + """
</svg></div><div class="legend">""" + legend + """</div></section>
<section><h2>Work items</h2><p class="muted">Bounded tasks are stored beside the architecture nodes they change.</p>
<div class="table-shell"><table><thead><tr><th>ID</th><th>Priority</th><th>Task</th><th>Nodes</th><th>Depends on</th><th>Owner files</th></tr></thead><tbody>""" + task_rows + """</tbody></table></div></section>
<section><h2>Execution profiles</h2><div class="profiles">""" + "".join(profile_sections) + """</div></section>
<section><h2>Data cleaning layers</h2><ul class="cards">""" + cleaning_cards + """</ul></section>
<section><h2>Metric lineage</h2><ul class="cards">""" + lineage_cards + """</ul></section>
<footer>Generated from config/data-routing.json. Do not edit this file by hand.</footer>
</main></body></html>
"""


def render_document_authority_markdown(document: Mapping[str, Any]) -> str:
    documents, unregistered = _validate_document_authority(document)
    authority = document["documentAuthority"]
    execution = document["agentExecution"]
    information_classes = _named(
        execution["informationClasses"],
        "id",
        "information classes",
    )
    document_classes = _document_class_map(
        execution["progressiveDisclosure"],
        documents,
        information_classes,
    )
    lines = [
        "# CARDZ Document Authority",
        "",
        "> Generated from `config/data-routing.json`. Do not edit this file by hand.",
        "",
        "A document that is not listed as `active` or `generated` below cannot direct an Agent.",
        "Unregistered Markdown defaults to `reference_only`. Files under `temp/`, `docs/evidence/`,",
        "`docs/archive/`, and `docs/mockups/` are never execution entrypoints.",
        "",
        "## Dispatcher entry sequence",
        "",
        "Workers do not follow this list in bulk. They use the exact worker initial",
        "set and one generated role pack defined by progressive disclosure.",
        "",
    ]
    for index, document_id in enumerate(authority["entrySequence"], start=1):
        item = documents[document_id]
        lines.append(f"{index}. `{item['path']}` — `{document_id}`.")
    lines.extend([
        "",
        "## Active authority domains",
        "",
        "| Domain | Document | Path | Information class | Claim policy |",
        "| --- | --- | --- | --- | --- |",
    ])
    domain_rows = sorted(
        (domain, document_id, item)
        for document_id, item in documents.items()
        if item["status"] in {"active", "generated"}
        for domain in item["authorityDomains"]
    )
    for domain, document_id, item in domain_rows:
        lines.append(
            f"| `{domain}` | `{document_id}` | `{item['path']}` | "
            f"`{document_classes[document_id]}` | `{item['claimPolicy']}` |"
        )
    lines.extend([
        "",
        "## Documents allowed to direct execution",
        "",
        "| ID | Status | Kind | Information class | Path |",
        "| --- | --- | --- | --- | --- |",
    ])
    for document_id, item in sorted(documents.items()):
        if item["instructional"] and item["executionAllowed"]:
            lines.append(
                f"| `{document_id}` | `{item['status']}` | `{item['kind']}` | "
                f"`{document_classes[document_id]}` | `{item['path']}` |"
            )
    lines.extend([
        "",
        "## Generated views — never hand-edit",
        "",
        "| ID | Path | Generator |",
        "| --- | --- | --- |",
    ])
    for document_id, item in sorted(documents.items()):
        if item["status"] == "generated":
            lines.append(
                f"| `{document_id}` | `{item['path']}` | `{item['generatorToolId']}` |"
            )
    lines.extend([
        "",
        "## Blocked and cleanup queue",
        "",
        "| ID | Status | Information class | Path | Replacement | Execution |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for document_id, item in sorted(documents.items()):
        if item["status"] in {
            "reference_only",
            "superseded",
            "historical",
            "quarantine",
            "delete_candidate",
        }:
            lines.append(
                "| `{id}` | `{status}` | `{class_id}` | `{path}` | {replacement} | denied |".format(
                    id=document_id,
                    status=item["status"],
                    class_id=document_classes[document_id],
                    path=item["path"],
                    replacement=(
                        f"`{item['supersededBy']}`"
                        if item.get("supersededBy")
                        else "—"
                    ),
                )
            )
    lines.extend([
        "",
        "## Unregistered Markdown",
        "",
        "These files are discoverable evidence only. They are automatically `reference_only`",
        "and cannot override an active authority or provide an executable command.",
        "",
    ])
    if unregistered:
        lines.extend(f"- `{path}`" for path in unregistered)
    else:
        lines.append("- None.")
    lines.extend([
        "",
        "## Conflict rules",
        "",
        "1. Timeless invariants define identity, authority, dependency order, ownership,",
        "   stop conditions, and feedback routing; they never contain current business numbers.",
        "2. Versioned contracts define schemas, formulas, reason codes, tool interfaces,",
        "   and QC predicates for one declared version or hash.",
        "3. Fresh runtime evidence can update volatile facts through a registered writer,",
        "   but cannot redefine architecture or field semantics.",
        "4. `config/data-routing.json` is the only handwritten architecture, document-authority, and Agent-assignment source.",
        "5. Typed public schema and validators own public property shape; prose cannot create a field.",
        "6. Generated views explain the registry and are replaced only by the generator.",
        "7. Dated, historical, quarantined, superseded, reference-only, unregistered, archive, evidence, mockup, and temp content cannot direct execution.",
        "8. A delete candidate may be deleted only after zero active inbound references and preservation review.",
        "",
    ])
    return "\n".join(lines)


def render_agent_execution_funnel_markdown(document: Mapping[str, Any]) -> str:
    architecture_nodes, work_items = _validate_architecture(document)
    documents, _ = _validate_document_authority(document)
    roles, claims, waves, _assignments = _validate_agent_execution(
        document,
        documents,
        architecture_nodes,
        work_items,
    )
    execution = document["agentExecution"]
    information_classes = _named(
        execution["informationClasses"],
        "id",
        "information classes",
    )
    feedback_categories = _named(
        execution["feedbackProtocol"]["categories"],
        "id",
        "feedback categories",
    )
    role_stages: dict[str, list[str]] = {role_id: [] for role_id in roles}
    for wave in waves.values():
        for stage in wave["roleStages"]:
            role_stages[stage.split(".", 1)[0]].append(stage)
    lines = [
        "# CARDZ Agent Execution Funnel",
        "",
        "> Generated from `config/data-routing.json`. Do not edit this file by hand.",
        "",
        "This is the dispatcher index for A01–A12. Workers load the invariant kernel,",
        "their one role pack, one work item, and only then the exact runtime material.",
        "A role does not gain DB, public, pointer, timer, deploy, commit, push, or",
        "release authority merely because it can read a file.",
        "",
        "## Progressive disclosure",
        "",
        "| Level | Context | Information class | Load rule |",
        "| --- | --- | --- | --- |",
    ]
    for level in execution["progressiveDisclosure"]["levels"]:
        lines.append(
            f"| `{level['id']}` | {level['label']} | `{level['informationClassId']}` | "
            f"{level['loadRule']} |"
        )
    lines.extend([
        "",
        "Workers must not bulk-load all role packs, manuals, historical reports, skills,",
        "or source trees. Adapter availability, current numbers, current statuses, and",
        "source availability are runtime material, never architecture truth.",
        "",
        f"Tool gate: {execution['policy']['toolExecutionPolicy']}.",
        "",
        "## Information durability",
        "",
        "| Class | Mutation boundary | Purpose |",
        "| --- | --- | --- |",
    ])
    for item in information_classes.values():
        lines.append(
            f"| `{item['id']}` | `{item['mutability']}` | {item['purpose']} |"
        )
    lines.extend([
        "",
        "### Registry compartment map",
        "",
        "| Section selector | Information class | Consumer rule |",
        "| --- | --- | --- |",
    ])
    for item in execution["progressiveDisclosure"]["sectionClassifications"]:
        lines.append(
            f"| `{item['selector']}` | `{item['informationClassId']}` | "
            f"{item['consumerRule']} |"
        )
    lines.extend([
        "",
        "## Worker preflight",
        "",
        "1. Read `AGENTS.md` and `docs/generated/DOCUMENT_AUTHORITY.md`.",
        "2. Read exactly `docs/generated/roles/<AGENT_ID>.md`; do not read the other role packs.",
        "3. Run `python -X utf8 scripts/backend.py explain <AGENT_ID>`.",
        "4. Run `python -X utf8 scripts/backend.py explain <WORK_ITEM_ID>`.",
        "5. Run `python -X utf8 scripts/backend.py explain document:<DOCUMENT_ID>` before",
        "   loading the one required document needed for the immediate decision.",
        "6. Load only prompt-named baseline, cohort, current-state slice, input artifacts,",
        "   and capability route whose `loadWhen` condition is true.",
        "7. Derive exact `allowed_tool_ids` from the assigned work-item nodes and role stage;",
        "   a registered tool that is not in that intersection remains forbidden.",
        "8. Declare exact `read_set`, `write_set`, and `generated_set` before editing.",
        "9. Stop the affected write if it is unowned, overlaps another claim, or needs a",
        "   later dependency; continue only independent read-only work.",
        "",
        "## Dependency waves",
        "",
        "| Wave | Purpose | Role stages | Depends on |",
        "| --- | --- | --- | --- |",
    ])
    for wave in waves.values():
        lines.append(
            "| `{id}` | {label} | {roles} | {deps} |".format(
                id=wave["id"],
                label=wave["label"],
                roles=", ".join(f"`{value}`" for value in wave["roleStages"]),
                deps=", ".join(f"`{value}`" for value in wave["dependsOn"]) or "—",
            )
        )
    lines.extend([
        "",
        "A11 is one role with three serialized stages: `bootstrap`, `control`, and `runtime`.",
        "It is not three concurrent writers.",
        "",
        "## Department router",
        "",
        "| Role | Exact role pack | Stage(s) | Mission | Exclusive claims |",
        "| --- | --- | --- | --- | --- |",
    ])
    for role in roles.values():
        pack = documents[role["roleDocumentId"]]["path"].removeprefix("docs/generated/")
        lines.append(
            "| `{id}` {label} | [{pack}]({pack}) | {stages} | {mission} | {claims} |".format(
                id=role["id"],
                label=role["label"],
                pack=pack,
                stages=", ".join(f"`{value}`" for value in role_stages[role["id"]]),
                mission=role["mission"],
                claims="<br>".join(f"`{value}`" for value in role["writeClaimIds"]) or "none",
            )
        )
    lines.extend([
        "",
        "## Dispatch-time assignment lookup",
        "",
        "Assignment owner, work-item status, baselines, cohorts, and dependency receipts",
        "are volatile material and are deliberately not copied into this durable funnel.",
        "MAIN must query them at dispatch time:",
        "",
        "```powershell",
        "python -X utf8 scripts/backend.py work-items --status planned",
        "python -X utf8 scripts/backend.py work-items --status in_progress",
        "python -X utf8 scripts/backend.py explain <WORK_ITEM_ID>",
        "```",
        "",
        "Dispatch exactly one open registered role-stage item and its immutable baseline.",
        "A worker must not infer scope from another work item or from a dated report.",
        "",
        "## Feedback and blocked-contract routing",
        "",
        "| Category | Agent action | Decision owner | Architecture change |",
        "| --- | --- | --- | --- |",
    ])
    for category in feedback_categories.values():
        lines.append(
            "| `{id}` | {action} | `{owner}` | `{architecture}` |".format(
                id=category["id"],
                action=category["agentAction"],
                owner=category["decisionOwner"],
                architecture=str(category["architectureChange"]).lower(),
            )
        )
    lines.extend([
        "",
        execution["feedbackProtocol"]["softFactRule"],
        "",
        execution["feedbackProtocol"]["hardInvariantRule"],
        "",
        "## Child-Agent delegation envelope",
        "",
        f"- Default child mode: `{execution['delegationPolicy']['defaultChildMode']}`.",
        f"- Authority: {execution['delegationPolicy']['authorityInheritance']}.",
        f"- Cross-role need: {execution['delegationPolicy']['crossRolePolicy']}.",
        "- A child receives no implicit DB, pointer, timer, public, deploy, commit, push,",
        "  or cross-role write authority.",
        "- A writing child requires every condition below:",
    ])
    lines.extend(
        f"  - {value}."
        for value in execution["delegationPolicy"]["childWriteRequirements"]
    )
    lines.extend([
        "- Every child prompt must carry:",
    ])
    lines.extend(
        f"  - `{value}`"
        for value in execution["delegationPolicy"]["requiredChildPromptFields"]
    )
    lines.extend([
        "",
        "## Exclusive write claims",
        "",
        "| Claim | Owner | Mode | Paths |",
        "| --- | --- | --- | --- |",
    ])
    for claim in claims.values():
        lines.append(
            "| `{id}` | `{owner}` | `{mode}` | {paths} |".format(
                id=claim["id"],
                owner=claim["ownerRoleId"],
                mode=claim["mode"],
                paths="<br>".join(f"`{value}`" for value in claim["paths"]),
            )
        )
    lines.extend([
        "",
        "## Dispatch contract",
        "",
        "Copy this block and replace every placeholder. Give the worker the one role-pack",
        "path and one work-item ID; do not paste all twelve role contracts:",
        "",
        "```text",
        "INTENT_FIDELITY=STRICT",
        "YAGNI=STRICT",
        "NO_PLACEHOLDERS_ALLOWED=true",
        "WAVE_ID=<exact registered wave>",
        "ROLE_STAGE=<exact registered role stage>",
        "AGENT_ID=<A01..A12>",
        "ROLE_PACK=docs/generated/roles/<AGENT_ID>.md",
        "ROLE_MODE=<exact roles[].mode>",
        "WORK_ITEM_ID=<one registered planned/in_progress role-stage work item>",
        "WORK_ITEM_STATUS=<planned|in_progress>",
        "WRITE_CLAIM_ID=<exact registered claim or NONE>",
        "CLAIM_MODE=<exact registered claim mode or read_only>",
        "ALLOWED_TOOL_IDS=<exact work-item-node and role-stage intersection or NONE>",
        "BASELINE_ID=<immutable id>",
        "BASELINE_MANIFEST=<exact path>",
        "BASELINE_MANIFEST_SHA256=<exact hash>",
        "COHORT_SHA256=<exact hash>",
        "ROUTING_SHA256=<exact hash>",
        "DB_STATE_FINGERPRINT=<exact hash or NONE for pre-fingerprint audit>",
        "AS_OF=<exact timestamp or evidence window>",
        "READ_SET=<exact roots/tables>",
        "REGISTERED_CLAIM_PATHS=<exact paths from WRITE_CLAIM_ID or NONE>",
        "WRITE_SET=<exact subset of REGISTERED_CLAIM_PATHS; empty for audit>",
        "GENERATED_SET=<exact generator-owned outputs or empty>",
        "ROLE_BLOCKED_ACTIONS=<exact role-pack blockedActions>",
        "DISPATCH_BLOCKED_ACTIONS=<additional restrictions>",
        "FORBIDDEN=<union of role and dispatch restrictions>",
        "INPUT_ARTIFACTS=<content-addressed paths and hashes>",
        "OUTPUT_ROOT=<private agent/run-specific path>",
        "DEPENDENCY_RECEIPTS=<required terminal receipt IDs and hashes>",
        "CAPABILITY_ROUTE_ID=<one exact triggered route or NONE>",
        "RUNTIME_MANUAL_ID=<one exact required document or NONE>",
        "RUNTIME_VERIFIER_TOOL_IDS=<exact document contract or NONE>",
        "ACCEPTANCE=<machine predicates and exact commands>",
        "HANDOFF_TO=<next role or MAIN>",
        "FEEDBACK_ROUTE=<MAIN unless the role pack says the fact is role-owned>",
        "",
        "Every placeholder must be replaced. Assignment owner, roleStage, wave, role,",
        "work item and write claim must agree exactly. A prompt may narrow authority but",
        "never widen it. A11.bootstrap, A11.control and A11.runtime are serialized.",
        "",
        "Do not choose a replacement source, field meaning, script, cohort, fallback, or",
        "authority document. If a contract is unresolved, return BLOCKED with evidence and",
        "the precise owner decision required. Never turn missing into zero, harvested into",
        "DB-written, or DB-written into QC-green.",
        "```",
        "",
        "## Mandatory handoff fields",
        "",
    ])
    lines.extend(f"- `{value}`" for value in execution["policy"]["requiredHandoffFields"])
    lines.extend([
        "",
        "MAIN is the only shared registry/generated-doc integrator. `mainHermes` retains",
        "release authority, and production promotion still requires DADDY's separate approval.",
        "",
    ])
    return "\n".join(lines)


def render_agent_role_pack_markdown(
    document: Mapping[str, Any],
    role_id: str,
) -> str:
    """Render one durable, demand-loaded role contract without live values."""

    architecture_nodes, work_items = _validate_architecture(document)
    documents, _ = _validate_document_authority(document)
    roles, claims, waves, _ = _validate_agent_execution(
        document,
        documents,
        architecture_nodes,
        work_items,
    )
    if role_id not in roles:
        raise RegistryError(f"unknown agent role pack: {role_id}")
    execution = document["agentExecution"]
    capabilities = _named(
        execution["capabilityRoutes"],
        "id",
        "capability routes",
    )
    role = roles[role_id]
    pack_path = documents[role["roleDocumentId"]]["path"]
    pack_dir = posixpath.dirname(pack_path)
    stages = [
        stage
        for wave in waves.values()
        for stage in wave["roleStages"]
        if stage.split(".", 1)[0] == role_id
    ]
    lines = [
        f"# {role_id} — {role['label']}",
        "",
        "> Durable generated role contract. It intentionally contains no live counts,",
        "> current prices, current completion claims, or installed-plugin state.",
        "",
        "## Exact load envelope",
        "",
        f"1. Read [`AGENTS.md`]({posixpath.relpath('AGENTS.md', pack_dir)}).",
        "2. Read [`DOCUMENT_AUTHORITY.md`](../DOCUMENT_AUTHORITY.md).",
        f"3. Read this `{role_id}` pack only; do not read the other eleven role packs.",
        f"4. Run `python -X utf8 scripts/backend.py explain {role_id}`.",
        "5. Run `python -X utf8 scripts/backend.py explain <ASSIGNED_WORK_ITEM_ID>`.",
        "6. Before a required document, run `python -X utf8 scripts/backend.py explain document:<DOCUMENT_ID>`.",
        "7. Load one required document or one triggered capability at a time.",
        "8. Load volatile baseline, PROJECT_STATE slice, cohort and input receipts only",
        "   when named by the work item or dispatcher prompt.",
        "",
        "## Stable role contract",
        "",
        f"- Layer: `{role['layer']}`.",
        f"- Mode: `{role['mode']}`.",
        f"- Stage(s): {', '.join(f'`{value}`' for value in stages)}.",
        f"- Mission: {role['mission']}",
        f"- Architecture nodes: {', '.join(f'`{value}`' for value in role['nodeIds'])}.",
        "",
        "### Read scope",
        "",
    ]
    lines.extend(f"- `{value}`" for value in role["readPaths"])
    lines.extend([
        "",
        "### Required documents — demand-load only",
        "",
    ])
    for document_id in role["requiredDocumentIds"]:
        item = documents[document_id]
        relative = posixpath.relpath(item["path"], pack_dir)
        lines.append(
            f"- [`{document_id}`]({relative}) — first run "
            f"`backend.py explain document:{document_id}`; load only when the current "
            "decision needs it."
        )
    lines.extend([
        "",
        "### Owned write claims",
        "",
    ])
    for claim_id in role["writeClaimIds"]:
        claim = claims[claim_id]
        lines.append(f"- `{claim_id}` (`{claim['mode']}`)")
        lines.extend(f"  - `{value}`" for value in claim["paths"])
    lines.extend([
        "",
        "No file outside these claims becomes writable through delegation, capability",
        "loading, repository discovery, or another Agent's request.",
        "",
        "### Capability routes — trigger before load",
        "",
    ])
    for capability_id in role["capabilityRouteIds"]:
        capability = capabilities[capability_id]
        lines.extend([
            f"- `{capability_id}`",
            f"  - Load when: {capability['loadWhen']}.",
            "  - Candidate adapters: "
            + ", ".join(f"`{value}`" for value in capability["adapterCandidates"])
            + ".",
            f"  - If absent: {capability['missingAdapterPolicy']}.",
            f"  - Output boundary: `{capability['outputRule']}`.",
        ])
    lines.extend([
        "",
        "Adapter availability is volatile material discovered at runtime. A listed",
        "candidate is not proof that a plugin or skill is installed, connected, loaded,",
        "or authorized. Read exactly one selected `SKILL.md` only after its trigger is true.",
        "",
        "## Child-Agent rules",
        "",
        f"- Default: `{execution['delegationPolicy']['defaultChildMode']}`.",
        f"- {execution['delegationPolicy']['authorityInheritance']}.",
        f"- {execution['delegationPolicy']['crossRolePolicy']}.",
        "- A child writer requires MAIN to partition a non-overlapping subclaim and must",
        "  return to this parent for integration; otherwise the child is read-only.",
        "- Every child prompt must name:",
    ])
    lines.extend(
        f"  - `{value}`"
        for value in execution["delegationPolicy"]["requiredChildPromptFields"]
    )
    lines.extend([
        "",
        "## Blocked actions",
        "",
    ])
    lines.extend(f"- `{value}`" for value in role["blockedActions"])
    lines.extend([
        "",
        "## Feedback decision",
        "",
        "- `FACT_UPDATE`: update only via the role-owned candidate/registered writer with",
        "  provenance, freshness and read-back evidence; do not edit architecture.",
        "- `IMPLEMENTATION_DEFECT`: fix only inside the existing claim and acceptance.",
        "- `CROSS_ROLE_DEPENDENCY`: pause the dependent write and route a structured request to MAIN.",
        "- `CONTRACT_GAP`: stop the affected decision and propose a versioned contract change.",
        "- `INVARIANT_CONFLICT`: stop; only DADDY + MAIN may accept and version the change.",
        "- `SECURITY_OR_PRIVACY`: stop the affected path and return redacted evidence.",
        "",
        "The feedback record must contain:",
        "",
    ])
    lines.extend(
        f"- `{value}`"
        for value in execution["feedbackProtocol"]["requiredFields"]
    )
    lines.extend([
        "",
        "## Acceptance",
        "",
    ])
    lines.extend(f"- {value}." for value in role["acceptance"])
    lines.extend([
        "",
        "## Handoff",
        "",
        "- Next role(s): "
        + ", ".join(f"`{value}`" for value in role["handoffTo"])
        + ".",
        "- Return the common handoff fields below with exact commands and raw output:",
    ])
    lines.extend(
        f"  - `{value}`"
        for value in execution["policy"]["requiredHandoffFields"]
    )
    lines.extend([
        "",
        "Do not turn a changing fact into a hard-coded rule, a missing value into zero,",
        "a discovered adapter into authority, or a blocked contract into a silent fallback.",
        "",
    ])
    return "\n".join(lines)


def render_agent_execution_architecture_html(document: Mapping[str, Any]) -> str:
    architecture_nodes, work_items = _validate_architecture(document)
    documents, _ = _validate_document_authority(document)
    roles, claims, waves, _ = _validate_agent_execution(
        document,
        documents,
        architecture_nodes,
        work_items,
    )
    wave_values = list(waves.values())
    width = 1600
    height = 1080
    column_width = 250
    wave_x = [45 + index * column_width for index in range(len(wave_values))]
    entry_nodes = [
        ("L0 · AGENTS.md", "timeless kernel", "#22d3ee"),
        ("L0 · DOC AUTHORITY", "trust filter", "#fb7185"),
        ("L1 · ONE ROLE PACK", "department scope", "#34d399"),
        ("L2 · WORK ITEM", "backend explain", "#fbbf24"),
        ("L4 · BASELINE", "volatile raw material", "#a78bfa"),
    ]
    entry_boxes: list[str] = []
    entry_arrows: list[str] = []
    for index, (label, sublabel, color) in enumerate(entry_nodes):
        x = 55 + index * 305
        if index:
            entry_arrows.append(
                f'<line x1="{x - 55}" y1="95" x2="{x - 8}" y2="95" '
                'stroke="#64748b" stroke-width="2" marker-end="url(#arrowhead)"/>'
            )
        entry_boxes.append(
            f'<rect x="{x}" y="60" width="250" height="70" rx="8" fill="#0f172a"/>'
            f'<rect x="{x}" y="60" width="250" height="70" rx="8" fill="{color}22" '
            f'stroke="{color}" stroke-width="1.5"/>'
            f'<text x="{x + 125}" y="88" fill="white" font-size="12" font-weight="700" '
            f'text-anchor="middle">{html.escape(label)}</text>'
            f'<text x="{x + 125}" y="109" fill="#94a3b8" font-size="9" '
            f'text-anchor="middle">{html.escape(sublabel)}</text>'
        )
    wave_arrows: list[str] = []
    for index in range(len(wave_values) - 1):
        x1 = wave_x[index] + 220
        x2 = wave_x[index + 1] - 10
        wave_arrows.append(
            f'<line x1="{x1}" y1="255" x2="{x2}" y2="255" '
            'stroke="#fbbf24" stroke-width="2" marker-end="url(#arrowhead)"/>'
        )
    layer_colors = {
        "L1": "#22d3ee",
        "L2": "#a78bfa",
        "L3": "#34d399",
        "L1_L2": "#fbbf24",
        "L2_L3": "#fbbf24",
        "L1_L3": "#fb923c",
        "ALL": "#fb7185",
    }
    wave_groups: list[str] = []
    for index, wave in enumerate(wave_values):
        x = wave_x[index]
        group = [
            f'<rect x="{x - 10}" y="205" width="240" height="720" rx="12" fill="transparent" '
            'stroke="#475569" stroke-width="1" stroke-dasharray="8,4"/>',
            f'<text x="{x + 110}" y="236" fill="#fbbf24" font-size="10" font-weight="700" '
            f'text-anchor="middle">{html.escape(wave["id"])}</text>',
            f'<text x="{x + 110}" y="274" fill="#94a3b8" font-size="8" '
            f'text-anchor="middle">{html.escape(wave["label"][:34])}</text>',
        ]
        for role_index, stage in enumerate(wave["roleStages"]):
            role_id = stage.split(".", 1)[0]
            role = roles[role_id]
            color = layer_colors.get(role["layer"], "#94a3b8")
            y = 310 + role_index * 96
            claim_label = ", ".join(role["writeClaimIds"])
            stage_label = stage if stage != role_id else role_id
            group.extend([
                f'<rect x="{x}" y="{y}" width="220" height="74" rx="7" fill="#0f172a"/>',
                f'<rect x="{x}" y="{y}" width="220" height="74" rx="7" fill="{color}22" '
                f'stroke="{color}" stroke-width="1.5"/>',
                f'<text x="{x + 110}" y="{y + 17}" fill="white" font-size="9" '
                f'font-weight="700" text-anchor="middle">{html.escape(stage_label)}</text>',
                f'<text x="{x + 110}" y="{y + 34}" fill="white" font-size="8" '
                f'font-weight="600" text-anchor="middle">{html.escape(role["label"][:38])}</text>',
                f'<text x="{x + 110}" y="{y + 49}" fill="#94a3b8" font-size="7" '
                f'text-anchor="middle">{html.escape(role["layer"])}</text>',
                f'<text x="{x + 110}" y="{y + 64}" fill="{color}" font-size="7" '
                f'text-anchor="middle">{html.escape(claim_label[:38])}</text>',
            ])
        wave_groups.append("".join(group))
    legend_items = "".join(
        f'<rect x="{70 + index * 190}" y="978" width="16" height="10" rx="2" fill="{color}22" '
        f'stroke="{color}"/><text x="{92 + index * 190}" y="987" fill="#94a3b8" '
        f'font-size="8">{html.escape(layer)}</text>'
        for index, (layer, color) in enumerate(layer_colors.items())
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>CARDZ Agent Execution Architecture</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#020617;color:white;font-family:ui-monospace,SFMono-Regular,Consolas,monospace;padding:2rem}}
.container{{max-width:1700px;margin:auto}}header{{margin-bottom:2rem}}.title{{display:flex;align-items:center;gap:1rem}}
.dot{{width:12px;height:12px;border-radius:50%;background:#22d3ee;animation:pulse 2s infinite}}@keyframes pulse{{50%{{opacity:.45}}}}
h1{{font-size:1.55rem;margin:0}}header p{{color:#94a3b8;font-size:.86rem;margin:.6rem 0 0 1.8rem}}
.diagram{{background:rgba(15,23,42,.55);border:1px solid #1e293b;border-radius:1rem;padding:1.5rem;overflow:auto}}
svg{{display:block;width:100%}}.cards{{display:grid;grid-template-columns:repeat(4,minmax(240px,1fr));gap:1rem;margin-top:1.5rem}}
.card{{background:rgba(15,23,42,.55);border:1px solid #1e293b;border-radius:.75rem;padding:1.1rem}}
.card h2{{font-size:.85rem;margin:0 0 .7rem}}.card p{{font-size:.74rem;color:#94a3b8;line-height:1.55;margin:0}}
footer{{text-align:center;color:#475569;font-size:.7rem;margin-top:1.4rem}}@media(max-width:900px){{.cards{{grid-template-columns:1fr}}}}
</style></head><body><div class="container">
<header><div class="title"><span class="dot"></span><h1>CARDZ Agent Execution Architecture</h1></div>
<p>Demand-loaded context, six dependency waves, twelve specialist roles, and serialized shared writers.</p></header>
<div class="diagram"><svg viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">
<title id="title">CARDZ document and multi-Agent execution funnel</title>
<desc id="desc">Agents load timeless rules, document authority, one role pack, one work item and one immutable baseline before executing in dependency waves.</desc>
<defs><marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto"><polygon points="0 0,10 3.5,0 7" fill="#64748b"/></marker>
<pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse"><path d="M40 0L0 0 0 40" fill="none" stroke="#1e293b" stroke-width=".5"/></pattern></defs>
<rect width="100%" height="100%" fill="url(#grid)"/>
{''.join(entry_arrows)}{''.join(wave_arrows)}
{''.join(entry_boxes)}
<text x="55" y="180" fill="#e2e8f0" font-size="13" font-weight="700">DEPENDENCY WAVES</text>
{''.join(wave_groups)}
<text x="55" y="953" fill="#e2e8f0" font-size="10" font-weight="700">LEGEND</text>{legend_items}
</svg></div>
<div class="cards">
<article class="card"><h2 style="color:#22d3ee">Progressive disclosure</h2><p>Each worker loads one role pack, one work item, triggered capabilities, and exact evidence—not all departments or skills.</p></article>
<article class="card"><h2 style="color:#a78bfa">Timeless vs volatile</h2><p>Architecture holds invariants and versioned contracts. Counts, prices, statuses, source health, and adapter availability remain runtime material.</p></article>
<article class="card"><h2 style="color:#fbbf24">Single shared writers</h2><p>MAIN owns the registry and generated docs. Canonical DB apply, pointer promotion, timers, and release remain serialized approval gates.</p></article>
<article class="card"><h2 style="color:#fb7185">Feedback without bypass</h2><p>Soft facts use registered writers. Contract gaps return to MAIN; invariant conflicts stop for DADDY + MAIN versioned approval.</p></article>
</div><footer>Generated from config/data-routing.json • Do not edit by hand</footer>
</div></body></html>
"""


def generated_documents(document: Mapping[str, Any]) -> dict[str, str]:
    generated = {
        "TOOL_REGISTRY.md": render_markdown(document),
        "DATA_LINEAGE.html": render_html_graph(document),
        "DATA_ROUTING.json": render_json(document),
        "DOCUMENT_AUTHORITY.md": render_document_authority_markdown(document),
        "AGENT_EXECUTION_FUNNEL.md": render_agent_execution_funnel_markdown(document),
        "AGENT_EXECUTION_ARCHITECTURE.html": render_agent_execution_architecture_html(document),
    }
    generated.update(
        {
            f"roles/{role_id}.md": render_agent_role_pack_markdown(document, role_id)
            for role_id in sorted(
                role["id"]
                for role in document["agentExecution"]["roles"]
            )
        }
    )
    return generated


def generate_docs(document: Mapping[str, Any], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in generated_documents(document).items():
        path = output_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
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
