#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CARDZ stock/incr collector control plane (Polaris era).

Operator modes:
  stock       - residual full pulls for gap ids
  incr        - cursor/due exact-id deltas
  first-stock - only registry streams with no checkpoint (new activate)

Does not revive archived run_daily factory. Display authority stays in operator_control.latest_prices.
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
import re
import signal
import stat as stat_mod
import subprocess
import sys
import threading
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collection_contract import (  # noqa: E402
    ADAPTER_LANE,
    CHECKPOINT_ADAPTERS,
    LIVE_EBAY_SOLD_SOURCE_CODES,
    SOURCE_ADAPTERS,
    SOURCE_NOT_REGISTERED,
)
from qualified_pool_operator import db, load_env  # noqa: E402
from operator_control import (  # noqa: E402
    CHECKPOINT_SLA_HOURS,
    current_universe,
    missing_checkpoint_streams,
)
from runtime_paths import assert_runtime_root  # noqa: E402
from native_image_resolver import ProcessedSnkDefaultImage, process_snk_default_image  # noqa: E402
from snkrdunk_bulk import (  # noqa: E402
    SNK_EN_PRODUCT_PAGE_CONTRACT,
    SnkrdunkApiPool,
    exact_en_product_url,
    master_default_image,
)

OUT_DIR = ROOT / "data" / "runtime" / "operator" / "collect"
REGISTRY_PATH = OUT_DIR / "collect_registry.jsonl"
LAST_INCR = OUT_DIR / "last_incr.json"
LAST_STOCK = OUT_DIR / "last_stock.json"
LAST_STATUS = OUT_DIR / "last_status.json"
PC_REFRESH_REPORT = OUT_DIR / "pc_cdp_refresh_report.json"
PC_DAILY_FULL_CYCLE_STAMP = OUT_DIR / "pc_daily_full_cycle.json"
# "fresh enough to publish" (SLA_HOURS, the acceptance gate) and "collect it
# again" are different questions -- the daily chain asks the second one and used
# to get the first one's answer, so it replayed day-old HTML every morning and
# the board trailed PriceCharting by 11-35 h.
PC_REFRESH_POLICIES = ("sla_replay", "daily_full", "fallback_replay")
# 被 9333 拒絕一次唔准即刻出尋日 HTML。第一次拒絕留返個 failure 俾 chain 重試
# （同一個 cycle 內），第二次先至准 fallback：owner 要「每日全部攞新」，
# 一次 Cloudflare storm 唔應該靜靜地變成成日舊價。
PC_DAILY_FULL_FALLBACK_MIN_REFUSALS = 2
PC_MAP = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
WINDOWS_PY = ROOT / ".venv-backend-windows/Scripts/python.exe"
# 每個 adapter 由邊條自動鏈收，係 adapter 自己嘅屬性，唔應該由兩個 .ps1 各自
# 抄一張名單 —— 抄名單就一定有漏。夜鏈叫 `--adapter http`、朝鏈叫
# `--adapter browser`，兩組加埋一定係 CHECKPOINT_ADAPTERS 全部，新 adapter 冇得
# 跌喺兩張名單中間。
#
# `manual` = 唔入任何自動鏈，要人手 `--adapter <名>` 先行到。而家冇 adapter 喺
# 呢類，個 group 留住係因為將來會有寫入形狀未 production-ready 嘅 lane。
COLLECT_LEASE_CONTRACT = "mysql_advisory_adapter_lease_v1"
# Adapter leases alone cannot serialize against daily-accept: 2026-08-16
# 16:30 refresh sat silent for 60 minutes on MDL/row locks while a human
# collect held cardz:collect:* . Mutating collect commands take the same
# operator e2e lease so the overlap fails in seconds instead of hanging.
COLLECT_E2E_LEASE_COMMANDS = frozenset({
    "stock",
    "incr",
    "first-stock",
    "prune-checkpoints",
    "commit-snk-price-receipt",
    "commit-snk-binding-delta",
})
# Per-item failure streaks and per-item success receipts live beside the other
# collect runtime state. The MySQL stream checkpoint remains the resume
# authority; these files carry the per-item evidence between daily runs.
QUARANTINE_PATH = OUT_DIR / "collect_quarantine.json"
ITEM_CHECKPOINT_PATH = OUT_DIR / "collect_item_checkpoints.json"
QUARANTINE_CONTRACT = "collect_item_quarantine_v1"
ITEM_CHECKPOINT_CONTRACT = "collect_item_checkpoint_v1"
QUARANTINE_THRESHOLD = 3  # consecutive failed runs before an item is skipped
# audit P1-7: a streak only clears when the item succeeds again, but a
# quarantined item is never attempted again -- three failures used to be a
# permanent, self-sustaining lock that needed the state file edited by hand.
# A streak whose last failure is older than this decays back to zero.
QUARANTINE_DECAY_DAYS = 7
# audit P1-7 / item 10: reasons that describe the transport, not the item.
# A dead browser session, a spent wall-clock budget or a SIGTERM is a lane
# failure; it must be reported (the lane still fails) but it must never push a
# per-item quarantine streak, which exists to isolate individually bad IDs.
GEMRATE_TRANSPORT_FAILURE_REASONS = frozenset({
    "interrupted_by_signal",
    "budget_exhausted",
    "missing_response",
    "browser_unavailable",
})
# "cloudflare_blocked" also covers "cloudflare_blocked_not_attempted".
GEMRATE_TRANSPORT_FAILURE_PREFIXES = ("browser_collection_failed", "cloudflare_blocked")
# 2026-09-26: a manifest that says Cloudflare blocked the card pages. The lane
# reports this once (the worker turns it into a degraded receipt) instead of
# failing into the retry ladder, which only knocked on the block until PARKED.
GEMRATE_BLOCKED_ERROR = "gemrate_blocked"
# audit P1-5 / audit trap 12: the gemrate adapter already runs max_concurrency=4
# shards, so N workers means 4N Chromes on this host. The "no 429 at 4 Chromes"
# evidence does not transfer to 8; going past 2 needs new measurement, so this
# fails closed rather than clamping silently.
GEMRATE_MAX_WORKERS = 2
RUNTIME_STATE_LEASE = "cardz:collect:runtime-state:v2"
DB_WRITER_LEASE = "cardz:collect:db-writer:v2"
# A sibling lane holds the writer lease for its whole ingest (SNKRDUNK's real
# harvest held it past 120 s on 2026-08-23 A01), so a waiter needs that much
# room; the worker heartbeat is a background thread, so a long GET_LOCK wait
# never reads as a dead worker.
DB_WRITER_LEASE_TIMEOUT_SECONDS = 900
# GET_LOCK is a SELECT. qualified_pool_operator.db() caps every SELECT at
# CARDZ_MAX_EXEC_MS (120 s) to kill runaway reads; on a lease connection that
# cap killed the *wait* itself (errno 3024, A01 2026-08-23 PC lane). A lease
# connection only waits on advisory locks and never reads, so lift it here.
LEASE_SESSION_UNCAP_SQL = "SET SESSION max_execution_time=0"
# An advisory lease lives exactly as long as its connection, and a lease
# connection is idle by design: it runs GET_LOCK, then nothing until
# RELEASE_LOCK. The server therefore cannot tell a working holder from an
# abandoned one, and wait_timeout defaults to 8 hours. 2026-08-25 A01: a
# rehearsal worker died without its FIN reaching the container (WSL -> Windows
# Docker loopback keeps the socket open), and the connection sat Sleep for 85
# minutes still holding en_price_ref, pc_ebay_sales and pc_cdp -- past the next
# day's 03:30 window opening, with no self-recovery and an error message
# ("stop the duplicate collector") that sends the operator hunting a duplicate
# that does not exist. Pinning a short session wait_timeout and pinging from a
# daemon thread makes idleness mean dead: a live holder refreshes the clock, an
# orphan is reaped by the server itself. Sweeping stale connections at startup
# would be the wrong cure -- a legitimate holder is idle for its whole 50-65
# minute sweep and looks identical to an orphan from the outside.
LEASE_IDLE_TIMEOUT_SECONDS = int(os.environ.get("CARDZ_LEASE_IDLE_TIMEOUT_SECONDS") or 900)
# Well inside the timeout so one lost ping never drops a live lease.
LEASE_KEEPALIVE_SECONDS = float(os.environ.get("CARDZ_LEASE_KEEPALIVE_SECONDS") or 120.0)
GEMRATE_PARALLEL_LEASE_SCOPES = ("0-of-4", "1-of-4", "2-of-4", "3-of-4")
PY = sys.executable
# Reporting freshness and the acceptance gate must quote the same number, so
# take it from the gate rather than keeping a second copy that can drift.
SLA_HOURS = CHECKPOINT_SLA_HOURS
# "fresh enough to publish" and "old enough to re-collect" are different
# questions, and answering both with SLA_HOURS is what broke the chain: the
# browser lane runs 09:30 JST, the acceptance gate runs again 03:30 JST, 18h
# later. A stream skipped at 09:30 for being "only" 24h old is 42h old at that
# night's gate -- past SLA -- so daily-accept raises and the board stops
# updating. Every stream failed on its second night. Refresh has to come due
# before one more lane interval can carry a stream past the gate.
LANE_INTERVAL_HOURS = 24
REFRESH_DUE_HOURS = SLA_HOURS - LANE_INTERVAL_HOURS
# REFRESH_DUE_HOURS 答嘅係「幾時**一定要**重收」（由 acceptance gate 倒推），但
# 佢一路兼任咗「幾時**先至准**重收」——兩條唔同嘅問題。lane 一日行一次、又喺
# 00:30 UTC 開跑，於是前一晚 12 個鐘內掂過嘅 stream 全部俾當日嗰轉跳過。
# 實測 2026-08-11 09:30 JST 嗰轉只打 208/993，因為 08-10 夜晚另一轉收咗其餘 785 條。
# HTTP 嗰邊咁樣冇問題：佢哋要嘅只係唔好過 SLA，而 SLA_HOURS - LANE_INTERVAL_HOURS
# 正正保證得到。
#
# 2026-08-15 DADDY 放開「每轉一定 CDP 掃齊 993」：classify 仍然用 0 個鐘
# （全部 due，唔准再靜靜少 785 條），但 `partition_local_pc_stock_pages` 對
# incr 同 stock 一視同仁——本地 HTML 未過 36h SLA 而且 exact PSA10 過關就
# replay，Chrome 只打過期／壞頁。sold 表 30 行／2 日燒滿嘅代價：極熱卡可能
# 少抄幾行新成交，直到 HTML 過 SLA。
PC_REFRESH_DUE_HOURS = 0.0
GEMRATE_WEBSITE_BUDGET_SECONDS = 5400
GEMRATE_CHILD_TIMEOUT_MAX_SECONDS = 4 * 3600
# Same shape as GemRate: a hard cap, never 45*universe hours.
# Healthy 2-tab fetch of ~1000 pages is ~35 min; 90 min is the lost-CDP bound.
PC_CDP_CHILD_TIMEOUT_MAX_SECONDS = 90 * 60
# The PC child used to run with capture_output, so a hung child's stdout stayed
# in this process's memory: nothing on disk said which page it stopped on. One
# log per run, and the last lines echoed into this process's own log on every
# exit code.
PC_CHILD_LOG_DIR = Path(
    os.environ.get("PC_CHILD_LOG_DIR") or (ROOT / "data" / "runtime" / "pc")
)
PC_CHILD_LOG_TAIL_LINES = 20
PC_HIDDEN_LAUNCH_VBS = ROOT / "pipelines" / "pc_cdp_hidden_launch.vbs"
# Exit 4 = the child's Cloudflare storm breaker. It is a provider refusal:
# retry it later, and never read it as a binding failure or as lost coverage.
PC_CF_STORM_CLASS = "pc_cf_storm"
CDP_UNREACHABLE_CLASS = "cdp_unreachable"
PC_CDP_REFRESH_FAILED_CLASS = "pc_cdp_refresh_failed"
# review 2026-08-24: the 9333 child is launched hidden through WSL interop, so
# it is not in this process's group. When the tick SIGKILLs the collect process
# mid-sweep the child survives and keeps fetching; a second child launched by
# the next tick would double the request rate on the one host this whole lane
# exists to stay welcome at (SourceSpec max_concurrency=1 governs V2 task
# claiming, not an orphan OS process).
PC_CHILD_ALREADY_RUNNING_CLASS = "pc_child_already_running"
# pc_cdp_sold_refresh_win imports playwright at module scope, and the probe
# below runs on every sweep -- including where that import cannot succeed. These
# mirror the child's own PROGRESS_STAMP_DIR / HARD_STALL_KILLER_SECONDS so a
# missing browser stack degrades to "probe anyway", never to "no probe at all".
PC_PROGRESS_STAMP_DIR_FALLBACK = Path(
    os.environ.get("PC_PROGRESS_STAMP_DIR")
    or (ROOT / "data/runtime/operator/collect")
)
PC_CHILD_HARD_STALL_SECONDS_FALLBACK = 360.0
PC_CHILD_EXIT_ERROR_CLASSES = {4: PC_CF_STORM_CLASS}
PC_ERROR_CLASS_RETRY_SECONDS = {
    PC_CF_STORM_CLASS: 20 * 60,
    CDP_UNREACHABLE_CLASS: 5 * 60,
    # One tick. The orphan either finishes its own sweep or its hard stall
    # killer takes it, and either way the next tick resumes from the pages it
    # already captured.
    PC_CHILD_ALREADY_RUNNING_CLASS: 10 * 60,
}
# review 2026-08-24 (production: collect took 4 attempts, #2 and #3 died on
# pc_child_already_running while the parent's own previous child was minutes
# from finishing): a child that is still beating is a reason to wait, not to
# burn a tick. Bounded on purpose -- the budget is a rounding error next to the
# 50-65 min sweep, so an exhausted wait still leaves the tick its work, and it
# refuses exactly as before. Nothing here ever kills the child.
# The env knobs are for a hand run and for the suites that drive this path with
# a stubbed child; they only shorten the wait, so no gate can move through them.
PC_CHILD_WAIT_BUDGET_SECONDS = float(
    os.environ.get("PC_CHILD_WAIT_BUDGET_SECONDS") or 120.0
)
PC_CHILD_WAIT_POLL_SECONDS = float(os.environ.get("PC_CHILD_WAIT_POLL_SECONDS") or 5.0)
# The hidden VBS launcher returns when the Windows child has exited, but the
# final ``.rc`` write can become visible to WSL a few seconds later.  This is
# part of the launcher hand-off contract, not a retry: wait for that authority
# file before classifying an otherwise completed 1179-page sweep as failed.
PC_CHILD_RC_GRACE_SECONDS = float(os.environ.get("PC_CHILD_RC_GRACE_SECONDS") or 10.0)
PC_CHILD_RC_POLL_SECONDS = float(os.environ.get("PC_CHILD_RC_POLL_SECONDS") or 0.1)
# Chrome's lifecycle belongs to the launcher preflight and the ChromeCdpWatchdog
# task; the chain only asks whether the session is the right one. 30s, not 90.
PC_ENSURE_CDP_TIMEOUT_SECONDS = 30
SNK_SHARED_HARVEST_ADAPTERS = ("snk_trades", "snk_price")
CARDZ_CDP_PORT = int(os.environ.get("CARDZ_CDP_PORT", "9333"))
# 9222 is the Codex browser profile. Attaching there drives somebody else's
# logged-in Chrome, and the ban on it lived only in AGENTS.md while this knob
# sat here happily accepting it from the environment.
if CARDZ_CDP_PORT == 9222:
    raise RuntimeError(
        "CARDZ_CDP_PORT=9222 is the Codex browser profile and must never be driven. "
        "PriceCharting uses 9333 (headed Chrome)."
    )
SNK_EN_ASSET_DIR = ROOT / "data" / "runtime" / "operator" / "snk-en-assets"
SNK_EN_SOURCE_CODE = "snkrdunk"
SNK_EN_FREEZE_SOURCE_CODE = "snkrdunk_en"
SNK_EN_ACCEPTED_BY = "collect_control:snk_en_image"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class SnkEnPrepared:
    variant_id: int
    external_id: str
    product_url: str
    product_page_payload_sha256: str
    product_page_observed_at: datetime
    master_payload_sha256: str
    default_image_url: str
    default_image_url_sha256: str
    downloaded_bytes_sha256: str
    image: ProcessedSnkDefaultImage
    identity_evidence_sha256: str
    source_observed_at: datetime
    captured_at: datetime
    master_reused: bool
    bytes_reused: bool
    downloaded: bool


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _bytes_sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _parse_datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _utc_naive(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current


def _stream_key(variant_id: int, external_id: Any) -> str:
    return f"{int(variant_id)}:{str(external_id or '').strip()}"[:100]


def _select_items(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    if limit is None or limit <= 0:
        return list(items)
    return list(items[:limit])


def _unique_items(items: list[dict[str, Any]], limit: int | None) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in items:
        variant_id = int(item["variantId"])
        if variant_id in seen:
            continue
        seen.add(variant_id)
        selected.append(item)
        if limit is not None and limit > 0 and len(selected) >= limit:
            break
    return selected


def _jsonl_rows(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _last_json_object(text: str) -> dict[str, Any] | None:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    for line in reversed(lines):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path)
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return result.stdout.strip()


def _age_hours(dt: datetime | None) -> float | None:
    if dt is None:
        return None
    if getattr(dt, "tzinfo", None) is not None:
        dt = dt.replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    return (now - dt).total_seconds() / 3600.0


def _child_interrupted(results: Iterable[Mapping[str, Any]]) -> bool:
    """True when any adapter's child was cut mid-step by the tick's SIGTERM.

    R3 (2026-08-24): the per-adapter result already carried this, but the run
    report did not, so a receipt read by the orchestrator or the observer could
    not tell "this lane failed" from "this lane was interrupted after ingesting
    everything it had captured".
    """

    return any(
        bool(result.get("childInterrupted"))
        for result in results
        if isinstance(result, Mapping)
    )


@contextmanager
def _deferred_termination() -> Iterator[dict[str, bool]]:
    """Do not die mid-child; finish the durable step, then fail the lane.

    audit item 10: the orchestrator terminates the whole worker process group at
    the tick deadline, so this process used to die inside ``subprocess.run``
    while the child had already fetched hundreds of cards.  Nothing was ingested
    and nothing was checkpointed, so the next attempt started from zero.  Inside
    this block SIGTERM/SIGINT only record that they happened; the caller ingests
    whatever the child managed to declare and then reports the lane as failed.
    The previous handlers are restored on exit, so a second signal (or the
    orchestrator's SIGKILL) still stops the process.
    """
    state = {"signalled": False}
    previous: dict[int, Any] = {}

    def _on_signal(signum, _frame) -> None:
        state["signalled"] = True
        print(f"[collect] signal {signum} deferred until the child step finishes", file=sys.stderr)

    for name in ("SIGTERM", "SIGINT"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            previous[number] = signal.signal(number, _on_signal)
        except (ValueError, OSError):
            # Not the main thread / unsupported platform: keep the old
            # all-or-nothing behaviour rather than failing the lane here.
            continue
    try:
        yield state
    finally:
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except (ValueError, OSError):
                continue


def _run(cmd: list[str], *, timeout: int, dry_run: bool) -> dict[str, Any]:
    item = {
        "cmd": cmd,
        "timeout": timeout,
        "dryRun": dry_run,
        "startedAt": utc_now(),
    }
    if dry_run:
        item.update({"exit": 0, "skipped": True, "note": "dry-run; not executed"})
        return item
    try:
        r = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        item.update(
            {
                "exit": r.returncode,
                "stdoutTail": (r.stdout or "")[-4000:],
                "stderrTail": (r.stderr or "")[-2000:],
            }
        )
    except subprocess.TimeoutExpired as exc:
        item.update({"exit": 124, "error": f"timeout:{exc}"})
    except Exception as exc:  # noqa: BLE001
        item.update({"exit": 1, "error": f"{type(exc).__name__}:{exc}"})
    item["finishedAt"] = utc_now()
    return item


def load_checkpoints(cur) -> dict[tuple[str, str], dict[str, Any]]:
    placeholders = ",".join(["%s"] * len(CHECKPOINT_ADAPTERS))
    cur.execute(
        f"""
        SELECT source_code, stream_key, last_effective_at, last_payload_sha256,
               last_run_id, updated_at
        FROM market_ingest_checkpoint
        WHERE source_code IN ({placeholders})
        """,
        CHECKPOINT_ADAPTERS,
    )
    return {
        (str(row["source_code"]), str(row["stream_key"])): dict(row)
        for row in cur.fetchall()
    }


def _checkpoint_for(
    checkpoints: dict[tuple[str, str], dict[str, Any]],
    adapter: str,
    variant_id: int,
    external_id: Any,
) -> dict[str, Any] | None:
    return checkpoints.get((adapter, _stream_key(variant_id, external_id)))


def _poll_mode(
    *,
    has_stock: bool,
    observed_at: datetime | None,
    checkpoint: dict[str, Any] | None,
    empty_poll_is_complete: bool = False,
    refresh_due_hours: float = REFRESH_DUE_HOURS,
) -> str:
    if not has_stock:
        if not empty_poll_is_complete or checkpoint is None:
            return "stock"
        age = _age_hours(_parse_datetime(checkpoint.get("last_effective_at")))
        return "incr" if age is None or age > refresh_due_hours else "ok"
    if checkpoint is None:
        return "incr"
    last_success = checkpoint.get("last_effective_at")
    age = _age_hours(_parse_datetime(last_success))
    return "incr" if age is None or age > refresh_due_hours else "ok"


def _insert_control_run(
    cur,
    *,
    adapter: str,
    mode: str,
    items: list[dict[str, Any]],
    payload: Any,
    started_at: datetime,
    completed_at: datetime,
) -> int:
    payload_sha = _sha256(payload)
    run_key = _sha256(
        {
            "adapter": adapter,
            "mode": mode,
            "startedAt": started_at.isoformat(),
            "payloadSha256": payload_sha,
            "streams": [
                _stream_key(int(item["variantId"]), item.get("externalId"))
                for item in items
            ],
        }
    )
    cur.execute(
        """
        INSERT INTO market_ingest_run
            (run_key, source_code, ingest_mode, effective_at, payload_sha256,
             manifest_sha256, status, observed_count, accepted_count,
             quarantined_count, rejected_count, started_at, completed_at)
        VALUES (%s,%s,%s,%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
        ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), status='completed',
            observed_count=VALUES(observed_count), accepted_count=VALUES(accepted_count),
            completed_at=VALUES(completed_at)
        """,
        (
            run_key,
            adapter,
            "incremental" if mode == "incr" else "stock",
            completed_at,
            payload_sha,
            payload_sha,
            len(items),
            len(items),
            started_at,
            completed_at,
        ),
    )
    return int(cur.lastrowid)


def _upsert_checkpoints(
    cur,
    *,
    adapter: str,
    items: list[dict[str, Any]],
    run_id: int,
    completed_at: datetime,
    payload_sha_by_external: dict[str, str] | None = None,
) -> int:
    payload_sha_by_external = payload_sha_by_external or {}
    count = 0
    for item in items:
        external = str(item.get("externalId") or "")
        payload_sha = payload_sha_by_external.get(external) or _sha256(
            {
                "adapter": adapter,
                "variantId": int(item["variantId"]),
                "externalId": external,
                "completedAt": completed_at.isoformat(),
            }
        )
        cur.execute(
            """
            INSERT INTO market_ingest_checkpoint
                (source_code, stream_key, last_effective_at, last_payload_sha256,
                 last_run_id, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                last_effective_at=VALUES(last_effective_at),
                last_payload_sha256=VALUES(last_payload_sha256),
                last_run_id=VALUES(last_run_id),
                updated_at=VALUES(updated_at)
            """,
            (
                adapter,
                _stream_key(int(item["variantId"]), external),
                completed_at,
                payload_sha,
                run_id,
                completed_at,
            ),
        )
        count += 1
    return count


def record_successful_poll(
    *,
    adapter: str,
    mode: str,
    items: list[dict[str, Any]],
    payload: Any,
    started_at: datetime,
    payload_sha_by_external: dict[str, str] | None = None,
    completed_at: datetime | None = None,
) -> dict[str, Any]:
    if not items:
        return {"runId": None, "checkpointed": 0}
    completed_at = completed_at or datetime.now(timezone.utc)
    if completed_at.tzinfo is not None:
        completed_at = completed_at.astimezone(timezone.utc).replace(tzinfo=None)
    if started_at.tzinfo is not None:
        started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
    conn = db()
    try:
        cur = conn.cursor()
        run_id = _insert_control_run(
            cur,
            adapter=adapter,
            mode=mode,
            items=items,
            payload=payload,
            started_at=started_at,
            completed_at=completed_at,
        )
        checkpointed = _upsert_checkpoints(
            cur,
            adapter=adapter,
            items=items,
            run_id=run_id,
            completed_at=completed_at,
            payload_sha_by_external=payload_sha_by_external,
        )
        conn.commit()
        return {"runId": run_id, "checkpointed": checkpointed}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """House pattern: durable tmp write + os.replace, never a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _load_runtime_state(path: Path, contract: str) -> dict[str, Any]:
    if not path.is_file():
        return {"contract": contract, "updatedAt": None, "adapters": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        state = None
    if not isinstance(state, dict) or state.get("contract") != contract:
        # A corrupt or foreign state file must not silently veto collection.
        return {"contract": contract, "updatedAt": None, "adapters": {}}
    state.setdefault("adapters", {})
    return state


class _LeaseSessionGuard:
    """Make an advisory-lease connection prove it is still owned.

    Bounds the session's idle life to LEASE_IDLE_TIMEOUT_SECONDS and pings from
    a daemon thread for as long as this process lives. The thread dies with the
    process -- including SIGKILL, which is exactly the case that orphaned the
    lease -- so the server reaps the abandoned session and the lock with it.
    """

    def __init__(
        self,
        connection,
        *,
        label: str,
        idle_timeout: int = LEASE_IDLE_TIMEOUT_SECONDS,
        interval: float = LEASE_KEEPALIVE_SECONDS,
    ) -> None:
        self._connection = connection
        self._label = label
        self._interval = max(1.0, float(interval))
        self._stop = threading.Event()
        self._lock = threading.Lock()
        try:
            cursor = connection.cursor()
            cursor.execute("SET SESSION wait_timeout=%s", (max(2, int(idle_timeout)),))
        except Exception as exc:  # noqa: BLE001 - falls back to the server default
            print(
                f"[collect] lease keepalive could not bound {self._label} session: {exc}",
                flush=True,
            )
        self._thread = threading.Thread(
            target=self._run, name=f"lease-keepalive-{label}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._stop.is_set():
                    return
                try:
                    cursor = self._connection.cursor()
                    cursor.execute("SELECT 1")
                    cursor.fetchall()
                except Exception:  # noqa: BLE001 - connection gone, lease gone with it
                    return

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=self._interval + 5.0)
        # Barrier: never let an in-flight ping share the connection with the
        # RELEASE_LOCK that follows.
        with self._lock:
            pass


@contextmanager
def _runtime_state_lease():
    """Serialize shared JSON read-modify-write across parallel V2 sources.

    Atomic replace prevents torn bytes, but it does not prevent two processes
    from loading the same old document and losing each other's adapter entry.
    MySQL advisory locks work across WSL and Windows and are already part of
    this collector's runtime contract.
    """
    load_env()
    connection = db()
    cursor = connection.cursor()
    acquired = False
    guard: _LeaseSessionGuard | None = None
    try:
        cursor.execute(LEASE_SESSION_UNCAP_SQL)
        cursor.execute("SELECT GET_LOCK(%s, 30) AS acquired", (RUNTIME_STATE_LEASE,))
        row = cursor.fetchone() or {}
        acquired = int(row.get("acquired") or 0) == 1
        if not acquired:
            raise RuntimeError("collect runtime-state lease timed out")
        # Only now: the keepalive owns the connection for the idle window, and
        # a blocked GET_LOCK is an active query, which wait_timeout ignores.
        guard = _LeaseSessionGuard(connection, label="runtime-state")
        yield
    finally:
        if guard is not None:
            guard.stop()
        try:
            if acquired:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (RUNTIME_STATE_LEASE,))
        finally:
            connection.close()


@contextmanager
def _db_writer_lease(timeout_seconds: int = DB_WRITER_LEASE_TIMEOUT_SECONDS):
    """Serialize short ingest/checkpoint transactions, never source fetches.

    V2 deliberately runs provider network work in parallel.  Those workers
    still converge on the same observation/checkpoint indexes, where parallel
    inserts can deadlock even when their variant shards do not overlap.  The
    advisory lease is held only around DB mutation so network concurrency is
    preserved and an interrupted worker releases it with its connection.
    """

    load_env()
    connection = db()
    cursor = connection.cursor()
    acquired = False
    guard: _LeaseSessionGuard | None = None
    try:
        cursor.execute(LEASE_SESSION_UNCAP_SQL)
        cursor.execute(
            "SELECT GET_LOCK(%s, %s) AS acquired",
            (DB_WRITER_LEASE, max(1, int(timeout_seconds))),
        )
        row = cursor.fetchone() or {}
        value = row.get("acquired") if isinstance(row, dict) else row[0]
        acquired = int(value or 0) == 1
        if not acquired:
            raise RuntimeError("collect DB-writer lease timed out")
        # After the wait, never during it: the GET_LOCK above can block for
        # DB_WRITER_LEASE_TIMEOUT_SECONDS on a busy sibling lane, and a blocked
        # query keeps the session active, so wait_timeout cannot reap it there.
        guard = _LeaseSessionGuard(connection, label="db-writer")
        yield
    finally:
        if guard is not None:
            guard.stop()
        try:
            if acquired:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (DB_WRITER_LEASE,))
        finally:
            connection.close()


# `reason` is deliberately NOT in the UPDATE list: first reason wins.  The
# receipt carries a stored row's reason back unchanged
# (pc_sale_title_quarantine.compose_entries), so the row says why the sale was
# condemned when it was condemned, not whichever discriminator re-flags it later.
PC_SALE_TITLE_QUARANTINE_UPSERT = """
    INSERT INTO market_pc_sale_title_quarantine
      (sale_observation_id, variant_id, reason, receipt_sha256, written_at)
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
      variant_id=VALUES(variant_id),
      receipt_sha256=VALUES(receipt_sha256),
      written_at=VALUES(written_at)
"""


def _sync_pc_sale_title_quarantine() -> dict[str, Any]:
    """Materialise the PC sale quarantine receipt (058).

    The receipt stays the derivation -- pc_sale_title_quarantine.py runs the
    discriminators (c11_pc_sold_ingest.title_collector_contradiction and, since
    2026-09-25, sale_price_outlier.is_isolated_price_outlier) and writes it.
    This only copies it into market_pc_sale_title_quarantine, so
    operator_eligible_accepted_psa10_sales_rows can drop the poisoned
    transactions once for every reader, instead of each reader having to
    remember to subtract them (operator_fe_export's daily projection and
    operator_card_daily_fact_projection never did).

    Upsert only, never DELETE: removing a row re-admits a sale the
    discriminator once condemned, and that is not a side effect a nightly sync
    gets to have.  A missing receipt raises, exactly like
    psa10_latest_sale_quote.load_title_quarantine -- "no file" must never be
    read as "nothing is quarantined".

    A missing TABLE (error 1146) is the one tolerated case and is reported as
    skipped=table_absent; see the comment at the except.

    Caller holds the DB-writer lease.
    """

    from psa10_latest_sale_quote import QUARANTINE_RECEIPT  # noqa: E402

    raw = QUARANTINE_RECEIPT.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    entries = doc.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"quarantine receipt has no entries list: {QUARANTINE_RECEIPT}")
    receipt_sha256 = hashlib.sha256(raw).hexdigest()
    written_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        (
            int(entry["saleObservationId"]),
            int(entry["variantId"]),
            str(entry.get("reason") or "title_collector_contradiction")[:64],
            receipt_sha256,
            written_at,
        )
        for entry in entries
    ]
    written = 0
    skipped = ""
    if rows:
        load_env()
        connection = db()
        try:
            cursor = connection.cursor()
            try:
                cursor.executemany(PC_SALE_TITLE_QUARANTINE_UPSERT, rows)
            except Exception as error:  # noqa: BLE001 - re-raised unless it is 1146
                # 1146 = table does not exist, i.e. 058 has not been applied to
                # this database yet.  That is the bootstrap window between
                # merging this code and the chain's migrate stage running, and
                # in it the OLD view is still installed -- so the quarantine is
                # still enforced exactly where it was before 058
                # (psa10_latest_sale_quote.plan drops the sales, the FE
                # subtracts them).  Degrading to that is not opening a hole;
                # aborting the PriceCharting lane over it would be.  Every
                # other error still raises.
                if tuple(getattr(error, "args", ()))[:1] != (1146,):
                    raise
                skipped = "table_absent"
            else:
                connection.commit()
                written = len(rows)
        finally:
            connection.close()
    report: dict[str, Any] = {
        "receipt": str(QUARANTINE_RECEIPT),
        "receiptSha256": receipt_sha256,
        "entries": written,
    }
    if skipped:
        report["skipped"] = skipped
    return report


def _mint_sale_quotes(source: str, *, dry_run: bool) -> dict[str, Any]:
    """Mint the ranked PSA10 quote from the latest real sale (owner 2026-08-23).

    Runs over the WHOLE routed universe, not this tick's due batch, and runs
    even when nothing was due.  Both price adapters are due-batched, so a hook
    that only fired after a successful fetch would leave most cards without a
    quote on most nights, and checked_at is what the accept-side staleness gate
    reads -- an unminted card goes stale and aborts the 100% contract.
    Inside the V2 chain an unchanged sale is not re-minted at all: the mint
    skips a variant whose quote already stands inside the business window
    with the same payload_sha256 (psa10_latest_sale_quote.same_day_standing_quotes),
    so a rehearsal of the same day adds zero quote revisions.

    Caller holds the DB-writer lease; this builds the argv, runs it, and for
    the PriceCharting lane first materialises that lane's title quarantine
    receipt into the database (058) so the sales-history view excludes the same
    transactions the planner refuses to quote.
    """

    cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/psa10_latest_sale_quote.py",
        "--source",
        source,
        "--variants",
        "all",
        "--write",
    ]
    if dry_run:
        return {"skipped": "dry_run", "source": source}
    extra: dict[str, Any] = {}
    if source == "pricecharting":
        # Before the mint, not after: a receipt this run cannot read is a hard
        # stop, and the quote it would otherwise mint is exactly the one the
        # quarantine exists to block.
        extra["titleQuarantine"] = _sync_pc_sale_title_quarantine()
    result = _run(cmd, timeout=900, dry_run=False)
    if result.get("exit") != 0:
        raise RuntimeError(f"{source} latest-sale quote mint failed")
    return {**result, **extra}


def _streak_has_decayed(entry: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """audit P1-7: a streak whose last failure is older than the decay window is stale.

    Quarantine is meant to skip an item that keeps failing, not to retire it.
    Without decay the only event that clears a streak -- one later success --
    can never happen, because a quarantined item is never attempted again.
    """
    last_attempt = _parse_datetime(entry.get("lastAttempt"))
    if last_attempt is None:
        return False
    if last_attempt.tzinfo is None:
        last_attempt = last_attempt.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return (reference - last_attempt).total_seconds() > QUARANTINE_DECAY_DAYS * 86400


def _quarantined_streams(adapter: str) -> dict[str, dict[str, Any]]:
    state = _load_runtime_state(QUARANTINE_PATH, QUARANTINE_CONTRACT)
    entries = state["adapters"].get(adapter) or {}
    return {
        stream: entry
        for stream, entry in entries.items()
        if int(entry.get("consecutiveFailures") or 0) >= QUARANTINE_THRESHOLD
        and not _streak_has_decayed(entry)
    }


def _partition_quarantined(
    adapter: str, items: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split due items into pollable and quarantined; quarantined are reported, never dropped."""
    quarantined_streams = _quarantined_streams(adapter)
    active: list[dict[str, Any]] = []
    quarantined: list[dict[str, Any]] = []
    for item in items:
        stream = _stream_key(int(item["variantId"]), item.get("externalId"))
        entry = quarantined_streams.get(stream)
        if entry is None:
            active.append(item)
            continue
        quarantined.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": str(item.get("externalId") or ""),
                "reason": entry.get("reason"),
                "consecutiveFailures": int(entry.get("consecutiveFailures") or 0),
                "lastAttempt": entry.get("lastAttempt"),
            }
        )
    return active, quarantined


def _record_item_outcomes(
    adapter: str,
    *,
    succeeded: list[dict[str, Any]],
    failed: list[dict[str, Any]],
) -> None:
    """Advance per-item failure streaks; one success clears the item's entry."""
    if not succeeded and not failed:
        return
    with _runtime_state_lease():
        now = utc_now()
        state = _load_runtime_state(QUARANTINE_PATH, QUARANTINE_CONTRACT)
        entries = dict(state["adapters"].get(adapter) or {})
        for item in succeeded:
            entries.pop(_stream_key(int(item["variantId"]), item.get("externalId")), None)
        for row in failed:
            stream = _stream_key(int(row["variantId"]), row.get("externalId"))
            previous = entries.get(stream) or {}
            if _streak_has_decayed(previous):
                # audit P1-7: an old streak is not evidence about today.
                previous = {}
            entries[stream] = {
                "variantId": int(row["variantId"]),
                "externalId": str(row.get("externalId") or ""),
                "reason": str(row.get("error") or row.get("reason") or "unknown"),
                "consecutiveFailures": int(previous.get("consecutiveFailures") or 0) + 1,
                "lastAttempt": now,
            }
        state["adapters"][adapter] = entries
        state["updatedAt"] = now
        _write_json_atomic(QUARANTINE_PATH, state)


def _persist_item_checkpoints(
    adapter: str,
    *,
    mode: str,
    items: list[dict[str, Any]],
    payload_sha_by_external: Mapping[str, str] | None = None,
    run_id: int | None = None,
) -> None:
    """Mirror per-item success into the runtime checkpoint JSON as it lands.

    The DB stream checkpoint stays the resume authority; this file is the
    incrementally-updated per-item receipt that survives a later lane abort.
    """
    if not items:
        return
    with _runtime_state_lease():
        now = utc_now()
        payload_sha_by_external = payload_sha_by_external or {}
        state = _load_runtime_state(ITEM_CHECKPOINT_PATH, ITEM_CHECKPOINT_CONTRACT)
        entries = dict(state["adapters"].get(adapter) or {})
        for item in items:
            external = str(item.get("externalId") or "")
            entries[_stream_key(int(item["variantId"]), external)] = {
                "variantId": int(item["variantId"]),
                "externalId": external,
                "mode": mode,
                "lastSuccessAt": now,
                "payloadSha256": payload_sha_by_external.get(external),
                "runId": run_id,
            }
        state["adapters"][adapter] = entries
        state["updatedAt"] = now
        _write_json_atomic(ITEM_CHECKPOINT_PATH, state)


def pc_child_error_class(exit_code: int | None) -> str:
    """Map the PC child's exit code to a retry class.

    Exit 4 is its Cloudflare storm breaker. Those pages were refused by the
    provider: they are not identities we failed to bind and not coverage we
    lost, so the class must never be read as either.
    """
    try:
        code = int(exit_code)
    except (TypeError, ValueError):
        return PC_CDP_REFRESH_FAILED_CLASS
    return PC_CHILD_EXIT_ERROR_CLASSES.get(code, PC_CDP_REFRESH_FAILED_CLASS)


def pc_error_retry_after_seconds(error_class: str) -> int | None:
    return PC_ERROR_CLASS_RETRY_SECONDS.get(str(error_class))


def pc_error_is_retryable(error_class: str) -> bool:
    return str(error_class) in PC_ERROR_CLASS_RETRY_SECONDS


def _running_under_wsl() -> bool:
    if sys.platform == "win32":
        return False
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        version = Path("/proc/version").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "microsoft" in version.lower()


def pc_business_date() -> str:
    value = os.environ.get("CARDZ_V2_BUSINESS_DATE", "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return datetime.now(JST).date().isoformat()


def pc_child_log_path(
    *, pid: int | None = None, business_date: str | None = None
) -> Path:
    owner = os.getpid() if pid is None else int(pid)
    return PC_CHILD_LOG_DIR / f"pc_cdp_{business_date or pc_business_date()}_{owner}.log"


def _read_rc_file(path: Path) -> int | None:
    """The child's real exit code.

    A return code does not propagate back through WSL interop, so the hidden
    launcher writes it to this file and the file is the authority.
    """
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        token = line.strip()
        if not token:
            continue
        try:
            return int(token)
        except ValueError:
            return None
    return None


def pc_wait_for_rc_file(
    path: Path,
    *,
    grace_seconds: float = PC_CHILD_RC_GRACE_SECONDS,
    poll_seconds: float = PC_CHILD_RC_POLL_SECONDS,
) -> tuple[int | None, float]:
    """Read the hidden launcher's authoritative rc after its WSL visibility lag."""

    started = time.monotonic()
    while True:
        value = _read_rc_file(path)
        if value is not None:
            return value, round(time.monotonic() - started, 3)
        remaining = float(grace_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            return None, round(time.monotonic() - started, 3)
        time.sleep(min(float(poll_seconds), remaining))


def _log_tail(path: Path, lines: int = PC_CHILD_LOG_TAIL_LINES) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-int(lines):])


def pc_hidden_launch_command(
    cmd: list[str], *, rc_path: Path, log_path: Path
) -> list[str]:
    """Contract C5: rc-file, log-file, then the command.

    The command runs inside cmd.exe on the Windows side, so the executable must
    be a Windows path. 2026-08-22 19:34 JST: WINDOWS_PY is a /mnt/c/... path
    (fine for WSL interop, which resolves it) and cmd.exe answered "cannot
    find the path specified" in 0.2 s for every PC attempt.
    """
    exe, *rest = cmd
    if exe.startswith("/"):
        exe = _windows_path(Path(exe))
    return [
        "wscript.exe",
        "//nologo",
        "//B",
        _windows_path(PC_HIDDEN_LAUNCH_VBS),
        _windows_path(rc_path),
        _windows_path(log_path),
        exe,
        *rest,
    ]


def pc_refresh_error_class(report: Any) -> str:
    """The PC page-acquisition failure class inside a collect report, if any.

    `_collect_mode` reports the fetch verdict under `pcRefresh.networkRefresh`;
    the adapters downstream only say `fresh_pc_pages_unavailable`, which cannot
    tell a refused sweep (contention) apart from a failed one.
    """

    if not isinstance(report, Mapping):
        return ""
    pc_refresh = report.get("pcRefresh")
    if not isinstance(pc_refresh, Mapping):
        return ""
    network = pc_refresh.get("networkRefresh")
    if not isinstance(network, Mapping) or bool(network.get("ok")):
        return ""
    return str(network.get("errorClass") or "")


def pc_child_alive_stamp(*, now: datetime | None = None) -> dict[str, Any] | None:
    """A 9333 child that is still running, seen through its own progress stamp.

    The child's Windows PID never comes back through WSL interop, so liveness is
    read off the artifact the child already maintains for its hard stall killer:
    ``pc_cdp_progress.<pid>.stamp``, touched on every page decision. That killer
    is what makes the window trustworthy in both directions -- a live child
    beats inside ``HARD_STALL_KILLER_SECONDS`` or the killer takes it -- so a
    stamp older than the window belongs to a child that is already dead and must
    not wedge the lane forever.

    Returns ``None`` when no child is running. A false positive costs one tick
    and retries; a false negative costs two sweeps hammering PriceCharting at
    once, so this errs closed.
    """

    try:
        from pc_cdp_sold_refresh_win import (  # noqa: PLC0415
            HARD_STALL_KILLER_SECONDS,
            PROGRESS_STAMP_DIR,
        )
    except Exception:  # noqa: BLE001 - playwright, or anything else that module needs
        # An unimportable child module must not silently disable single-flight:
        # that is the failure mode this whole probe exists to prevent.
        HARD_STALL_KILLER_SECONDS = PC_CHILD_HARD_STALL_SECONDS_FALLBACK
        PROGRESS_STAMP_DIR = PC_PROGRESS_STAMP_DIR_FALLBACK

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    window = float(HARD_STALL_KILLER_SECONDS)
    newest: dict[str, Any] | None = None
    try:
        candidates = sorted(Path(PROGRESS_STAMP_DIR).glob("pc_cdp_progress.*.stamp"))
    except OSError:
        return None
    for stamp in candidates:
        try:
            beat = datetime.fromtimestamp(stamp.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        age = (moment - beat).total_seconds()
        if age > window:
            continue
        if newest is None or age < float(newest["ageSeconds"]):
            newest = {
                "stamp": str(stamp),
                "beatAt": beat.isoformat().replace("+00:00", "Z"),
                "ageSeconds": round(age, 3),
                "windowSeconds": window,
            }
    return newest


def pc_wait_for_child_exit(
    *,
    budget_seconds: float = PC_CHILD_WAIT_BUDGET_SECONDS,
    poll_seconds: float = PC_CHILD_WAIT_POLL_SECONDS,
    probe: Any = None,
) -> dict[str, Any]:
    """Wait, inside a bounded budget, for a live 9333 child to stop beating.

    Liveness stays the child's own progress stamp (``pc_child_alive_stamp``), so
    a stamp that went stale mid-wait -- the hard stall killer took the child --
    ends the wait the same way a clean exit does. The budget expiring is not an
    escalation: the caller falls through to the same refusal as before, because
    the one thing worse than losing a tick is two sweeps on PriceCharting.
    """

    probe_fn = pc_child_alive_stamp if probe is None else probe
    started = time.monotonic()
    polls = 0
    last: dict[str, Any] | None = None
    while True:
        remaining = float(budget_seconds) - (time.monotonic() - started)
        if remaining <= 0:
            break
        time.sleep(min(float(poll_seconds), remaining))
        polls += 1
        last = probe_fn()
        if last is None:
            return {
                "exited": True,
                "polls": polls,
                "waitedSeconds": round(time.monotonic() - started, 3),
                "budgetSeconds": float(budget_seconds),
            }
    return {
        "exited": False,
        "polls": polls,
        "waitedSeconds": round(time.monotonic() - started, 3),
        "budgetSeconds": float(budget_seconds),
        "lastStamp": last,
    }


def _run_pc_child(
    cmd: list[str], *, timeout: int, dry_run: bool, use_vbs: bool | None = None
) -> dict[str, Any]:
    """Run the PC CDP child with its output on disk and its rc read from a file.

    Two failures this closes. The child ran with capture_output, so a hung
    child's stdout lived only in this process's memory and nobody could see the
    page it stopped on. And a plain WSL-interop launch of the Windows Python
    pops a Windows Terminal window; on 2026-08-22 a human closing that window
    killed the tick with CTRL_CLOSE. Hidden launch, log on disk, rc from file.
    """
    log_path = pc_child_log_path()
    rc_path = log_path.with_name(log_path.name + ".rc")
    hidden = _running_under_wsl() if use_vbs is None else bool(use_vbs)
    item: dict[str, Any] = {
        "cmd": cmd,
        "timeout": timeout,
        "dryRun": dry_run,
        "startedAt": utc_now(),
        "childLog": str(log_path),
        "hiddenLaunch": hidden,
    }
    if dry_run:
        item.update({"exit": 0, "skipped": True, "note": "dry-run; not executed"})
        return item
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rc_path.unlink()
    except OSError:
        pass
    interop_cwd = str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32"
    try:
        if hidden:
            launch = pc_hidden_launch_command(cmd, rc_path=rc_path, log_path=log_path)
            item["rcFile"] = str(rc_path)
            item["launchCmd"] = launch
            proc = subprocess.run(
                launch,
                cwd=interop_cwd,
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            rc_value, rc_waited = pc_wait_for_rc_file(rc_path)
            item["rcFileExit"] = rc_value
            item["rcFileWaitedSeconds"] = rc_waited
            if rc_value is None:
                item["exit"] = proc.returncode
                item["error"] = "pc_child_rc_file_missing"
            else:
                item["exit"] = rc_value
        else:
            with log_path.open("w", encoding="utf-8", errors="replace") as sink:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    stdout=sink,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=timeout,
                )
            item["exit"] = proc.returncode
    except subprocess.TimeoutExpired as exc:
        item.update({"exit": 124, "error": f"timeout:{exc}"})
    except Exception as exc:  # noqa: BLE001
        item.update({"exit": 1, "error": f"{type(exc).__name__}:{exc}"})
    tail = _log_tail(log_path)
    item["childLogTail"] = tail
    # Any exit code, not just failures: a "successful" run that stopped early is
    # exactly the case that used to leave nothing behind.
    print(f"PC child exit={item.get('exit')} log={log_path}", flush=True)
    for line in tail.splitlines():
        print(f"  pc-child| {line}", flush=True)
    item["finishedAt"] = utc_now()
    return item


def _cdp_unreachable(**extra: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "errorClass": CDP_UNREACHABLE_CLASS,
        "retryable": True,
        "retryAfterSeconds": pc_error_retry_after_seconds(CDP_UNREACHABLE_CLASS),
    }
    result.update(extra)
    return result


def ensure_cdp(port: int = 9333) -> dict[str, Any]:
    """Check the one dedicated CARDZ CDP session; do not build it here.

    Always go through ensure_chrome_cdp.ps1. A 200 on /json/version is not
    identity: WSL/Headless Chrome on 9333 answers 200 and must be rejected.
    Version-only 200 is also not a usable session: a hung /json/list is a
    jammed DevTools websocket.

    Identity only. 9333 had three owners — the launcher preflight, the
    ChromeCdpWatchdog task, and this chain — and they recycled Chrome under
    each other mid sweep. The chain now answers one question inside 30s and
    reports a retryable ``cdp_unreachable`` instead of spending 90s trying to
    start a browser it does not own. PC_ENSURE_CDP_FULL=1 restores the full
    evict/start path for a hand run.
    """
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    if not ps1.exists():
        return _cdp_unreachable(reason="ensure_chrome_cdp.ps1 missing")
    identity_only = os.environ.get("PC_ENSURE_CDP_FULL") != "1"
    # From WSL, call powershell.exe if present
    pwsh = "powershell.exe"
    cmd = [
        pwsh,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        _windows_path(ps1),
        "-Port",
        str(port),
    ]
    if identity_only:
        cmd.append("-IdentityOnly")
    try:
        # A Windows executable launched through WSL interop must not inherit a
        # /mnt/c/... working directory.  PowerShell treats that translated cwd
        # inconsistently even though the explicit -File path is valid.
        interop_cwd = str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32"
        r = subprocess.run(
            cmd,
            cwd=interop_cwd,
            capture_output=True,
            text=True,
            timeout=PC_ENSURE_CDP_TIMEOUT_SECONDS,
        )
        observed = {
            "identityOnly": identity_only,
            "exit": r.returncode,
            "stdoutTail": (r.stdout or "")[-1500:],
            "stderrTail": (r.stderr or "")[-1000:],
        }
        if r.returncode == 0:
            return {"ok": True, **observed}
        return _cdp_unreachable(**observed)
    except Exception as exc:  # noqa: BLE001
        return _cdp_unreachable(
            identityOnly=identity_only, error=f"{type(exc).__name__}:{exc}"
        )


def _migration_029_ready(cur) -> bool:
    cur.execute(
        "SELECT COUNT(*) AS n FROM cardz_schema_version WHERE version_code='029'"
    )
    row = cur.fetchone()
    return int((row.get("n") if isinstance(row, dict) else row[0]) or 0) == 1


def _require_migration_029(cur) -> None:
    if not _migration_029_ready(cur):
        raise RuntimeError(
            "snk_en_image requires completed migration 029; table existence alone is not readiness"
        )


def load_universe_rows(cur) -> list[dict[str, Any]]:
    u = current_universe(cur)
    vids = u["variantIds"]
    if not vids:
        return []
    ph = ",".join(["%s"] * len(vids))
    cur.execute(
        f"""
        SELECT v.id AS variant_id, v.opaque_id, v.card_language, v.canonical_name, v.collector_number
        FROM catalog_variant v
        WHERE v.id IN ({ph})
        """,
        vids,
    )
    base = {int(r["variant_id"]): dict(r) for r in cur.fetchall()}

    cur.execute(
        f"""
        SELECT variant_id, source_code, external_entity_id, match_status,
               evidence_sha256, bind_evidence_json
        FROM catalog_source_identity
        WHERE variant_id IN ({ph})
          AND match_status = 'exact'
        """,
        vids,
    )
    binds: dict[int, dict[str, str]] = {}
    snk_en_identity: dict[int, dict[str, Any]] = {}
    exact_snk_ids_by_variant: dict[int, set[str]] = {}
    for r in cur.fetchall():
        variant_id = int(r["variant_id"])
        source_code = str(r["source_code"])
        external_id = str(r["external_entity_id"])
        binds.setdefault(variant_id, {})[source_code] = external_id
        if source_code in {"snk", "snkrdunk", "snkrdunk_en"}:
            exact_snk_ids_by_variant.setdefault(variant_id, set()).add(external_id)
        if source_code == SNK_EN_SOURCE_CODE:
            previous = snk_en_identity.get(variant_id)
            if previous and str(previous["externalId"]) != external_id:
                raise RuntimeError(
                    f"multiple exact snkrdunk IDs for active variant {variant_id}"
                )
            snk_en_identity[variant_id] = {
                "externalId": external_id,
                "evidenceSha256": str(r.get("evidence_sha256") or ""),
            }
    multiple_exact_snk = {
        variant_id: sorted(external_ids)
        for variant_id, external_ids in exact_snk_ids_by_variant.items()
        if len(external_ids) > 1
    }
    if multiple_exact_snk:
        raise RuntimeError(f"multiple exact SNK IDs in active universe: {multiple_exact_snk}")

    # snk_market_data ingests kline only for pairs in operator_strict_source_identity
    # (load_exact_snk_item_to_variant), which is stricter than match_status='exact'
    # above: the 037 view also demands 036 provider-native evidence and a matching
    # capture receipt. A pre-036 binding that S7 declined to restamp (soft parallel
    # mismatch) keeps status 'exact' and so used to land in the snk_price poll list,
    # where the ingest then skipped it as no_exact_identity -- and that one skip
    # failed the adapter contract, throwing away the checkpoints of every card that
    # HAD been ingested in the same batch. Poll what the ingest can accept.
    cur.execute(
        f"""
        SELECT variant_id, external_entity_id
        FROM operator_strict_source_identity
        WHERE variant_id IN ({ph})
          AND source_code = 'snkrdunk'
        """,
        vids,
    )
    strict_snk_by_variant: dict[int, str] = {}
    for r in cur.fetchall():
        strict_snk_by_variant[int(r["variant_id"])] = str(r["external_entity_id"])

    snk_en_accepted: dict[int, dict[str, Any]] = {}
    if _migration_029_ready(cur):
        cur.execute(
            f"""
            SELECT variant_id, canonical_image_snk_item_id,
                   canonical_image_source_observed_at
            FROM operator_canonical_image_projection
            WHERE variant_id IN ({ph})
            """,
            vids,
        )
        for r in cur.fetchall():
            snk_en_accepted[int(r["variant_id"])] = {
                "externalId": str(r["canonical_image_snk_item_id"]),
                "observedAt": r["canonical_image_source_observed_at"],
            }

    cur.execute(
        f"""
        SELECT variant_id, source_code, MAX(sold_at) AS max_sold, COUNT(*) AS n
        FROM market_sale_observation
        WHERE variant_id IN ({ph})
        GROUP BY variant_id, source_code
        """,
        vids,
    )
    sales: dict[int, dict[str, dict[str, Any]]] = {}
    for r in cur.fetchall():
        sales.setdefault(int(r["variant_id"]), {})[str(r["source_code"])] = {
            "max": r["max_sold"],
            "n": int(r["n"] or 0),
        }

    cur.execute(
        f"""
        SELECT variant_id, source_code, MAX(effective_at) AS max_eff, COUNT(*) AS n
        FROM market_price_observation
        WHERE variant_id IN ({ph})
        GROUP BY variant_id, source_code
        """,
        vids,
    )
    prices: dict[int, dict[str, dict[str, Any]]] = {}
    for r in cur.fetchall():
        prices.setdefault(int(r["variant_id"]), {})[str(r["source_code"])] = {
            "max": r["max_eff"],
            "n": int(r["n"] or 0),
        }

    # An arbitrary legacy PriceCharting/eBay row is not proof that the current
    # EN PSA10 reference contract has been materialized.  Registry readiness
    # must use the same explicit PriceCharting field and exact identity gate as
    # the canonical projections; otherwise a fresh checkpoint can hide a
    # missing ``pc_psa10_current_price_v1`` observation indefinitely.
    cur.execute(
        f"""
        SELECT p.variant_id, MAX(p.effective_at) AS max_eff
        FROM market_price_observation p
        INNER JOIN market_source_observation so ON so.id=p.source_observation_id
          AND so.source_code='pricecharting'
          AND so.external_entity_id=p.source_external_entity_id
          AND so.payload_sha256=p.payload_sha256
          AND so.observed_date=p.observed_date
        INNER JOIN catalog_source_identity si ON si.variant_id=p.variant_id
          AND si.source_code='pricecharting'
          AND si.external_entity_id=p.source_external_entity_id
          AND LOWER(si.match_status)='exact'
        WHERE p.variant_id IN ({ph})
          AND p.source_code='pricecharting'
          AND p.metric_status='ready' AND p.price_usd>0 AND p.source_priority=95
          AND so.observation_kind='psa10_price_guide'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
          AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%%'
          AND p.source_external_entity_id REGEXP '^[0-9]+$'
        GROUP BY p.variant_id
        """,
        vids,
    )
    explicit_pc_prices = {
        int(r["variant_id"]): r["max_eff"] for r in cur.fetchall()
    }

    cur.execute(
        f"""
        SELECT variant_id, MAX(effective_at) AS max_eff
        FROM market_grader_population_observation
        WHERE variant_id IN ({ph}) AND source_code='gemrate'
        GROUP BY variant_id
        """,
        vids,
    )
    pop = {int(r["variant_id"]): r["max_eff"] for r in cur.fetchall()}

    member_by_variant = {
        int(member["variant_id"]): dict(member) for member in u.get("members", [])
    }
    rows = []
    for vid, meta in base.items():
        lang = str(meta.get("card_language") or "").lower()
        b = binds.get(vid) or {}
        s = sales.get(vid) or {}
        p = prices.get(vid) or {}
        snk_sale_max = None
        for sc in ("snkrdunk", "snk", "snk_psa10"):
            if sc in s and s[sc]["max"] is not None:
                if snk_sale_max is None or s[sc]["max"] > snk_sale_max:
                    snk_sale_max = s[sc]["max"]
        ebay_sale_max = None
        for sc in LIVE_EBAY_SOLD_SOURCE_CODES:
            hit = (s.get(sc) or {}).get("max")
            if hit is not None and (ebay_sale_max is None or hit > ebay_sale_max):
                ebay_sale_max = hit
        snk_price_max = None
        for sc in ("snk_psa10", "snkrdunk", "snk"):
            if sc in p and p[sc]["max"] is not None:
                if snk_price_max is None or p[sc]["max"] > snk_price_max:
                    snk_price_max = p[sc]["max"]
        en_price_max = None
        for sc in LIVE_EBAY_SOLD_SOURCE_CODES:
            if sc in p and p[sc]["max"] is not None:
                if en_price_max is None or p[sc]["max"] > en_price_max:
                    en_price_max = p[sc]["max"]

        row = {
            "variantId": vid,
            "opaqueId": meta.get("opaque_id"),
            "lang": lang,
            "name": meta.get("canonical_name"),
            "collector": meta.get("collector_number"),
            "ids": {
                "snkrdunk": b.get("snkrdunk") or b.get("snk"),
                "snkrdunkStrict": strict_snk_by_variant.get(vid),
                "snkrdunkEn": (snk_en_identity.get(vid) or {}).get("externalId"),
                "pricecharting": b.get("pricecharting"),
                "ebay": b.get("ebay"),
                "gemrate": b.get("gemrate"),
            },
            "snkEnIdentityEvidenceSha256": (
                snk_en_identity.get(vid) or {}
            ).get("evidenceSha256"),
            "snkEnAccepted": snk_en_accepted.get(vid),
            "activeRank": (member_by_variant.get(vid) or {}).get("market_rank"),
            "activeRole": (member_by_variant.get(vid) or {}).get("member_role"),
            "activeSegment": (member_by_variant.get(vid) or {}).get("segment_code"),
            "sales": {
                "snkMax": snk_sale_max.isoformat(sep=" ") if hasattr(snk_sale_max, "isoformat") else snk_sale_max,
                "ebayMax": ebay_sale_max.isoformat(sep=" ") if hasattr(ebay_sale_max, "isoformat") else ebay_sale_max,
                "snkAny": snk_sale_max is not None,
                "ebayAny": ebay_sale_max is not None,
            },
            "prices": {
                "snkMax": snk_price_max.isoformat(sep=" ") if hasattr(snk_price_max, "isoformat") else snk_price_max,
                "enMax": en_price_max.isoformat(sep=" ") if hasattr(en_price_max, "isoformat") else en_price_max,
                "snkAny": snk_price_max is not None,
                "enAny": en_price_max is not None,
                "enExplicitPc": explicit_pc_prices.get(vid) is not None,
            },
            "popMax": pop.get(vid).isoformat(sep=" ") if hasattr(pop.get(vid), "isoformat") else pop.get(vid),
            "_snkSaleMax": snk_sale_max,
            "_ebaySaleMax": ebay_sale_max,
            "_snkPriceMax": snk_price_max,
            "_enPriceMax": en_price_max,
            "_enExplicitPcMax": explicit_pc_prices.get(vid),
            "_popMax": pop.get(vid),
        }
        rows.append(row)
    rows.sort(
        key=lambda row: (
            int(row.get("activeRank") or 4294967295),
            int(row["variantId"]),
        )
    )
    return rows


def classify_needs(
    row: dict[str, Any],
    checkpoints: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return adapter need rows for registry."""
    checkpoints = checkpoints or {}
    needs = []
    lang = row["lang"]
    ids = row["ids"]
    vid = int(row["variantId"])

    # gemrate pop
    if ids.get("gemrate"):
        mode = _poll_mode(
            has_stock=row.get("_popMax") is not None,
            observed_at=row.get("_popMax"),
            checkpoint=_checkpoint_for(checkpoints, "gemrate_pop", vid, ids["gemrate"]),
        )
        needs.append({"adapter": "gemrate_pop", "modeNeeded": mode, "externalId": ids["gemrate"], "transport": "http_curl", "polarRole": "pop"})

    # SNK is an exact market authority whenever the card has an exact SNK ID;
    # card language does not invalidate that binding (the active cohort includes
    # English-language Pokemon and One Piece cards traded on SNK).
    if ids.get("snkrdunk"):
        mode = _poll_mode(
            has_stock=bool(row["sales"]["snkAny"]),
            observed_at=row.get("_snkSaleMax"),
            checkpoint=_checkpoint_for(checkpoints, "snk_trades", vid, ids["snkrdunk"]),
            empty_poll_is_complete=True,
        )
        needs.append({
            "adapter": "snk_trades",
            "modeNeeded": mode,
            "externalId": ids["snkrdunk"],
            "transport": "http_curl",
            "polarRole": "ja_sales_primary" if lang == "ja" else "cross_market_sales",
        })
        # Only the strict pair is pollable for price: the kline ingest resolves
        # identity through operator_strict_source_identity and skips anything
        # else, and one such skip fails the whole adapter contract. Cards held
        # back here are counted in status as snkPriceIdentityNotStrict, never
        # dropped quietly.
        if ids.get("snkrdunkStrict"):
            pmode = _poll_mode(
                has_stock=bool(row["prices"]["snkAny"]),
                observed_at=row.get("_snkPriceMax"),
                checkpoint=_checkpoint_for(
                    checkpoints, "snk_price", vid, ids["snkrdunkStrict"]
                ),
                empty_poll_is_complete=True,
            )
            needs.append({
                "adapter": "snk_price",
                "modeNeeded": pmode,
                "externalId": ids["snkrdunkStrict"],
                "transport": "http_curl",
                "polarRole": "ja_price_primary" if lang == "ja" else "cross_market_price",
            })
    elif lang != "en":
        needs.append({"adapter": "bind_snk", "modeNeeded": "bind", "externalId": None, "transport": None, "polarRole": "ja_identity"})

    # SNK EN default-image authority is independent of card language.  It is
    # admitted only from the exact `snkrdunk` catalog binding; legacy `snk`
    # aliases and generic CDN pointers are deliberately ineligible.
    snk_en_external = str(ids.get("snkrdunkEn") or "").strip()
    if snk_en_external:
        accepted = row.get("snkEnAccepted") or {}
        has_stock = str(accepted.get("externalId") or "") == snk_en_external
        mode = _poll_mode(
            has_stock=has_stock,
            observed_at=accepted.get("observedAt") if has_stock else None,
            checkpoint=_checkpoint_for(
                checkpoints,
                "snk_en_image",
                vid,
                snk_en_external,
            ),
        )
        needs.append(
            {
                "adapter": "snk_en_image",
                "modeNeeded": mode,
                "externalId": snk_en_external,
                "identityEvidenceSha256": row.get(
                    "snkEnIdentityEvidenceSha256"
                ),
                "transport": "http_requests_no_browser",
                "polarRole": "canonical_en_default_image",
            }
        )

    # PriceCharting 同 SNK 係並行源，唔係二揀一。有 exact SNK 唔等於唔使 PC。
    # 2026-08-20：舊閘「有 SNK 就唔 bind PC」令 477 張（已入 universe、有 SNK、
    # 冇 PC id）永遠唔入 bind／9333，今日 cap 停喺 1127/1604。
    # 冇 exact 數字 PC product id → 仍然要 bind；有就 poll。兩者可以同卡並行。
    pc_external = str(ids.get("pricecharting") or "").strip()
    pc_exact_product = pc_external if pc_external.isdigit() else None
    if not pc_exact_product:
        needs.append({"adapter": "bind_pc_or_ebay", "modeNeeded": "bind", "externalId": None, "transport": "cdp_9333", "polarRole": "pc_identity"})
    if pc_exact_product:
        external = pc_exact_product
        smode = _poll_mode(
            has_stock=bool(row["sales"]["ebayAny"]),
            observed_at=row.get("_ebaySaleMax"),
            checkpoint=_checkpoint_for(checkpoints, "pc_ebay_sales", vid, external),
            empty_poll_is_complete=True,
            refresh_due_hours=PC_REFRESH_DUE_HOURS,
        )
        needs.append({
            "adapter": "pc_ebay_sales",
            "modeNeeded": smode,
            "externalId": external,
            "transport": "cdp_9333",
            "polarRole": "en_sales_primary",
        })
        # 2026-09-25: 477 streams had an en_price_ref checkpoint (08-20/08-27)
        # but no current explicit PC price row left, so they sat in 'stock'
        # for a month: the daily run only takes incr and checkpoint-repair
        # only takes streams with no checkpoint. A checkpoint means the first
        # stock already ran; re-derive it daily like the other adapters.
        pmode = _poll_mode(
            has_stock=bool(row["prices"]["enExplicitPc"]),
            observed_at=row.get("_enExplicitPcMax"),
            checkpoint=_checkpoint_for(checkpoints, "en_price_ref", vid, external),
            empty_poll_is_complete=True,
            refresh_due_hours=PC_REFRESH_DUE_HOURS,
        )
        needs.append({
            "adapter": "en_price_ref",
            "modeNeeded": pmode,
            "externalId": external,
            "transport": "cdp_9333",
            "polarRole": "en_price_fallback",
        })
    return needs


def build_registry(
    rows: list[dict[str, Any]],
    checkpoints: dict[tuple[str, str], dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    reg = []
    for row in rows:
        for need in classify_needs(row, checkpoints):
            reg.append({
                "variantId": row["variantId"],
                "opaqueId": row["opaqueId"],
                "lang": row["lang"],
                "name": row["name"],
                **need,
                "builtAt": utc_now(),
            })
    return reg


def write_registry(reg: list[dict[str, Any]]) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with REGISTRY_PATH.open("w", encoding="utf-8") as f:
        for item in reg:
            f.write(json.dumps(item, ensure_ascii=False, default=str) + "\n")
    return REGISTRY_PATH


# Daily-facing freshness clocks only.
# G10 `source_code='ebay'` last wrote 2026-08-04 and is NOT the 9333
# PriceCharting eBay-sold lane (C11 writes those as source_code='pricecharting').
# Naming that archive `ebay_sales` made operators report 9333 dead. Do not add it back.
# pc_sales SLA clock is fetched_at (poll time). sold_at is listing day at 00:00
# and will go red overnight even when Chrome just finished a clean sweep.
DAILY_FRESHNESS_STREAMS: tuple[tuple[str, str], ...] = (
    (
        "snk_sales",
        "SELECT MAX(sold_at) m, COUNT(*) n FROM market_sale_observation "
        "WHERE source_code IN ('snkrdunk','snk','snk_psa10')",
    ),
    (
        "snk_price",
        "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation "
        "WHERE source_code IN ('snk_psa10','snkrdunk','snk')",
    ),
    (
        "pc_sales",
        "SELECT MAX(fetched_at) m, COUNT(*) n FROM market_sale_observation "
        "WHERE source_code='pricecharting'",
    ),
    (
        "pc_price",
        "SELECT MAX(effective_at) m, COUNT(*) n FROM market_price_observation "
        "WHERE source_code='pricecharting'",
    ),
    (
        "gemrate_pop",
        "SELECT MAX(effective_at) m, COUNT(*) n FROM market_grader_population_observation "
        "WHERE source_code='gemrate'",
    ),
)
PC_SALES_LISTING_SQL = (
    "SELECT MAX(sold_at) m FROM market_sale_observation WHERE source_code='pricecharting'"
)


def freshness_summary(
    cur, registry: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    out = {"asOf": utc_now(), "slaHours": SLA_HOURS, "streams": {}, "polls": {}}
    queries = list(DAILY_FRESHNESS_STREAMS)
    if _migration_029_ready(cur):
        queries.append(
            (
                "snk_en_image",
                "SELECT MAX(canonical_image_source_observed_at) m, COUNT(*) n "
                "FROM operator_canonical_image_projection",
            )
        )
    for name, sql in queries:
        cur.execute(sql)
        r = cur.fetchone()
        m = r["m"] if isinstance(r, dict) else r[0]
        n = int((r["n"] if isinstance(r, dict) else r[1]) or 0)
        age = _age_hours(m)
        # A negative age means the newest row is stamped in the FUTURE, which is
        # not freshness -- it is a stamping defect. SNK daily bars carry
        # effective_at = observed_date 23:59:59 (snk_market_data.py:1303), so
        # this stream reads -19.33 h right now and used to report slaOk=1. A
        # dead feed can hide behind that for a whole extra day. Say what is true:
        # ok requires the stamp to be in the past AND inside the SLA.
        future = age is not None and age < 0
        entry = {
            "maxAt": m.isoformat(sep=" ") if hasattr(m, "isoformat") else m,
            "rows": n,
            "ageHours": None if age is None else round(age, 2),
            "futureStamped": future,
            "slaOk": age is not None and 0 <= age <= SLA_HOURS,
        }
        if name == "pc_sales":
            entry["clock"] = "fetched_at"
            entry["lane"] = "pricecharting_c11_ebay_sold"
        out["streams"][name] = entry
    if "pc_sales" in out["streams"]:
        cur.execute(PC_SALES_LISTING_SQL)
        listing = cur.fetchone()
        listing_max = listing["m"] if isinstance(listing, dict) else listing[0]
        out["streams"]["pc_sales"]["listingMaxAt"] = (
            listing_max.isoformat(sep=" ") if hasattr(listing_max, "isoformat") else listing_max
        )
    checkpoints = load_checkpoints(cur)
    for adapter in CHECKPOINT_ADAPTERS:
        expected = [
            item
            for item in (registry or [])
            if item.get("adapter") == adapter and item.get("externalId") is not None
        ]
        active = [
            _checkpoint_for(
                checkpoints,
                adapter,
                int(item["variantId"]),
                item.get("externalId"),
            )
            for item in expected
        ]
        active = [row for row in active if row is not None]
        if registry is None:
            active = [row for (source, _), row in checkpoints.items() if source == adapter]
            expected_count = len(active)
        else:
            expected_count = len(expected)
        times = [_parse_datetime(row.get("last_effective_at")) for row in active]
        times = [value for value in times if value is not None]
        oldest = min(times) if times else None
        newest = max(times) if times else None
        oldest_age = _age_hours(_parse_datetime(oldest))
        out["polls"][adapter] = {
            "expectedStreams": expected_count,
            "checkpointedStreams": len(active),
            "missingStreams": max(0, expected_count - len(active)),
            "oldestSuccessAt": oldest.isoformat(sep=" ") if hasattr(oldest, "isoformat") else oldest,
            "newestSuccessAt": newest.isoformat(sep=" ") if hasattr(newest, "isoformat") else newest,
            "oldestAgeHours": None if oldest_age is None else round(oldest_age, 2),
            "slaOk": (
                expected_count > 0
                and len(active) == expected_count
                and oldest_age is not None
                and oldest_age <= SLA_HOURS
            ),
        }
    return out


def cmd_prune_checkpoints(*, apply: bool) -> dict[str, Any]:
    """刪走對唔返任何 active exact stream 嘅 checkpoint 行。

    卡一旦解綁／換咗 external id／跌出 active universe，佢原本嗰條
    `market_ingest_checkpoint` 行仲會留喺度，永遠唔會再有人寫。呢啲行會令
    `SELECT COUNT(*)` 同 lane 實際掃嘅張數對唔上（實測 1,077 vs 993），查 lane
    覆蓋率嗰陣要人手扣返，係一個永遠會再中伏嘅落差。expected 由同一個
    `build_registry` 出，唔會有第二套判斷。
    """
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        rows = load_universe_rows(cur)
        checkpoints = load_checkpoints(cur)
        reg = build_registry(rows, checkpoints)
        expected: dict[str, set[str]] = {adapter: set() for adapter in CHECKPOINT_ADAPTERS}
        for item in reg:
            adapter = str(item.get("adapter") or "")
            if adapter in expected and item.get("externalId") is not None:
                expected[adapter].add(_stream_key(int(item["variantId"]), item.get("externalId")))
        orphans = [
            dict(row)
            for (source, stream_key), row in sorted(checkpoints.items())
            if source in expected and stream_key not in expected[source]
        ]
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact = OUT_DIR / f"checkpoint_orphans_{stamp}.jsonl"
        with artifact.open("w", encoding="utf-8") as handle:
            for row in orphans:
                handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        deleted = 0
        if apply and orphans:
            for row in orphans:
                cur.execute(
                    "DELETE FROM market_ingest_checkpoint WHERE source_code=%s AND stream_key=%s",
                    (row["source_code"], row["stream_key"]),
                )
                deleted += cur.rowcount
            conn.commit()
        by_adapter: dict[str, int] = {}
        for row in orphans:
            key = str(row["source_code"])
            by_adapter[key] = by_adapter.get(key, 0) + 1
        report = {
            "action": "prune-checkpoints",
            "asOf": utc_now(),
            "applied": bool(apply),
            "expectedStreams": {adapter: len(keys) for adapter, keys in expected.items()},
            "checkpointRows": {
                adapter: sum(1 for (source, _) in checkpoints if source == adapter)
                for adapter in CHECKPOINT_ADAPTERS
            },
            "orphans": len(orphans),
            "orphansByAdapter": by_adapter,
            "deleted": deleted,
            "artifact": str(artifact),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return report
    finally:
        conn.close()


def cmd_status(*, rebuild_registry: bool = True) -> dict[str, Any]:
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        rows = load_universe_rows(cur)
        checkpoints = load_checkpoints(cur)
        reg = build_registry(rows, checkpoints) if rebuild_registry else []
        if rebuild_registry:
            write_registry(reg)
        counts = {
            "universe": len(rows),
            "registryRows": len(reg),
            "byAdapterMode": {},
            "stockDue": 0,
            "incrDue": 0,
            "bindDue": 0,
            "ok": 0,
        }
        for item in reg:
            key = f"{item['adapter']}:{item['modeNeeded']}"
            counts["byAdapterMode"][key] = counts["byAdapterMode"].get(key, 0) + 1
            mode = item["modeNeeded"]
            if mode == "stock":
                counts["stockDue"] += 1
            elif mode == "incr":
                counts["incrDue"] += 1
            elif mode == "bind":
                counts["bindDue"] += 1
            elif mode == "ok":
                counts["ok"] += 1
        # A card with an exact SNK binding whose pair is not in the strict view
        # cannot be kline-ingested, so it is not in the poll list above. Say so
        # here: a gap that only shows up as an absence is a gap nobody reads.
        not_strict = sorted(
            int(row["variantId"])
            for row in rows
            if (row.get("ids") or {}).get("snkrdunk")
            and not (row.get("ids") or {}).get("snkrdunkStrict")
        )
        counts["snkPriceIdentityNotStrict"] = len(not_strict)
        fresh = freshness_summary(cur, reg)
        report = {
            "action": "status",
            "asOf": utc_now(),
            "counts": counts,
            "freshness": fresh,
            "registryPath": str(REGISTRY_PATH) if rebuild_registry else None,
            "snkPriceIdentityNotStrict": not_strict,
            "notes": [
                "stockDue = residual full pulls",
                "incrDue = stale beyond SLA",
                "bindDue = missing identity before collect",
                "Polaris display is separate (operator_control.latest_prices)",
            ],
        }
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        LAST_STATUS.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return report
    finally:
        conn.close()


def _due(reg: list[dict[str, Any]], adapter: str, modes: set[str]) -> list[dict[str, Any]]:
    return [r for r in reg if r.get("adapter") == adapter and r.get("modeNeeded") in modes]


def _snk_ids(items: list[dict[str, Any]], limit: int | None) -> list[int]:
    ids = []
    seen = set()
    for it in items:
        raw = it.get("externalId")
        if raw is None:
            continue
        try:
            n = int(str(raw).strip())
        except ValueError:
            continue
        if n in seen:
            continue
        seen.add(n)
        ids.append(n)
        if limit is not None and len(ids) >= limit:
            break
    return ids


def _ingest_gemrate_manifest(
    manifest: dict[str, Any],
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    resolved = list(manifest.get("resolved") or [])
    item_by_external = {str(item.get("externalId") or ""): item for item in items}
    resolved = [row for row in resolved if str(row.get("gemrateId") or "") in item_by_external]
    if len(resolved) != len(item_by_external):
        missing = sorted(set(item_by_external) - {str(row.get("gemrateId") or "") for row in resolved})
        raise RuntimeError(f"GemRate manifest is incomplete for {len(missing)} active IDs")

    fetched_at = _parse_datetime(manifest.get("fetchedAt")) or datetime.now(timezone.utc)
    fetched_naive = fetched_at.astimezone(timezone.utc).replace(tzinfo=None)
    manifest_sha = _sha256(manifest)
    run_key = _sha256({"source": "gemrate", "manifestSha256": manifest_sha})
    conn = db()
    payload_sha_by_external: dict[str, str] = {}
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256,
                 manifest_sha256, status, observed_count, accepted_count,
                 quarantined_count, rejected_count, started_at, completed_at)
            VALUES (%s,'gemrate','incremental',%s,%s,%s,'completed',%s,%s,0,0,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), status='completed',
                observed_count=VALUES(observed_count), accepted_count=VALUES(accepted_count),
                completed_at=VALUES(completed_at)
            """,
            (
                run_key,
                fetched_naive,
                manifest_sha,
                manifest_sha,
                len(resolved),
                len(resolved),
                fetched_naive,
                fetched_naive,
            ),
        )
        run_id = int(cur.lastrowid)
        for row in resolved:
            external = str(row["gemrateId"])
            item = item_by_external[external]
            effective_date = str(row["effectiveDate"])[:10]
            effective_at = f"{effective_date} 00:00:00"
            population = int(row["populationPsa10"])
            payload = {
                "grader": "PSA",
                "grade": "10",
                "population": population,
                "transport": row.get("transport"),
                "effectiveDateSource": row.get("effectiveDateSource"),
                "fetchedAt": manifest.get("fetchedAt"),
            }
            payload_sha = _sha256(payload)
            payload_sha_by_external[external] = payload_sha
            cur.execute(
                """
                INSERT INTO market_source_observation
                    (run_id, source_code, external_entity_id, observation_kind,
                     effective_at, observed_date, payload_sha256, payload_json, observed_at)
                VALUES (%s,'gemrate',%s,'grader_population_psa10',%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE run_id=VALUES(run_id), observed_at=VALUES(observed_at)
                """,
                (
                    run_id,
                    external,
                    effective_at,
                    effective_date,
                    payload_sha,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    fetched_naive,
                ),
            )
            cur.execute(
                """
                INSERT INTO market_grader_population_observation
                    (run_id, variant_id, source_code, external_entity_id, grader_code,
                     top_grade_label, total_population, top_grade_population, estimated,
                     effective_at, observed_date, payload_sha256)
                VALUES (%s,%s,'gemrate',%s,'PSA','10',NULL,%s,0,%s,%s,%s)
                -- top_grade_label and estimated sit outside uq_market_grader_population
                -- (variant_id, grader_code, source_code, observed_date), so a row an
                -- undecomposed 'top' lane landed first for the same day keeps that label
                -- unless we restate it here -- and the population acceptance lane reads
                -- the label, not the number. Restating is what makes this row mean PSA 10.
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id), external_entity_id=VALUES(external_entity_id),
                    top_grade_label=VALUES(top_grade_label), estimated=VALUES(estimated),
                    top_grade_population=GREATEST(top_grade_population,VALUES(top_grade_population)),
                    effective_at=VALUES(effective_at), payload_sha256=VALUES(payload_sha256)
                """,
                (
                    run_id,
                    int(item["variantId"]),
                    external,
                    population,
                    effective_at,
                    effective_date,
                    payload_sha,
                ),
            )
        checkpointed = _upsert_checkpoints(
            cur,
            adapter="gemrate_pop",
            items=items,
            run_id=run_id,
            completed_at=fetched_naive,
            payload_sha_by_external=payload_sha_by_external,
        )
        conn.commit()
        return {
            "runId": run_id,
            "inserted": len(resolved),
            "checkpointed": checkpointed,
            "payloadShaByExternal": payload_sha_by_external,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def gemrate_child_timeout_seconds(selected_count: int) -> int:
    """Kill timer for gemrate_source.py daily.

    Must NOT scale with universe size. ``10 * len(selected)`` on 1604 IDs
    was 16040s — longer than the 4h Task Scheduler limit — so 2026-08-20's
    morning chain died in GemRate and never reached PC/publish.
    ``selected_count`` is accepted so call sites stay honest; it must not
    increase the timeout.
    """
    del selected_count
    return min(
        max(2 * GEMRATE_WEBSITE_BUDGET_SECONDS, 900),
        GEMRATE_CHILD_TIMEOUT_MAX_SECONDS,
    )


def pc_cdp_child_timeout_seconds(selected_count: int) -> int:
    """Kill timer for pc_cdp_sold_refresh_win.py.

    ``45 * len(selected)`` on 1028 IDs was 46260s. A wedged CDP tab then
    looked alive for 12.8 hours while V2 heartbeated every 10s. Cap at 90
    minutes; ``selected_count`` stays on the call site so it cannot silently
    start scaling the timeout again.
    """
    del selected_count
    return PC_CDP_CHILD_TIMEOUT_MAX_SECONDS


def _gemrate_reuse_window_start(business_date: str) -> datetime | None:
    """audit P2-12: anchor manifest reuse to the run, not to JST midnight.

    Same midnight-bug family as the FX freshness floor. A manual window opened
    before the business date's JST midnight fetches real data whose ``fetchedAt``
    lands on the previous JST day, so the strict ``date() == business_date``
    test threw the receipt away and re-ran 828s/shard of provider work that the
    docstring below calls "both slow and semantically wrong". The window now
    opens at the earlier of JST midnight and this run's creation, and closes at
    now; the shard-suffix and exact resolved-ID-set checks are unchanged.
    """
    try:
        midnight = datetime.fromisoformat(business_date).replace(tzinfo=JST).astimezone(timezone.utc)
    except ValueError:
        return None
    started = _parse_datetime(os.environ.get("CARDZ_V2_RUN_STARTED_AT", "").strip())
    return min(midnight, started) if started is not None else midnight


def _reusable_gemrate_manifest(
    selected: list[dict[str, Any]], safe_scope: str
) -> tuple[Path, dict[str, Any]] | None:
    """Resume V2 ingest from today's exact immutable shard receipt.

    Fetch and ingest are separate durability boundaries.  If MySQL rejects the
    short ingest transaction, retrying the provider network work is both slow
    and semantically wrong: the completed manifest is already the source
    receipt.  Reuse is deliberately strict to the same JST business date,
    shard suffix and exact resolved external-ID set.
    """

    business_date = os.environ.get("CARDZ_V2_BUSINESS_DATE", "").strip()
    if not (
        os.environ.get("CARDZ_DAILY_CHAIN_V2") == "1"
        and safe_scope
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", business_date)
    ):
        return None
    window_start = _gemrate_reuse_window_start(business_date)
    if window_start is None:
        return None
    window_end = datetime.now(timezone.utc)
    expected = {str(item.get("externalId") or "") for item in selected}
    pattern = f"daily_*_{safe_scope}/manifest.json"
    for path in sorted(
        (ROOT / "data/private/gemrate/runs").glob(pattern),
        key=lambda candidate: candidate.stat().st_mtime_ns,
        reverse=True,
    ):
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = _parse_datetime(manifest.get("fetchedAt"))
            resolved = {
                str(row.get("gemrateId") or "")
                for row in (manifest.get("resolved") or [])
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
        if (
            fetched_at is not None
            and window_start <= fetched_at <= window_end
            and resolved == expected
            and not manifest.get("mismatches")
            and bool(manifest.get("promoted"))
            and bool(manifest.get("promotable"))
        ):
            return path, manifest
    return None


def _gemrate_transport_outage(manifest: Mapping[str, Any], selected_count: int) -> bool:
    """audit P1-7: tell a lane failure apart from N independent item failures.

    A transport that attempted the whole cohort and resolved nothing is one
    dead browser session, not 399 bad GemRate IDs.  The manifest already
    carries the discriminator, so read it instead of pushing a quarantine
    streak onto every card that a dead session never even requested.
    """
    transports = manifest.get("transports")
    if not isinstance(transports, Mapping) or selected_count <= 0:
        return False
    attempted_any = False
    for counts in transports.values():
        if not isinstance(counts, Mapping):
            return False
        attempted = int(counts.get("attempted") or 0)
        if attempted <= 0:
            continue
        attempted_any = True
        if int(counts.get("succeeded") or 0) > 0 or attempted != selected_count:
            return False
    return attempted_any


def _gemrate_failure_is_transport(reason: str) -> bool:
    """audit P1-7 / item 10: is this reason about the pipe, not about the card?"""
    text = str(reason or "")
    return text in GEMRATE_TRANSPORT_FAILURE_REASONS or text.startswith(
        GEMRATE_TRANSPORT_FAILURE_PREFIXES
    )


def run_gemrate_pop(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    work_scope: str | None = None,
    gemrate_workers: int = 1,
) -> dict[str, Any]:
    # audit P1-5 + trap 12: workers is payload-driven, but 4 shards x N workers
    # is 4N Chromes on this host. Fail closed above the measured ceiling.
    workers = max(1, int(gemrate_workers or 1))
    if workers > GEMRATE_MAX_WORKERS:
        raise RuntimeError(
            f"gemrate workers={workers} exceeds GEMRATE_MAX_WORKERS="
            f"{GEMRATE_MAX_WORKERS}; {workers} workers x 4 shards is "
            f"{workers * 4} Chromes on this host"
        )
    active, quarantined = _partition_quarantined("gemrate_pop", items)
    selected = _unique_items(active, limit)
    report: dict[str, Any] = {
        "adapter": "gemrate_pop",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "quarantined": len(quarantined),
        "quarantinedItems": quarantined,
        "failed": 0,
        "failedItems": [],
        # audit P1-5: the operator has to be able to see what this shard
        # actually ran with before anyone argues about raising it.
        "gemrateWorkers": workers,
        "ok": True,
    }
    if not selected:
        report["note"] = (
            "all due GemRate IDs are quarantined" if quarantined else "no exact GemRate IDs due"
        )
        return report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_scope = re.sub(r"[^a-z0-9-]+", "-", str(work_scope or "").lower()).strip("-")
    ids_path = OUT_DIR / (
        f"gemrate_ids_{mode}_{safe_scope}.txt" if safe_scope else f"gemrate_ids_{mode}.txt"
    )
    ids = [str(item["externalId"]) for item in selected]
    ids_path.write_text("\n".join(ids) + "\n", encoding="utf-8")
    if dry_run:
        report.update({"dryRun": True, "checkpointed": 0})
        return report

    child_interrupted = False
    reusable = _reusable_gemrate_manifest(selected, safe_scope)
    if reusable is not None:
        manifest_path, manifest = reusable
        exit_code = 0
        report["run"] = {
            "exit": 0,
            "reusedManifest": True,
            "manifest": str(manifest_path),
            "networkRequests": 0,
        }
    else:
        before = set((ROOT / "data/private/gemrate/runs").glob("daily_*/manifest.json"))
        command = [
            PY,
            "-X",
            "utf8",
            "pipelines/gemrate_source.py",
            "daily",
            "--ids-file",
            str(ids_path),
            "--speed",
            "fast",
            "--website-budget-seconds",
            str(GEMRATE_WEBSITE_BUDGET_SECONDS),
            "--workers",
            str(workers),
        ]
        if safe_scope:
            command.extend(["--run-suffix", safe_scope])
            if not safe_scope.startswith("0-of-"):
                command.append("--skip-grader-volume")
        # The kill timer must leave headroom above the child's website budget
        # (5400s): the child also runs the direct-API leg, the mirror pass and two
        # grader-volume browser launches. Aliasing the two guaranteed a SIGKILL
        # exactly when the website budget was actually needed.
        # MUST NOT scale with universe size: ``10 * len(selected)`` on 1604 IDs was
        # 16040s, longer than the 4h Task Scheduler limit, so the morning chain
        # was killed before PC/publish (2026-08-20).
        # audit item 10: the orchestrator SIGTERMs the whole worker group at
        # the tick deadline. Dying here threw away every card the child had
        # already fetched (2026-08-24: 604 cards in 20 min, then 0 ingested,
        # then a second attempt that started again from zero). Hold the signal
        # while the child finishes its declared-partial manifest, ingest what
        # it got, and only then let the lane fail.
        with _deferred_termination() as interrupted:
            report["run"] = _run(
                command, timeout=gemrate_child_timeout_seconds(len(selected)), dry_run=False
            )
        child_interrupted = interrupted["signalled"]
        # Exit 1 is the child's declared-partial signal (some IDs unresolved); the
        # manifest still carries every resolved row, so per-item ingest continues.
        exit_code = report["run"].get("exit")
        if exit_code not in (0, 1):
            report.update({"ok": False, "error": "gemrate_daily_failed", "checkpointed": 0})
            return report
        if child_interrupted:
            report["childInterrupted"] = True
        after = set((ROOT / "data/private/gemrate/runs").glob("daily_*/manifest.json"))
        candidates = sorted(after - before, key=lambda path: path.stat().st_mtime, reverse=True)
        if safe_scope:
            candidates = [
                path for path in candidates
                if path.parent.name.endswith(f"_{safe_scope}")
            ]
        if not candidates and not safe_scope:
            candidates = sorted(after, key=lambda path: path.stat().st_mtime, reverse=True)
        if not candidates:
            report.update({"ok": False, "error": "gemrate_manifest_missing", "checkpointed": 0})
            return report
        manifest_path = candidates[0]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("mismatches"):
        # Direct/mirror disagreement is a source-integrity failure, never a
        # per-item one; nothing may advance from a mismatched manifest.
        report.update({"ok": False, "error": "gemrate_manifest_mismatch", "manifest": str(manifest_path), "checkpointed": 0})
        return report
    if exit_code != 0 and not manifest.get("partial"):
        # A non-zero exit without a declared partial manifest is an infra
        # failure; per-item streaks must not advance on it.
        report.update({"ok": False, "error": "gemrate_daily_failed", "manifest": str(manifest_path), "checkpointed": 0})
        return report
    if exit_code == 0 and not (manifest.get("promoted") and manifest.get("promotable")):
        report.update({"ok": False, "error": "gemrate_manifest_not_promotable", "manifest": str(manifest_path), "checkpointed": 0})
        return report

    # Per-item split: only manifest-resolved IDs advance; unresolved IDs fail
    # individually and feed the quarantine streak instead of the whole lane.
    resolved_ids = {str(row.get("gemrateId") or "") for row in (manifest.get("resolved") or [])}
    identity_failed = {
        str(row.get("gemrateId") or ""): str(row.get("reason") or "identity_receipt_failed")
        for row in (manifest.get("directIdentityReceiptFailures") or [])
    }
    unresolved_reasons = {
        str(row.get("gemrateId") or ""): str(row.get("reason") or "unresolved")
        for row in (manifest.get("unresolved") or [])
    }
    ok_items: list[dict[str, Any]] = []
    failed_items: list[dict[str, Any]] = []
    for item in selected:
        external = str(item.get("externalId") or "")
        if external in resolved_ids and external not in identity_failed:
            ok_items.append(item)
            continue
        failed_items.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": external,
                "error": identity_failed.get(external)
                or unresolved_reasons.get(external)
                or "gemrate_manifest_missing_id",
            }
        )
    blocked = manifest.get("blocked") if failed_items else None
    blocked = dict(blocked) if isinstance(blocked, Mapping) else None
    if blocked is None and _gemrate_transport_outage(manifest, len(selected)):
        # audit P1-7: every attempted transport resolved nothing for the whole
        # cohort -- one dead browser session, not len(selected) bad IDs. Report
        # the lane failure and advance NO per-item streak; the core contract
        # barrier still holds publish, which is what actually protects the data.
        report.update({
            "ok": False,
            "error": "gemrate_transport_unavailable",
            "manifest": str(manifest_path),
            "checkpointed": 0,
            "failed": len(failed_items),
            "failedItems": failed_items,
        })
        return report
    # audit P1-7 / item 10: a budget-exhausted, interrupted or dead-browser card
    # is a transport verdict, not a verdict about that GemRate ID.
    streak_items = [
        row for row in failed_items
        if not _gemrate_failure_is_transport(str(row.get("error") or ""))
    ]
    report["quarantineStreaksSkipped"] = len(failed_items) - len(streak_items)
    if streak_items:
        # Per-item source failures advance the quarantine streak immediately so
        # they persist even if the ingest step below fails for other reasons.
        _record_item_outcomes("gemrate_pop", succeeded=[], failed=streak_items)
    write: dict[str, Any] = {"runId": None, "inserted": 0, "checkpointed": 0}
    if ok_items:
        try:
            with _db_writer_lease():
                write = _ingest_gemrate_manifest(manifest, ok_items)
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"gemrate_ingest:{type(exc).__name__}:{exc}", "checkpointed": 0})
            return report
        payload_sha_by_external = write.pop("payloadShaByExternal", None) or {}
        _persist_item_checkpoints(
            "gemrate_pop",
            mode=mode,
            items=ok_items,
            payload_sha_by_external=payload_sha_by_external,
            run_id=write.get("runId"),
        )
        _record_item_outcomes("gemrate_pop", succeeded=ok_items, failed=[])
    report.update({"manifest": str(manifest_path), **write})
    report["failed"] = len(failed_items)
    report["failedItems"] = failed_items
    # audit P1-5: the 429 ceiling that --workers 1 was protecting has never
    # fired in measurement. Publish the count so raising workers is a decision
    # backed by this shard's own receipt instead of by an assumption.
    report["rateLimited429"] = sum(
        1
        for reason in (manifest.get("websiteFailureReceipts") or {}).values()
        if str(reason) == "rate_limited_429_exhausted"
    )
    if blocked is not None:
        # The cards above are ingested; the rest met Cloudflare's block page.
        # Retrying today only knocks again, so the lane says so once and the
        # V2 contract falls back to each card's last pop inside
        # POP_BLOCKED_FALLBACK_DAYS (daddy 2026-09-26: 3 days, then block).
        report.update({"ok": False, "error": GEMRATE_BLOCKED_ERROR, "blocked": blocked})
    elif child_interrupted or manifest.get("interrupted"):
        # audit item 10: the cards above are ingested and checkpointed, but an
        # interrupted run is never a clean verdict for the rest of the cohort.
        report.update({
            "ok": False,
            "error": "gemrate_child_interrupted",
            # R3: the child may have declared the interruption in its manifest
            # without this process ever seeing the signal.
            "childInterrupted": True,
        })
    elif failed_items:
        report.update({"ok": False, "error": "gemrate_partial_items_failed"})
    return report


def _snk_selection(
    items: list[dict[str, Any]], limit: int | None
) -> tuple[list[dict[str, Any]], list[int]]:
    selected = _unique_items(items, limit)
    ids: list[int] = []
    seen: set[int] = set()
    for item in selected:
        try:
            external_id = int(str(item.get("externalId") or "").strip())
        except ValueError as exc:
            raise RuntimeError(f"invalid SNK external ID for variant {item['variantId']}") from exc
        if external_id in seen:
            raise RuntimeError(f"duplicate SNK external ID in active cohort: {external_id}")
        seen.add(external_id)
        ids.append(external_id)
    return selected, ids


def _validate_snk_harvest(
    harvest_path: Path, requested_ids: list[int]
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    rows = _jsonl_rows(harvest_path)
    by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        item_id = row.get("item_id")
        if not isinstance(item_id, int) or item_id in by_id or row.get("error"):
            raise RuntimeError("SNK harvest contains invalid, duplicate, or error rows")
        by_id[item_id] = row
    if set(by_id) != set(requested_ids):
        missing = sorted(set(requested_ids) - set(by_id))
        extra = sorted(set(by_id) - set(requested_ids))
        raise RuntimeError(f"SNK harvest exact-ID mismatch missing={missing[:10]} extra={extra[:10]}")
    return rows, {str(item_id): _sha256(by_id[item_id]) for item_id in requested_ids}


def _partition_snk_harvest(
    harvest_path: Path, requested_ids: list[int]
) -> tuple[dict[int, dict[str, Any]], dict[int, str]]:
    """Split one harvest into per-item rows and per-item failure reasons.

    A duplicate or foreign row is still a whole-harvest contract violation; a
    per-row source error or a missing row only fails that one item.
    """
    requested = set(requested_ids)
    rows_by_id: dict[int, dict[str, Any]] = {}
    failed_by_id: dict[int, str] = {}
    for row in _jsonl_rows(harvest_path):
        item_id = row.get("item_id")
        if not isinstance(item_id, int) or item_id not in requested:
            raise RuntimeError(f"SNK harvest contains an invalid or foreign row: {item_id!r}")
        if item_id in rows_by_id or item_id in failed_by_id:
            raise RuntimeError(f"SNK harvest contains a duplicate row: {item_id}")
        error = row.get("error")
        if error:
            failed_by_id[item_id] = f"snk_harvest_row_error:{str(error)[:200]}"
        else:
            rows_by_id[item_id] = row
    for item_id in requested - set(rows_by_id) - set(failed_by_id):
        failed_by_id[item_id] = "snk_harvest_row_missing"
    return rows_by_id, failed_by_id


def _snk_harvest_once(
    ids: list[int],
    *,
    label: str,
    mode: str,
    delay: float,
    workers: int,
) -> dict[str, Any]:
    """Run the SNKRDUNK harvest child exactly once for one exact-ID list."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ids_path = OUT_DIR / f"{label}_ids_{mode}_{stamp}.txt"
    harvest_path = OUT_DIR / f"{label}_harvest_{mode}_{stamp}.jsonl"
    ids_path.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
    harvest_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/snk_market_data.py",
        "--ids-file",
        str(ids_path),
        "--out",
        str(harvest_path),
        "--delay",
        str(delay),
        "--workers",
        str(max(1, int(workers))),
        "--condition",
        "trading_card_single_psa10",
    ]
    result: dict[str, Any] = {
        "ok": True,
        "idsPath": str(ids_path),
        "harvestPath": str(harvest_path),
        "requestedIds": list(ids),
        "run": _run(harvest_cmd, timeout=max(180, 8 * len(ids)), dry_run=False),
    }
    if result["run"].get("exit") != 0 or not harvest_path.is_file():
        result.update({"ok": False, "error": "snk_harvest_failed"})
        return result
    try:
        result["rowsById"], result["failedById"] = _partition_snk_harvest(harvest_path, ids)
    except Exception as exc:  # noqa: BLE001
        result.update({"ok": False, "error": f"snk_harvest_contract:{type(exc).__name__}:{exc}"})
    return result


def _validate_snk_price_ingest(
    ingest_summary: dict[str, Any], requested_ids: list[int]
) -> dict[str, Any]:
    """Treat an exact-ID poll with no kline as a successful, checkpointable poll."""
    requested = set(requested_ids)
    accepted = {int(value) for value in (ingest_summary.get("acceptedItemIds") or [])}
    skipped = list(ingest_summary.get("skipped") or [])
    empty = {
        int(row["itemId"])
        for row in skipped
        if str(row.get("reason") or "") == "empty_kline" and row.get("itemId") is not None
    }
    other_skips = [row for row in skipped if str(row.get("reason") or "") != "empty_kline"]
    hard_skip_keys = (
        "skippedAllowlist",
        "skippedCondition",
        "skippedErrorRow",
        "skippedNoExactIdentity",
    )
    hard_skip_counts = {
        key: int(ingest_summary.get(key) or 0)
        for key in hard_skip_keys
    }
    if accepted & empty:
        raise RuntimeError("SNK price ingest accepted/empty sets overlap")
    if other_skips or any(hard_skip_counts.values()):
        raise RuntimeError(
            f"SNK price ingest contains non-empty-kline skips: "
            f"rows={len(other_skips)} counters={hard_skip_counts}"
        )
    if int(ingest_summary.get("skippedEmptyKline") or 0) != len(empty):
        raise RuntimeError("SNK price empty-kline counter does not match skipped rows")
    polled = accepted | empty
    if polled != requested:
        missing = sorted(requested - polled)
        extra = sorted(polled - requested)
        raise RuntimeError(
            f"SNK price ingest exact-ID mismatch missing={missing[:10]} extra={extra[:10]}"
        )
    return {
        "accepted": len(accepted),
        "emptyKline": len(empty),
        "polled": len(polled),
    }


def _run_snk_adapter(
    items: list[dict[str, Any]],
    *,
    adapter: str,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    active, quarantined = _partition_quarantined(adapter, items)
    try:
        selected, ids = _snk_selection(active, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": adapter,
            "mode": mode,
            "due": len(items),
            "processed": 0,
            "checkpointed": 0,
            "quarantined": len(quarantined),
            "quarantinedItems": quarantined,
            "ok": False,
            "error": f"snk_selection:{type(exc).__name__}:{exc}",
        }
    report: dict[str, Any] = {
        "adapter": adapter,
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "quarantined": len(quarantined),
        "quarantinedItems": quarantined,
        "failed": 0,
        "failedItems": [],
        "ok": True,
    }
    if not selected:
        report["note"] = (
            "all due SNK IDs are quarantined" if quarantined else "no exact SNK IDs due"
        )
        if adapter == "snk_price":
            # Same reason as the EN lane: quotes are minted for the whole routed
            # universe from trades already landed, not for tonight's due batch.
            try:
                with _db_writer_lease():
                    report["saleQuotes"] = _mint_sale_quotes("snkrdunk", dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                report.update({"ok": False, "error": f"sale_quotes:{type(exc).__name__}:{exc}"})
        return report
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    ids_path = OUT_DIR / f"{adapter}_ids_{mode}_{stamp}.txt"
    harvest_path = OUT_DIR / f"{adapter}_harvest_{mode}_{stamp}.jsonl"
    if dry_run:
        ids_path.write_text("\n".join(str(value) for value in ids) + "\n", encoding="ascii")
        report.update({"idsPath": str(ids_path), "harvestPath": str(harvest_path)})
        report.update({"dryRun": True, "note": "exact IDs selected; network and DB writes not executed"})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if shared_harvest is not None and set(ids) <= set(shared_harvest.get("requestedIds") or []):
        # One collect run performs one SNKRDUNK harvest; both ingest consumers
        # (trades and price) read the same immutable rows instead of refetching.
        if not shared_harvest.get("ok"):
            report.update(
                {
                    "harvest": shared_harvest.get("run"),
                    "ok": False,
                    "error": str(shared_harvest.get("error") or "snk_harvest_failed"),
                }
            )
            return report
        rows_by_id = {
            item_id: shared_harvest["rowsById"][item_id]
            for item_id in ids
            if item_id in shared_harvest["rowsById"]
        }
        failed_by_id = {
            item_id: shared_harvest["failedById"][item_id]
            for item_id in ids
            if item_id in shared_harvest["failedById"]
        }
        shared_run = shared_harvest.get("run") or {}
        report["harvest"] = {
            "exit": 0,
            "reusedSharedHarvest": True,
            "sharedHarvestPath": shared_harvest.get("harvestPath"),
            "startedAt": shared_run.get("startedAt"),
            "finishedAt": shared_run.get("finishedAt"),
        }
        shared_started = _parse_datetime(shared_run.get("startedAt"))
        if shared_started is not None:
            started_at = _utc_naive(shared_started)
    else:
        harvest = _snk_harvest_once(
            ids, label=f"{adapter}_raw", mode=mode, delay=delay, workers=workers
        )
        report["harvest"] = harvest.get("run")
        if not harvest.get("ok"):
            report.update({"ok": False, "error": str(harvest.get("error") or "snk_harvest_failed")})
            return report
        rows_by_id = harvest["rowsById"]
        failed_by_id = harvest["failedById"]

    ok_items: list[dict[str, Any]] = []
    ok_ids: list[int] = []
    failed_items: list[dict[str, Any]] = []
    for item, external_id in zip(selected, ids):
        if external_id in rows_by_id:
            ok_items.append(item)
            ok_ids.append(external_id)
            continue
        failed_items.append(
            {
                "variantId": int(item["variantId"]),
                "externalId": str(item.get("externalId") or ""),
                "error": failed_by_id.get(external_id, "snk_harvest_row_missing"),
            }
        )
    report["failed"] = len(failed_items)
    report["failedItems"] = failed_items
    if failed_items:
        # Per-item source failures advance the quarantine streak immediately so
        # they persist even if the ingest step below fails for other reasons.
        _record_item_outcomes(adapter, succeeded=[], failed=failed_items)
    if not ok_items:
        report.update({"ok": False, "error": f"{adapter}_all_items_failed"})
        return report

    # The ingest consumer only ever sees the validated exact-ID subset; the raw
    # harvest (including error rows) stays immutable at its own path.
    ids_path.write_text("\n".join(str(value) for value in ok_ids) + "\n", encoding="ascii")
    harvest_path.write_text(
        "".join(
            json.dumps(rows_by_id[item_id], ensure_ascii=False, sort_keys=True) + "\n"
            for item_id in ok_ids
        ),
        encoding="utf-8",
    )
    report.update({"idsPath": str(ids_path), "harvestPath": str(harvest_path)})
    try:
        raw_rows, payload_sha = _validate_snk_harvest(harvest_path, ok_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"snk_harvest_contract:{type(exc).__name__}:{exc}"})
        return report

    if adapter == "snk_trades":
        ingest_cmd = [
            PY,
            "-X",
            "utf8",
            "pipelines/ingest_snk_trades_sales.py",
            "--harvest",
            str(harvest_path),
        ]
    else:
        ingest_report_path = OUT_DIR / f"snk_price_ingest_{mode}_{stamp}.json"
        ingest_cmd = [
            PY,
            "-X",
            "utf8",
            "pipelines/snk_market_data.py",
            "--ingest-jsonl",
            str(harvest_path),
            "--condition",
            "trading_card_single_psa10",
            "--out",
            str(ingest_report_path),
        ]
    try:
        with _db_writer_lease():
            report["ingest"] = _run(
                ingest_cmd,
                timeout=max(180, 5 * len(ok_ids)),
                dry_run=False,
            )
            if report["ingest"].get("exit") != 0:
                report.update({"ok": False, "error": f"{adapter}_ingest_failed"})
                return report
            if adapter == "snk_price":
                ingest_summary = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
                ingest_contract = _validate_snk_price_ingest(ingest_summary, ok_ids)
                report.update(ingest_contract)
                # K-line bars landed above are chart points only; the PRICE is
                # minted here from the completed trades.
                report["saleQuotes"] = _mint_sale_quotes("snkrdunk", dry_run=False)
            checkpoint = record_successful_poll(
                adapter=adapter,
                mode=mode,
                items=ok_items,
                payload={"harvest": raw_rows, "ingest": report["ingest"]},
                started_at=started_at,
                payload_sha_by_external=payload_sha,
            )
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"checkpoint:{type(exc).__name__}:{exc}"})
        return report
    _persist_item_checkpoints(
        adapter,
        mode=mode,
        items=ok_items,
        payload_sha_by_external=payload_sha,
        run_id=checkpoint.get("runId"),
    )
    _record_item_outcomes(adapter, succeeded=ok_items, failed=[])
    report.update(checkpoint)
    if failed_items:
        report.update({"ok": False, "error": f"{adapter}_partial_items_failed"})
    return report


def _snk_en_harvest_files() -> list[Path]:
    candidates = [
        *OUT_DIR.glob("snk_*harvest*.jsonl"),
        *OUT_DIR.glob("binding_delta_snk_harvest.jsonl"),
        *(ROOT / "data" / "runtime" / "operator").glob(
            "snk-global-market*.jsonl"
        ),
    ]
    return sorted(
        {path.resolve() for path in candidates if path.is_file()},
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )


def _local_snk_master_cache(
    external_ids: set[str],
) -> dict[str, tuple[dict[str, Any], datetime, Path]]:
    """Index already-landed exact masters, newest immutable payload first."""

    found: dict[str, tuple[dict[str, Any], datetime, Path]] = {}
    for path in _snk_en_harvest_files():
        with path.open(encoding="utf-8-sig") as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                external_id = str(row.get("item_id") or "").strip()
                if (
                    external_id not in external_ids
                    or row.get("error")
                ):
                    continue
                source_payload = row.get("source_payload")
                master = (
                    source_payload.get("master")
                    if isinstance(source_payload, Mapping)
                    else None
                )
                if not isinstance(master, Mapping):
                    continue
                try:
                    master_default_image(master, int(external_id))
                except (TypeError, ValueError):
                    continue
                observed_at = _parse_datetime(row.get("fetched_at"))
                if observed_at is None:
                    continue
                observed_at = _utc_naive(observed_at)
                previous = found.get(external_id)
                if previous is not None and previous[1] >= observed_at:
                    continue
                found[external_id] = (
                    dict(master),
                    observed_at,
                    path,
                )
    return found


def _local_snk_default_bytes(
    external_ids: set[str],
) -> dict[tuple[str, str], Path]:
    """Index exact URL-bound G10 bytes; a filename or SNK ID alone is not proof."""

    root = ROOT / "data" / "runtime" / "private-landing" / "g10"
    if not root.is_dir():
        return {}
    provider_roots = sorted(
        (path for path in root.rglob("snkrdunk") if path.parent.name == "cards"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    found: dict[tuple[str, str], Path] = {}
    for provider_root in provider_roots:
        payload_root = provider_root.parents[1]
        image_root = payload_root / "images"
        for external_id in external_ids:
            asset_info = provider_root / external_id / "asset_info.json"
            if not asset_info.is_file():
                continue
            try:
                payload = json.loads(asset_info.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                continue
            query = payload.get("assetQueryId")
            if (
                not isinstance(query, Mapping)
                or str(query.get("source") or "") != "snkrdunk"
                or str(query.get("id") or "") != external_id
            ):
                continue
            url = str(payload.get("image") or "").strip()
            if not url or (external_id, url) in found:
                continue
            paths = sorted(image_root.glob(f"snkrdunk_{external_id}.*"))
            if paths:
                found[(external_id, url)] = paths[0]
    return found


def _resolve_object_path(value: Any) -> Path:
    path = Path(str(value or ""))
    return path if path.is_absolute() else ROOT / path


def _load_existing_snk_en_acceptances(
    cur, items: list[dict[str, Any]]
) -> dict[int, dict[str, Any]]:
    if not items:
        return {}
    variant_ids = [int(item["variantId"]) for item in items]
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT ca.variant_id,ca.id AS acceptance_id,ca.evidence_sha256,
               l.id AS lineage_id,l.exact_item_id,l.product_url,
               l.master_payload_sha256,l.default_image_url,
               l.default_image_url_sha256,l.downloaded_bytes_sha256,
               l.processed_content_sha256,l.transform_sha256,
               l.identity_evidence_sha256,l.lineage_sha256,
               l.source_observed_at,
               page.product_page_payload_sha256,
               page.product_page_observed_at,page.authority_sha256,
               a.id AS image_asset_id,
               a.private_object_key,a.content_sha256,a.mime_type,
               q.semantic_match_status,q.card_number_match,q.language_match,
               q.tcg_match,q.raw_front_confirmed,q.public_allowed
        FROM market_canonical_image_acceptance ca
        INNER JOIN market_snk_en_storefront_lineage l
          ON l.id=ca.storefront_lineage_id AND l.variant_id=ca.variant_id
         AND l.lineage_sha256=ca.lineage_sha256
        INNER JOIN market_snk_en_product_page_authority page
          ON page.storefront_lineage_id=l.id
         AND page.variant_id=l.variant_id
         AND page.exact_item_id=l.exact_item_id
         AND page.product_url=l.product_url
         AND page.default_image_url=l.default_image_url
         AND page.master_payload_sha256=l.master_payload_sha256
         AND page.identity_evidence_sha256=l.identity_evidence_sha256
        INNER JOIN market_image_asset a
          ON a.id=ca.image_asset_id AND a.id=l.processed_image_asset_id
         AND a.variant_id=ca.variant_id
         AND a.content_sha256=l.processed_content_sha256
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.id=(SELECT q2.id FROM market_image_qc q2
                   WHERE q2.image_asset_id=a.id
                   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
        WHERE ca.variant_id IN ({placeholders})
          AND l.source_code='snkrdunk' AND l.storefront_code='en'
          AND NOT EXISTS (
            SELECT 1 FROM market_canonical_image_acceptance newer
            WHERE newer.supersedes_acceptance_id=ca.id
          )
        """,
        variant_ids,
    )
    return {int(row["variant_id"]): dict(row) for row in cur.fetchall()}


def _existing_snk_en_is_reusable(
    item: Mapping[str, Any], row: Mapping[str, Any]
) -> bool:
    external_id = str(item.get("externalId") or "").strip()
    product_url = exact_en_product_url(int(external_id))
    content_sha = str(row.get("content_sha256") or "")
    object_path = _resolve_object_path(row.get("private_object_key"))
    if (
        str(row.get("exact_item_id") or "") != external_id
        or str(row.get("product_url") or "") != product_url
        or str(row.get("default_image_url_sha256") or "")
        != _text_sha256(str(row.get("default_image_url") or ""))
        or str(row.get("processed_content_sha256") or "") != content_sha
        or str(row.get("mime_type") or "") != "image/webp"
        or str(row.get("identity_evidence_sha256") or "")
        != str(item.get("identityEvidenceSha256") or "")
        or str(row.get("semantic_match_status") or "")
        not in {"accepted_freeze", "human_or_vision_confirmed"}
        or not all(
            int(row.get(field) or 0) == 1
            for field in (
                "card_number_match",
                "language_match",
                "tcg_match",
                "raw_front_confirmed",
                "public_allowed",
            )
        )
        or not all(
            SHA256_RE.fullmatch(str(row.get(field) or ""))
            for field in (
                "master_payload_sha256",
                "downloaded_bytes_sha256",
                "processed_content_sha256",
                "transform_sha256",
                "identity_evidence_sha256",
                "lineage_sha256",
                "evidence_sha256",
                "product_page_payload_sha256",
                "authority_sha256",
            )
        )
        or row.get("product_page_observed_at") is None
        or not object_path.is_file()
    ):
        return False
    try:
        return _bytes_sha256(object_path.read_bytes()) == content_sha
    except OSError:
        return False


def _prepare_snk_en_target(
    item: Mapping[str, Any],
    *,
    api_pool: SnkrdunkApiPool,
    master_cache: Mapping[str, tuple[dict[str, Any], datetime, Path]],
    bytes_cache: Mapping[tuple[str, str], Path],
) -> SnkEnPrepared:
    variant_id = int(item["variantId"])
    external_id = str(item.get("externalId") or "").strip()
    if not external_id.isdigit() or int(external_id) <= 0:
        raise ValueError(f"invalid exact SNK external ID: {external_id}")
    identity_evidence = str(item.get("identityEvidenceSha256") or "")
    if not SHA256_RE.fullmatch(identity_evidence):
        raise ValueError(
            f"exact snkrdunk binding lacks hash-bound identity evidence: {external_id}"
        )

    cached = master_cache.get(external_id)
    if cached is None:
        master = api_pool.get_master(int(external_id))
        source_observed_at = _utc_naive()
        master_reused = False
    else:
        master, source_observed_at, _ = cached
        master_reused = True
    default_url = master_default_image(master, int(external_id))
    page_authority = api_pool.get_en_product_page_authority(
        int(external_id), default_url
    )
    if (
        page_authority.get("contract") != SNK_EN_PRODUCT_PAGE_CONTRACT
        or page_authority.get("productUrl")
        != exact_en_product_url(int(external_id))
        or page_authority.get("finalUrl") != page_authority.get("productUrl")
        or int(page_authority.get("httpStatus") or 0) != 200
        or page_authority.get("defaultImageUrl") != default_url
        or not SHA256_RE.fullmatch(
            str(page_authority.get("productPagePayloadSha256") or "")
        )
    ):
        raise RuntimeError(f"incomplete SNK EN product-page authority: {external_id}")
    product_page_observed_at = _utc_naive()
    local_bytes_path = bytes_cache.get((external_id, default_url))
    if local_bytes_path is not None:
        raw = local_bytes_path.read_bytes()
        captured_at = _utc_naive(source_observed_at)
        bytes_reused = True
        downloaded = False
    else:
        raw = api_pool.get_bytes(default_url)
        captured_at = _utc_naive()
        bytes_reused = False
        downloaded = True
    processed = process_snk_default_image(raw)
    return SnkEnPrepared(
        variant_id=variant_id,
        external_id=external_id,
        product_url=exact_en_product_url(int(external_id)),
        product_page_payload_sha256=str(
            page_authority["productPagePayloadSha256"]
        ),
        product_page_observed_at=product_page_observed_at,
        master_payload_sha256=_sha256(master),
        default_image_url=default_url,
        default_image_url_sha256=_text_sha256(default_url),
        downloaded_bytes_sha256=_bytes_sha256(raw),
        image=processed,
        identity_evidence_sha256=identity_evidence,
        source_observed_at=max(
            _utc_naive(source_observed_at), captured_at, product_page_observed_at
        ),
        captured_at=captured_at,
        master_reused=master_reused,
        bytes_reused=bytes_reused,
        downloaded=downloaded,
    )


def _write_snk_en_asset(image: ProcessedSnkDefaultImage) -> tuple[Path, str]:
    SNK_EN_ASSET_DIR.mkdir(parents=True, exist_ok=True)
    path = SNK_EN_ASSET_DIR / f"{image.content_sha256}.webp"
    relative = path.relative_to(ROOT).as_posix()
    if path.is_file():
        if _bytes_sha256(path.read_bytes()) != image.content_sha256:
            raise RuntimeError(f"content-addressed SNK EN asset hash mismatch: {path}")
        return path, relative
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(image.content)
    if _bytes_sha256(temporary.read_bytes()) != image.content_sha256:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"SNK EN asset write hash mismatch: {path}")
    os.replace(temporary, path)
    return path, relative


def _snk_en_human_rejected(cur, variant_id: int, content_sha256: str) -> bool:
    cur.execute(
        "SELECT 1 FROM market_image_rejection_registry"
        " WHERE variant_id=%s AND content_sha256=%s",
        (variant_id, content_sha256),
    )
    return cur.fetchone() is not None


def _upsert_snk_en_freeze(
    cur,
    *,
    variant_id: int,
    external_id: str,
    content_sha256: str,
    acceptance_id: int,
    lineage_sha256: str,
    evidence_sha256: str,
    accepted_at: datetime,
) -> None:
    # 人手 reject 過嘅 (variant, content) 唔可以由 collector 再 accept。呢張表
    # (market_image_rejection_registry) 一直只有 rebuild_036 image-bind 讀，
    # 呢條 lane 由頭到尾冇讀過 —— 所以人手換走一張圖之後，下一次 SNK EN lane
    # 會將舊 sha 寫返 'accepted'，同人手釘落嘅 freeze 變成兩行 accepted。
    # 照跑照 checkpoint，但個 status 要講真話。
    human_rejected = _snk_en_human_rejected(cur, variant_id, content_sha256)
    status = "rejected" if human_rejected else "accepted"
    note = (
        "human-rejected content; SNK EN storefront default not published"
        if human_rejected
        else "exact SNK EN storefront default image"
    )
    cur.execute(
        """
        INSERT INTO operator_binding_freeze
          (variant_id,freeze_kind,source_code,external_entity_id,
           content_sha256,canonical_image_acceptance_id,
           accepted_lineage_sha256,acceptance_status,actor,
           evidence_sha256,note,accepted_at)
        VALUES (%s,'image',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          external_entity_id=VALUES(external_entity_id),
          content_sha256=VALUES(content_sha256),
          canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),
          accepted_lineage_sha256=VALUES(accepted_lineage_sha256),
          acceptance_status=VALUES(acceptance_status),actor=VALUES(actor),
          evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
          accepted_at=VALUES(accepted_at)
        """,
        (
            variant_id,
            SNK_EN_FREEZE_SOURCE_CODE,
            external_id,
            content_sha256,
            acceptance_id,
            lineage_sha256,
            status,
            SNK_EN_ACCEPTED_BY,
            evidence_sha256,
            note,
            accepted_at,
        ),
    )


def _assert_exact_snk_en_binding(cur, item: Mapping[str, Any]) -> str:
    variant_id = int(item["variantId"])
    external_id = str(item.get("externalId") or "").strip()
    cur.execute(
        """
        SELECT external_entity_id
        FROM catalog_source_identity
        WHERE variant_id=%s
          AND source_code IN ('snk','snkrdunk','snkrdunk_en')
          AND LOWER(match_status)='exact'
        LOCK IN SHARE MODE
        """,
        (variant_id,),
    )
    exact_ids = {
        str(binding.get("external_entity_id") or "").strip()
        for binding in cur.fetchall()
    }
    if exact_ids != {external_id}:
        raise RuntimeError(
            f"multiple or changed exact SNK IDs before commit: {variant_id}:{sorted(exact_ids)}"
        )
    cur.execute(
        """
        SELECT evidence_sha256
        FROM catalog_source_identity
        WHERE variant_id=%s AND source_code='snkrdunk'
          AND external_entity_id=%s AND LOWER(match_status)='exact'
        LIMIT 1
        LOCK IN SHARE MODE
        """,
        (variant_id, external_id),
    )
    row = cur.fetchone()
    evidence_sha = str((row or {}).get("evidence_sha256") or "")
    if (
        not SHA256_RE.fullmatch(evidence_sha)
        or evidence_sha != str(item.get("identityEvidenceSha256") or "")
    ):
        raise RuntimeError(
            f"exact snkrdunk binding changed before commit: {variant_id}:{external_id}"
        )
    return evidence_sha


def _checkpoint_snk_en_item(
    cur,
    *,
    item: dict[str, Any],
    mode: str,
    started_at: datetime,
    completed_at: datetime,
    lineage_sha256: str,
    payload: Mapping[str, Any],
) -> int:
    run_id = _insert_control_run(
        cur,
        adapter="snk_en_image",
        mode=mode,
        items=[item],
        payload=dict(payload),
        started_at=started_at,
        completed_at=completed_at,
    )
    checkpointed = _upsert_checkpoints(
        cur,
        adapter="snk_en_image",
        items=[item],
        run_id=run_id,
        completed_at=completed_at,
        payload_sha_by_external={str(item["externalId"]): lineage_sha256},
    )
    if checkpointed != 1:
        raise RuntimeError(f"SNK EN checkpoint did not advance exactly once: {item['externalId']}")
    return run_id


def _persist_reused_snk_en(
    cur,
    *,
    item: dict[str, Any],
    row: Mapping[str, Any],
    mode: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    _assert_exact_snk_en_binding(cur, item)
    lineage_sha = str(row["lineage_sha256"])
    evidence_sha = str(row["evidence_sha256"])
    _upsert_snk_en_freeze(
        cur,
        variant_id=int(item["variantId"]),
        external_id=str(item["externalId"]),
        content_sha256=str(row["content_sha256"]),
        acceptance_id=int(row["acceptance_id"]),
        lineage_sha256=lineage_sha,
        evidence_sha256=evidence_sha,
        accepted_at=completed_at,
    )
    run_id = _checkpoint_snk_en_item(
        cur,
        item=item,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        lineage_sha256=lineage_sha,
        payload={
            "contract": "snk-en-storefront-lineage-v1",
            "reusedAcceptanceId": int(row["acceptance_id"]),
            "lineageSha256": lineage_sha,
        },
    )
    return {"inserted": 0, "runId": run_id, "lineageSha256": lineage_sha}


def _persist_prepared_snk_en(
    cur,
    *,
    item: dict[str, Any],
    prepared: SnkEnPrepared,
    mode: str,
    started_at: datetime,
    completed_at: datetime,
) -> dict[str, Any]:
    _assert_exact_snk_en_binding(cur, item)
    _, private_object_key = _write_snk_en_asset(prepared.image)
    asset_source_version = _sha256(
        {
            "masterPayloadSha256": prepared.master_payload_sha256,
            "downloadedBytesSha256": prepared.downloaded_bytes_sha256,
            "transformSha256": prepared.image.transform_sha256,
        }
    )
    cur.execute(
        """
        INSERT INTO market_image_asset
          (variant_id,image_kind,content_sha256,private_object_key,mime_type,
           width_px,height_px,source_version_sha256,captured_at)
        VALUES (%s,'raw_front',%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          private_object_key=VALUES(private_object_key),mime_type=VALUES(mime_type),
          width_px=VALUES(width_px),height_px=VALUES(height_px),
          source_version_sha256=VALUES(source_version_sha256),
          captured_at=GREATEST(captured_at,VALUES(captured_at))
        """,
        (
            prepared.variant_id,
            prepared.image.content_sha256,
            private_object_key,
            prepared.image.mime_type,
            prepared.image.width,
            prepared.image.height,
            asset_source_version,
            prepared.captured_at,
        ),
    )
    cur.execute(
        """
        SELECT id FROM market_image_asset
        WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s
        LIMIT 1
        """,
        (prepared.variant_id, prepared.image.content_sha256),
    )
    asset = cur.fetchone()
    if not asset:
        raise RuntimeError(f"SNK EN asset row missing: {prepared.external_id}")
    asset_id = int(asset["id"])
    cur.execute(
        """
        INSERT INTO market_image_source_pointer
          (variant_id,image_kind,remote_url_sha256,source_path,
           source_version_sha256,public_allowed,observed_at)
        VALUES (%s,'raw_front',%s,%s,%s,1,%s)
        ON DUPLICATE KEY UPDATE
          remote_url_sha256=VALUES(remote_url_sha256),
          source_path=VALUES(source_path),public_allowed=1,
          observed_at=GREATEST(observed_at,VALUES(observed_at))
        """,
        (
            prepared.variant_id,
            prepared.default_image_url_sha256,
            prepared.default_image_url,
            asset_source_version,
            prepared.source_observed_at,
        ),
    )
    # Human-rejected content (SAMPLE watermark / overlay / not a card front /
    # wrong printing — DADDY 2026-09-25) is still recorded as seen (asset,
    # lineage, page authority, checkpoint) but never re-vouched: this QC row
    # would be the asset's newest, public_allowed=1, and a new acceptance would
    # supersede whatever replaced it.  The FE read drops it too; this keeps the
    # writer from putting it back in the first place.
    human_rejected = _snk_en_human_rejected(
        cur, prepared.variant_id, prepared.image.content_sha256
    )
    if not human_rejected:
        cur.execute(
            """
            INSERT INTO market_image_qc
              (image_asset_id,semantic_match_status,card_number_match,language_match,
               tcg_match,raw_front_confirmed,public_allowed,rejection_reason,
               checked_at,qc_version)
            VALUES (%s,'accepted_freeze',1,1,1,1,1,NULL,%s,%s)
            ON DUPLICATE KEY UPDATE
              semantic_match_status='accepted_freeze',card_number_match=1,
              language_match=1,tcg_match=1,raw_front_confirmed=1,
              public_allowed=1,rejection_reason=NULL,checked_at=VALUES(checked_at)
            """,
            (asset_id, completed_at, prepared.image.qc_version),
        )
    lineage_payload = {
        "contract": "snk-en-storefront-lineage-v1",
        "variantId": prepared.variant_id,
        "sourceCode": SNK_EN_SOURCE_CODE,
        "storefront": "en",
        "exactItemId": prepared.external_id,
        "productUrl": prepared.product_url,
        "productPagePayloadSha256": prepared.product_page_payload_sha256,
        "productPageObservedAt": prepared.product_page_observed_at.isoformat(
            timespec="microseconds"
        ),
        "masterPayloadSha256": prepared.master_payload_sha256,
        "defaultImageUrl": prepared.default_image_url,
        "defaultImageUrlSha256": prepared.default_image_url_sha256,
        "downloadedBytesSha256": prepared.downloaded_bytes_sha256,
        "processedContentSha256": prepared.image.content_sha256,
        "transformSha256": prepared.image.transform_sha256,
        "identityEvidenceSha256": prepared.identity_evidence_sha256,
        "sourceObservedAt": prepared.source_observed_at.isoformat(timespec="microseconds"),
    }
    lineage_sha = _sha256(lineage_payload)
    cur.execute(
        """
        INSERT IGNORE INTO market_snk_en_storefront_lineage
          (variant_id,source_code,storefront_code,exact_item_id,product_url,
           master_payload_sha256,default_image_url,default_image_url_sha256,
           downloaded_bytes_sha256,processed_image_asset_id,
           processed_content_sha256,transform_sha256,transform_json,
           identity_evidence_sha256,lineage_sha256,source_observed_at)
        VALUES (%s,'snkrdunk','en',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            prepared.variant_id,
            prepared.external_id,
            prepared.product_url,
            prepared.master_payload_sha256,
            prepared.default_image_url,
            prepared.default_image_url_sha256,
            prepared.downloaded_bytes_sha256,
            asset_id,
            prepared.image.content_sha256,
            prepared.image.transform_sha256,
            json.dumps(
                prepared.image.transform,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            prepared.identity_evidence_sha256,
            lineage_sha,
            prepared.source_observed_at,
        ),
    )
    lineage_inserted = int(cur.rowcount or 0) == 1
    cur.execute(
        "SELECT id FROM market_snk_en_storefront_lineage WHERE lineage_sha256=%s",
        (lineage_sha,),
    )
    lineage = cur.fetchone()
    if not lineage:
        raise RuntimeError(f"SNK EN lineage row missing: {prepared.external_id}")
    lineage_id = int(lineage["id"])
    authority_payload = {
        "contract": SNK_EN_PRODUCT_PAGE_CONTRACT,
        "variantId": prepared.variant_id,
        "exactItemId": prepared.external_id,
        "productUrl": prepared.product_url,
        "finalUrl": prepared.product_url,
        "httpStatus": 200,
        "productPagePayloadSha256": prepared.product_page_payload_sha256,
        "productPageObservedAt": prepared.product_page_observed_at.isoformat(
            timespec="microseconds"
        ),
        "defaultImageUrl": prepared.default_image_url,
        "masterPayloadSha256": prepared.master_payload_sha256,
        "identityEvidenceSha256": prepared.identity_evidence_sha256,
        "storefrontLineageSha256": lineage_sha,
    }
    authority_sha = _sha256(authority_payload)
    cur.execute(
        """
        INSERT IGNORE INTO market_snk_en_product_page_authority
          (storefront_lineage_id,variant_id,exact_item_id,product_url,final_url,
           http_status,product_page_payload_sha256,product_page_observed_at,
           default_image_url,master_payload_sha256,identity_evidence_sha256,
           authority_sha256)
        VALUES (%s,%s,%s,%s,%s,200,%s,%s,%s,%s,%s,%s)
        """,
        (
            lineage_id,
            prepared.variant_id,
            prepared.external_id,
            prepared.product_url,
            prepared.product_url,
            prepared.product_page_payload_sha256,
            prepared.product_page_observed_at,
            prepared.default_image_url,
            prepared.master_payload_sha256,
            prepared.identity_evidence_sha256,
            authority_sha,
        ),
    )
    cur.execute(
        """
        SELECT id FROM market_snk_en_product_page_authority
        WHERE storefront_lineage_id=%s AND authority_sha256=%s
        """,
        (lineage_id, authority_sha),
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            f"SNK EN product-page authority row missing: {prepared.external_id}"
        )
    acceptance_evidence = _sha256(
        {
            "contract": "canonical-snk-en-image-acceptance-v1",
            "lineageSha256": lineage_sha,
            "imageContentSha256": prepared.image.content_sha256,
            "qcVersion": prepared.image.qc_version,
        }
    )
    cur.execute(
        """
        SELECT ca.id,ca.lineage_sha256,ca.accepted_by
        FROM market_canonical_image_acceptance ca
        WHERE ca.variant_id=%s
          AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer
                          WHERE newer.supersedes_acceptance_id=ca.id)
        ORDER BY ca.accepted_at DESC,ca.id DESC LIMIT 1
        """,
        (prepared.variant_id,),
    )
    current = cur.fetchone()
    # 一張卡嘅 canonical image 有三個 writer：呢條 lane、rebuild_036 image-bind、
    # 同人手。三個都無條件插一行 supersede 上一行，所以邊個最後跑就邊個贏。
    # 2026-08-11 12:27 呢條 lane 一次過搶咗 677 張：52 張由 image-bind 揀嘅 PC 圖
    # 跌返做未發佈嘅 SNK 圖（bytes 只喺 data/runtime/operator/snk-en-assets/，
    # public/market-assets 冇），rank 18 Dodgers Luffy 更加蓋咗 1 個鐘之前人手
    # 揀嗰張。
    #
    # 規矩：只可以 supersede 自己寫嘅嗰行。當前 canonical 唔係自己嘅，照收貨
    # （asset / lineage / product-page authority 照寫，checkpoint 照過，SLA 照綠），
    # 但唔郁 canonical，亦唔郁 freeze —— 唔會再靜靜換走人手或者 image-bind 嘅決定。
    owned = current is None or str(current["accepted_by"]) == SNK_EN_ACCEPTED_BY
    if current and str(current["lineage_sha256"]) == lineage_sha:
        # Same lineage already canonical: the freeze below is rewritten and says
        # 'rejected' when this content is in the registry.
        acceptance_id = int(current["id"])
        acceptance_inserted = False
    elif not owned or human_rejected:
        # Human-rejected content never gets a new acceptance: superseding the
        # current row with it would take the card's image down (or put the
        # rejected one back), whoever owns the current row.
        acceptance_id = None
        acceptance_inserted = False
    else:
        # image_lane_policy is the only writer of the table.  A held decision
        # (One Piece head: SNK OP art is the SAMPLE source; review-approved
        # head; registry pair) is handled like "not owned" above: no acceptance,
        # no freeze, the rest of the item is still recorded and checkpointed.
        from image_lane_policy import accept_canonical_image

        lane = accept_canonical_image(
            cur,
            variant_id=prepared.variant_id,
            image_asset_id=asset_id,
            content_sha256=prepared.image.content_sha256,
            lineage_sha256=lineage_sha,
            evidence_sha256=acceptance_evidence,
            accepted_by=SNK_EN_ACCEPTED_BY,
            accepted_at=completed_at,
            storefront_lineage_id=lineage_id,
        )
        acceptance_id = lane.acceptance_id
        acceptance_inserted = lane.inserted
    if acceptance_id is not None:
        _upsert_snk_en_freeze(
            cur,
            variant_id=prepared.variant_id,
            external_id=prepared.external_id,
            content_sha256=prepared.image.content_sha256,
            acceptance_id=acceptance_id,
            lineage_sha256=lineage_sha,
            evidence_sha256=acceptance_evidence,
            accepted_at=completed_at,
        )
    run_id = _checkpoint_snk_en_item(
        cur,
        item=item,
        mode=mode,
        started_at=started_at,
        completed_at=completed_at,
        lineage_sha256=lineage_sha,
        payload={
            **lineage_payload,
            "lineageSha256": lineage_sha,
            "productPageAuthoritySha256": authority_sha,
            "acceptanceId": acceptance_id,
            "acceptanceEvidenceSha256": acceptance_evidence,
        },
    )
    return {
        "inserted": int(lineage_inserted or acceptance_inserted),
        "runId": run_id,
        "lineageSha256": lineage_sha,
    }


def _snk_en_checkpoint_window() -> tuple[datetime | None, datetime | None, int]:
    """Oldest/newest success of this lane's own checkpoints (read only)."""

    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT MIN(last_effective_at) AS oldest,
                   MAX(last_effective_at) AS newest,
                   COUNT(*) AS n
            FROM market_ingest_checkpoint
            WHERE source_code = %s
            """,
            ("snk_en_image",),
        )
        row = cur.fetchone() or {}
    finally:
        conn.close()
    return (
        _parse_datetime(row.get("oldest")),
        _parse_datetime(row.get("newest")),
        int(row.get("n") or 0),
    )


def _snk_en_idle_freshness() -> dict[str, Any]:
    """Freshness of a poll that selected nothing, judged by the last success.

    2026-09-25 crawler follow-up: every registry row reads modeNeeded=stock
    (the projection never shows SNK EN lineage, see the work-folder note), so
    V2 incr selected nothing and this branch said slaOk=True from 08-28 on.
    Nothing due only means fresh when the lane's newest success is inside
    SLA_HOURS; an unreadable or missing checkpoint is not fresh either.
    """

    freshness: dict[str, Any] = {
        "slaHours": SLA_HOURS,
        "oldestSuccessAt": None,
        "newestSuccessAt": None,
        "slaOk": False,
        "judgedBy": "lane_checkpoint_newest_success",
    }
    try:
        oldest, newest, rows = _snk_en_checkpoint_window()
    except Exception as exc:  # noqa: BLE001
        freshness["reason"] = f"checkpoint_unreadable:{type(exc).__name__}"
        return freshness
    age = _age_hours(newest)
    freshness.update(
        {
            "oldestSuccessAt": oldest.isoformat(sep=" ") if oldest else None,
            "newestSuccessAt": newest.isoformat(sep=" ") if newest else None,
            "newestAgeHours": None if age is None else round(age, 2),
            "checkpointRows": rows,
            "slaOk": age is not None and 0 <= age <= SLA_HOURS,
        }
    )
    if not freshness["slaOk"]:
        freshness["reason"] = (
            "no_lane_checkpoint" if newest is None
            else "nothing_due_and_last_success_past_sla"
        )
    return freshness


def run_snk_en_image(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
) -> dict[str, Any]:
    try:
        selected, _ = _snk_selection(items, limit)
    except Exception as exc:  # noqa: BLE001
        return {
            "adapter": "snk_en_image",
            "mode": mode,
            "due": len(items),
            "processed": 0,
            "inserted": 0,
            "checkpoint": 0,
            "checkpointed": 0,
            "reused": 0,
            "downloaded": 0,
            "failed": len(items),
            "ok": False,
            "error": f"snk_en_selection:{type(exc).__name__}:{exc}",
        }
    report: dict[str, Any] = {
        "adapter": "snk_en_image",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "inserted": 0,
        "checkpoint": 0,
        "checkpointed": 0,
        "reused": 0,
        "reusedAccepted": 0,
        "reusedMaster": 0,
        "reusedBytes": 0,
        "downloaded": 0,
        "downloadedMaster": 0,
        "failed": 0,
        "failedItems": [],
        "ok": True,
        "targetScope": "current_active_universe_all_exact_snkrdunk",
        "storefront": "en",
        "browserUsed": False,
    }
    if not selected:
        report["freshness"] = _snk_en_idle_freshness()
        return report
    if dry_run:
        report.update(
            {
                "dryRun": True,
                "note": "exact active SNK IDs selected; network, asset and DB writes not executed",
                "freshness": {
                    "slaHours": SLA_HOURS,
                    "oldestSuccessAt": None,
                    "newestSuccessAt": None,
                    "slaOk": False,
                },
            }
        )
        return report

    load_env()
    writer = db()
    success_times: list[datetime] = []
    completed_variants: set[int] = set()
    try:
        cur = writer.cursor()
        _require_migration_029(cur)
        # A due SNK EN image poll always performs the exact product-page GET.
        # Existing accepted lineage is used by classify_needs while fresh, not
        # as a substitute for a newly due storefront observation.
        reusable: dict[int, dict[str, Any]] = {}
        writer.commit()
        missing = [
            item for item in selected if int(item["variantId"]) not in reusable
        ]
        external_ids = {str(item["externalId"]) for item in missing}
        master_cache = _local_snk_master_cache(external_ids)
        bytes_cache = _local_snk_default_bytes(external_ids)
        http_workers = max(1, min(int(workers), len(missing) or 1, 12))
        report["httpWorkers"] = http_workers
        api_pool = SnkrdunkApiPool(
            workers=http_workers,
            delay=max(0.0, float(delay)),
            retries=1,
        )
        futures: dict[int, Future[SnkEnPrepared]] = {}
        executor = ThreadPoolExecutor(
            max_workers=http_workers,
            thread_name_prefix="snk-en-image",
        )
        try:
            for item in missing:
                futures[int(item["variantId"])] = executor.submit(
                    _prepare_snk_en_target,
                    item,
                    api_pool=api_pool,
                    master_cache=master_cache,
                    bytes_cache=bytes_cache,
                )
            for item in selected:
                variant_id = int(item["variantId"])
                started_at = _utc_naive()
                try:
                    if variant_id in reusable:
                        completed_at = _utc_naive()
                        report["reusedAccepted"] += 1
                        report["reused"] += 1
                        write = _persist_reused_snk_en(
                            cur,
                            item=item,
                            row=reusable[variant_id],
                            mode=mode,
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                    else:
                        prepared = futures[variant_id].result()
                        completed_at = _utc_naive()
                        report["reusedMaster"] += int(prepared.master_reused)
                        report["reusedBytes"] += int(prepared.bytes_reused)
                        report["reused"] += int(
                            prepared.master_reused or prepared.bytes_reused
                        )
                        report["downloaded"] += int(prepared.downloaded)
                        report["downloadedMaster"] += int(
                            not prepared.master_reused
                        )
                        write = _persist_prepared_snk_en(
                            cur,
                            item=item,
                            prepared=prepared,
                            mode=mode,
                            started_at=started_at,
                            completed_at=completed_at,
                        )
                    writer.commit()
                    report["inserted"] += int(write["inserted"])
                    report["checkpointed"] += 1
                    success_times.append(completed_at)
                    completed_variants.add(variant_id)
                except Exception as exc:  # noqa: BLE001
                    writer.rollback()
                    report["failedItems"].append(
                        {
                            "variantId": variant_id,
                            "externalId": str(item.get("externalId") or ""),
                            "error": f"{type(exc).__name__}:{exc}",
                        }
                    )
        finally:
            executor.shutdown(wait=True, cancel_futures=False)
    except Exception as exc:  # noqa: BLE001
        writer.rollback()
        already_failed = {
            int(row["variantId"]) for row in report["failedItems"]
        }
        for item in selected:
            variant_id = int(item["variantId"])
            if variant_id in completed_variants or variant_id in already_failed:
                continue
            report["failedItems"].append(
                {
                    "variantId": variant_id,
                    "externalId": str(item.get("externalId") or ""),
                    "error": f"{type(exc).__name__}:{exc}",
                }
            )
    finally:
        writer.close()

    report["failed"] = len(report["failedItems"])
    report["checkpoint"] = report["checkpointed"]
    report["ok"] = (
        report["failed"] == 0
        and report["checkpointed"] == report["processed"]
    )
    report["freshness"] = {
        "slaHours": SLA_HOURS,
        "oldestSuccessAt": (
            min(success_times).isoformat(sep=" ") if success_times else None
        ),
        "newestSuccessAt": (
            max(success_times).isoformat(sep=" ") if success_times else None
        ),
        "slaOk": bool(report["ok"] and success_times),
    }
    if not report["ok"]:
        report["error"] = "one_or_more_exact_snk_en_targets_failed"
    return report


def _command_arg_path(command: list[Any], flag: str) -> Path:
    values = [str(value) for value in command]
    try:
        return Path(values[values.index(flag) + 1])
    except (ValueError, IndexError) as exc:
        raise RuntimeError(f"receipt command is missing {flag}") from exc


def cmd_commit_snk_price_receipt(*, receipt_path: Path) -> dict[str, Any]:
    """Commit checkpoints from the already-completed exact-ID SNK price receipt.

    This command never performs a network request or a second ingest. It exists so a
    controller-contract repair can resume from durable harvest/ingest evidence.
    """
    receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    matches = [
        row
        for row in (receipt.get("results") or [])
        if row.get("adapter") == "snk_price"
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one SNK price result in receipt, found {len(matches)}")
    row = matches[0]
    harvest_path = Path(str(row.get("harvestPath") or ""))
    ingest_path = _command_arg_path((row.get("ingest") or {}).get("cmd") or [], "--out")
    if (row.get("harvest") or {}).get("exit") != 0:
        raise RuntimeError("receipt harvest did not exit zero")
    if (row.get("ingest") or {}).get("exit") != 0:
        raise RuntimeError("receipt ingest did not exit zero")
    if not harvest_path.is_file() or not ingest_path.is_file():
        raise RuntimeError("receipt harvest or ingest artifact is missing")

    requested_ids = [
        int(line.strip())
        for line in Path(str(row.get("idsPath") or "")).read_text(encoding="ascii").splitlines()
        if line.strip()
    ]
    if len(requested_ids) != len(set(requested_ids)):
        raise RuntimeError("receipt requested IDs are duplicated")
    raw_rows, payload_sha = _validate_snk_harvest(harvest_path, requested_ids)
    ingest_summary = json.loads(ingest_path.read_text(encoding="utf-8-sig"))
    ingest_contract = _validate_snk_price_ingest(ingest_summary, requested_ids)

    registry = _jsonl_rows(REGISTRY_PATH)
    by_external = {
        str(item.get("externalId") or ""): item
        for item in registry
        if item.get("adapter") == "snk_price"
    }
    requested_text = {str(value) for value in requested_ids}
    if set(by_external) != requested_text:
        missing = sorted(requested_text - set(by_external))
        extra = sorted(set(by_external) - requested_text)
        raise RuntimeError(
            f"current active SNK registry differs from receipt missing={missing[:10]} extra={extra[:10]}"
        )
    items = [by_external[str(value)] for value in requested_ids]
    started_at = _parse_datetime((row.get("harvest") or {}).get("startedAt"))
    completed_at = _parse_datetime((row.get("ingest") or {}).get("finishedAt"))
    if started_at is None or completed_at is None or completed_at < started_at:
        raise RuntimeError("receipt timestamps are missing or invalid")
    checkpoint = record_successful_poll(
        adapter="snk_price",
        mode=str(row.get("mode") or "incr"),
        items=items,
        payload={
            "resumeContract": "snk_price_empty_kline_checkpoint_v1",
            "receiptSha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            "harvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
            "ingestSha256": hashlib.sha256(ingest_path.read_bytes()).hexdigest(),
            "harvestRows": len(raw_rows),
            **ingest_contract,
        },
        started_at=started_at,
        completed_at=completed_at,
        payload_sha_by_external=payload_sha,
    )
    result = {
        "action": "commit-snk-price-receipt",
        "ok": True,
        "receipt": str(receipt_path),
        "harvest": str(harvest_path),
        "ingest": str(ingest_path),
        **ingest_contract,
        **checkpoint,
        "lastEffectiveAt": completed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "networkRequests": 0,
        "ingestRuns": 0,
    }
    output = OUT_DIR / "snk_price_checkpoint_resume.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["output"] = str(output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def cmd_commit_snk_binding_delta(
    *,
    harvest_path: Path,
    variant_ids: list[int],
) -> dict[str, Any]:
    """Ingest and checkpoint exact new bindings from one already-completed SNK harvest.

    No network request is made here.  The broad source harvest may contain other IDs;
    only the explicitly named active variants are admitted into the delta receipt.
    """
    wanted_variants = list(dict.fromkeys(int(value) for value in variant_ids))
    if not wanted_variants or any(value <= 0 for value in wanted_variants):
        raise RuntimeError("at least one positive --variant-id is required")
    if not harvest_path.is_file():
        raise RuntimeError(f"completed SNK harvest is missing: {harvest_path}")

    registry = _jsonl_rows(REGISTRY_PATH)
    items_by_adapter: dict[str, dict[int, dict[str, Any]]] = {}
    for adapter in ("snk_trades", "snk_price"):
        items_by_adapter[adapter] = {
            int(row["variantId"]): row
            for row in registry
            if row.get("adapter") == adapter
            and int(row.get("variantId") or 0) in wanted_variants
            and str(row.get("externalId") or "").isdigit()
        }
        missing = sorted(set(wanted_variants) - set(items_by_adapter[adapter]))
        if missing:
            raise RuntimeError(f"active registry missing exact {adapter} variants: {missing}")

    external_by_variant = {
        variant_id: str(items_by_adapter["snk_trades"][variant_id]["externalId"])
        for variant_id in wanted_variants
    }
    for variant_id in wanted_variants:
        if str(items_by_adapter["snk_price"][variant_id]["externalId"]) != external_by_variant[variant_id]:
            raise RuntimeError(f"SNK adapter identity mismatch for variant {variant_id}")
    wanted_external = set(external_by_variant.values())

    broad_rows = [
        json.loads(line)
        for line in harvest_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    selected_rows = [
        row for row in broad_rows if str(row.get("item_id") or "") in wanted_external
    ]
    selected_ids = [int(external_by_variant[variant_id]) for variant_id in wanted_variants]
    if len(selected_rows) != len(wanted_external):
        found = sorted(str(row.get("item_id") or "") for row in selected_rows)
        raise RuntimeError(
            f"completed SNK harvest does not contain each exact delta ID wanted={sorted(wanted_external)} found={found}"
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    subset_path = OUT_DIR / "binding_delta_snk_harvest.jsonl"
    subset_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in selected_rows),
        encoding="utf-8",
    )
    validated_rows, payload_sha = _validate_snk_harvest(subset_path, selected_ids)
    timestamps = [
        value
        for value in (_parse_datetime(row.get("fetched_at")) for row in selected_rows)
        if value is not None
    ]
    if len(timestamps) != len(selected_rows):
        raise RuntimeError("SNK delta harvest has missing fetched_at timestamps")
    started_at = min(timestamps)
    completed_at = max(timestamps)

    results: list[dict[str, Any]] = []
    for adapter in ("snk_trades", "snk_price"):
        items = [items_by_adapter[adapter][variant_id] for variant_id in wanted_variants]
        result: dict[str, Any] = {
            "adapter": adapter,
            "mode": "incr",
            "due": len(items),
            "processed": len(items),
            "checkpointed": 0,
            "ok": False,
        }
        if adapter == "snk_trades":
            ingest_report_path = None
            ingest_cmd = [
                PY,
                "-X",
                "utf8",
                "pipelines/ingest_snk_trades_sales.py",
                "--harvest",
                str(subset_path),
            ]
        else:
            ingest_report_path = OUT_DIR / "binding_delta_snk_price_ingest.json"
            ingest_cmd = [
                PY,
                "-X",
                "utf8",
                "pipelines/snk_market_data.py",
                "--ingest-jsonl",
                str(subset_path),
                "--condition",
                "trading_card_single_psa10",
                "--out",
                str(ingest_report_path),
            ]
        result["ingest"] = _run(ingest_cmd, timeout=180, dry_run=False)
        if result["ingest"].get("exit") != 0:
            result["error"] = f"{adapter}_delta_ingest_failed"
            results.append(result)
            break
        if adapter == "snk_price":
            ingest_summary = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
            result.update(_validate_snk_price_ingest(ingest_summary, selected_ids))
        checkpoint = record_successful_poll(
            adapter=adapter,
            mode="incr",
            items=items,
            payload={
                "contract": "snk_post_binding_delta_v1",
                "sourceHarvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
                "subsetHarvestSha256": hashlib.sha256(subset_path.read_bytes()).hexdigest(),
                "sourceHarvestRows": len(broad_rows),
                "rows": validated_rows,
                "ingest": result["ingest"],
            },
            started_at=started_at,
            completed_at=completed_at,
            payload_sha_by_external=payload_sha,
        )
        result.update(checkpoint)
        result["ok"] = int(result.get("checkpointed") or 0) == len(items)
        results.append(result)
        if not result["ok"]:
            break

    ok = len(results) == 2 and all(row.get("ok") is True for row in results)
    receipt = {
        "action": "commit-snk-binding-delta",
        "asOf": utc_now(),
        "ok": ok,
        "requestedAdapters": ["snk_trades", "snk_price"],
        "variantIds": wanted_variants,
        "externalIds": selected_ids,
        "sourceHarvest": str(harvest_path),
        "sourceHarvestSha256": hashlib.sha256(harvest_path.read_bytes()).hexdigest(),
        "sourceHarvestRows": len(broad_rows),
        "subsetHarvest": str(subset_path),
        "networkRequests": 0,
        "sourceHarvestNetworkRequests": len(broad_rows),
        "results": results,
    }
    output = OUT_DIR / "binding_delta_snk_refresh.json"
    output.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return receipt


def run_snk_trades(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int = 24,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_trades",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        shared_harvest=shared_harvest,
    )


def run_snk_price(
    items: list[dict[str, Any]],
    *,
    mode: str,
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int = 24,
    shared_harvest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run_snk_adapter(
        items,
        adapter="snk_price",
        mode=mode,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        shared_harvest=shared_harvest,
    )


def _pc_subset_map(
    items: list[dict[str, Any]], *, mode: str, label: str
) -> tuple[Path, list[dict[str, Any]]]:
    if not PC_MAP.is_file():
        raise RuntimeError(f"canonical PC map is missing: {PC_MAP}")
    requested = {int(item["variantId"]) for item in items}
    rows = [
        row
        for row in _jsonl_rows(PC_MAP)
        if int(row.get("variant_id") or 0) in requested
    ]
    rows_by_variant: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_variant.setdefault(int(row.get("variant_id") or 0), []).append(row)
    duplicates = sorted(
        variant_id for variant_id, matches in rows_by_variant.items() if len(matches) != 1
    )
    if duplicates:
        raise RuntimeError(f"canonical PC map has duplicate active variants: {duplicates[:20]}")
    by_variant = {
        variant_id: matches[0] for variant_id, matches in rows_by_variant.items()
    }
    missing = sorted(requested - set(by_variant))
    if missing or len(by_variant) != len(requested):
        raise RuntimeError(f"canonical PC map missing active variants: {missing[:20]}")
    external_id_mismatches = []
    for item in items:
        variant_id = int(item["variantId"])
        requested_external_id = str(item.get("externalId") or "").strip()
        mapped_external_id = str(by_variant[variant_id].get("pc_product_id") or "").strip()
        if not requested_external_id or requested_external_id != mapped_external_id:
            external_id_mismatches.append(variant_id)
    if external_id_mismatches:
        raise RuntimeError(
            "canonical PC map external ID mismatch for active variants: "
            f"{external_id_mismatches[:20]}"
        )
    ordered = [by_variant[int(item["variantId"])] for item in items]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"pc_map_{label}_{mode}.jsonl"
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in ordered),
        encoding="utf-8",
    )
    return path, ordered


def pc_daily_full_cycle_started_at(
    cycle_key: str, *, now: datetime | None = None
) -> datetime:
    """When the current ``daily_full`` refresh cycle started collecting.

    A tick interruption kills this process mid-sweep, so the anchor cannot be
    the process start: the next tick would refetch every page it already paid
    Cloudflare for. It is stamped once per cycle key (the chain run id, one per
    business date) and every later attempt reads the same instant back, so a
    page whose HTML was captured after it counts as already fetched this run.
    """

    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    key = str(cycle_key or "").strip()
    if not key:
        raise RuntimeError("daily_full PC refresh needs a cycle key")
    try:
        stamped = json.loads(PC_DAILY_FULL_CYCLE_STAMP.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        stamped = None
    if isinstance(stamped, dict) and str(stamped.get("cycle") or "") == key:
        started = _parse_datetime(stamped.get("startedAt"))
        if started is not None:
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return started.astimezone(timezone.utc)
    # review 2026-08-24: 5-6 collect workers run daily_full in one thread pool,
    # so this file is written concurrently. A torn read here is not harmless: it
    # reads back as "no cycle", re-anchors startedAt to now, and every page this
    # cycle already paid Cloudflare for looks unfetched again.
    _write_json_atomic(
        PC_DAILY_FULL_CYCLE_STAMP,
        {
            "cycle": key,
            "startedAt": moment.isoformat().replace("+00:00", "Z"),
        },
    )
    return moment


def pc_daily_full_record_refusal(cycle_key: str, *, started_at: datetime) -> int:
    """Count the refused ``daily_full`` sweeps of this cycle, including this one.

    Lives on the cycle stamp because every attempt is a fresh process: a counter
    in memory would reset with it and the first refusal would fall back forever.
    """

    key = str(cycle_key or "").strip()
    if not key:
        raise RuntimeError("daily_full PC refresh needs a cycle key")
    try:
        stamped = json.loads(PC_DAILY_FULL_CYCLE_STAMP.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        stamped = None
    if not isinstance(stamped, dict) or str(stamped.get("cycle") or "") != key:
        stamped = {
            "cycle": key,
            "startedAt": started_at.isoformat().replace("+00:00", "Z"),
        }
    refusals = int(stamped.get("refusedSweeps") or 0) + 1
    stamped["refusedSweeps"] = refusals
    _write_json_atomic(PC_DAILY_FULL_CYCLE_STAMP, stamped)
    return refusals


def pc_daily_full_run_anchor(
    refresh_policy: str,
    *,
    dry_run: bool,
    pc_items: list[dict[str, Any]],
    cycle_key: str,
) -> datetime | None:
    """The resume anchor, and the only place allowed to create the cycle stamp.

    ``run_collect`` sends ``refresh_policy="daily_full"`` for every V2 collect
    source (gemrate shards, snkrdunk, pricecharting) and they run in one thread
    pool, so without this gate five workers with zero PC pages race the one
    worker that has them for a file only the PC sweep reads.
    """

    if refresh_policy != "daily_full" or dry_run or not pc_items:
        return None
    return pc_daily_full_cycle_started_at(cycle_key)


def partition_local_pc_stock_pages(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    force_network: bool = False,
    refresh_policy: str = "sla_replay",
    run_started_at: datetime | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Use exact saved PC evidence when the artifact is still inside the SLA.

    ``sla_replay`` (operator ``stock`` / ``incr``): stock and incr both replay.
    Chrome only runs for missing / invalid / older-than-36h HTML. Classify still
    marks every PC stream due (``PC_REFRESH_DUE_HOURS = 0``); this function is
    the skip, not poll-mode.

    ``daily_full`` (the chain's daily source task, owner directive 2026-08-24):
    every stream's page is fetched fresh through CDP 9333 on every run. Only a
    page already captured during *this* cycle (``run_started_at``) replays, so
    an interrupted tick resumes instead of refetching. The 36h SLA is untouched
    -- it stays the acceptance/checkpoint gate, it just no longer decides
    whether to re-collect.

    ``fallback_replay``: after a fresh fetch failed (Cloudflare / timeout / CDP
    down), replay exactly what ``sla_replay`` would have replayed and label it
    ``fresh_fetch_failed_replay_local`` so the receipt never calls a fallback a
    fresh page. Nothing outside the SLA replays here either.

    ``force_network`` is operator catch-up: skip the SLA replay and CDP-fetch
    every exact page. Morning/nightly must not pass it for a whole-universe
    stock/incr. The single sanctioned automated exception is
    ``cmd_first_stock`` (v2 stage ``checkpoint-repair``), which forces only the
    streams ``missing_checkpoint_streams`` just returned;
    ``assert_force_network_scope`` keeps that limit in code, not in prose.
    """

    if refresh_policy not in PC_REFRESH_POLICIES:
        raise RuntimeError(f"unknown PC refresh policy: {refresh_policy!r}")
    selected = _unique_items(items, None)
    if force_network and selected and not dry_run:
        return (
            [],
            selected,
            {
                "adapter": "pc_local_stock_replay",
                "mode": mode,
                "processed": 0,
                "ok": True,
                "cursorAdvanced": False,
                "payloadShaByVariant": {},
                "rows": [],
                "networkReasons": {
                    str(int(item["variantId"])): "operator_force_network" for item in selected
                },
                "refreshPolicy": refresh_policy,
                "replayReasons": {},
            },
        )
    empty_report = {
        "adapter": "pc_local_stock_replay",
        "mode": mode,
        "processed": 0,
        "ok": True,
        "cursorAdvanced": False,
        "payloadShaByVariant": {},
        "rows": [],
        "networkReasons": {},
        "refreshPolicy": refresh_policy,
        "replayReasons": {},
    }
    if dry_run or not selected:
        return [], selected, empty_report
    if refresh_policy != "sla_replay":
        if run_started_at is None:
            raise RuntimeError(
                f"PC refresh policy {refresh_policy!r} needs run_started_at:"
                " without the cycle anchor a resume cannot be told from a stale page"
            )
        if run_started_at.tzinfo is None:
            run_started_at = run_started_at.replace(tzinfo=timezone.utc)
    _, map_rows = _pc_subset_map(selected, mode=mode, label="local-stock")
    from pc_psa10_price_derivation import validate_pc_psa10

    map_by_variant = {int(row["variant_id"]): row for row in map_rows}
    replayed: list[dict[str, Any]] = []
    network: list[dict[str, Any]] = []
    payload_sha_by_variant: dict[str, str] = {}
    evidence_times: list[datetime] = []
    evidence_rows: list[dict[str, Any]] = []
    network_reasons: dict[str, str] = {}
    replay_reasons: dict[str, str] = {}
    map_contract_errors: list[int] = []
    for item in selected:
        variant_id = int(item["variantId"])
        # audit P2-13: a bare map_by_variant[variant_id] made one absent map row
        # a KeyError that escaped _collect_mode_impl (which has no except at
        # all) and took the whole pricecharting task down into a 62 min backoff.
        row = map_by_variant.get(variant_id)
        if row is None:
            network.append(item)
            network_reasons[str(variant_id)] = "local_exact_map_row_missing"
            continue
        exact_price, reason = validate_pc_psa10(row)
        if exact_price is None:
            network.append(item)
            network_reasons[str(variant_id)] = reason
            continue
        html_field = str(row.get("html_path") or row.get("htmlPath") or "")
        if not html_field:
            # audit P2-13: an empty map field is a map contract error, not a
            # missing file -- and ROOT / "" resolves to ROOT itself, so stat()
            # used to succeed on a directory and read_bytes() raised
            # IsADirectoryError. Name it, keep the card on the network lane.
            map_contract_errors.append(variant_id)
            network.append(item)
            network_reasons[str(variant_id)] = "local_exact_map_html_path_empty"
            continue
        html_path = ROOT / html_field
        # A04 2026-08-23: this loop cost 33 s for 1238 pages on /mnt/c.
        # validate_pc_psa10 has just read and hashed the page (artifact_sha256);
        # is_file() + stat() + read_bytes() + sha256 here were a second pass
        # over every byte. One stat for the SLA clock, the validator's hash.
        try:
            html_stat = html_path.stat()
        except OSError:
            html_stat = None
        if html_stat is None or not stat_mod.S_ISREG(html_stat.st_mode):
            # audit P2-13: mirror the SLA branch above -- a missing artifact is
            # a refetch, never an exception out of the whole task.
            network.append(item)
            network_reasons[str(variant_id)] = "local_exact_html_missing"
            continue
        modified_at = datetime.fromtimestamp(html_stat.st_mtime, timezone.utc)
        if (_age_hours(modified_at) or 0) > SLA_HOURS:
            network.append(item)
            network_reasons[str(variant_id)] = "local_exact_html_exceeds_36h_sla"
            continue
        replay_reason = "local_exact_html_within_sla"
        if refresh_policy != "sla_replay":
            captured_this_run = run_started_at is not None and modified_at >= run_started_at
            if refresh_policy == "daily_full" and not captured_this_run:
                network.append(item)
                network_reasons[str(variant_id)] = "daily_full_refresh_due"
                continue
            replay_reason = (
                "fresh_page_captured_this_run"
                if captured_this_run
                else "fresh_fetch_failed_replay_local"
            )
        replay_reasons[str(variant_id)] = replay_reason
        replayed.append(item)
        payload_sha_by_variant[str(variant_id)] = str(
            exact_price.get("artifact_sha256")
            or hashlib.sha256(html_path.read_bytes()).hexdigest()
        )
        evidence_times.append(modified_at)
        evidence_rows.append(
            {
                "variantId": variant_id,
                "externalId": str(row.get("pc_product_id") or ""),
                "sourceUrl": str(row.get("pc_url") or ""),
                "htmlPath": str(html_path),
                "htmlSha256": payload_sha_by_variant[str(variant_id)],
                "explicitField": exact_price.get("field"),
                "sourceObservedDate": exact_price.get("observed_date"),
                "localArtifactModifiedAt": modified_at.isoformat().replace(
                    "+00:00", "Z"
                ),
                "reason": reason,
            }
        )
    report = {
        "adapter": "pc_local_stock_replay",
        "mode": mode,
        "processed": len(replayed),
        "ok": True,
        "cursorAdvanced": False,
        "payloadShaByVariant": payload_sha_by_variant,
        "evidenceAsOf": (
            max(evidence_times).isoformat().replace("+00:00", "Z")
            if evidence_times
            else None
        ),
        "evidenceFreshnessFloor": (
            min(evidence_times).isoformat().replace("+00:00", "Z")
            if evidence_times
            else None
        ),
        "rows": evidence_rows,
        "networkReasons": network_reasons,
        "refreshPolicy": refresh_policy,
        "runStartedAt": (
            run_started_at.isoformat().replace("+00:00", "Z")
            if run_started_at is not None
            else None
        ),
        # Why each replay happened, so a fallback after a refused fetch can
        # never read as a fresh page in the receipt.
        "replayReasons": replay_reasons,
        "fallbackReplays": sum(
            1
            for reason in replay_reasons.values()
            if reason == "fresh_fetch_failed_replay_local"
        ),
        # audit P2-13: an empty html_path in the PC map is a map contract
        # error. It must be named in the receipt, not hidden behind a generic
        # "the file is missing" refetch reason.
        "mapContractErrors": map_contract_errors,
    }
    return replayed, network, report


def _merge_pc_replay_reports(
    base: dict[str, Any], extra: dict[str, Any]
) -> dict[str, Any]:
    """One ``localStockReplay`` block covering the cycle replay + the fallback."""

    evidence_times = [
        stamp
        for stamp in (
            _parse_datetime(report.get(key))
            for report in (base, extra)
            for key in ("evidenceAsOf", "evidenceFreshnessFloor")
        )
        if stamp is not None
    ]
    merged = {
        **base,
        "processed": int(base.get("processed") or 0) + int(extra.get("processed") or 0),
        "ok": bool(base.get("ok")) and bool(extra.get("ok")),
        "payloadShaByVariant": {
            **(base.get("payloadShaByVariant") or {}),
            **(extra.get("payloadShaByVariant") or {}),
        },
        "rows": [*(base.get("rows") or []), *(extra.get("rows") or [])],
        "networkReasons": {
            **(base.get("networkReasons") or {}),
            **(extra.get("networkReasons") or {}),
        },
        "replayReasons": {
            **(base.get("replayReasons") or {}),
            **(extra.get("replayReasons") or {}),
        },
        "fallbackReplays": int(base.get("fallbackReplays") or 0)
        + int(extra.get("fallbackReplays") or 0),
        "mapContractErrors": [
            *(base.get("mapContractErrors") or []),
            *(extra.get("mapContractErrors") or []),
        ],
    }
    if evidence_times:
        merged["evidenceAsOf"] = (
            max(evidence_times).isoformat().replace("+00:00", "Z")
        )
        merged["evidenceFreshnessFloor"] = (
            min(evidence_times).isoformat().replace("+00:00", "Z")
        )
    return merged


def pc_child_attempted_failed_ids(started_at: datetime) -> list[int]:
    """Variants this child run actually opened and failed on.

    The child writes one ``results`` row per page it decided, so the rows are
    the only proof that a fetch happened at all. A page the sweep never reached
    did not have a fetch *fail*; covering it from yesterday's HTML would rebuild
    the exact bug R5 kills, so it must stay on the network lane. A report older
    than this run proves nothing about this sweep and counts as zero attempts.
    """

    try:
        payload = json.loads(PC_REFRESH_REPORT.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, dict):
        return []
    report_time = _parse_datetime(payload.get("asOf"))
    if report_time is None:
        return []
    if report_time.tzinfo is None:
        report_time = report_time.replace(tzinfo=timezone.utc)
    if report_time < started_at:
        return []
    failed: set[int] = set()
    for row in payload.get("results") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("status") or "") == "ok":
            continue
        try:
            failed.add(int(row.get("variant_id")))
        except (TypeError, ValueError):
            continue
    return sorted(failed)


def pc_fresh_fetch_fallback(
    network_items: list[dict[str, Any]],
    network_refresh: dict[str, Any],
    *,
    mode: str,
    run_started_at: datetime,
    cycle_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]] | None:
    """Cover a refused CDP sweep with local evidence, or refuse to cover it.

    The fallback is never wider than what ``sla_replay`` would have replayed on
    its own, so no gate moves: a page missing / invalid / past the 36h SLA still
    has no evidence and the sweep stays failed for everyone. ``None`` means the
    lane keeps its failure -- silence is not an option here.

    Three more things it refuses to cover (review 2026-08-24):
    * a sweep the tick interrupted -- the failure is what makes the next tick
      resume, so masking it turns the resume machinery into dead code;
    * a page the child never opened (``attemptedFailedVariantIds``) -- an
      unreached page did not have a fetch fail;
    * the first refusal of a cycle -- the chain gets a real retry at the sweep
      before anyone publishes yesterday's HTML.
    """

    if bool(network_refresh.get("childInterrupted")):
        return None
    attempted_failed = {
        int(variant_id)
        for variant_id in (network_refresh.get("attemptedFailedVariantIds") or [])
    }
    replayed, uncovered, replay_report = partition_local_pc_stock_pages(
        network_items,
        mode=mode,
        dry_run=False,
        refresh_policy="fallback_replay",
        run_started_at=run_started_at,
    )
    if uncovered or not replayed:
        return None
    replay_reasons = replay_report.get("replayReasons") or {}
    stale_cover = sorted(
        int(variant_id)
        for variant_id, reason in replay_reasons.items()
        if reason == "fresh_fetch_failed_replay_local"
    )
    if [vid for vid in stale_cover if vid not in attempted_failed]:
        # The child never opened these pages. They stay on the network lane so
        # the sweep stays not-ok and the next tick resumes it.
        return None
    if stale_cover:
        refusals = pc_daily_full_record_refusal(cycle_key, started_at=run_started_at)
        if refusals < PC_DAILY_FULL_FALLBACK_MIN_REFUSALS:
            return None
    degraded = dict(network_refresh)
    fresh_error = degraded.pop("error", None)
    fresh_error_class = degraded.pop("errorClass", None)
    degraded.pop("retryable", None)
    degraded.pop("retryAfterSeconds", None)
    degraded.update(
        {
            "ok": True,
            "processed": sum(
                1
                for reason in replay_reasons.values()
                if reason == "fresh_page_captured_this_run"
            ),
            # 2026-09-25: a tick-interrupted attempt's orphan child finished the
            # sweep, so every page was captured this run and nothing was covered
            # with older HTML. That is not a failed fetch; flagging it degraded
            # the source with 1640/1640 fresh pages.
            "freshFetchFailed": bool(stale_cover),
            "freshFetchError": fresh_error,
            "freshFetchErrorClass": fresh_error_class,
            "fallbackReplayVariantIds": sorted(
                int(variant_id)
                for variant_id, reason in replay_reasons.items()
                if reason == "fresh_fetch_failed_replay_local"
            ),
        }
    )
    return replayed, replay_report, degraded


def refresh_pc_pages(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    resume_report: Path | None,
    sleep_seconds: float | None,
    tabs: int | None,
    cdp_already_ensured: bool,
    bind_missing_ids: list[int] | None = None,
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    bind_missing_ids = sorted({int(v) for v in (bind_missing_ids or []) if int(v) > 0})
    report: dict[str, Any] = {
        "adapter": "pc_cdp_fresh_pages",
        "mode": mode,
        "processed": len(selected),
        "ok": True,
        "payloadShaByVariant": {},
        "bindMissingIds": bind_missing_ids,
    }
    if not selected and not bind_missing_ids:
        report["note"] = "no exact PC variants due"
        return report
    # Bind-only sweep (every exact page replayed from local stock): there is no
    # exact MAP subset, so the contract step below has no rows to hash.
    # 2026-08-22 attempt 15 died here with UnboundLocalError after 152/152.
    map_rows: list[dict[str, Any]] = []
    if selected:
        try:
            _, map_rows = _pc_subset_map(selected, mode=mode, label="refresh")
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
            return report
    ids_path = OUT_DIR / f"pc_variant_ids_{mode}.txt"
    ids_path.write_text(
        "\n".join(str(item["variantId"]) for item in selected) + "\n",
        encoding="ascii",
    )
    report["variantIdsPath"] = str(ids_path)
    bind_path = None
    if bind_missing_ids:
        bind_path = OUT_DIR / f"pc_bind_missing_{mode}.txt"
        bind_path.write_text(
            "\n".join(str(vid) for vid in bind_missing_ids) + "\n",
            encoding="ascii",
        )
        report["bindMissingPath"] = str(bind_path)
    if dry_run:
        report.update({"dryRun": True, "note": "exact PC variants selected; CDP was not executed"})
        return report
    if not WINDOWS_PY.is_file():
        report.update({"ok": False, "error": f"Windows backend Python missing: {WINDOWS_PY}"})
        return report

    # review 2026-08-24: single-flight on 9333. The daily_full sweep is 50-65
    # min, so the tick interrupts it on ordinary business dates and SIGKILLs
    # this process while the hidden Windows child keeps going. Launching a
    # second child then puts two sweeps on PriceCharting at once. An empty
    # attemptedFailedVariantIds is the honest answer here -- nothing was opened,
    # so the fallback may not cover a single page with yesterday's HTML.
    already_running = pc_child_alive_stamp()
    child_wait: dict[str, Any] | None = None
    if already_running is not None:
        # The old child is usually finishing, not stuck: wait it out inside a
        # bounded budget instead of failing the attempt blind.
        child_wait = pc_wait_for_child_exit()
        report["childWait"] = child_wait
        if child_wait.get("exited"):
            print(
                "[collect] 9333 child finished while waiting; proceeding: "
                + json.dumps(child_wait, ensure_ascii=False, sort_keys=True),
                file=sys.stderr,
                flush=True,
            )
            already_running = None
    if already_running is not None:
        report.update(
            {
                "ok": False,
                "error": PC_CHILD_ALREADY_RUNNING_CLASS,
                "errorClass": PC_CHILD_ALREADY_RUNNING_CLASS,
                "retryable": True,
                "retryAfterSeconds": pc_error_retry_after_seconds(
                    PC_CHILD_ALREADY_RUNNING_CLASS
                ),
                "childAlreadyRunning": already_running,
                "attemptedFailedVariantIds": [],
                "identityMissing": [],
                "coverageLoss": False,
            }
        )
        print(
            "[collect] 9333 child still running; refusing a second sweep: "
            + json.dumps(already_running, ensure_ascii=False, sort_keys=True),
            file=sys.stderr,
            flush=True,
        )
        return report

    started_at = datetime.now(timezone.utc)
    try:
        cmd = [
            str(WINDOWS_PY),
            "-X",
            "utf8",
            _windows_path(ROOT / "pipelines/pc_cdp_sold_refresh_win.py"),
            "--variant-ids-file",
            _windows_path(ids_path),
            "--limit",
            "0",
            "--no-ingest",
            "--cdp-port",
            str(CARDZ_CDP_PORT),
        ]
        # 唔傳就用 pc_cdp_sold_refresh_win.py 自己嗰兩個量返嚟嘅常數。呢度一旦寫死
        # 一個 default，嗰邊改幾多次都冇用 —— 舊版就係咁：refresher 寫住 politeness
        # sleep，但呢度硬塞 `--pc-sleep 4.0`，所以真正決定 993 張要跑幾耐嘅係呢一行。
        if sleep_seconds is not None:
            cmd.extend(["--sleep", str(max(0.0, float(sleep_seconds)))])
        if tabs is not None:
            cmd.extend(["--workers", str(max(1, int(tabs)))])
        if cdp_already_ensured:
            cmd.append("--cdp-already-ensured")
        if resume_report is None and PC_REFRESH_REPORT.is_file():
            try:
                previous = json.loads(PC_REFRESH_REPORT.read_text(encoding="utf-8-sig"))
            except (OSError, json.JSONDecodeError):
                previous = None
            if previous:
                from pc_cdp_sold_refresh_win import should_auto_resume_report

                if should_auto_resume_report(previous):
                    resume_report = PC_REFRESH_REPORT
                    report["autoResumeReport"] = str(PC_REFRESH_REPORT)
        if resume_report is not None:
            cmd.extend(["--resume-report", _windows_path(resume_report)])
        if bind_path is not None:
            cmd.extend(["--bind-missing-ids-file", _windows_path(bind_path)])
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"windows_path:{type(exc).__name__}:{exc}"})
        return report
    # audit item 10 / review 2026-08-24: the orchestrator SIGTERMs the whole
    # worker group at the tick deadline. Hold the signal until the child's step
    # is over so nothing captured is abandoned, and remember that it happened:
    # an interrupted sweep must keep its failure so the next tick resumes it
    # instead of publishing whatever local HTML happens to be inside the SLA.
    with _deferred_termination() as interrupted:
        report["run"] = _run_pc_child(
            cmd, timeout=pc_cdp_child_timeout_seconds(len(selected)), dry_run=False
        )
    if interrupted["signalled"]:
        report["childInterrupted"] = True
    report["childLog"] = report["run"].get("childLog")
    report["childLogTail"] = report["run"].get("childLogTail")
    if report["run"].get("exit") != 0 or not PC_REFRESH_REPORT.is_file():
        error_class = pc_child_error_class(report["run"].get("exit"))
        report.update(
            {
                "ok": False,
                "error": error_class,
                "errorClass": error_class,
                # Which pages this sweep actually opened and failed on: the only
                # ones a local replay may stand in for.
                "attemptedFailedVariantIds": pc_child_attempted_failed_ids(started_at),
                "retryable": pc_error_is_retryable(error_class),
                "retryAfterSeconds": pc_error_retry_after_seconds(error_class),
                # A Cloudflare storm is the provider refusing us, not a variant
                # we failed to bind and not coverage that disappeared.
                "identityMissing": [],
                "coverageLoss": False,
            }
        )
        return report
    try:
        source_report = json.loads(PC_REFRESH_REPORT.read_text(encoding="utf-8-sig"))
        if source_report.get("partial"):
            # `partial: true` is a crashed/stopped run's receipt. It is resume
            # material for the next attempt, never a completed sweep.
            raise RuntimeError("PC refresh report is partial")
        report_time = _parse_datetime(source_report.get("asOf"))
        if report_time is None or report_time < started_at:
            raise RuntimeError("PC refresh report is stale")
        expected = len(selected)
        batch = int(source_report.get("batch") or 0)
        # batch may exceed expected: bind ids resolved in this same child run
        # ride along. Unresolved bind ids (bindUnresolvedVariantIds) are an
        # identity-lane gap, not a fetch failure, so they never fail the sweep.
        if not (
            batch >= expected
            and int(source_report.get("ok") or 0) == batch
            and int(source_report.get("fail") or 0) == 0
            and int(source_report.get("cf") or 0) == 0
            and not source_report.get("missingRequestedVariantIds")
        ):
            raise RuntimeError("PC refresh report is incomplete")
        if source_report.get("bindUnresolvedVariantIds"):
            report["bindUnresolvedVariantIds"] = list(source_report["bindUnresolvedVariantIds"])
        payload_sha_by_variant: dict[str, str] = {}
        for row in map_rows:
            html_path = ROOT / str(row.get("html_path") or row.get("htmlPath") or "")
            if not html_path.is_file():
                raise RuntimeError(f"refreshed PC HTML missing for variant {row.get('variant_id')}")
            payload_sha_by_variant[str(int(row["variant_id"]))] = hashlib.sha256(
                html_path.read_bytes()
            ).hexdigest()
    except Exception as exc:  # noqa: BLE001
        report.update(
            {
                "ok": False,
                "error": f"pc_refresh_contract:{type(exc).__name__}:{exc}",
                "attemptedFailedVariantIds": pc_child_attempted_failed_ids(started_at),
            }
        )
        return report
    report.update(
        {
            "sourceReport": source_report,
            "payloadShaByVariant": payload_sha_by_variant,
        }
    )
    return report


def _pc_checkpoint_hashes(
    items: list[dict[str, Any]], refresh: dict[str, Any]
) -> dict[str, str]:
    by_variant = refresh.get("payloadShaByVariant") or {}
    return {
        str(item.get("externalId") or ""): str(by_variant[str(int(item["variantId"]))])
        for item in items
    }


def run_pc_ebay_sales(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    refresh: dict[str, Any],
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    report: dict[str, Any] = {
        "adapter": "pc_ebay_sales",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact PC/eBay sales variants due"
        return report
    if not refresh.get("ok"):
        report.update({"ok": False, "error": "fresh_pc_pages_unavailable"})
        return report
    try:
        map_path, _ = _pc_subset_map(selected, mode=mode, label="sales")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
        return report
    if dry_run:
        report.update({"dryRun": True, "map": str(map_path)})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    ingest_report_path = OUT_DIR / f"pc_ebay_sales_ingest_{mode}.json"
    command = [
        PY,
        "-X",
        "utf8",
        "pipelines/c11_pc_sold_ingest.py",
        "--map",
        str(map_path),
        "--report",
        str(ingest_report_path),
        "--write",
    ]
    try:
        with _db_writer_lease():
            report["run"] = _run(
                command, timeout=max(1200, 10 * len(selected)), dry_run=False
            )
            if report["run"].get("exit") != 0 or not ingest_report_path.is_file():
                raise RuntimeError("PC/eBay sales ingest failed")
            ingest = json.loads(ingest_report_path.read_text(encoding="utf-8-sig"))
            stats = ingest.get("stats") or {}
            if not (
                int(ingest.get("mapReadyHigh") or 0) == len(selected)
                and int(ingest.get("mapExistingVariant") or 0) == len(selected)
                and int(ingest.get("mapExactProductGateRejected") or 0) == 0
                and not ingest.get("missingVariantIds")
                and int(stats.get("cards_no_html") or 0) == 0
                and int(stats.get("cards_parse_fail") or 0) == 0
            ):
                raise RuntimeError("PC/eBay ingest report is incomplete")
            local_replay_ids = {
                int(value) for value in (refresh.get("localReplayVariantIds") or [])
            }
            replay_floor = _parse_datetime(
                (refresh.get("localStockReplay") or {}).get("evidenceFreshnessFloor")
            )
            checkpoint = record_successful_poll(
                adapter="pc_ebay_sales",
                mode=mode,
                items=selected,
                payload=ingest,
                started_at=started_at,
                payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
                # A stock replay is valid source evidence, but its checkpoint must
                # retain the artifact's real age instead of pretending it was
                # fetched at the time of this control-plane run.
                completed_at=replay_floor if local_replay_ids else None,
            )
            checkpoint["cursorAdvanced"] = True
            checkpoint["reused"] = len(local_replay_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_ebay_contract:{type(exc).__name__}:{exc}"})
        return report
    report.update({"map": str(map_path), "ingestReport": str(ingest_report_path), **checkpoint})
    return report


def run_en_price_ref(
    items: list[dict[str, Any]],
    *,
    mode: str,
    dry_run: bool,
    refresh: dict[str, Any],
) -> dict[str, Any]:
    selected = _unique_items(items, None)
    report: dict[str, Any] = {
        "adapter": "en_price_ref",
        "mode": mode,
        "due": len(items),
        "processed": len(selected),
        "checkpointed": 0,
        "ok": True,
    }
    if not selected:
        report["note"] = "no exact EN price-reference variants due"
        # Nothing to FETCH is not nothing to PRICE: the sales that arrived on
        # earlier ticks still have to become quotes, or every EN card whose
        # page was not due tonight goes stale and aborts the 100% contract.
        try:
            with _db_writer_lease():
                report["saleQuotes"] = _mint_sale_quotes("pricecharting", dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001
            report.update({"ok": False, "error": f"sale_quotes:{type(exc).__name__}:{exc}"})
        return report
    if not refresh.get("ok"):
        report.update({"ok": False, "error": "fresh_pc_pages_unavailable"})
        return report
    try:
        map_path, _ = _pc_subset_map(selected, mode=mode, label="price")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"pc_map:{type(exc).__name__}:{exc}"})
        return report
    if dry_run:
        report.update({"dryRun": True, "map": str(map_path)})
        return report

    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    plan_path = OUT_DIR / f"pc_price_plan_{mode}.json"
    derive_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/pc_psa10_price_derivation.py",
        "--map",
        str(map_path),
        "--out",
        str(plan_path),
        "--sources",
        "pricecharting",
    ]
    report["derive"] = _run(derive_cmd, timeout=max(1200, 10 * len(selected)), dry_run=False)
    if report["derive"].get("exit") != 0 or not plan_path.is_file():
        report.update({"ok": False, "error": "en_price_plan_failed"})
        return report
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
        planned_variants = {int(row["variantId"]) for row in (plan.get("rows") or [])}
        expected_variants = {int(item["variantId"]) for item in selected}
        if not (
            plan.get("contract") == "pc_psa10_current_price_v1"
            and int(plan.get("exactBindings") or 0) == len(selected)
            and int(plan.get("rejectedBindings") or 0) == 0
            and planned_variants == expected_variants
            and isinstance(plan.get("planSha256"), str)
            and len(plan["planSha256"]) == 64
        ):
            raise RuntimeError("EN price plan is incomplete for active variants")
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"en_price_plan_contract:{type(exc).__name__}:{exc}"})
        return report
    materialize_cmd = [
        PY,
        "-X",
        "utf8",
        "pipelines/pc_psa10_price_materialize.py",
        "--plan",
        str(plan_path),
        "--plan-sha256",
        str(plan["planSha256"]),
        "--map",
        str(map_path),
        "--write",
    ]
    try:
        with _db_writer_lease():
            report["materialize"] = _run(
                materialize_cmd,
                timeout=max(1200, 10 * len(selected)),
                dry_run=False,
            )
            if report["materialize"].get("exit") != 0:
                raise RuntimeError("EN price materialize failed")
            # The materialize above lands chart points only; the PRICE is minted
            # here from the PSA 10 sales.
            report["saleQuotes"] = _mint_sale_quotes("pricecharting", dry_run=False)
            local_replay_ids = {
                int(value) for value in (refresh.get("localReplayVariantIds") or [])
            }
            replay_floor = _parse_datetime(
                (refresh.get("localStockReplay") or {}).get("evidenceFreshnessFloor")
            )
            checkpoint = record_successful_poll(
                adapter="en_price_ref",
                mode=mode,
                items=selected,
                payload=plan,
                started_at=started_at,
                payload_sha_by_external=_pc_checkpoint_hashes(selected, refresh),
                completed_at=replay_floor if local_replay_ids else None,
            )
            checkpoint["cursorAdvanced"] = True
            checkpoint["reused"] = len(local_replay_ids)
    except Exception as exc:  # noqa: BLE001
        report.update({"ok": False, "error": f"checkpoint:{type(exc).__name__}:{exc}"})
        return report
    report.update({"map": str(map_path), "plan": str(plan_path), **checkpoint})
    return report


def _requested_adapters(adapters: list[str]) -> list[str]:
    allowed = list(CHECKPOINT_ADAPTERS)
    groups = {
        "all": set(allowed),
        "http": {a for a in allowed if ADAPTER_LANE[a] == "http"},
        "browser": {a for a in allowed if ADAPTER_LANE[a] == "browser"},
        "manual": {a for a in allowed if ADAPTER_LANE[a] == "manual"},
    }
    wanted = set(adapters)
    unknown = wanted - set(allowed) - set(groups)
    if unknown:
        raise ValueError(f"unknown adapters: {sorted(unknown)}")
    selected = {a for name in wanted for a in groups.get(name, {name})}
    return [adapter for adapter in allowed if adapter in selected]


def _adapter_runner(source_key: str):
    """Resolve one registry entry to the function that actually collects it."""
    spec = SOURCE_ADAPTERS.get(str(source_key))
    if not spec:
        return None
    runner = globals().get(str(spec.get("runner") or ""))
    return runner if callable(runner) else None


def dispatch_adapter(
    source_key: str, items: list[dict[str, Any]], **kwargs: Any
) -> dict[str, Any]:
    """The one dispatch point for every collected source.

    Adding a source used to mean editing the lane table plus a chain of
    ``if adapter in requested`` blocks here, and a source that missed one of
    them was silently skipped while the run still reported ok. The registry in
    collection_contract is now the only list; anything it does not name fails
    closed as ``source_not_registered``. Runner signatures differ (harvest,
    delay, refresh…), so each runner is handed only the keywords it declares.
    """
    runner = _adapter_runner(source_key)
    if runner is None:
        return {
            "adapter": str(source_key),
            "ok": False,
            "processed": 0,
            "error": SOURCE_NOT_REGISTERED,
            "errorClass": SOURCE_NOT_REGISTERED,
        }
    accepted = inspect.signature(runner).parameters
    return runner(items, **{k: v for k, v in kwargs.items() if k in accepted})


def _shared_snk_harvest(
    requested: list[str],
    due_by_adapter: dict[str, list[dict[str, Any]]],
    *,
    mode: str,
    limit: int | None,
    delay: float,
    workers: int,
    dry_run: bool,
) -> dict[str, Any] | None:
    """One SNKRDUNK harvest per collect run.

    When both SNK ingest consumers are requested, the union of their due exact
    IDs is fetched once and each lane ingests from the same immutable rows
    (previously each lane refetched).
    """
    if dry_run or not all(name in requested for name in SNK_SHARED_HARVEST_ADAPTERS):
        return None
    union_ids: list[int] = []
    seen_ids: set[int] = set()
    for adapter in SNK_SHARED_HARVEST_ADAPTERS:
        lane_active, _ = _partition_quarantined(adapter, due_by_adapter[adapter])
        try:
            _, lane_ids = _snk_selection(lane_active, limit)
        except Exception:  # noqa: BLE001
            # Selection errors belong to the lane report; fall back to
            # per-lane harvesting so the lane fails with its own error.
            return None
        for external_id in lane_ids:
            if external_id not in seen_ids:
                seen_ids.add(external_id)
                union_ids.append(external_id)
    if not union_ids:
        return None
    return _snk_harvest_once(
        union_ids, label="snk_shared", mode=mode, delay=delay, workers=workers
    )


def _acquire_adapter_leases(adapters: list[str], *, lease_scope: str | None = None):
    """Hold one MySQL advisory lease per adapter for the complete collection run."""
    load_env()
    conn = db()
    cur = conn.cursor()
    acquired: list[str] = []
    try:
        lease_roles = set(adapters)
        if lease_roles.intersection({"pc_ebay_sales", "en_price_ref"}):
            lease_roles.add("pc_cdp")
        lock_names: set[str] = set()
        for adapter in sorted(lease_roles):
            suffix = f":{lease_scope}" if lease_scope else ""
            lock_names.add(f"cardz:collect:{adapter}{suffix}"[:64])
            if adapter == "gemrate_pop" and not lease_scope:
                # The legacy/manual unsharded collector must be exclusive with
                # every V2 shard.  It takes all four shard locks; a V2 shard
                # takes exactly its own.  This preserves four-way V2 parallelism
                # without letting an old/manual process bypass the same host.
                lock_names.update(
                    f"cardz:collect:{adapter}:{scope}"[:64]
                    for scope in GEMRATE_PARALLEL_LEASE_SCOPES
                )
        for lock_name in sorted(lock_names):
            cur.execute("SELECT GET_LOCK(%s, 0) AS acquired", (lock_name,))
            row = cur.fetchone()
            value = row.get("acquired") if isinstance(row, dict) else row[0]
            if int(value or 0) != 1:
                raise RuntimeError(
                    f"adapter lease already held: {lock_name}; stop the duplicate collector/Chrome runner"
                )
            acquired.append(lock_name)
        # Only once every lock is held: from here the connection is idle for the
        # whole collection run, which is what let an orphan hold PC's leases for
        # 85 minutes on 2026-08-25.  The guard bounds that to LEASE_IDLE_TIMEOUT.
        guard = _LeaseSessionGuard(conn, label="adapter")
        return conn, acquired, guard
    except Exception:
        for lock_name in reversed(acquired):
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
        conn.close()
        raise


def _release_adapter_leases(
    conn, lock_names: list[str], guard: "_LeaseSessionGuard | None" = None
) -> None:
    try:
        if guard is not None:
            guard.stop()
        cur = conn.cursor()
        for lock_name in reversed(lock_names):
            cur.execute("SELECT RELEASE_LOCK(%s)", (lock_name,))
    finally:
        conn.close()


def pc_bind_missing_ids(
    reg: list[dict[str, Any]], requested: list[str], explicit_variants: set[int]
) -> list[int]:
    """Variants the PC child must try to bind (identity) on this call.

    Same 9333 dual-tab script does identity + cap. 576 universe cards have no
    numeric PC product id (mostly JA); they are bind_pc_or_ebay in the
    registry. Leaving this list empty made quote-only V2 skip them forever.

    An explicit variant scope (v2 ``checkpoint-repair`` / operator incr for
    named streams) repairs those streams only: the whole-universe bind sweep
    belongs to the lane's own call and ran twice more per repair on
    2026-08-23 A01 (2 x ~100 s of page fetches, one 429, zero proposals).
    """
    if explicit_variants:
        return []
    if not any(name in requested for name in ("pc_ebay_sales", "en_price_ref")):
        return []
    return sorted(
        {
            int(row["variantId"])
            for row in reg
            if row.get("adapter") == "bind_pc_or_ebay"
            and int(row.get("variantId") or 0) > 0
        }
    )


def _collect_mode_impl(
    *,
    mode: str,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float | None,
    pc_workers: int | None,
    variant_ids: list[int] | None = None,
    force_network: bool = False,
    refresh_policy: str = "sla_replay",
    refresh_cycle_key: str | None = None,
    rebuild_registry: bool = True,
    report_path: Path | None = None,
    work_scope: str | None = None,
    gemrate_workers: int = 1,
) -> dict[str, Any]:
    # A03 2026-08-23: the PC lane spent 34 s before its child spawned and the
    # receipt had no clock for it. Each phase below lands in the report.
    phase_started = time.monotonic()
    phase_seconds: dict[str, float] = {}

    def _mark(name: str) -> None:
        nonlocal phase_started
        now = time.monotonic()
        phase_seconds[name] = round(now - phase_started, 3)
        phase_started = now

    status = cmd_status(rebuild_registry=rebuild_registry)
    _mark("status")
    if not REGISTRY_PATH.is_file():
        raise RuntimeError("collection registry is missing; V2 registry barrier did not complete")
    reg = _jsonl_rows(REGISTRY_PATH)
    requested = _requested_adapters(adapters)
    # incr 只拉 cursor/due incremental。residual stock 用 `stock`。
    # 未完成 first stock（registry 有、checkpoint 無）用 `first-stock`，
    # 唔好混入 incr，亦唔好一次過拉晒  residual stockDue。
    modes = {"stock"} if mode == "stock" else {"incr"}
    explicit_variants = set(int(value) for value in (variant_ids or []))
    due_by_adapter = {
        adapter: sorted(
            (
                [
                    row
                    for row in reg
                    if row.get("adapter") == adapter
                    and int(row.get("variantId") or 0) in explicit_variants
                    and str(row.get("externalId") or "").strip()
                ]
                if explicit_variants
                else _due(reg, adapter, modes)
            ),
            key=lambda row: 0 if row["modeNeeded"] == "incr" else 1,
        )
        for adapter in requested
    }
    if explicit_variants:
        for adapter, rows in due_by_adapter.items():
            found = {int(row["variantId"]) for row in rows}
            missing = sorted(explicit_variants - found)
            if missing:
                raise RuntimeError(f"explicit {adapter} variants are not exact-bound: {missing}")
    selected_by_adapter = {
        adapter: _unique_items(rows, limit)
        for adapter, rows in due_by_adapter.items()
    }
    _mark("classify")
    results: list[dict[str, Any]] = []

    # `requested` keeps registry order, so the lanes still run gemrate_pop,
    # snk_trades, snk_price, snk_en_image — with the shared SNK harvest taken
    # once, immediately before the first SNK lane, exactly as before.
    snk_shared_harvest: dict[str, Any] | None = None
    snk_harvest_taken = False
    for source_key in [a for a in requested if ADAPTER_LANE.get(a) == "http"]:
        if source_key in SNK_SHARED_HARVEST_ADAPTERS and not snk_harvest_taken:
            snk_harvest_taken = True
            snk_shared_harvest = _shared_snk_harvest(
                requested,
                due_by_adapter,
                mode=mode,
                limit=limit,
                delay=delay,
                workers=workers,
                dry_run=dry_run,
            )
        results.append(
            dispatch_adapter(
                source_key,
                due_by_adapter[source_key],
                mode=mode,
                limit=limit,
                dry_run=dry_run,
                delay=delay,
                workers=workers,
                work_scope=work_scope,
                gemrate_workers=gemrate_workers,
                shared_harvest=snk_shared_harvest,
            )
        )

    _mark("httpLanes")
    pc_items_by_variant: dict[int, dict[str, Any]] = {}
    for adapter in ("pc_ebay_sales", "en_price_ref"):
        for item in selected_by_adapter.get(adapter, []):
            variant_id = int(item["variantId"])
            current = pc_items_by_variant.get(variant_id)
            if current is None:
                pc_items_by_variant[variant_id] = dict(item)
            elif item.get("modeNeeded") == "incr":
                # One fresh page serves both PC consumers. If either consumer
                # is incremental, this variant must take the network lane.
                pc_items_by_variant[variant_id] = {
                    **current,
                    "modeNeeded": "incr",
                }
    browser_bootstrap: dict[str, Any] = {
        "requested": bool(ensure_browser),
        "needed": bool(pc_items_by_variant),
        "ok": True,
    }
    cdp_already_ensured = False
    all_pc_items = list(pc_items_by_variant.values())
    bind_missing_ids = pc_bind_missing_ids(reg, requested, explicit_variants)
    pc_run_started_at = pc_daily_full_run_anchor(
        refresh_policy,
        dry_run=dry_run,
        pc_items=all_pc_items,
        cycle_key=refresh_cycle_key or "",
    )
    local_pc_items, network_pc_items, local_pc_report = partition_local_pc_stock_pages(
        all_pc_items,
        mode=mode,
        dry_run=dry_run,
        force_network=force_network,
        refresh_policy=refresh_policy,
        run_started_at=pc_run_started_at,
    )
    _mark("pcPartition")
    if local_pc_items and not network_pc_items and not bind_missing_ids:
        browser_bootstrap = {
            "requested": bool(ensure_browser),
            "needed": False,
            "ok": True,
            "reason": "all exact missing contracts replayed from local immutable HTML",
        }
    if network_pc_items or bind_missing_ids:
        if ensure_browser and not dry_run:
            browser_bootstrap = {
                "requested": True,
                "needed": True,
                **ensure_cdp(CARDZ_CDP_PORT),
            }
            if not bool(browser_bootstrap.get("ok")):
                raise RuntimeError(
                    "the singleton CARDZ CDP session could not be started; no PC adapter ran: "
                    + json.dumps(browser_bootstrap, ensure_ascii=False, sort_keys=True)
                )
            cdp_already_ensured = True
        network_pc_refresh = refresh_pc_pages(
            network_pc_items,
            mode=mode,
            dry_run=dry_run,
            resume_report=pc_resume_report,
            sleep_seconds=pc_sleep,
            tabs=pc_workers,
            cdp_already_ensured=cdp_already_ensured,
            bind_missing_ids=bind_missing_ids,
        )
    else:
        network_pc_refresh = {
            "adapter": "pc_cdp_fresh_pages",
            "mode": mode,
            "processed": 0,
            "ok": True,
            "payloadShaByVariant": {},
            "note": "no exact PC variants require network refresh",
        }
    if (
        refresh_policy == "daily_full"
        and network_pc_items
        and not dry_run
        and not bool(network_pc_refresh.get("ok"))
        and pc_run_started_at is not None
    ):
        # 9333 refused the sweep (Cloudflare / timeout / CDP down). Falling back
        # to the same within-SLA local evidence sla_replay would have used keeps
        # the board publishing without widening anything: a page past the SLA
        # still has no evidence and the lane still fails.
        fallback = pc_fresh_fetch_fallback(
            network_pc_items,
            network_pc_refresh,
            mode=mode,
            run_started_at=pc_run_started_at,
            cycle_key=str(refresh_cycle_key or ""),
        )
        if fallback is not None:
            fallback_items, fallback_report, network_pc_refresh = fallback
            local_pc_items = [*local_pc_items, *fallback_items]
            local_pc_report = _merge_pc_replay_reports(local_pc_report, fallback_report)
            print(
                "[collect] WARNING PC daily_full fell back to local HTML for "
                f"{int(fallback_report.get('fallbackReplays') or 0)} page(s): "
                f"{network_pc_refresh.get('freshFetchError')}",
                file=sys.stderr,
                flush=True,
            )
    _mark("pcNetworkRefresh")
    pc_refresh = {
        "adapter": "pc_page_acquisition",
        "mode": mode,
        "refreshPolicy": refresh_policy,
        "runStartedAt": (
            pc_run_started_at.isoformat().replace("+00:00", "Z")
            if pc_run_started_at is not None
            else None
        ),
        "processed": len(all_pc_items),
        "ok": bool(local_pc_report.get("ok")) and bool(network_pc_refresh.get("ok")),
        "localReplay": bool(all_pc_items) and not network_pc_items,
        "localReplayVariantIds": sorted(
            int(item["variantId"]) for item in local_pc_items
        ),
        "networkVariantIds": sorted(
            int(item["variantId"]) for item in network_pc_items
        ),
        "payloadShaByVariant": {
            **(local_pc_report.get("payloadShaByVariant") or {}),
            **(network_pc_refresh.get("payloadShaByVariant") or {}),
        },
        "localStockReplay": local_pc_report,
        "networkRefresh": network_pc_refresh,
    }
    for source_key in [a for a in requested if ADAPTER_LANE.get(a) == "browser"]:
        results.append(
            dispatch_adapter(
                source_key,
                selected_by_adapter[source_key],
                mode=mode,
                dry_run=dry_run,
                refresh=pc_refresh,
            )
        )

    _mark("pcAdapters")
    by_adapter = {str(result.get("adapter")): result for result in results}
    failed = [
        adapter
        for adapter in requested
        if adapter not in by_adapter or not bool(by_adapter[adapter].get("ok"))
    ]
    truncated = [
        adapter
        for adapter in requested
        # Quarantined items are known-partial skips, not truncation.
        if int(by_adapter.get(adapter, {}).get("processed") or 0)
        + int(by_adapter.get(adapter, {}).get("quarantined") or 0)
        != len(due_by_adapter[adapter])
    ]
    ok = not failed and not truncated
    processed_count = sum(int(result.get("processed") or 0) for result in results)
    inserted_count = sum(int(result.get("inserted") or 0) for result in results)
    checkpointed_count = sum(
        int(result.get("checkpointed") or 0) for result in results
    )
    reused_count = sum(int(result.get("reused") or 0) for result in results)
    downloaded_count = sum(
        int(result.get("downloaded") or 0) for result in results
    )
    failed_count = sum(
        int(result.get("failed") or (0 if result.get("ok") else 1))
        for result in results
    )
    quarantined_count = sum(
        int(result.get("quarantined") or 0) for result in results
    )
    snk_shared_summary = None
    if snk_shared_harvest is not None:
        snk_shared_summary = {
            "ok": bool(snk_shared_harvest.get("ok")),
            "idsPath": snk_shared_harvest.get("idsPath"),
            "harvestPath": snk_shared_harvest.get("harvestPath"),
            "requestedIds": len(snk_shared_harvest.get("requestedIds") or []),
            "rows": len(snk_shared_harvest.get("rowsById") or {}),
            "failedRows": len(snk_shared_harvest.get("failedById") or {}),
            "error": snk_shared_harvest.get("error"),
        }
    report = {
        "action": mode,
        "asOf": utc_now(),
        "ok": ok,
        "dryRun": dry_run,
        "limit": limit,
        "requestedAdapters": requested,
        "failedAdapters": failed,
        "truncatedAdapters": truncated,
        "processed": processed_count,
        "inserted": inserted_count,
        "checkpoint": checkpointed_count,
        "checkpointed": checkpointed_count,
        "reused": reused_count,
        "downloaded": downloaded_count,
        "failed": failed_count,
        "quarantined": quarantined_count,
        # R3: an interrupted child is named in the run report so the worker
        # receipt can carry it; the lane still fails, it just says why.
        "childInterrupted": _child_interrupted(results),
        "snkSharedHarvest": snk_shared_summary,
        "preStatusCounts": status.get("counts"),
        "phaseSeconds": phase_seconds,
        "pcRefresh": pc_refresh,
        "browserBootstrap": browser_bootstrap,
        "results": results,
        "browserBootstrapRequested": bool(ensure_browser),
        "leaseContract": COLLECT_LEASE_CONTRACT,
        "notes": [
            "All requested active exact-ID adapters are fail-closed.",
            "A successful source poll advances only that adapter and stream checkpoint.",
            "SNK EN image collection is exact-ID HTTP only and never opens Chrome.",
            "Missing PC contracts replay strict local HTML first; incremental PC/eBay and EN price reference share one fresh CDP page acquisition.",
            "SNK trades and price ingest one shared exact-ID harvest per run; the remote source is fetched once.",
            "Quarantined items (3+ consecutive failed runs) are skipped, reported per lane, and count as known-partial, never lane failure.",
        ],
    }
    load_env()
    conn = db()
    cur = conn.cursor()
    try:
        report["postFreshness"] = freshness_summary(cur, reg)
        report["freshness"] = report["postFreshness"]
    finally:
        conn.close()
    output = report_path or (LAST_STOCK if mode == "stock" else LAST_INCR)
    report["reportPath"] = str(output)
    _write_json_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def _collect_mode(
    *,
    mode: str,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float | None,
    pc_workers: int | None,
    variant_ids: list[int] | None = None,
    force_network: bool = False,
    refresh_policy: str = "sla_replay",
    refresh_cycle_key: str | None = None,
    rebuild_registry: bool = True,
    report_path: Path | None = None,
    lease_scope: str | None = None,
    gemrate_workers: int = 1,
) -> dict[str, Any]:
    requested = _requested_adapters(adapters)
    lease_conn, lock_names, lease_guard = _acquire_adapter_leases(
        requested, lease_scope=lease_scope
    )
    try:
        return _collect_mode_impl(
            mode=mode,
            adapters=adapters,
            limit=limit,
            dry_run=dry_run,
            delay=delay,
            workers=workers,
            ensure_browser=ensure_browser,
            pc_resume_report=pc_resume_report,
            pc_sleep=pc_sleep,
            pc_workers=pc_workers,
            variant_ids=variant_ids,
            force_network=force_network,
            refresh_policy=refresh_policy,
            refresh_cycle_key=refresh_cycle_key,
            rebuild_registry=rebuild_registry,
            report_path=report_path,
            work_scope=lease_scope,
            gemrate_workers=gemrate_workers,
        )
    finally:
        _release_adapter_leases(lease_conn, lock_names, lease_guard)


def cmd_stock(
    *,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float | None,
    pc_workers: int | None,
    variant_ids: list[int] | None = None,
    force_network: bool = False,
    refresh_policy: str = "sla_replay",
    refresh_cycle_key: str | None = None,
    rebuild_registry: bool = True,
    report_path: Path | None = None,
    lease_scope: str | None = None,
    gemrate_workers: int = 1,
) -> dict[str, Any]:
    return _collect_mode(
        mode="stock",
        adapters=adapters,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        ensure_browser=ensure_browser,
        pc_resume_report=pc_resume_report,
        pc_sleep=pc_sleep,
        pc_workers=pc_workers,
        variant_ids=variant_ids,
        force_network=force_network,
        refresh_policy=refresh_policy,
        refresh_cycle_key=refresh_cycle_key,
        rebuild_registry=rebuild_registry,
        report_path=report_path,
        lease_scope=lease_scope,
        gemrate_workers=gemrate_workers,
    )


def assert_force_network_scope(
    adapter: str, variant_ids: list[int], missing_rows: list[dict[str, Any]] | None
) -> None:
    """``force_network`` may only skip the SLA replay for checkpoint-less streams.

    ``partition_local_pc_stock_pages`` records the rule as prose: morning /
    nightly must not force a whole-universe fetch.  ``cmd_first_stock`` is the
    one automated caller allowed to force, and only for the streams
    ``missing_checkpoint_streams`` returned, so widening that call site fails
    here instead of quietly CDP-fetching every exact page.
    """

    allowed = {int(row["variantId"]) for row in (missing_rows or ())}
    widened = sorted({int(value) for value in variant_ids} - allowed)
    if not variant_ids or widened:
        raise RuntimeError(
            "force_network is limited to checkpoint-less streams: "
            f"adapter={adapter} forced={len(variant_ids)} allowed={len(allowed)} "
            f"widened={widened[:20]}"
        )


def cmd_first_stock(
    *,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float | None,
    pc_workers: int | None,
    force_network: bool = False,
) -> dict[str, Any]:
    """Collect only active registry streams that still have no checkpoint.

    2026-08-17: 08-16 激活 v2016 / v1034 之後，朝／夜鏈只跑 incr，
    first-stock 從未發生，daily-accept checkpoint gate 缺 1 條就成日唔出街。
    """
    cmd_status(rebuild_registry=True)
    registry = _jsonl_rows(REGISTRY_PATH)
    requested = _requested_adapters(adapters)
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        missing = missing_checkpoint_streams(cur, registry=registry)
    finally:
        conn.close()
    planned = {
        adapter: list(missing.get(adapter) or [])
        for adapter in requested
        if missing.get(adapter)
    }
    report: dict[str, Any] = {
        "action": "first-stock",
        "asOf": utc_now(),
        "requestedAdapters": requested,
        "missing": {
            adapter: [int(row["variantId"]) for row in rows]
            for adapter, rows in planned.items()
        },
        "ran": [],
        "ok": True,
    }
    if not planned:
        report["note"] = "nothing missing"
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return report

    browser_missing = [
        adapter for adapter in planned if ADAPTER_LANE.get(adapter) == "browser"
    ]
    if browser_missing and not dry_run:
        consolidate = subprocess.run(
            [PY, "-X", "utf8", str(ROOT / "pipelines" / "consolidate_pc_map.py"), "--write"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        report["consolidatePcMap"] = {
            "exit": consolidate.returncode,
            "stderrTail": (consolidate.stderr or consolidate.stdout or "")[-2000:],
        }
        if consolidate.returncode != 0:
            report["ok"] = False
            report["error"] = "consolidate_pc_map failed before browser first-stock"
            print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
            return report

    for adapter, rows in planned.items():
        variant_ids = sorted({int(row["variantId"]) for row in rows})
        if force_network:
            assert_force_network_scope(adapter, variant_ids, missing.get(adapter))
        sub = cmd_stock(
            adapters=[adapter],
            limit=limit,
            dry_run=dry_run,
            delay=delay,
            workers=workers,
            ensure_browser=ensure_browser and ADAPTER_LANE.get(adapter) == "browser",
            pc_resume_report=pc_resume_report,
            pc_sleep=pc_sleep,
            pc_workers=pc_workers,
            variant_ids=variant_ids,
            force_network=force_network,
        )
        ran = {
            "adapter": adapter,
            "variantIds": variant_ids,
            "ok": bool(sub.get("ok")),
            "error": sub.get("error"),
            "errorClass": pc_refresh_error_class(sub),
        }
        report["ran"].append(ran)
        if not sub.get("ok"):
            report["ok"] = False
            report["error"] = f"first-stock adapter={adapter} failed"
            # The V2 checkpoint-repair stage has to tell a refused sweep apart
            # from a failed one: the first defers, the second fails.
            report["errorClass"] = ran["errorClass"]
            break
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def cmd_incr(
    *,
    adapters: list[str],
    limit: int | None,
    dry_run: bool,
    delay: float,
    workers: int,
    ensure_browser: bool,
    pc_resume_report: Path | None,
    pc_sleep: float | None,
    pc_workers: int | None,
    variant_ids: list[int] | None = None,
    force_network: bool = False,
    refresh_policy: str = "sla_replay",
    refresh_cycle_key: str | None = None,
    rebuild_registry: bool = True,
    report_path: Path | None = None,
    lease_scope: str | None = None,
    gemrate_workers: int = 1,
) -> dict[str, Any]:
    return _collect_mode(
        mode="incr",
        adapters=adapters,
        limit=limit,
        dry_run=dry_run,
        delay=delay,
        workers=workers,
        ensure_browser=ensure_browser,
        pc_resume_report=pc_resume_report,
        pc_sleep=pc_sleep,
        pc_workers=pc_workers,
        variant_ids=variant_ids,
        force_network=force_network,
        refresh_policy=refresh_policy,
        refresh_cycle_key=refresh_cycle_key,
        rebuild_registry=rebuild_registry,
        report_path=report_path,
        lease_scope=lease_scope,
        gemrate_workers=gemrate_workers,
    )


def main() -> int:
    assert_runtime_root(ROOT)
    parser = argparse.ArgumentParser(description="CARDZ stock/incr collect control plane")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="freshness + due registry")
    p_status.add_argument("--no-rebuild", action="store_true")

    p_prune = sub.add_parser(
        "prune-checkpoints",
        help="drop market_ingest_checkpoint rows that map to no active exact stream",
    )
    p_prune.add_argument("--apply", action="store_true")

    def add_common(p):
        p.add_argument("--adapter", action="append", default=[], help="all|http|browser|manual|gemrate_pop|snk_trades|snk_price|snk_en_image|pc_ebay_sales|en_price_ref (repeatable)")
        p.add_argument("--limit", type=int, default=None, help="max exact ids per network adapter")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--delay", type=float, default=0.0, help="SNK per-worker delay; 0 = max concurrent")
        p.add_argument("--workers", type=int, default=24, help="SNK concurrent workers (API, not CDP)")
        p.add_argument("--ensure-browser", action="store_true", help="ensure the dedicated single CARDZ CDP session before PC path")
        p.add_argument("--pc-resume-report", type=Path, help="reuse successful exact-ID pages from one strict PC receipt")
        p.add_argument("--pc-sleep", type=float, help="override the refresher's calibrated per-tab pause")
        p.add_argument("--pc-workers", type=int, help="override the refresher's calibrated tab count")
        p.add_argument("--variant-id", action="append", type=int, default=[], help="force exact active variant only (repeatable)")
        p.add_argument("--force-network", action="store_true", help="operator catch-up: CDP-fetch exact PC pages even if local HTML is inside SLA")

    def add_refresh_policy(p):
        p.add_argument(
            "--refresh-policy",
            choices=("sla_replay", "daily_full"),
            default="sla_replay",
            help="daily_full: fetch every exact PC page fresh through CDP this cycle",
        )
        p.add_argument(
            "--refresh-cycle-key",
            default=None,
            help="daily_full resume anchor (the chain passes its run id)",
        )

    p_stock = sub.add_parser("stock", help="residual full pulls only")
    add_common(p_stock)
    add_refresh_policy(p_stock)
    p_first = sub.add_parser(
        "first-stock",
        help="first collect for registry streams that still have no checkpoint",
    )
    add_common(p_first)
    p_incr = sub.add_parser("incr", help="daily deltas for due exact ids")
    add_common(p_incr)
    add_refresh_policy(p_incr)
    p_resume_snk = sub.add_parser(
        "commit-snk-price-receipt",
        help="checkpoint an already successful SNK exact-ID harvest/ingest; no network",
    )
    p_resume_snk.add_argument("--receipt", type=Path, default=LAST_INCR)
    p_commit_delta = sub.add_parser(
        "commit-snk-binding-delta",
        help="ingest exact variants from one already-completed SNK harvest; no network",
    )
    p_commit_delta.add_argument("--harvest", type=Path, required=True)
    p_commit_delta.add_argument("--variant-id", action="append", type=int, required=True)

    args = parser.parse_args()
    adapters = args.adapter if getattr(args, "adapter", None) else []
    if not adapters:
        adapters = ["all"]

    def _dispatch() -> int:
        report = None
        if args.cmd == "status":
            cmd_status(rebuild_registry=not args.no_rebuild)
        elif args.cmd == "prune-checkpoints":
            cmd_prune_checkpoints(apply=args.apply)
        elif args.cmd == "stock":
            report = cmd_stock(adapters=adapters, limit=args.limit, dry_run=args.dry_run, delay=args.delay, workers=args.workers, ensure_browser=args.ensure_browser, pc_resume_report=args.pc_resume_report, pc_sleep=args.pc_sleep, pc_workers=args.pc_workers, variant_ids=args.variant_id, force_network=args.force_network, refresh_policy=args.refresh_policy, refresh_cycle_key=args.refresh_cycle_key)
        elif args.cmd == "first-stock":
            report = cmd_first_stock(adapters=adapters, limit=args.limit, dry_run=args.dry_run, delay=args.delay, workers=args.workers, ensure_browser=args.ensure_browser, pc_resume_report=args.pc_resume_report, pc_sleep=args.pc_sleep, pc_workers=args.pc_workers, force_network=args.force_network)
        elif args.cmd == "incr":
            report = cmd_incr(adapters=adapters, limit=args.limit, dry_run=args.dry_run, delay=args.delay, workers=args.workers, ensure_browser=args.ensure_browser, pc_resume_report=args.pc_resume_report, pc_sleep=args.pc_sleep, pc_workers=args.pc_workers, variant_ids=args.variant_id, force_network=args.force_network, refresh_policy=args.refresh_policy, refresh_cycle_key=args.refresh_cycle_key)
        elif args.cmd == "commit-snk-price-receipt":
            report = cmd_commit_snk_price_receipt(receipt_path=args.receipt)
        elif args.cmd == "commit-snk-binding-delta":
            report = cmd_commit_snk_binding_delta(
                harvest_path=args.harvest.resolve(),
                variant_ids=args.variant_id,
            )
        else:
            raise SystemExit(f"unknown command: {args.cmd}")
        return 0 if report is None or report.get("ok") else 1

    if args.cmd in COLLECT_E2E_LEASE_COMMANDS:
        from operator_control import operator_e2e_lease

        with operator_e2e_lease(f"collect:{args.cmd}"):
            return _dispatch()
    return _dispatch()


if __name__ == "__main__":
    raise SystemExit(main())
