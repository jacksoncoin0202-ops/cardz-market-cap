#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stamp scheduled live-confirm days. Two consecutive JST days = proven.

Credit needs BOTH:
  --scheduled          argv token, only passed by daily_public_release.sh when
                       daily_public_release.ps1 got -Scheduled from a wrapper that
                       was itself spawned by the Task Scheduler service.
  CARDZ_DAILY_CHAIN=1  env, forwarded by the same ps1 via `wsl.exe -- env ...`.
The two must agree; --scheduled without the env is a plumbing bug and exits 2
(2026-08-15: env alone never crossed wsl.exe, every scheduled slot was recorded
as manual catch-up). Env alone (no token) is a manual run with a stale shell
variable and is recorded as manual catch-up.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data" / "runtime" / "operator" / "daily_chain_autonomy.json"
CONTRACT = "cardz-daily-chain-autonomy-v1"
JST = timezone(timedelta(hours=9))
REQUIRED_CONSECUTIVE_DAYS = 2
DAILY_CHAIN_ENV = "CARDZ_DAILY_CHAIN"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _jst_date(moment: datetime) -> str:
    return moment.astimezone(JST).date().isoformat()


def _load(receipt: Path) -> dict:
    if not receipt.is_file():
        return {
            "contract": CONTRACT,
            "rule": (
                "Two consecutive JST calendar days of scheduled "
                "(Task Scheduler launched: --scheduled + CARDZ_DAILY_CHAIN=1) "
                "live-confirmed publish or no-change. "
                "Manual catch-up does not count. proven=true only after that."
            ),
            "requiredConsecutiveDays": REQUIRED_CONSECUTIVE_DAYS,
            "proven": False,
            "consecutiveScheduledDays": 0,
            "days": [],
            "lastManualCatchupAt": None,
        }
    return json.loads(receipt.read_text(encoding="utf-8"))


def _write(receipt: Path, document: dict) -> None:
    receipt.parent.mkdir(parents=True, exist_ok=True)
    temporary = receipt.with_suffix(receipt.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(receipt)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--outcome", choices=("published", "no-change"), required=True)
    parser.add_argument("--scheduled", action="store_true",
                        help="launched by Task Scheduler (passed down by daily_public_release.ps1 -Scheduled)")
    parser.add_argument("--receipt", type=Path, default=RECEIPT,
                        help="receipt path override (proof-of-fire against a temp copy)")
    args = parser.parse_args()
    env_daily_chain = os.environ.get(DAILY_CHAIN_ENV, "").strip() == "1"
    if args.scheduled and not env_daily_chain:
        print(json.dumps({
            "autonomy": "launcher-plumbing-mismatch",
            "error": f"--scheduled given but {DAILY_CHAIN_ENV}!=1 in this process; refusing to stamp",
        }, ensure_ascii=False), file=sys.stderr)
        return 2
    scheduled = bool(args.scheduled)
    now = _utc_now()
    document = _load(args.receipt)
    document["contract"] = CONTRACT
    document["requiredConsecutiveDays"] = REQUIRED_CONSECUTIVE_DAYS
    event = {
        "at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "jstDate": _jst_date(now),
        "generation": args.generation,
        "generatedAt": args.generated_at,
        "outcome": args.outcome,
        "scheduled": scheduled,
        "envDailyChain": env_daily_chain,
    }
    if not scheduled:
        document["lastManualCatchupAt"] = event
        _write(args.receipt, document)
        print(json.dumps({"autonomy": "manual-catchup-ignored", **event}, ensure_ascii=False))
        return 0

    days = [row for row in document.get("days") or [] if row.get("scheduled")]
    if days and days[-1].get("jstDate") == event["jstDate"]:
        days[-1] = event
    else:
        days.append(event)
    consecutive = 0
    previous = None
    for row in days:
        current = datetime.fromisoformat(row["jstDate"]).date()
        if previous is None or current == previous + timedelta(days=1):
            consecutive = 1 if previous is None else consecutive + 1
        elif current == previous:
            pass
        else:
            consecutive = 1
        previous = current
    document["days"] = days[-14:]
    document["consecutiveScheduledDays"] = consecutive
    document["proven"] = consecutive >= REQUIRED_CONSECUTIVE_DAYS
    document["lastScheduledAt"] = event
    _write(args.receipt, document)
    print(json.dumps({
        "autonomy": "scheduled-day-stamped",
        "consecutiveScheduledDays": consecutive,
        "proven": document["proven"],
        **event,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
