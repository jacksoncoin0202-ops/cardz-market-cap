#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fleet CDP: 9333 PC + 9444 social, never 9222, never prompt."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "ensure_cardz_cdp_fleet.ps1"


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


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _windows_path(PS1),
            *extra,
        ],
        cwd=str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32",
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


def main() -> int:
    failed = 0
    text = PS1.read_text(encoding="utf-8")
    if "9222" not in text:
        print("FAIL fleet-script-must-name-9222-to-refuse-it")
        failed += 1
    else:
        print("OK script-mentions-9222-as-refuse")
    if "9333" not in text or "9444" not in text:
        print("FAIL fleet-missing-ports")
        failed += 1
    else:
        print("OK script-has-9333-and-9444")
    result = _run("-SelfTest")
    out = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        print("FAIL selftest-rc\n", out)
        failed += 1
    for needle in (
        "SELFTEST_OK fleet-skips-9222",
        "SELFTEST_OK fleet-has-9333-pc",
        "SELFTEST_OK fleet-has-9444-social",
        "SELFTEST_PASSED",
    ):
        if needle not in out:
            print(f"FAIL missing {needle}\n{out}")
            failed += 1
        else:
            print(f"OK {needle}")
    # Plant: a fleet that accepted 9222 would fail the selftest assertion above.
    if "CDP_REFUSED port=9222" not in text and "never touches Codex 9222" not in text.lower() and "Never touches Codex 9222" not in text:
        print("FAIL missing hard 9222 refuse comment")
        failed += 1
    if failed:
        print(f"{failed} failed")
        return 1
    print("PASS fleet selftest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
