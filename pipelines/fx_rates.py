#!/usr/bin/env python3
"""Collect one validated daily USD FX snapshot for private CARDZ pipelines."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SUPPORTED_CURRENCIES = ("USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW")
QUOTE_CURRENCIES = SUPPORTED_CURRENCIES[1:]
DEFAULT_ENDPOINT = "https://api.frankfurter.dev/v2/rates"
DEFAULT_CACHE = Path(__file__).resolve().parents[1] / "data/runtime/private-fx/latest.json"


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("FX timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def stable_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def payload_sha256(value: Any) -> str:
    return hashlib.sha256(stable_bytes(value)).hexdigest()


def effective_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("FX date is missing")
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def normalise_response(payload: Any, fetched_at: datetime) -> dict[str, Any]:
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
        raise ValueError("FX response must be an array")
    rates: dict[str, dict[str, Any]] = {
        "USD": {"value": 1.0, "effectiveAt": iso_utc(fetched_at)},
    }
    for row in payload:
        if not isinstance(row, Mapping) or row.get("base") != "USD":
            raise ValueError("FX response contains a non-USD base")
        quote = row.get("quote")
        rate = row.get("rate")
        if quote not in QUOTE_CURRENCIES:
            continue
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"FX rate for {quote} is invalid")
        rates[str(quote)] = {
            "value": float(rate),
            "effectiveAt": iso_utc(effective_time(row.get("date"))),
        }
    missing = [currency for currency in QUOTE_CURRENCIES if currency not in rates]
    if missing:
        raise ValueError(f"FX response is missing: {', '.join(missing)}")
    return {
        "schemaVersion": "1.0.0",
        "base": "USD",
        "supported": list(SUPPORTED_CURRENCIES),
        "fetchedAt": iso_utc(fetched_at),
        "payloadSha256": payload_sha256(payload),
        "rates": rates,
    }


def validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise ValueError("FX snapshot must be an object")
    if snapshot.get("schemaVersion") != "1.0.0" or snapshot.get("base") != "USD":
        raise ValueError("FX snapshot contract is invalid")
    if snapshot.get("supported") != list(SUPPORTED_CURRENCIES):
        raise ValueError("FX supported-currency contract is invalid")
    parse_time(snapshot.get("fetchedAt"))
    if not isinstance(snapshot.get("payloadSha256"), str) or len(snapshot["payloadSha256"]) != 64:
        raise ValueError("FX payload hash is invalid")
    rates = snapshot.get("rates")
    if not isinstance(rates, dict) or set(rates) != set(SUPPORTED_CURRENCIES):
        raise ValueError("FX rate set is incomplete")
    for currency in SUPPORTED_CURRENCIES:
        row = rates[currency]
        if not isinstance(row, dict):
            raise ValueError(f"FX rate for {currency} is invalid")
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0:
            raise ValueError(f"FX rate for {currency} is invalid")
        parse_time(row.get("effectiveAt"))
    if float(rates["USD"]["value"]) != 1.0:
        raise ValueError("USD base rate must equal one")
    return snapshot


def request_payload(endpoint: str, timeout_seconds: float, retries: int) -> Any:
    parsed = urllib.parse.urlparse(endpoint)
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
        raise ValueError("FX endpoint must use HTTPS unless it is local self-hosting")
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key not in {"base", "quotes"}
    ]
    query.extend((("base", "USD"), ("quotes", ",".join(QUOTE_CURRENCIES))))
    url = urllib.parse.urlunparse(parsed._replace(query=urllib.parse.urlencode(query)))
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "CARDZ-Market-Cap-FX/1"})
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise RuntimeError(f"FX endpoint returned HTTP {response.status}")
                return json.loads(response.read())
        except Exception as error:  # network/parser errors share one bounded retry policy
            last_error = error
            if attempt + 1 < max(1, retries):
                time.sleep(2**attempt)
    raise RuntimeError("FX collection failed") from last_error


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def collect_or_reuse(
    output: Path,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    fixture: Path | None = None,
    now: datetime | None = None,
    timeout_seconds: float = 20,
    retries: int = 3,
    last_good_hours: float = 72,
) -> tuple[dict[str, Any], bool]:
    fetched_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        payload = read_json(fixture) if fixture is not None else request_payload(endpoint, timeout_seconds, retries)
        snapshot = normalise_response(payload, fetched_at)
        atomic_write(output, snapshot)
        return snapshot, False
    except Exception:
        if not output.is_file():
            raise
        previous = validate_snapshot(read_json(output))
        age = fetched_at - parse_time(previous["fetchedAt"])
        if age < timedelta(0) or age > timedelta(hours=last_good_hours):
            raise
        return previous, True


def public_currency_block(snapshot: Mapping[str, Any] | None, generated_at: datetime) -> tuple[dict[str, Any], str | None]:
    generated_at = generated_at.astimezone(timezone.utc)
    metrics: dict[str, dict[str, Any]] = {}
    worst: str | None = None
    validated: Mapping[str, Any] | None = None
    try:
        validated = validate_snapshot(dict(snapshot)) if snapshot is not None else None
    except (TypeError, ValueError):
        validated = None
    for currency in SUPPORTED_CURRENCIES:
        if currency == "USD":
            metrics[currency] = {"value": 1.0, "status": "ready", "asOf": iso_utc(generated_at)}
            continue
        if validated is None:
            metrics[currency] = {"value": None, "status": "unavailable", "asOf": None}
            worst = "unavailable"
            continue
        row = validated["rates"][currency]
        age = generated_at - parse_time(row["effectiveAt"])
        if age < timedelta(0) or age > timedelta(days=7):
            metrics[currency] = {"value": None, "status": "unavailable", "asOf": None}
            worst = "unavailable"
        else:
            status = "ready" if age <= timedelta(hours=96) else "stale"
            metrics[currency] = {"value": float(row["value"]), "status": status, "asOf": row["effectiveAt"]}
            if status == "stale" and worst is None:
                worst = "stale"
    as_of_values = [metric["asOf"] for currency, metric in metrics.items() if currency != "USD" and metric["asOf"]]
    return {
        "base": "USD",
        "supported": list(SUPPORTED_CURRENCIES),
        "rates": metrics,
        "asOf": min(as_of_values) if as_of_values else None,
    }, worst


def self_test() -> dict[str, Any]:
    now = datetime(2026, 7, 22, 12, tzinfo=timezone.utc)
    payload = [
        {"date": "2026-07-22", "base": "USD", "quote": "HKD", "rate": 7.8},
        {"date": "2026-07-22", "base": "USD", "quote": "CNY", "rate": 7.2},
        {"date": "2026-07-22", "base": "USD", "quote": "GBP", "rate": 0.78},
        {"date": "2026-07-22", "base": "USD", "quote": "TWD", "rate": 32.5},
        {"date": "2026-07-22", "base": "USD", "quote": "JPY", "rate": 162.5},
        {"date": "2026-07-22", "base": "USD", "quote": "KRW", "rate": 1380.0},
    ]
    snapshot = normalise_response(payload, now)
    public, state = public_currency_block(snapshot, now)
    with tempfile.TemporaryDirectory(prefix="cardz-fx-test-") as temporary:
        root = Path(temporary)
        fixture = root / "valid.json"
        invalid = root / "invalid.json"
        output = root / "latest.json"
        atomic_write(fixture, payload)
        atomic_write(invalid, {})
        collect_or_reuse(output, fixture=fixture, now=now)
        _, reused = collect_or_reuse(output, fixture=invalid, now=now + timedelta(hours=24), last_good_hours=72)
        expired_blocked = False
        try:
            collect_or_reuse(output, fixture=invalid, now=now + timedelta(hours=80), last_good_hours=72)
        except Exception:
            expired_blocked = True
    return {
        "supported": snapshot["supported"],
        "jpy": public["rates"]["JPY"],
        "krw": public["rates"]["KRW"],
        "state": state,
        "hashStable": snapshot["payloadSha256"] == payload_sha256(payload),
        "lastGoodReused": reused,
        "expiredLastGoodBlocked": expired_blocked,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect CARDZ daily USD exchange rates")
    parser.add_argument("--output", type=Path, default=DEFAULT_CACHE)
    parser.add_argument("--endpoint", default=os.environ.get("CARDZ_FX_ENDPOINT", DEFAULT_ENDPOINT))
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--timeout-seconds", type=float, default=20)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--last-good-hours", type=float, default=72)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(), sort_keys=True))
        return 0
    snapshot, reused = collect_or_reuse(
        args.output.resolve(),
        endpoint=args.endpoint,
        fixture=args.fixture.resolve() if args.fixture else None,
        timeout_seconds=args.timeout_seconds,
        retries=args.retries,
        last_good_hours=args.last_good_hours,
    )
    print(json.dumps({"status": "reused" if reused else "collected", "fetchedAt": snapshot["fetchedAt"], "supported": snapshot["supported"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
