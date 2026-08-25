#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Windows-host CDP refresh for PriceCharting sold HTML.

Runs on Windows Python so Playwright talks to local Chrome :9333 without WSL networking weirdness.
Then optionally runs C11 sold ingest via WSL venv.

一個 Chrome、一個 profile、一個 cookie jar，但唔止一個 tab。舊版係單 tab 串行
再加 `--sleep 4.0`：實測 102 頁用咗 8 分 50 秒 = 5.2 s/頁，其中 **4.0 s 係
sleep**，真正 goto + content + parse 得 1.2 s。全量 993 張就係 86 分鐘，入面
66 分鐘係喺度瞓。嗰 4 秒冇任何量度撐住 —— runbook 只寫「politeness」。

而家：`--workers` 條 tab 共用同一個 CDP session（cookie / CF clearance 都係同
一份，所以唔會因為多開 tab 而變成「新訪客」），加一條**共用**嘅 backoff —— 任
何一條 tab 食到 429 / CF，全部 tab 一齊停低，唔係淨係嗰條慢返。HTML parse
（validate_pc_psa10）掉落 thread pool，唔准塞住 event loop 拖低所有 tab。

**分頁唔係樽頸，速率先係。** 同一批 40 張逐個設定量（2026-08-12）：

    分頁/sleep   每頁      429   推算 993 張
    4 / 1.0s     2.78s     3     46 分鐘
    3 / 1.0s     2.18s     2     36 分鐘
    2 / 1.5s     1.99s     1     33 分鐘
    2 / 3.0s     2.03s     0     34 分鐘   ← 2026-08-12 用呢個
    6 / 8.0s     2.19s     1     36 分鐘
    4 / 4.8s     2.16s     1     36 分鐘

加分頁反而慢：食一次 429 就全部分頁一齊停 30/60/120 秒，賺嘅嘢蝕晒。乾淨上限
大約 0.5 goto/s，即係 993 張 ≈ 34 分鐘 —— 呢個係 Cloudflare 個閘，唔係腳本慢。

2026-08-15：incr 唔再為新鮮 HTML 開 Chrome（見 collect_control partition）。
剩低過期頁用 in-page `fetch()`（1 個 document，唔係 `page.route` 擋資源）。
9333 小樣本：fetch 12/12 @3s、8/8 @1.5s、8/8 @1.0s、12/12 兩張並行，sold≈30、
PSA10 過、0 次 429。預設改 2 tab + fetch + 1.5s。CF/短頁先 fallback `goto`。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from html import unescape
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from pricecharting_cf_session import _is_cf  # noqa: E402
from pc_psa10_price_derivation import validate_pc_psa10  # noqa: E402

MAP = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
OUT = ROOT / "data/runtime/operator/collect/pc_cdp_refresh_report.json"
PY_WSL = "wsl.exe"
BACKOFF_LADDER = (30.0, 60.0, 120.0)
# 呢兩個數係量返嚟嘅，唔係估；calibration 全表見 docs/COLLECTION_RUNBOOK.md。
# 唔好淨係睇住「加分頁 = 快」就改大 —— 實測 4 分頁比 2 分頁**慢**，因為每食一次
# 429 就要全部分頁一齊停 30/60/120 秒，賺嘅嘢蝕晒。
PC_TABS = 2
PC_SLEEP_SECONDS = 1.5
PC_TRANSPORT = "fetch"
IN_PAGE_FETCH_JS = """async (target) => {
    const response = await fetch(target, {
        credentials: "include",
        signal: AbortSignal.timeout(60000),
    });
    const text = await response.text();
    return {
        status: response.status,
        text,
        retryAfter: response.headers.get("retry-after"),
    };
}"""
PAGE_DECISION_STALL_SECONDS = 300.0
CONNECT_OVER_CDP_TIMEOUT_MS = 15_000
CDP_SETUP_SECONDS = 30.0
HARD_STALL_KILLER_SECONDS = 360.0
# 每個 run 一個 stamp。舊版全部 run 共用 `pc_cdp_progress.stamp`：一個手跑撞正
# 日更，新 run 一 touch，另一個 run 個 killer 就永遠見唔到 stale；反方向一個舊
# killer 見到 stale 就照 `taskkill /F` 一個唔關佢事嘅 PID（Windows 幾分鐘就會
# 重用 PID）。killer 只准睇返自己嗰個 stamp，而且殺之前要驗返 PID 身份。
PROGRESS_STAMP_DIR = Path(
    os.environ.get("PC_PROGRESS_STAMP_DIR")
    or (ROOT / "data/runtime/operator/collect")
)
STALL_KILLER_IDENTITY_TOKEN = "pc_cdp_sold_refresh_win.py"
# 每 N 個 page decision 落一次 partial report（tmp + os.replace）。舊版全程只喺
# watchdog stall 同收工兩個位寫 report，中間任何一種死法都係一個字都冇留低，
# 下一轉冇得 resume，成 batch 由零再燒一次 Cloudflare 額度。
PARTIAL_REPORT_EVERY = max(1, int(os.environ.get("PC_PARTIAL_REPORT_EVERY", "10") or 10))
# 連續 N 版 Cloudflare challenge / 403 就當風暴，停手交返俾上游 backoff。
# 2026-08-22 一轉燒咗 13 次 attempt 直到 TERMINAL，全程冇一個閘叫停。
PC_CF_STORM_THRESHOLD = max(1, int(os.environ.get("PC_CF_STORM_THRESHOLD", "8") or 8))
EXIT_CF_STORM = 4
EXIT_INTERRUPTED = 3
# 由 main() 揀：鏈入面跑（--cdp-already-ensured）就淨係驗身份，Chrome 生死由
# launcher preflight 同 ChromeCdpWatchdog 擁有。手跑仍然照開。
CDP_IDENTITY_ONLY = {"value": False}
# 邊個 status 由邊個 counter 記住。撤銷一個判死嗰陣要減返啱嗰幾個 —— 呢個表存在
# 嘅原因就係曾經「加嘅時候加兩個、減嘅時候減錯一個」，令 fail 少報咗一個。
FAILURE_COUNTERS = {
    "rate_limited": ("fail", "rateLimited"),
    "cf_or_fail": ("cf",),
    "server_error": ("fail",),
}
DEFAULT_FAILURE_COUNTERS = ("fail",)
RETRYABLE_STATUSES = ("rate_limited", "cf_or_fail", "server_error")
CONTENT_RACE_TEXT = "page is navigating and changing the content"
CONTENT_RACE_ATTEMPTS = 5


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def canonical_url_from_html(value: str) -> str:
    patterns = (
        r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',
        r'<link[^>]+href=["\']([^"\']+)["\'][^>]+rel=["\']canonical["\']',
    )
    for pattern in patterns:
        match = re.search(pattern, value, re.I)
        if match:
            return unescape(match.group(1)).rstrip("/")
    return ""


def url_key(value: str) -> str:
    return unescape(str(value or "")).rstrip("/")


def load_resume_report(path: Path, cdp_port: int) -> dict:
    """Load a --resume-report file, or {} when it cannot be trusted.

    A resume report is bookkeeping about what a previous run already proved,
    never data: ignoring a bad one only costs fresh fetches. 2026-08-24 a
    test-fixture state leak wrote the production report, and the old raise on
    contract mismatch parked the whole PC lane for the night (the chain
    retried into the same poisoned file five times).
    """

    try:
        previous = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        print(f"resume report ignored (unreadable): {path} ({exc})", flush=True)
        return {}
    try:
        contract_ok = (
            isinstance(previous, dict)
            and previous.get("singleBrowserSession") is True
            and int(previous.get("fallbackBrowsers", -1)) == 0
            and int(previous.get("cdpPort") or 0) == cdp_port
        )
    except (TypeError, ValueError):
        contract_ok = False
    if not contract_ok:
        print(
            "resume report ignored: not produced by the strict single-browser "
            f"contract on port {cdp_port}: {path}",
            flush=True,
        )
        return {}
    return previous


def resume_entry_stale_reason(prior: dict, row: dict) -> str | None:
    """None when the sidecar on disk still proves this exact product and the
    recorded fetch; otherwise why the entry is stale. Reuse stays exactly as
    strict as before -- but a stale entry is discarded so its variant
    re-fetches fresh, instead of aborting the whole lane."""

    html_path = ROOT / str(row.get("html_path") or row.get("htmlPath") or "")
    html = html_path.read_text(encoding="utf-8", errors="replace") if html_path.is_file() else ""
    if len(html) <= 5000:
        return "sidecar_missing_or_truncated"
    title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    title = (title_match.group(1) if title_match else "").strip()
    if _is_cf(title, html):
        return "sidecar_is_cf_challenge"
    product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
    actual_product_id = product_match.group(1) if product_match else ""
    if actual_product_id != str(row.get("pc_product_id") or "").strip():
        return "product_id_mismatch"
    if canonical_url_from_html(html) != url_key(row.get("pc_url")):
        return "canonical_url_mismatch"
    try:
        recorded_len = int(prior.get("len") or -1)
    except (TypeError, ValueError):
        recorded_len = -1
    if recorded_len != len(html):
        return "recorded_len_mismatch"
    return None


async def stable_page_content(page, watchdog: dict[str, float]) -> str:
    """Read the current document after a same-tab redirect finishes.

    ``goto(..., wait_until='domcontentloaded')`` can return for the first
    document immediately before PriceCharting replaces it. Playwright refuses
    ``page.content()`` during that tiny navigation window. That is not a bad
    card or a provider failure, so wait on the same tab and read the same
    response instead of discarding the whole 993-page batch.
    """

    for attempt in range(CONTENT_RACE_ATTEMPTS):
        try:
            return await page.content()
        except Exception as exc:  # noqa: BLE001
            if (
                CONTENT_RACE_TEXT not in str(exc).lower()
                or attempt + 1 >= CONTENT_RACE_ATTEMPTS
            ):
                raise
            watchdog["beat"] = time.monotonic()
            await page.wait_for_timeout(250)
    raise AssertionError("stable_page_content exhausted without returning")


def title_from_html(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    return unescape((match.group(1) if match else "")).strip()


async def load_via_fetch(page, url: str) -> tuple[int | None, str, str, str | None]:
    payload = await page.evaluate(IN_PAGE_FETCH_JS, url)
    html = str((payload or {}).get("text") or "")
    code = (payload or {}).get("status")
    retry_after = (payload or {}).get("retryAfter")
    return (
        int(code) if code is not None else None,
        html,
        title_from_html(html),
        str(retry_after) if retry_after else None,
    )


async def load_via_goto(
    page, url: str, watchdog: dict[str, float]
) -> tuple[int | None, str, str, str | None]:
    response = await page.goto(url, wait_until="domcontentloaded", timeout=120000)
    code = response.status if response is not None else None
    retry_after = response.headers.get("retry-after") if response is not None else None
    html = await stable_page_content(page, watchdog)
    title = (await page.title()).strip()
    return code, html, title, retry_after


def ensure_cdp(port: int = 9333, *, identity_only: bool | None = None) -> None:
    """Verify the singleton CARDZ session; inside the chain do not start Chrome.

    9333 had three owners (launcher preflight, the ChromeCdpWatchdog task, and
    this script) and they recycled each other mid sweep. In a chain run the
    lifecycle belongs to the first two, so this call degrades to
    ``-IdentityOnly``. Hand runs still start Chrome, and PC_ENSURE_CDP_FULL=1
    forces the full path for a one-off repair.
    """
    from cdp_identity import require_session_ready

    only = CDP_IDENTITY_ONLY["value"] if identity_only is None else bool(identity_only)
    if os.environ.get("PC_ENSURE_CDP_FULL") == "1":
        only = False
    ps1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
    cmd = [
        "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1),
        "-Port", str(port),
    ]
    if only:
        cmd.append("-IdentityOnly")
    subprocess.run(cmd, check=False)
    require_session_ready(port)


def progress_stamp_path(pid: int | None = None) -> Path:
    """Per-run stamp path. A killer only ever watches the PID it was spawned for."""

    owner = os.getpid() if pid is None else int(pid)
    return PROGRESS_STAMP_DIR / f"pc_cdp_progress.{owner}.stamp"


def touch_progress_stamp() -> None:
    stamp = progress_stamp_path()
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(utc_now(), encoding="utf-8")


# --- durable partial report -------------------------------------------------
# 死法唔止一種：exception、Ctrl-C、Task Scheduler 收工送 CTRL_CLOSE、SIGTERM。
# 每一種都要留低一份「做到邊」嘅 receipt，`partial: true` + `stop_reason`，
# 落 disk 用 tmp + os.replace，半路俾人 kill 都唔會撕爛個 JSON。
_REPORT_STATE: dict = {
    "batch": 0,
    "results": [],
    "base": {},
    "decisions": 0,
    "armed": False,
    "complete": False,
    "terminal": False,
}


def write_report_atomic(payload: dict, path: Path | None = None) -> None:
    target = OUT if path is None else path
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temporary, target)


def register_report_state(
    *, batch: int, results: list, base: dict | None = None
) -> None:
    _REPORT_STATE.update(
        {
            "batch": int(batch),
            "results": results,
            "base": dict(base or {}),
            "armed": True,
            "complete": False,
            "terminal": False,
        }
    )


def tally_results(results: list) -> dict:
    out = {"ok": 0, "fail": 0, "cf": 0, "rateLimited": 0}
    for row in results or []:
        status = str(row.get("status") or "")
        if status == "ok":
            out["ok"] += 1
            continue
        for key in FAILURE_COUNTERS.get(status, DEFAULT_FAILURE_COUNTERS):
            out[key] += 1
    return out


def partial_report_payload(stop_reason: str) -> dict:
    results = list(_REPORT_STATE.get("results") or [])
    payload = dict(_REPORT_STATE.get("base") or {})
    payload.update(tally_results(results))
    payload.update(
        {
            "asOf": utc_now(),
            "partial": True,
            "stop_reason": str(stop_reason),
            "batch": int(_REPORT_STATE.get("batch") or 0),
            "results": results,
        }
    )
    return payload


def write_partial_report(stop_reason: str, *, terminal: bool = True) -> bool:
    """Persist what this run already proved. Never raises: it runs in death paths."""

    if not _REPORT_STATE.get("armed"):
        # `--help`, a bad flag, or a crash before the batch was known must not
        # overwrite the previous run's usable receipt with an empty one.
        return False
    if not _REPORT_STATE.get("results"):
        # Armed but proved zero pages (dual-tab guard, exception or signal
        # right after register_report_state): an empty partial would clobber
        # the previous run's usable receipt and there is nothing to resume.
        # Guarded HERE so every exit path (finally, except, signal) obeys it.
        return False
    if _REPORT_STATE.get("complete"):
        return False
    if terminal and _REPORT_STATE.get("terminal"):
        return False
    try:
        write_report_atomic(partial_report_payload(stop_reason))
    except Exception:  # noqa: BLE001
        return False
    if terminal:
        _REPORT_STATE["terminal"] = True
    return True


def install_partial_report_signals() -> None:
    """SIGTERM / Windows SIGBREAK (CTRL_BREAK, CTRL_CLOSE) still leave a receipt."""

    def _handler(signum, _frame):
        write_partial_report(f"signal_{int(signum)}")
        os._exit(EXIT_INTERRUPTED)

    for name in ("SIGTERM", "SIGBREAK"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _handler)
        except (OSError, ValueError):
            continue


# The detached killer runs as `python -c HARD_STALL_KILLER_CODE`, so it cannot
# import this module. Keeping it as one module constant means the test execs the
# exact shipped source instead of a copy that can drift.
HARD_STALL_KILLER_CODE = """import os
import subprocess
import time


def process_cmdline(pid):
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_Process -Filter 'ProcessId=%d').CommandLine"
                % int(pid),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return ""
    return (proc.stdout or "").strip()


def identity_ok(cmdline, token):
    # A bare PID is not an identity: Windows reuses PIDs within minutes, and the
    # old killer would taskkill /F whatever program inherited the number.
    text = (cmdline or "").lower()
    if not text:
        return False
    return "python" in text and token.lower() in text


def should_kill(age, limit, cmdline, token):
    if age <= limit:
        return False
    return identity_ok(cmdline, token)


def watch(parent, stamp, limit, token):
    while True:
        time.sleep(15)
        cmdline = process_cmdline(parent)
        if not identity_ok(cmdline, token):
            return 0
        try:
            age = time.time() - os.path.getmtime(stamp)
        except OSError:
            age = limit + 1
        if should_kill(age, limit, cmdline, token):
            os.system("taskkill /F /PID %d" % parent)
            return 3
"""


def spawn_hard_stall_killer(
    limit_seconds: float = HARD_STALL_KILLER_SECONDS,
) -> subprocess.Popen:
    """Independent process: kill this PID if *its own* progress stamp goes stale.

    The in-process stall thread cannot fire if Playwright holds the GIL on a
    wedged CDP websocket. A detached python -c does not share that GIL.

    Two hardenings over the 2026-08-22 shape: the stamp is per-run
    (``pc_cdp_progress.<pid>.stamp``, never the shared global one), and the PID
    is re-identified from its Win32 command line before any taskkill, so a
    reused PID belonging to another program is left alone.
    """

    parent = os.getpid()
    stamp = str(progress_stamp_path(parent))
    code = HARD_STALL_KILLER_CODE + (
        "\nraise SystemExit(watch("
        f"{parent}, {stamp!r}, {float(limit_seconds)!r}, "
        f"{STALL_KILLER_IDENTITY_TOKEN!r}))\n"
    )
    popen_kwargs: dict = {
        "args": [sys.executable, "-X", "utf8", "-c", code],
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200  # DETACHED | NEW_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(**popen_kwargs)


def new_stall_watchdog_state(*, batch: int, results: list, limit: float = PAGE_DECISION_STALL_SECONDS) -> dict:
    now = time.monotonic()
    return {
        "beat": now,
        "pageDecisionBeat": now,
        "limit": float(limit),
        "done": False,
        "batch": batch,
        "results": results,
    }


def mark_page_decision(state: dict, *, now: float | None = None) -> None:
    """Record that a tab reached a per-card status (ok / fail / requeue)."""

    clock = time.monotonic() if now is None else now
    state["pageDecisionBeat"] = clock
    state["beat"] = clock
    touch_progress_stamp()
    _REPORT_STATE["decisions"] = int(_REPORT_STATE.get("decisions") or 0) + 1
    if _REPORT_STATE["decisions"] % PARTIAL_REPORT_EVERY == 0:
        write_partial_report("in_progress", terminal=False)


def stall_watchdog_should_fire(state: dict, *, now: float | None = None) -> bool:
    """True when no card has reached a status decision within limit seconds.

    Liveness beats — CF 5s polls, shared backoff sleeps, dequeue — must not
    count. Two tabs share one watchdog dict: one hung Playwright call used
    to keep `beat` fresh (or leave gather() blocked) while zero HTML landed.
    """

    clock = time.monotonic() if now is None else now
    limit = float(state.get("limit") or 0.0)
    if limit <= 0:
        return False
    progress = state.get("pageDecisionBeat")
    if progress is None:
        progress = state.get("beat") or 0.0
    return (clock - float(progress)) > limit


def should_auto_resume_report(
    previous: dict,
    *,
    now: datetime | None = None,
    max_age_seconds: float = 6 * 3600,
) -> bool:
    """Reuse a crashed run's exact-ok pages; never skip a finished full batch."""

    as_of = str(previous.get("asOf") or "").strip()
    if not as_of:
        return False
    try:
        stamped = datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    clock = now or datetime.now(timezone.utc)
    if (clock - stamped.astimezone(timezone.utc)).total_seconds() > max_age_seconds:
        return False
    ok = int(previous.get("ok") or 0)
    batch = int(previous.get("batch") or 0)
    if (
        previous.get("partial")
        or previous.get("watchdogStall")
        or previous.get("sessionError")
    ):
        # `partial: true` is written by every death path (exception, Ctrl-C,
        # SIGTERM/SIGBREAK, CF storm) and by the every-10-pages flush. It is a
        # crashed run's receipt, never a finished sweep.
        return True
    return ok > 0 and batch > 0 and ok < batch


def arm_stall_watchdog(state: dict) -> None:
    """Turn a wedged CDP session into a bounded, visible failure.

    Playwright protocol calls such as page.content()/page.title() accept no
    timeout, so one dropped CDP response can park the run forever while the
    caller's subprocess timeout scales with batch size (hours). V2 also
    heartbeats every 10s for as long as the subprocess lives, so a zombie
    looks healthy. Stall on page *decisions*, persist a partial report, and
    hard-exit 3 so the next incr can `--resume-report` that receipt.
    """

    def _watch() -> None:
        while not state.get("done"):
            time.sleep(15.0)
            if not stall_watchdog_should_fire(state):
                continue
            limit = float(state.get("limit") or 0.0)
            try:
                payload = partial_report_payload("watchdog_stall")
                payload.update(
                    {
                        "watchdogStall": True,
                        "stallLimitSeconds": limit,
                        "batch": state.get("batch"),
                        "results": state.get("results"),
                    }
                )
                write_report_atomic(payload)
            except Exception:  # noqa: BLE001
                pass
            print(
                f"WATCHDOG_STALL no page decision for {limit:.0f}s; exit 3",
                flush=True,
            )
            os._exit(3)

    threading.Thread(target=_watch, daemon=True, name="stall-watchdog").start()


async def run_fetch_pool_with_pages(
    pending: list[dict],
    *,
    pages: list,
    sleep_seconds: float,
    challenge_wait: float,
    watchdog: dict,
    results: list[dict],
    start_index: int,
    batch_size: int,
    transport: str = PC_TRANSPORT,
    cf_storm_threshold: int | None = None,
) -> dict:
    """揸 `tabs` 條 tab 同時抽，共用一條 backoff。

    `throttle` 係共用嘅：一條 tab 食到 429 / CF，`until` 一推，全部 tab 落到
    下一頁之前都會等。舊版嗰個 `backoff_level` 係單線程獨有嘅，照搬落多 tab
    就變成「其餘 N-1 條照衝」—— 即係越撞越快，一定會俾人封。

    429 / CF 會**擺返落隊尾重試**，唔係當場判死。呢個唔係「容錯」，係做完件事：
    上游一 lane fail-closed，991 張成功嘅頁一齊唔入庫、checkpoint 一步都唔郁。
    實測 2026-08-11 全量 993 張：ok 991、429 兩張 → `inserted 0, checkpointed 0`。
    993 張入面撞到一兩次 429 幾乎係必然（實測率 0.2%），即係無人睇住嘅話呢條
    lane 日日都會咁死。而個共用 backoff 本來就已經等咗 30/60/120 秒 —— 等完之後
    唔重試返嗰張卡，等於白等。擺落隊尾係最疏嘅間隔（成隊行晒先輪到佢）。

    上游 5xx 一樣要重試。2026-08-12 第二轉全量：429 全部重試成功（rateLimited 0），
    但 **10 版一次過回 HTTP 500**（同一個 5189 bytes error page，vid 1009–1093），
    而嗰 10 條 URL 40 分鐘之前先啱啱成功過 —— 純粹上游一陣間唔得。舊版將佢跌落
    `product_id_mismatch`（500 個頁冇 product-id，identity 梗係唔過），而個 status
    係終局判決，於是 983 版好頁又一次全部作廢。5xx 唔用共用 backoff：佢係單版嘢壞，
    唔係成個 IP 俾人限速。
    """
    out: dict = {
        "ok": 0,
        "fail": 0,
        "cf": 0,
        "rateLimited": 0,
        "sessionError": None,
        "cfStorm": False,
    }
    queue: asyncio.Queue = asyncio.Queue()
    for index, row in enumerate(pending):
        queue.put_nowait((index, row, 0))
    throttle = {"level": 0, "until": 0.0}
    # Cloudflare 風暴斷路器：連續 N 版 challenge / 403 就唔好再撞。個 requeue
    # ladder 本身係為「993 版入面撞到一兩次」設計，唔係為「成個 IP 俾人封」——
    # 2026-08-22 就係咁樣一路重試燒到 TERMINAL，冇一個閘叫停。
    storm_threshold = (
        PC_CF_STORM_THRESHOLD
        if cf_storm_threshold is None
        else max(1, int(cf_storm_threshold))
    )
    storm = {"streak": 0, "tripped": False}
    counter = {"done": 0}
    retried: list[dict] = []

    async def worker(page, tab_index: int) -> None:
        while True:
            if storm["tripped"]:
                return
            try:
                index, row, attempt = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            watchdog["beat"] = time.monotonic()
            counter["done"] += 1
            position = start_index + counter["done"]
            url = unescape(str(row.get("pc_url") or ""))
            html_rel = row.get("html_path") or row.get("htmlPath")
            if not url or not html_rel:
                out["fail"] += 1
                results.append({"variant_id": row.get("variant_id"), "status": "missing"})
                continue
            target = ROOT / html_rel
            target.parent.mkdir(parents=True, exist_ok=True)
            while True:
                remaining = throttle["until"] - time.monotonic()
                if remaining <= 0:
                    break
                watchdog["beat"] = time.monotonic()
                await asyncio.sleep(min(remaining, 5.0))
            try:
                if transport == "fetch":
                    code, html, title, retry_after = await load_via_fetch(page, url)
                    blocked_preview = _is_cf(title, html) if html else True
                    fetch_ok = code == 200 and len(html) > 5000 and not blocked_preview
                    retryable = code == 429 or (code is not None and code >= 500)
                    if not fetch_ok and not retryable:
                        code, html, title, retry_after = await load_via_goto(
                            page, url, watchdog
                        )
                else:
                    code, html, title, retry_after = await load_via_goto(
                        page, url, watchdog
                    )
            except Exception as exc:  # noqa: BLE001
                out["fail"] += 1
                results.append(
                    {
                        "variant_id": row.get("variant_id"),
                        "status": "navigation_error",
                        "error": f"{type(exc).__name__}:{exc}",
                    }
                )
                mark_page_decision(watchdog)
                continue
            expected_product_id = str(row.get("pc_product_id") or "").strip()
            product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
            actual_product_id = product_match.group(1) if product_match else ""
            canonical_url = canonical_url_from_html(html)
            blocked = _is_cf(title, html) if html else True
            challenge_resolved = False
            if code == 403 and blocked and challenge_wait > 0:
                deadline = time.monotonic() + challenge_wait
                while time.monotonic() < deadline:
                    await page.wait_for_timeout(5000)
                    watchdog["beat"] = time.monotonic()
                    html = await stable_page_content(page, watchdog)
                    title = (await page.title()).strip()
                    product_match = re.search(r'\bproduct-id=["\'](\d+)["\']', html, re.I)
                    actual_product_id = product_match.group(1) if product_match else ""
                    canonical_url = canonical_url_from_html(html)
                    blocked = _is_cf(title, html) if html else True
                    if (
                        len(html) > 5000
                        and not blocked
                        and actual_product_id == expected_product_id
                        and canonical_url == url_key(url)
                        and page.url == url
                    ):
                        challenge_resolved = True
                        break
            loose = bool(row.get("looseHtml"))
            identity_ok = (
                (not blocked and len(html) > 5000)
                if loose
                else (
                    bool(expected_product_id)
                    and actual_product_id == expected_product_id
                    and canonical_url == url_key(url)
                )
            )
            # 多 tab 之後 pid 唔再夠獨特 —— 兩條 tab 唔會撞同一張卡，但一齊寫
            # 同一個 `.pid.next` 名就會互相踩。加 tab index 收返。
            temporary = target.with_name(f".{target.name}.{os.getpid()}.{tab_index}.next")
            await asyncio.to_thread(
                temporary.write_text, html, encoding="utf-8", errors="replace"
            )
            validation_row = dict(row)
            validation_row.pop("htmlPath", None)
            validation_row["html_path"] = str(temporary)
            validation_row["notes"] = ""
            # 300 KB HTML 全 parse 一次，實測百幾 ms。留喺 event loop 度就等於
            # 全部 tab 排住隊等佢，tab 開幾多都冇用。
            if loose:
                exact_price, exact_reason = None, "loose_html"
                explicit_psa10_ok = True
            else:
                exact_price, exact_reason = await asyncio.to_thread(
                    validate_pc_psa10, validation_row
                )
                explicit_psa10_ok = exact_price is not None
            if (
                (code == 200 or challenge_resolved)
                and len(html) > 5000
                and not blocked
                and identity_ok
                and explicit_psa10_ok
            ):
                os.replace(temporary, target)
                out["ok"] += 1
                status = "ok"
            else:
                temporary.unlink(missing_ok=True)
                if code == 429:
                    status = "rate_limited"
                elif blocked:
                    status = "cf_or_fail"
                elif code is not None and code >= 500:
                    # 上游 5xx 唔係「我哋張 map 錯」，係佢哋伺服器嗰陣唔得。分開一個
                    # status 嚟講，係因為舊版將佢跌落 product_id_mismatch（500 個頁
                    # 冇 product-id，identity 一定唔過），而 product_id_mismatch 係
                    # 終局判決 —— 即係上游打個乞嚏就報「我哋認錯咗卡」。
                    status = "server_error"
                elif identity_ok and not explicit_psa10_ok:
                    status = "explicit_psa10_missing"
                else:
                    status = "product_id_mismatch" if not identity_ok else "http_or_content_fail"
                for key in FAILURE_COUNTERS.get(status, DEFAULT_FAILURE_COUNTERS):
                    out[key] += 1
            results.append({
                "variant_id": row.get("variant_id"),
                "status": status,
                "code": code,
                "len": len(html),
                "title": title[:80],
                "expectedProductId": expected_product_id or None,
                "actualProductId": actual_product_id or None,
                "actualCanonicalUrl": canonical_url or None,
                "explicitPsa10": explicit_psa10_ok,
                "explicitPsa10Reason": exact_reason,
                "retryAfter": retry_after,
                "challengeResolved": challenge_resolved,
            })
            mark_page_decision(watchdog)
            print(
                f"[{position}/{batch_size}] {status} vid={row.get('variant_id')} "
                f"len={len(html)} code={code} tab={tab_index}",
                flush=True,
            )
            if status == "cf_or_fail" or code == 403 or blocked:
                storm["streak"] += 1
                if storm["streak"] >= storm_threshold and not storm["tripped"]:
                    storm["tripped"] = True
                    out["cfStorm"] = True
                    out["cfStormStreak"] = storm["streak"]
                    print(
                        f"PC_CF_STORM {storm['streak']} consecutive Cloudflare/403"
                        f" pages (threshold {storm_threshold}); stopping the sweep",
                        flush=True,
                    )
            else:
                storm["streak"] = 0
            if status in RETRYABLE_STATUSES:
                if status == "server_error":
                    # 5xx 係單版嘢壞，唔係成個 IP 俾人限速 —— 停晒全部分頁冇意思，
                    # 而且 10 版 500 × 3 次重試 × 30/60/120s 會白白蝕半個鐘。擺落
                    # 隊尾等成隊行完先再試，本身已經係最疏嘅間隔。
                    delay = 0.0
                else:
                    delay = BACKOFF_LADDER[min(throttle["level"], len(BACKOFF_LADDER) - 1)]
                    throttle["level"] += 1
                    throttle["until"] = max(throttle["until"], time.monotonic() + delay)
                requeued = attempt + 1 <= len(BACKOFF_LADDER)
                if requeued:
                    # 呢一筆唔算數：撤返啱先加落 out 同 results 嗰個判死，擺返落隊尾。
                    results.pop()
                    for key in FAILURE_COUNTERS.get(status, DEFAULT_FAILURE_COUNTERS):
                        out[key] -= 1
                    counter["done"] -= 1
                    retried.append({"variant_id": row.get("variant_id"), "attempt": attempt + 1, "status": status})
                    queue.put_nowait((index, row, attempt + 1))
                print(
                    f"{f'backoff {delay:.0f}s (all tabs)' if delay else 'no backoff (upstream 5xx)'}"
                    f" after {status} vid={row.get('variant_id')}"
                    f"{f'; requeued attempt {attempt + 2}' if requeued else '; giving up'}",
                    flush=True,
                )
                continue
            if status != "ok":
                continue
            throttle["level"] = 0
            if sleep_seconds > 0:
                await asyncio.sleep(sleep_seconds)

    await asyncio.gather(*(worker(page, index) for index, page in enumerate(pages)))
    out["retries"] = retried
    return out


async def run_fetch_pool(
    pending: list[dict],
    *,
    cdp_port: int,
    tabs: int,
    **kwargs,
) -> dict:
    """開 CDP、備妥 `tabs` 條 tab，然後交俾上面條 pool 行。

    分開兩層淨係為咗一件事：上面條 pool 入面嘅 queue／backoff／requeue 算術，可以
    用假 page 直接測（見 scripts/test_pc_refresh_requeue.py）。呢層有 Playwright，
    測唔到；嗰層冇，測得到。
    """
    from cdp_identity import require_session_ready

    async with async_playwright() as playwright:
        async def attach_tabs():
            require_session_ready(cdp_port)
            browser = await playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{cdp_port}",
                timeout=CONNECT_OVER_CDP_TIMEOUT_MS,
            )
            if not browser.contexts:
                raise RuntimeError("dedicated CARDZ Chrome has no browser context")
            context = browser.contexts[0]
            price_pages = [
                page
                for page in context.pages
                if "pricecharting.com" in (page.url or "")
            ]
            pool = price_pages[:tabs]
            for extra in price_pages[tabs:]:
                await extra.close()
            while len(pool) < tabs:
                pool.append(await context.new_page())
            for page in pool:
                page.set_default_timeout(120000)
                if "pricecharting.com" not in (page.url or ""):
                    await page.goto(
                        "https://www.pricecharting.com/",
                        wait_until="domcontentloaded",
                        timeout=min(120000, int(CDP_SETUP_SECONDS * 1000)),
                    )
            return pool

        # 唔好諗住 `page.route` 擋走圖／css 嚟慳額度：試過，會反效果。一版產品頁
        # 向 www.pricecharting.com 打 31 個 request（15 圖 / 6 script / 4 css /
        # 2 manifest / 2 xhr / 1 fetch / 1 document），睇落擋走 21 個就可以行快
        # 三倍。實際係同一個設定（2 分頁 / 3.0s）、隔 3 分鐘背對背行兩轉：
        # 唔擋 40/40 全清 2.05 s/頁，擋咗 38/40 兩次 429 3.69 s/頁。Cloudflare
        # 見到「瀏覽器」淨係攞 HTML 唔攞 css／圖，直接當你係 bot。要扮足全套。
        last_exc: Exception | None = None
        pool = None
        for attach_attempt in range(2):
            try:
                pool = await asyncio.wait_for(attach_tabs(), timeout=CDP_SETUP_SECONDS)
                break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if attach_attempt == 0:
                    ensure_cdp(cdp_port)
                    continue
        if pool is None:
            return {
                "ok": 0,
                "fail": 0,
                "cf": 0,
                "rateLimited": 0,
                "retries": [],
                "sessionError": (
                    f"single_cdp_connect:{type(last_exc).__name__}:{last_exc}"
                ),
            }
        kwargs.setdefault("transport", PC_TRANSPORT)
        out = await run_fetch_pool_with_pages(pending, pages=pool, **kwargs)
        # 收工剩返一條 tab，同單 tab 年代嘅 session 狀態一模一樣。
        for extra in pool[1:]:
            try:
                await extra.close()
            except Exception:  # noqa: BLE001
                pass
    return out


def _run_loose_jobs(jobs: list[dict], *, cdp_port: int) -> dict:
    """2-tab fetch for console/index/product HTML that is not yet a bound map row."""

    if not jobs:
        return {"ok": 0, "fail": 0, "cf": 0, "rateLimited": 0, "sessionError": None, "retries": []}
    rows: list[dict] = []
    for i, job in enumerate(jobs):
        html_path = Path(str(job["html_path"]))
        if not html_path.is_absolute():
            html_path = ROOT / html_path
        rel = html_path.resolve().relative_to(ROOT.resolve()).as_posix()
        rows.append(
            {
                "variant_id": int(job.get("variant_id") or -(i + 1)),
                "pc_url": job["url"],
                "html_path": rel,
                "htmlPath": rel,
                "pc_product_id": "",
                "looseHtml": True,
            }
        )
    results: list[dict] = []
    watchdog = new_stall_watchdog_state(batch=len(rows), results=results)
    arm_stall_watchdog(watchdog)
    try:
        return asyncio.run(
            run_fetch_pool(
                rows,
                cdp_port=cdp_port,
                tabs=PC_TABS,
                sleep_seconds=PC_SLEEP_SECONDS,
                challenge_wait=120.0,
                watchdog=watchdog,
                results=results,
                start_index=0,
                batch_size=len(rows),
            )
        )
    finally:
        watchdog["done"] = True


def bind_missing_ids_dual_tab(variant_ids: list[int], *, cdp_port: int) -> dict:
    """Add-card (PC identity) uses the same 2-tab 9333 script as sold cap.

    SNK exact is not a substitute. Prefetch console + survivor product pages
    with PC_TABS=2, then let identity discover write from the local HTML.
    """

    import pc_identity_discover as disc
    import rebuild_036 as R

    scoped = sorted({int(v) for v in variant_ids if int(v) > 0})
    report = {"requested": len(scoped), "targets": 0, "written": 0, "ok": True}
    if not scoped:
        return report
    conn = R.connect(R.DAILY_CREDENTIALS_ENV)
    try:
        generation = disc._latest_generation(conn)
        targets = disc.select_targets(conn, generation, 1000, "", 0, "*", scoped)
    finally:
        conn.close()
    report["targets"] = len(targets)
    if not targets:
        return report

    index_jobs = []
    for tcg, category in disc.CATEGORY_BY_TCG.items():
        path = disc._console_index_path(tcg)
        if not path.is_file() or path.stat().st_size < 5000:
            index_jobs.append(
                {
                    "url": f"https://www.pricecharting.com/category/{category}",
                    "html_path": path,
                }
            )
    _run_loose_jobs(index_jobs, cdp_port=cdp_port)
    indexes = {
        tcg: disc.load_console_index(tcg, allow_fetch=False, timeout_s=90)
        for tcg in disc.CATEGORY_BY_TCG
    }

    slug_by_row: list[tuple[dict, str]] = []
    slugs: list[str] = []
    for row in targets:
        tcg = str(row.get("tcg_code") or "")
        slug, _why = disc.match_console(
            str(row.get("set_name") or row.get("canonical_name") or ""),
            str(row.get("card_language") or ""),
            indexes.get(tcg) or {},
        )
        if slug:
            slug_by_row.append((row, slug))
            if slug not in slugs:
                slugs.append(slug)

    pending = [(slug, 0) for slug in slugs]
    seen: set[tuple[str, int]] = set()
    while pending:
        jobs = []
        nxt: list[tuple[str, int]] = []
        for slug, cursor in pending:
            key = (slug, cursor)
            if key in seen:
                continue
            seen.add(key)
            path = disc._console_page_path(slug, cursor)
            url = f"https://www.pricecharting.com/console/{slug}"
            if cursor:
                url += f"?cursor={cursor}&when=none&sort="
            if not path.is_file() or path.stat().st_size < 5000:
                jobs.append({"url": url, "html_path": path})
            nxt.append((slug, cursor))
        if jobs:
            _run_loose_jobs(jobs, cdp_port=cdp_port)
        more: list[tuple[str, int]] = []
        for slug, cursor in nxt:
            path = disc._console_page_path(slug, cursor)
            if not path.is_file():
                continue
            page_rows = disc.parse_console_rows(
                path.read_text(encoding="utf-8", errors="replace")
            )
            if len(page_rows) >= disc.CONSOLE_PAGE_SIZE:
                more.append((slug, cursor + disc.CONSOLE_PAGE_SIZE))
        pending = more

    product_jobs = []
    seen_pid: set[str] = set()
    for row, slug in slug_by_row:
        index = indexes.get(str(row.get("tcg_code") or "")) or {}
        for cand_slug, judged_set, _via in disc.console_candidates(row, index):
            listings = disc.console_rows(
                cand_slug, allow_fetch=False, timeout_s=90, delay=0.0
            )
            for listing in listings:
                ok, _reason = disc.judge_listing(row, listing, judged_set)
                if not ok:
                    continue
                pid = str(listing["pid"])
                vid = int(row["variant_id"])
                page_path = disc.PAGES_DIR / f"{vid}_{pid}.html"
                if page_path.is_file() and page_path.stat().st_size > 5000:
                    continue
                key = f"{vid}:{pid}"
                if key in seen_pid:
                    continue
                seen_pid.add(key)
                product_jobs.append(
                    {
                        "url": listing["url"]
                        if str(listing["url"]).startswith("http")
                        else f"https://www.pricecharting.com{listing['url']}",
                        "html_path": page_path,
                        "variant_id": vid,
                    }
                )
    if product_jobs:
        _run_loose_jobs(product_jobs, cdp_port=cdp_port)

    from types import SimpleNamespace

    written = 0
    for tcg in sorted(disc.CATEGORY_BY_TCG):
        tcg_ids = [int(row["variant_id"]) for row in targets if row.get("tcg_code") == tcg]
        if not tcg_ids:
            continue
        code = disc.cmd_pc_identity_discover(
            SimpleNamespace(
                write=True,
                generation=generation,
                tcg=tcg,
                language="*",
                min_pop=1000,
                limit=0,
                delay=0.0,
                allow_repoint=False,
                timeout=90,
                no_fetch=True,
                credentials_env=None,
                variant_ids=tcg_ids,
                report_sink=[],
            )
        )
        if code != 0:
            report["ok"] = False
            report["error"] = f"pc_identity_discover:{tcg}:{code}"
            return report
        written += len(tcg_ids)
    report["written"] = written
    return report


def partition_requested(
    exact_requested: set[int], bind_ids: list[int], selected_ids: set[int]
) -> tuple[list[int], list[int]]:
    """Split what the MAP could not serve into two very different things.

    ``missing_requested``: exact-ID variants the caller asked for that have no
    MAP row -- a real acquisition failure, fails the run.
    ``bind_unresolved``: bind-list variants (no PC id by construction) that the
    bind step could not resolve this run -- an identity-lane gap, reported but
    never a fetch failure. 2026-08-22: 13 attempts exited 1 with 650/650 pages
    OK because 378 unresolvable bind ids were counted as missing_requested.
    """
    bind_only = {int(v) for v in bind_ids} - exact_requested
    missing_requested = sorted(exact_requested - selected_ids)
    bind_unresolved = sorted(bind_only - selected_ids)
    return missing_requested, bind_unresolved


def select_refresh_rows(rows: list[dict], exact_requested: set[int]) -> list[dict]:
    """MAP rows the sold refresh may fetch: the caller's exact ids, nothing else.

    Bind ids never ride along. The bind step writes proposals (manual_review
    rows + the proposal ledger), never a canonical MAP row, so a MAP row that
    carries a bind id is a leftover of a rejected / manual_review identity.
    Until 2026-08-23 those leftovers were merged into ``requested_ids`` and 57
    such pages were re-fetched on every child run (A01 lane 58, A01
    checkpoint-repair 61 x2 with a 429, A02 lane 57 -- ~100 s each, identical
    bytes, zero proposals written).
    """
    return [row for row in rows if int(row.get("variant_id") or 0) in exact_requested]


def sweep_complete(
    *,
    batch: list,
    ok: int,
    fail: int,
    cf: int,
    rate_limited: int,
    session_error,
    results: list,
    missing_requested: list,
    exact_requested: set[int],
    bind_ran: bool,
    selected_ids: set[int],
    ingest,
) -> bool:
    """Exit-0 contract of one child run.

    A bind-only run (no exact ids, bind step ran) has nothing to fetch and an
    empty batch is its complete state; before 2026-08-23 it only looked
    complete because leftover MAP rows padded the batch.
    """
    return (
        (len(batch) > 0 or (not exact_requested and bind_ran))
        and ok == len(batch)
        and fail == 0
        and cf == 0
        and rate_limited == 0
        and session_error is None
        and len(results) == len(batch)
        and not missing_requested
        and (not exact_requested or len(batch) == len(selected_ids))
        and (ingest is None or ingest.get("exit") == 0)
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="0 = all selected rows")
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=PC_SLEEP_SECONDS)
    ap.add_argument(
        "--workers",
        type=int,
        default=PC_TABS,
        help="concurrent tabs inside the one CARDZ CDP session",
    )
    ap.add_argument(
        "--challenge-wait",
        type=float,
        default=120.0,
        help="wait on the same 403 page for its JS/human challenge to resolve; never reload",
    )
    ap.add_argument("--cdp-port", type=int, default=9333)
    ap.add_argument(
        "--cdp-already-ensured",
        action="store_true",
        help="caller already started the singleton CARDZ CDP session while holding its adapter leases",
    )
    ap.add_argument(
        "--resume-report",
        type=Path,
        help="reuse only previously successful exact-ID pages from this durable report",
    )
    ap.add_argument(
        "--variant-ids-file",
        type=Path,
        help="exact active variant IDs to refresh; one integer per line",
    )
    ap.add_argument("--no-ingest", action="store_true")
    ap.add_argument(
        "--bind-missing-ids-file",
        type=Path,
        help="active variants with no exact PC id; identity+cap stay in this 2-tab script",
    )
    args = ap.parse_args()

    started_monotonic = time.monotonic()
    touch_progress_stamp()
    spawn_hard_stall_killer()
    boot_results: list[dict] = []
    # Arm before the MAP read / CDP attach so a crash in those still writes a
    # report the next run can `--resume-report` (the child rejects a resume
    # report that lacks the strict single-browser contract fields).
    register_report_state(
        batch=0,
        results=boot_results,
        base={
            "cdpPort": args.cdp_port,
            "singleBrowserSession": True,
            "fallbackBrowsers": 0,
            "tabs": max(1, int(args.workers)),
            "transport": PC_TRANSPORT,
        },
    )
    watchdog = new_stall_watchdog_state(batch=0, results=boot_results)
    arm_stall_watchdog(watchdog)
    tabs = max(1, int(args.workers))
    if tabs != PC_TABS:
        raise SystemExit(
            f"9333 PC script is dual-tab only (PC_TABS={PC_TABS}); got --workers={tabs}"
        )
    CDP_IDENTITY_ONLY["value"] = bool(args.cdp_already_ensured)
    if not args.cdp_already_ensured:
        ensure_cdp(args.cdp_port)
    bind_report = None
    if args.bind_missing_ids_file:
        bind_ids = [
            int(line.strip())
            for line in args.bind_missing_ids_file.read_text(encoding="utf-8-sig").splitlines()
            if line.strip()
        ]
        try:
            bind_report = bind_missing_ids_dual_tab(bind_ids, cdp_port=args.cdp_port)
        except Exception as error:  # noqa: BLE001 - optional identity work is receipted
            # The caller deliberately treats unresolved bind ids as an
            # identity-lane gap, not a failure of the active exact collection.
            # Keep that contract when the discovery preflight itself is
            # unavailable: name the error in the bind receipt, then continue
            # with the exact MAP rows.  No identity is created or accepted by
            # this branch.
            bind_report = {
                "requested": len(bind_ids),
                "targets": 0,
                "written": 0,
                "ok": False,
                "errorCode": "bind_missing_identity_preflight_failed",
                "error": f"{type(error).__name__}:{error}",
            }
        print(json.dumps({"pcBindMissing": bind_report}, ensure_ascii=False), flush=True)
    rows = [json.loads(l) for l in MAP.read_text(encoding="utf-8").splitlines() if l.strip()]
    requested_ids: list[int] = []
    exact_requested: set[int] = set()
    bind_ids_for_split: list[int] = []
    if args.variant_ids_file:
        requested_ids = list(
            dict.fromkeys(
                int(line.strip())
                for line in args.variant_ids_file.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            )
        )
        exact_requested = set(requested_ids)
        if bind_report is not None:
            bind_ids_for_split = list(bind_ids)
        rows = select_refresh_rows(rows, exact_requested)
    selected_ids = {int(row.get("variant_id") or 0) for row in rows}
    missing_requested, bind_unresolved = partition_requested(
        exact_requested, bind_ids_for_split, selected_ids
    )
    rows = rows[args.offset :]
    batch = rows if args.limit <= 0 else rows[: args.limit]
    row_by_variant = {int(row["variant_id"]): row for row in batch}
    reused_results: list[dict] = []
    resume_discarded: dict[int, str] = {}
    if args.resume_report:
        previous = load_resume_report(args.resume_report, args.cdp_port)
        for prior in previous.get("results") or []:
            if prior.get("status") != "ok":
                continue
            variant_id = int(prior.get("variant_id") or 0)
            row = row_by_variant.get(variant_id)
            if not row:
                continue
            stale = resume_entry_stale_reason(prior, row)
            if stale is not None:
                resume_discarded[variant_id] = stale
                continue
            reused_results.append({**prior, "resumed": True})
    if resume_discarded:
        preview = dict(list(resume_discarded.items())[:20])
        print(
            f"resume entries discarded for {len(resume_discarded)} variants "
            f"(stale bookkeeping; those variants re-fetch fresh in this batch): {preview}",
            flush=True,
        )

    reused_ids = {int(row["variant_id"]) for row in reused_results}
    pending_batch = [row for row in batch if int(row["variant_id"]) not in reused_ids]
    ok = len(reused_results)
    fail = cf = rate_limited = 0
    results = list(reused_results)
    session_error = None
    retries: list[dict] = []
    # Worst legitimate *decision*: goto 120s + challenge wait 120s. CF 5s
    # polls and shared backoff sleeps are not decisions. 300s without a
    # status line means a lost CDP reply, not a slow page.
    # Watchdog was armed at process start so MAP load / resume / connect
    # hangs cannot sit silent. Reuse the same dict; do not reset the beat.
    watchdog["batch"] = len(batch)
    watchdog["results"] = results
    register_report_state(
        batch=len(batch),
        results=results,
        base={
            "limit": args.limit,
            "offset": args.offset,
            "cdpPort": args.cdp_port,
            "singleBrowserSession": True,
            "fallbackBrowsers": 0,
            "tabs": max(1, int(args.workers)),
            "sleepSeconds": max(0.0, float(args.sleep)),
            "transport": PC_TRANSPORT,
            "resumeReport": str(args.resume_report) if args.resume_report else None,
            "reused": len(reused_results),
            "resumeDiscarded": len(resume_discarded),
            "requested": len(requested_ids),
            "selected": len(rows),
            "missingRequestedVariantIds": missing_requested,
        },
    )
    if pending_batch:
        pool = asyncio.run(
            run_fetch_pool(
                pending_batch,
                cdp_port=args.cdp_port,
                tabs=max(1, int(args.workers)),
                sleep_seconds=max(0.0, float(args.sleep)),
                challenge_wait=float(args.challenge_wait),
                watchdog=watchdog,
                results=results,
                start_index=len(reused_results),
                batch_size=len(batch),
            )
        )
        ok += pool["ok"]
        fail += pool["fail"]
        cf += pool["cf"]
        rate_limited += pool["rateLimited"]
        session_error = pool["sessionError"]
        retries = pool["retries"]
        if pool.get("cfStorm"):
            watchdog["done"] = True
            write_partial_report("pc_cf_storm")
            print(
                f"PC_CF_STORM partial report written to {OUT}; exit {EXIT_CF_STORM}",
                flush=True,
            )
            return EXIT_CF_STORM


    ingest = None
    if not args.no_ingest and ok == len(batch) and fail == 0 and cf == 0:
        # C11 via WSL backend venv (MySQL path lives there); ingest must run in
        # THIS checkout (ROOT), never a hardcoded sibling tree.
        watchdog["beat"] = time.monotonic()
        watchdog["limit"] = 1500.0  # WSL ingest is bounded by its own timeout=1200
        root_wsl = "/mnt/" + ROOT.drive[0].lower() + ROOT.as_posix()[len(ROOT.drive):]
        cmd = [
            PY_WSL, "-d", "Ubuntu", "--", "bash", "-lc",
            f"cd '{root_wsl}' && "
            "/home/jackson0202/cardz-market-cap/.venv-backend/bin/python -X utf8 "
            "pipelines/c11_pc_sold_ingest.py --map data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl --write"
        ]
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1200)
        ingest = {"exit": r.returncode, "stdoutTail": (r.stdout or "")[-2500:], "stderrTail": (r.stderr or "")[-800:]}

    report = {
        "asOf": utc_now(),
        "limit": args.limit,
        "offset": args.offset,
        "cdpPort": args.cdp_port,
        "singleBrowserSession": True,
        "fallbackBrowsers": 0,
        "tabs": max(1, int(args.workers)),
        "sleepSeconds": max(0.0, float(args.sleep)),
        "transport": PC_TRANSPORT,
        "elapsedSeconds": round(time.monotonic() - started_monotonic, 1),
        "retries": retries,
        "sessionError": session_error,
        "resumeReport": str(args.resume_report) if args.resume_report else None,
        "reused": len(reused_results),
        "fetched": ok - len(reused_results),
        "rateLimited": rate_limited,
        "batch": len(batch),
        "requested": len(requested_ids),
        "selected": len(rows),
        "missingRequestedVariantIds": missing_requested,
        "bindUnresolvedVariantIds": bind_unresolved,
        "ok": ok,
        "cf": cf,
        "fail": fail,
        "results": results,
        "bindMissing": bind_report,
        "ingest": ingest,
    }
    write_report_atomic(report)
    _REPORT_STATE["complete"] = True
    watchdog["done"] = True
    print(json.dumps({k: report[k] for k in ["asOf", "batch", "ok", "cf", "fail"]}, ensure_ascii=False, indent=2))
    print(f"REPORT {OUT}")
    complete = sweep_complete(
        batch=batch,
        ok=ok,
        fail=fail,
        cf=cf,
        rate_limited=rate_limited,
        session_error=session_error,
        results=results,
        missing_requested=missing_requested,
        exact_requested=exact_requested,
        bind_ran=bind_report is not None,
        selected_ids=selected_ids,
        ingest=ingest,
    )
    return 0 if complete else 1


def run_cli() -> int:
    """Every exit path leaves a receipt on disk.

    Before this wrapper the report only existed at watchdog-stall time and at a
    clean finish, so an exception / Ctrl-C / Task Scheduler CTRL_CLOSE threw
    away every page the run had already proved and the next attempt restarted
    the whole sweep against the same Cloudflare budget.
    """

    install_partial_report_signals()
    try:
        return main()
    except KeyboardInterrupt:
        write_partial_report("keyboard_interrupt")
        return EXIT_INTERRUPTED
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        write_partial_report(f"exception:{type(exc).__name__}")
        raise
    finally:
        # Zero-progress runs are refused inside write_partial_report itself.
        write_partial_report("interrupted")


if __name__ == "__main__":
    raise SystemExit(run_cli())
