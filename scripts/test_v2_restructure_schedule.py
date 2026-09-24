#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""R7 2026-09-25: the daily schedule has one source of truth, and nothing drifts from it.

Python states the unattended window once (FIRST_SCHEDULED_TICK_JST /
LAST_SCHEDULED_TICK_JST plus the cutoff/SLA fractions in daily_chain_v2.py).
jst_schedule(), next_scheduled_tick_utc() and last_scheduled_tick_utc() must
all give that same window, with no second copy of the times in their bodies.
The Windows side states it again in PowerShell: the Task Scheduler trigger in
scripts/install_cardz_daily_v2_task.ps1 (start hour, window minutes, the
repetition pattern and the plan text) and whatever the launcher
scripts/cardz_daily_v2_launcher.ps1 hands WSL.  Both .ps1 files, and the
observer's expected tick, are only READ here; a disagreement fails this test.

No MySQL, no WSL, no Task Scheduler, no network.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
for name in ("CARDZ_V2_FIRST_TICK_JST", "CARDZ_V2_LAST_TICK_JST"):
    os.environ.pop(name, None)

import daily_chain_v2 as v2core  # noqa: E402

INSTALLER = ROOT / "scripts" / "install_cardz_daily_v2_task.ps1"
LAUNCHER = ROOT / "scripts" / "cardz_daily_v2_launcher.ps1"
OBSERVER = ROOT / "scripts" / "v2_run_observer.py"
JST = v2core.JST
failures: list[str] = []


def check(name: str, condition: bool, detail: Any = "") -> None:
    if condition:
        print(f"POSITIVE_OK {name}")
    else:
        failures.append(name)
        print(f"FAIL {name} {detail}")


def iso_minutes(text: str) -> int:
    """PT6H / PT10M / PT1H30M -> minutes."""

    match = re.fullmatch(r"PT(?:(\d+)H)?(?:(\d+)M)?", text)
    if not match:
        raise ValueError(text)
    return int(match.group(1) or 0) * 60 + int(match.group(2) or 0)


# --- Python: one statement of the window -----------------------------------
first = v2core.jst_hhmm(v2core.FIRST_SCHEDULED_TICK_JST)
last = v2core.jst_hhmm(v2core.LAST_SCHEDULED_TICK_JST)
check("python: the daily run starts 11:00 JST", first == (11, 0), first)
check("python: the daily run ends 17:00 JST", last == (17, 0), last)
WINDOW = timedelta(hours=last[0] - first[0], minutes=last[1] - first[1])
check("python: the window is six hours", WINDOW == timedelta(hours=6), WINDOW)

for day in (date(2026, 1, 5), date(2026, 8, 20), date(2026, 9, 25), date(2026, 12, 31)):
    sched = v2core.jst_schedule(day)
    start, final = sched["start"], sched["final"]
    check(f"{day}: start is 11:00 JST", start == datetime.combine(day, day_time(11, 0), tzinfo=JST), start)
    check(f"{day}: final is 17:00 JST", final == datetime.combine(day, day_time(17, 0), tzinfo=JST), final)
    check(f"{day}: source cutoff is 14:00 JST (50%)",
          sched["source_cutoff"] == datetime.combine(day, day_time(14, 0), tzinfo=JST)
          and sched["source_cutoff"] - start == WINDOW / 2, sched["source_cutoff"])
    check(f"{day}: SLA is 15:30 JST (75%)",
          sched["sla"] == datetime.combine(day, day_time(15, 30), tzinfo=JST)
          and sched["sla"] - start == WINDOW * 3 / 4, sched["sla"])
    check(f"{day}: the next scheduled tick is the schedule start",
          v2core.next_scheduled_tick_utc(start - timedelta(seconds=1)) == start,
          v2core.next_scheduled_tick_utc(start - timedelta(seconds=1)))
    check(f"{day}: the next tick after the start is tomorrow's start",
          v2core.next_scheduled_tick_utc(start) == start + timedelta(days=1))
    check(f"{day}: the last scheduled tick is the schedule final",
          v2core.last_scheduled_tick_utc(day) == final, v2core.last_scheduled_tick_utc(day))

# No second copy of the times inside the three readers (the pre-R7 bodies had
# at(11, 0)/at(17, 0), a "11:00" default and a 17, 0 fallback).
for function, pattern in (
    (v2core.jst_schedule, r"at\(\s*\d"),
    (v2core.next_scheduled_tick_utc, r"\"\d{1,2}:\d{2}\""),
    (v2core.last_scheduled_tick_utc, r"=\s*\d{1,2}\s*,\s*\d{1,2}\b"),
):
    body = inspect.getsource(function)
    body = body.split('"""', 2)[-1] if body.count('"""') >= 2 else body
    check(f"python: {function.__name__} reads the schedule constants, not a literal copy",
          not re.search(pattern, body), re.findall(pattern, body))

# --- Installer: the Task Scheduler trigger ----------------------------------
installer = INSTALLER.read_text(encoding="utf-8-sig")
start_match = re.search(
    r"(?m)^\$firstNaturalStart\s*=\s*\$nowLocal\.Date\.AddHours\((\d+)\)(?:\.AddMinutes\((\d+)\))?\s*$",
    installer,
)
check("installer: the daily StartBoundary is parsed", start_match is not None)
if start_match:
    installed_first = (int(start_match.group(1)), int(start_match.group(2) or 0))
    check("installer: the trigger starts at the python start", installed_first == first,
          (installed_first, first))
window_match = re.search(r"(?m)^\$DailyWindowMinutes\s*=\s*(\d+)", installer)
check("installer: $DailyWindowMinutes is parsed", window_match is not None)
if window_match:
    check("installer: $DailyWindowMinutes is the python window",
          int(window_match.group(1)) * 60 == WINDOW.total_seconds(), window_match.group(1))
repetition = re.search(
    r"New-ScheduledTaskTrigger -Daily -At \$firstNaturalStart.*?Interval\s*=\s*\"(PT[0-9HM]+)\""
    r"\s*Duration\s*=\s*\"(PT[0-9HM]+)\"",
    installer, re.S,
)
check("installer: the daily repetition pattern is parsed", repetition is not None)
if repetition:
    check("installer: the daily trigger repeats every ten minutes",
          iso_minutes(repetition.group(1)) == 10, repetition.group(1))
    check("installer: the daily repetition covers the python window",
          iso_minutes(repetition.group(2)) * 60 == WINDOW.total_seconds(), repetition.group(2))
hhmm = f"{first[0]:02d}:{first[1]:02d}"
hours = f"PT{int(WINDOW.total_seconds() // 3600)}H"
for label, text in (
    ("plan", f'trigger = "next {hhmm} local; then daily; repeat PT10M for {hours}"'),
    ("daily action", f'trigger = "daily {hhmm} local; repeat PT10M for {hours}"'),
):
    check(f"installer: the {label} text says {hhmm} for {hours}", text in installer, text)
# The trigger is "local" time and python is JST: they agree only on a +09:00
# box.  That is a fact about the host that runs the installer, not drift between
# the files, so a test run elsewhere (WSL with TZ=UTC, CI) only notes it
# (review fix 2026-09-25).
local_offset = datetime.combine(date(2026, 9, 25), day_time(11, 0)).astimezone().utcoffset()
if local_offset == timedelta(hours=9):
    print("POSITIVE_OK this machine's local time is JST, so 'local' in the trigger means JST")
else:
    print(f"SKIP local offset is {local_offset}, not +09:00: the installer's '11:00 local'"
          " is 11:00 JST only on a JST host")

# --- Launcher: hands WSL no schedule of its own -----------------------------
launcher = LAUNCHER.read_text(encoding="utf-8-sig")
for name in ("CARDZ_V2_FIRST_TICK_JST", "CARDZ_V2_LAST_TICK_JST"):
    check(f"launcher: never overrides {name}", name not in launcher)
env_assignments = re.findall(r'"env",\s*((?:"[A-Z0-9_]+=[^"]*",\s*)+)', launcher)
names = sorted(set(re.findall(r'"([A-Z0-9_]+)=', " ".join(env_assignments))))
check("launcher: the only env it hands WSL is CARDZ_V2_AUTO_SUPERSEDE",
      names == ["CARDZ_V2_AUTO_SUPERSEDE"], names)

# --- Observer: its expected first tick is the python start ------------------
observer = OBSERVER.read_text(encoding="utf-8")
expected = re.search(r"(?m)^EXPECTED_TICK_JST\s*=\s*\((\d+),\s*(\d+)\)", observer)
check("observer: EXPECTED_TICK_JST is parsed", expected is not None)
if expected:
    check("observer: EXPECTED_TICK_JST is the python start",
          (int(expected.group(1)), int(expected.group(2))) == first, expected.group(0))

if failures:
    print(f"FAILED {len(failures)}: {failures}")
    raise SystemExit(1)
print("ALL_OK test_v2_restructure_schedule")
