#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proof that every v2_run_observer detector fires (and stays quiet on a clean run).

No WSL, no docker, no network: the probes are monkeypatched with canned data and
the journal reader is exercised against a throw-away sqlite file that carries the
real chain_* column names.  The one real subprocess is the run_capped timeout
proof (a python child that sleeps).  Run with the Windows test python:
  python -X utf8 scripts/test_v2_run_observer.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v2_run_observer as obs  # noqa: E402

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"{'OK  ' if ok else 'FAIL'} {name}{(' — ' + detail) if detail and not ok else ''}")


def ts(minutes_ago: float) -> str:
    return obs.iso(obs.utc_now() - dt.timedelta(minutes=minutes_ago))


DAY = "2026-08-25"
RUN = f"cardz-v2:{DAY}"


def journal_fixture(*, clean: bool) -> dict:
    run = {"run_id": RUN, "business_date": DAY, "status": "RUNNING", "origin": "scheduled",
           "publication_status": None, "generation_id": None, "manual_intervention_count": 0,
           "created_at": ts(60), "completed_at": None,
           "source_cutoff_at": ts(-300), "sla_at": ts(-200), "final_at": ts(-100)}
    tasks = [
        {"task_key": f"{DAY}:gemrate:pop+identity:0-of-4:f270a1c7a1b2c3d4", "phase": "source", "required_class": "core", "status": "RUNNING",
         "attempts": 1, "max_attempts": 7, "heartbeat_at": ts(0.5), "interruptions": 0, "last_error_code": None, "last_error": None},
        {"task_key": f"{DAY}:system:daily-accept:all:0123456789abcdef", "phase": "barrier", "required_class": "core", "status": "COMPLETED",
         "attempts": 1, "max_attempts": 5, "heartbeat_at": ts(20), "interruptions": 0, "last_error_code": None, "last_error": None},
    ]
    attempts = [
        {"task_key": tasks[1]["task_key"], "attempt_no": 1, "status": "COMPLETED", "started_at": ts(22), "finished_at": ts(20.5),
         "error_code": None, "error_text": None, "receipt_json": json.dumps({"accepted": 1604, "token": "should-be-scrubbed"})},
    ]
    events = [{"event_key": "e1", "event_type": "RUN_STARTED", "created_at": ts(60), "payload_json": "{}"}]
    if not clean:
        run["sla_at"] = ts(5)                                              # SLA already passed while RUNNING
        tasks[0]["heartbeat_at"] = ts(10)                                  # 600 s stale
        tasks[0]["interruptions"] = 4                                      # INTERRUPTION_BUDGET
        tasks.append({"task_key": f"{DAY}:gemrate:contract-repair:pop:89abcdef01234567", "phase": "source", "required_class": "optional", "status": "SKIPPED",
                      "attempts": 2, "max_attempts": 2, "heartbeat_at": ts(30), "interruptions": 1, "last_error_code": "WORKER_INTERRUPTED", "last_error": "drain"})
        tasks.append({"task_key": f"{DAY}:pricecharting:quote+price+sales+identity:all:bbbbbbbbbbbbbbbb", "phase": "source", "required_class": "core",
                      "status": "DEGRADED", "attempts": 3, "max_attempts": 7, "heartbeat_at": ts(15), "interruptions": 0,
                      "last_error_code": "SOURCE_DEGRADED", "last_error": "cutoff"})
        attempts.append({"task_key": tasks[2]["task_key"], "attempt_no": 1, "status": "INTERRUPTED", "started_at": ts(50),
                         "finished_at": ts(30), "error_code": "WORKER_INTERRUPTED", "error_text": "tick drained", "receipt_json": None})
        attempts.append({"task_key": tasks[1]["task_key"], "attempt_no": 2, "status": "RETRY", "started_at": ts(19),
                         "finished_at": ts(18), "error_code": "SOURCE_FAILED", "error_text": "checkpoint gate failed", "receipt_json": None})
        attempts.append({"task_key": tasks[3]["task_key"], "attempt_no": 3, "status": "DEGRADED", "started_at": ts(17),
                         "finished_at": ts(15), "error_code": "SOURCE_DEGRADED", "error_text": "cutoff", "receipt_json": None})
        attempts.append({"task_key": f"{DAY}:pricecharting:psa10-sale-quote:all:aaaaaaaaaaaaaaaa", "attempt_no": 1,
                         "status": "COMPLETED", "started_at": ts(40), "finished_at": ts(20), "error_code": None, "error_text": None, "receipt_json": None})  # 1200 s vs 330
        events.append({"event_key": "e2", "event_type": "TASK_ERROR", "created_at": ts(18),
                       "payload_json": json.dumps({"taskKey": tasks[1]["task_key"], "errorCode": "SOURCE_FAILED", "logPath": "__LOGPATH__"})})
        events.append({"event_key": "e3", "event_type": "WEIRD_EVENT", "created_at": ts(17), "payload_json": "{}"})
        events.append({"event_key": "e4", "event_type": "FAILED_FINAL", "created_at": ts(16), "payload_json": "{}"})
    return {"runId": RUN, "run": run, "tasks": tasks, "attempts": attempts, "events": events, "otherRuns": [], "ms": 900}


def make_observer(tmp: Path, run_id: str = RUN) -> obs.Observer:
    return obs.Observer(run_id, tmp / "out", poll=1, max_hours=1, settle_minutes=0)


NORMAL_HOST = {"cpu": 12.0, "freeMb": 9000, "totalMb": 32000, "chromeCount": 60, "chromeRssMb": 7000, "wslProcs": 2}


def patch_probes(journal: dict | None, *, cdp_ok: bool = True, mysql_long: list[dict] | None = None, host: list[dict] | None = None,
                 wsl_ok: bool = True, wsl_running: bool = True, task_info: dict | None = None) -> None:
    if journal is not None:
        obs.probe_journal = lambda run_id: dict(journal)
    seq = list(host or [NORMAL_HOST])

    def next_host() -> dict:            # walk the sequence, then repeat the last sample
        return seq.pop(0) if len(seq) > 1 else seq[0]
    obs.probe_host = next_host
    obs.probe_wsl = lambda: {"ok": wsl_ok, "running": wsl_running, "ms": 300, "rc": 0 if wsl_ok else None, "error": "" if wsl_ok else "EXEC_TIMEOUT"}
    obs.probe_http = lambda url, timeout=5.0: ({"ok": True, "ms": 5, "json": {"Browser": "Chrome/139"}} if "version" in url
                                              else {"ok": True, "ms": 5, "json": [{"id": 1}, {"id": 2}]}) if cdp_ok \
        else {"ok": False, "ms": 5000, "error": "connection refused"}
    obs.probe_mysql = lambda: {"long": list(mysql_long or [])}
    obs.probe_task_info = lambda name: dict(task_info or {"state": "Ready", "rc": 0, "last": ts(1), "next": None})


def kinds(out_dir: Path, *, include_cleared: bool = False) -> dict[str, int]:
    counts: dict[str, int] = {}
    for line in (out_dir / "anomalies.jsonl").read_text(encoding="utf-8").splitlines() if (out_dir / "anomalies.jsonl").exists() else []:
        rec = json.loads(line)
        if rec["kind"].endswith("_CLEARED") and not include_cleared:
            continue
        counts[rec["kind"]] = counts.get(rec["kind"], 0) + 1
    return counts


def severity_of(out_dir: Path, kind: str) -> str | None:
    for line in (out_dir / "anomalies.jsonl").read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec["kind"] == kind:
            return rec["severity"]
    return None


def write_health(tmp: Path, *, age_min: float, run_id: str = RUN) -> None:
    d = tmp / "data" / "runtime" / "daily-chain-v2"
    d.mkdir(parents=True, exist_ok=True)
    (d / "health.json").write_text(json.dumps({"run_id": run_id, "run_state": "RUNNING", "tick_phase": "tick", "written_at_utc": ts(age_min),
                                               "tick_exit_code": 0, "parked": [], "last_alert": None}), encoding="utf-8")


def main() -> int:
    originals = {n: getattr(obs, n) for n in ("probe_journal", "probe_host", "probe_wsl", "probe_http", "probe_mysql", "probe_task_info",
                                              "run_capped", "ROOT", "RECURRING_REPEAT_SECONDS", "MAIN_JOURNAL")}
    try:
        # 1. pure helpers
        check("short_key strips day and hash", obs.short_key("2026-08-25:gemrate:pop+identity:0-of-4:f270a1c7a1b2c3d4") == "gemrate:pop+identity:0-of-4")
        check("short_key keeps non-hash tail", obs.short_key("2026-08-25:system:box:all") == "system:box:all")
        check("baseline gemrate shard 900", obs.baseline_for("gemrate:pop+identity:2-of-4") == 900)
        check("baseline contract-repair beats lane prefix", obs.baseline_for("pricecharting:contract-repair:quote") == 240)
        check("baseline candidate-stock before generic lane", obs.baseline_for("pricecharting:candidate-stock:all") == 330 and obs.baseline_for("gemrate:candidate-stock:all") == 600)
        check("baseline unknown -> None", obs.baseline_for("mystery:thing") is None)
        check("publish baselines provisional", obs.baseline_provisional("system:release:release") and not obs.baseline_provisional("system:daily-accept:all"))
        check("wsl_path maps drive", obs.wsl_path(Path("C:/x/y.py")).startswith("/mnt/c/"))
        check("windows_path maps /mnt/c", str(obs.windows_path("/mnt/c/Users/x/a.log")).replace("\\", "/") == "C:/Users/x/a.log")
        check("windows_path linux-only -> None", obs.windows_path("/home/x/a.log") is None)
        check("parse_ts launcher 7-digit fraction + offset", obs.iso(obs.parse_ts("2026-08-23T03:30:01.2490936+09:00")) == "2026-08-22T18:30:01Z")
        check("launcher_line_time ISO line", obs.iso(obs.launcher_line_time("2026-08-23T03:30:01.2490936+09:00 CARDZ_V2_START", "20260823")) == "2026-08-22T18:30:01Z")
        check("launcher_line_time clock line uses file day as JST", obs.iso(obs.launcher_line_time("[03:30:10] CARDZ_V2_END exit=0", "20260825")) == "2026-08-24T18:30:10Z")
        check("expected_tick_start 03:30 JST", obs.iso(obs.expected_tick_start("2026-08-25")) == "2026-08-24T18:30:00Z")
        check("run_business_day accepts base, supersede, and label",
              obs.run_business_day(RUN) == DAY and obs.run_business_day(RUN + "/2") == DAY and obs.run_business_day(RUN + "#T1") == DAY)
        check("scheduled family sequence accepts only base and numeric /2+",
              obs.scheduled_family_sequence(RUN, RUN) == 1 and obs.scheduled_family_sequence(RUN, RUN + "/2") == 2
              and obs.scheduled_family_sequence(RUN, RUN + "/10") == 10 and obs.scheduled_family_sequence(RUN, RUN + "/1") is None
              and obs.scheduled_family_sequence(RUN, RUN + "#T1") is None)
        expected = obs.expected_tick_start("2026-08-25")
        assert expected is not None
        after_grace = expected + dt.timedelta(seconds=obs.RUN_START_GRACE_SECONDS + 1)
        check("task/tick liveness healthy", obs.task_tick_liveness(
            {"state": "Ready", "rc": 0, "last": obs.iso(expected)},
            expected, expected, after_grace,
        ) == {})
        check("task/tick liveness catches missing task run", "TASK_NOT_RUN" in obs.task_tick_liveness(
            {"state": "Ready", "rc": 0, "last": obs.iso(expected - dt.timedelta(days=1))},
            expected, None, after_grace,
        ))
        check("task/tick liveness catches scheduler/repo split", "TASK_REPO_TICK_MISMATCH" in obs.task_tick_liveness(
            {"state": "Ready", "rc": 0, "last": obs.iso(expected)},
            expected, None, after_grace,
        ))
        check("promo_time 17:45 JST", obs.iso(obs.promo_time("2026-08-25")) == "2026-08-25T08:45:00Z")
        check("event_severity allowlist/error/unknown", obs.event_severity("RUN_STARTED") is None and obs.event_severity("FAILED_FINAL") == "error"
              and obs.event_severity("SOMETHING_NEW") == "warn" and obs.event_severity("TASK_ERROR") == "warn")
        check("scrub token/password/pb_live/query", obs.scrub('token=abc123 "password": "p@ss" pb_live_XyZ_9 https://h/x?sig=1') ==
              'token=<redacted> "password": "<redacted>" pb_live_<redacted> https://h/x?<query-redacted>')
        chain = Path(__file__).resolve().parents[1] / "pipelines" / "daily_chain_v2.py"
        if chain.exists():
            src = chain.read_text(encoding="utf-8", errors="replace")
            named = [e for e in obs.INFO_EVENTS | obs.ERROR_EVENTS if "." not in e and e != "RUN_ABORTED"]
            missing = [e for e in named if f'"{e}"' not in src]
            check("classified event names exist in daily_chain_v2.py", not missing, json.dumps(missing))

        # 2. run_capped is a real cap on Windows (sleeping child, no pipes) and returns output for a normal child
        t0 = time.monotonic()
        rc, out, err = obs.run_capped([sys.executable, "-c", "import time; time.sleep(30)"], 1.5)
        check("run_capped timeout returns None fast", rc is None and err == "TIMEOUT" and time.monotonic() - t0 < 12, f"rc={rc} took={time.monotonic() - t0:.1f}s")
        rc, out, err = obs.run_capped([sys.executable, "-c", "print('hi')"], 20)
        check("run_capped captures stdout", rc == 0 and out.strip() == "hi", f"rc={rc} out={out!r} err={err!r}")

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            obs.ROOT = tmp
            # 3. clean run -> zero anomalies, not finished
            patch_probes(journal_fixture(clean=True))
            o = make_observer(tmp / "clean")
            with redirect_stdout(io.StringIO()):
                done = o.poll_once()
            check("clean run: poll not finished", done is False)
            check("clean run: zero anomalies", kinds(o.out_dir) == {}, json.dumps(kinds(o.out_dir)))
            check("clean run: live.json written", (o.out_dir / "live.json").exists())
            check("clean run: journal-latest.json persisted", (o.out_dir / "journal-latest.json").exists())
            check("clean run: run status logged RUNNING", o.run_status == "RUNNING")
            check("clean run: receipt scrubbed", "should-be-scrubbed" not in (o.out_dir / "receipts.jsonl").read_text(encoding="utf-8")
                  and "<redacted>" in (o.out_dir / "receipts.jsonl").read_text(encoding="utf-8"))
            # An observer anomaly must have an external consequence in the
            # installed --notify mode, and a dropped alert must leave evidence.
            notify_calls: list[list[str]] = []
            obs.run_capped = lambda command, timeout, **kwargs: (notify_calls.append(command), (1, "", "drop"))[1]
            notifying = obs.Observer(RUN, tmp / "notify", poll=1, max_hours=1, settle_minutes=0, notify_alerts=True)
            with redirect_stdout(io.StringIO()):
                notifying.anomaly("FIXTURE_ANOMALY", "error", {"task": "system:release"})
            alert_failures = (notifying.out_dir / "alert-failures.jsonl").read_text(encoding="utf-8")
            check("observer anomaly invokes required-delivery alert", len(notify_calls) == 1 and "--require-delivery" in notify_calls[0])
            check("observer alert drop leaves failure artifact", "FIXTURE_ANOMALY" in alert_failures and '"exitCode": 1' in alert_failures)
            obs.run_capped = originals["run_capped"]
            # CDP down before any tick / with no run must not alarm (watchdog territory)
            patch_probes({"runId": RUN, "run": None, "otherRuns": [], "ms": 10}, cdp_ok=False)
            o0 = make_observer(tmp / "norun")
            o0.expected_start = obs.utc_now() + dt.timedelta(hours=1)
            with redirect_stdout(io.StringIO()):
                o0.poll_once(); o0.poll_once()
            check("no run yet: CDP down not flagged", "CDP_9333_DOWN" not in kinds(o0.out_dir) and o0.run_status == "NOT_STARTED", json.dumps(kinds(o0.out_dir)))
            # RUN_NOT_STARTED once the expected tick is 20+ min late, once per 30 min
            o0.expected_start = obs.utc_now() - dt.timedelta(hours=1)
            with redirect_stdout(io.StringIO()):
                o0.poll_once(); o0.poll_once()
            check("RUN_NOT_STARTED fires once when late", kinds(o0.out_dir).get("RUN_NOT_STARTED") == 1, json.dumps(kinds(o0.out_dir)))

            # A scheduled observer requests the stable base ID while the current
            # live row may be an automatically-created supersede /N.
            supersede = journal_fixture(clean=True)
            supersede["runId"] = RUN + "/2"
            supersede["requestedRunId"] = RUN
            supersede["resolvedRunId"] = RUN + "/2"
            supersede["run"]["run_id"] = RUN + "/2"
            patch_probes(supersede)
            osup = make_observer(tmp / "supersede")
            osup.expected_start = obs.utc_now() - dt.timedelta(hours=1)
            with redirect_stdout(io.StringIO()):
                osup.poll_once()
            live_sup = json.loads((osup.out_dir / "live.json").read_text(encoding="utf-8"))
            check("observer resolves scheduled base to current supersede",
                  osup.resolved_run_id == RUN + "/2" and osup.run_status == "RUNNING"
                  and live_sup.get("requestedRunId") == RUN and live_sup.get("resolvedRunId") == RUN + "/2")
            check("resolved supersede does not raise RUN_NOT_STARTED", "RUN_NOT_STARTED" not in kinds(osup.out_dir), json.dumps(kinds(osup.out_dir)))

            # 4. dirty run -> every detector fires exactly as designed
            (tmp / "logs" / "daily-chain-v2").mkdir(parents=True)
            launcher = tmp / "logs" / "daily-chain-v2" / f"launcher-{dt.datetime.now().strftime('%Y%m%d')}.log"
            launcher.write_bytes(("\ufeff[03:30:00] CARDZ_V2_PREFLIGHT cdp=9333 exit=2\n"
                                  "[03:30:01] CARDZ_V2_START event=107 parent=wscript.exe\n"
                                  "[03:30:09] CARDZ_V2_WSL_PREFLIGHT exit=3 WSL_DEAD probeMs=30000 Wsl/Service/0x8007274c\n"
                                  "[03:30:10] CARDZ_V2_END exit=3 provenance=x wsl=dead\n"
                                  "[03:31:00] CARDZ_V2_LAUNCHER_EXCEPTION System.Exception: boom\n").encode("utf-8"))
            write_health(tmp, age_min=30)
            (tmp / "data" / "runtime" / "daily-chain-v2" / "tick-skipped.json").write_text(
                json.dumps({"last_skipped_at_utc": ts(1), "reason": "TICK_SKIPPED_LOCKED", "business_date": DAY}), encoding="utf-8")
            tasklog = tmp / "task.log"
            tasklog.write_text("line1\nTraceback: boom\n", encoding="utf-8")
            dirty = journal_fixture(clean=False)
            dirty["events"][1]["payload_json"] = dirty["events"][1]["payload_json"].replace("__LOGPATH__", str(tasklog).replace("\\", "/"))
            patch_probes(dirty, cdp_ok=False,
                         mysql_long=[{"id": 9, "user": "cardz", "seconds": 700, "state": "executing", "sql": "SELECT ..."}],
                         host=[{"cpu": 99.0, "freeMb": 1000, "totalMb": 32000, "chromeCount": 60, "chromeRssMb": 7000, "wslProcs": 2},
                               {"cpu": 99.0, "freeMb": 1000, "totalMb": 32000, "chromeCount": 90, "chromeRssMb": 12000, "wslProcs": 2}],
                         wsl_ok=False)
            o = make_observer(tmp / "dirty")
            with redirect_stdout(io.StringIO()):
                o.poll_once(); o.poll_once(); o.poll_once()   # CPU_SATURATED needs 3 consecutive polls; recurring kinds must not repeat
            k = kinds(o.out_dir)
            expected_once = ["SLA_EXCEEDED", "TASK_STATE_SKIPPED", "TASK_STATE_DEGRADED", "ATTEMPT_INTERRUPTED", "ATTEMPT_RETRY", "ATTEMPT_DEGRADED",
                             "SLOW_TASK", "EVENT_TASK_ERROR", "EVENT_WEIRD_EVENT", "EVENT_FAILED_FINAL", "TICK_EXIT_NONZERO", "WSL_PREFLIGHT_NOT_OK",
                             "WSL_SERVICE_ERROR", "CDP_PREFLIGHT_FAILED", "LAUNCHER_EXCEPTION", "CPU_SATURATED", "INTERRUPTION_BUDGET",
                             "STALE_HEARTBEAT", "CDP_9333_DOWN", "MYSQL_LONG_QUERY", "WSL_PROBE_FAILED", "LOW_FREE_RAM",
                             "CHROME_COUNT_GROWTH", "CHROME_RSS_GROWTH", "HEALTH_STALE", "TICK_SKIPPED_LOCKED"]
            for kind in expected_once:
                check(f"fires once: {kind}", k.get(kind) == 1, f"count={k.get(kind)}")
            allowed = set(expected_once) | {"TICK_GAP", "LAUNCHER_SILENT"}   # launcher fixture timestamps are today 03:30 JST: usually long idle
            check("no unexpected anomaly kinds", set(k) <= allowed, json.dumps(sorted(set(k) - allowed)))
            check("DEGRADED core task is error", severity_of(o.out_dir, "TASK_STATE_DEGRADED") == "error")
            check("ATTEMPT_DEGRADED is warn", severity_of(o.out_dir, "ATTEMPT_DEGRADED") == "warn")
            check("unknown event is warn, FAILED_FINAL error", severity_of(o.out_dir, "EVENT_WEIRD_EVENT") == "warn" and severity_of(o.out_dir, "EVENT_FAILED_FINAL") == "error")
            log = (o.out_dir / "observer.log").read_text(encoding="utf-8")
            check("launcher line echoed once, BOM stripped", log.count("tick| [03:30:10] CARDZ_V2_END exit=3") == 1 and "\ufeff" not in log)
            check("attempt duration logged", "system:daily-accept:all" in log and "COMPLETED" in log)
            check("tick timeline parsed", len(o.ticks) == 1 and o.ticks[0]["exit"] == 3 and o.ticks[0]["seconds"] == 9, json.dumps(o.ticks))
            tails = list((o.out_dir / "task-logs").glob("*.txt")) if (o.out_dir / "task-logs").exists() else []
            check("TASK_ERROR logPath tailed into task-logs/", len(tails) == 1 and "Traceback: boom" in tails[0].read_text(encoding="utf-8"), str(tails))
            check("receipts.jsonl has the finished attempts", sum(1 for _ in (o.out_dir / "receipts.jsonl").open(encoding="utf-8")) == 5)
            # 4b. conditions clear -> one *_CLEARED each, nothing re-fires
            write_health(tmp, age_min=1)
            patch_probes(journal_fixture(clean=True))
            with redirect_stdout(io.StringIO()):
                o.poll_once()
            kc = kinds(o.out_dir, include_cleared=True)
            for kind in ["STALE_HEARTBEAT", "CDP_9333_DOWN", "MYSQL_LONG_QUERY", "WSL_PROBE_FAILED", "LOW_FREE_RAM", "CHROME_COUNT_GROWTH",
                         "CHROME_RSS_GROWTH", "HEALTH_STALE", "CPU_SATURATED"]:
                check(f"cleared once: {kind}_CLEARED", kc.get(kind + "_CLEARED") == 1 and kc.get(kind) == 1, f"{kind}={kc.get(kind)} cleared={kc.get(kind + '_CLEARED')}")
            check("TICK_SKIPPED_LOCKED not repeated for same timestamp", kc.get("TICK_SKIPPED_LOCKED") == 1)
            # 4c. persisting condition repeats after the cooldown
            obs.RECURRING_REPEAT_SECONDS = 0
            patch_probes(journal_fixture(clean=True), host=[{**NORMAL_HOST, "freeMb": 500}])
            o4 = make_observer(tmp / "repeat")
            with redirect_stdout(io.StringIO()):
                o4.poll_once(); o4.poll_once(); o4.poll_once()
            check("recurring repeats every poll when cooldown is 0", kinds(o4.out_dir).get("LOW_FREE_RAM") == 3, json.dumps(kinds(o4.out_dir)))
            obs.RECURRING_REPEAT_SECONDS = originals["RECURRING_REPEAT_SECONDS"]
            # 4d. launcher silent / tick gap from parsed tick times; truncation resets the offset
            patch_probes(journal_fixture(clean=True))
            o5 = make_observer(tmp / "silent")
            with redirect_stdout(io.StringIO()):
                o5.poll_once()                                     # reads the launcher fixture (today 03:30 JST)
            o5.last_tick_start = obs.utc_now() - dt.timedelta(minutes=40)
            o5.last_tick_end = obs.utc_now() - dt.timedelta(minutes=30)
            with redirect_stdout(io.StringIO()):
                o5.poll_once(); o5.poll_once()
            k5 = kinds(o5.out_dir, include_cleared=True)
            check("LAUNCHER_SILENT + TICK_GAP fire once on 30 min idle, never cleared while idle",
                  k5.get("LAUNCHER_SILENT") == 1 and k5.get("TICK_GAP") == 1 and "LAUNCHER_SILENT_CLEARED" not in k5 and "TICK_GAP_CLEARED" not in k5, json.dumps(k5))
            launcher.write_bytes("[04:00:00] CARDZ_V2_START event=107\n".encode("utf-8"))   # shorter file = rotated/truncated
            with redirect_stdout(io.StringIO()):
                o5.poll_once()
            check("LAUNCHER_TRUNCATED resets offset and re-reads", kinds(o5.out_dir).get("LAUNCHER_TRUNCATED") == 1
                  and "tick| [04:00:00] CARDZ_V2_START" in (o5.out_dir / "observer.log").read_text(encoding="utf-8"))
            # 4e. WSL idle between ticks: journal probe skipped (cached data reused) until the 10-min re-probe
            calls = []
            base = journal_fixture(clean=True)
            obs.probe_journal = lambda run_id: (calls.append(1), dict(base))[1]
            o6 = make_observer(tmp / "idle")
            patch_probes(None, wsl_running=False)
            with redirect_stdout(io.StringIO()):
                o6.poll_once(); o6.poll_once(); o6.poll_once()
            check("WSL idle: journal probed on first poll only", len(calls) == 1 and o6.peaks["journalSkips"] == 2 and o6.run_status == "RUNNING", f"calls={len(calls)}")
            o6.last_journal_ok_at -= obs.JOURNAL_IDLE_REPROBE_SECONDS + 1
            with redirect_stdout(io.StringIO()):
                o6.poll_once()
            check("WSL idle: re-probed after the idle interval", len(calls) == 2)

            # 5. journal probe failure is itself an anomaly, does not crash the poll, and the report falls back to the cached journal
            patch_probes(journal_fixture(clean=True))
            o2 = make_observer(tmp / "probefail")
            with redirect_stdout(io.StringIO()):
                o2.poll_once()
            obs.probe_journal = lambda run_id: {"error": "WSL_SNAPSHOT_TIMEOUT", "ms": 75000}
            with redirect_stdout(io.StringIO()):
                done = o2.poll_once(); o2.poll_once()
            check("journal timeout -> JOURNAL_PROBE_FAILED once", kinds(o2.out_dir).get("JOURNAL_PROBE_FAILED") == 1, json.dumps(kinds(o2.out_dir)))
            check("journal timeout counted", o2.peaks["journalTimeouts"] == 2 and done is False)
            with redirect_stdout(io.StringIO()):
                text = obs.write_report(RUN, o2.out_dir).read_text(encoding="utf-8")
            check("report falls back to cached journal", "journal source: cached" in text and "system:daily-accept:all" in text)

            # 6. terminal run -> finished after settle, report rendered, promo collected
            term = journal_fixture(clean=True)
            term["run"]["status"] = "PUBLISHED"; term["run"]["publication_status"] = "PUBLISHED"
            term["run"]["generation_id"] = "db3308_test"; term["run"]["completed_at"] = ts(1)
            patch_probes(term, cdp_ok=False, task_info={"state": "Ready", "rc": 0, "last": ts(180), "next": None})   # CDP down after publish must NOT alarm
            o3 = make_observer(tmp / "term")
            with redirect_stdout(io.StringIO()):
                done = o3.poll_once()
            check("terminal run: finished with settle 0", done is True)
            check("terminal run: CDP down not flagged after terminal", "CDP_9333_DOWN" not in kinds(o3.out_dir))
            promo_at = obs.utc_now() - dt.timedelta(minutes=30)
            with redirect_stdout(io.StringIO()):
                o3.collect_promo(promo_at)
            k3 = kinds(o3.out_dir)
            check("promo: task not run since promo time -> PROMO_TASK_NOT_RUN", k3.get("PROMO_TASK_NOT_RUN") == 1, json.dumps(k3))
            check("promo: missing brief -> PROMO_BRIEF_MISSING", k3.get("PROMO_BRIEF_MISSING") == 1)
            pdir = tmp / "data" / "runtime" / "promo" / DAY
            pdir.mkdir(parents=True)
            (pdir / "brief.json").write_text(json.dumps({"generation": "db3308_other", "lagHours": 6.0, "post": False}), encoding="utf-8")
            patch_probes(term, task_info={"state": "Ready", "rc": 1, "last": ts(5), "next": None})
            with redirect_stdout(io.StringIO()):
                o3.collect_promo(promo_at)
            k3 = kinds(o3.out_dir)
            check("promo: rc 1 -> PROMO_TASK_RC_NONZERO", k3.get("PROMO_TASK_RC_NONZERO") == 1)
            check("promo: task ran without inner receipt -> PROMO_SCHEDULER_RECEIPT_MISSING", k3.get("PROMO_SCHEDULER_RECEIPT_MISSING") == 1)
            check("promo: generation mismatch flagged", k3.get("PROMO_GENERATION_MISMATCH") == 1 and (o3.out_dir / "promo.json").exists())
            # promo dirs are keyed by the JST day of the bake: the previous day's dir carries this run's generation
            prev_pdir = tmp / "data" / "runtime" / "promo" / "2026-08-24"
            prev_pdir.mkdir(parents=True)
            (prev_pdir / "brief.json").write_text(json.dumps({"generation": "db3308_test", "lagHours": 5.0, "post": True}), encoding="utf-8")
            scheduler_dir = tmp / "data" / "runtime" / "promo" / "scheduler"
            scheduler_dir.mkdir(parents=True)
            (scheduler_dir / f"{DAY}.json").write_text(json.dumps({
                "contract": "cardz-promo-pack-scheduled-v1", "businessDate": DAY,
                "generation": "db3308_test", "outcome": "ok", "exitCode": 0,
                "recordedAt": ts(1),
            }), encoding="utf-8")
            o4 = make_observer(tmp / "term-prevday")
            o4.run_generation = "db3308_test"
            with redirect_stdout(io.StringIO()):
                o4.collect_promo(promo_at)
            k4 = kinds(o4.out_dir)
            promo4 = json.loads((o4.out_dir / "promo.json").read_text(encoding="utf-8"))
            check("promo: previous JST-day dir carrying the run generation -> no mismatch",
                  "PROMO_GENERATION_MISMATCH" not in k4 and "PROMO_BRIEF_MISSING" not in k4
                  and "PROMO_SCHEDULER_RECEIPT_MISSING" not in k4, json.dumps(k4))
            check("promo: resolved dir is the one holding the run generation",
                  str(promo4.get("dir") or "").replace("\\", "/").endswith("promo/2026-08-24")
                  and (promo4.get("brief") or {}).get("generation") == "db3308_test", json.dumps(promo4.get("dir")))
            (scheduler_dir / f"{DAY}.json").write_text(json.dumps({
                "contract": "cardz-promo-pack-scheduled-v1", "businessDate": DAY,
                "generation": "db3308_test", "outcome": "error", "exitCode": 2,
                "artifact": "failure.json", "recordedAt": ts(1),
            }), encoding="utf-8")
            o_receipt = make_observer(tmp / "term-receipt-rc")
            o_receipt.run_generation = "db3308_test"
            patch_probes(term, task_info={"state": "Ready", "rc": 0, "last": ts(5), "next": None})
            with redirect_stdout(io.StringIO()):
                o_receipt.collect_promo(promo_at)
            check("promo: inner rc 2 beats masked scheduler rc 0",
                  kinds(o_receipt.out_dir).get("PROMO_TASK_RC_NONZERO") == 1,
                  json.dumps(kinds(o_receipt.out_dir)))
            o5 = make_observer(tmp / "term-nogen")
            o5.run_generation = "db3308_nowhere"
            with redirect_stdout(io.StringIO()):
                o5.collect_promo(promo_at)
            check("promo: no promo dir carries the run generation -> still flagged",
                  kinds(o5.out_dir).get("PROMO_GENERATION_MISMATCH") == 1, json.dumps(kinds(o5.out_dir)))
            with redirect_stdout(io.StringIO()):
                report = obs.write_report(RUN, o3.out_dir, journal=term)
            text = report.read_text(encoding="utf-8")
            check("report has status line", "**PUBLISHED**" in text and "db3308_test" in text)
            check("report has task table row", "system:daily-accept:all" in text)
            check("report has host + promo sections", "## Host / probes" in text and "## Promo" in text)
            check("report anomalies sorted error first", text.index("### PROMO_TASK_RC_NONZERO") < text.index("### PROMO_TASK_NOT_RUN"))
            check("summary.json written with counts", json.loads((o3.out_dir / "summary.json").read_text(encoding="utf-8")).get("errors") == 1)

            # 7. lock: live python pid refused (rc path), stale pid taken over
            ld = tmp / "lock"
            ld.mkdir()
            check("lock acquired when absent", obs.acquire_lock(ld) is True)
            (ld / "observer.lock").write_text(json.dumps({"pid": os.getpid()}), encoding="utf-8")
            check("lock refused for a live python pid", obs.acquire_lock(ld) is False and (ld / "lock-conflict.json").exists())
            (ld / "observer.lock").write_text("1", encoding="utf-8")
            check("stale lock (pid 1) taken over", obs.acquire_lock(ld) is True)

            # 8. snapshot subcommand against a sqlite journal with the real column names
            jp = tmp / "daily-chain-v2-T1.sqlite3"
            c = sqlite3.connect(jp)
            c.executescript("""
            CREATE TABLE chain_run(run_id TEXT PRIMARY KEY, business_date TEXT, status TEXT, origin TEXT, scheduled_event_107_count INT,
              manual_intervention_count INT, source_cutoff_at TEXT, sla_at TEXT, final_at TEXT, publication_status TEXT, generation_id TEXT,
              generated_at TEXT, content_sha256 TEXT, active_count INT, degraded_sources_json TEXT, proven_autonomous INT, created_at TEXT,
              updated_at TEXT, completed_at TEXT, manual_window_renewals INT);
            CREATE TABLE chain_task(task_key TEXT PRIMARY KEY, run_id TEXT, phase TEXT, source_code TEXT, capability TEXT, required_class TEXT,
              concurrency_group TEXT, max_concurrency INT, status TEXT, input_revision TEXT, payload_json TEXT, checkpoint_json TEXT, result_json TEXT,
              attempts INT, max_attempts INT, next_retry_at TEXT, lease_token TEXT, lease_expires_at TEXT, heartbeat_at TEXT, last_error_code TEXT,
              last_error TEXT, created_at TEXT, updated_at TEXT, interruptions INT);
            CREATE TABLE chain_attempt(id INTEGER PRIMARY KEY, task_key TEXT, attempt_no INT, claim_token TEXT, status TEXT, started_at TEXT,
              heartbeat_at TEXT, finished_at TEXT, worker_pid INT, process_started_at TEXT, command_sha256 TEXT, receipt_json TEXT, error_code TEXT, error_text TEXT);
            CREATE TABLE chain_event(event_key TEXT PRIMARY KEY, run_id TEXT, event_type TEXT, payload_json TEXT, delivered INT, created_at TEXT, delivered_at TEXT);
            INSERT INTO chain_run(run_id,business_date,status,created_at) VALUES('cardz-v2:2026-08-25#T1','2026-08-25','RUNNING','2026-08-24T18:30:00Z');
            INSERT INTO chain_task(task_key,run_id,phase,required_class,status,attempts,max_attempts,created_at) VALUES('2026-08-25:system:box:all:abcdef0123456789','cardz-v2:2026-08-25#T1','publish','core','COMPLETED',1,3,'2026-08-24T18:31:00Z');
            INSERT INTO chain_attempt(task_key,attempt_no,status,started_at,finished_at) VALUES('2026-08-25:system:box:all:abcdef0123456789',1,'COMPLETED','2026-08-24T18:31:00Z','2026-08-24T18:31:05Z');
            INSERT INTO chain_event(event_key,run_id,event_type,payload_json,delivered,created_at) VALUES('k1','cardz-v2:2026-08-25#T1','RUN_STARTED','{}',1,'2026-08-24T18:30:01Z');
            """)
            c.commit(); c.close()
            obs.MAIN_JOURNAL = str(tmp / "daily-chain-v2{suffix}.sqlite3")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = obs.cmd_snapshot("cardz-v2:2026-08-25#T1")
            data = json.loads(buf.getvalue())
            check("snapshot rc 0", rc == 0)
            check("snapshot run/tasks/attempts/events incl required_class", data["run"]["status"] == "RUNNING" and len(data["tasks"]) == 1
                  and data["tasks"][0]["required_class"] == "core" and len(data["attempts"]) == 1 and len(data["events"]) == 1, json.dumps({k: data.get(k) for k in ("error",)}))
            # The main journal only retains the live supersede row after the
            # previous scheduled run is archived.  A base request follows it.
            main_jp = tmp / "daily-chain-v2.sqlite3"
            c = sqlite3.connect(main_jp)
            c.executescript("""
            CREATE TABLE chain_run(run_id TEXT PRIMARY KEY, business_date TEXT, status TEXT, origin TEXT, scheduled_event_107_count INT,
              manual_intervention_count INT, source_cutoff_at TEXT, sla_at TEXT, final_at TEXT, publication_status TEXT, generation_id TEXT,
              generated_at TEXT, content_sha256 TEXT, active_count INT, degraded_sources_json TEXT, proven_autonomous INT, created_at TEXT,
              updated_at TEXT, completed_at TEXT, manual_window_renewals INT);
            CREATE TABLE chain_task(task_key TEXT PRIMARY KEY, run_id TEXT, phase TEXT, source_code TEXT, capability TEXT, required_class TEXT,
              concurrency_group TEXT, max_concurrency INT, status TEXT, input_revision TEXT, payload_json TEXT, checkpoint_json TEXT, result_json TEXT,
              attempts INT, max_attempts INT, next_retry_at TEXT, lease_token TEXT, lease_expires_at TEXT, heartbeat_at TEXT, last_error_code TEXT,
              last_error TEXT, created_at TEXT, updated_at TEXT, interruptions INT);
            CREATE TABLE chain_attempt(id INTEGER PRIMARY KEY, task_key TEXT, attempt_no INT, claim_token TEXT, status TEXT, started_at TEXT,
              heartbeat_at TEXT, finished_at TEXT, worker_pid INT, process_started_at TEXT, command_sha256 TEXT, receipt_json TEXT, error_code TEXT, error_text TEXT);
            CREATE TABLE chain_event(event_key TEXT PRIMARY KEY, run_id TEXT, event_type TEXT, payload_json TEXT, delivered INT, created_at TEXT, delivered_at TEXT);
            INSERT INTO chain_run(run_id,business_date,status,created_at) VALUES('cardz-v2:2026-08-25/2','2026-08-25','RUNNING','2026-08-24T19:30:00Z');
            INSERT INTO chain_task(task_key,run_id,phase,required_class,status,attempts,max_attempts,created_at) VALUES('2026-08-25:system:box:all:fedcba9876543210','cardz-v2:2026-08-25/2','publish','core','COMPLETED',1,3,'2026-08-24T19:31:00Z');
            INSERT INTO chain_attempt(task_key,attempt_no,status,started_at,finished_at) VALUES('2026-08-25:system:box:all:fedcba9876543210',1,'COMPLETED','2026-08-24T19:31:00Z','2026-08-24T19:31:05Z');
            INSERT INTO chain_event(event_key,run_id,event_type,payload_json,delivered,created_at) VALUES('k2','cardz-v2:2026-08-25/2','RUN_STARTED','{}',1,'2026-08-24T19:30:01Z');
            """)
            c.commit(); c.close()
            buf = io.StringIO()
            with redirect_stdout(buf):
                obs.cmd_snapshot(RUN)
            data = json.loads(buf.getvalue())
            check("snapshot base request resolves current /2 row",
                  data.get("requestedRunId") == RUN and data.get("resolvedRunId") == RUN + "/2" and data.get("runId") == RUN + "/2"
                  and data["run"]["run_id"] == RUN + "/2" and len(data["tasks"]) == 1 and len(data["attempts"]) == 1 and len(data["events"]) == 1,
                  json.dumps({k: data.get(k) for k in ("requestedRunId", "resolvedRunId", "runId", "error")}))
            buf = io.StringIO()
            with redirect_stdout(buf):
                obs.cmd_snapshot("cardz-v2:2026-08-25#NOPE")
            check("snapshot missing journal -> NO_JOURNAL", json.loads(buf.getvalue()).get("error") == "NO_JOURNAL")
    finally:
        for n, v in originals.items():
            setattr(obs, n, v)
    failed = [c for c in CHECKS if not c[1]]
    print(f"CHECKS {len(CHECKS)} FAILED {len(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
