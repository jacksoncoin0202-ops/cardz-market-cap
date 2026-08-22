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
from typing import Any, Iterable, Mapping, Protocol, Sequence


CONTRACT_VERSION = "cardz-daily-chain-v2"
JST_OFFSET = timedelta(hours=9)
SOURCE_REQUIRED_CLASSES = frozenset({"core", "quote", "extra"})
RESULT_STATUSES = frozenset({
    "completed", "degraded", "quarantined", "retry", "terminal", "interrupted",
})
TRANSIENT_RETRY_SECONDS = (60, 120, 240, 480, 960, 1800)
INFRA_RETRY_SECONDS = (30, 60, 120)
PUBLISH_RETRY_SECONDS = (120, 300, 600, 1200, 1800)


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


def classify_error(text: str, *, stage: str = "source") -> RetryDecision:
    """Classify one failure without source-specific orchestration branches."""

    value = _clean(text).casefold()
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
        "unauthorized", "forbidden", "invalid api key", "authentication",
        "schema contract", "contract mismatch", "illegal numeric", "nan", "infinity",
    )):
        return RetryDecision("TERMINAL_CONTRACT", True, ())
    if stage == "publish":
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
