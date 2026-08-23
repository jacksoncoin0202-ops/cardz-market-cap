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
    # 3. bind ids never ride the sold refresh (2026-08-23 A01/A02: 57 leftover
    #    MAP rows of rejected / manual_review identities were re-fetched on
    #    every child run), and a bind-only run is complete with an empty batch.
    rows = [{"variant_id": 1, "pid": "a"}, {"variant_id": 2, "pid": "b"}, {"variant_id": 3, "pid": "c"}]
    vids = lambda picked: [int(row["variant_id"]) for row in picked]  # noqa: E731
    check("exact ids are refreshed", vids(mod.select_refresh_rows(rows, {1, 3})), [1, 3])
    check("bind-only ids with a leftover MAP row are NOT refreshed", vids(mod.select_refresh_rows(rows, set())), [])
    check("an id that is exact and bind is refreshed once", vids(mod.select_refresh_rows(rows, {2})), [2])
    base_done = dict(ok=0, fail=0, cf=0, rate_limited=0, session_error=None, results=[],
                     missing_requested=[], ingest=None)
    check("bind-only run with nothing to fetch is complete",
          mod.sweep_complete(batch=[], exact_requested=set(), bind_ran=True, selected_ids=set(), **base_done), True)
    check("an empty batch without a bind step is not complete",
          mod.sweep_complete(batch=[], exact_requested=set(), bind_ran=False, selected_ids=set(), **base_done), False)
    check("exact ids requested but none served is not complete",
          mod.sweep_complete(batch=[], exact_requested={1}, bind_ran=True, selected_ids=set(),
                             **{**base_done, "missing_requested": [1]}), False)
    served = dict(ok=2, fail=0, cf=0, rate_limited=0, session_error=None, results=[{}, {}],
                  missing_requested=[], ingest=None)
    check("every exact page served is complete",
          mod.sweep_complete(batch=[{}, {}], exact_requested={1, 2}, bind_ran=True, selected_ids={1, 2}, **served), True)
    check("one failed page is not complete",
          mod.sweep_complete(batch=[{}, {}], exact_requested={1, 2}, bind_ran=True, selected_ids={1, 2},
                             **{**served, "ok": 1, "fail": 1}), False)

    # 4. the parent sends the bind list on a lane sweep only; a scoped repair
    #    (explicit variant ids) sends none.
    import collect_control as cc
    reg = [
        {"adapter": "bind_pc_or_ebay", "variantId": 7},
        {"adapter": "bind_pc_or_ebay", "variantId": 5},
        {"adapter": "pc_ebay_sales", "variantId": 9},
    ]
    check("lane sweep sends the bind list", cc.pc_bind_missing_ids(reg, ["pc_ebay_sales"], set()), [5, 7])
    check("scoped repair sends no bind list", cc.pc_bind_missing_ids(reg, ["pc_ebay_sales"], {9}), [])
    check("non-PC request sends no bind list", cc.pc_bind_missing_ids(reg, ["snk_price"], set()), [])

    if FAILED:
        print(f"FAILED {len(FAILED)}: {FAILED}")
        return 1
    print("PC_BIND_UNRESOLVED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
