#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Proof that every v2_run_observer detector fires (and stays quiet on a clean run).

No WSL, no docker, no network: the probes are monkeypatched with canned data and
the journal reader is exercised against a throw-away sqlite file that carries the
real chain_* column names.  Run with the Windows test python:
  python -X utf8 scripts/test_v2_run_observer.py
"""
from __future__ import annotations

import datetime as dt
import io
import json
import sqlite3
import sys
import tempfile
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


def journal_fixture(*, clean: bool) -> dict:
    day = "2026-08-25"
    run = {"run_id": f"cardz-v2:{day}", "business_date": day, "status": "RUNNING", "origin": "scheduled",
           "publication_status": None, "generation_id": None, "manual_intervention_count": 0,
           "created_at": ts(60), "completed_at": None,
           "source_cutoff_at": ts(-300), "sla_at": ts(-200), "final_at": ts(-100)}
    tasks = [
        {"task_key": f"{day}:gemrate:pop+identity:0-of-4:f270a1c7a1b2c3d4", "phase": "source", "status": "RUNNING",
         "attempts": 1, "max_attempts": 7, "heartbeat_at": ts(0.5), "last_error_code": None, "last_error": None},
        {"task_key": f"{day}:system:daily-accept:all:0123456789abcdef", "phase": "barrier", "status": "COMPLETED",
         "attempts": 1, "max_attempts": 5, "heartbeat_at": ts(20), "last_error_code": None, "last_error": None},
    ]
    attempts = [
        {"task_key": tasks[1]["task_key"], "attempt_no": 1, "status": "COMPLETED", "started_at": ts(22), "finished_at": ts(20.5),
         "error_code": None, "error_text": None},
    ]
    events = [{"event_key": "e1", "event_type": "RUN_STARTED", "created_at": ts(60), "payload_json": "{}"}]
    if not clean:
        run["sla_at"] = ts(5)                                              # SLA already passed while RUNNING
        tasks[0]["heartbeat_at"] = ts(10)                                  # 600 s stale
        tasks.append({"task_key": f"{day}:gemrate:contract-repair:pop:89abcdef01234567", "phase": "source", "status": "SKIPPED",
                      "attempts": 2, "max_attempts": 2, "heartbeat_at": ts(30), "last_error_code": "WORKER_INTERRUPTED",
                      "last_error": "drain"})
        attempts.append({"task_key": tasks[2]["task_key"], "attempt_no": 1, "status": "INTERRUPTED", "started_at": ts(50),
                         "finished_at": ts(30), "error_code": "WORKER_INTERRUPTED", "error_text": "tick drained"})
        attempts.append({"task_key": tasks[1]["task_key"], "attempt_no": 2, "status": "RETRY", "started_at": ts(19),
                         "finished_at": ts(18), "error_code": "SOURCE_FAILED", "error_text": "checkpoint gate failed"})
        attempts.append({"task_key": f"{day}:pricecharting:psa10-sale-quote:all:aaaaaaaaaaaaaaaa", "attempt_no": 1,
                         "status": "COMPLETED", "started_at": ts(40), "finished_at": ts(20), "error_code": None, "error_text": None})  # 1200 s vs 330
        events.append({"event_key": "e2", "event_type": "TASK_ERROR", "created_at": ts(18),
                       "payload_json": json.dumps({"taskKey": tasks[1]["task_key"], "errorCode": "SOURCE_FAILED"})})
    return {"runId": run["run_id"], "run": run, "tasks": tasks, "attempts": attempts, "events": events, "ms": 900}


def make_observer(tmp: Path, run_id: str) -> obs.Observer:
    return obs.Observer(run_id, tmp / "out", poll=1, max_hours=1, settle_minutes=0)


def patch_probes(journal: dict, *, cdp_ok: bool, mysql_long: list[dict], host: list[dict] | None = None, wsl_ok: bool = True) -> None:
    obs.probe_journal = lambda run_id: dict(journal)
    seq = list(host or [{"cpu": 12.0, "freeMb": 9000, "totalMb": 32000, "chromeCount": 60, "chromeRssMb": 7000, "wslProcs": 2}])

    def next_host() -> dict:            # walk the sequence, then repeat the last sample
        return seq.pop(0) if len(seq) > 1 else seq[0]
    obs.probe_host = next_host
    obs.probe_wsl = lambda: {"ok": wsl_ok, "ms": 300, "rc": 0 if wsl_ok else None, "error": "" if wsl_ok else "TIMEOUT"}
    obs.probe_http = lambda url, timeout=5.0: ({"ok": True, "ms": 5, "json": {"Browser": "Chrome/139"}} if "version" in url
                                              else {"ok": True, "ms": 5, "json": [{"id": 1}, {"id": 2}]}) if cdp_ok \
        else {"ok": False, "ms": 5000, "error": "connection refused"}
    obs.probe_mysql = lambda: {"long": mysql_long}


def kinds(out_dir: Path) -> dict[str, int]:
    p = out_dir / "anomalies.jsonl"
    if not p.exists():
        return {}
    counts: dict[str, int] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        k = json.loads(line)["kind"]
        counts[k] = counts.get(k, 0) + 1
    return counts


def main() -> int:
    originals = {n: getattr(obs, n) for n in ("probe_journal", "probe_host", "probe_wsl", "probe_http", "probe_mysql", "ROOT")}
    try:
        # 1. pure helpers
        check("short_key strips day and hash", obs.short_key("2026-08-25:gemrate:pop+identity:0-of-4:f270a1c7a1b2c3d4") == "gemrate:pop+identity:0-of-4")
        check("short_key keeps non-hash tail", obs.short_key("2026-08-25:system:box:all") == "system:box:all")
        check("baseline gemrate shard 900", obs.baseline_for("gemrate:pop+identity:2-of-4") == 900)
        check("baseline contract-repair beats lane prefix", obs.baseline_for("pricecharting:contract-repair:quote") == 240)
        check("baseline unknown -> None", obs.baseline_for("mystery:thing") is None)
        check("wsl_path maps drive", obs.wsl_path(Path("C:/x/y.py")).startswith("/mnt/c/x/y.py") or obs.wsl_path(Path("C:/x/y.py")).startswith("/mnt/c/"))

        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # 2. clean run -> zero anomalies, not finished
            obs.ROOT = tmp
            patch_probes(journal_fixture(clean=True), cdp_ok=True, mysql_long=[])
            o = make_observer(tmp / "clean", "cardz-v2:2026-08-25")
            with redirect_stdout(io.StringIO()):
                done = o.poll_once()
            check("clean run: poll not finished", done is False)
            check("clean run: zero anomalies", kinds(o.out_dir) == {}, json.dumps(kinds(o.out_dir)))
            check("clean run: live.json written", (o.out_dir / "live.json").exists())
            check("clean run: run status logged RUNNING", o.run_status == "RUNNING")

            # 3. dirty run -> every detector fires exactly as designed
            (tmp / "logs" / "daily-chain-v2").mkdir(parents=True)
            launcher = tmp / "logs" / "daily-chain-v2" / f"launcher-{dt.datetime.now().strftime('%Y%m%d')}.log"
            launcher.write_text("[03:30:01] CARDZ_V2_START event=107 parent=wscript.exe\n"
                                "[03:30:09] CARDZ_V2_WSL_PREFLIGHT exit=3 WSL_DEAD probeMs=30000 Wsl/Service/0x8007274c\n"
                                "[03:30:10] CARDZ_V2_END exit=3 provenance=x wsl=dead\n", encoding="utf-8")
            patch_probes(journal_fixture(clean=False), cdp_ok=False,
                         mysql_long=[{"id": 9, "user": "cardz", "seconds": 700, "state": "executing", "sql": "SELECT ..."}],
                         host=[{"cpu": 99.0, "freeMb": 1000, "totalMb": 32000, "chromeCount": 60, "chromeRssMb": 7000, "wslProcs": 2},
                               {"cpu": 99.0, "freeMb": 1000, "totalMb": 32000, "chromeCount": 90, "chromeRssMb": 12000, "wslProcs": 2}],
                         wsl_ok=False)
            o = make_observer(tmp / "dirty", "cardz-v2:2026-08-25")
            with redirect_stdout(io.StringIO()):
                o.poll_once(); o.poll_once(); o.poll_once()   # CPU_SATURATED needs 3 consecutive polls
            k = kinds(o.out_dir)
            expected_once = ["SLA_EXCEEDED", "TASK_STATE_SKIPPED", "ATTEMPT_INTERRUPTED", "ATTEMPT_RETRY", "SLOW_TASK",
                             "EVENT_TASK_ERROR", "TICK_EXIT_NONZERO", "WSL_PREFLIGHT_NOT_OK", "WSL_SERVICE_ERROR", "CPU_SATURATED"]
            for kind in expected_once:
                check(f"fires once: {kind}", k.get(kind) == 1, f"count={k.get(kind)}")
            for kind in ["STALE_HEARTBEAT", "CDP_9333_DOWN", "MYSQL_LONG_QUERY", "WSL_PROBE_FAILED", "LOW_FREE_RAM"]:
                check(f"fires every poll: {kind}", k.get(kind) == 3, f"count={k.get(kind)}")
            for kind in ["CHROME_COUNT_GROWTH", "CHROME_RSS_GROWTH"]:       # first poll is the baseline, growth fires on polls 2+3
                check(f"fires on growth only: {kind}", k.get(kind) == 2, f"count={k.get(kind)}")
            check("no unexpected anomaly kinds", set(k) <= set(expected_once) | {"STALE_HEARTBEAT", "CDP_9333_DOWN", "MYSQL_LONG_QUERY",
                  "WSL_PROBE_FAILED", "LOW_FREE_RAM", "CHROME_COUNT_GROWTH", "CHROME_RSS_GROWTH"}, json.dumps(sorted(k)))
            log = (o.out_dir / "observer.log").read_text(encoding="utf-8")
            check("launcher line echoed once", log.count("tick| [03:30:10] CARDZ_V2_END exit=3") == 1)
            check("attempt duration logged", "system:daily-accept:all" in log and "COMPLETED" in log)

            # 4. journal probe failure is itself an anomaly and does not crash the poll
            obs.probe_journal = lambda run_id: {"error": "WSL_SNAPSHOT_TIMEOUT", "ms": 75000}
            o2 = make_observer(tmp / "probefail", "cardz-v2:2026-08-25")
            with redirect_stdout(io.StringIO()):
                done = o2.poll_once()
            check("journal timeout -> JOURNAL_PROBE_FAILED", kinds(o2.out_dir).get("JOURNAL_PROBE_FAILED") == 1)
            check("journal timeout counted", o2.peaks["journalTimeouts"] == 1 and done is False)

            # 5. terminal run -> finished after settle, report rendered
            term = journal_fixture(clean=True)
            term["run"]["status"] = "PUBLISHED"; term["run"]["publication_status"] = "PUBLISHED"
            term["run"]["generation_id"] = "db3308_test"; term["run"]["completed_at"] = ts(1)
            patch_probes(term, cdp_ok=False, mysql_long=[])       # CDP down after publish must NOT alarm
            o3 = make_observer(tmp / "term", "cardz-v2:2026-08-25")
            with redirect_stdout(io.StringIO()):
                done = o3.poll_once()
            check("terminal run: finished with settle 0", done is True)
            check("terminal run: CDP down not flagged after terminal", "CDP_9333_DOWN" not in kinds(o3.out_dir))
            with redirect_stdout(io.StringIO()):
                report = obs.write_report("cardz-v2:2026-08-25", o3.out_dir)
            text = report.read_text(encoding="utf-8")
            check("report has status line", "**PUBLISHED**" in text and "db3308_test" in text)
            check("report has task table row", "system:daily-accept:all" in text)
            check("report has host section", "## Host / probes" in text)
            check("summary.json written", (o3.out_dir / "summary.json").exists())

            # 6. snapshot subcommand against a sqlite journal with the real column names
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
            INSERT INTO chain_task(task_key,run_id,phase,status,attempts,max_attempts,created_at) VALUES('2026-08-25:system:box:all:abcdef0123456789','cardz-v2:2026-08-25#T1','publish','COMPLETED',1,3,'2026-08-24T18:31:00Z');
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
            check("snapshot run/tasks/attempts/events", data["run"]["status"] == "RUNNING" and len(data["tasks"]) == 1
                  and len(data["attempts"]) == 1 and len(data["events"]) == 1, json.dumps({k: data.get(k) for k in ("error",)}))
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
