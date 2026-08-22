#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bind-list variants that stay unresolved must NOT fail the PC sweep.

2026-08-22 real journal: PC task hit TERMINAL at attempts 13/12 while the child
had fetched 650/650 pages (cf=0, fail=0). The child merged the 576-card bind
list (variants with no PC id by construction) into ``requested_ids`` so the
unresolved ones became ``missing_requested`` -> exit 1 -> collect_control
``pc_cdp_refresh_failed`` -> both adapters fail-closed, every attempt.

Run: python -X utf8 scripts/test_pc_bind_unresolved.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import pc_cdp_sold_refresh_win as mod  # noqa: E402
import test_pc_lane_durability as lane  # noqa: E402

FAILED: list[str] = []


def check(label: str, got, want) -> None:
    ok = got == want
    print(("OK   " if ok else "FAIL ") + label + ("" if ok else f" got={got!r} want={want!r}"))
    if not ok:
        FAILED.append(label)


def main() -> int:
    # 1. pure split: exact ids drive completeness, bind ids only report
    missing, unresolved = mod.partition_requested({1, 2}, [3, 4], {1, 2, 3})
    check("exact ids all served -> no missing_requested", missing, [])
    check("bind id 4 unresolved is reported, not missing", unresolved, [4])
    missing, unresolved = mod.partition_requested({1, 2}, [3], {1, 3})
    check("exact id 2 unserved IS missing_requested", missing, [2])
    check("resolved bind id 3 is not unresolved", unresolved, [])
    missing, unresolved = mod.partition_requested({1}, [1, 9], {1})
    check("bind id that is also exact never double counts", unresolved, [9])

    # 2. collect_control accepts a report with unresolved bind ids and with
    #    batch > expected (bound-this-run rows ride along); fires on real gaps.
    (ROOT / "temp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="pc_bind_", dir=ROOT / "temp") as d:
        tmp = Path(d)
        base = {"fail": 0, "cf": 0, "results": [{"variant_id": 1, "status": "ok"}]}
        good = lane._refresh_with_stub(
            tmp,
            {"exit": 0, "childLogTail": ""},
            {**base, "batch": 3, "ok": 3, "missingRequestedVariantIds": [],
             "bindUnresolvedVariantIds": [404, 405]},
        )
        check("unresolved bind ids do not fail the sweep", good.get("ok"), True)
        check("unresolved bind ids are surfaced", good.get("bindUnresolvedVariantIds"), [404, 405])
        short = lane._refresh_with_stub(
            tmp,
            {"exit": 0, "childLogTail": ""},
            {**base, "batch": 0, "ok": 0, "missingRequestedVariantIds": [1]},
        )
        check("a real exact-id gap still fails", short.get("ok"), False)
        check("a real gap is reported as incomplete",
              "incomplete" in str(short.get("error") or ""), True)
        notok = lane._refresh_with_stub(
            tmp,
            {"exit": 0, "childLogTail": ""},
            {**base, "batch": 3, "ok": 2, "fail": 1, "missingRequestedVariantIds": []},
        )
        check("a page failure still fails even with batch >= expected", notok.get("ok"), False)
    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("PC_BIND_UNRESOLVED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
