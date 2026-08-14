#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stamp scheduled live-confirm days. Two consecutive JST days = proven."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "data" / "runtime" / "operator" / "daily_chain_autonomy.json"
CONTRACT = "cardz-daily-chain-autonomy-v1"
JST = timezone(timedelta(hours=9))
REQUIRED_CONSECUTIVE_DAYS = 2


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _jst_date(moment: datetime) -> str:
    return moment.astimezone(JST).date().isoformat()


def _load() -> dict:
    if not RECEIPT.is_file():
        return {
            "contract": CONTRACT,
            "rule": (
                "Two consecutive JST calendar days of scheduled "
                "(CARDZ_DAILY_CHAIN=1) live-confirmed publish or no-change. "
                "Manual catch-up does not count. proven=true only after that."
            ),
            "requiredConsecutiveDays": REQUIRED_CONSECUTIVE_DAYS,
            "proven": False,
            "consecutiveScheduledDays": 0,
            "days": [],
            "lastManualCatchupAt": None,
        }
    return json.loads(RECEIPT.read_text(encoding="utf-8"))


def _write(document: dict) -> None:
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    temporary = RECEIPT.with_suffix(RECEIPT.suffix + ".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(RECEIPT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation", required=True)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--outcome", choices=("published", "no-change"), required=True)
    args = parser.parse_args()
    scheduled = os.environ.get("CARDZ_DAILY_CHAIN", "").strip() == "1"
    now = _utc_now()
    document = _load()
    document["contract"] = CONTRACT
    document["requiredConsecutiveDays"] = REQUIRED_CONSECUTIVE_DAYS
    event = {
        "at": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "jstDate": _jst_date(now),
        "generation": args.generation,
        "generatedAt": args.generated_at,
        "outcome": args.outcome,
        "scheduled": scheduled,
    }
    if not scheduled:
        document["lastManualCatchupAt"] = event
        _write(document)
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
    _write(document)
    print(json.dumps({
        "autonomy": "scheduled-day-stamped",
        "consecutiveScheduledDays": consecutive,
        "proven": document["proven"],
        **event,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
