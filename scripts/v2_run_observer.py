#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only observer for one V2 daily-chain run (the "監測器" daddy asked for on 2026-08-23).

Runs on the Windows side (the stable half of this box: WSL died 3x on 2026-08-23
while docker / MySQL 3308 kept answering) and, once a minute, records everything
the batch-fix pass needs afterwards:

  * the V2 journal (chain_run / chain_task / chain_attempt / chain_event), read
    INSIDE WSL through `wsl.exe -d Ubuntu -- python3 <this file> snapshot <run>`
    so sqlite locking is honoured (never open the journal over \\wsl.localhost);
    the last good snapshot is persisted as journal-latest.json so the report
    never depends on WSL answering at the end;
  * the tick launcher log (CARDZ_V2_PREFLIGHT / _START / _END / _WSL_PREFLIGHT /
    _LAUNCHER_EXCEPTION, 0x8007274c) with the tick timeline parsed from it;
  * health.json (staleness) / tick-skipped.json (flock skips);
  * host: chrome.exe count + working set (growth vs the first poll), CPU %,
    free RAM, CDP 9333 liveness + tab count, WSL running/latency;
  * MySQL 3308 sessions running a query for > 60 s (root, via docker exec; the
    password is expanded inside the container and never printed);
  * after the run is terminal: the 17:45 JST CARDZ-Promo-After-Publish task
    (last run / rc / brief.json) so the day's promo leg is in the same report.

It never writes to the repo, the journal, or MySQL, never takes a chain lease,
and never kills anything.  Every external probe is hard-capped (output goes to
temp files, the child is killed and abandoned on timeout - no pipe to block on,
which is how subprocess.run(timeout=) hangs on Windows when wslhost.exe keeps
the pipe open).  Findings are appended to
`data/runtime/daily-chain-v2/observer/<business-date>/` as they happen
(observer.log for humans, snapshots.jsonl / anomalies.jsonl / receipts.jsonl
for machines, live.json = latest state, report.md re-rendered every 10 polls
and at the end).  Persisting conditions (stale heartbeat, CDP down, long query,
low RAM ...) are recorded once, repeated every 30 min while they persist, and
closed with a `<KIND>_CLEARED` record, so anomalies.jsonl stays a list of
incidents rather than a heartbeat.

Usage (Windows):
  python -X utf8 -u scripts/v2_run_observer.py watch [--business-date YYYY-MM-DD | --run-id cardz-v2:YYYY-MM-DD[/N|#LABEL]]
        [--start-at ISO-UTC] [--poll 60] [--max-hours 14.75] [--settle-minutes 25] [--no-promo-sweep] [--notify]
  python -X utf8 -u scripts/v2_run_observer.py report --run-id ...      # render report.md from what was recorded
Usage (inside WSL, used by `watch`):
  python3 scripts/v2_run_observer.py snapshot <run_id>                  # JSON on stdout
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JST = dt.timezone(dt.timedelta(hours=9), "JST")
TERMINAL_RUN_STATES = {"PUBLISHED", "PUBLISHED_DEGRADED", "FAILED_FINAL", "ABORTED"}
FINISHED_ATTEMPT_STATES = {"COMPLETED", "DEGRADED", "RETRY", "TERMINAL", "INTERRUPTED"}
ATTEMPT_SEVERITY = {"RETRY": "warn", "DEGRADED": "warn", "INTERRUPTED": "error", "TERMINAL": "error"}
FLAGGED_TASK_STATES = {"PARKED": "error", "TERMINAL": "error", "SKIPPED": "warn", "DEGRADED": "warn"}

# Journal events.  Allowlist of the routine ones; the chain's own alert table
# (daily_chain_v2 ALWAYS_ALERT_EVENTS + what 2026-08-20..24 journals contain)
# decides which are errors; anything unknown is recorded as warn so a new event
# type can never slip past the batch-fix review unseen.
INFO_EVENTS = {"RUN_STARTED", "SOURCE_CONTRACT_REPAIR_PLANNED", "identity.brief", "live.confirmed", "origin.manual", "origin.scheduled"}
ERROR_EVENTS = {"CORE_TASK_PARKED", "SLA_MISSED", "FAILED_FINAL", "ORCHESTRATOR_ERROR", "TICK_CRASHED", "TICK_SIGNALLED",
                "PUBLISH_TERMINAL", "SOURCE_CONTRACT_REPAIR_PLAN_ERROR", "DELIVERY_LEDGER_ERROR", "RUN_ABORTED"}


def event_severity(event_type: str) -> str | None:
    if event_type in INFO_EVENTS:
        return None
    return "error" if event_type in ERROR_EVENTS else "warn"


def human_task_label(task: str) -> str:
    t = (task or "").casefold()
    rules = (
        ("pricecharting", "PriceCharting 價錢同成交"),
        ("gemrate", "GemRate 人口"),
        ("snkrdunk", "SNK 價錢同成交"),
        ("fx:", "匯率"),
        ("candidate-activation", "今日新卡啟用"),
        ("identity", "卡牌身份核對"),
        ("system:release", "出今日網站版"),
        ("daily-accept", "入庫"),
        ("system:box", "組 Box"),
        ("core-contract", "核心合約檢查"),
    )
    for needle, label in rules:
        if needle in t:
            return label
    return "更新鏈其中一步"


def _alert_extra(error: str) -> str:
    e = error or ""
    folded = e.casefold()
    if "PC_CHILD_ALREADY_RUNNING" in e:
        return "上一輪 PriceCharting 未完，所以撞車。"
    if "9333" in e:
        return "專用 Chrome 開唔到。"
    if "wsl" in folded or "WSL_DEAD" in e:
        return "入唔到 Ubuntu。"
    if "SOURCE_FAILED" in e:
        return "資料來源暫時失敗。"
    return ""


def human_alert_text(kind: str, severity: str, detail: dict, day: str) -> str:
    kind_u = (kind or "").upper()
    payload = detail if isinstance(detail, dict) else {}
    task = human_task_label(str(payload.get("task") or payload.get("key") or ""))
    attempt = payload.get("attempt")
    err = " ".join(str(payload.get(k) or "") for k in ("errorCode", "error", "line"))
    extra = _alert_extra(err)
    nth = f"第 {attempt} 次" if attempt not in (None, "") else ""
    mark = "🔴" if severity == "error" else "⚠️"
    # 2026-09-25 dead-man: when the chain goes quiet before its 17:00 JST final no
    # tick is left to stamp FAILED_FINAL, so FINAL_EXCEEDED is the only page that
    # the day has no live-confirmed publication.  It must say that in words.
    final = parse_ts(payload.get("finalAt"))
    final_jst = final.astimezone(JST).strftime("%H:%M JST") if final else ""
    templates = {
        "ATTEMPT_RETRY": f"{mark} 更新鏈重試中（{day}）\n{task}{nth and ' ' + nth}自動再跑。{extra and ' ' + extra}\n唔使人手。約 10 分鐘內會再試。",
        "ATTEMPT_INTERRUPTED": f"{mark} 更新鏈被打斷（{day}）\n{task}未做完就被停。{extra and ' ' + extra}\n下一個 10 分鐘檔會續跑。",
        "ATTEMPT_TERMINAL": f"{mark} 更新鏈呢步停咗（{day}）\n{task}已放棄。{extra and ' ' + extra}",
        "ATTEMPT_DEGRADED": f"{mark} 更新鏈呢步降級（{day}）\n{task}未達標，鏈繼續。{extra and ' ' + extra}",
        "LAUNCHER_EXCEPTION": f"{mark} 更新鏈開唔到（{day}）\n{extra or '啟動程式出事。'}",
        "CDP_PREFLIGHT_FAILED": f"{mark} 專用 Chrome 未就緒（{day}）\nPriceCharting／SNK 呢步會卡住。",
        "CDP_9333_DOWN": f"{mark} 專用 Chrome 斷咗（{day}）\n更新鏈睇唔到 PriceCharting／SNK。",
        "WSL_PREFLIGHT_NOT_OK": f"{mark} 入唔到 Ubuntu（{day}）\n更新鏈開唔到。",
        "WSL_PROBE_FAILED": f"{mark} Ubuntu 無回應（{day}）",
        "WSL_SERVICE_ERROR": f"{mark} Ubuntu 入口有問題（{day}）",
        "TASK_STATE_DEGRADED": f"{mark} 更新鏈有一步降級（{day}）\n{task}",
        "TASK_STATE_SKIPPED": f"{mark} 更新鏈跳過一步（{day}）\n{task}",
        "SLA_EXCEEDED": f"{mark} 更新鏈超時（{day}）\n{task}行得太耐。",
        "FINAL_EXCEEDED": f"{mark} 今日網站版未確認上線（{day}）\n過咗收工時間 {final_jst}，更新鏈仍未完成（狀態 {payload.get('status')}）。今日唔會再開新一步；如果最後一檔收尾時上咗線，會另有上線通知。",
        "HEALTH_STALE": f"{mark} 更新鏈心跳停咗（{day}）\n狀態檔太舊，可能卡住。",
        "TICK_SKIPPED_LOCKED": f"{mark} 更新鏈呢檔跳過（{day}）\n上一檔未完，所以冇重開。正常。",
    }
    body = templates.get(kind_u)
    if body:
        return re.sub(r" +", " ", body).replace(" \n", "\n").strip()
    fallback = f"{mark} 更新鏈告警（{day}）\n{task}：{kind_u}。"
    if extra:
        fallback += " " + extra
    return fallback.strip()


# Expected attempt seconds per task kind [KNOWN: real runs 2026-08-23/24 +
# rehearsal A10].  A finished attempt slower than max(2x, +120 s) is flagged.
# candidate-stock lanes were never observed (no rebuild day yet): provisional.
BASELINE_SECONDS = (
    ("gemrate:contract-repair", 1300),
    ("gemrate:candidate", 600),
    ("gemrate:pop+identity", 900),
    ("pricecharting:contract-repair", 240),
    ("pricecharting:candidate", 330),
    ("pricecharting:", 330),
    ("snkrdunk:contract-repair", 40),
    ("snkrdunk:candidate", 200),
    ("snkrdunk:", 200),
    ("system:daily-accept", 100),
    ("system:release", 210),
    ("system:checkpoint-repair", 10),
    ("system:identity-reverify", 15),
    ("system:identity-", 15),
    ("system:core-contract", 10),
    ("system:candidate-", 10),
    ("system:box", 10),
    ("system:live-confirm", 10),
    ("system:collection-registry", 6),
    ("system:schema-", 4),
    ("system:pending-identities", 6),
    ("fx:", 4),
)
# Publish-phase baselines come from 2 runs only: a slow one is info, not warn.
PROVISIONAL_BASELINE_PREFIXES = ("system:box", "system:release", "system:live-confirm", "gemrate:candidate", "pricecharting:candidate", "snkrdunk:candidate")
STALE_HEARTBEAT_SECONDS = 180        # TASK_LEASE_SECONDS is 90 in the chain
TICK_GAP_SECONDS = 12 * 60           # scheduler fires every 10 min: one missed tick
LAUNCHER_SILENT_SECONDS = 25 * 60    # two missed ticks + slack: the scheduler is not firing
HEALTH_STALE_SECONDS = 25 * 60       # same threshold the watchdog uses
RUN_START_GRACE_SECONDS = 20 * 60    # 03:30 JST tick + 2 periods without a chain_run row
RECURRING_REPEAT_SECONDS = 30 * 60   # persisting condition: repeat record every 30 min
LONG_QUERY_SECONDS = 600
CHROME_COUNT_GROWTH = 24             # vs the first poll: daddy's own Chrome is always open (60+ procs), only growth matters
CHROME_RSS_GROWTH_MB = 4096
FREE_RAM_MIN_MB = 2048
INTERRUPTION_BUDGET = 4              # chain_task.interruptions at which the lane is burning its attempts on drains
CDP_CONSECUTIVE_FAILS = 2            # one failed /json/version is noise (the watchdog restarts Chrome); two in a row is a finding
CDP_TIMEOUT_SECONDS = 15.0
MYSQL_BACKOFF_POLLS = 10             # after 3 probe timeouts in a row, stop asking for this many polls
JOURNAL_IDLE_REPROBE_SECONDS = 600   # Ubuntu stopped between ticks: re-read the journal (boots the distro) at most every 10 min
EXPECTED_TICK_JST = (11, 0)          # CARDZ-Marketcap-Daily-V2 trigger: daily 11:00 JST, PT10M for PT6H
DAILY_TASK = "CARDZ-Marketcap-Daily-V2"
PROMO_TASK = "CARDZ-Promo-After-Publish"
PROMO_JST = (17, 45)
PROMO_WAIT_SLACK_SECONDS = 12 * 60

MAIN_JOURNAL = "~/.local/state/cardz-marketcap/daily-chain-v2{suffix}.sqlite3"
NOTIFY_SCRIPT = ROOT / "scripts" / "notify_hermes.py"
NOTIFY_TIMEOUT_SECONDS = 20

# Nothing secret is supposed to reach the journal / launcher log, but payloads
# and stderr are copied verbatim into this folder, so scrub the usual shapes.
SCRUB_RES = (
    (re.compile(r"(?i)(password|passwd|pwd|token|secret|api[_-]?key|authorization|cookie)(\"?\s*[=:]\s*\"?)[^\s,;\"'<>]+"), r"\1\2<redacted>"),
    (re.compile(r"pb_live_[A-Za-z0-9_-]+"), "pb_live_<redacted>"),
    (re.compile(r"(https?://[^\s?\"'<>]+)\?[^\s\"'<>]*"), r"\1?<query-redacted>"),
)


def scrub(text: str) -> str:
    for rx, rep in SCRUB_RES:
        text = rx.sub(rep, text)
    return text


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    text = str(s).strip().replace("Z", "+00:00")
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)          # launcher log: 7-digit fraction
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)  # +0900 -> +09:00
    try:
        t = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def expected_tick_start(day: str) -> dt.datetime | None:
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        return None
    return dt.datetime.combine(d, dt.time(*EXPECTED_TICK_JST), tzinfo=JST).astimezone(dt.timezone.utc)


def promo_time(day: str) -> dt.datetime | None:
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        return None
    return dt.datetime.combine(d, dt.time(*PROMO_JST), tzinfo=JST).astimezone(dt.timezone.utc)


def promo_brief(pdir: Path) -> dict | None:
    """The brief.json fields the observer reports; None when the dir has no brief."""
    bp = pdir / "brief.json"
    if not bp.exists():
        return None
    try:
        b = json.loads(bp.read_text(encoding="utf-8"))
    except ValueError:
        return {"error": "unreadable"}
    if not isinstance(b, dict):
        return {"error": "not an object"}
    return {k: b.get(k) for k in ("generation", "lagHours", "post", "businessDate", "generatedAt", "publishedAt")}


def resolve_promo_dir(day: str, generation: str | None) -> tuple[Path, dict | None, list[str]]:
    """`data/runtime/promo/<dir>` is keyed by the JST day of the BAKE, not by the run's
    business date: the bake that publishes business date D usually happens on D-1 JST, so
    the D-1 dir is the one holding D's generation.  Prefer whichever dir actually carries
    this run's generation (nearest day first); fall back to the business-date dir so a
    genuinely missing brief is still reported against the expected location."""
    base = ROOT / "data" / "runtime" / "promo"
    default = base / day
    scanned: list[str] = []
    if generation and base.is_dir():
        def distance(name: str) -> int:
            try:
                return abs((dt.date.fromisoformat(name) - dt.date.fromisoformat(day)).days)
            except ValueError:
                return 10 ** 6
        for pdir in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: (distance(p.name), p.name)):
            scanned.append(pdir.name)
            brief = promo_brief(pdir)
            if brief and brief.get("generation") == generation:
                return pdir, brief, scanned
    return default, promo_brief(default), scanned


def promo_scheduler_receipt(day: str) -> tuple[Path, dict | None]:
    """Read the promo program's own rc, which is authoritative across the VBS/WSL boundary."""

    path = ROOT / "data" / "runtime" / "promo" / "scheduler" / f"{day}.json"
    if not path.exists():
        return path, None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return path, {"error": f"{type(error).__name__}: {error}"}
    if not isinstance(receipt, dict):
        return path, {"error": "not an object"}
    if receipt.get("contract") != "cardz-promo-pack-scheduled-v1":
        return path, {"error": "wrong contract"}
    if receipt.get("businessDate") != day:
        return path, {"error": "wrong business date"}
    if not isinstance(receipt.get("exitCode"), int):
        return path, {"error": "exitCode is not an integer"}
    return path, receipt


def short_key(task_key: str) -> str:
    """'2026-08-24:gemrate:pop+identity:0-of-4:f270a1c7…' -> 'gemrate:pop+identity:0-of-4'."""
    parts = task_key.split(":")
    if parts and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[0]):
        parts = parts[1:]
    if parts and re.fullmatch(r"[0-9a-f]{16,}", parts[-1]):
        parts = parts[:-1]
    return ":".join(parts)


def baseline_for(short: str) -> int | None:
    for prefix, seconds in BASELINE_SECONDS:
        if short.startswith(prefix):
            return seconds
    return None


def baseline_provisional(short: str) -> bool:
    return short.startswith(PROVISIONAL_BASELINE_PREFIXES)


def wsl_path(p: Path) -> str:
    s = str(p).replace("\\", "/")
    # Off Windows "C:/x" is a relative path, and resolve() would glue it onto the cwd.
    if os.name == "nt" or not (len(s) > 1 and s[1] == ":"):
        s = str(p.resolve()).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        s = f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def windows_path(p: str) -> Path | None:
    """Map a path string found in a journal payload (written inside WSL) to something readable here."""
    if not p:
        return None
    m = re.match(r"^/mnt/([a-z])/(.*)$", p)
    if m:
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    if p.startswith("/"):
        return None                                   # Linux-only path: read through wsl.exe
    return Path(p) if Path(p).is_absolute() else ROOT / p


# --------------------------------------------------------------------------- snapshot (runs inside WSL)

def run_business_day(run_id: str) -> str:
    """Return YYYY-MM-DD for base, supersede (/N), and labelled (#LABEL) IDs."""
    raw = run_id.removeprefix("cardz-v2:").split("#", 1)[0]
    return raw.split("/", 1)[0]


def scheduled_family_sequence(base_run_id: str, candidate_run_id: str) -> int | None:
    """Sequence within one scheduled family; base is 1 and supersedes are /2+."""
    if candidate_run_id == base_run_id:
        return 1
    match = re.fullmatch(re.escape(base_run_id) + r"/([0-9]+)", candidate_run_id)
    sequence = int(match.group(1)) if match else 0
    return sequence if sequence >= 2 else None


def cmd_snapshot(run_id: str) -> int:
    import sqlite3

    raw_id, _, label = run_id.removeprefix("cardz-v2:").partition("#")
    day = run_business_day(run_id)
    path = os.path.expanduser(MAIN_JOURNAL.format(suffix=f"-{label}" if label else ""))
    out: dict = {
        "runId": run_id,
        "requestedRunId": run_id,
        "resolvedRunId": run_id,
        "journalPath": path,
        "capturedAt": iso(utc_now()),
    }
    if not os.path.exists(path):
        out["error"] = "NO_JOURNAL"
        print(json.dumps(out))
        return 0
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20)
        c.execute("SELECT 1 FROM chain_run LIMIT 1").fetchall()
    except sqlite3.OperationalError as exc:
        # read-only open can fail while the WAL needs recovery; fall back to rw + query_only (still never writes)
        out["roError"] = str(exc)[:200]
        try:
            c = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=20)
            c.execute("PRAGMA query_only=ON")
            c.execute("SELECT 1 FROM chain_run LIMIT 1").fetchall()
        except sqlite3.OperationalError as exc2:
            out["error"] = "JOURNAL_OPEN_FAILED"
            out["exc"] = str(exc2)[:200]
            print(json.dumps(out))
            return 0
    c.row_factory = sqlite3.Row
    run = c.execute("SELECT * FROM chain_run WHERE run_id=?", (run_id,)).fetchone()
    base_run_id = f"cardz-v2:{day}"
    if not label and raw_id == day:
        candidates = []
        for candidate in c.execute("SELECT * FROM chain_run WHERE business_date=?", (day,)).fetchall():
            seq = scheduled_family_sequence(base_run_id, str(candidate["run_id"]))
            if seq is not None:
                candidates.append((seq, str(candidate["created_at"] or ""), candidate))
        if candidates:
            run = max(candidates, key=lambda item: (item[0], item[1]))[2]
    resolved_run_id = str(run["run_id"]) if run else run_id
    out["runId"] = resolved_run_id
    out["resolvedRunId"] = resolved_run_id
    out["run"] = dict(run) if run else None
    out["otherRuns"] = [dict(r) for r in c.execute(
        "SELECT run_id,business_date,status,publication_status,created_at,completed_at FROM chain_run ORDER BY created_at DESC LIMIT 5")]
    if run is None:
        print(json.dumps(out, default=str))
        return 0
    out["tasks"] = [dict(r) for r in c.execute(
        "SELECT task_key,phase,source_code,capability,required_class,concurrency_group,status,attempts,max_attempts,next_retry_at,"
        " heartbeat_at,last_error_code,substr(last_error,1,400) AS last_error,created_at,updated_at,interruptions,"
        " substr(checkpoint_json,1,300) AS checkpoint_json FROM chain_task WHERE run_id=? ORDER BY created_at", (resolved_run_id,))]
    out["attempts"] = [dict(r) for r in c.execute(
        "SELECT a.task_key,a.attempt_no,a.status,a.started_at,a.heartbeat_at,a.finished_at,a.worker_pid,a.error_code,"
        " substr(a.error_text,1,400) AS error_text, substr(a.receipt_json,1,4000) AS receipt_json"
        " FROM chain_attempt a JOIN chain_task t ON t.task_key=a.task_key WHERE t.run_id=? ORDER BY a.started_at, a.attempt_no",
        (resolved_run_id,))]
    out["events"] = [dict(r) for r in c.execute(
        "SELECT event_key,event_type,created_at,delivered,substr(payload_json,1,1500) AS payload_json"
        " FROM chain_event WHERE run_id=? ORDER BY created_at", (resolved_run_id,))]
    print(json.dumps(out, default=str, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- host probes (Windows)

def _decode(b: bytes) -> str:
    return b.decode("utf-8", "replace").replace("\x00", "").replace(chr(0xFEFF), "")


def run_capped(cmd: list[str], timeout: float, *, env: dict | None = None) -> tuple[int | None, str, str]:
    """Hard cap.  stdout/stderr go to temp files (never pipes), so a child that
    survives kill() (wslhost.exe) cannot hold a pipe we would block draining.
    Returns (rc, stdout, stderr); rc None = timed out (child killed + abandoned)."""
    fd_o, po = tempfile.mkstemp(prefix="v2obs-out-")
    fd_e, pe = tempfile.mkstemp(prefix="v2obs-err-")
    fo = os.fdopen(fd_o, "w+b")
    fe = os.fdopen(fd_e, "w+b")
    try:
        try:
            p = subprocess.Popen(cmd, stdout=fo, stderr=fe, stdin=subprocess.DEVNULL, env=env,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            return -1, "", f"OSError {exc}"
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                p.kill()
            except OSError:
                pass
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            return None, "", "TIMEOUT"
        fo.seek(0)
        fe.seek(0)
        return rc, _decode(fo.read()), _decode(fe.read())
    finally:
        for fh, path in ((fo, po), (fe, pe)):
            try:
                fh.close()
            except OSError:
                pass
            try:
                os.unlink(path)
            except OSError:
                pass


def probe_journal(run_id: str) -> dict:
    t0 = time.monotonic()
    rc, out, err = run_capped(["wsl.exe", "-d", "Ubuntu", "--", "python3", "-X", "utf8", wsl_path(Path(__file__)), "snapshot", run_id], 75)
    ms = int((time.monotonic() - t0) * 1000)
    if rc is None:
        return {"error": "WSL_SNAPSHOT_TIMEOUT", "ms": ms}
    if rc != 0:
        return {"error": f"WSL_SNAPSHOT_RC_{rc}", "ms": ms, "stderr": err[-400:]}
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else {"error": "WSL_SNAPSHOT_EMPTY"}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"error": "WSL_SNAPSHOT_BAD_JSON", "ms": ms, "head": out[:200], "exc": str(exc)}
    data["ms"] = ms
    return data


def probe_http(url: str, timeout: float = CDP_TIMEOUT_SECONDS) -> dict:
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:  # noqa: S310 - loopback only
            body = r.read(200_000).decode("utf-8", "replace")
        ms = int((time.monotonic() - t0) * 1000)
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError:
            parsed = None
        return {"ok": True, "ms": ms, "json": parsed}
    except Exception as exc:  # noqa: BLE001 - any failure is "down"
        return {"ok": False, "ms": int((time.monotonic() - t0) * 1000), "error": str(exc)[:160]}


HOST_PS = (
    "$ErrorActionPreference='SilentlyContinue';"
    "$c=(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter \"Name='_Total'\").PercentProcessorTime;"
    "$os=Get-CimInstance Win32_OperatingSystem;"
    "$ch=@(Get-Process chrome -ErrorAction SilentlyContinue);"
    "$ws=($ch | Measure-Object WorkingSet64 -Sum).Sum;"
    "$w=@(Get-Process wslservice,wslhost -ErrorAction SilentlyContinue).Count;"
    "[pscustomobject]@{cpu=[double]$c;freeMb=[int]($os.FreePhysicalMemory/1KB);totalMb=[int]($os.TotalVisibleMemorySize/1KB);"
    "chromeCount=$ch.Count;chromeRssMb=[int]($ws/1MB);wslProcs=$w} | ConvertTo-Json -Compress"
)


def probe_host() -> dict:
    rc, out, err = run_capped(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", HOST_PS], 40)
    if rc is None:
        return {"error": "HOST_PROBE_TIMEOUT"}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError):
        return {"error": "HOST_PROBE_BAD_OUTPUT", "head": (out or err)[:160]}


def probe_wsl() -> dict:
    """`wsl -l --running` first (does not boot anything), then an exec only if Ubuntu is up."""
    t0 = time.monotonic()
    rc, out, err = run_capped(["wsl.exe", "-l", "--running"], 30)
    ms = int((time.monotonic() - t0) * 1000)
    if rc is None:
        return {"ok": False, "running": None, "ms": ms, "error": "LIST_TIMEOUT"}
    blob = (out + " " + err).strip()
    if re.search(r"0x8007|Wsl/Service", blob):
        return {"ok": False, "running": None, "ms": ms, "rc": rc, "error": blob[:160]}
    running = "ubuntu" in out.lower()
    res: dict = {"ok": True, "running": running, "ms": ms, "rc": rc, "error": ""}
    if running:
        t1 = time.monotonic()
        rc2, _, err2 = run_capped(["wsl.exe", "-d", "Ubuntu", "--", "true"], 30)
        res["execMs"] = int((time.monotonic() - t1) * 1000)
        res["ms"] = int((time.monotonic() - t0) * 1000)
        if rc2 is None:
            res.update(ok=False, error="EXEC_TIMEOUT")
        elif rc2 != 0:
            res.update(ok=False, error=err2.strip()[:160] or f"rc={rc2}")
    return res


MYSQL_SQL = (
    "SET SESSION max_execution_time=20000; SELECT ID,USER,TIME,STATE,LEFT(REPLACE(REPLACE(INFO,'\\n',' '),'\\r',' '),240)"
    " FROM information_schema.PROCESSLIST WHERE COMMAND='Query' AND TIME>60 AND INFO NOT LIKE '%information_schema.PROCESSLIST%'"
)


def probe_mysql() -> dict:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    shell = ('T=""; command -v timeout >/dev/null 2>&1 && T="timeout 25"; '
             '$T mysql --connect-timeout=5 -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "' + MYSQL_SQL.replace('"', '\\"') + '"')
    rc, out, err = run_capped(["docker", "exec", "cardz-market-cap-db-1", "sh", "-lc", shell], 40, env=env)
    if rc is None:
        return {"error": "MYSQL_PROBE_TIMEOUT"}
    if rc != 0:
        clean = "\n".join(l for l in err.splitlines() if "Using a password" not in l)
        return {"error": f"MYSQL_PROBE_RC_{rc}", "stderr": clean[-300:]}
    rows = []
    for line in out.splitlines():
        cells = line.split("\t")
        if len(cells) >= 5 and cells[2].isdigit():
            rows.append({"id": int(cells[0]), "user": cells[1], "seconds": int(cells[2]), "state": cells[3], "sql": cells[4][:240]})
    return {"long": rows}


def probe_task_info(name: str) -> dict:
    ps = ("$ErrorActionPreference='SilentlyContinue';$t=Get-ScheduledTask -TaskName '" + name + "';"
          "if($t){$i=$t|Get-ScheduledTaskInfo;$o=[ordered]@{state=[string]$t.State;rc=$i.LastTaskResult;last=$null;next=$null};"
          "if($i.LastRunTime){$o.last=$i.LastRunTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')};"
          "if($i.NextRunTime){$o.next=$i.NextRunTime.ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')};"
          "[pscustomobject]$o|ConvertTo-Json -Compress}else{'{\"error\":\"TASK_ABSENT\"}'}")
    rc, out, err = run_capped(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", ps], 40)
    if rc is None:
        return {"error": "TASK_PROBE_TIMEOUT"}
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError, json.JSONDecodeError):
        return {"error": "TASK_PROBE_BAD_OUTPUT", "head": (out or err)[:160]}


def task_tick_liveness(
    info: dict, expected: dt.datetime, repo_tick: dt.datetime | None,
    now: dt.datetime,
) -> dict[str, dict]:
    """Cross-check Task Scheduler's run with the repository-side tick."""

    if now <= expected + dt.timedelta(seconds=RUN_START_GRACE_SECONDS):
        return {}
    if info.get("error"):
        return {"TASK_PROBE_FAILED": {"task": DAILY_TASK, **info}}
    last = parse_ts(info.get("last"))
    if last is None or last < expected:
        return {"TASK_NOT_RUN": {
            "task": DAILY_TASK, "last": info.get("last"),
            "expectedStart": iso(expected), "state": info.get("state"),
        }}
    if repo_tick is None or repo_tick < expected:
        return {"TASK_REPO_TICK_MISMATCH": {
            "task": DAILY_TASK, "taskLastRun": iso(last),
            "repoTick": iso(repo_tick), "expectedStart": iso(expected),
        }}
    return {}


def tail_path(p: str, limit: int = 8192) -> str:
    """Last `limit` bytes of a file named in a journal payload (Windows-readable or via WSL)."""
    wp = windows_path(p)
    if wp is not None:
        try:
            with wp.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                size = fh.tell()
                fh.seek(max(0, size - limit))
                return _decode(fh.read())
        except OSError as exc:
            return f"<unreadable {wp}: {exc}>"
    rc, out, err = run_capped(["wsl.exe", "-d", "Ubuntu", "--", "tail", "-c", str(limit), p], 20)
    return out if rc == 0 else f"<wsl tail rc={rc} {err[:120]}>"


LAUNCHER_TS_ISO = re.compile(r"^\[?(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?)(Z|[+-]\d{2}:?\d{2})?\]?")
LAUNCHER_TS_CLOCK = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\]")


def launcher_line_time(line: str, file_day: str) -> dt.datetime:
    m = LAUNCHER_TS_ISO.match(line)
    if m:
        t = parse_ts(m.group(1) + (m.group(2) or ""))
        if t is not None:
            if not m.group(2):
                t = t.replace(tzinfo=JST)     # launcher stamps are local (JST) when no offset is written
            return t.astimezone(dt.timezone.utc)
    m = LAUNCHER_TS_CLOCK.match(line)
    if m:
        try:
            d = dt.datetime.strptime(file_day, "%Y%m%d").date()
            return dt.datetime.combine(d, dt.time(int(m.group(1)), int(m.group(2)), int(m.group(3))), tzinfo=JST).astimezone(dt.timezone.utc)
        except ValueError:
            pass
    return utc_now()


# --------------------------------------------------------------------------- the watcher

class Observer:
    def __init__(
        self,
        run_id: str,
        out_dir: Path,
        *,
        poll: float,
        max_hours: float,
        settle_minutes: float,
        promo_sweep: bool = True,
        notify_alerts: bool = False,
    ) -> None:
        self.run_id = run_id
        self.requested_run_id = run_id
        self.resolved_run_id = run_id
        self.day = run_business_day(run_id)
        self.scheduled = "#" not in run_id              # labelled runs are manual rehearsals: no 11:00 / promo expectations
        self.expected_start = expected_tick_start(self.day) if self.scheduled else None
        self.out_dir = out_dir
        self.poll = poll
        self.deadline = time.monotonic() + max_hours * 3600
        self.settle_seconds = settle_minutes * 60
        self.promo_sweep = promo_sweep
        self.notify_alerts = notify_alerts
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = out_dir / "observer.log"
        self.snap_path = out_dir / "snapshots.jsonl"
        self.anom_path = out_dir / "anomalies.jsonl"
        self.receipt_path = out_dir / "receipts.jsonl"
        self.live_path = out_dir / "live.json"
        self.seen_events: set[str] = set()
        self.seen_attempts: set[tuple[str, int]] = set()
        self.seen_tick_skips: set[str] = set()
        self.seen_log_tails: set[str] = set()
        self.interruption_flagged: set[str] = set()
        self.task_status: dict[str, str] = {}
        self.run_status: str | None = None
        self.run_generation: str | None = None
        self.launcher_offsets: dict[str, int] = {}
        self.last_tick_start: dt.datetime | None = None
        self.last_tick_end: dt.datetime | None = None
        self.ticks: list[dict] = []
        self.cpu_high_polls = 0
        self.cdp_fail_streak = 0
        self.mysql_timeouts = 0
        self.mysql_backoff_until_poll = 0
        self.health_unreadable_polls = 0
        self.chrome_base: tuple[int, int] | None = None
        self.terminal_seen_at: float | None = None
        self.last_journal: dict | None = None
        self.last_journal_ok_at: float | None = None
        self.active: dict[str, dict] = {}               # recurring conditions currently open
        self.promo: dict | None = None
        self.peaks = {"chromeCount": 0, "chromeRssMb": 0, "cpuMax": 0.0, "freeMbMin": None, "wslMsMax": 0, "wslTimeouts": 0,
                      "journalMsMax": 0, "journalTimeouts": 0, "journalSkips": 0, "cdpDowns": 0, "polls": 0}
        self.anomaly_counts: dict[str, int] = {}

    # -- output helpers
    def log(self, line: str) -> None:
        stamp = utc_now().strftime("%H:%M:%SZ")
        text = f"{stamp} {scrub(line)}"
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def anomaly(self, kind: str, severity: str, detail: dict) -> None:
        rec = {"at": iso(utc_now()), "kind": kind, "severity": severity, **detail}
        with self.anom_path.open("a", encoding="utf-8") as fh:
            fh.write(scrub(json.dumps(rec, ensure_ascii=False, default=str)) + "\n")
        self.anomaly_counts[kind] = self.anomaly_counts.get(kind, 0) + 1
        brief = json.dumps({k: v for k, v in detail.items() if k not in ("payload", "tail")}, ensure_ascii=False, default=str)
        self.log(f"!! {severity.upper():5} {kind} {brief[:400]}")
        if self.notify_alerts and severity in {"warn", "error"}:
            self.send_anomaly_alert(kind, severity, detail)

    def send_anomaly_alert(self, kind: str, severity: str, detail: dict) -> bool:
        """Deliver an anomaly and durably admit when delivery did not land."""

        safe_kind = re.sub(r"[^a-z0-9_-]+", "-", kind.casefold()).strip("-") or "anomaly"
        key = f"v2-observer:{self.day}:{safe_kind}"
        message = scrub(human_alert_text(kind, severity, detail, self.day))
        rc, _stdout, _stderr = run_capped(
            [
                sys.executable, "-X", "utf8", str(NOTIFY_SCRIPT), "alert",
                "--key", key, "--text", message, "--level", severity,
                "--human", "--cooldown-min", "30", "--require-delivery",
            ],
            NOTIFY_TIMEOUT_SECONDS,
        )
        if rc == 0:
            return True
        failure = {
            "contract": "cardz-v2-observer-alert-delivery-failure-v1",
            "at": iso(utc_now()),
            "kind": kind,
            "severity": severity,
            "key": key,
            "exitCode": rc,
        }
        with (self.out_dir / "alert-failures.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(failure, ensure_ascii=False, default=str) + "\n")
        self.log(f"!! ERROR ALERT_DELIVERY_FAILED kind={kind} exit={rc}")
        return False

    def recurring(self, kind: str, severity: str, present: dict[str, dict]) -> None:
        """Open / repeat / close one condition per key.  `present` = the keys true right now."""
        now = time.monotonic()
        for key, detail in present.items():
            k = f"{kind}|{key}"
            st = self.active.get(k)
            if st is None:
                self.active[k] = {"since": now, "last": now, "n": 1}
                self.anomaly(kind, severity, {"key": key, **detail})
            else:
                st["n"] += 1
                if now - st["last"] >= RECURRING_REPEAT_SECONDS:
                    st["last"] = now
                    self.anomaly(kind, severity, {"key": key, "persistingMin": int((now - st["since"]) / 60), "polls": st["n"], **detail})
        for k in [k for k in self.active if k.startswith(kind + "|")]:
            key = k.split("|", 1)[1]
            if key not in present:
                st = self.active.pop(k)
                self.anomaly(kind + "_CLEARED", "info", {"key": key, "minutes": int((now - st["since"]) / 60), "polls": st["n"]})

    # -- launcher log (UTF-8 + BOM, written by cardz_daily_v2_launcher.ps1 with Add-Content -Encoding UTF8)
    def read_launcher(self) -> list[tuple[str, str]]:
        new: list[tuple[str, str]] = []
        local_day = dt.datetime.now().strftime("%Y%m%d")
        days = {local_day, (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y%m%d")}
        for d in sorted(days):
            p = ROOT / "logs" / "daily-chain-v2" / f"launcher-{d}.log"
            if not p.exists():
                continue
            raw = p.read_bytes()
            off = self.launcher_offsets.get(str(p), 0)
            if off == 0 and d != local_day and str(p) not in self.launcher_offsets:
                self.launcher_offsets[str(p)] = len(raw)   # yesterday: only what is appended from now on
                continue
            if len(raw) < off:
                self.anomaly("LAUNCHER_TRUNCATED", "warn", {"file": p.name, "was": off, "now": len(raw)})
                off = 0
            if len(raw) > off:
                text = _decode(raw[off:])
                new.extend((d, l) for l in text.splitlines() if l.strip())
            self.launcher_offsets[str(p)] = len(raw)
        return new

    def handle_launcher_lines(self, lines: list[tuple[str, str]]) -> None:
        for file_day, line in lines:
            self.log(f"tick| {line[:300]}")
            t = launcher_line_time(line, file_day)
            if "CARDZ_V2_START" in line:
                self.last_tick_start = t
                self.ticks.append({"start": iso(t), "end": None, "exit": None, "seconds": None})
            if "CARDZ_V2_END" in line:
                self.last_tick_end = t
                m = re.search(r"exit=(-?\d+)", line)
                code = int(m.group(1)) if m else None
                if self.ticks and self.ticks[-1]["end"] is None:
                    tk = self.ticks[-1]
                    tk["end"] = iso(t)
                    tk["exit"] = code
                    s = parse_ts(tk["start"])
                    tk["seconds"] = int((t - s).total_seconds()) if s else None
                else:
                    self.ticks.append({"start": None, "end": iso(t), "exit": code, "seconds": None})
                if code not in (0, None):
                    self.anomaly("TICK_EXIT_NONZERO", "error", {"exit": code, "line": line[:300]})
            if re.search(r"\bCARDZ_V2_PREFLIGHT\b", line):
                m = re.search(r"exit=(-?\d+)", line)
                if m and m.group(1) != "0":
                    self.anomaly("CDP_PREFLIGHT_FAILED", "error", {"exit": int(m.group(1)), "line": line[:300]})
            if "CARDZ_V2_WSL_PREFLIGHT" in line and "WSL_OK" not in line:
                self.anomaly("WSL_PREFLIGHT_NOT_OK", "warn", {"line": line[:300]})
            if "CARDZ_V2_LAUNCHER_EXCEPTION" in line:
                self.anomaly("LAUNCHER_EXCEPTION", "error", {"line": line[:300]})
            if "0x8007274c" in line or "Wsl/Service" in line:
                self.anomaly("WSL_SERVICE_ERROR", "error", {"line": line[:300]})

    # -- journal consumers
    def consume_run(self, run: dict | None, data_fresh: bool, journal: dict, now: dt.datetime) -> None:
        if run:
            resolved_run_id = str(journal.get("resolvedRunId") or run.get("run_id") or self.requested_run_id)
            if resolved_run_id != self.resolved_run_id:
                previous = self.resolved_run_id
                self.resolved_run_id = resolved_run_id
                self.run_status = None
                self.run_generation = None
                self.terminal_seen_at = None
                self.task_status.clear()
                self.seen_attempts.clear()
                self.seen_events.clear()
                self.log(f"RUN_RESOLVED requested={self.requested_run_id} {previous} -> {resolved_run_id}")
            status = str(run.get("status"))
            self.run_generation = run.get("generation_id") or self.run_generation
            if status != self.run_status:
                self.log(f"RUN {self.resolved_run_id} status {self.run_status} -> {status} pub={run.get('publication_status')} gen={run.get('generation_id')}"
                         f" origin={run.get('origin')} created={str(run.get('created_at'))[:19]} sla={str(run.get('sla_at'))[11:19]}"
                         f" final={str(run.get('final_at'))[11:19]} cutoff={str(run.get('source_cutoff_at'))[11:19]}"
                         f" interventions={run.get('manual_intervention_count')} degraded={run.get('degraded_sources_json')}")
                self.run_status = status
                if status in TERMINAL_RUN_STATES and self.terminal_seen_at is None:
                    self.terminal_seen_at = time.monotonic()
            sla = parse_ts(run.get("sla_at"))
            final = parse_ts(run.get("final_at"))
            if status not in TERMINAL_RUN_STATES:
                if sla and now > sla and self.anomaly_counts.get("SLA_EXCEEDED", 0) == 0:
                    self.anomaly("SLA_EXCEEDED", "error", {"slaAt": iso(sla), "status": status})
                if final and now > final and self.anomaly_counts.get("FINAL_EXCEEDED", 0) == 0:
                    self.anomaly("FINAL_EXCEEDED", "error", {"finalAt": iso(final), "status": status})
        elif data_fresh:
            if self.run_status != "NOT_STARTED":
                others = ", ".join(f"{r['run_id']}={r['status']}" for r in (journal.get("otherRuns") or [])[:3])
                self.log(f"RUN {self.requested_run_id} not in journal yet (latest: {others})")
                self.run_status = "NOT_STARTED"
        late = {}
        if run is None and self.last_journal is not None and self.expected_start and now > self.expected_start + dt.timedelta(seconds=RUN_START_GRACE_SECONDS):
            late = {"start": {"expectedStart": iso(self.expected_start), "minutesLate": int((now - self.expected_start).total_seconds() / 60),
                              "latest": [f"{r['run_id']}={r['status']}" for r in (self.last_journal.get("otherRuns") or [])[:3]]}}
        self.recurring("RUN_NOT_STARTED", "error", late)

    def consume_tasks(self, tasks: list[dict], now: dt.datetime) -> tuple[dict[str, int], list[dict]]:
        counts: dict[str, int] = {}
        running: list[dict] = []
        stale: dict[str, dict] = {}
        for t in tasks:
            st = str(t["status"])
            counts[st] = counts.get(st, 0) + 1
            key = t["task_key"]
            short = short_key(key)
            prev = self.task_status.get(key)
            if prev != st:
                extra = f" err={t.get('last_error_code')} {str(t.get('last_error') or '')[:160]}" if t.get("last_error_code") else ""
                retry = f" next={str(t.get('next_retry_at'))[11:19]}" if st == "RETRY" and t.get("next_retry_at") else ""
                self.log(f"task {short:<46} {prev or '(new)'} -> {st} att={t.get('attempts')}/{t.get('max_attempts')}{retry}{extra}")
                self.task_status[key] = st
                if st in FLAGGED_TASK_STATES:
                    sev = FLAGGED_TASK_STATES[st]
                    if st == "DEGRADED" and str(t.get("required_class") or "") == "core":
                        sev = "error"
                    self.anomaly("TASK_STATE_" + st, sev, {"task": short, "requiredClass": t.get("required_class"), "attempts": t.get("attempts"),
                                                           "interruptions": t.get("interruptions"), "errorCode": t.get("last_error_code"),
                                                           "error": t.get("last_error")})
            ints = int(t.get("interruptions") or 0)
            if ints >= INTERRUPTION_BUDGET and key not in self.interruption_flagged:
                self.interruption_flagged.add(key)
                self.anomaly("INTERRUPTION_BUDGET", "warn", {"task": short, "interruptions": ints, "attempts": t.get("attempts"), "max": t.get("max_attempts")})
            if st in ("RUNNING", "CLAIMED"):
                hb = parse_ts(t.get("heartbeat_at"))
                age = (now - hb).total_seconds() if hb else None
                running.append({"task": short, "attempts": t.get("attempts"), "interruptions": ints, "heartbeatAge": None if age is None else int(age)})
                if age is not None and age > STALE_HEARTBEAT_SECONDS:
                    stale[short] = {"heartbeatAge": int(age), "attempts": t.get("attempts")}
        self.recurring("STALE_HEARTBEAT", "warn", stale)
        return counts, running

    def consume_attempts(self, attempts: list[dict]) -> None:
        for a in attempts:
            ident = (a["task_key"], int(a["attempt_no"]))
            st = str(a["status"])
            if st not in FINISHED_ATTEMPT_STATES or ident in self.seen_attempts:
                continue
            self.seen_attempts.add(ident)
            s, e = parse_ts(a.get("started_at")), parse_ts(a.get("finished_at"))
            dur = (e - s).total_seconds() if s and e else None
            short = short_key(a["task_key"])
            base = baseline_for(short)
            self.log(f"attempt {short:<46} #{a['attempt_no']} {st:<11} {'' if dur is None else f'{dur:7.1f}s'} base={base} {a.get('error_code') or ''}")
            receipt = a.get("receipt_json")
            try:
                receipt_obj = json.loads(receipt) if receipt else None
            except (ValueError, TypeError):
                receipt_obj = receipt
            with self.receipt_path.open("a", encoding="utf-8") as fh:
                fh.write(scrub(json.dumps({"task": short, "attempt": a["attempt_no"], "status": st, "seconds": dur, "startedAt": a.get("started_at"),
                                           "finishedAt": a.get("finished_at"), "workerPid": a.get("worker_pid"), "errorCode": a.get("error_code"),
                                           "error": a.get("error_text"), "receipt": receipt_obj}, ensure_ascii=False, default=str)) + "\n")
            if st != "COMPLETED":
                self.anomaly("ATTEMPT_" + st, ATTEMPT_SEVERITY.get(st, "error"),
                             {"task": short, "attempt": a["attempt_no"], "seconds": dur, "errorCode": a.get("error_code"), "error": a.get("error_text")})
            if dur is not None and base is not None and dur > max(2 * base, base + 120):
                self.anomaly("SLOW_TASK", "info" if baseline_provisional(short) else "warn",
                             {"task": short, "attempt": a["attempt_no"], "seconds": round(dur, 1), "baseline": base})

    def consume_events(self, events: list[dict]) -> None:
        for ev in events:
            k = ev["event_key"]
            if k in self.seen_events:
                continue
            self.seen_events.add(k)
            et = str(ev["event_type"])
            payload = ev.get("payload_json") or ""
            full = et in ("identity.brief", "live.confirmed")
            self.log(f"event {str(ev.get('created_at'))[11:19]} {et:<28} {payload if full else payload[:220]}")
            if et == "identity.brief":
                with (self.out_dir / "identity-brief.txt").open("a", encoding="utf-8") as fh:
                    fh.write(f"{ev.get('created_at')}\n{scrub(payload)}\n\n")
            sev = event_severity(et)
            if sev:
                detail: dict = {"createdAt": ev.get("created_at"), "payload": payload}
                try:
                    pobj = json.loads(payload) if payload else {}
                except ValueError:
                    pobj = {}
                lp = (pobj.get("logPath") or pobj.get("log_path")) if isinstance(pobj, dict) else None
                if lp and lp not in self.seen_log_tails:
                    self.seen_log_tails.add(lp)
                    tl = tail_path(str(lp))
                    tdir = self.out_dir / "task-logs"
                    tdir.mkdir(exist_ok=True)
                    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{et}_{str(ev.get('created_at'))[:19]}_{Path(str(lp)).name}")[:120]
                    (tdir / f"{name}.txt").write_text(scrub(tl), encoding="utf-8")
                    detail["tailFile"] = name + ".txt"
                self.anomaly("EVENT_" + et, sev, detail)

    # -- one poll
    def poll_once(self) -> bool:
        """Returns True when observation is finished."""
        self.peaks["polls"] += 1
        now = utc_now()
        snap: dict = {"at": iso(now)}

        # WSL first: decides whether the journal can (and should) be read this poll
        wsl = probe_wsl()
        snap["wsl"] = wsl
        self.peaks["wslMsMax"] = max(self.peaks["wslMsMax"], int(wsl.get("ms") or 0))
        if not wsl.get("ok"):
            self.peaks["wslTimeouts"] += 1
        self.recurring("WSL_PROBE_FAILED", "error", {"wsl": {"error": wsl.get("error"), "ms": wsl.get("ms")}} if not wsl.get("ok") else {})
        idle_elapsed = self.last_journal_ok_at is None or time.monotonic() - self.last_journal_ok_at > JOURNAL_IDLE_REPROBE_SECONDS
        if wsl.get("running") is None:
            journal: dict = {"error": "WSL_UNRESPONSIVE", "ms": wsl.get("ms")}
        elif self.last_journal is None or wsl.get("running") or idle_elapsed:
            journal = probe_journal(self.requested_run_id)
        else:
            journal = {"skipped": "WSL_IDLE", "ms": 0}
            self.peaks["journalSkips"] += 1
        fresh = not journal.get("error") and not journal.get("skipped")
        snap["journal"] = {k: journal.get(k) for k in ("error", "skipped", "ms", "capturedAt", "roError", "requestedRunId", "resolvedRunId")}
        self.peaks["journalMsMax"] = max(self.peaks["journalMsMax"], int(journal.get("ms") or 0))
        if journal.get("error"):
            if "TIMEOUT" in journal["error"] or "UNRESPONSIVE" in journal["error"]:
                self.peaks["journalTimeouts"] += 1
            self.recurring("JOURNAL_PROBE_FAILED", "error", {"journal": {k: journal.get(k) for k in ("error", "stderr", "head", "exc") if journal.get(k)}})
        else:
            self.recurring("JOURNAL_PROBE_FAILED", "error", {})
        if fresh:
            self.last_journal = journal
            self.last_journal_ok_at = time.monotonic()
            (self.out_dir / "journal-latest.json").write_text(scrub(json.dumps(journal, ensure_ascii=False, default=str)), encoding="utf-8")
        data = journal if fresh else (self.last_journal or {})
        run = data.get("run")
        tasks = data.get("tasks") or []
        attempts = data.get("attempts") or []
        events = data.get("events") or []

        self.consume_run(run, fresh, journal, now)
        counts, running = self.consume_tasks(tasks, now)
        self.consume_attempts(attempts)
        self.consume_events(events)

        # launcher / ticks
        self.handle_launcher_lines(self.read_launcher())
        if self.scheduled and self.expected_start is not None:
            daily_task = probe_task_info(DAILY_TASK)
            snap["dailyTask"] = daily_task
            run_created = parse_ts(run.get("created_at")) if run else None
            repo_tick = self.last_tick_start
            if run_created is not None and (
                repo_tick is None or run_created > repo_tick
            ):
                repo_tick = run_created
            task_issues = task_tick_liveness(
                daily_task, self.expected_start, repo_tick, now,
            )
            for issue in (
                "TASK_PROBE_FAILED", "TASK_NOT_RUN", "TASK_REPO_TICK_MISMATCH",
            ):
                detail = task_issues.get(issue)
                self.recurring(issue, "error", {DAILY_TASK: detail} if detail else {})
        gap: dict = {}
        silent: dict = {}
        if run and self.run_status not in TERMINAL_RUN_STATES and self.scheduled and self.last_tick_end \
                and (self.last_tick_start is None or self.last_tick_start <= self.last_tick_end):
            idle = (now - self.last_tick_end).total_seconds()
            if idle > TICK_GAP_SECONDS:
                gap = {"tick": {"idleMin": int(idle / 60), "lastTickEnd": iso(self.last_tick_end)}}
            if idle > LAUNCHER_SILENT_SECONDS:
                silent = {"launcher": {"idleMin": int(idle / 60), "lastTickEnd": iso(self.last_tick_end)}}
        self.recurring("TICK_GAP", "warn", gap)
        self.recurring("LAUNCHER_SILENT", "error", silent)

        # health.json / tick-skipped.json
        hp = ROOT / "data" / "runtime" / "daily-chain-v2" / "health.json"
        try:
            h = json.loads(hp.read_text(encoding="utf-8"))
            self.health_unreadable_polls = 0
        except (OSError, ValueError):
            h = None
            self.health_unreadable_polls += 1
        self.recurring("HEALTH_UNREADABLE", "warn", {"health": {"path": str(hp), "polls": self.health_unreadable_polls}} if self.health_unreadable_polls >= 3 else {})
        health = {k: h.get(k) for k in ("run_id", "run_state", "tick_phase", "written_at_utc", "tick_exit_code", "tick_started_at_utc",
                                        "tick_ended_at_utc", "tick_duration_s", "next_retry_at_utc", "parked", "autonomous_proven", "last_alert")} if h else {"error": "unreadable"}
        snap["health"] = health
        stale: dict = {}
        if h and run and self.run_status not in TERMINAL_RUN_STATES and h.get("run_id") == self.resolved_run_id:
            w = parse_ts(h.get("written_at_utc"))
            age = (now - w).total_seconds() if w else None
            if age is not None and age > HEALTH_STALE_SECONDS:
                stale = {"health": {"ageMin": int(age / 60), "tickPhase": h.get("tick_phase"), "writtenAt": h.get("written_at_utc"), "runState": h.get("run_state")}}
        self.recurring("HEALTH_STALE", "error", stale)
        skipped = ROOT / "data" / "runtime" / "daily-chain-v2" / "tick-skipped.json"
        if skipped.exists():
            try:
                sk = json.loads(skipped.read_text(encoding="utf-8"))
                last = str(sk.get("last_skipped_at_utc") or "")
            except (OSError, ValueError):
                sk, last = {}, ""
            snap["tickSkipped"] = last or iso(dt.datetime.fromtimestamp(skipped.stat().st_mtime, dt.timezone.utc))
            if last and last not in self.seen_tick_skips:
                self.seen_tick_skips.add(last)
                t = parse_ts(last)
                ref = parse_ts(run.get("created_at")) if run else self.expected_start
                if t and (ref is None or t >= ref):
                    self.anomaly("TICK_SKIPPED_LOCKED", "warn", {"lastSkippedAt": last, "reason": sk.get("reason") if isinstance(sk, dict) else None})

        # host
        host = probe_host()
        snap["host"] = host
        if not host.get("error"):
            self.recurring("HOST_PROBE_FAILED", "warn", {})
            self.peaks["chromeCount"] = max(self.peaks["chromeCount"], int(host.get("chromeCount") or 0))
            self.peaks["chromeRssMb"] = max(self.peaks["chromeRssMb"], int(host.get("chromeRssMb") or 0))
            self.peaks["cpuMax"] = max(self.peaks["cpuMax"], float(host.get("cpu") or 0))
            fm = host.get("freeMb")
            low: dict = {}
            if fm is not None:
                self.peaks["freeMbMin"] = fm if self.peaks["freeMbMin"] is None else min(self.peaks["freeMbMin"], fm)
                if fm < FREE_RAM_MIN_MB:
                    low = {"ram": {"freeMb": fm}}
            self.recurring("LOW_FREE_RAM", "warn", low)
            cc, cr = int(host.get("chromeCount") or 0), int(host.get("chromeRssMb") or 0)
            if self.chrome_base is None:
                self.chrome_base = (cc, cr)
                self.log(f"host baseline chrome={cc} procs {cr} MB · free {fm} MB / {host.get('totalMb')} MB · cpu {host.get('cpu')}%")
            self.recurring("CHROME_COUNT_GROWTH", "warn", {"count": {"chromeCount": cc, "baseline": self.chrome_base[0], "chromeRssMb": cr}}
                           if cc - self.chrome_base[0] > CHROME_COUNT_GROWTH else {})
            self.recurring("CHROME_RSS_GROWTH", "warn", {"rss": {"chromeRssMb": cr, "baseline": self.chrome_base[1]}}
                           if cr - self.chrome_base[1] > CHROME_RSS_GROWTH_MB else {})
            self.cpu_high_polls = self.cpu_high_polls + 1 if float(host.get("cpu") or 0) > 95 else 0
            self.recurring("CPU_SATURATED", "warn", {"cpu": {"polls": self.cpu_high_polls, "cpu": host.get("cpu")}} if self.cpu_high_polls >= 3 else {})
        else:
            self.recurring("HOST_PROBE_FAILED", "warn", {"host": host})

        # CDP 9333 (PriceCharting lane) - only a finding once the run exists / the first tick fired, and only twice in a row
        cdp = probe_http("http://127.0.0.1:9333/json/version")
        tabs = probe_http("http://127.0.0.1:9333/json/list") if cdp.get("ok") else {"ok": False}
        snap["cdp9333"] = {"ok": cdp.get("ok"), "ms": cdp.get("ms"), "browser": (cdp.get("json") or {}).get("Browser") if cdp.get("ok") else None,
                           "tabs": len(tabs.get("json") or []) if tabs.get("ok") and isinstance(tabs.get("json"), list) else None}
        down = not cdp.get("ok")
        self.cdp_fail_streak = self.cdp_fail_streak + 1 if down else 0
        if down:
            self.peaks["cdpDowns"] += 1
        armed = bool(self.ticks) or (run is not None)
        flag = down and armed and self.cdp_fail_streak >= CDP_CONSECUTIVE_FAILS and self.run_status not in TERMINAL_RUN_STATES
        self.recurring("CDP_9333_DOWN", "error", {"9333": {"error": cdp.get("error"), "streak": self.cdp_fail_streak}} if flag else {})

        # MySQL 3308 long sessions (root inside the container; backs off after 3 timeouts)
        if self.peaks["polls"] >= self.mysql_backoff_until_poll:
            mysql = probe_mysql()
            if mysql.get("error") and "TIMEOUT" in mysql["error"]:
                self.mysql_timeouts += 1
                if self.mysql_timeouts >= 3:
                    self.mysql_backoff_until_poll = self.peaks["polls"] + MYSQL_BACKOFF_POLLS
                    self.mysql_timeouts = 0
                    self.log(f"mysql probe timed out 3x: backing off {MYSQL_BACKOFF_POLLS} polls")
            elif not mysql.get("error"):
                self.mysql_timeouts = 0
        else:
            mysql = {"skipped": "BACKOFF"}
        snap["mysql"] = mysql
        if not mysql.get("skipped"):
            self.recurring("MYSQL_PROBE_FAILED", "warn", {"probe": mysql} if mysql.get("error") else {})
            self.recurring("MYSQL_LONG_QUERY", "error", {str(r["id"]): r for r in (mysql.get("long") or []) if r["seconds"] > LONG_QUERY_SECONDS})

        snap["run"] = {k: run.get(k) for k in ("run_id", "status", "publication_status", "generation_id", "manual_intervention_count", "completed_at")} if run else None
        snap["taskCounts"] = counts
        snap["running"] = running
        with self.snap_path.open("a", encoding="utf-8") as fh:
            fh.write(scrub(json.dumps(snap, ensure_ascii=False, default=str)) + "\n")
        self.write_live(snap["at"], counts, running, host, wsl, snap["cdp9333"], mysql.get("long"), "watch")
        (self.out_dir / "ticks.json").write_text(json.dumps(self.ticks, indent=1), encoding="utf-8")

        # finished?
        if self.terminal_seen_at is not None and time.monotonic() - self.terminal_seen_at >= self.settle_seconds:
            self.log(f"run terminal ({self.run_status}) and settled {int(self.settle_seconds)} s: watch done")
            return True
        if time.monotonic() > self.deadline:
            self.log("max-hours reached: done")
            self.anomaly("OBSERVER_DEADLINE", "warn", {"runStatus": self.run_status})
            return True
        return False

    def write_live(self, at: str, counts: dict, running: list, host: dict, wsl: dict, cdp: dict, mysql_long, phase: str) -> None:
        live = {"runId": self.resolved_run_id, "requestedRunId": self.requested_run_id, "resolvedRunId": self.resolved_run_id,
                "at": at, "phase": phase, "runStatus": self.run_status, "generation": self.run_generation,
                "taskCounts": counts, "running": running, "host": host, "wsl": wsl, "cdp9333": cdp, "mysqlLong": mysql_long,
                "anomalyCounts": self.anomaly_counts, "open": sorted(self.active), "peaks": self.peaks, "ticks": len(self.ticks),
                "lastTickStart": iso(self.last_tick_start), "lastTickEnd": iso(self.last_tick_end), "promo": self.promo}
        self.live_path.write_text(scrub(json.dumps(live, ensure_ascii=False, indent=1, default=str)), encoding="utf-8")

    # -- after the run: the 17:45 JST promo leg
    def postrun_promo(self) -> None:
        promo_at = promo_time(self.day)
        if promo_at is None:
            return
        deadline_promo = promo_at + dt.timedelta(seconds=PROMO_WAIT_SLACK_SECONDS)
        if utc_now() > promo_at + dt.timedelta(hours=3):
            self.log(f"promo window {iso(promo_at)} long past: collecting what is there")
        elif utc_now() < deadline_promo:
            self.log(f"post-run: waiting for {PROMO_TASK} at {iso(promo_at)} (light polls, launcher + live.json only)")
            while utc_now() < deadline_promo and time.monotonic() < self.deadline:
                self.handle_launcher_lines(self.read_launcher())
                self.write_live(iso(utc_now()), {}, [], {}, {}, {}, None, "postrun-wait")
                time.sleep(min(300.0, max(5.0, (deadline_promo - utc_now()).total_seconds())))
        self.collect_promo(promo_at)

    def collect_promo(self, promo_at: dt.datetime) -> None:
        info = probe_task_info(PROMO_TASK)
        pdir, brief, scanned = resolve_promo_dir(self.day, self.run_generation)
        scheduler_path, scheduler_receipt = promo_scheduler_receipt(self.day)
        files = sorted(p.name for p in pdir.iterdir()) if pdir.exists() else []
        rec = {"at": iso(utc_now()), "promoAt": iso(promo_at), "task": info, "dir": str(pdir), "dirsScanned": scanned,
               "files": files, "brief": brief, "runGeneration": self.run_generation,
               "schedulerReceiptPath": str(scheduler_path), "schedulerReceipt": scheduler_receipt}
        (self.out_dir / "promo.json").write_text(json.dumps(rec, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
        self.promo = rec
        self.log(f"promo: task={json.dumps(info)} files={files} brief={json.dumps(brief, default=str)}"
                 f" schedulerReceipt={json.dumps(scheduler_receipt, default=str)}")
        # 2026-09-25: the owner stopped the promo chain on 2026-09-23 and left this
        # task Disabled.  While it is Disabled every promo check below is skipped:
        # that is the owner's decision, not a fault, so it is recorded as info
        # (report only) instead of paging PROMO_TASK_NOT_RUN / PROMO_BRIEF_MISSING
        # every evening.  Enabling the task brings every check back, no code change.
        if str(info.get("state") or "") == "Disabled":
            self.anomaly("PROMO_TASK_DISABLED", "info", {"last": info.get("last"), "promoAt": iso(promo_at)})
            return
        task_ran = False
        if info.get("error"):
            self.anomaly("PROMO_TASK_PROBE_FAILED", "warn", info)
        else:
            last = parse_ts(info.get("last"))
            if last is None or last < promo_at:
                self.anomaly("PROMO_TASK_NOT_RUN", "warn", {"last": info.get("last"), "promoAt": iso(promo_at), "state": info.get("state")})
            else:
                task_ran = True
                if int(info.get("rc") or 0) != 0:
                    self.anomaly("PROMO_TASK_RC_NONZERO", "error", {"rc": info.get("rc"), "last": info.get("last"), "source": "taskScheduler"})
        if task_ran:
            if scheduler_receipt is None:
                self.anomaly("PROMO_SCHEDULER_RECEIPT_MISSING", "error", {"path": str(scheduler_path), "last": info.get("last")})
            elif scheduler_receipt.get("error"):
                self.anomaly("PROMO_SCHEDULER_RECEIPT_INVALID", "error", {"path": str(scheduler_path), "error": scheduler_receipt.get("error")})
            else:
                receipt_at = parse_ts(scheduler_receipt.get("recordedAt"))
                if receipt_at is None or receipt_at < promo_at:
                    self.anomaly("PROMO_SCHEDULER_RECEIPT_STALE", "error", {"path": str(scheduler_path), "recordedAt": scheduler_receipt.get("recordedAt"), "promoAt": iso(promo_at)})
                if int(scheduler_receipt.get("exitCode") or 0) != 0:
                    self.anomaly("PROMO_TASK_RC_NONZERO", "error", {"rc": scheduler_receipt.get("exitCode"), "outcome": scheduler_receipt.get("outcome"), "artifact": scheduler_receipt.get("artifact"), "source": "schedulerReceipt"})
        if brief is None:
            self.anomaly("PROMO_BRIEF_MISSING", "warn", {"dir": str(pdir), "files": files})
        elif self.run_generation and brief.get("generation") and brief["generation"] != self.run_generation:
            self.anomaly("PROMO_GENERATION_MISMATCH", "warn", {"brief": brief.get("generation"), "run": self.run_generation,
                                                              "dir": str(pdir), "dirsScanned": scanned})

    def watch(self) -> int:
        self.log(f"observer start requestedRun={self.requested_run_id} poll={self.poll}s out={self.out_dir} root={ROOT} scheduled={self.scheduled}"
                 f" expectedStart={iso(self.expected_start)} promoSweep={self.promo_sweep}")
        missing = [str(p) for p in (ROOT / "logs" / "daily-chain-v2", ROOT / "data" / "runtime" / "daily-chain-v2" / "health.json") if not p.exists()]
        if missing:
            self.anomaly("OBSERVER_MISCONFIGURED", "warn", {"missing": missing, "root": str(ROOT)})
        while True:
            t0 = time.monotonic()
            done = False
            try:
                done = self.poll_once()
            except Exception as exc:  # noqa: BLE001 - the observer must outlive its own bugs
                self.anomaly("OBSERVER_EXCEPTION", "warn", {"error": f"{type(exc).__name__}: {exc}"[:400], "trace": traceback.format_exc()[-1200:]})
            if self.peaks["polls"] % 10 == 0 and self.last_journal is not None:
                try:
                    write_report(self.requested_run_id, self.out_dir, journal=self.last_journal)
                except Exception as exc:  # noqa: BLE001
                    self.log(f"report render failed: {type(exc).__name__}: {exc}")
            if done:
                break
            time.sleep(max(5.0, self.poll - (time.monotonic() - t0)))
        if self.promo_sweep and self.scheduled and self.terminal_seen_at is not None and time.monotonic() < self.deadline:
            try:
                self.postrun_promo()
            except Exception as exc:  # noqa: BLE001
                self.anomaly("OBSERVER_EXCEPTION", "warn", {"error": f"promo: {type(exc).__name__}: {exc}"[:400]})
        write_report(self.requested_run_id, self.out_dir)      # one live re-probe; falls back to journal-latest.json
        self.log(f"report {self.out_dir / 'report.md'}")
        return 0


# --------------------------------------------------------------------------- report

SEVERITY_RANK = {"error": 0, "warn": 1, "info": 2}


def _read_jsonl(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def load_cached_journal(out_dir: Path) -> dict | None:
    p = out_dir / "journal-latest.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None


def write_report(run_id: str, out_dir: Path, journal: dict | None = None) -> Path:
    snaps = _read_jsonl(out_dir / "snapshots.jsonl")
    anoms = _read_jsonl(out_dir / "anomalies.jsonl")
    receipts = _read_jsonl(out_dir / "receipts.jsonl")
    source = "live"
    if journal is None:
        journal = probe_journal(run_id)
    if journal.get("error") or not journal.get("run"):
        cached = load_cached_journal(out_dir)
        if cached and cached.get("run"):
            source = f"cached {cached.get('capturedAt')} (live: {journal.get('error') or 'no run'})"
            journal = cached
        else:
            source = f"none (live: {journal.get('error') or 'no run'})"
    run = journal.get("run") or {}
    resolved_run_id = str(journal.get("resolvedRunId") or run.get("run_id") or run_id)
    lines = [f"# V2 run observer report — `{resolved_run_id}`", ""]
    lines.append(f"- rendered {iso(utc_now())} · polls {len(snaps)} · anomalies {len(anoms)} · journal source: {source}")
    if resolved_run_id != run_id:
        lines.append(f"- requested `{run_id}` · resolved current scheduled run `{resolved_run_id}`")
    if run:
        c, d = parse_ts(run.get("created_at")), parse_ts(run.get("completed_at"))
        wall = f"{(d - c).total_seconds() / 60:.1f} min" if c and d else "(not completed)"
        lines.append(f"- status **{run.get('status')}** · publication {run.get('publication_status')} · generation `{run.get('generation_id')}`"
                     f" · origin {run.get('origin')} · created {iso(c)} · completed {iso(d)} · wall {wall}"
                     f" · interventions {run.get('manual_intervention_count')} · renewals {run.get('manual_window_renewals')}"
                     f" · proven_autonomous {run.get('proven_autonomous')} · degraded {run.get('degraded_sources_json')}")
        lines.append(f"- schedule: cutoff {str(run.get('source_cutoff_at'))[:19]} · sla {str(run.get('sla_at'))[:19]} · final {str(run.get('final_at'))[:19]}")
    else:
        others = ", ".join(f"{r.get('run_id')}={r.get('status')}" for r in (journal.get("otherRuns") or [])[:5])
        lines.append(f"- run row absent · latest runs: {others or 'n/a'}")
    lines += ["", "## Tasks (attempt seconds vs baseline)", "", "| task | phase | class | status | att | int | attempt seconds | baseline | errors |", "|---|---|---|---|---|---|---|---|---|"]
    by_task: dict[str, list[dict]] = {}
    for a in journal.get("attempts") or []:
        by_task.setdefault(a["task_key"], []).append(a)
    for t in journal.get("tasks") or []:
        atts = by_task.get(t["task_key"], [])
        secs = []
        for a in atts:
            s, e = parse_ts(a.get("started_at")), parse_ts(a.get("finished_at"))
            secs.append(f"{(e - s).total_seconds():.0f}" if s and e else "…")
        errs = "; ".join(f"#{a['attempt_no']} {a['status']} {a.get('error_code') or ''}" for a in atts if a.get("error_code") or a.get("status") not in ("COMPLETED", "RUNNING"))
        short = short_key(t["task_key"])
        base = baseline_for(short)
        mark = ""
        for sec in secs:
            if sec != "…" and base is not None and float(sec) > max(2 * base, base + 120):
                mark = " ⚠"
        lines.append(f"| `{short}` | {t.get('phase')} | {t.get('required_class') or ''} | {t.get('status')} | {t.get('attempts')}/{t.get('max_attempts')} | {t.get('interruptions') or 0}"
                     f" | {', '.join(secs)}{mark} | {base if base is not None else ''} | {scrub(errs).replace('|', '¦')} |")
    lines += ["", "## Events", "", "| at | type | sev | payload |", "|---|---|---|---|"]
    for ev in journal.get("events") or []:
        sev = event_severity(str(ev.get("event_type"))) or "info"
        lines.append(f"| {str(ev.get('created_at'))[11:19]} | {ev.get('event_type')} | {sev} | `{scrub((ev.get('payload_json') or '')[:300]).replace('|', '¦')}` |")
    lines += ["", "## Anomalies (batch-fix input; error → warn → info, then by count)", ""]
    grouped: dict[str, list[dict]] = {}
    for a in anoms:
        grouped.setdefault(a["kind"], []).append(a)
    if not grouped:
        lines.append("none recorded")
    for kind, items in sorted(grouped.items(), key=lambda kv: (SEVERITY_RANK.get(kv[1][0].get("severity"), 9), -len(kv[1]), kv[0])):
        first, last = items[0]["at"], items[-1]["at"]
        lines.append(f"### {kind} × {len(items)} ({items[0]['severity']}) · {first} → {last}")
        for it in items[:8]:
            detail = {k: v for k, v in it.items() if k not in ("at", "kind", "severity", "payload", "trace")}
            lines.append(f"- {it['at'][11:19]} `{json.dumps(detail, ensure_ascii=False, default=str)[:500].replace('|', '¦')}`")
        if len(items) > 8:
            lines.append(f"- … {len(items) - 8} more in anomalies.jsonl")
        lines.append("")
    ticks = []
    tp = out_dir / "ticks.json"
    if tp.exists():
        try:
            ticks = json.loads(tp.read_text(encoding="utf-8"))
        except ValueError:
            ticks = []
    if ticks:
        lines += ["## Launcher ticks", "", "| start | end | exit | seconds |", "|---|---|---|---|"]
        for tk in ticks:
            lines.append(f"| {tk.get('start') or ''} | {tk.get('end') or ''} | {tk.get('exit') if tk.get('exit') is not None else ''} | {tk.get('seconds') if tk.get('seconds') is not None else ''} |")
        nz = [tk for tk in ticks if tk.get("exit") not in (0, None)]
        lines.append("")
        lines.append(f"- ticks seen {len(ticks)} · non-zero exits {len(nz)} · longest {max((tk.get('seconds') or 0) for tk in ticks)} s")
        lines.append("")
    pp = out_dir / "promo.json"
    if pp.exists():
        try:
            promo = json.loads(pp.read_text(encoding="utf-8"))
        except ValueError:
            promo = {}
        lines += ["## Promo (CARDZ-Promo-After-Publish)", "",
                  f"- task: `{json.dumps(promo.get('task'), default=str)}` · expected at {promo.get('promoAt')}",
                  f"- dir `{promo.get('dir')}` files {promo.get('files')}",
                  f"- scheduler receipt: `{json.dumps(promo.get('schedulerReceipt'), ensure_ascii=False, default=str)}`",
                  f"- brief: `{json.dumps(promo.get('brief'), ensure_ascii=False, default=str)}` · run generation `{promo.get('runGeneration')}`", ""]
    if snaps:
        hosts = [s.get("host") or {} for s in snaps if not (s.get("host") or {}).get("error")]
        wsls = [s.get("wsl") or {} for s in snaps]
        js = [s.get("journal") or {} for s in snaps]
        cdps = [s.get("cdp9333") or {} for s in snaps]

        def mx(key, rows, default=0):
            vals = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
            return max(vals) if vals else default

        def mn(key, rows):
            vals = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
            return min(vals) if vals else None
        lines += ["## Host / probes", "",
                  f"- chrome.exe count max {mx('chromeCount', hosts)} · chrome RSS max {mx('chromeRssMb', hosts)} MB · CPU max {mx('cpu', hosts)} % · free RAM min {mn('freeMb', hosts)} MB",
                  f"- WSL probe: max {mx('ms', wsls)} ms · failures {sum(1 for w in wsls if not w.get('ok'))} / {len(wsls)} · Ubuntu running on {sum(1 for w in wsls if w.get('running'))} polls",
                  f"- journal snapshot: max {mx('ms', js)} ms · failures {sum(1 for j in js if j.get('error'))} · skipped (WSL idle) {sum(1 for j in js if j.get('skipped'))} / {len(js)}",
                  f"- CDP 9333: downs {sum(1 for c in cdps if c.get('ok') is False)} / {len(cdps)} · tabs max {mx('tabs', cdps)}",
                  f"- MySQL >60 s queries seen: {sum(len((s.get('mysql') or {}).get('long') or []) for s in snaps)} rows across polls",
                  f"- receipts recorded: {len(receipts)} finished attempts",
                  ""]
    text = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps({
        "runId": resolved_run_id, "requestedRunId": run_id, "resolvedRunId": resolved_run_id,
        "status": run.get("status"), "publicationStatus": run.get("publication_status"),
        "generation": run.get("generation_id"), "origin": run.get("origin"), "provenAutonomous": run.get("proven_autonomous"),
        "anomalyKinds": {k: len(v) for k, v in grouped.items()},
        "errors": sum(1 for a in anoms if a.get("severity") == "error"), "warns": sum(1 for a in anoms if a.get("severity") == "warn"),
        "polls": len(snaps), "ticks": len(ticks), "journalSource": source, "renderedAt": iso(utc_now())}, indent=1), encoding="utf-8")
    return out_dir / "report.md"


# --------------------------------------------------------------------------- cli

def resolve_run_id(args: argparse.Namespace) -> str:
    if args.run_id:
        return args.run_id
    day = args.business_date or dt.datetime.now(JST).date().isoformat()
    return f"cardz-v2:{day}"


def acquire_lock(out_dir: Path) -> bool:
    """One observer per run folder.  A stale lock (pid gone / not python) is taken over; a live one is refused."""
    lock = out_dir / "observer.lock"
    if lock.exists():
        raw = lock.read_text(encoding="utf-8").strip()
        try:
            info = json.loads(raw) if raw.startswith("{") else {"pid": int(raw or 0)}
        except ValueError:
            info = {"pid": 0}
        pid = int(info.get("pid") or 0)
        if pid:
            if os.name == "nt":
                rc, out, _ = run_capped(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], 15)
                live_python = rc == 0 and f'"{pid}"' in out and "python" in out.lower()
            else:
                try:
                    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\x00", b" ").lower()
                except OSError:
                    cmdline = b""
                live_python = b"python" in cmdline
            if live_python:
                (out_dir / "lock-conflict.json").write_text(json.dumps({"refusedAt": iso(utc_now()), "holder": info, "me": os.getpid()}, indent=1), encoding="utf-8")
                return False
    lock.write_text(json.dumps({"pid": os.getpid(), "startedAt": iso(utc_now()), "image": sys.executable}), encoding="utf-8")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot")
    snap.add_argument("run_id")
    for name in ("watch", "report"):
        p = sub.add_parser(name)
        p.add_argument("--run-id")
        p.add_argument("--business-date")
        p.add_argument("--out-root", type=Path, default=ROOT / "data" / "runtime" / "daily-chain-v2" / "observer")
        if name == "watch":
            p.add_argument("--start-at", help="ISO-8601 UTC; sleep until then before the first poll")
            p.add_argument("--poll", type=float, default=60.0)
            p.add_argument("--max-hours", type=float, default=14.75)
            p.add_argument("--settle-minutes", type=float, default=25.0, help="keep polling this long after a terminal state (post-publish ticks)")
            p.add_argument("--no-promo-sweep", action="store_true", help="exit right after the settle instead of waiting for the 17:45 JST promo task")
            p.add_argument("--notify", action="store_true", help="deliver warn/error anomalies through notify_hermes")
    args = ap.parse_args()
    if args.command == "snapshot":
        return cmd_snapshot(args.run_id)
    run_id = resolve_run_id(args)
    out_dir = args.out_root / run_id.removeprefix("cardz-v2:").replace("#", "-").replace("/", "-")
    if args.command == "report":
        print(write_report(run_id, out_dir))
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    if not acquire_lock(out_dir):
        print(f"observer already running for {run_id}; see {out_dir / 'lock-conflict.json'}")
        return 3
    try:
        if args.start_at:
            target = parse_ts(args.start_at)
            while target and utc_now() < target:
                time.sleep(min(60.0, max(1.0, (target - utc_now()).total_seconds())))
        return Observer(run_id, out_dir, poll=args.poll, max_hours=args.max_hours, settle_minutes=args.settle_minutes,
                        promo_sweep=not args.no_promo_sweep, notify_alerts=args.notify).watch()
    finally:
        try:
            (out_dir / "observer.lock").unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
