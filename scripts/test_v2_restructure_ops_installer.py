"""Ratchet: the V2 installer never registers or enables the promo task.

2026-09-25: the owner stopped the promo chain on 2026-09-23 (Hermes job paused,
Task Scheduler CARDZ-Promo-After-Publish left Disabled).  The V2 installer still
managed that task, and Register-ScheduledTask -Force writes a fresh definition
whose settings default to Enabled, so any -Apply of the cutover would have
switched promo back on.  Pure text: the .ps1 files are read, never executed
(no Task Scheduler, no WSL), so this runs anywhere scripts/run_all_tests.py does.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_cardz_daily_v2_task.ps1"
OBSERVER_INSTALLER = ROOT / "scripts" / "install_v2_observer_task.ps1"

PROMO_TASK = "CARDZ-Promo-After-Publish"
MANAGED = ["CARDZ-Marketcap-Daily-V2", "CARDZ-037-Watchdog-Live-Release"]


def code_of(text: str) -> str:
    """Drop full-line comments: the WHY notes may name the promo task, code may not."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def check_installer(text: str) -> None:
    code = code_of(text)
    assert PROMO_TASK not in code, f"installer code names {PROMO_TASK} again"
    assert "promo_after_publish" not in code, "installer code points an action at promo_after_publish.py"
    assert not re.search(r"(?m)^\s*promo\s*=", code), "installer plan grew a promo action block"
    # PowerShell cmdlet names are case-insensitive, and schtasks.exe /ENABLE or
    # Set-ScheduledTask can switch a task on without any Register call (review
    # 2026-09-25).  Besides Register-CardzManagedTask, the only task write left
    # is Disable-ScheduledTask on the legacy tasks.
    folded = code.casefold()
    assert "enable-scheduledtask" not in folded, "installer enables a scheduled task"
    assert "set-scheduledtask" not in folded and "schtasks" not in folded, (
        "installer edits a task outside Register-CardzManagedTask"
    )
    assert code.count("Register-ScheduledTask") == 1, "a registration bypasses Register-CardzManagedTask"
    names = re.findall(r"Register-CardzManagedTask\s*`\s*\n\s*-Name \$(\w+)", code)
    registered = []
    for var in names:
        value = re.search(rf'(?m)^\${var}\s*=\s*"([^"]+)"', code)
        assert value is not None, f"${var} is not a literal task name"
        registered.append(value.group(1))
    assert registered == MANAGED, f"installer registers {registered}, expected exactly {MANAGED}"
    # One definition plus one call per managed task: a single-line call slips
    # past the -Name regex above, so it has to show up in this count instead.
    assert code.count("Register-CardzManagedTask") == 1 + len(MANAGED), (
        "a Register-CardzManagedTask call escapes the -Name check"
    )
    managed = re.search(r"(?m)^\$ManagedTasks\s*=\s*@\(([^)]*)\)", code)
    assert managed is not None and managed.group(1).replace(" ", "") == "$TaskName,$WatchdogTaskName", (
        f"$ManagedTasks drifted: {managed.group(1) if managed else None}"
    )


def check_kept(text: str, observer_text: str) -> None:
    """The daily task, the watchdog and the observer definitions are still there."""
    code = code_of(text)
    for needle in ('"cardz_daily_v2_launcher.ps1"', "-AllowPublish -Notify", 'Interval = "PT10M"', 'Duration = "PT6H"',
                   '"watchdog_live_release.ps1"', 'Interval = "PT15M"', 'Duration = "PT7H"',
                   "daily = [ordered]@{", "watchdog = [ordered]@{", "-Execute $WScriptExe"):
        assert needle in code, f"installer lost {needle}"
    observer = code_of(observer_text)
    assert '$TaskName = "CARDZ-V2-Run-Observer"' in observer, "observer task name gone"
    assert "watch --max-hours $MaxHours --notify" in observer, "observer no longer watches with --notify"
    assert PROMO_TASK not in observer and "Enable-ScheduledTask" not in observer, "observer installer touches promo"


def fires(text: str) -> str:
    try:
        check_installer(text)
    except AssertionError as exc:
        return str(exc)
    raise AssertionError("check_installer did NOT fire on a planted promo registration")


def main() -> int:
    text = INSTALLER.read_text(encoding="utf-8-sig")
    check_installer(text)
    check_kept(text, OBSERVER_INSTALLER.read_text(encoding="utf-8-sig"))
    print("NEGATIVE_OK installer manages only the daily task and the watchdog; observer kept")

    # The checker must fire on the shapes that re-enable promo, or it proves nothing.
    # The split name dodges the literal check so the other two assertions are exercised.
    anchor = '$plan["result"] = "applied"'
    assert text.count(anchor) == 1, "mutation anchor vanished; fix this test"
    register = "Register-CardzManagedTask `\n    -Name $P `\n    -Principal $principal\n"
    planted = [
        ("literal name", f'$P = "{PROMO_TASK}"\n{register}', PROMO_TASK),
        ("split-name registration", f'$P = "CARDZ-Promo" + "-After-Publish"\n{register}', "installer registers"),
        ("enable", 'Enable-ScheduledTask -TaskName ("CARDZ-Promo" + "-After-Publish") | Out-Null\n', "enables"),
        ("lower-case enable", 'enable-scheduledtask -TaskName ("CARDZ-Promo" + "-After-Publish") | Out-Null\n', "enables"),
        ("schtasks enable", 'schtasks.exe /Change /TN ("CARDZ-Promo" + "-After-Publish") /ENABLE | Out-Null\n',
         "outside Register-CardzManagedTask"),
        ("single-line split-name registration",
         '$Q = "CARDZ-Promo" + "-After-Publish"\nRegister-CardzManagedTask -Name $Q -Principal $principal\n',
         "escapes the -Name check"),
    ]
    for label, lines, expected in planted:
        msg = fires(text.replace(anchor, lines + anchor))
        assert expected in msg, f"planted {label} fired the wrong check: {msg}"
        print(f"POSITIVE_OK planted {label} fires: {msg}")
    print("OK v2 installer leaves the promo task alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
