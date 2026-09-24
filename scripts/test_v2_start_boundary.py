"""Call site for the V2 installer's StartBoundary guard.

P0, 2026-08-24: `install_cardz_daily_v2_task.ps1 -Apply` run inside the live
03:30-17:00 window used to roll StartBoundary to *tomorrow*, which replaces the
day's trigger wholesale and deletes every remaining repetition. Task Scheduler
Operational log, event 100, task CARDZ-Marketcap-Daily-V2: 08-23 fired 75 ticks
across the full window; the 03:58:36 re-install on 08-24 left 4 and the next
tick was 08-25 03:30. Nothing errored -- the plan printed "applied".

StartBoundary on a -Daily trigger carries the daily recurrence time, so the fix
is NOT max(now, window start) (that would move the chain to run daily at the
re-install time). The time-of-day stays 03:30 and only the date rolls, and only
once the window has closed.

The installer exposes nowLocal / startBoundary / insideDailyWindow /
todayTicksPreserved in its -Print plan, and -NowOverride seeds a synthetic
clock (dry-run only), so all of this is provable offline with no registration.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "install_cardz_daily_v2_task.ps1"

# The guard as shipped, and the pre-fix line it replaced. The planted-bug twin
# swaps one for the other, so this pair is also the drift ratchet.
FIXED_GUARD = "if ($nowLocal -ge $firstNaturalStart.AddMinutes($DailyWindowMinutes)) {"
BUGGY_GUARD = "if ($firstNaturalStart -le $nowLocal) {"

# (now, expected startBoundary date+time, insideDailyWindow, todayTicksPreserved)
CASES = [
    ("2026-08-25T02:23:00", "2026-08-25 11:00:00", False, 0),
    ("2026-08-25T10:58:00", "2026-08-25 11:00:00", False, 0),
    ("2026-08-25T11:28:00", "2026-08-25 11:00:00", True, 34),
    ("2026-08-25T16:59:00", "2026-08-25 11:00:00", True, 1),
    ("2026-08-25T17:30:00", "2026-08-26 11:00:00", False, 0),
]


def _windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path)
    return subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def _plan(script: Path, *extra: str) -> dict:
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _windows_path(script),
            "-Print",
            *extra,
        ],
        cwd=str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32",
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    out = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, out
    return json.loads(result.stdout)


def _boundary(plan: dict) -> str:
    # PowerShell emits "2026-08-25T03:30:00+09:00"; compare date+time only so the
    # test does not depend on the box's offset.
    return plan["startBoundary"][:19].replace("T", " ")


def test_start_boundary_keeps_todays_window():
    for now, want_boundary, want_inside, want_ticks in CASES:
        plan = _plan(PS1, "-NowOverride", now)
        assert _boundary(plan) == want_boundary, (now, plan)
        assert bool(plan["insideDailyWindow"]) is want_inside, (now, plan)
        assert int(plan["todayTicksPreserved"]) == want_ticks, (now, plan)


def test_now_override_is_refused_under_apply():
    result = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _windows_path(PS1),
            "-Apply",
            "-NowOverride",
            "2026-08-25T09:07:00",
        ],
        cwd=str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32",
        capture_output=True,
        text=True,
        errors="replace",
        timeout=120,
    )
    out = (result.stdout or "") + (result.stderr or "")
    assert "must not be combined with -Apply" in out, out
    assert '"result"' not in out, out


def test_planted_bug_loses_the_day():
    """Revert the guard in a throwaway copy; it must lose the ticks again.

    Without this the positive test only proves the installer prints numbers, not
    that the guard is what produces them.
    """
    text = PS1.read_text(encoding="utf-8-sig")
    assert text.count(FIXED_GUARD) == 1, "mutation anchor must be unique"
    reverted = text.replace(FIXED_GUARD, BUGGY_GUARD)
    assert reverted != text

    # PowerShell cannot reliably execute a -File path through a WSL UNC temp
    # share. Keep the planted twin on the mounted Windows volume in WSL; on
    # native Windows the same directory is local as well.
    temp_root = ROOT / "data" / "runtime" / "tests"
    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=temp_root) as tmp:
        # Copy the whole scripts dir: the installer Test-Paths its siblings
        # (launcher, silent runner, watchdog) and throws if they are missing.
        stage = Path(tmp) / "scripts"
        shutil.copytree(ROOT / "scripts", stage)
        twin = stage / "install_cardz_daily_v2_task.ps1"
        twin.write_text(reverted, encoding="utf-8")

        plan = _plan(twin, "-NowOverride", "2026-08-24T11:28:00")
        assert _boundary(plan) == "2026-08-25 11:00:00", (
            "planted bug did not fire: the reverted guard still kept the day's "
            f"window, so this test proves nothing. plan={plan}"
        )
        assert int(plan["todayTicksPreserved"]) == 0, plan
    print("PLANTED_BUG_FIRED start-boundary")


def test_window_constants_match_the_repetition_patterns():
    text = PS1.read_text(encoding="utf-8-sig")
    assert re.search(r"\$DailyWindowMinutes\s*=\s*360", text)
    assert 'Duration = "PT6H"' in text
    assert re.search(r"\$WatchdogWindowMinutes\s*=\s*420", text)
    assert 'Duration = "PT7H"' in text
    assert 'Interval = "PT10M"' in text


if __name__ == "__main__":
    tests = [
        test_start_boundary_keeps_todays_window,
        test_now_override_is_refused_under_apply,
        test_planted_bug_loses_the_day,
        test_window_constants_match_the_repetition_patterns,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    sys.exit(1 if failed else 0)
