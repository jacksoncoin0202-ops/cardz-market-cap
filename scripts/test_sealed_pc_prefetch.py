#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove sealed_pc fetches box pages on the card sweep's 2-tab 9333 pool.

daddy 2026-09-25: boxes run 2 tabs like cards. Before this, run_pc fetched one page at a time through
cf.cmd_fetch.
  - only pages without a fresh VGPC copy go to the pool, and they go in one batch on PC_TABS (2) tabs
  - the pool's stall watchdog is never armed: armed, it os._exit()s the box pull and writes the card
    PC report path
  - a page the pool landed is parsed without cf.cmd_fetch; one it missed falls back to cf.cmd_fetch
  - after a Cloudflare storm no page falls back, and a pool that raises still leaves the fallback
No MySQL, no network, no browser: the pool, the fetcher, the parser and the run receipt are faked.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import pc_cdp_sold_refresh_win as pool  # noqa: E402
import sealed_collect as sc  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sealed-pc-prefetch-"))
sc.HTML_DIR = TMP
PAGE = "<html>" + "x" * 6000 + " VGPC.chart_data = {} </html>"
ITEMS = [
    {"sealedId": 1, "sku": "a", "url": "https://www.pricecharting.com/game/a", "externalId": "a"},
    {"sealedId": 2, "sku": "b", "url": "https://www.pricecharting.com/game/b", "externalId": "b"},
    {"sealedId": 3, "sku": "c", "url": "https://www.pricecharting.com/game/c", "externalId": "c"},
]
FETCHED: list[str] = []
POOL_CALLS: list[dict] = []


class FakeConn:
    def cursor(self):
        return self

    def commit(self):
        pass

    def rollback(self):
        pass


def fake_cmd_fetch(url, out, headless=True, timeout_s=90):
    FETCHED.append(url)
    Path(out).write_text(PAGE, encoding="utf-8")
    return 0


sys.modules["pricecharting_cf_session"] = types.SimpleNamespace(cmd_fetch=fake_cmd_fetch)
sys.modules["pricecharting_page_parse"] = types.SimpleNamespace(parse_product_html=lambda html, source_url: {"ok": False})
sc.record_sealed_run = lambda conn, **kwargs: {"runKey": "test"}


def refuse_arm(state):
    raise AssertionError("the box pull armed the card PC stall watchdog")


pool.arm_stall_watchdog = refuse_arm


def fake_pool(land: set[str], *, storm: bool = False, raises: bool = False):
    async def run_fetch_pool(pending, *, cdp_port, tabs, **kwargs):
        POOL_CALLS.append({"urls": [row["pc_url"] for row in pending], "port": cdp_port, "tabs": tabs, "rows": pending})
        if raises:
            raise RuntimeError("cdp gone")
        ok = 0
        for row in pending:
            assert row["looseHtml"] is True and Path(row["html_path"]).is_absolute(), row
            if row["pc_url"] in land:
                Path(row["html_path"]).write_text(PAGE, encoding="utf-8")
                ok += 1
        return {"ok": ok, "fail": len(pending) - ok, "cf": 0, "rateLimited": 0, "sessionError": None, "cfStorm": storm}
    pool.run_fetch_pool = run_fetch_pool


def reset():
    for path in TMP.iterdir():
        path.unlink()
    FETCHED.clear()
    POOL_CALLS.clear()


def statuses(report):
    return {row["sku"]: (row["status"], row.get("reason")) for row in report["items"]}


def run():
    return sc.run_pc(FakeConn(), ITEMS, mode="incr", html_max_age_h=20, timeout_s=5)


try:
    # a is cached fresh; b the pool lands; c the pool misses -> only c goes to cf.cmd_fetch
    reset()
    sc._pc_html_path(ITEMS[0]).write_text(PAGE, encoding="utf-8")
    fake_pool({ITEMS[1]["url"]})
    report = run()
    assert [call["urls"] for call in POOL_CALLS] == [[ITEMS[1]["url"], ITEMS[2]["url"]]], POOL_CALLS
    assert POOL_CALLS[0]["tabs"] == 2 == pool.PC_TABS and POOL_CALLS[0]["port"] == 9333, POOL_CALLS
    assert FETCHED == [ITEMS[2]["url"]], FETCHED
    assert report["prefetch"]["ok"] == 1 and report["prefetch"]["attempted"] == 2, report["prefetch"]
    print("POSITIVE_OK stale box pages go to the 2-tab 9333 pool in one batch; only the page it missed falls back")

    # a stale cached page that is a CF challenge is not fresh
    reset()
    sc._pc_html_path(ITEMS[0]).write_text("<html>Just a moment...</html>", encoding="utf-8")
    fake_pool(set())
    run()
    assert POOL_CALLS[0]["urls"] == [item["url"] for item in ITEMS], POOL_CALLS
    old = sc._pc_html_path(ITEMS[1])
    reset()
    old.write_text(PAGE, encoding="utf-8")
    os.utime(old, (time.time() - 21 * 3600, time.time() - 21 * 3600))
    fake_pool({item["url"] for item in ITEMS})
    run()
    assert POOL_CALLS[0]["urls"] == [item["url"] for item in ITEMS] and FETCHED == [], (POOL_CALLS, FETCHED)
    print("NEGATIVE_OK a CF challenge or a 21 h old copy is refetched; a pool that lands every page leaves no fallback")

    # Cloudflare storm: nothing falls back
    reset()
    fake_pool(set(), storm=True)
    report = run()
    assert FETCHED == [], FETCHED
    assert set(statuses(report).values()) == {("fetch_failed", "cf_storm")}, statuses(report)
    print("NEGATIVE_OK after a Cloudflare storm no page falls back to a third tab")

    # pool raises: every page still gets the one-page fetch
    reset()
    fake_pool(set(), raises=True)
    report = run()
    assert FETCHED == [item["url"] for item in ITEMS], FETCHED
    assert "cdp gone" in report["prefetch"]["sessionError"], report["prefetch"]
    print("POSITIVE_OK a pool that raises leaves every page to the one-page fallback")

    # all fresh: the pool is not opened at all
    reset()
    for item in ITEMS:
        sc._pc_html_path(item).write_text(PAGE, encoding="utf-8")
    fake_pool(set())
    report = run()
    assert POOL_CALLS == [] and FETCHED == [] and report["prefetch"] == {"attempted": 0}, (POOL_CALLS, FETCHED)
    print("NEGATIVE_OK with every page fresh the pool never opens 9333")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL_OK sealed_pc 2-tab prefetch")
