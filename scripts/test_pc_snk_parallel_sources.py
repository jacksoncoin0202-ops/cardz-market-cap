#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SNK exact is not a substitute for PriceCharting.

2026-08-20: classify_needs skipped bind_pc_or_ebay whenever snkrdunk was set,
and pc_identity_discover.select_targets excluded any variant that already had
snkrdunk/snk_psa10 exact. Result: 477 active-universe cards never got a
pricecharting identity row, so 9333 only capped 1127/1604.

Run: python -X utf8 scripts/test_pc_snk_parallel_sources.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from collect_control import classify_needs  # noqa: E402

FAILED: list[str] = []
CHECKS = 0


def check(label: str, got, want) -> None:
    global CHECKS
    CHECKS += 1
    if got != want:
        FAILED.append(f"FAIL {label}\n  got  {got!r}\n  want {want!r}")


def _row(*, snk: str | None, pc: str | None, lang: str = "ja") -> dict:
    return {
        "variantId": 3,
        "lang": lang,
        "ids": {
            "snkrdunk": snk,
            "snkrdunkStrict": snk,
            "snkrdunkEn": None,
            "pricecharting": pc,
            "ebay": None,
            "gemrate": None,
        },
        "sales": {"snkAny": bool(snk), "ebayAny": False, "snkMax": None, "ebayMax": None},
        "prices": {
            "snkAny": bool(snk),
            "enAny": False,
            "enExplicitPc": False,
            "snkMax": None,
            "enMax": None,
        },
        "_popMax": None,
        "_snkSaleMax": None,
        "_ebaySaleMax": None,
        "_snkPriceMax": None,
        "_enPriceMax": None,
        "_enExplicitPcMax": None,
        "snkEnAccepted": None,
        "snkEnIdentityEvidenceSha256": None,
    }


def main() -> int:
    snk_only = {item["adapter"] for item in classify_needs(_row(snk="93331", pc=None), {})}
    check("SNK-only still queues PC bind", "bind_pc_or_ebay" in snk_only, True)
    check("SNK-only does not pretend it has a PC product to poll", "pc_ebay_sales" in snk_only, False)
    check("SNK-only still queues SNK trades", "snk_trades" in snk_only, True)

    both = {item["adapter"] for item in classify_needs(_row(snk="93331", pc="5834844"), {})}
    check("SNK+PC polls PC sales", "pc_ebay_sales" in both, True)
    check("SNK+PC does not re-bind PC", "bind_pc_or_ebay" in both, False)
    check("SNK+PC still queues SNK trades", "snk_trades" in both, True)

    collect_src = (ROOT / "pipelines" / "collect_control.py").read_text(encoding="utf-8")
    check(
        "PC quote run sends registry bind_pc_or_ebay ids to 9333",
        'row.get("adapter") == "bind_pc_or_ebay"' in collect_src
        and "bind_missing_ids = sorted" in collect_src,
        True,
    )
    classify_src = collect_src
    check(
        "classify_needs no longer skips PC bind when SNK exists",
        "and not ids.get(\"snkrdunk\")" in classify_src.split("def classify_needs", 1)[-1].split("def build_registry", 1)[0],
        False,
    )

    discover_src = (ROOT / "pipelines" / "pc_identity_discover.py").read_text(encoding="utf-8")
    check(
        "PC discover no longer treats SNK exact as already having a price source",
        "si.source_code IN ('snkrdunk', 'snk_psa10', 'pricecharting')" in discover_src,
        False,
    )
    check(
        "PC discover still excludes exact PriceCharting",
        "si.source_code = 'pricecharting'" in discover_src,
        True,
    )

    for line in FAILED:
        print(line)
    print(f"{CHECKS - len(FAILED)}/{CHECKS} checks passed")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
