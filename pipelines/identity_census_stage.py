#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Keep the GemRate population census fresh enough for identity intake.

`gemrate_identity_intake.census()` calls the census stale past seven days and
then takes in zero cards -- which reads exactly like "no card crossed the
population floor" while actually meaning "nobody looked".  Nothing in the daily
chain refreshed that file, so the census only ever got younger when an operator
remembered to harvest by hand.

This stage is the missing refresher.  It refuses to start a half-hour harvest
it cannot finish inside the tick and records every outcome.  The orchestrator
owns the fail-closed decision: a business date cannot move on to source work
until this receipt says the all-set census was refreshed.
Budget refusal starts no harvest: the wrapper refunds the attempt and the
orchestrator ends claiming for that tick, leaving the census for the next one.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CENSUS_RELATIVE = Path("data") / "private" / "gemrate_brute" / "psa10_1000_plus.jsonl"
RECEIPT_RELATIVE = Path("data") / "runtime" / "daily-chain-v2"
# Daily chain policy: one successful all-set refresh per natural business date.
# The dated receipt below prevents a second crawl during same-date retries;
# mtime remains evidence, never the schedule authority.
CENSUS_MAX_AGE_DAYS = 0.75
# A full --all-sets harvest owns the first tick.  The subprocess timeout is the
# hard network bound; the extra two minutes cover atomic promotion and the
# stage receipt while still fitting the natural 35-minute claim window.
HARVEST_BUDGET_SECONDS = 1920.0
HARVEST_TIMEOUT_SECONDS = 1800
SECONDS_PER_DAY = 86400.0


def census_path(root: Path | None = None) -> Path:
    return (root or ROOT) / CENSUS_RELATIVE


def receipt_path_for(business_date: str, root: Path | None = None) -> Path:
    return (root or ROOT) / RECEIPT_RELATIVE / f"identity-census-{business_date}.json"


def census_age_days(path: Path, now: datetime) -> float | None:
    """Age of the census in days, or None when there is no census at all."""

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return (now.timestamp() - mtime) / SECONDS_PER_DAY


def census_mtime_iso(path: Path) -> str | None:
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(mtime, timezone.utc).isoformat(timespec="seconds")


def harvest_command(root: Path | None = None) -> list[str]:
    return [
        sys.executable,
        "-X",
        "utf8",
        str((root or ROOT) / "pipelines" / "gemrate_brute_harvest.py"),
        "--all-sets",
    ]


def _subprocess_runner(command: list[str], *, timeout: int, cwd: Path) -> dict[str, Any]:
    proc = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
    return {
        "exitCode": int(proc.returncode),
        "outputTail": "\n".join(combined.splitlines()[-20:]),
    }


def write_receipt(receipt: Mapping[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_text(
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    return path


def run_identity_census(
    *,
    root: Path | None = None,
    now: datetime | None = None,
    business_date: str | None = None,
    budget_seconds: float | None = None,
    max_age_days: float = CENSUS_MAX_AGE_DAYS,
    runner: Callable[..., Mapping[str, Any]] | None = None,
    receipt_path: Path | None = None,
) -> dict[str, Any]:
    """Refresh the census when it is stale, and say what was decided either way.

    Never raises: every refusal and harvest failure comes back as a receipt.
    The daily-chain wrapper turns ``refreshed=false`` into the fail-closed task
    verdict; keeping this function total also keeps its dated evidence intact.
    """

    base = root or ROOT
    moment = now or datetime.now(timezone.utc)
    day = business_date or moment.strftime("%Y-%m-%d")
    path = census_path(base)
    target = receipt_path if receipt_path is not None else receipt_path_for(day, base)
    already_refreshed_today = False
    if target.is_file():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        already_refreshed_today = bool(
            isinstance(existing, Mapping)
            and str(existing.get("businessDate") or "") == day
            and existing.get("refreshed") is True
        )
    age = census_age_days(path, moment)
    receipt: dict[str, Any] = {
        "stage": "identity-census",
        "businessDate": day,
        "censusPath": str(path),
        "censusMtime": census_mtime_iso(path),
        "ageDays": None if age is None else round(age, 4),
        "maxAgeDays": float(max_age_days),
        "budgetSeconds": None if budget_seconds is None else round(float(budget_seconds), 1),
        # Keep the durable success bit true on same-date no-op receipts so a
        # later retry does not erase the evidence and trigger a second crawl.
        "refreshed": already_refreshed_today,
        "alreadyRefreshedThisBusinessDate": already_refreshed_today,
        "checkedAt": moment.isoformat(timespec="seconds"),
    }
    refresh_required = not already_refreshed_today
    if already_refreshed_today:
        receipt["skipReason"] = "business-date-already-refreshed"
    elif budget_seconds is not None and float(budget_seconds) < HARVEST_BUDGET_SECONDS:
        # Stated as its own reason: "stale and not refreshed" must never be
        # readable as "fresh", and the morning brief reads this receipt.
        receipt["skipReason"] = "tick-budget-too-short"
    else:
        receipt["skipReason"] = ""
        call = runner or _subprocess_runner
        command = harvest_command(base)
        receipt["command"] = list(command)
        try:
            outcome = call(command, timeout=HARVEST_TIMEOUT_SECONDS, cwd=base)
        except Exception as error:  # noqa: BLE001 - a stale census must not fail the run
            receipt["error"] = f"{type(error).__name__}: {error}"
        else:
            receipt["exitCode"] = int(outcome.get("exitCode", 1))
            receipt["outputTail"] = str(outcome.get("outputTail", ""))
            if receipt["exitCode"] == 0:
                receipt["refreshed"] = True
                receipt["censusMtime"] = census_mtime_iso(path)
                refreshed_age = census_age_days(path, moment)
                receipt["ageDays"] = None if refreshed_age is None else round(refreshed_age, 4)
            else:
                receipt["error"] = f"harvest exit={receipt['exitCode']}"
    if not receipt["refreshed"] and refresh_required:
        receipt["censusNote"] = (
            "business-date all-set census was not refreshed: the chain must not"
            " use an older file as evidence that no card crossed the floor"
        )
    # Named before it is written, so the file on disk and the stage result the
    # journal stores are the same document.
    receipt["receiptPath"] = str(target)
    write_receipt(receipt, target)
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-date", default=os.environ.get("CARDZ_V2_BUSINESS_DATE"))
    parser.add_argument("--max-age-days", type=float, default=CENSUS_MAX_AGE_DAYS)
    parser.add_argument("--budget-seconds", type=float, default=None)
    args = parser.parse_args()
    receipt = run_identity_census(
        business_date=args.business_date,
        max_age_days=args.max_age_days,
        budget_seconds=args.budget_seconds,
    )
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
