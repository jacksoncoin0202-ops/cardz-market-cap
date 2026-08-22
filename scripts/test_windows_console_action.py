"""Ratchet: the Task Scheduler actions must stay on the hidden wscript runner.

2026-08-22: a `powershell.exe` action under InteractiveToken is delegated to
Windows Terminal and STILL pops a visible window with `-WindowStyle Hidden`.
A human closed that window and 9 ticks died with 0xC000013A. The fix is
scripts/cardz_silent_run.vbs.

The old ratchet (`"-WindowStyle Hidden" in installer`) now matches only the
explanatory comment, so it passes on a reverted tree and proves nothing. This
file is the call site that actually fires. It is pure text (no Windows, no DB),
and lives in scripts/test_*.py so scripts/run_all_tests.py discovers it -- the
PowerShell suite scripts/test_cardz_daily_v2_windows.ps1 is not reachable from
that runner.
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_cardz_daily_v2_task.ps1"
RUNNER = ROOT / "scripts" / "cardz_silent_run.vbs"

NO_RUNNER = "installer no longer references scripts/cardz_silent_run.vbs"
NO_WSCRIPT = "task actions no longer execute wscript.exe (-Execute $WScriptExe)"
RAW_POWERSHELL = "a task action executes powershell.exe directly -> Windows Terminal window"


def check_installer(installer: str) -> None:
    """Raise AssertionError when the console fix has been reverted."""
    assert "cardz_silent_run.vbs" in installer, NO_RUNNER
    assert "consoleWindowStyle" in installer, "installer manifest lost consoleWindowStyle"
    assert "-Execute $WScriptExe" in installer, NO_WSCRIPT
    assert '-Execute "powershell.exe"' not in installer, RAW_POWERSHELL


def fires(installer: str) -> str:
    try:
        check_installer(installer)
    except AssertionError as exc:
        return str(exc)
    raise AssertionError("check_installer did NOT fire on a reverted installer")


def main() -> None:
    installer = INSTALLER.read_text(encoding="utf-8-sig")

    # NEGATIVE: the current tree is silent.
    check_installer(installer)
    print("NEGATIVE_OK current installer keeps the wscript hidden-runner actions")

    # POSITIVE 1: revert the action back to a bare powershell.exe execute.
    reverted = installer.replace("-Execute $WScriptExe", '-Execute "powershell.exe"')
    assert reverted != installer, "anchor '-Execute $WScriptExe' vanished; fix this test"
    msg = fires(reverted)
    assert msg in (NO_WSCRIPT, RAW_POWERSHELL), msg
    print(f"POSITIVE_OK powershell.exe action fires: {msg}")

    # POSITIVE 2: drop the runner entirely (comment included).
    stripped = installer.replace("cardz_silent_run.vbs", "cardz_silent_run.MISSING")
    assert stripped != installer
    msg = fires(stripped)
    assert msg == NO_RUNNER, msg
    print(f"POSITIVE_OK missing runner fires: {msg}")

    # The runner the installer points at must still be the hidden-window form.
    assert RUNNER.exists(), f"missing {RUNNER}"
    runner = RUNNER.read_text(encoding="utf-8-sig")
    assert "sh.Run(cmd, 0, True)" in runner, "vbs runner no longer runs the child hidden+waited"
    assert "WScript.Quit" in runner, "vbs runner no longer propagates the child rc"
    print("NEGATIVE_OK cardz_silent_run.vbs still Run(cmd, 0, True) + WScript.Quit")

    print("OK windows console action ratchet")


if __name__ == "__main__":
    main()
