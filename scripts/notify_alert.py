#!/usr/bin/env python3
"""Notification layer sitting on top of the daily outcome gate.

`verify_daily_run.py` already decides whether a run succeeded; this module
decides whether a human hears about it.  The default behaviour is unchanged --
an alert file plus a non-zero exit.  When ``CARDZ_ALERT_WEBHOOK`` is set the
same verdict is POSTed as JSON to that URL; when it is unset the POST is skipped
silently, so no channel decision (email / Telegram / Slack) is baked into the
repository.

Two entry points feed it, covering two different death modes:

  * ``scripts/verify_daily_run.py`` calls :func:`notify_failure` in-process.
    This covers "the chain exited 0 but the data is wrong" -- systemd's
    ``OnFailure=`` cannot see that case on its own, it only reacts to the exit
    code the gate produces.
  * ``deploy/systemd/run-cardz-alert.sh`` runs this as a CLI from the templated
    ``cardz-market-cap-alert@.service`` unit.  This covers the cases where the
    gate never ran at all: ``TimeoutStartSec`` killing the process tree,
    226/NAMESPACE, or a crash before the gate is reached.

Both paths derive the same dedupe key from the unit/tag name, so a single
incident that trips both produces one message, not two.

Repeated identical failures are throttled: the same status for the same key is
delivered once and then re-sent only every ``CARDZ_ALERT_REPEAT_DAYS`` days
(default 3).  A three-day outage must not produce three identical messages --
that is how an alert becomes background noise.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALERTS_DIR = ROOT / "data" / "runtime" / "alerts"
LOGS_DIR = ROOT / "data" / "runtime" / "logs"
# 唔放喺 alerts/ 入面：cardz-status.sh 數 alerts/*.json 當「未清 alert」，
# 狀態檔擺埋落去會令狀態永遠報紅。
STATE_PATH = ROOT / "data" / "runtime" / "notify" / "state.json"

WEBHOOK_ENV = "CARDZ_ALERT_WEBHOOK"
REPEAT_DAYS_ENV = "CARDZ_ALERT_REPEAT_DAYS"

DEFAULT_REPEAT_DAYS = 3
# 同一單事故嘅兩個面向（verify 判 fail，跟住 unit 因為呢個 exit code 變 failed）
# 會喺幾秒內先後叫呢個 notifier。呢個窗令佢哋只出一次聲。
QUIET_MINUTES = 30
TAIL_LINES = 12
# log 可以係幾百 MB 嘅爬蟲進度，只讀尾段。
TAIL_BYTES = 262_144
POST_TIMEOUT_SECONDS = 10

ERROR_LINE = re.compile(
    r"traceback|error|exception|fail|critical|refused|timed?\s?out|abort|killed",
    re.IGNORECASE,
)

# log 尾段會直接離開呢部機，過濾走明顯嘅憑證先好出街。
REDACTIONS = (
    (re.compile(r"((?:password|passwd|pwd|secret|token|api[_-]?key)\s*[=:]\s*)(\S+)", re.IGNORECASE), r"\1***"),
    (re.compile(r"(://[^/\s:@]+:)([^@\s]+)(@)"), r"\1***\3"),
    (re.compile(r"(bearer\s+)(\S+)", re.IGNORECASE), r"\1***"),
)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def redact(text: str) -> str:
    for pattern, replacement in REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def env_repeat_days() -> int:
    try:
        value = int(os.environ.get(REPEAT_DAYS_ENV, "").strip())
    except ValueError:
        return DEFAULT_REPEAT_DAYS
    return value if value > 0 else DEFAULT_REPEAT_DAYS


def latest_log(pattern: str, logs_dir: Path = LOGS_DIR) -> Path | None:
    candidates = [p for p in Path(logs_dir).glob(pattern) if p.is_file()]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def error_lines(log_path, limit: int = TAIL_LINES) -> list:
    """Last few lines that look like a failure, falling back to a plain tail.

    A real daily log is thousands of `public card pages 825/1468` progress
    lines; a naive tail would say nothing about why the run died.
    """
    if not log_path:
        return []
    path = Path(log_path)
    if not path.is_file():
        return []
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - TAIL_BYTES))
            chunk = handle.read()
    except OSError:
        return []
    lines = [line.rstrip() for line in chunk.decode("utf-8", "replace").splitlines() if line.strip()]
    if not lines:
        return []
    hits = [line for line in lines if ERROR_LINE.search(line)]
    picked = hits[-limit:] if hits else lines[-limit:]
    return [redact(line)[:500] for line in picked]


def read_state(state_path: Path = STATE_PATH) -> dict:
    try:
        loaded = json.loads(Path(state_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_state(state: dict, state_path: Path = STATE_PATH) -> None:
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_notify(
    entry,
    status: str,
    now: datetime,
    repeat_after_days: int = DEFAULT_REPEAT_DAYS,
    quiet_minutes: int = QUIET_MINUTES,
) -> tuple:
    """Pure throttle decision; returns (send, reason)."""
    if not entry:
        return True, "first occurrence"
    last = parse_ts(entry.get("lastNotifiedAt"))
    if last is None:
        return True, "nothing delivered yet"
    age = now - last
    if entry.get("status") != status:
        if age < timedelta(minutes=quiet_minutes):
            return False, f"another facet of the same incident went out {int(age.total_seconds() // 60)}m ago"
        return True, "status changed"
    if age >= timedelta(days=repeat_after_days):
        return True, f"still failing {repeat_after_days}d after the last message"
    return False, f"unchanged since {entry.get('lastNotifiedAt')}"


def build_payload(
    *,
    key: str,
    status: str,
    stage: str,
    expected_date: str,
    exit_code: int,
    log_path=None,
    log_tail=None,
    unit=None,
    occurrences: int = 1,
    first_seen=None,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    summary = f"CARDZ {key} FAILED {expected_date} — {stage} (exit {exit_code})"
    if occurrences > 1:
        summary += f" — day {occurrences} of this failure"
    return {
        "alert": "cardz_daily_failure",
        # Slack-shaped receivers render `text` verbatim; everything else can read the fields.
        "text": summary,
        "key": key,
        "status": status,
        "stage": stage,
        "date": expected_date,
        "exitCode": exit_code,
        "unit": unit,
        "host": socket.gethostname(),
        "logPath": str(log_path) if log_path else None,
        "logTail": list(log_tail or []),
        "occurrences": occurrences,
        "firstSeen": first_seen or iso(now),
        "notifiedAt": iso(now),
    }


def post_webhook(url: str, payload: dict, timeout: int = POST_TIMEOUT_SECONDS) -> tuple:
    """POST the payload as JSON. Returns (delivered, detail); never raises.

    The URL itself is never echoed back — it is a secret in backend.env.
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as error:
        return False, f"HTTP {error.code}"
    except Exception as error:  # DNS / TLS / refused / malformed URL
        detail = str(error).replace(url, "<webhook>")
        return False, f"{type(error).__name__}: {detail[:200]}"


def notify(
    *,
    key: str,
    status: str,
    stage: str,
    expected_date: str,
    exit_code: int,
    log_path=None,
    unit=None,
    webhook=None,
    now: datetime | None = None,
    state_path: Path = STATE_PATH,
    repeat_after_days=None,
    force: bool = False,
    persist: bool = True,
    transport=None,
) -> dict:
    """Throttle, build, and (optionally) deliver one failure notification."""
    now = now or datetime.now(timezone.utc)
    webhook = os.environ.get(WEBHOOK_ENV, "").strip() if webhook is None else webhook
    repeat_after_days = env_repeat_days() if repeat_after_days is None else repeat_after_days
    transport = transport or post_webhook

    state = read_state(state_path)
    entry = state.get(key) if isinstance(state.get(key), dict) else None
    same_status = bool(entry and entry.get("status") == status)
    occurrences = (entry.get("occurrences", 0) + 1) if same_status else 1
    first_seen = entry.get("firstSeen") if same_status else iso(now)

    send, reason = (True, "forced") if force else should_notify(entry, status, now, repeat_after_days)

    payload = build_payload(
        key=key, status=status, stage=stage, expected_date=expected_date,
        exit_code=exit_code, log_path=log_path, log_tail=error_lines(log_path),
        unit=unit, occurrences=occurrences, first_seen=first_seen, now=now,
    )

    delivered, detail = False, ""
    if not send:
        summary = f"skipped ({reason})"
    elif not webhook:
        summary = f"no {WEBHOOK_ENV} configured; alert recorded only"
    else:
        delivered, detail = transport(webhook, payload)
        summary = f"delivered ({detail})" if delivered else f"delivery failed ({detail})"

    if persist:
        state[key] = {
            "status": status,
            "stage": stage,
            "firstSeen": first_seen,
            "lastSeen": iso(now),
            # 只有真係送到先推進，否則下一次 run 應該再試。
            "lastNotifiedAt": iso(now) if delivered else (entry or {}).get("lastNotifiedAt"),
            "occurrences": occurrences,
        }
        write_state(state, state_path)

    return {
        "sent": delivered,
        "attempted": send,
        "reason": reason,
        "detail": detail,
        "summary": summary,
        "webhookConfigured": bool(webhook),
        "payload": payload,
    }


def clear(key: str, state_path: Path = STATE_PATH) -> bool:
    """Forget a key after a good run so the next failure alerts immediately."""
    try:
        state = read_state(state_path)
        if key not in state:
            return False
        del state[key]
        write_state(state, state_path)
    except OSError:
        return False
    return True


def failure_stage(checks, reason: str) -> str:
    failed = [c.get("check") for c in checks or [] if not c.get("pass")]
    if failed:
        return "verify gate: " + ", ".join(str(name) for name in failed)
    return reason or "verify gate"


def failure_status(exit_code: int, checks) -> str:
    failed = sorted(str(c.get("check")) for c in checks or [] if not c.get("pass"))
    return f"exit{exit_code}|" + (",".join(failed) if failed else "no-checks")


def notify_failure(*, tag: str, expected_date: str, exit_code: int, checks, reason: str) -> dict:
    """In-process hook for verify_daily_run.py. Never raises — a notifier
    problem must not turn a diagnosable data failure into a crash."""
    try:
        return notify(
            key=tag,
            status=failure_status(exit_code, checks),
            stage=failure_stage(checks, reason),
            expected_date=expected_date,
            exit_code=exit_code,
            log_path=latest_log(f"{tag}_*.log"),
        )
    except Exception as error:  # noqa: BLE001 - notifier must never break the gate
        return {"sent": False, "summary": f"notifier error: {type(error).__name__}: {error}"}


def write_alert_file(payload: dict) -> Path:
    ALERTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = ALERTS_DIR / f"{payload['key']}_notify_{payload['date']}_{stamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", default="daily", help="Dedupe key; matches the verify --tag (daily | watchdog)")
    parser.add_argument("--status", default="", help="Short machine status; the dedupe compares this")
    parser.add_argument("--stage", default="", help="Human-readable stage that died")
    parser.add_argument("--exit-code", type=int, default=1)
    parser.add_argument("--expected-date", default=datetime.now(timezone.utc).date().isoformat())
    parser.add_argument("--unit", default=None, help="systemd unit name, for context only")
    parser.add_argument("--log", default=None, help="Explicit log path; otherwise the newest --log-glob match")
    parser.add_argument("--log-glob", default=None, help="Glob under data/runtime/logs (default <key>_*.log)")
    parser.add_argument("--write-alert", action="store_true",
                        help="Also drop a JSON under data/runtime/alerts/ (for callers that have not written one)")
    parser.add_argument("--resolved", action="store_true", help="Clear the dedupe state for --key and exit")
    parser.add_argument("--self-test", action="store_true",
                        help="Send one clearly-labelled test notification, bypassing dedupe and leaving state untouched")
    args = parser.parse_args()

    if args.resolved:
        print(f"[notify] state cleared for {args.key}" if clear(args.key) else f"[notify] no state for {args.key}")
        return 0

    log_path = Path(args.log) if args.log else latest_log(args.log_glob or f"{args.key}_*.log")

    if args.self_test:
        stamp = iso(datetime.now(timezone.utc))
        result = notify(
            key="selftest",
            status=f"selftest:{stamp}",
            stage=args.stage or "manual self-test — no real failure",
            expected_date=args.expected_date,
            exit_code=args.exit_code,
            log_path=log_path,
            unit=args.unit,
            force=True,
            persist=False,
        )
    else:
        result = notify(
            key=args.key,
            status=args.status or f"exit{args.exit_code}",
            stage=args.stage or f"unit exit {args.exit_code}",
            expected_date=args.expected_date,
            exit_code=args.exit_code,
            log_path=log_path,
            unit=args.unit,
        )

    print(f"[notify] {result['summary']}")
    print(json.dumps(result["payload"], ensure_ascii=False, indent=2))

    if args.write_alert:
        print(f"[notify] alert written: {write_alert_file(result['payload'])}")

    # 有 webhook 但送唔到 = 值得見紅；冇 webhook 或者被 dedupe 擋住都係正常路徑。
    if result["attempted"] and result["webhookConfigured"] and not result["sent"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
