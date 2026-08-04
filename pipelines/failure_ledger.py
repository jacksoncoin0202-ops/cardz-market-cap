#!/usr/bin/env python3
"""Private append-only failure receipts and deterministic retry worklists.

Failures are operational evidence, not canonical market facts.  Each attempt is
one immutable JSON event under ``data/runtime/failures/events``.  A later
verified success writes a resolution event against the same stable failure ID;
history is never deleted.

The module deliberately uses only the Python standard library so collectors can
call it from both Windows and WSL runtimes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LEDGER_ROOT = ROOT / "data" / "runtime" / "failures"
EVENTS_DIRNAME = "events"
RETRY_DIRNAME = "retry-worklists"
SCHEMA_VERSION = 1

_SECRET_KEY = re.compile(
    r"(?:authorization|cookie|credential|password|passwd|secret|session|token|api[_-]?key)",
    re.IGNORECASE,
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(authorization|cookie|password|passwd|secret|token|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)
_URL = re.compile(r"https?://[^\s<>'\"]+")
_MAX_STRING = 2000
_MAX_LIST = 50
_MAX_DEPTH = 5


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_text(value: datetime | None = None) -> str:
    stamp = (value or utc_now()).astimezone(timezone.utc)
    return stamp.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _clean_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value[:_MAX_STRING]
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return value[:_MAX_STRING]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _clean_string(value: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    text = _URL.sub(lambda match: _clean_url(match.group(0)), text)
    return text[:_MAX_STRING]


def sanitize(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-safe value with secret-like fields redacted."""

    if depth >= _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Path):
        return _clean_string(str(value))
    if isinstance(value, str):
        return _clean_string(value)
    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key in sorted(value, key=lambda item: str(item)):
            key = str(raw_key)[:128]
            if _SECRET_KEY.search(key):
                cleaned[key] = "[REDACTED]"
            else:
                cleaned[key] = sanitize(value[raw_key], depth=depth + 1)
        return cleaned
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitize(item, depth=depth + 1) for item in list(value)[:_MAX_LIST]]
    return _clean_string(str(value))


def normalize_script(script: str | Path) -> str:
    path = Path(str(script))
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()[:512]


def normalize_item_key(item_key: str | int) -> str:
    value = str(item_key).strip()
    if value.startswith(("http://", "https://")):
        return _clean_url(value)[:512]
    return _clean_string(value)[:512]


def stable_failure_id(
    *,
    source: str,
    stage: str,
    script: str | Path,
    item_key: str | int,
) -> str:
    identity = {
        "source": str(source).strip().casefold(),
        "stage": str(stage).strip().casefold(),
        "script": normalize_script(script).casefold(),
        "itemKey": normalize_item_key(item_key),
    }
    if not all(identity.values()):
        raise ValueError("failure identity requires source, stage, script, and item_key")
    return f"failure_{_sha256(identity)[:24]}"


def _events_root(ledger_root: Path) -> Path:
    return ledger_root.resolve() / EVENTS_DIRNAME


def _event_paths(ledger_root: Path, failure_id: str | None = None) -> list[Path]:
    root = _events_root(ledger_root)
    if failure_id is not None:
        return sorted((root / failure_id).glob("*.json"))
    return sorted(root.glob("failure_*/*.json"))


def _read_event_path(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"invalid failure event: {path}") from error
    if not isinstance(value, Mapping) or value.get("schemaVersion") != SCHEMA_VERSION:
        raise RuntimeError(f"unsupported failure event: {path}")
    return dict(value)


def read_events(
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    failure_id: str | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for path in _event_paths(ledger_root, failure_id):
        events.append(_read_event_path(path))
    return sorted(
        events,
        key=lambda event: (
            str(event.get("occurredAt") or ""),
            str(event.get("eventId") or ""),
        ),
    )


def _write_event(event: Mapping[str, Any], *, ledger_root: Path) -> Path:
    failure_id = str(event["failureId"])
    event_id = str(event["eventId"])
    occurred = re.sub(r"[^0-9]", "", str(event["occurredAt"]))[:20]
    destination = _events_root(ledger_root) / failure_id / f"{occurred}_{event_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        dict(event),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    if destination.is_file():
        if destination.read_bytes() != payload:
            raise RuntimeError(f"failure event collision: {destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def record_failure(
    *,
    source: str,
    stage: str,
    script: str | Path,
    item_key: str | int,
    reason_code: str,
    message: str = "",
    retryable: bool = True,
    run_id: str | None = None,
    url: str | None = None,
    context: Mapping[str, Any] | None = None,
    evidence_paths: Iterable[str | Path] = (),
    next_action: str | None = None,
    error_type: str | None = None,
    occurred_at: datetime | None = None,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
) -> Path | None:
    failure_id = stable_failure_id(
        source=source,
        stage=stage,
        script=script,
        item_key=item_key,
    )
    occurred_text = utc_text(occurred_at)
    existing_events = read_events(ledger_root=ledger_root, failure_id=failure_id)
    normalized_run_id = sanitize(run_id) if run_id else None
    normalized_reason = str(reason_code).strip() or "unknown_failure"
    if any(
        event.get("status") == "open"
        and event.get("occurredAt") == occurred_text
        and event.get("runId") == normalized_run_id
        and event.get("reasonCode") == normalized_reason
        for event in existing_events
    ):
        return None
    attempt = 1 + sum(1 for event in existing_events if event.get("status") == "open")
    event: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "failureId": failure_id,
        "status": "open",
        "source": str(source).strip(),
        "stage": str(stage).strip(),
        "script": normalize_script(script),
        "itemKey": normalize_item_key(item_key),
        "reasonCode": normalized_reason,
        "message": sanitize(message),
        "retryable": bool(retryable),
        "attempt": attempt,
        "runId": normalized_run_id,
        "url": _clean_url(url) if url else None,
        "context": sanitize(dict(context or {})),
        "evidencePaths": [
            normalize_script(path) if Path(str(path)).is_absolute() else Path(str(path)).as_posix()
            for path in list(evidence_paths)[:_MAX_LIST]
        ],
        "nextAction": str(next_action or ("retry" if retryable else "agent_review")),
        "errorType": str(error_type or "") or None,
        "occurredAt": occurred_text,
    }
    event["eventId"] = f"event_{_sha256(event)[:32]}"
    return _write_event(event, ledger_root=ledger_root)


def record_resolution(
    *,
    source: str,
    stage: str,
    script: str | Path,
    item_key: str | int,
    run_id: str | None = None,
    resolution: str = "verified_success",
    context: Mapping[str, Any] | None = None,
    evidence_paths: Iterable[str | Path] = (),
    occurred_at: datetime | None = None,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
) -> Path | None:
    failure_id = stable_failure_id(
        source=source,
        stage=stage,
        script=script,
        item_key=item_key,
    )
    existing_events = read_events(ledger_root=ledger_root, failure_id=failure_id)
    if not existing_events or existing_events[-1].get("status") != "open":
        return None
    event: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "failureId": failure_id,
        "status": "resolved",
        "source": str(source).strip(),
        "stage": str(stage).strip(),
        "script": normalize_script(script),
        "itemKey": normalize_item_key(item_key),
        "resolution": str(resolution).strip() or "verified_success",
        "runId": sanitize(run_id) if run_id else None,
        "context": sanitize(dict(context or {})),
        "evidencePaths": [
            normalize_script(path) if Path(str(path)).is_absolute() else Path(str(path)).as_posix()
            for path in list(evidence_paths)[:_MAX_LIST]
        ],
        "occurredAt": utc_text(occurred_at),
    }
    event["eventId"] = f"event_{_sha256(event)[:32]}"
    return _write_event(event, ledger_root=ledger_root)


def current_failure_states(
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
) -> list[dict[str, Any]]:
    root = _events_root(ledger_root)
    directories = [path for path in sorted(root.glob("failure_*")) if path.is_dir()]

    def load_current(directory: Path) -> dict[str, Any] | None:
        if not directory.is_dir():
            return None
        paths = sorted(directory.glob("*.json"))
        if not paths:
            return None
        latest = _read_event_path(paths[-1])
        if latest.get("status") != "open":
            return None
        state = dict(latest)
        state["attempts"] = int(latest.get("attempt") or 1)
        first_open = latest
        for path in paths:
            candidate = _read_event_path(path)
            if candidate.get("status") == "open":
                first_open = candidate
                break
        state["firstSeenAt"] = first_open["occurredAt"]
        state["lastSeenAt"] = latest["occurredAt"]
        state["failureId"] = str(latest["failureId"])
        return state

    states: list[dict[str, Any]] = []
    workers = min(32, max(4, (os.cpu_count() or 4) * 2))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for loaded in executor.map(load_current, directories):
            if loaded is not None:
                states.append(loaded)

    for state in states:
        context = state.get("context")
        if isinstance(context, Mapping) and not context.get("searchTerms"):
            search_terms = [
                value
                for value in (
                    context.get("tcg"),
                    context.get("name"),
                    context.get("set"),
                    context.get("setName"),
                    context.get("collectorNumber"),
                    "PSA 10",
                    state.get("source"),
                )
                if value
            ]
            if search_terms:
                state["context"] = {**context, "searchTerms": search_terms}
    return sorted(
        states,
        key=lambda state: (
            str(state.get("source") or ""),
            str(state.get("stage") or ""),
            str(state.get("script") or ""),
            str(state.get("itemKey") or ""),
        ),
    )


def current_failures(
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    source: str | None = None,
    script: str | None = None,
    stage: str | None = None,
    retryable_only: bool = False,
    next_action: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    states = current_failure_states(ledger_root=ledger_root)
    if source:
        states = [row for row in states if str(row.get("source") or "") == source]
    if script:
        normalized = normalize_script(script)
        states = [row for row in states if str(row.get("script") or "") == normalized]
    if stage:
        states = [row for row in states if str(row.get("stage") or "") == stage]
    if retryable_only:
        states = [row for row in states if row.get("retryable") is True]
    if next_action:
        states = [
            row for row in states
            if str(row.get("nextAction") or "") == next_action
        ]
    if limit is not None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        states = states[:limit]
    return states


def export_retry_worklist(
    destination: Path,
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
    source: str | None = None,
    script: str | None = None,
    stage: str | None = None,
    next_action: str | None = None,
    limit: int | None = None,
) -> Path:
    rows = current_failures(
        ledger_root=ledger_root,
        source=source,
        script=script,
        stage=stage,
        retryable_only=True,
        next_action=next_action,
        limit=limit,
    )
    document = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "cardz-failure-retry-worklist",
        "source": source,
        "script": normalize_script(script) if script else None,
        "stage": stage,
        "nextAction": next_action,
        "limit": limit,
        "asOf": max((str(row.get("lastSeenAt") or "") for row in rows), default=None),
        "count": len(rows),
        "items": rows,
    }
    payload = json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def summary(
    *,
    ledger_root: Path = DEFAULT_LEDGER_ROOT,
) -> dict[str, Any]:
    states = current_failure_states(ledger_root=ledger_root)
    by_source: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for state in states:
        source = str(state.get("source") or "unknown")
        reason = str(state.get("reasonCode") or "unknown_failure")
        by_source[source] = by_source.get(source, 0) + 1
        by_reason[reason] = by_reason.get(reason, 0) + 1
    return {
        "schemaVersion": SCHEMA_VERSION,
        "open": len(states),
        "retryable": sum(1 for state in states if state.get("retryable") is True),
        "agentReview": sum(
            1
            for state in states
            if str(state.get("nextAction") or "").startswith("agent_review")
        ),
        "bySource": dict(sorted(by_source.items())),
        "byReason": dict(sorted(by_reason.items())),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CARDZ private failure ledger")
    parser.add_argument("--ledger-root", type=Path, default=DEFAULT_LEDGER_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", help="print current open failures")
    list_cmd.add_argument("--source")
    list_cmd.add_argument("--script")
    list_cmd.add_argument("--stage")
    list_cmd.add_argument("--retryable-only", action="store_true")
    list_cmd.add_argument("--next-action")
    list_cmd.add_argument("--limit", type=int)

    sub.add_parser("summary", help="print aggregate current failure counts")

    export_cmd = sub.add_parser("export-retry", help="write deterministic retry worklist")
    export_cmd.add_argument("--out", type=Path, required=True)
    export_cmd.add_argument("--source")
    export_cmd.add_argument("--script")
    export_cmd.add_argument("--stage")
    export_cmd.add_argument("--next-action")
    export_cmd.add_argument("--limit", type=int)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.ledger_root.resolve()
    if args.command == "list":
        value: Any = current_failures(
            ledger_root=root,
            source=args.source,
            script=args.script,
            stage=args.stage,
            retryable_only=args.retryable_only,
            next_action=args.next_action,
            limit=args.limit,
        )
    elif args.command == "summary":
        value = summary(ledger_root=root)
    elif args.command == "export-retry":
        path = export_retry_worklist(
            args.out,
            ledger_root=root,
            source=args.source,
            script=args.script,
            stage=args.stage,
            next_action=args.next_action,
            limit=args.limit,
        )
        exported = json.loads(path.read_text(encoding="utf-8"))
        value = {
            "output": str(path),
            "exported": int(exported.get("count") or 0),
            "ledgerSummary": summary(ledger_root=root),
        }
    else:  # pragma: no cover - argparse enforces the choices
        raise AssertionError(args.command)
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
