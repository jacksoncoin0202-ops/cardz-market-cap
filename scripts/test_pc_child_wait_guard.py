#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC 9333 child self-collision guard: wait for our own child, don't fail blind.

2026-08-24 production: collect 行咗 4 次先過，attempt 2 同 3 死喺
``PC_CHILD_ALREADY_RUNNING`` —— 上一個 child 仲喺度收尾，parent 就即刻報錯，
成條鏈盲重試，等到舊 child 啱啱死咗嗰次先成功。

呢個檔守住新行為：
1. stamp 過咗 hard-stall window（child 已經死／marker 係殘留）→ 照開，唔使等。
2. child 仲跳緊但喺 budget 之內收工 → 等佢，然後照開，唔算 attempt 失敗。
3. child 一路唔收工 → budget 燒完之後同今日一模一樣噉報返
   ``pc_child_already_running``（可重試、唔算 coverage 蝕），而且**唔准**開第二個
   child，亦都唔准殺佢。

Run: python -X utf8 scripts/test_pc_child_wait_guard.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as cc  # noqa: E402
import pc_cdp_sold_refresh_win as mod  # noqa: E402

FAILED: list[str] = []
CHECKS = 0

PRODUCT_ID = "7108819"
BASE_URL = "https://www.pricecharting.com/game/pokemon-promo"


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class FakeClock:
    """A virtual clock: the guard's polling must never really sleep in a test."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(float(seconds))
        self.now += float(seconds)

    def __getattr__(self, name: str):  # anything else stays the real time module
        import time as real_time

        return getattr(real_time, name)


def _live_stamp(age: float = 1.0) -> dict:
    return {
        "stamp": "/fake/pc_cdp_progress.4242.stamp",
        "beatAt": utc_stamp(),
        "ageSeconds": age,
        "windowSeconds": 360.0,
    }


def _refresh_with_stub(tmp: Path, *, probe, clock: FakeClock) -> tuple[dict, int]:
    """Drive refresh_pc_pages with a stubbed child, PC map and liveness probe.

    Returns the report plus how many times the child was actually launched:
    a guard that waits is only correct if it still refuses to spawn a second
    sweep when the wait fails.
    """

    html_path = tmp / "refresh.html"
    html_path.write_text("y" * 6000, encoding="utf-8")
    row = {
        "variant_id": 1,
        "pc_product_id": PRODUCT_ID,
        "pc_url": f"{BASE_URL}/1",
        "html_path": str(html_path.relative_to(ROOT)).replace("\\", "/"),
    }
    report_path = tmp / "pc_cdp_refresh_report.json"
    if report_path.exists():
        report_path.unlink()
    complete = {
        "batch": 1,
        "ok": 1,
        "fail": 0,
        "cf": 0,
        "missingRequestedVariantIds": [],
        "results": [{"variant_id": 1, "status": "ok"}],
    }
    launches = {"n": 0}

    originals = (
        cc.OUT_DIR,
        cc.PC_REFRESH_REPORT,
        cc.WINDOWS_PY,
        cc._pc_subset_map,
        cc._run_pc_child,
        cc.time,
        cc.pc_child_alive_stamp,
    )
    cc.OUT_DIR = tmp
    cc.PC_REFRESH_REPORT = report_path
    cc.WINDOWS_PY = ROOT / "pipelines" / "pc_cdp_hidden_launch.vbs"
    cc._pc_subset_map = lambda items, *, mode, label: (tmp / "map.jsonl", [row])  # type: ignore[assignment]
    cc.time = clock  # type: ignore[assignment]
    if probe is not None:
        cc.pc_child_alive_stamp = probe  # type: ignore[assignment]

    def fake_child(cmd, *, timeout, dry_run, use_vbs=None):
        launches["n"] += 1
        report_path.write_text(
            json.dumps({**complete, "asOf": utc_stamp()}, ensure_ascii=False),
            encoding="utf-8",
        )
        return {"exit": 0, "childLogTail": ""}

    cc._run_pc_child = fake_child  # type: ignore[assignment]
    try:
        report = cc.refresh_pc_pages(
            [{"variantId": 1, "externalId": PRODUCT_ID, "modeNeeded": "incr"}],
            mode="incr",
            dry_run=False,
            resume_report=None,
            sleep_seconds=None,
            tabs=None,
            cdp_already_ensured=True,
        )
    finally:
        (
            cc.OUT_DIR,
            cc.PC_REFRESH_REPORT,
            cc.WINDOWS_PY,
            cc._pc_subset_map,
            cc._run_pc_child,
            cc.time,
            cc.pc_child_alive_stamp,
        ) = originals
    return report, launches["n"]


# ---------------------------------------------------------------------------
# (a) stale stamp / dead child: proceed, no wait at all
# ---------------------------------------------------------------------------
def test_stale_stamp_proceeds(tmp: Path) -> None:
    liveness = tmp / "liveness-stale"
    liveness.mkdir(parents=True, exist_ok=True)
    stamp = liveness / "pc_cdp_progress.4242.stamp"
    stamp.write_text("x", encoding="utf-8")
    window = float(getattr(mod, "HARD_STALL_KILLER_SECONDS", 360.0))
    stale_at = datetime.now(timezone.utc).timestamp() - (window + 120.0)
    os.utime(stamp, (stale_at, stale_at))

    original_dir = mod.PROGRESS_STAMP_DIR
    original_fallback = cc.PC_PROGRESS_STAMP_DIR_FALLBACK
    mod.PROGRESS_STAMP_DIR = liveness
    cc.PC_PROGRESS_STAMP_DIR_FALLBACK = liveness
    clock = FakeClock()
    try:
        check("a stale stamp is not a live child", cc.pc_child_alive_stamp(), None)
        report, launches = _refresh_with_stub(tmp, probe=None, clock=clock)
    finally:
        mod.PROGRESS_STAMP_DIR = original_dir
        cc.PC_PROGRESS_STAMP_DIR_FALLBACK = original_fallback

    check("a stale marker does not block the sweep", report.get("ok"), True)
    check("a stale marker costs no waiting", clock.slept, [])
    check("the sweep launched its child", launches, 1)
    check("no wait is recorded when nothing was running", "childWait" in report, False)


# ---------------------------------------------------------------------------
# (b) live child that exits inside the budget: wait, then proceed
# ---------------------------------------------------------------------------
def test_live_child_exits_within_budget(tmp: Path) -> None:
    calls = {"n": 0}

    def probe():
        calls["n"] += 1
        # busy on the first probe and the first poll; gone on the second poll
        return None if calls["n"] >= 3 else _live_stamp()

    clock = FakeClock()
    report, launches = _refresh_with_stub(tmp, probe=probe, clock=clock)

    check("waiting out our own child is not an attempt failure", report.get("ok"), True)
    check("the sweep runs after the wait", launches, 1)
    check("no contention error is reported", report.get("errorClass"), None)
    wait = report.get("childWait") or {}
    check("the wait is recorded", wait.get("exited"), True)
    check("it polled until the child was gone", wait.get("polls"), 2)
    check(
        "it waited one poll interval per poll",
        clock.slept,
        [cc.PC_CHILD_WAIT_POLL_SECONDS, cc.PC_CHILD_WAIT_POLL_SECONDS],
    )
    check(
        "the wait stays inside the budget",
        sum(clock.slept) <= cc.PC_CHILD_WAIT_BUDGET_SECONDS,
        True,
    )


# ---------------------------------------------------------------------------
# (c) live child that never exits: same refusal as today, no second child
# ---------------------------------------------------------------------------
def test_live_child_never_exits_still_refuses(tmp: Path) -> None:
    clock = FakeClock()
    report, launches = _refresh_with_stub(tmp, probe=lambda: _live_stamp(), clock=clock)

    check("a child that never exits still fails the sweep", report.get("ok"), False)
    check(
        "the error class is unchanged",
        report.get("errorClass"),
        cc.PC_CHILD_ALREADY_RUNNING_CLASS,
    )
    check("the refusal stays retryable", report.get("retryable"), True)
    check(
        "the backoff is unchanged",
        report.get("retryAfterSeconds"),
        cc.pc_error_retry_after_seconds(cc.PC_CHILD_ALREADY_RUNNING_CLASS),
    )
    check("nothing was opened, so nothing may be covered",
          report.get("attemptedFailedVariantIds"), [])
    check("contention is not coverage loss", report.get("coverageLoss"), False)
    check("a second 9333 child is never launched", launches, 0)
    wait = report.get("childWait") or {}
    check("the exhausted wait is recorded", wait.get("exited"), False)
    check(
        "the wait is bounded by the budget",
        sum(clock.slept) <= cc.PC_CHILD_WAIT_BUDGET_SECONDS,
        True,
    )
    check(
        "the budget is spent, not abandoned early",
        sum(clock.slept),
        cc.PC_CHILD_WAIT_BUDGET_SECONDS,
    )
    check("the still-running child is still reported", bool(report.get("childAlreadyRunning")), True)


# ---------------------------------------------------------------------------
# the waiter itself
# ---------------------------------------------------------------------------
def test_wait_helper_direct() -> None:
    original_time = cc.time
    clock = FakeClock()
    cc.time = clock  # type: ignore[assignment]
    try:
        never = cc.pc_wait_for_child_exit(
            budget_seconds=30.0, poll_seconds=7.0, probe=lambda: _live_stamp()
        )
        check("a budget that expires reports exited=False", never.get("exited"), False)
        check("the last poll is trimmed to the budget", clock.slept, [7.0, 7.0, 7.0, 7.0, 2.0])
        check("the waiter never overruns its budget", never.get("waitedSeconds"), 30.0)
        check("the last seen stamp is kept for the report", bool(never.get("lastStamp")), True)

        clock.slept.clear()
        zero = cc.pc_wait_for_child_exit(
            budget_seconds=0.0, poll_seconds=5.0, probe=lambda: _live_stamp()
        )
        check("a zero budget polls nothing", (zero.get("polls"), clock.slept), (0, []))
        check("a zero budget refuses immediately", zero.get("exited"), False)
    finally:
        cc.time = original_time  # type: ignore[assignment]

    # The guard waits; it must never reach for the child's life. (The word
    # SIGKILL appears elsewhere in the module as prose, so scope this to the
    # waiter's own source.)
    import inspect

    waiter_src = inspect.getsource(cc.pc_wait_for_child_exit)
    for banned in ("taskkill", "os.kill", "SIGKILL", ".terminate("):
        check(f"the waiter never kills the child: {banned}", banned in waiter_src, False)


def test_rc_file_visibility_grace(tmp: Path) -> None:
    """The VBS/WSL hand-off may expose the rc file after subprocess.run returns."""

    rc_path = tmp / "late.rc"
    original_time = cc.time
    original_reader = cc._read_rc_file
    clock = FakeClock()
    reads = {"n": 0}
    release_after = {"n": 4}

    def delayed_reader(path: Path) -> int | None:
        check("the rc waiter reads the requested authority file", path, rc_path)
        reads["n"] += 1
        return 0 if reads["n"] >= release_after["n"] else None

    cc.time = clock  # type: ignore[assignment]
    cc._read_rc_file = delayed_reader  # type: ignore[assignment]
    try:
        value, waited = cc.pc_wait_for_rc_file(
            rc_path, grace_seconds=10.0, poll_seconds=1.0
        )
        check("a late successful rc remains successful", value, 0)
        check("the waiter records the visibility lag", waited, 3.0)
        check("the rc waiter polls only until the file is readable", clock.slept, [1.0, 1.0, 1.0])

        reads["n"] = 0
        release_after["n"] = 999
        clock.slept.clear()
        value, waited = cc.pc_wait_for_rc_file(
            rc_path, grace_seconds=2.5, poll_seconds=1.0
        )
        check("a missing rc still fails after the grace budget", value, None)
        check("the final poll is trimmed to the grace budget", clock.slept, [1.0, 1.0, 0.5])
        check("the rc waiter never exceeds its budget", waited, 2.5)
    finally:
        cc.time = original_time  # type: ignore[assignment]
        cc._read_rc_file = original_reader  # type: ignore[assignment]


def main() -> int:
    test_tmp_root = ROOT / "data" / "runtime" / "test-tmp"
    test_tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="pc_child_wait_", dir=str(test_tmp_root)))
    try:
        test_stale_stamp_proceeds(tmp)
        test_live_child_exits_within_budget(tmp)
        test_live_child_never_exits_still_refuses(tmp)
        test_wait_helper_direct()
        test_rc_file_visibility_grace(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
