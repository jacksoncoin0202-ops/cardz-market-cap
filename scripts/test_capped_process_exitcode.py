#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wrapper so run_all_tests.py glob picks up the PS 5.1 ExitCode ratchet."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "test_capped_process_exitcode.ps1"


def main() -> int:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PS1),
    ]
    proc = subprocess.run(cmd, cwd=str(ROOT))
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
