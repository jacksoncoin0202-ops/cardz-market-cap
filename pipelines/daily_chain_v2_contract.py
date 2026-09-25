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
# The V2 branch of scripts/daily_public_release.sh used to force ONE
# bake/sync/validate attempt, so a single flaky bake spent a whole orchestrator
# attempt plus a ladder step.  The script's retry runs
# `git checkout -- data/public` first, so replaying the leg is idempotent.
# scripts/test_v2s_classify.py parses the script and asserts the two numbers
# agree, so neither side can drift on its own.
PUBLISH_ASSET_MAX_ATTEMPTS = 3
# The parts of one release leg in scripts/daily_public_release.sh: one
# bake -> sync -> validate pass (measured ~180 s, and the script's own
# `asset_pass_seconds`), the `sleep` between asset attempts, and the live
# health poll, 60 x 10 s before it gives up.  scripts/test_v2s_classify.py
# reads all three back off the script so the numbers cannot drift apart.
PUBLISH_ASSET_PASS_SECONDS = 180
PUBLISH_ASSET_RETRY_SLEEP_SECONDS = 15
PUBLISH_HEALTH_POLL_SECONDS = 600
# One release leg: the leg that can still SUCCEED -- one bake/sync/validate
# pass plus the live health poll (the poll exits on the first healthy
# generation, so this is already generous).  A retry scheduled later than
# `final - PUBLISH_LEG_SECONDS` cannot finish before the business date's
# 17:00 JST cutoff, so it is a retry that will never run: on 2026-08-23 the
# 2/5/10/20/30 minute ladder above (67 minutes for five retries) could park the
# next publish attempt past `final`, where lifecycle_events() stamps
# FAILED_FINAL unconditionally -- attempts left, no window to spend them in.
# The cutoff is not ours to move; the ladder is clamped to this instead.
# NOT the all-fail leg (3 passes + 2 sleeps + the full poll = 1170 s): the bake
# normally passes on attempt 1, so charging every retry the worst case refuses
# retries that would very likely have published -- a PUBLISH_FAILED at 16:45
# JST would be TERMINAL with five attempts unspent.  The extra bake passes are
# the SCRIPT's budget and are bounded where they are spent: the retry loop in
# scripts/daily_public_release.sh refuses a pass it cannot finish before
# CARDZ_V2_STAGE_DEADLINE_EPOCH, which daily_chain_v2.py derives from a work
# deadline that is never later than `final`.
PUBLISH_LEG_SECONDS = PUBLISH_ASSET_PASS_SECONDS + PUBLISH_HEALTH_POLL_SECONDS  # 780
# `live-confirm` is the other task in the publish phase, and it is nothing like
# the release leg: it reads the release snapshot, makes ONE health request
# (fetch_live_health, 20 s timeout) and inserts one outbox row, and it is
# planned only after `release` COMPLETED -- i.e. always in the tail of the day.
# Its own gate is `--confirm-before final`, so its PUBLISH_FAILED ladder is
# exactly the mechanism that waits out FE deploy lag.  Charging it the release
# leg would make every failure inside the last 13 minutes TERMINAL with
# attempts unspent and the day's `live.confirmed` lost.
LIVE_CONFIRM_LEG_SECONDS = 120
# Publish-phase capability -> the leg that capability actually needs.  A leg is
# a measurement, not a phase-wide constant; scripts/test_v2s_classify.py
# asserts this covers exactly the publish capabilities the planner registers,
# so a new one cannot quietly inherit the release leg.
PUBLISH_LEG_SECONDS_BY_CAPABILITY = {
    "release": PUBLISH_LEG_SECONDS,
    "live-confirm": LIVE_CONFIRM_LEG_SECONDS,
}
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
    # Contention is not failure: another holder of the same single-flight
    # resource is working, or the tick has no room to start the work. Such a
    # class repeats its last delay instead of exhausting its ladder to a terminal
    # verdict, and daily_chain_v2_journal.finish_failure gives back the attempt
    # the claim spent -- the failure budget exists for real failures.
    contention: bool = False

    def delay_for_attempt(self, attempt_number: int) -> int | None:
        index = int(attempt_number) - 1
        if self.terminal or index < 0 or not self.delays_seconds:
            return None
        if index >= len(self.delays_seconds):
            if not self.contention:
                return None
            index = len(self.delays_seconds) - 1
        return self.delays_seconds[index]


# audit P1-2: 2026-08-23 slept 68.8 minutes on six byte-identical failed test
# runs.  The release runner now emits a versioned JSON verdict; policy reads
# that contract instead of scraping its human summary.  A release whose tests
# pass but whose git push fails therefore remains retryable, while failed
# tests are terminal without depending on presentation text.
PUBLISH_TEST_RESULT_PREFIX = "CARDZ_TEST_RESULT "
PUBLISH_TEST_RESULT_CONTRACT = "cardz-test-result-v1"
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


def publish_test_result(value: str) -> dict[str, Any] | None:
    """Return the last valid structured runner verdict embedded in a log."""

    for line in reversed(value.splitlines()):
        marker = line.find(PUBLISH_TEST_RESULT_PREFIX)
        if marker < 0:
            continue
        raw = line[marker + len(PUBLISH_TEST_RESULT_PREFIX):].strip()
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(result, dict) or result.get("contract") != PUBLISH_TEST_RESULT_CONTRACT:
            continue
        counts = [result.get(key) for key in ("entries", "passed", "failed", "skipped", "timeouts")]
        if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
            continue
        if result["entries"] != result["passed"] + result["failed"] + result["skipped"]:
            continue
        if result["timeouts"] > result["failed"]:
            continue
        return result
    return None


def publish_failure_is_deterministic(value: str) -> bool:
    """True when repeating this publish attempt cannot change its verdict."""

    verdict = publish_test_result(value)
    if verdict is not None and verdict["failed"] >= 1:
        return True
    folded = value.casefold()
    return bool(
        PUBLISH_ERROR_CLASS_RE.search(folded)
        or PUBLISH_COMMAND_MISSING_RE.search(folded)
    )


# audit P1-4: "nan" and "infinity" were matched as substrings, so
# mai-nan-tenance, gover-nan-ce and fi-nan-ce all read as numeric contract
# faults with an empty retry ladder.  Only a standalone token is a fault.
TERMINAL_NUMERIC_RE = re.compile(r"(?<![a-z])(nan|infinity)(?![a-z])")
# 2026-09-25 live: an identity-completeness error carrying 1858 card hashes was
# read as CDP_9333_UNAVAILABLE because one sha1 contained "...933334...".  A port
# counts only as a whole number, never as digits inside a hex run.
CDP_PORT_RE = re.compile(r"(?<![0-9a-f])9333(?![0-9a-f])")
MYSQL_PORT_RE = re.compile(r"(?<![0-9a-f])3308(?![0-9a-f])")
# 2026-09-25 crawler follow-up: the bare substring "cdp" also matched file
# names (pc_cdp_refresh_report.json, ensure_chrome_cdp.ps1, cdp.lock) and the
# success line "CDP_IDENTITY_OK port=9333" that 182 collect-report stdout tails
# carry, so any collect failure with a PC stdout tail took the CDP ladder.
# "cdp" now counts as a standalone word or as a failure marker the CDP code
# really emits (journal + runtime receipts, read 2026-09-25).  A success line
# is removed before the scan, so it can no longer vote for a dead 9333.
CDP_WORD_RE = re.compile(r"(?<![a-z0-9_./\\-])cdp(?![a-z0-9_-]|\.[a-z])")
CDP_FAILURE_MARKERS = (
    "cdp_unreachable", "cdp_identity_reject", "cdp_down", "cdp_still_down",
    "cdp_fail", "cdp_wrong", "cdp_jammed", "cdp_revive_failed",
    "cdp_revive_still_wrong", "cdp_evict_still_up", "cdp_evict_wsl",
    "cdp_preflight_failed", "cdp_9333_unavailable",
    # The PC child's generic nonzero exit.  Not always a dead browser, but
    # dropping it could hide one, so it keeps the CDP reading it had.
    "pc_cdp_refresh_failed",
    "devtoolsactiveport", "chrome not reachable",
)
CDP_SUCCESS_LINE_RE = re.compile(
    r"(?:cdp_identity_ok|cdp_ok|cdp_revived|fetch cdp port=\d+ ok)[^\n\"\\]*"
)
# DNS failures clear on their own; 2026-09-18 an SNK harvest lost every item to
# "[Errno -3] Temporary failure in name resolution" and climbed SOURCE_FAILED.
DNS_TRANSIENT_TOKENS = (
    "temporary failure in name resolution",
    "could not resolve host",
)


# audit P2-4 (first step): the worker already knows what failed, so a verdict
# it states explicitly beats scanning 6000 characters of provider prose whose
# tail decides the retry policy.  Only codes whose meaning is unambiguous
# belong here; every other code falls through to the substring scan so a MySQL
# or CDP fault inside the blob is still recognised.
ERROR_CODE_TOKEN_RE = re.compile(r"errorcode=([a-z0-9_]+)")
# collect_control.PC_CHILD_ALREADY_RUNNING_CLASS, spelled again here so the
# orchestrator never imports the collector; scripts/test_pc_daily_full_refresh.py
# pins the two spellings to each other.
PC_CHILD_ALREADY_RUNNING_CLASS = "pc_child_already_running"
# One tick.  The orphaned 9333 child either finishes its own 40-90 min sweep or
# its hard stall killer takes it, and either way the next tick resumes from the
# pages it already captured.
PC_CHILD_ALREADY_RUNNING_RETRY_SECONDS = (600,)
ERROR_CODE_DECISIONS: dict[str, RetryDecision] = {
    # Budget admission refused before harvest: use the existing no-work
    # accounting. The orchestrator closes claiming so only a new tick retries.
    "census_tick_budget_deferred": RetryDecision(
        "CENSUS_TICK_BUDGET_DEFERRED", False, (0,), contention=True
    ),
    "identity_completeness_tick_budget_deferred": RetryDecision(
        "IDENTITY_COMPLETENESS_TICK_BUDGET_DEFERRED", False, (0,), contention=True
    ),
    "worker_receipt_missing": RetryDecision(
        "WORKER_RECEIPT_MISSING", False, INFRA_RETRY_SECONDS
    ),
    # review 2026-08-24 (blocking): a refused sweep is this lane's OWN 9333
    # child still fetching, not a failed fetch.  Read as SOURCE_FAILED it
    # climbed the 60..1800 ladder and turned the source TERMINAL on the 7th
    # refusal while the orphan was still working -- the day then published with
    # no fresh PriceCharting data.  Same reading as PUBLISH_LOCK_HELD below:
    # contention must not burn the ladder, and here it must not burn the
    # attempt budget either.
    PC_CHILD_ALREADY_RUNNING_CLASS: RetryDecision(
        "PC_CHILD_ALREADY_RUNNING",
        False,
        PC_CHILD_ALREADY_RUNNING_RETRY_SECONDS,
        contention=True,
    ),
}


def classify_error(text: str, *, stage: str = "source") -> RetryDecision:
    """Classify one failure without source-specific orchestration branches."""

    cleaned = _clean(text)
    value = cleaned.casefold()
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
        if publish_failure_is_deterministic(cleaned):
            return RetryDecision("PUBLISH_DETERMINISTIC", True, ())
        return RetryDecision("PUBLISH_FAILED", False, PUBLISH_RETRY_SECONDS)
    cdp_scan = CDP_SUCCESS_LINE_RE.sub(" ", value)
    if (
        CDP_PORT_RE.search(cdp_scan)
        or CDP_WORD_RE.search(cdp_scan)
        or any(token in cdp_scan for token in CDP_FAILURE_MARKERS)
    ):
        return RetryDecision("CDP_9333_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    if MYSQL_PORT_RE.search(value) or any(token in value for token in (
        "can't connect to mysql", "cannot connect to mysql", "mysql server has gone away",
        "operationalerror(2003", "no such container: cardz-market-cap-db-1",
    )):
        return RetryDecision("MYSQL_UNAVAILABLE", False, INFRA_RETRY_SECONDS)
    transient_http_status = bool(re.search(
        r"(?:http(?:\s+status)?|status|response)\s*[:=]?\s*(?:408|429|5\d\d)\b",
        value,
    ))
    if transient_http_status or any(token in value for token in (
        "too many requests", "retry-after", "timed out", "timeout",
        *DNS_TRANSIENT_TOKENS,
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


# 2026-09-25 crawler follow-up: worker stderr now reaches the journal, the
# receipts and the operator's Telegram, so anything shaped like a credential is
# masked before it leaves the worker.  A key only needs to CONTAIN a secret
# word (MYSQL_PASSWORD=, x-api-key:, "session_id": ...); the lookbehind keeps a
# long hex run from being rescanned at every offset.
_SECRET_KEY_PATTERN = (
    r"(?<![a-z0-9_.\-])[a-z0-9_.\-]*"
    r"(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|authorization|cookie|credential|session[_-]?id)"
    r"[a-z0-9_.\-]*"
)
_SECRET_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Whole header lines: a cookie jar or an auth scheme has spaces and ';'.
    (re.compile(r"(?im)^(\s*(?:set-cookie|cookie|authorization)\s*:\s*).*$"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=\-]{8,}"), r"\1 [REDACTED]"),
    (
        re.compile(
            r"(?i)(" + _SECRET_KEY_PATTERN + r"\\?[\"']?\s*[:=]\s*\\?[\"']?)[^\s\"',;&\\]+"
        ),
        r"\1[REDACTED]",
    ),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)[^/\s:@\"']+:[^/\s@\"']+@"), r"\1[REDACTED]@"),
    (re.compile(r"(?i)\bbot\d{6,}:[a-z0-9_\-]{30,}"), "bot[REDACTED]"),
)
STDERR_EXCERPT_PER_TAIL = 400
STDERR_EXCERPT_MAX_TAILS = 3
STDERR_EXCERPT_MAX_CHARS = 1500
COLLECT_COMMAND_NAMES = ("run", "harvest", "ingest", "derive", "materialize")


def redact_secrets(text: Any) -> str:
    value = str(text or "")
    for pattern, replacement in _SECRET_REDACTIONS:
        value = pattern.sub(replacement, value)
    return value


def adapter_stderr_excerpt(failed_detail: Iterable[Mapping[str, Any]]) -> str:
    """A bounded, redacted stderr tail for a COLLECT_ADAPTER_FAILED error.

    2026-09-18: SNK harvest stderr said "Temporary failure in name resolution"
    on every item, but the error kept only the last 6000 characters of the
    sorted detail, i.e. stdout progress lines, and the journal read
    SOURCE_FAILED.  The last lines of each distinct stderr tail now lead the
    detail.  Empty string when no failed command wrote to stderr.
    """

    tails: list[str] = []
    for row in failed_detail or ():
        commands = row.get("commands") if isinstance(row, Mapping) else None
        if not isinstance(commands, Mapping):
            continue
        for name in COLLECT_COMMAND_NAMES:
            command = commands.get(name)
            if not isinstance(command, Mapping):
                continue
            # Redact the whole tail before cutting it, so the cut can never
            # split a key from its value.  The cut stays raw: a trailing short
            # line must not push the causal line out of the budget.
            tail = redact_secrets(command.get("stderrTail")).strip()[-STDERR_EXCERPT_PER_TAIL:]
            if tail and tail not in tails:
                tails.append(tail)
        if len(tails) >= STDERR_EXCERPT_MAX_TAILS:
            break
    tails = tails[:STDERR_EXCERPT_MAX_TAILS]
    if not tails:
        return ""
    excerpt = json.dumps(tails, ensure_ascii=False)
    while len(excerpt) > STDERR_EXCERPT_MAX_CHARS and len(tails) > 1:
        tails.pop()
        excerpt = json.dumps(tails, ensure_ascii=False)
    return excerpt[-STDERR_EXCERPT_MAX_CHARS:]


def publish_leg_seconds(capability: Any) -> int:
    """How long one attempt of this publish capability needs to finish.

    Unknown capabilities get the longest measured leg: capping too early only
    ends a run sooner, while capping too late schedules a retry that runs past
    the cutoff.  The planner-coverage check in scripts/test_v2s_classify.py is
    what keeps "unknown" from becoming the normal case.
    """

    return int(
        PUBLISH_LEG_SECONDS_BY_CAPABILITY.get(str(capability or ""), PUBLISH_LEG_SECONDS)
    )


def clamp_retry_at(
    now: datetime,
    delay_seconds: int,
    *,
    not_after: datetime | None,
) -> datetime | None:
    """Where a backoff actually lands once a hard deadline is taken into account.

    `None` means the deadline leaves no room at all.  A caller must treat that
    exactly like an exhausted ladder (TERMINAL), never as "retry anyway": the
    deadline is the gate, and pretending to schedule past it is what made
    2026-08-23's release attempts disappear into FAILED_FINAL.
    """

    candidate = now + timedelta(seconds=int(delay_seconds))
    if not_after is None or candidate <= not_after:
        return candidate
    if now <= not_after:
        return not_after
    return None


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
    # The production task deliberately launches through the hidden
    # cardz_silent_run.vbs wrapper, so PowerShell's direct parent is wscript.
    # Accept that shape only with a fresh, fully-bound event 107 receipt; the
    # event-missing repetition shortcut below remains restricted to native
    # Task Scheduler parents so a manually launched VBS cannot claim autonomy.
    hidden_task_wrapper = parent == "wscript.exe"
    task_ok = task_name.rstrip("\\") == "\\CARDZ-Marketcap-Daily-V2".rstrip("\\")
    if event_id == 110:
        return "manual"
    if (
        event_id == 107
        and task_ok
        and record_id > 0
        and bool(instance_id)
        and 0 <= age_seconds <= 120
        and (scheduler_parent or hidden_task_wrapper)
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


# Supersede-in-place run identity.  A business date that already published may
# be run again with fresh data; the rerun keeps the date and carries the
# generation in a `/N` suffix (`cardz-v2:2026-08-24/2`).  Generation 1 has no
# suffix, so every historical run id, event key and outbox row keeps its exact
# meaning and nothing that never supersedes has to know the suffix exists.
RUN_ID_SUPERSEDE_RE = re.compile(r"/([1-9][0-9]*)$")


def supersede_seq_of(run_id: Any) -> int:
    """Supersede generation encoded in a run id; an unsuffixed id is 1."""

    match = RUN_ID_SUPERSEDE_RE.search(str(run_id or ""))
    return int(match.group(1)) if match else 1


def supersede_suffix(seq: Any) -> str:
    """`/N` for a superseding generation, empty string for the first one."""

    try:
        value = int(seq)
    except (TypeError, ValueError):
        value = 1
    return f"/{value}" if value > 1 else ""


def run_id_with_supersede_seq(run_id: Any, seq: Any) -> str:
    """Same run id, re-stamped for generation `seq`.  The one suffix rewriter."""

    return RUN_ID_SUPERSEDE_RE.sub("", str(run_id or "")) + supersede_suffix(seq)


def run_id_business_date(run_id: Any) -> str:
    """Business date of a run id, whatever label or supersede suffix it carries.

    Every place that needs the date from an id uses this instead of splitting
    on its own: `cardz-v2:2026-08-24`, `cardz-v2:2026-08-24#A01` and
    `cardz-v2:2026-08-24/2` all answer `2026-08-24`.
    """

    _, _, rest = str(run_id or "").partition(":")
    return RUN_ID_SUPERSEDE_RE.sub("", rest).partition("#")[0]


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
