#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only observer for one V2 daily-chain run (the "監測器" daddy asked for on 2026-08-23).

Runs on the Windows side (the stable half of this box: WSL died 3x on 2026-08-23
while docker / MySQL 3308 kept answering) and, once a minute, records everything
the batch-fix pass needs afterwards:

  * the V2 journal (chain_run / chain_task / chain_attempt / chain_event), read
    INSIDE WSL through `wsl.exe -d Ubuntu -- python3 <this file> snapshot <run>`
    so sqlite locking is honoured (never open the journal over \\wsl.localhost);
  * the tick launcher log (CARDZ_V2_START / _END / _WSL_PREFLIGHT, 0x8007274c);
  * health.json / tick-skipped.json;
  * host: chrome.exe count + working set, CPU %, free RAM, CDP 9333 liveness +
    tab count, WSL probe latency;
  * MySQL 3308 sessions running a query for > 60 s (root, via docker exec; the
    password is expanded inside the container and never printed).

It never writes to the repo, the journal, or MySQL, never takes a chain lease,
and never kills anything.  Findings are appended to
`data/runtime/daily-chain-v2/observer/<business-date>/` as they happen
(observer.log for humans, snapshots.jsonl / anomalies.jsonl for machines,
live.json = latest state) and a report.md is rendered when the run reaches a
terminal state (PUBLISHED / PUBLISHED_DEGRADED / FAILED_FINAL / ABORTED).

Usage (Windows):
  python -X utf8 -u scripts/v2_run_observer.py watch [--business-date YYYY-MM-DD | --run-id cardz-v2:YYYY-MM-DD[#LABEL]]
        [--start-at ISO-UTC] [--poll 60] [--max-hours 14] [--settle-minutes 25]
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
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JST = dt.timezone(dt.timedelta(hours=9), "JST")
TERMINAL_RUN_STATES = {"PUBLISHED", "PUBLISHED_DEGRADED", "FAILED_FINAL", "ABORTED"}
FINISHED_ATTEMPT_STATES = {"COMPLETED", "RETRY", "TERMINAL", "INTERRUPTED"}

# Events the chain itself treats as trouble (daily_chain_v2 alert table + what
# the 2026-08-20..24 journals actually contain).  Everything else is "info".
ANOMALY_EVENTS = {
    "TASK_ERROR": "warn",
    "TASK_RETRY_STATE": "warn",
    "CORE_TASK_PARKED": "error",
    "SLA_MISSED": "error",
    "ORCHESTRATOR_ERROR": "error",
    "TICK_CRASHED": "error",
    "RUN_ABORTED": "error",
    "DB_LONG_SESSIONS_OBSERVED": "warn",
    "TICK_DRAINING": "warn",
    "MANUAL_WINDOW_RENEWED": "warn",
    "TASK_UNPARKED": "warn",
    "TASK_RETIRED": "warn",
    "PUBLISH_TERMINAL": "error",
}

# Expected attempt seconds per task kind [KNOWN: real runs 2026-08-23/24 +
# rehearsal A10].  A finished attempt slower than max(2x, +120 s) is flagged.
BASELINE_SECONDS = (
    ("gemrate:contract-repair", 1300),
    ("gemrate:pop+identity", 900),
    ("pricecharting:contract-repair", 240),
    ("pricecharting:", 330),
    ("snkrdunk:contract-repair", 40),
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
STALE_HEARTBEAT_SECONDS = 180        # TASK_LEASE_SECONDS is 90 in the chain
TICK_GAP_SECONDS = 12 * 60           # scheduler fires every 10 min
LONG_QUERY_SECONDS = 600
CHROME_COUNT_GROWTH = 24             # vs the first poll: daddy's own Chrome is always open (60+ procs), only growth matters
CHROME_RSS_GROWTH_MB = 4096
FREE_RAM_MIN_MB = 2048

MAIN_JOURNAL = "~/.local/state/cardz-marketcap/daily-chain-v2{suffix}.sqlite3"


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(t: dt.datetime | None) -> str:
    return t.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if t else ""


def parse_ts(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        t = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


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


def wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if len(s) > 1 and s[1] == ":":
        s = f"/mnt/{s[0].lower()}{s[2:]}"
    return s


# --------------------------------------------------------------------------- snapshot (runs inside WSL)

def cmd_snapshot(run_id: str) -> int:
    import sqlite3

    day, _, label = run_id.removeprefix("cardz-v2:").partition("#")
    path = os.path.expanduser(MAIN_JOURNAL.format(suffix=f"-{label}" if label else ""))
    out: dict = {"runId": run_id, "journalPath": path, "capturedAt": iso(utc_now())}
    if not os.path.exists(path):
        out["error"] = "NO_JOURNAL"
        print(json.dumps(out))
        return 0
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    run = c.execute("SELECT * FROM chain_run WHERE run_id=?", (run_id,)).fetchone()
    out["run"] = dict(run) if run else None
    out["otherRuns"] = [dict(r) for r in c.execute(
        "SELECT run_id,business_date,status,publication_status,created_at,completed_at FROM chain_run ORDER BY created_at DESC LIMIT 5")]
    if run is None:
        print(json.dumps(out, default=str))
        return 0
    out["tasks"] = [dict(r) for r in c.execute(
        "SELECT task_key,phase,source_code,capability,concurrency_group,status,attempts,max_attempts,next_retry_at,"
        " heartbeat_at,last_error_code,substr(last_error,1,400) AS last_error,created_at,updated_at,interruptions,"
        " substr(checkpoint_json,1,300) AS checkpoint_json FROM chain_task WHERE run_id=? ORDER BY created_at", (run_id,))]
    out["attempts"] = [dict(r) for r in c.execute(
        "SELECT a.task_key,a.attempt_no,a.status,a.started_at,a.heartbeat_at,a.finished_at,a.worker_pid,a.error_code,"
        " substr(a.error_text,1,400) AS error_text, substr(a.receipt_json,1,600) AS receipt_json"
        " FROM chain_attempt a JOIN chain_task t ON t.task_key=a.task_key WHERE t.run_id=? ORDER BY a.started_at, a.attempt_no",
        (run_id,))]
    out["events"] = [dict(r) for r in c.execute(
        "SELECT event_key,event_type,created_at,delivered,substr(payload_json,1,1500) AS payload_json"
        " FROM chain_event WHERE run_id=? ORDER BY created_at", (run_id,))]
    print(json.dumps(out, default=str, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- host probes (Windows)

def run_capped(cmd: list[str], timeout: float, *, text: bool = True) -> tuple[int | None, str, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, text=text, encoding="utf-8", errors="replace",
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"
    except OSError as exc:
        return -1, "", f"OSError {exc}"


def probe_journal(run_id: str) -> dict:
    t0 = time.monotonic()
    rc, out, err = run_capped(["wsl.exe", "-d", "Ubuntu", "--", "python3", "-X", "utf8", wsl_path(Path(__file__)), "snapshot", run_id], 75)
    ms = int((time.monotonic() - t0) * 1000)
    out = out.replace("\x00", "")
    if rc is None:
        return {"error": "WSL_SNAPSHOT_TIMEOUT", "ms": ms}
    if rc != 0:
        return {"error": f"WSL_SNAPSHOT_RC_{rc}", "ms": ms, "stderr": err.replace("\x00", "")[-400:]}
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else {"error": "WSL_SNAPSHOT_EMPTY"}
    except (ValueError, json.JSONDecodeError) as exc:
        return {"error": "WSL_SNAPSHOT_BAD_JSON", "ms": ms, "head": out[:200], "exc": str(exc)}
    data["ms"] = ms
    return data


def probe_http(url: str, timeout: float = 5.0) -> dict:
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
    "$c=(Get-Counter '\\Processor(_Total)\\% Processor Time' -SampleInterval 1 -MaxSamples 1).CounterSamples[0].CookedValue;"
    "$os=Get-CimInstance Win32_OperatingSystem;"
    "$ch=@(Get-Process chrome -ErrorAction SilentlyContinue);"
    "$ws=($ch | Measure-Object WorkingSet64 -Sum).Sum;"
    "$w=@(Get-Process wslservice,wslhost -ErrorAction SilentlyContinue).Count;"
    "[pscustomobject]@{cpu=[math]::Round($c,1);freeMb=[int]($os.FreePhysicalMemory/1KB);totalMb=[int]($os.TotalVisibleMemorySize/1KB);"
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
    t0 = time.monotonic()
    rc, _, err = run_capped(["wsl.exe", "-d", "Ubuntu", "--", "true"], 30)
    ms = int((time.monotonic() - t0) * 1000)
    if rc is None:
        return {"ok": False, "ms": ms, "error": "TIMEOUT"}
    return {"ok": rc == 0, "ms": ms, "rc": rc, "error": err.replace("\x00", "")[:160] if rc else ""}


MYSQL_SQL = (
    "SET SESSION max_execution_time=20000; SELECT ID,USER,TIME,STATE,LEFT(REPLACE(REPLACE(INFO,'\\n',' '),'\\r',' '),240)"
    " FROM information_schema.PROCESSLIST WHERE COMMAND='Query' AND TIME>60 AND INFO NOT LIKE '%information_schema.PROCESSLIST%'"
)


def probe_mysql() -> dict:
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    try:
        p = subprocess.run(
            ["docker", "exec", "cardz-market-cap-db-1", "sh", "-lc",
             'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -B -e "' + MYSQL_SQL.replace('"', '\\"') + '"'],
            capture_output=True, timeout=40, text=True, encoding="utf-8", errors="replace", env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return {"error": "MYSQL_PROBE_TIMEOUT"}
    except OSError as exc:
        return {"error": f"MYSQL_PROBE_OSERROR {exc}"}
    if p.returncode != 0:
        err = "\n".join(l for l in (p.stderr or "").splitlines() if "Using a password" not in l)
        return {"error": f"MYSQL_PROBE_RC_{p.returncode}", "stderr": err[-300:]}
    rows = []
    for line in (p.stdout or "").splitlines():
        cells = line.split("\t")
        if len(cells) >= 5 and cells[2].isdigit():
            rows.append({"id": int(cells[0]), "user": cells[1], "seconds": int(cells[2]), "state": cells[3], "sql": cells[4][:240]})
    return {"long": rows}


# --------------------------------------------------------------------------- the watcher

class Observer:
    def __init__(self, run_id: str, out_dir: Path, *, poll: float, max_hours: float, settle_minutes: float) -> None:
        self.run_id = run_id
        self.day = run_id.removeprefix("cardz-v2:").split("#")[0]
        self.out_dir = out_dir
        self.poll = poll
        self.deadline = time.monotonic() + max_hours * 3600
        self.settle_seconds = settle_minutes * 60
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = out_dir / "observer.log"
        self.snap_path = out_dir / "snapshots.jsonl"
        self.anom_path = out_dir / "anomalies.jsonl"
        self.live_path = out_dir / "live.json"
        self.seen_events: set[str] = set()
        self.seen_attempts: set[tuple[str, int]] = set()
        self.task_status: dict[str, str] = {}
        self.run_status: str | None = None
        self.launcher_offsets: dict[str, int] = {}
        self.last_tick_start: dt.datetime | None = None
        self.last_tick_end: dt.datetime | None = None
        self.cpu_high_polls = 0
        self.chrome_base: tuple[int, int] | None = None
        self.terminal_seen_at: float | None = None
        self.peaks = {"chromeCount": 0, "chromeRssMb": 0, "cpuMax": 0.0, "freeMbMin": None, "wslMsMax": 0, "wslTimeouts": 0,
                      "journalMsMax": 0, "journalTimeouts": 0, "cdpDowns": 0, "polls": 0}
        self.anomaly_counts: dict[str, int] = {}

    # -- output helpers
    def log(self, line: str) -> None:
        stamp = utc_now().strftime("%H:%M:%SZ")
        text = f"{stamp} {line}"
        print(text, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    def anomaly(self, kind: str, severity: str, detail: dict) -> None:
        rec = {"at": iso(utc_now()), "kind": kind, "severity": severity, **detail}
        with self.anom_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.anomaly_counts[kind] = self.anomaly_counts.get(kind, 0) + 1
        brief = json.dumps({k: v for k, v in detail.items() if k not in ("payload",)}, ensure_ascii=False, default=str)
        self.log(f"!! {severity.upper():5} {kind} {brief[:400]}")

    # -- launcher log (UTF-8, written by cardz_daily_v2_launcher.ps1 with Add-Content -Encoding UTF8)
    def read_launcher(self) -> list[str]:
        new: list[str] = []
        local_day = dt.datetime.now().strftime("%Y%m%d")
        days = {local_day, (dt.datetime.now() - dt.timedelta(days=1)).strftime("%Y%m%d")}
        for d in sorted(days):
            p = ROOT / "logs" / "daily-chain-v2" / f"launcher-{d}.log"
            if not p.exists():
                continue
            raw = p.read_bytes()
            off = self.launcher_offsets.get(str(p), 0)
            if off == 0 and d != local_day:
                self.launcher_offsets[str(p)] = len(raw)   # yesterday: only what is appended from now on
                continue
            if len(raw) > off:
                chunk = raw[off:]
                text = chunk.decode("utf-8", "replace").replace("\x00", "")
                text = text.replace(chr(0xFEFF), "")   # launcher log starts with a UTF-8 BOM (Add-Content -Encoding UTF8)
                new.extend(l for l in text.splitlines() if l.strip())
                self.launcher_offsets[str(p)] = len(raw)
        return new

    def handle_launcher_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.log(f"tick| {line[:300]}")
            if "CARDZ_V2_START" in line:
                self.last_tick_start = utc_now()
            if "CARDZ_V2_END" in line:
                self.last_tick_end = utc_now()
                m = re.search(r"exit=(-?\d+)", line)
                if m and m.group(1) != "0":
                    self.anomaly("TICK_EXIT_NONZERO", "error", {"line": line[:300]})
            if "CARDZ_V2_WSL_PREFLIGHT" in line and "WSL_OK" not in line:
                self.anomaly("WSL_PREFLIGHT_NOT_OK", "warn", {"line": line[:300]})
            if "0x8007274c" in line or "Wsl/Service" in line:
                self.anomaly("WSL_SERVICE_ERROR", "error", {"line": line[:300]})

    # -- one poll
    def poll_once(self) -> bool:
        """Returns True when observation is finished."""
        self.peaks["polls"] += 1
        now = utc_now()
        snap: dict = {"at": iso(now)}
        journal = probe_journal(self.run_id)
        snap["journal"] = {k: journal.get(k) for k in ("error", "ms", "capturedAt")}
        self.peaks["journalMsMax"] = max(self.peaks["journalMsMax"], int(journal.get("ms") or 0))
        if journal.get("error"):
            if "TIMEOUT" in journal["error"]:
                self.peaks["journalTimeouts"] += 1
            self.anomaly("JOURNAL_PROBE_FAILED", "error", {k: journal.get(k) for k in ("error", "stderr", "head", "exc")})
        run = journal.get("run")
        tasks = journal.get("tasks") or []
        attempts = journal.get("attempts") or []
        events = journal.get("events") or []

        # run status
        if run:
            status = str(run.get("status"))
            if status != self.run_status:
                self.log(f"RUN {self.run_id} status {self.run_status} -> {status} pub={run.get('publication_status')} gen={run.get('generation_id')}"
                         f" created={str(run.get('created_at'))[:19]} sla={str(run.get('sla_at'))[11:19]} final={str(run.get('final_at'))[11:19]}"
                         f" cutoff={str(run.get('source_cutoff_at'))[11:19]} interventions={run.get('manual_intervention_count')}")
                self.run_status = status
                if status in TERMINAL_RUN_STATES and self.terminal_seen_at is None:
                    self.terminal_seen_at = time.monotonic()
            sla = parse_ts(run.get("sla_at")); final = parse_ts(run.get("final_at"))
            if status not in TERMINAL_RUN_STATES:
                if sla and now > sla and self.anomaly_counts.get("SLA_EXCEEDED", 0) == 0:
                    self.anomaly("SLA_EXCEEDED", "error", {"slaAt": iso(sla), "status": status})
                if final and now > final and self.anomaly_counts.get("FINAL_EXCEEDED", 0) == 0:
                    self.anomaly("FINAL_EXCEEDED", "error", {"finalAt": iso(final), "status": status})
        elif not journal.get("error"):
            if self.run_status != "NOT_STARTED":
                others = ", ".join(f"{r['run_id']}={r['status']}" for r in (journal.get("otherRuns") or [])[:3])
                self.log(f"RUN {self.run_id} not in journal yet (latest: {others})")
                self.run_status = "NOT_STARTED"

        # tasks
        counts: dict[str, int] = {}
        running: list[dict] = []
        for t in tasks:
            st = str(t["status"]); counts[st] = counts.get(st, 0) + 1
            key = t["task_key"]; short = short_key(key)
            prev = self.task_status.get(key)
            if prev != st:
                extra = f" err={t.get('last_error_code')} {str(t.get('last_error') or '')[:160]}" if t.get("last_error_code") else ""
                self.log(f"task {short:<46} {prev or '(new)'} -> {st} att={t.get('attempts')}/{t.get('max_attempts')}{extra}")
                self.task_status[key] = st
                if st in ("PARKED", "TERMINAL", "SKIPPED"):
                    self.anomaly("TASK_STATE_" + st, "error" if st != "SKIPPED" else "warn",
                                 {"task": short, "attempts": t.get("attempts"), "errorCode": t.get("last_error_code"), "error": t.get("last_error")})
            if st in ("RUNNING", "CLAIMED"):
                hb = parse_ts(t.get("heartbeat_at")); age = (now - hb).total_seconds() if hb else None
                running.append({"task": short, "attempts": t.get("attempts"), "heartbeatAge": None if age is None else int(age)})
                if age is not None and age > STALE_HEARTBEAT_SECONDS:
                    self.anomaly("STALE_HEARTBEAT", "warn", {"task": short, "heartbeatAge": int(age)})
        # attempts
        for a in attempts:
            ident = (a["task_key"], int(a["attempt_no"]))
            st = str(a["status"])
            if st in FINISHED_ATTEMPT_STATES and ident not in self.seen_attempts:
                self.seen_attempts.add(ident)
                s, e = parse_ts(a.get("started_at")), parse_ts(a.get("finished_at"))
                dur = (e - s).total_seconds() if s and e else None
                short = short_key(a["task_key"]); base = baseline_for(short)
                self.log(f"attempt {short:<46} #{a['attempt_no']} {st:<10} {'' if dur is None else f'{dur:7.1f}s'} base={base} {a.get('error_code') or ''}")
                if st != "COMPLETED":
                    self.anomaly("ATTEMPT_" + st, "warn" if st == "RETRY" else "error",
                                 {"task": short, "attempt": a["attempt_no"], "seconds": dur, "errorCode": a.get("error_code"), "error": a.get("error_text")})
                if dur is not None and base is not None and dur > max(2 * base, base + 120):
                    self.anomaly("SLOW_TASK", "warn", {"task": short, "attempt": a["attempt_no"], "seconds": round(dur, 1), "baseline": base})
        # events
        for ev in events:
            k = ev["event_key"]
            if k in self.seen_events:
                continue
            self.seen_events.add(k)
            et = str(ev["event_type"]); payload = ev.get("payload_json") or ""
            self.log(f"event {str(ev.get('created_at'))[11:19]} {et:<28} {payload[:220]}")
            if et in ANOMALY_EVENTS:
                self.anomaly("EVENT_" + et, ANOMALY_EVENTS[et], {"createdAt": ev.get("created_at"), "payload": payload})

        # launcher / health
        self.handle_launcher_lines(self.read_launcher())
        if run and self.run_status not in TERMINAL_RUN_STATES and self.last_tick_start:
            gap = (now - self.last_tick_start).total_seconds()
            if self.last_tick_end and self.last_tick_end > self.last_tick_start and gap > TICK_GAP_SECONDS \
                    and self.anomaly_counts.get("TICK_GAP", 0) < 3:
                self.anomaly("TICK_GAP", "warn", {"secondsSinceTickStart": int(gap)})
        health: dict = {}
        hp = ROOT / "data" / "runtime" / "daily-chain-v2" / "health.json"
        try:
            h = json.loads(hp.read_text(encoding="utf-8"))
            health = {k: h.get(k) for k in ("run_id", "run_state", "tick_exit_code", "tick_ended_at_utc", "tick_duration_s", "parked", "last_alert")}
        except (OSError, ValueError):
            health = {"error": "health.json unreadable"}
        snap["health"] = health
        skipped = ROOT / "data" / "runtime" / "daily-chain-v2" / "tick-skipped.json"
        if skipped.exists():
            snap["tickSkippedMtime"] = iso(dt.datetime.fromtimestamp(skipped.stat().st_mtime, dt.timezone.utc))

        # host
        host = probe_host(); snap["host"] = host
        if not host.get("error"):
            self.peaks["chromeCount"] = max(self.peaks["chromeCount"], int(host.get("chromeCount") or 0))
            self.peaks["chromeRssMb"] = max(self.peaks["chromeRssMb"], int(host.get("chromeRssMb") or 0))
            self.peaks["cpuMax"] = max(self.peaks["cpuMax"], float(host.get("cpu") or 0))
            fm = host.get("freeMb")
            if fm is not None:
                self.peaks["freeMbMin"] = fm if self.peaks["freeMbMin"] is None else min(self.peaks["freeMbMin"], fm)
                if fm < FREE_RAM_MIN_MB:
                    self.anomaly("LOW_FREE_RAM", "warn", {"freeMb": fm})
            cc, cr = int(host.get("chromeCount") or 0), int(host.get("chromeRssMb") or 0)
            if self.chrome_base is None:
                self.chrome_base = (cc, cr)
                self.log(f"host baseline chrome={cc} procs {cr} MB · free {fm} MB / {host.get('totalMb')} MB · cpu {host.get('cpu')}%")
            if cc - self.chrome_base[0] > CHROME_COUNT_GROWTH:
                self.anomaly("CHROME_COUNT_GROWTH", "warn", {"chromeCount": cc, "baseline": self.chrome_base[0], "chromeRssMb": cr})
            if cr - self.chrome_base[1] > CHROME_RSS_GROWTH_MB:
                self.anomaly("CHROME_RSS_GROWTH", "warn", {"chromeRssMb": cr, "baseline": self.chrome_base[1]})
            self.cpu_high_polls = self.cpu_high_polls + 1 if float(host.get("cpu") or 0) > 95 else 0
            if self.cpu_high_polls == 3:
                self.anomaly("CPU_SATURATED", "warn", {"polls": 3, "cpu": host.get("cpu")})
        else:
            self.anomaly("HOST_PROBE_FAILED", "warn", host)
        wsl = probe_wsl(); snap["wsl"] = wsl
        self.peaks["wslMsMax"] = max(self.peaks["wslMsMax"], int(wsl.get("ms") or 0))
        if not wsl.get("ok"):
            self.peaks["wslTimeouts"] += 1
            self.anomaly("WSL_PROBE_FAILED", "error", wsl)
        cdp = probe_http("http://127.0.0.1:9333/json/version"); tabs = probe_http("http://127.0.0.1:9333/json/list")
        snap["cdp9333"] = {"ok": cdp.get("ok"), "ms": cdp.get("ms"), "browser": (cdp.get("json") or {}).get("Browser") if cdp.get("ok") else None,
                           "tabs": len(tabs.get("json") or []) if tabs.get("ok") and isinstance(tabs.get("json"), list) else None}
        if not cdp.get("ok") and self.run_status not in TERMINAL_RUN_STATES:
            self.peaks["cdpDowns"] += 1
            self.anomaly("CDP_9333_DOWN", "error", {"error": cdp.get("error")})
        mysql = probe_mysql(); snap["mysql"] = mysql
        if mysql.get("error"):
            self.anomaly("MYSQL_PROBE_FAILED", "warn", mysql)
        for row in mysql.get("long") or []:
            if row["seconds"] > LONG_QUERY_SECONDS:
                self.anomaly("MYSQL_LONG_QUERY", "error", row)

        snap["run"] = {k: run.get(k) for k in ("status", "publication_status", "generation_id", "manual_intervention_count", "completed_at")} if run else None
        snap["taskCounts"] = counts; snap["running"] = running
        with self.snap_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, ensure_ascii=False, default=str) + "\n")
        live = {"runId": self.run_id, "at": snap["at"], "runStatus": self.run_status, "taskCounts": counts, "running": running,
                "host": host, "wsl": wsl, "cdp9333": snap["cdp9333"], "mysqlLong": mysql.get("long"), "anomalyCounts": self.anomaly_counts,
                "peaks": self.peaks, "lastTickStart": iso(self.last_tick_start), "lastTickEnd": iso(self.last_tick_end)}
        self.live_path.write_text(json.dumps(live, ensure_ascii=False, indent=1, default=str), encoding="utf-8")

        # finished?
        if self.terminal_seen_at is not None and time.monotonic() - self.terminal_seen_at >= self.settle_seconds:
            self.log(f"run terminal ({self.run_status}) and settled {int(self.settle_seconds)} s: done")
            return True
        if time.monotonic() > self.deadline:
            self.log("max-hours reached: done")
            self.anomaly("OBSERVER_DEADLINE", "warn", {"runStatus": self.run_status})
            return True
        return False

    def watch(self) -> int:
        self.log(f"observer start run={self.run_id} poll={self.poll}s out={self.out_dir}")
        while True:
            t0 = time.monotonic()
            try:
                if self.poll_once():
                    break
            except Exception as exc:  # noqa: BLE001 - the observer must outlive its own bugs
                self.anomaly("OBSERVER_EXCEPTION", "warn", {"error": f"{type(exc).__name__}: {exc}"[:400]})
            time.sleep(max(5.0, self.poll - (time.monotonic() - t0)))
        write_report(self.run_id, self.out_dir)
        self.log(f"report {self.out_dir / 'report.md'}")
        return 0


# --------------------------------------------------------------------------- report

def write_report(run_id: str, out_dir: Path) -> Path:
    snaps = [json.loads(l) for l in (out_dir / "snapshots.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] \
        if (out_dir / "snapshots.jsonl").exists() else []
    anoms = [json.loads(l) for l in (out_dir / "anomalies.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()] \
        if (out_dir / "anomalies.jsonl").exists() else []
    journal = probe_journal(run_id)
    run = journal.get("run") or {}
    lines = [f"# V2 run observer report — `{run_id}`", ""]
    lines.append(f"- rendered {iso(utc_now())} · polls {len(snaps)} · anomalies {len(anoms)} · journal probe: {journal.get('error') or 'ok'}")
    if run:
        c, d = parse_ts(run.get("created_at")), parse_ts(run.get("completed_at"))
        wall = f"{(d - c).total_seconds() / 60:.1f} min" if c and d else "(not completed)"
        lines.append(f"- status **{run.get('status')}** · publication {run.get('publication_status')} · generation `{run.get('generation_id')}`"
                     f" · origin {run.get('origin')} · created {iso(c)} · completed {iso(d)} · wall {wall}"
                     f" · interventions {run.get('manual_intervention_count')} · renewals {run.get('manual_window_renewals')}"
                     f" · proven_autonomous {run.get('proven_autonomous')} · degraded {run.get('degraded_sources_json')}")
        lines.append(f"- schedule: cutoff {str(run.get('source_cutoff_at'))[:19]} · sla {str(run.get('sla_at'))[:19]} · final {str(run.get('final_at'))[:19]}")
    lines += ["", "## Tasks (attempt seconds vs baseline)", "", "| task | phase | status | att | attempt seconds | baseline | errors |", "|---|---|---|---|---|---|---|"]
    by_task: dict[str, list[dict]] = {}
    for a in journal.get("attempts") or []:
        by_task.setdefault(a["task_key"], []).append(a)
    for t in journal.get("tasks") or []:
        atts = by_task.get(t["task_key"], [])
        secs = []
        for a in atts:
            s, e = parse_ts(a.get("started_at")), parse_ts(a.get("finished_at"))
            secs.append(f"{(e - s).total_seconds():.0f}" if s and e else "…")
        errs = "; ".join(f"#{a['attempt_no']} {a['status']} {a.get('error_code') or ''}" for a in atts if a.get("error_code"))
        short = short_key(t["task_key"]); base = baseline_for(short)
        mark = ""
        for a, sec in zip(atts, secs):
            if sec != "…" and base is not None and float(sec) > max(2 * base, base + 120):
                mark = " ⚠"
        lines.append(f"| `{short}` | {t['phase']} | {t['status']} | {t['attempts']}/{t['max_attempts']} | {', '.join(secs)}{mark} | {base if base is not None else ''} | {errs} |")
    lines += ["", "## Events", "", "| at | type | payload |", "|---|---|---|"]
    for ev in journal.get("events") or []:
        lines.append(f"| {str(ev.get('created_at'))[11:19]} | {ev['event_type']} | `{(ev.get('payload_json') or '')[:300].replace('|', '¦')}` |")
    lines += ["", "## Anomalies (batch-fix input)", ""]
    if not anoms:
        lines.append("none recorded")
    grouped: dict[str, list[dict]] = {}
    for a in anoms:
        grouped.setdefault(a["kind"], []).append(a)
    for kind, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        first, last = items[0]["at"], items[-1]["at"]
        lines.append(f"### {kind} × {len(items)} ({items[0]['severity']}) · {first} → {last}")
        for it in items[:8]:
            detail = {k: v for k, v in it.items() if k not in ("at", "kind", "severity")}
            lines.append(f"- {it['at'][11:19]} `{json.dumps(detail, ensure_ascii=False, default=str)[:500].replace('|', '¦')}`")
        if len(items) > 8:
            lines.append(f"- … {len(items) - 8} more in anomalies.jsonl")
        lines.append("")
    # host / probes
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
                  f"- WSL probe: max {mx('ms', wsls)} ms · failures {sum(1 for w in wsls if not w.get('ok'))} / {len(wsls)}",
                  f"- journal snapshot: max {mx('ms', js)} ms · failures {sum(1 for j in js if j.get('error'))} / {len(js)}",
                  f"- CDP 9333: downs {sum(1 for c in cdps if c.get('ok') is False)} / {len(cdps)} · tabs max {mx('tabs', cdps)}",
                  f"- MySQL >60 s queries seen: {sum(len(s.get('mysql', {}).get('long') or []) for s in snaps)} rows across polls",
                  ""]
    text = "\n".join(lines) + "\n"
    (out_dir / "report.md").write_text(text, encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps({
        "runId": run_id, "status": run.get("status"), "publicationStatus": run.get("publication_status"),
        "generation": run.get("generation_id"), "anomalyKinds": {k: len(v) for k, v in grouped.items()},
        "polls": len(snaps), "renderedAt": iso(utc_now())}, indent=1), encoding="utf-8")
    return out_dir / "report.md"


# --------------------------------------------------------------------------- cli

def resolve_run_id(args: argparse.Namespace) -> str:
    if args.run_id:
        return args.run_id
    day = args.business_date or dt.datetime.now(JST).date().isoformat()
    return f"cardz-v2:{day}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    snap = sub.add_parser("snapshot"); snap.add_argument("run_id")
    for name in ("watch", "report"):
        p = sub.add_parser(name)
        p.add_argument("--run-id"); p.add_argument("--business-date")
        p.add_argument("--out-root", type=Path, default=ROOT / "data" / "runtime" / "daily-chain-v2" / "observer")
        if name == "watch":
            p.add_argument("--start-at", help="ISO-8601 UTC; sleep until then before the first poll")
            p.add_argument("--poll", type=float, default=60.0)
            p.add_argument("--max-hours", type=float, default=14.0)
            p.add_argument("--settle-minutes", type=float, default=25.0, help="keep polling this long after a terminal state (post-publish ticks)")
    args = ap.parse_args()
    if args.command == "snapshot":
        return cmd_snapshot(args.run_id)
    run_id = resolve_run_id(args)
    out_dir = args.out_root / run_id.removeprefix("cardz-v2:").replace("#", "-")
    if args.command == "report":
        print(write_report(run_id, out_dir))
        return 0
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = out_dir / "observer.lock"
    if lock.exists():
        try:
            other = int(lock.read_text(encoding="utf-8").strip() or 0)
        except ValueError:
            other = 0
        alive = False
        if other:
            rc, out, _ = run_capped(["tasklist", "/FI", f"PID eq {other}", "/FO", "CSV", "/NH"], 15)
            alive = rc == 0 and f'"{other}"' in out
        if alive:
            print(f"observer already running pid={other}; exiting")
            return 0
    lock.write_text(str(os.getpid()), encoding="utf-8")
    try:
        if args.start_at:
            target = parse_ts(args.start_at)
            while target and utc_now() < target:
                time.sleep(min(60.0, max(1.0, (target - utc_now()).total_seconds())))
        return Observer(run_id, out_dir, poll=args.poll, max_hours=args.max_hours, settle_minutes=args.settle_minutes).watch()
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    sys.exit(main())
