#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the daily box stage re-pulls an SNK box that yesterday's stage pulled.

2026-09-25: the V2 box stage runs once a day, but sealed incr waited for the 36 h publish SLA
before pulling an SNK item again. Yesterday's item was ~24 h old, so it was skipped:
147 SNK items with data, 0 due at 35.4 h, and SNK boxes refreshed every other day.
  - incr pulls an SNK item with data once it is older than REFRESH_DUE_HOURS (36 - 24 = 12 h),
    so a once-a-day stage always finds yesterday's item due, and one more day can never carry
    it past the 36 h SLA.
  - an item pulled a few hours ago (a second run the same day) is not pulled again.
  - stock still pulls only items without data; sealed_pc keeps its no-cooldown rule.
No MySQL, no network: the loaders are faked.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import sealed_collect as sc  # noqa: E402
import sealed_runtime as sr  # noqa: E402

SNK = {"sealedId": 35, "externalId": "apparels:864395", "itemId": 864395, "sku": "optcg:en:OP-17:booster-box:std"}
PC = {"sealedId": 69, "externalId": "pokemon-a/booster-box", "url": "https://www.pricecharting.com/game/pokemon-a/booster-box",
      "sku": "ptcg:en:A:booster-box:std"}
STATE: dict = {"items": [], "have": set(), "age": None}

sc._load_adapter_items = lambda cur, adapter, allow_candidates: [dict(i) for i in STATE["items"]]
sc._items_with_data = lambda cur, adapter: {sc._data_key(adapter, i["sealedId"], i["externalId"])
                                            for i in STATE["items"] if i["sealedId"] in STATE["have"]}
sc.load_sealed_checkpoints = lambda cur, adapter: {}
sc.checkpoint_age_hours = lambda checkpoints, key: STATE["age"]


def due(adapter, mode, item, *, has_data, age):
    STATE["items"], STATE["have"], STATE["age"] = [item], ({item["sealedId"]} if has_data else set()), age
    selected = sc._select(None, adapter, mode, limit=None, allow_candidates=False, force=False)[0]
    return [i["sku"] for i in selected] == [item["sku"]]


def main() -> int:
    assert sr.REFRESH_DUE_HOURS + sr.LANE_INTERVAL_HOURS == sr.SLA_HOURS, (sr.REFRESH_DUE_HOURS, sr.SLA_HOURS)
    assert sr.REFRESH_DUE_HOURS < sr.LANE_INTERVAL_HOURS, sr.REFRESH_DUE_HOURS
    cases = [
        # (adapter, mode, has_data, age, expected, why)
        ("sealed_snk", "incr", True, 23.5, True, "yesterday's stage, one day later"),
        ("sealed_snk", "incr", True, 35.35, True, "the 2026-09-25 box stage"),
        ("sealed_snk", "incr", True, 17.0, True, "an evening run, then the next day's stage"),
        ("sealed_snk", "incr", True, 6.0, False, "a second run the same day"),
        ("sealed_snk", "incr", True, None, True, "no checkpoint"),
        ("sealed_snk", "incr", False, 48.0, False, "incr never pulls an item without data"),
        ("sealed_snk", "stock", True, 48.0, False, "stock pulls only items without data"),
        ("sealed_snk", "stock", False, None, True, "stock pulls an item without data"),
        ("sealed_pc", "incr", True, 1.0, True, "sealed_pc has no cooldown"),
    ]
    for adapter, mode, has_data, age, expected, why in cases:
        item = SNK if adapter == "sealed_snk" else PC
        got = due(adapter, mode, item, has_data=has_data, age=age)
        assert got is expected, f"{adapter} {mode} has_data={has_data} age={age}: due={got}, want {expected} ({why})"
        print(f"OK   {adapter} {mode} has_data={has_data} age={age} due={got} ({why})")
    print(f"PASS sealed incr refresh due ({len(cases)} cases, REFRESH_DUE_HOURS={sr.REFRESH_DUE_HOURS:g})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
