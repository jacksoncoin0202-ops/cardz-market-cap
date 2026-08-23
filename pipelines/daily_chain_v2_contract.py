#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pure contracts and policies for the CARDZ daily-chain V2.

This module deliberately has no database, browser, scheduler, or network
dependency.  The orchestrator consumes these contracts; adapters own provider
details.  Keeping the decision rules pure makes the one consolidated test run
able to exercise every failure branch without touching production systems.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol, Sequence


CONTRACT_VERSION = "cardz-daily-chain-v2"
JST_OFFSET = timedelta(hours=9)
SOURCE_REQUIRED_CLASSES = frozenset({"core", "quote", "extra"})
# Adding a data source must stay a registry row + migration file + adapter
# registration.  Every constant below is only the no-registry fallback.
V2_MIGRATION_GLOB = "0[5-9][0-9]_daily_chain_v2_*.mysql.sql"
CORE_CONTRACT_FALLBACK_KEYS = ("fx", "gemrate", "quotes")
IDENTITY_LANE_FALLBACK = (("browser", "cdp:9333"), ("http", "host:snkrdunk"))
DEFAULT_ROUTE_PRIORITY = 30
ROUTE_POLICY_VERSION = "cardz-route-v2"
# The token the orchestrator looks for to decide that a failed barrier is
# repairable coverage rather than an infrastructure fault.
CONTRACT_SHORTFALL_MARKER = "Missing="
# Wall-clock twin of the orchestrator's monotonic work deadline, exported
# to every stage subprocess so a stage can shrink its own work instead of
# being killed mid-fetch at the cutoff.
WORK_DEADLINE_ENV = "CARDZ_V2_WORK_DEADLINE_EPOCH"
# The orchestrator refuses to start work inside this reserve; a stage that
# reads its wall-clock deadline honours the same number rather than a second
# opinion about how much of a tick is left.  One definition, both processes.
TICK_RESERVE_SECONDS = 30
RESULT_STATUSES = frozenset({
    "completed", "degraded", "quarantined", "retry", "terminal", "interrupted",
})
TRANSIENT_RETRY_SECONDS = (60, 120, 240, 480, 960, 1800)
INFRA_RETRY_SECONDS = (30, 60, 120)
PUBLISH_RETRY_SECONDS = (120, 300, 600, 1200, 1800)
# audit P2-15: scripts/daily_public_release.sh exits 75 when the legacy
# publisher already holds the release flock.  The orchestrator stamps the
# marker on the error text so a held lock never reads as a broken release.
PUBLISH_LOCK_EXIT_CODE = 75
PUBLISH_LOCK_MARKER = "publisher-lock-held"


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _clean(value: Any) -> str:
    return str(value or "").strip()


@dataclass(frozen=True)
class SourceSpec:
    source_code: str
    capabilities: tuple[str, ...]
    transport: str
    concurrency_group: str
    max_concurrency: int
    cadence: str
    freshness_sla_minutes: int
    required_class: str
    adapter_version: str = "1"
    enabled: bool = True
    # Declared, never branched on: the orchestrator reads these instead of
    # naming providers.  None means "this source owns no discovery lane" and
    # "let the registry stage apply the default route priority".
    identity_lane: str | None = None
    route_priority: int | None = None
    # Declared, never branched on: this source's worker cannot finish inside one
    # claim window, so the tick interrupts it on ordinary business dates and the
    # work resumes from on-disk progress.  The orchestrator reads it to size the
    # attempt budget instead of naming the provider.
    resumable_sweep: bool = False

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,63}", self.source_code):
            raise ValueError(f"invalid source_code: {self.source_code!r}")
        if not self.capabilities or any(not _clean(item) for item in self.capabilities):
            raise ValueError(f"{self.source_code}: capabilities are required")
        if not _clean(self.concurrency_group):
            raise ValueError(f"{self.source_code}: concurrency_group is required")
        if int(self.max_concurrency) < 1:
            raise ValueError(f"{self.source_code}: max_concurrency must be positive")
        if int(self.freshness_sla_minutes) < 1:
            raise ValueError(f"{self.source_code}: freshness SLA must be positive")
        if self.required_class not in SOURCE_REQUIRED_CLASSES:
            raise ValueError(
                f"{self.source_code}: required_class must be one of"
                f" {sorted(SOURCE_REQUIRED_CLASSES)}"
            )


@dataclass(frozen=True)
class SourceTask:
    run_id: str
    business_date: str
    source_code: str
    capability: str
    variant_id: int | None = None
    external_id: str | None = None
    shard: str = "all"
    input_revision: str = "1"
    checkpoint: Mapping[str, Any] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        # The readable prefix makes operations usable without weakening the
        # digest that protects long IDs and arbitrary provider external IDs.
        material = {
            "businessDate": self.business_date,
            "sourceCode": self.source_code,
            "capability": self.capability,
            "variantId": self.variant_id,
            "externalId": self.external_id,
            "shard": self.shard,
            "inputRevision": self.input_revision,
        }
        return (
            f"{self.business_date}:{self.source_code}:{self.capability}:"
            f"{self.shard}:{sha256(material)[:24]}"
        )


@dataclass(frozen=True)
class SourceResult:
    status: str
    observed_at: str
    checked_at: str
    native_currency: str | None = None
    native_value: str | None = None
    usd_value: str | None = None
    payload_sha256: str | None = None
    evidence_ref: str | None = None
    error_code: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict)
    detail: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in RESULT_STATUSES:
            raise ValueError(f"invalid result status: {self.status}")
        if self.payload_sha256 and not re.fullmatch(r"[0-9a-f]{64}", self.payload_sha256):
            raise ValueError("payload_sha256 must be lowercase sha256")
        if self.native_value is not None:
            _positive_decimal(self.native_value, "native_value")
        if self.usd_value is not None:
            _positive_decimal(self.usd_value, "usd_value")


def _positive_decimal(value: Any, field_name: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError(f"{field_name} is not numeric") from error
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field_name} must be finite and positive")
    return parsed


class SourceAdapter(Protocol):
    """Provider plug-in contract.  Core orchestration never branches by source."""

    spec: SourceSpec

    def plan(self, context: Mapping[str, Any]) -> Sequence[SourceTask]: ...

    def execute(
        self,
        task: SourceTask,
        context: Mapping[str, Any],
        heartbeat: "Heartbeat",
    ) -> Mapping[str, Any]: ...

    def ingest(
        self,
        task: SourceTask,
        execution: Mapping[str, Any],
        context: Mapping[str, Any],
    ) -> SourceResult: ...


class Heartbeat(Protocol):
    def __call__(self, *, worker_pid: int | None = None, checkpoint: Any = None) -> None: ...


class AdapterRegistry:
    def __init__(self, adapters: Iterable[SourceAdapter] = ()) -> None:
        self._adapters: dict[str, SourceAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: SourceAdapter) -> None:
        code = adapter.spec.source_code
        if code in self._adapters:
            raise ValueError(f"duplicate source adapter: {code}")
        self._adapters[code] = adapter

    def enabled(self) -> tuple[SourceAdapter, ...]:
        return tuple(
            self._adapters[code]
            for code in sorted(self._adapters)
            if self._adapters[code].spec.enabled
        )

    def get(self, source_code: str) -> SourceAdapter:
        try:
            return self._adapters[source_code]
        except KeyError as error:
            raise KeyError(f"source adapter is not registered: {source_code}") from error

    def plan_all(self, context: Mapping[str, Any]) -> list[SourceTask]:
        tasks: list[SourceTask] = []
        seen: set[str] = set()
        for adapter in self.enabled():
            for task in adapter.plan(context):
                if task.source_code != adapter.spec.source_code:
                    raise ValueError(
                        f"adapter {adapter.spec.source_code} planned foreign task"
                        f" {task.source_code}"
                    )
                key = task.idempotency_key
                if key in seen:
                    raise ValueError(f"duplicate source task idempotency key: {key}")
                seen.add(key)
                tasks.append(task)
        return tasks


@dataclass(frozen=True)
class RetryDecision:
    error_code: str
    terminal: bool
    delays_seconds: tuple[int, ...]

    def delay_for_attempt(self, attempt_number: int) -> int | None:
        index = int(attempt_number) - 1
        if self.terminal or index < 0 or index >= len(self.delays_seconds):
            return None
        return self.delays_seconds[index]


# audit P1-2: 2026-08-23 slept 68.8 minutes across the 2/5/10/20/30 minute
# ladder on six byte-identical release logs, all of them "65/66 passed, 1
# failed, 7 skipped".  Waiting cannot fix a failing test count, a Python or
# Node error class, or a missing command.  The failed-count capture group is
# mandatory (audit 6 #5): a release whose tests all pass but whose git push
# fails must stay retryable, and these checks may only run inside the publish
# branch (audit 6 #4) because the same words are ordinary in a source
# worker's traceback.
PUBLISH_TEST_VERDICT_RE = re.compile(r"(\d+)\s*/\s*(\d+)\s+passed,\s*(\d+)\s+failed")
# The class must open a line or follow a "...:" label (the orchestrator's own
# "release exit=1: " prefix is one), never appear mid-word: "prototypeerrors"
# is not a verdict.
PUBLISH_ERROR_CLASS_RE = re.compile(
    r"(?:^|:\s+)(?:nameerror|attributeerror|typeerror|importerror"
    r"|modulenotfounderror|syntaxerror|referenceerror)\b",
    re.MULTILINE,
)
PUBLISH_COMMAND_MISSING_RE = re.compile(r"\bexit=127\b|\bcommand not found\b")
PUBLISH_LOCK_HELD_RE = re.compile(rf"\bexit={PUBLISH_LOCK_EXIT_CODE}\b")


def publish_failure_is_deterministic(value: str) -> bool:
    """True when repeating this publish attempt cannot change its verdict."""

    verdict = PUBLISH_TEST_VERDICT_RE.search(value)
    if verdict and int(verdict.group(3)) >= 1:
        return True
    return bool(
        PUBLISH_ERROR_CLASS_RE.search(value)
        or PUBLISH_COMMAND_MISSING_RE.search(value)
    )


# audit P1-4: "nan" and "infinity" were matched as substrings, so
# mai-nan-tenance, gover-nan-ce and fi-nan-ce all read as numeric contract
# faults with an empty retry ladder.  Only a standalone token is a fault.
TERMINAL_NUMERIC_RE = re.compile(r"(?<![a-z])(nan|infinity)(?![a-z])")


# audit P2-4 (first step): the worker already knows what failed, so a verdict
# it states explicitly beats scanning 6000 characters of provider prose whose
# tail decides the retry policy.  Only codes whose meaning is unambiguous
# belong here; every other code falls through to the substring scan so a MySQL
# or CDP fault inside the blob is still recognised.
ERROR_CODE_TOKEN_RE = re.compile(r"errorcode=([a-z0-9_]+)")
ERROR_CODE_DECISIONS: dict[str, RetryDecision] = {
    "worker_receipt_missing": RetryDecision(
        "WORKER_RECEIPT_MISSING", False, INFRA_RETRY_SECONDS
    ),
}


def classify_error(text: str, *, stage: str = "source") -> RetryDecision:
    """Classify one failure without source-specific orchestration branches."""

    value = _clean(text).casefold()
    code_token = ERROR_CODE_TOKEN_RE.search(value)
    if code_token is not None:
        mapped = ERROR_CODE_DECISIONS.get(code_token.group(1))
        if mapped is not None:
            return mapped
    if any(token in value for token in (
        "cardz-linked scheduled tasks not disabled",
        "scheduler state conflict",
    )):
        return RetryDecision("SCHEDULER_STATE_CONFLICT", False, INFRA_RETRY_SECONDS)
    if any(token in value for token in (
        "no such file or directory: 'powershell'",
        'no such file or directory: "powershell"',
        "powershell.exe not found",
        "windows bridge unavailable",
    )):
        return RetryDecision("WINDOWS_BRIDGE_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    if "input drifted" in value and "invalidate-from" in value:
        return RetryDecision("CHECKPOINT_INPUT_DRIFT", False, INFRA_RETRY_SECONDS)
    if "old checkout missing" in value:
        return RetryDecision("WORKSPACE_PATH_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    if any(token in value for token in (
        "invalid api key",
        "schema contract", "contract mismatch", "illegal numeric",
        # A migration whose recorded bytes no longer match is a contract fault,
        # not a flaky source: retrying it just replays the same mismatch.
        "migration content changed", "migration hash", "migration checksum",
    )) or TERMINAL_NUMERIC_RE.search(value):
        return RetryDecision("TERMINAL_CONTRACT", True, ())
    # audit P1-4: a Cloudflare 403 and an expired session are the two most
    # common shapes on this chain and both clear after a cooldown or a
    # re-auth.  A rejected API key stays terminal above; these do not, because
    # a terminal decision can only be reopened by a hand `unpark`.
    if any(token in value for token in (
        "unauthorized", "forbidden", "authentication",
    )):
        return RetryDecision("AUTH_OR_BLOCKED", False, INFRA_RETRY_SECONDS)
    if stage == "publish":
        # audit P2-15: another publisher holding the release flock is
        # contention, not a broken release; it must not burn the publish
        # ladder waiting for a hand publish to finish.
        if PUBLISH_LOCK_MARKER in value or PUBLISH_LOCK_HELD_RE.search(value):
            return RetryDecision("PUBLISH_LOCK_HELD", False, INFRA_RETRY_SECONDS)
        # audit P1-2: terminal on attempt 1 so the operator sees the verdict
        # at +18 minutes instead of +87.
        if publish_failure_is_deterministic(value):
            return RetryDecision("PUBLISH_DETERMINISTIC", True, ())
        return RetryDecision("PUBLISH_FAILED", False, PUBLISH_RETRY_SECONDS)
    if any(token in value for token in (
        "cdp", "9333", "devtoolsactiveport", "chrome not reachable",
    )):
        return RetryDecision("CDP_9333_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    if any(token in value for token in (
        "3308", "can't connect to mysql", "cannot connect to mysql", "mysql server has gone away",
        "operationalerror(2003", "no such container: cardz-market-cap-db-1",
    )):
        return RetryDecision("MYSQL_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    transient_http_status = bool(re.search(
        r"(?:http(?:\s+status)?|status|response)\s*[:=]?\s*(?:408|429|5\d\d)\b",
        value,
    ))
    if transient_http_status or any(token in value for token in (
        "too many requests", "retry-after", "timed out", "timeout",
    )):
        retry_after = re.search(r"retry-after\s*[:=]\s*(\d{1,5})", value)
        if retry_after:
            requested = max(1, min(86400, int(retry_after.group(1))))
            return RetryDecision(
                "TRANSIENT_SOURCE",
                False,
                (requested, *TRANSIENT_RETRY_SECONDS[1:]),
            )
        return RetryDecision("TRANSIENT_SOURCE", False, TRANSIENT_RETRY_SECONDS)
    if any(token in value for token in ("ambiguous", "multiple exact", "identity conflict")):
        return RetryDecision("IDENTITY_AMBIGUOUS", True, ())
    return RetryDecision("SOURCE_FAILED", False, TRANSIENT_RETRY_SECONDS)


def classify_provenance(receipt: Mapping[str, Any]) -> str:
    """Only a recent, launcher-bound Task Scheduler event 107 is automatic."""

    def safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    def safe_float(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError, OverflowError):
            return 999999.0

    raw_event = receipt.get("event_id")
    if raw_event is None:
        raw_event = receipt.get("eventId")
    event_missing = raw_event in (None, "", 0, "0")
    event_id = safe_int(raw_event)
    parent = _clean(receipt.get("parent_process") or receipt.get("parentProcess")).casefold()
    task_name = _clean(receipt.get("task_name") or receipt.get("taskName"))
    record_id = safe_int(receipt.get("event_record_id") or receipt.get("eventRecordId"))
    instance_id = _clean(receipt.get("instance_id") or receipt.get("instanceId"))
    age_seconds = safe_float(
        receipt.get("event_age_seconds")
        if receipt.get("event_age_seconds") is not None
        else receipt.get("eventAgeSeconds")
    )
    scheduler_parent = parent in {"taskeng.exe", "taskhostw.exe", "svchost.exe"}
    task_ok = task_name.rstrip("\\") == "\\CARDZ-Marketcap-Daily-V2".rstrip("\\")
    if event_id == 110:
        return "manual"
    if (
        event_id == 107
        and task_ok
        and record_id > 0
        and bool(instance_id)
        and 0 <= age_seconds <= 120
        and scheduler_parent
    ):
        return "scheduled"
    # 10-minute repetition often has no fresh event 107 in the launcher's
    # 3-minute lookback. The parent is still Task Scheduler, not CLI.
    if scheduler_parent and task_ok and event_missing:
        return "scheduled"
    return "manual"


def previous_business_date(value: date) -> date:
    candidate = value - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def autonomous_proven(
    current_business_date: date,
    rows: Iterable[Mapping[str, Any]],
) -> bool:
    eligible = {
        str(row.get("business_date")): row
        for row in rows
        # A degraded publication is intentionally not called a complete
        # autonomous success.  It stays visible and may be operationally
        # acceptable, but two fully healthy natural days are required before
        # the system can prove hands-off operation.
        if _clean(row.get("status")) == "PUBLISHED"
    }
    expected = (
        current_business_date.isoformat(),
        previous_business_date(current_business_date).isoformat(),
    )
    for day in expected:
        row = eligible.get(day)
        if row is None:
            return False
        if int(row.get("manual_intervention_count") or 0) != 0:
            return False
        if int(row.get("scheduled_event_107_count") or 0) < 1:
            return False
    return True


def identity_disposition(
    *,
    canonical_matches: int,
    series_matches: int,
    pop: int | None,
    quote_eligible: bool,
    image_ready: bool,
    public_fields_ready: bool,
) -> str:
    """Pure activation gate; caller persists evidence and quarantine state."""

    if canonical_matches != 1 or series_matches != 1:
        return "quarantined_ambiguous"
    if pop is None or int(pop) < 1000:
        return "policy_excluded"
    if not (quote_eligible and image_ready and public_fields_ready):
        return "pending_identity"
    return "activate"


def normalise_language(value: Any) -> str:
    return _clean(value).casefold().replace("_", "-")


@dataclass(frozen=True)
class QuoteCandidate:
    source_code: str
    quote_revision_id: int
    checked_at: str
    usd_value: str


@dataclass(frozen=True)
class QuotePolicyRow:
    policy_version: str
    language_code: str
    source_code: str
    priority: int
    eligible: bool = True


@dataclass(frozen=True)
class QuoteSelection:
    selected_source_code: str
    policy_version: str
    quote_revision_id: int
    source_switched: bool
    usd_value: str


def select_quote(
    language: str,
    candidates: Sequence[QuoteCandidate],
    policy_rows: Sequence[QuotePolicyRow],
    *,
    previous_source_code: str | None = None,
) -> QuoteSelection | None:
    lang = normalise_language(language)
    policy_by_source: dict[str, QuotePolicyRow] = {}
    for source in {item.source_code for item in candidates}:
        rows = [
            row for row in policy_rows
            if row.source_code == source and row.eligible
            and normalise_language(row.language_code) in {lang, "*"}
        ]
        if not rows:
            continue
        rows.sort(
            key=lambda row: (
                0 if normalise_language(row.language_code) == lang else 1,
                int(row.priority),
                row.policy_version,
            )
        )
        policy_by_source[source] = rows[0]
    eligible = [item for item in candidates if item.source_code in policy_by_source]
    if not eligible:
        return None
    eligible.sort(
        key=lambda item: (
            int(policy_by_source[item.source_code].priority),
            -int(datetime.fromisoformat(item.checked_at.replace("Z", "+00:00")).timestamp()),
            -int(item.quote_revision_id),
        )
    )
    winner = eligible[0]
    policy = policy_by_source[winner.source_code]
    return QuoteSelection(
        selected_source_code=winner.source_code,
        policy_version=policy.policy_version,
        quote_revision_id=int(winner.quote_revision_id),
        source_switched=bool(previous_source_code and previous_source_code != winner.source_code),
        usd_value=str(winner.usd_value),
    )


def market_content_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    material = [dict(row) for row in rows]
    material.sort(key=lambda row: (int(row.get("variantId") or 0), canonical_json(row)))
    return sha256(material)


def daily_generation_sha256(business_date: str, content_sha256: str) -> str:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date):
        raise ValueError("business_date must be YYYY-MM-DD")
    if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
        raise ValueError("content_sha256 must be lowercase sha256")
    return sha256({
        "contract": "cardz-daily-generation-v2",
        "businessDate": business_date,
        "contentSha256": content_sha256,
    })


@dataclass(frozen=True)
class PublicationDecision:
    ready: bool
    status: str
    blockers: tuple[str, ...]
    degraded_sources: tuple[str, ...]


def publication_decision(
    source_states: Iterable[Mapping[str, Any]],
    *,
    gemrate_coverage_complete: bool,
    quote_coverage_complete: bool,
    fx_ok: bool,
    accept_ok: bool,
    ranking_ok: bool,
    box_ok: bool,
) -> PublicationDecision:
    rows = list(source_states)
    blockers: list[str] = []
    degraded: list[str] = []
    for row in rows:
        status = _clean(row.get("status")).lower()
        required_class = _clean(row.get("required_class")).lower()
        code = _clean(row.get("source_code")) or "unknown"
        if status != "completed":
            if required_class == "core":
                blockers.append(f"source:{code}:{status or 'missing'}")
            else:
                degraded.append(code)
    for name, ok in (
        ("gemrate_coverage", gemrate_coverage_complete),
        ("quote_coverage", quote_coverage_complete),
        ("fx", fx_ok),
        ("db_accept", accept_ok),
        ("ranking", ranking_ok),
        ("box", box_ok),
    ):
        if not ok:
            blockers.append(name)
    blockers = sorted(set(blockers))
    degraded = sorted(set(degraded))
    if blockers:
        return PublicationDecision(False, "BLOCKED", tuple(blockers), tuple(degraded))
    return PublicationDecision(
        True,
        "PUBLISHED_DEGRADED" if degraded else "PUBLISHED",
        (),
        tuple(degraded),
    )


# ---------------------------------------------------------------------------
# Generic-source policy.  Everything below exists so that registering another
# provider stays "registry row + migration file + adapter registration": the
# orchestrator asks these functions instead of hard-coding provider names.


def list_v2_migrations(root: Any) -> tuple[str, ...]:
    """Sorted daily-chain V2 migration file names under ``root``.

    Dropping a new ``05x_daily_chain_v2_*.mysql.sql`` in place is enough; no
    stage list and no ``migrate --only`` argument has to be edited.
    """

    folder = Path(str(root)) / "pipelines" / "migrations"
    return tuple(sorted(
        path.name for path in folder.glob(V2_MIGRATION_GLOB) if path.is_file()
    ))


def v2_schema_capabilities(root: Any) -> tuple[str, ...]:
    """Journal capability per V2 migration: ``051_...sql`` -> ``schema-051``."""

    return tuple(
        f"schema-{name.split('_', 1)[0]}" for name in list_v2_migrations(root)
    )


def registry_enabled(row: Mapping[str, Any]) -> bool:
    value = row.get("enabled")
    if value is None:
        return True
    if isinstance(value, str):
        return _clean(value).casefold() not in {"", "0", "false", "no"}
    return bool(value)


def registry_capabilities(row: Mapping[str, Any]) -> frozenset[str]:
    raw: Any = row.get("capabilities")
    if raw is None:
        raw = row.get("capabilities_json")
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = ()
    if not isinstance(raw, (list, tuple, set, frozenset)):
        raw = ()
    return frozenset(_clean(value).casefold() for value in raw if _clean(value))


def registry_config(row: Mapping[str, Any]) -> Mapping[str, Any]:
    raw: Any = row.get("config")
    if raw is None:
        raw = row.get("config_json")
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    return raw if isinstance(raw, Mapping) else {}


def core_contract_keys(
    source_rows: Iterable[Mapping[str, Any]] | None,
) -> tuple[str, ...]:
    """Contract sections the pre/post barrier must see complete.

    Every enabled ``required_class='core'`` source contributes its own section
    and any enabled quote-capable source contributes the shared ``quotes``
    section.  The literal tuple survives only as the no-registry fallback.
    """

    rows = [row for row in (source_rows or ()) if isinstance(row, Mapping)]
    if not rows:
        return CORE_CONTRACT_FALLBACK_KEYS
    keys: set[str] = set()
    for row in rows:
        if not registry_enabled(row):
            continue
        code = _clean(row.get("source_code"))
        if not code:
            continue
        if _clean(row.get("required_class")).casefold() == "core":
            keys.add(code)
        if "quote" in registry_capabilities(row):
            keys.add("quotes")
    return tuple(sorted(keys)) or CORE_CONTRACT_FALLBACK_KEYS


def pop_contract_sources(
    source_rows: Iterable[Mapping[str, Any]] | None,
) -> tuple[dict[str, str], ...]:
    """Core sources whose coverage is measured by a POP ingest checkpoint."""

    found: list[dict[str, str]] = []
    for row in source_rows or ():
        if not isinstance(row, Mapping) or not registry_enabled(row):
            continue
        if _clean(row.get("required_class")).casefold() != "core":
            continue
        if "pop" not in registry_capabilities(row):
            continue
        code = _clean(row.get("source_code"))
        if not code:
            continue
        canonical = _clean(row.get("canonical_source_code")) or code
        checkpoint = (
            _clean(registry_config(row).get("popCheckpointSourceCode"))
            or f"{canonical}_pop"
        )
        found.append({
            "sourceCode": code,
            "identitySourceCode": _clean(row.get("identity_source_code")) or code,
            "popCheckpointSourceCode": checkpoint,
        })
    return tuple(sorted(found, key=lambda item: item["sourceCode"]))


def rates_contract_sources(
    source_rows: Iterable[Mapping[str, Any]] | None,
) -> tuple[str, ...]:
    """Core sources whose coverage is measured by FX rate observations."""

    return tuple(sorted({
        _clean(row.get("source_code"))
        for row in source_rows or ()
        if isinstance(row, Mapping)
        and registry_enabled(row)
        and _clean(row.get("required_class")).casefold() == "core"
        and "rates" in registry_capabilities(row)
        and _clean(row.get("source_code"))
    }))


def identity_lanes(specs: Iterable[Any] | None) -> tuple[tuple[str, str], ...]:
    """``(lane, concurrency group)`` pairs declared by identity sources."""

    lanes: dict[str, str] = {}
    for spec in specs or ():
        if not bool(getattr(spec, "enabled", True)):
            continue
        lane = _clean(getattr(spec, "identity_lane", None))
        if not lane:
            continue
        capabilities = {
            _clean(value).casefold()
            for value in (getattr(spec, "capabilities", ()) or ())
        }
        if "identity" not in capabilities:
            continue
        lanes.setdefault(
            lane, _clean(getattr(spec, "concurrency_group", "")) or "db-writer"
        )
    return tuple(sorted(lanes.items())) or IDENTITY_LANE_FALLBACK


def route_policy_upserts(
    specs: Iterable[Any] | None,
    *,
    existing_source_codes: Iterable[str] = (),
    activated_at: str,
    policy_version: str = ROUTE_POLICY_VERSION,
) -> tuple[tuple[Any, ...], ...]:
    """Route-policy rows a newly registered quote source still needs.

    A source that already owns policy rows is skipped on purpose: the
    migration-owned per-language priorities stay authoritative, so registering
    a new provider can never re-price an existing one.
    """

    existing = {_clean(value) for value in existing_source_codes if _clean(value)}
    rows: list[tuple[Any, ...]] = []
    for spec in specs or ():
        if not bool(getattr(spec, "enabled", True)):
            continue
        capabilities = {
            _clean(value).casefold()
            for value in (getattr(spec, "capabilities", ()) or ())
        }
        if "quote" not in capabilities:
            continue
        code = _clean(getattr(spec, "source_code", ""))
        if not code or code in existing:
            continue
        declared = getattr(spec, "route_priority", None)
        priority = DEFAULT_ROUTE_PRIORITY if declared is None else int(declared)
        if priority < 1:
            raise ValueError(f"{code}: route_priority must be positive")
        rows.append((policy_version, "*", code, priority, 1, 1, activated_at))
    return tuple(sorted(rows))


def contract_shortfall(
    contract: Mapping[str, Any],
    keys: Iterable[str],
) -> str:
    """Human-readable shortfall for each barrier section, in one line.

    Coverage sections report ``<key>Missing=<n>``; that token is what tells the
    orchestrator a targeted repair is worth planning, for every source rather
    than for the two that used to be spelled out.
    """

    parts: list[str] = []
    for name in keys:
        section = contract.get(name) or {}
        if not isinstance(section, Mapping):
            parts.append(f"{name}=unknown")
        elif "missing" in section:
            parts.append(
                f"{name}{CONTRACT_SHORTFALL_MARKER}"
                f"{len(section.get('missing') or [])}"
            )
        else:
            parts.append(
                f"{name}={section.get('covered')}/{section.get('expected')}"
            )
    return " ".join(parts)


def quote_repair_routes(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Group missing variants by repair source, cross-checked against policy.

    ``select_quote`` is the declared, versioned routing policy.  The MySQL join
    is faster but its ordering is easy to break silently, so a variant whose SQL
    winner disagrees with the policy is rejected rather than repaired from an
    unproven source.
    """

    by_variant: dict[int, list[Mapping[str, Any]]] = {}
    for row in rows:
        variant_id = int(row.get("variant_id") or 0)
        if variant_id <= 0:
            continue
        by_variant.setdefault(variant_id, []).append(row)
    grouped: dict[str, list[int]] = {}
    rejected: list[dict[str, Any]] = []
    for variant_id in sorted(by_variant):
        variant_rows = by_variant[variant_id]
        sql_winner = _clean(variant_rows[0].get("source_code"))
        language = variant_rows[0].get("card_language")
        candidates: list[QuoteCandidate] = []
        policies: list[QuotePolicyRow] = []
        seen: set[str] = set()
        for index, row in enumerate(variant_rows):
            code = _clean(row.get("source_code"))
            if not code or code in seen:
                continue
            seen.add(code)
            candidates.append(QuoteCandidate(
                source_code=code,
                # Descending so that a priority tie keeps the SQL row order
                # instead of inventing a second, different tie-break.
                quote_revision_id=len(variant_rows) - index,
                checked_at="1970-01-01T00:00:00+00:00",
                usd_value="1",
            ))
            policies.append(QuotePolicyRow(
                policy_version=_clean(row.get("policy_version")) or "unknown",
                language_code=_clean(row.get("language_code")) or "*",
                source_code=code,
                priority=int(row.get("priority") or DEFAULT_ROUTE_PRIORITY),
            ))
        selection = select_quote(language, candidates, policies)
        if selection is None or selection.selected_source_code != sql_winner:
            rejected.append({
                "variantId": variant_id,
                "sqlWinner": sql_winner or None,
                "policyWinner": selection.selected_source_code if selection else None,
            })
            continue
        grouped.setdefault(selection.selected_source_code, []).append(variant_id)
    return {
        "routes": {code: sorted(ids) for code, ids in sorted(grouped.items())},
        "rejected": rejected,
    }
