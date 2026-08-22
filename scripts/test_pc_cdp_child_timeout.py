#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PC CDP child timeout must not scale with universe size.

2026-08-22: timeout=max(600, 45*len(selected)) on 1028 IDs = 46260s.
A wedged 9333 tab stayed RUNNING for hours; V2 heartbeated every 10s.

Run: python -X utf8 scripts/test_pc_cdp_child_timeout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collect_control import (  # noqa: E402
    PC_CDP_CHILD_TIMEOUT_MAX_SECONDS,
    pc_cdp_child_timeout_seconds,
)

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def main() -> int:
    one = pc_cdp_child_timeout_seconds(1)
    many = pc_cdp_child_timeout_seconds(1028)
    huge = pc_cdp_child_timeout_seconds(10000)
    check("1 id and 1028 ids same timeout", one, many)
    check("10000 ids still same timeout", huge, many)
    check("timeout is the 90 minute cap", many, PC_CDP_CHILD_TIMEOUT_MAX_SECONDS)
    check("old 45*1028 formula is gone", many != 46260, True)
    check("timeout <= 90 minutes", many <= 90 * 60, True)

    source = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
    check(
        "old timeout=max(600, 45 * len(selected)) formula gone",
        "timeout=max(600, 45 * len(selected))" in source,
        False,
    )
    check(
        "refresh_pc_pages calls pc_cdp_child_timeout_seconds",
        "pc_cdp_child_timeout_seconds(len(selected))" in source,
        True,
    )
    planted = "timeout=max(600, 45 * len(selected))" in source
    check("planted 45*n formula is absent (would be red)", planted, False)

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
