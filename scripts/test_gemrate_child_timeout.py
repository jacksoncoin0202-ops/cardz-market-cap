#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GemRate child timeout must not scale with universe size.

2026-08-20: timeout=max(2*5400, 10*len(selected)) on 1604 IDs = 16040s,
longer than the 4h Task Scheduler limit. Morning died in GemRate; PC never ran.

Run: python -X utf8 scripts/test_gemrate_child_timeout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collect_control import (  # noqa: E402
    GEMRATE_CHILD_TIMEOUT_MAX_SECONDS,
    gemrate_child_timeout_seconds,
)

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    one = gemrate_child_timeout_seconds(1)
    many = gemrate_child_timeout_seconds(1604)
    huge = gemrate_child_timeout_seconds(10000)
    check("1 id and 1604 ids same timeout", one, many)
    check("10000 ids still same timeout", huge, many)
    check("timeout <= 4h cap", many <= GEMRATE_CHILD_TIMEOUT_MAX_SECONDS, True)
    check("timeout is 2*5400 website headroom", many, 10800)
    check("old 10*1604 formula is gone", many != 16040, True)

    source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
    check(
        "old timeout=max(2 * 5400, 10 * len(selected)) formula gone",
        "timeout=max(2 * 5400, 10 * len(selected))" in source,
        False,
    )
    check(
        "run_gemrate_pop calls gemrate_child_timeout_seconds",
        "gemrate_child_timeout_seconds(len(selected))" in source,
        True,
    )

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
