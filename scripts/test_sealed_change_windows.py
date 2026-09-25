#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin the /box change windows, 1d to 365d (sealed_operator._windows_from_line), and the daily line they read
(sealed_price_compose.daily_line).

2026-09-25 box audit: the change ran from the newest line point, not the displayed price, to any older point, dated
from that newest point, not today; and the line took one day's sales median (a single sale) or else that day's market
point, so it flipped sold/market and SNK/PC day to day. Fixtures replay the audit's cases:
  - CG's $6,339.80 dip as a 7d anchor: +336.39%
  - xy2 "7d" +360.8%: two SNK sales 14 months apart, shown as a 7d move
  - jp-s5r 30d soldCount 13 counted back from the newest point; 1 counted back from today
  - sv1v: an SNK sold price measured against a PC market point (a lane switch)
2026-09-26 (coordinator, cards and boxes alike): a window is as-of. The anchor is the last same-lane point on or before
today-N, with no ±band; a window with no new point in it is 0%. The long windows moved here from the FE's
deriveBoxWindow, which anchored on any lane.
DADDY 2026-09-26 (boxes): a carried or expired anchor is withheld. op-17 JP's 7d / 30d -44.19% anchored on 08-20's SNK
sold median, carried through the 08-21..09-22 collection gap; FFI's 30d +90.88% on a PC point 421 days old.
Every rule is pinned twice: the shipped module passes its check, and a twin with that one rule planted back to the
bug FAILS the same check (AGENTS.md rule 9). No DB, no network.
"""
from __future__ import annotations

import re
import sys
import types
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import sealed_operator as OP  # noqa: E402
import sealed_price_compose as COMP  # noqa: E402

SOURCES = {
    "op": ROOT / "pipelines" / "sealed_operator.py",
    "comp": ROOT / "pipelines" / "sealed_price_compose.py",
}
TEXT = {key: path.read_text(encoding="utf-8") for key, path in SOURCES.items()}
CARD_RULE = ROOT / "apps" / "web" / "src" / "lib" / "live-db-snapshot.ts"
T = date(2026, 9, 26)


def ago(days: int) -> date:
    return T - timedelta(days=days)


def twin(which: str, fixed: str, buggy: str) -> types.ModuleType:
    """The module with exactly one anchor replaced by its bug."""
    source = TEXT[which]
    assert source.count(fixed) == 1, f"plant anchor must be unique in {which}: {fixed!r}"
    module = types.ModuleType(f"{which}_twin")
    module.__file__ = str(SOURCES[which])
    exec(compile(source.replace(fixed, buggy), str(SOURCES[which]), "exec"), module.__dict__)
    return module


PLANTS: list[tuple[str, Callable[..., None], str, str, str]] = []


def rule(name: str, check: Callable[..., None], plants: list[tuple[str, str, str]]) -> None:
    check(COMP, OP)
    for which, fixed, buggy in plants:
        PLANTS.append((name, check, which, fixed, buggy))
    print(f"POSITIVE_OK {name}")


_ids = iter(range(1, 10_000))


def sale(day: date, usd: float, source: str, qty: int = 1, status: str = "ok") -> dict:
    return {"id": next(_ids), "sold_at": datetime(day.year, day.month, day.day, 12), "unit_price_usd": usd,
            "source_code": source, "quantity": qty, "metric_status": status}


def box(comp, op, group: str, sales: list[dict], market: dict[str, list[tuple[date, float]]]) -> dict[str, Any]:
    """What export_sealed_subset does for one SKU, minus the DB: the line compose writes, the displayed price, windows."""
    line = comp.daily_line(group_code=group, sales=sales, market_by_source=market)
    kept = [s for s in sales if s["metric_status"] == "ok" and s["sold_at"].date() >= T - timedelta(days=comp.SOLD_WINDOW_D)]
    current = comp.compose_current(group_code=group, kept_sales=kept, market_by_source=market, ask=None, today=T,
                                   all_sales=sales)
    windows, _history, _latest = op._windows_from_line(line, T, current)
    return {"line": line, "current": current, "windows": windows, "by_day": {r["observed_date"]: r for r in line}}


def change(result: dict, label: str) -> float | None:
    return result["windows"].get(label, {}).get("changePct")


# ------------------------------------------------------------------ fixtures

CG = ("ptcg-en", [], {"pricecharting": [(date(2026, 8, 1), 27655.0), (date(2026, 8, 14), 6339.80),
                                         (date(2026, 9, 23), 27666.18)]})
CG_DIP = ("ptcg-en", [], {"pricecharting": [(date(2026, 9, 1), 27655.0), (date(2026, 9, 19), 6339.80),
                                             (date(2026, 9, 25), 27666.18)]})
XY2 = ("ptcg-jp", [sale(date(2025, 2, 9), 150.0, "snkrdunk"), sale(date(2026, 4, 23), 691.2, "snkrdunk")], {})
STALE = ("ptcg-jp", [sale(ago(34), 105.0, "snkrdunk"), sale(ago(33), 100.0, "snkrdunk")], {})
S5R = ("ptcg-jp",
       [sale(ago(d), 200.0, "snkrdunk") for d in range(44, 32, -1)] + [sale(ago(25), 200.0, "snkrdunk")],
       {"snkrdunk": [(ago(d), 200.0) for d in range(60, 19, -1)]})
EDGES = ("ptcg-en", [sale(ago(30), 100.0, "ebay"), sale(ago(7), 100.0, "ebay", qty=2), sale(T, 100.0, "ebay")], {})
SV1V = ("ptcg-jp", [sale(ago(3), 59.0, "snkrdunk"), sale(ago(2), 60.44, "snkrdunk")],
        {"pricecharting": [(ago(d), 94.25) for d in range(14, 0, -1)]})
SNK_PC = ("ptcg-jp", [], {"snkrdunk": [(ago(10), 100.0)], "pricecharting": [(ago(d), 150.0) for d in range(12, 0, -1)]})
NOT_LATEST = ("ptcg-en", [sale(date(2026, 8, 24), 100.0, "ebay"), sale(date(2026, 9, 1), 200.0, "ebay"),
                          sale(date(2026, 9, 20), 210.0, "ebay")], {})
TODAY_ROW = ("ptcg-en", [], {"pricecharting": [(ago(3), 100.0), (T, 110.0)]})
STEP = ("ptcg-en", [], {"pricecharting": [(date(2026, 8, 10), 100.0), (ago(2), 110.0), (ago(1), 110.0)]})
STEP_OTHER_LANE = ("ptcg-en", [sale(date(2026, 8, 20), 105.0, "ebay")], STEP[2])
STEP_STALE = ("ptcg-en", [], {"pricecharting": [(date(2026, 7, 1), 100.0), (ago(2), 110.0), (ago(1), 110.0)]})
STEP_SOLD = ("ptcg-en", [sale(date(2026, 7, 20), 100.0, "ebay"), sale(date(2026, 7, 30), 300.0, "ebay"),
                         sale(date(2026, 9, 20), 330.0, "ebay")], {})
# A band (±5 d of today-30) would take ago(28), after today-30; as-of takes ago(40).
AS_OF = ("ptcg-en", [], {"pricecharting": [(ago(40), 100.0), (ago(28), 120.0), (ago(1), 130.0)]})
NO_NEW_POINT = ("ptcg-en", [], {"pricecharting": [(ago(37), 100.0)]})
LONG = ("ptcg-en", [], {"pricecharting": [(ago(200), 100.0), (ago(95), 100.0), (ago(1), 450.0)]})
# op-17 JP: 08-20's sales carried through an SNK collection gap (SNK's market line kept running), then new sales.
OP17 = ("optcg-jp", [sale(ago(37), 136.6, "snkrdunk") for _ in range(3)]
        + [sale(ago(3), 76.0, "snkrdunk"), sale(ago(1), 76.48, "snkrdunk")],
        {"snkrdunk": [(ago(d), 100.0) for d in range(36, 0, -1)]})
FRESH = ("ptcg-jp", [sale(ago(31), 100.0, "snkrdunk"), sale(ago(30), 100.0, "snkrdunk"), sale(ago(7), 110.0, "snkrdunk"),
                     sale(ago(1), 120.0, "snkrdunk"), sale(T, 120.0, "snkrdunk")], {})
PC_MONTHLY = ("ptcg-en", [], {"pricecharting": [(date(2026, 6, 1), 100.0), (date(2026, 7, 1), 105.0),
                                                 (date(2026, 8, 1), 110.0), (date(2026, 9, 1), 120.0), (ago(1), 125.0)]})
# FFI: eBay sales put 08-01 / 08-14 on the sold lane, so the market lane's last point before today-30 is 2025-07-01.
FFI = ("ptcg-en", [sale(date(2026, 7, 16), 3800.0, "ebay"), sale(date(2026, 7, 26), 4500.0, "ebay"),
                   sale(date(2026, 7, 31), 4119.0, "ebay")],
       {"pricecharting": [(date(2025, 7, 1), 2203.59), (date(2026, 8, 1), 4168.17), (date(2026, 8, 14), 4169.80),
                          (date(2026, 9, 1), 4206.17), (ago(1), 4206.17)]})
# DEX (EN): PC's chart ran 3-4x under the eBay sales from March to May, then caught up by July. Its March point is the
# market lane's last point on or before today-180, but /box showed the $20,101 sale of 03-08 on that day.
DEX = ("ptcg-en", [sale(date(2025, 5, 15), 6999.95, "ebay"), sale(date(2026, 3, 8), 20101.0, "ebay"),
                   sale(date(2026, 4, 12), 7500.0, "ebay"), sale(date(2026, 4, 24), 17500.0, "ebay"),
                   sale(date(2026, 5, 15), 21194.28, "ebay")],
       {"pricecharting": [(date(2025, 7, 1), 5411.91), (date(2025, 8, 1), 5926.04), (date(2025, 9, 1), 6010.55),
                          (date(2025, 10, 1), 6010.55), (date(2025, 11, 1), 6010.55), (date(2025, 12, 1), 5411.91),
                          (date(2026, 1, 1), 5411.91), (date(2026, 2, 1), 5411.91), (date(2026, 3, 1), 5000.0),
                          (date(2026, 4, 1), 6372.73), (date(2026, 5, 1), 7591.74), (date(2026, 6, 1), 12500.0),
                          (date(2026, 7, 1), 16573.82), (date(2026, 8, 1), 16573.82), (date(2026, 9, 1), 16573.82),
                          (ago(1), 16573.82)]})
# A sale after today-90 but more than SOLD_WINDOW_D before today: /box showed PC's point on today-90.
LATER_SALE = ("ptcg-en", [sale(ago(50), 105.0, "ebay")], {"pricecharting": [(ago(100), 100.0), (ago(1), 110.0)]})
ALL = {"CG": CG, "CG_DIP": CG_DIP, "XY2": XY2, "STALE": STALE, "S5R": S5R, "EDGES": EDGES, "SV1V": SV1V,
       "SNK_PC": SNK_PC, "NOT_LATEST": NOT_LATEST, "TODAY_ROW": TODAY_ROW, "STEP": STEP,
       "STEP_OTHER_LANE": STEP_OTHER_LANE, "STEP_STALE": STEP_STALE, "STEP_SOLD": STEP_SOLD, "AS_OF": AS_OF,
       "NO_NEW_POINT": NO_NEW_POINT, "LONG": LONG, "OP17": OP17, "FRESH": FRESH, "PC_MONTHLY": PC_MONTHLY, "FFI": FFI,
       "DEX": DEX, "LATER_SALE": LATER_SALE}


# ------------------------------------------------------------------ windows (sealed_operator)

def check_as_of(comp, op) -> None:
    r = box(comp, op, *AS_OF)
    assert (r["current"]["kind"], r["current"]["usd"]) == ("market", 130.0), r["current"]
    got = {label: change(r, label) for label in ("1d", "7d", "30d")}
    assert got == {"1d": 0.0, "7d": 8.33, "30d": 30.0}, \
        f"30d: the price shown on today-30 is ago(40)'s $100, not ago(28)'s $120 two days after it: {got}"
    r = box(comp, op, *NO_NEW_POINT)
    got = {label: change(r, label) for label in ("1d", "7d", "30d", "90d")}
    assert got == {"1d": 0.0, "7d": 0.0, "30d": 0.0, "90d": None}, \
        f"no new point in a window is 0%, by definition; no point on or before today-90 is not ready: {got}"
    r = box(comp, op, *CG)
    assert change(r, "1d") == 0.0, f"1d: 09-23's point is the price shown on 09-25: {r['windows']}"
    # The retired 30d step fallback's limits (market lane only, no other lane between, MARKET_MAX_AGE_D) are gone with
    # it: same-lane as-of is the whole rule.
    assert (change(box(comp, op, *STEP), "7d"), change(box(comp, op, *STEP), "30d")) == (10.0, 10.0), "STEP"
    # DADDY 2026-09-26: a market anchor the display had dropped by today-30 (07-01, 57 days > MARKET_MAX_AGE_D) is withheld.
    assert change(box(comp, op, *STEP_STALE), "30d") is None, "STEP_STALE: an expired market anchor is withheld"


rule("as-of: the anchor is the lane's last point on or before today-N, no band", check_as_of, [
    # the ±band back (68b82c75): a point up to 5 days either side of today-N
    ("op", 'or r["observed_date"] > target:', 'or abs((r["observed_date"] - target).days) > 5:'),
    # the oldest point before today-N, not the last one
    ("op", 'if best is None or r["observed_date"] > best["observed_date"]:',
     'if best is None or r["observed_date"] < best["observed_date"]:'),
])


def check_ratio(comp, op) -> None:
    r = box(comp, op, *CG_DIP)
    assert change(r, "1d") == 0.0, r["windows"]
    assert change(r, "7d") is None, f"a 4.36x move off CG's $6,339.80 dip is withheld, not +336.39%: {r['windows']}"
    assert change(r, "30d") is None, f"nothing on or before 08-27 (09-01 comes after it): {r['windows']}"
    r = box(comp, op, *CG)
    assert change(r, "7d") is None and change(r, "30d") is None, f"CG 7d / 30d anchor on the 08-14 dip: {r['windows']}"
    r = box(comp, op, *LONG)
    got = {label: change(r, label) for label in ("90d", "180d", "365d")}
    assert got == {"90d": None, "180d": 350.0, "365d": None}, \
        f"4.5x: past 90d's 4x, inside 180d's 5x; nothing on or before today-365: {got}"


rule("a move past MAX_WINDOW_RATIO is withheld (1d / 7d / 30d 3x, 90d 4x, 180d 5x, 365d 7x)", check_ratio, [
    ("op", 'MAX_WINDOW_RATIO = {"1d": 3.0, "7d": 3.0, "30d": 3.0, "90d": 4.0, "180d": 5.0, "365d": 7.0}',
     'MAX_WINDOW_RATIO = {"1d": 1e9, "7d": 1e9, "30d": 1e9, "90d": 1e9, "180d": 1e9, "365d": 1e9}'),
    ("op", 'MAX_WINDOW_RATIO = {"1d": 3.0, "7d": 3.0, "30d": 3.0, "90d": 4.0, "180d": 5.0, "365d": 7.0}',
     'MAX_WINDOW_RATIO = {"1d": 3.0, "7d": 3.0, "30d": 3.0, "90d": 5.0, "180d": 5.0, "365d": 7.0}'),
])


def check_carried(comp, op) -> None:
    r = box(comp, op, *OP17)
    assert (r["current"]["kind"], r["current"]["usd"]) == ("sold", 76.24), r["current"]
    assert r["by_day"][ago(7)]["composed_price_usd"] == 136.6, "the line carries ago(37)'s median to ago(7)"
    assert change(r, "1d") == 0.0, r["windows"]
    assert change(r, "7d") is None and change(r, "30d") is None, \
        f"op-17: 7d / 30d anchors carry sales 30 / 7 days older than themselves; withheld, not -44.19%: {r['windows']}"
    r = box(comp, op, *FRESH)
    got = {label: change(r, label) for label in ("7d", "30d")}
    assert got == {"7d": 15.0, "30d": 15.0}, f"a fresh anchor (a sale on its own day) keeps its number: {got}"


rule("a carried anchor (newest observation > max(3 d, N/10) before it) is withheld", check_carried, [
    ("op", "carried = (anchor_day - observed).days > max(ANCHOR_CARRY_FLOOR_D, days * ANCHOR_CARRY_FRACTION)",
     "carried = False"),
    ("op", "carried = (anchor_day - observed).days > max(ANCHOR_CARRY_FLOOR_D, days * ANCHOR_CARRY_FRACTION)",
     "carried = (anchor_day - observed).days >= 0"),
    ("op", "if anchor and _anchor_withheld(anchor, sale_days, start, days):", "if False:"),
])


def check_expired(comp, op) -> None:
    r = box(comp, op, *FFI)
    assert (r["current"]["kind"], r["current"]["usd"]) == ("market", 4206.17), r["current"]
    got = {label: change(r, label) for label in OP.WINDOW_DAYS}
    assert got == {"1d": 0.0, "7d": 0.0, "30d": None, "90d": None, "180d": None, "365d": None}, \
        f"FFI: a 2025-07-01 PC anchor is withheld, not +90.88%: {got}"
    r = box(comp, op, *PC_MONTHLY)
    got = {label: change(r, label) for label in ("7d", "30d", "90d")}
    assert got == {"7d": 4.17, "30d": 13.64, "90d": 25.0}, f"PC's month-1st points are their own observation: {got}"


rule("an expired anchor (past compose's own sold / market age) is withheld; PC month-1st points stay", check_expired, [
    ("op", "expired = (target - observed).days > expires", "expired = False"),
    ("op", "expired = (target - observed).days > expires",
     "expired = (target - observed).days > max(ANCHOR_CARRY_FLOOR_D, days * ANCHOR_CARRY_FRACTION)"),
    ("op", "if anchor and _anchor_withheld(anchor, sale_days, start, days):", "if False:"),
])


def check_superseded(comp, op) -> None:
    r = box(comp, op, *DEX)
    assert (r["current"]["kind"], r["current"]["usd"]) == ("market", 16573.82), r["current"]
    got = {label: change(r, label) for label in OP.WINDOW_DAYS}
    assert got == {"1d": 0.0, "7d": 0.0, "30d": 0.0, "90d": None, "180d": None, "365d": 175.75}, \
        f"DEX 180d: /box showed 03-08's $20,101 sale on today-180, not PC's $5,000 March point (+231.48%): {got}"
    assert change(box(comp, op, *STEP_OTHER_LANE), "30d") is None, \
        "STEP_OTHER_LANE: on today-30 /box showed 08-20's eBay sale, not PC's 08-10 point"
    assert change(box(comp, op, *LATER_SALE), "90d") == 10.0, \
        "LATER_SALE: a sale after today-90 does not unseat the PC point /box showed on today-90"


rule("a market anchor a sale put behind the sold median on today-N is withheld (DEX 180d)", check_superseded, [
    ("op", "        if idx and (target - sale_days[idx - 1]).days <= SOLD_WINDOW_D:\n            return True\n", ""),
    ("op", "        idx = bisect_right(sale_days, target)\n", "        idx = bisect_right(sale_days, anchor_day)\n"),
    ("op", "        idx = bisect_right(sale_days, target)\n", "        idx = len(sale_days)\n"),
])


def check_today_anchor(comp, op) -> None:
    r = box(comp, op, *XY2)
    assert r["current"].get("note") == "last_sold", r["current"]
    for label in OP.WINDOW_DAYS:
        assert change(r, label) is None, f"xy2 {label}: a stale last_sold display has no {label} move: {r['windows']}"
    for label in ("1d", "7d", "30d"):
        assert r["windows"][label]["soldCount"] == 0, f"xy2 sold nothing in the {label} before today: {r['windows']}"
    r = box(comp, op, *S5R)
    counts = {label: r["windows"][label]["soldCount"] for label in ("1d", "7d", "30d")}
    assert counts == {"1d": 0, "7d": 0, "30d": 1}, f"jp-s5r: 1 sale in (today-30, today], not 13: {counts}"


rule("xy2 / jp-s5r: windows and soldCount count back from today", check_today_anchor, [
    ("op", "        start = today - timedelta(days=days)\n",
     '        start = latest["observed_date"] - timedelta(days=days)\n'),
])


def check_edges(comp, op) -> None:
    r = box(comp, op, *EDGES)
    counts = {label: r["windows"][label]["soldCount"] for label in ("1d", "7d", "30d")}
    assert counts == {"1d": 1, "7d": 1, "30d": 3}, f"soldCount is units in (today-N, today]: {counts}"


rule("soldCount window is (today-N, today]", check_edges, [
    ("op", 'if start < r["observed_date"] <= today)', 'if start <= r["observed_date"] <= today)'),
])


def check_lane(comp, op) -> None:
    r = box(comp, op, *SV1V)
    assert (r["current"]["kind"], r["current"]["source"], r["current"]["usd"]) == ("sold", "snkrdunk", 59.72), r["current"]
    assert change(r, "1d") == 0.0, r["windows"]
    assert change(r, "7d") is None and change(r, "30d") is None, \
        f"sv1v: SNK sold $59.72 has no SNK-sold point on or before today-7; the PC $94.25 points are another lane: {r['windows']}"


rule("sv1v: the anchor is in the displayed price's lane", check_lane, [
    ("op", 'if (r["composed_kind"], r["composed_source"]) != lane or r["observed_date"] > target:',
     'if r["observed_date"] > target:'),
])


def check_stale_display(comp, op) -> None:
    r = box(comp, op, *STALE)
    assert r["current"].get("note") == "last_sold", r["current"]
    assert change(r, "30d") is None, f"a last_sold price (observed 33 days ago) has no 30d move: {r['windows']}"


rule("a stale last_sold / last_market display has no window", check_stale_display, [
    ("op", 'if price and not current.get("note") else None', "if price else None"),
])


def check_displayed_price(comp, op) -> None:
    r = box(comp, op, *NOT_LATEST)
    assert r["current"]["usd"] == 205.0, r["current"]
    assert r["by_day"][date(2026, 9, 20)]["composed_price_usd"] == 200.0, r["line"]
    # 7d as-of 09-19: the last point on or before it is 09-01's, the median of 08-24's $100 and 09-01's $200.
    assert change(r, "7d") == 36.67, f"the displayed $205 against 09-01's $150, not the newest point's $200: {r['windows']}"


rule("the change starts from the displayed price", check_displayed_price, [
    ("op", 'price = float(current["usd"]) if current and current.get("usd") is not None else None',
     'price = float(latest["composed_price_usd"]) if current and current.get("usd") is not None else None'),
])


def check_before_today(comp, op) -> None:
    r = box(comp, op, *TODAY_ROW)
    assert change(r, "1d") == 10.0, f"today's own point is never its anchor (card rule: strictly older): {r['windows']}"


rule("an anchor is strictly before today", check_before_today, [
    ("op", "anchor = _window_anchor(pts, lane, start) if lane else None",
     "anchor = _window_anchor(pts, lane, today) if lane else None"),
])


def check_export_call(comp, op) -> None:
    source = getattr(op, "__twin_source__", TEXT["op"])
    assert source.count("windows, history, latest = _windows_from_line(line, today, current)") == 1, \
        "export_sealed_subset must hand the displayed price to _windows_from_line"


# ------------------------------------------------------------------ the line (sealed_price_compose)

def reference_point(comp, group: str, sales: list[dict], market: dict, d: date) -> dict | None:
    """The price /box would have shown on d, the obvious way: compose_current on what was known on d, no ask."""
    jp = group.endswith("-jp")
    sources = comp.JP_SOLD_SOURCES if jp else comp.EN_SOLD_SOURCES
    kept = [s for s in sales if s["metric_status"] == "ok" and s["source_code"] in sources
            and d - timedelta(days=comp.SOLD_WINDOW_D) <= s["sold_at"].date() <= d]
    known = {source: [(x, v) for x, v in series if x <= d] for source, series in market.items()}
    return comp.compose_current(group_code=group, kept_sales=kept, market_by_source=known, ask=None, today=d, all_sales=[])


def check_line_is_display_as_of(comp, op) -> None:
    for name, (group, sales, market) in ALL.items():
        for row in comp.daily_line(group_code=group, sales=sales, market_by_source=market):
            want = reference_point(comp, group, sales, market, row["observed_date"])
            got = (row["composed_price_usd"], row["composed_kind"], row["composed_source"])
            assert got == ((want["usd"], want["kind"], want["source"]) if want else (None, None, None)), \
                f"{name} {row['observed_date']}: line {got} is not the price shown that day {want}"
    r = box(comp, op, *SV1V)
    assert [r["by_day"][ago(d)]["composed_kind"] for d in (3, 2, 1)] == ["sold"] * 3, \
        f"sv1v: after an SNK sale the line stays SNK sold for the window, it does not fall back to PC: {r['line']}"


rule("daily_line: a point is the display's rule as of that day", check_line_is_display_as_of, [
    ("comp", "window = sold[bisect_left(sold_days, d - timedelta(days=SOLD_WINDOW_D)):bisect_right(sold_days, d)]",
     "window = by_day.get(d, [])"),
    ("comp", "window = sold[bisect_left(sold_days, d - timedelta(days=SOLD_WINDOW_D)):bisect_right(sold_days, d)]",
     "window = sold[bisect_left(sold_days, d - timedelta(days=SOLD_WINDOW_D)):bisect_left(sold_days, d)]"),
    ("comp", "            if idx:\n                as_of[source]",
     "            if idx and days[idx - 1] == d:\n                as_of[source]"),
])


def check_no_snk_pc_flip(comp, op) -> None:
    r = box(comp, op, *SNK_PC)
    lanes = [(r["by_day"][ago(d)]["composed_kind"], r["by_day"][ago(d)]["composed_source"]) for d in range(9, 0, -1)]
    assert set(lanes) == {("market", "snkrdunk")}, f"JP: SNK market holds the line for 45 days, PC days do not flip it: {lanes}"
    assert change(r, "1d") == 0.0 and change(r, "7d") == 0.0, r["windows"]


rule("daily_line: JP market days do not flip SNK / PC", check_no_snk_pc_flip, [
    ("comp", "            if idx:\n                as_of[source]",
     "            if idx and days[idx - 1] == d:\n                as_of[source]"),
])


def check_single_sale_day(comp, op) -> None:
    sales = [sale(ago(d), 100.0, "ebay") for d in (20, 15, 12, 9)] + [sale(ago(8), 130.0, "ebay")]
    line = {r["observed_date"]: r for r in comp.daily_line(group_code="ptcg-en", sales=sales, market_by_source={})}
    assert line[ago(8)]["composed_price_usd"] == 100.0, f"one $130 sale is not the day's price; the 30d median is: {line[ago(8)]}"


rule("daily_line: one sale is not the day's price", check_single_sale_day, [
    ("comp", "window = sold[bisect_left(sold_days, d - timedelta(days=SOLD_WINDOW_D)):bisect_right(sold_days, d)]",
     "window = by_day.get(d, [])"),
])


# ------------------------------------------------------------------ the card rule, mirrored

def check_card_literal(comp, op) -> None:
    ts = CARD_RULE.read_text(encoding="utf-8")
    ratio = re.search(r"const MAX_WINDOW_RATIO[^=]*=\s*\{([^}]*)\}", ts)
    days = re.search(r"const WINDOWS = \{([^}]*)\}", ts)
    # every window's sale anchor: latestBefore(history, targetMs + 1, currentSource, ...), at or before today-N
    as_of = re.search(r"const saleAnchor = latestBefore\(history, targetMs \+ 1, currentSource[,)]", ts)
    assert ratio and days and as_of, \
        f"the card window rule moved in {CARD_RULE.name}; re-mirror sealed_operator's window rule"
    assert "const tolerance" not in ts and "nearestPrice(" not in ts, f"a ±band came back in {CARD_RULE.name}"
    card_ratio = {k: float(v) for k, v in re.findall(r'"(\w+)":\s*([\d.]+)', ratio.group(1))}
    card_days = {k: int(v) for k, v in re.findall(r'"(\w+)":\s*(\d+)', days.group(1))}
    assert card_ratio == op.MAX_WINDOW_RATIO, (card_ratio, op.MAX_WINDOW_RATIO)
    assert card_days == op.WINDOW_DAYS, (card_days, op.WINDOW_DAYS)


rule("the box window table equals the card rule's literals", check_card_literal, [
    ("op", 'MAX_WINDOW_RATIO = {"1d": 3.0, "7d": 3.0, "30d": 3.0, "90d": 4.0, "180d": 5.0, "365d": 7.0}',
     'MAX_WINDOW_RATIO = {"1d": 3.0, "7d": 3.0, "30d": 4.0, "90d": 4.0, "180d": 5.0, "365d": 7.0}'),
    ("op", 'WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365}',
     'WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}'),
])


rule("export hands the displayed price to the windows", check_export_call, [
    ("op", "windows, history, latest = _windows_from_line(line, today, current)",
     "windows, history, latest = _windows_from_line(line, today)"),
])


# ------------------------------------------------------------------ plants

fired = 0
for name, check, which, fixed, buggy in PLANTS:
    mutant = twin(which, fixed, buggy)
    mutant.__twin_source__ = TEXT[which].replace(fixed, buggy)
    args = (mutant, OP) if which == "comp" else (COMP, mutant)
    try:
        check(*args)
    except AssertionError:
        # Only the check's own verdict counts: a NameError from a broken twin would otherwise pass as "fired".
        fired += 1
        print(f"PLANTED_BUG_FIRED {name} :: {buggy.strip()[:70]!r}")
        continue
    raise AssertionError(f"planted bug did not fire, so this check proves nothing: {name} :: {buggy!r}")

print(f"ALL_OK sealed change windows: {len(PLANTS)} plants, {fired} fired")
