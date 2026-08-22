#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stall watchdog must ignore CF/backoff liveness beats.

2026-08-22: 9333 looked RUNNING for 30+ min, V2 heartbeated every 10s, HTML
wrote 0 pages. The old watchdog reset `beat` on CF 5s polls and shared
backoff sleeps, so one hung tab never tripped exit 3.

Run: python -X utf8 scripts/test_pc_stall_watchdog.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_cdp_sold_refresh_win as mod  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    results: list[dict] = []
    state = mod.new_stall_watchdog_state(batch=1028, results=results, limit=300.0)
    t0 = float(state["pageDecisionBeat"])
    check("fresh state does not fire", mod.stall_watchdog_should_fire(state, now=t0 + 10), False)
    state["beat"] = t0 + 400
    check(
        "liveness beat alone still fires after 300s without a page decision",
        mod.stall_watchdog_should_fire(state, now=t0 + 400),
        True,
    )
    planted = False
    try:
        check(
            "planted no-decision stall is visible",
            mod.stall_watchdog_should_fire(state, now=t0 + 400),
            True,
        )
        planted = True
    finally:
        check("planted stall was observed before restore", planted, True)
    mod.mark_page_decision(state, now=t0 + 400)
    check(
        "a page decision clears the stall",
        mod.stall_watchdog_should_fire(state, now=t0 + 410),
        False,
    )
    check(
        "300s after the last decision fires again",
        mod.stall_watchdog_should_fire(state, now=t0 + 701),
        True,
    )

    now = datetime(2026, 8, 22, 5, 0, tzinfo=timezone.utc)
    check(
        "finished batch is not auto-resumed",
        mod.should_auto_resume_report(
            {"asOf": "2026-08-22T04:50:00.000000Z", "ok": 1028, "batch": 1028},
            now=now,
        ),
        False,
    )
    check(
        "watchdog stall auto-resumes",
        mod.should_auto_resume_report(
            {
                "asOf": "2026-08-22T04:40:00.000000Z",
                "ok": 0,
                "batch": 1028,
                "watchdogStall": True,
            },
            now=now,
        ),
        True,
    )
    check(
        "partial ok auto-resumes",
        mod.should_auto_resume_report(
            {"asOf": "2026-08-22T04:40:00.000000Z", "ok": 40, "batch": 1028},
            now=now,
        ),
        True,
    )
    check(
        "stale stall is not auto-resumed",
        mod.should_auto_resume_report(
            {
                "asOf": "2026-08-21T04:40:00.000000Z",
                "ok": 0,
                "batch": 1028,
                "watchdogStall": True,
            },
            now=now,
        ),
        False,
    )
    check(
        "older than 6h is not auto-resumed",
        mod.should_auto_resume_report(
            {
                "asOf": (now - timedelta(hours=6, seconds=1)).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                ),
                "watchdogStall": True,
                "ok": 0,
                "batch": 10,
            },
            now=now,
            max_age_seconds=6 * 3600,
        ),
        False,
    )

    source = (ROOT / "pipelines" / "pc_cdp_sold_refresh_win.py").read_text(encoding="utf-8")
    check(
        "connect_over_cdp has an explicit timeout",
        "timeout=CONNECT_OVER_CDP_TIMEOUT_MS" in source,
        True,
    )
    check(
        "hard stall killer exists",
        "def spawn_hard_stall_killer" in source,
        True,
    )
    check(
        "watchdog armed before MAP load",
        source.find("arm_stall_watchdog(watchdog)")
        < source.find("MAP.read_text"),
        True,
    )
    check(
        "jammed attach recycles chrome once",
        "ensure_cdp(cdp_port)" in source
        and "for attach_attempt in range(2)" in source,
        True,
    )

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
