#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prove the daily PC box pull writes PC's live price as a row dated the day the page was fetched, and that one rule
(sealed_price_compose.pc_live_status) judges every row carrying that price.

2026-09-25: PC stamps its live Ungraded price on the chart's current-month 1st, so run_pc only rewrote month-1st rows;
every PC-priced box kept the 09-23 console-scan price and asOf although 212/212 pages were pulled.
  - the chart's month points are still written, plus one row dated the page's mtime (not today) with the live price
  - a chart whose last point is not from the fetch month writes no live row
  - a live price MARKET_TRIM_RATIO or more off the item's newest ok point lands 'outlier_trimmed' on the dated row AND
    the month-1st row (QC 09-25: the month-1st row wrote CG's $1,289.64 'ok' beside the trimmed dated row), and a
    month rollover cannot release it
  - a quarantined month-1st row holds the live price: no dated row passes it (QC 09-25)
  - only the bound item's rows are judged against
Runs on sqlite with the same upsert head as MySQL, day after day. No MySQL, no network, no browser.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import types
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
sys.path.insert(0, str(ROOT / "scripts"))

import sealed_collect as sc  # noqa: E402
import sealed_price_compose as comp  # noqa: E402
from test_sealed_discover import LiteConn, LiteCursor  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="sealed-pc-live-row-"))
sc.HTML_DIR = TMP
PAGE = "<html>" + "x" * 6000 + " VGPC.chart_data = {} </html>"
ITEM = {"sealedId": 151, "sku": "ptcg-en-cg", "url": "https://www.pricecharting.com/game/cg", "externalId": "cg"}
SERIES: list = []
SCHEMA = """
CREATE TABLE operator_sealed_binding_freeze (sealed_id INTEGER, freeze_kind TEXT, source_code TEXT, external_entity_id TEXT,
  acceptance_status TEXT, PRIMARY KEY (sealed_id, freeze_kind, source_code));
CREATE TABLE market_sealed_price_observation (sealed_id INTEGER, source_code TEXT, price_kind TEXT, observed_date TEXT,
  native_price REAL, native_currency TEXT, price_usd REAL, external_entity_id TEXT, source_url TEXT, metric_status TEXT,
  ingest_run_key TEXT, PRIMARY KEY (sealed_id, source_code, price_kind, observed_date));
INSERT INTO operator_sealed_binding_freeze VALUES (151, 'source', 'pricecharting', 'cg', 'accepted');
"""


class UpsertCursor(LiteCursor):
    """MySQL's ON DUPLICATE KEY UPDATE on sqlite (as test_sealed_discover.test_price_upsert_keeps_a_quarantine)."""

    def execute(self, sql, params=()):
        sql = sql.replace("ON DUPLICATE KEY UPDATE", "ON CONFLICT(sealed_id, source_code, price_kind, observed_date) DO UPDATE SET")
        super().execute(re.sub(r"\bVALUES\((\w+)\)", r"excluded.\1", sql).replace("IF(", "iif("), params)

    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)


class DB(LiteConn):
    def cursor(self):
        return UpsertCursor(self.conn.cursor())

    def seed(self, day: str, usd: float, status: str = "ok", ext: str = "cg") -> None:
        self.cursor().execute(
            "INSERT INTO market_sealed_price_observation (sealed_id, source_code, price_kind, observed_date, price_usd, "
            "external_entity_id, metric_status) VALUES (151, 'pricecharting', 'market', %s, %s, %s, %s)", (day, usd, ext, status))

    def rows(self, ext: str = "cg") -> dict[str, tuple[float, str]]:
        cur = self.cursor()
        cur.execute("SELECT observed_date, price_usd, metric_status FROM market_sealed_price_observation "
                    "WHERE sealed_id=151 AND external_entity_id=%s", (ext,))
        return {r["observed_date"]: (r["price_usd"], r["metric_status"]) for r in cur.fetchall()}

    def shown(self, today: str) -> float | None:
        """What compose puts on /box for this EN box with no sales: the newest ok PC point within 45 days."""
        series = [(date.fromisoformat(d), v) for d, v in comp.load_market(self.cursor()).get((151, "pricecharting"), [])]
        cur = comp.compose_current(group_code="ptcg-en", kept_sales=[], market_by_source={"pricecharting": series},
                                   ask=None, today=date.fromisoformat(today), all_sales=[])
        return cur and cur["usd"]


def ms(day: str) -> int:
    return int(datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def chart(*points: tuple[str, float]) -> list:
    return [[ms(d), round(usd * 100)] for d, usd in points]


sys.modules["pricecharting_cf_session"] = types.SimpleNamespace(
    cmd_fetch=lambda *a, **k: (_ for _ in ()).throw(AssertionError("the cached page must be reused")))
sys.modules["pricecharting_page_parse"] = types.SimpleNamespace(
    parse_product_html=lambda html, source_url: {"ok": True, "chart": {"used": {"series": SERIES}}, "sales": {}, "product": {}})
sc.record_sealed_run = lambda conn, **kwargs: {"runKey": "test"}
sc.warehouse_sealed = lambda cur, **kwargs: None


def pull(db: DB, series: list, fetched: str) -> dict:
    SERIES[:] = series
    page = sc._pc_html_path(ITEM)
    page.write_text(PAGE, encoding="utf-8")
    stamp = datetime.strptime(fetched, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc).timestamp()
    os.utime(page, (stamp, stamp))
    # A cache window wide enough that a fixed past mtime still counts as fresh: the date under test is the mtime's.
    report = sc.run_pc(db, [ITEM], mode="incr", html_max_age_h=24 * 3650, timeout_s=5)
    assert report["ok"] == 1, report
    return report["items"][0]


CG_AUG, CG_CONSOLE, CG_LIVE = 27655.07, 27666.18, 1289.64
try:
    db = DB(SCHEMA)
    db.seed("2026-08-10", 1300.0)
    res = pull(db, chart(("2026-07-01", 1250.0), ("2026-08-01", CG_LIVE)), "2026-08-20 22:30")
    assert db.rows() == {"2026-07-01": (1250.0, "ok"), "2026-08-01": (CG_LIVE, "ok"), "2026-08-10": (1300.0, "ok"),
                         "2026-08-20": (CG_LIVE, "ok")}, db.rows()
    assert res["livePrice"] == "ok" and db.shown("2026-08-21") == CG_LIVE, (res, db.shown("2026-08-21"))
    print("POSITIVE_OK the month points stay and the live price is also written on the page's fetch day (08-20, not today)")

    # CG 2026-09-25: yesterday's month-1st and the 09-23 console scan read ~$27.6k; the page now says $1,289.64.
    db = DB(SCHEMA)
    db.seed("2026-09-01", CG_AUG)
    db.seed("2026-09-23", CG_CONSOLE)
    sep = chart(("2026-08-01", CG_AUG), ("2026-09-01", CG_LIVE))
    for day in ("2026-09-25", "2026-09-26"):
        res = pull(db, sep, f"{day} 04:23")
        rows = db.rows()
        assert rows["2026-09-01"] == (CG_LIVE, "outlier_trimmed") and rows[day] == (CG_LIVE, "outlier_trimmed"), rows
        assert res["livePrice"] == "outlier_trimmed" and db.shown(day) == CG_CONSOLE, (res, db.shown(day))
    print("NEGATIVE_OK a 5x move lands outlier_trimmed on the dated row and the month-1st row; /box keeps $27,666.18")

    res = pull(db, sep, "2026-10-01 04:23")  # PC has not rolled its chart to October yet: 09-01 is history now
    assert res["livePrice"] == "none" and db.rows()["2026-09-01"] == (CG_LIVE, "outlier_trimmed"), (res, db.rows())
    octo = chart(("2026-08-01", CG_AUG), ("2026-09-01", CG_LIVE), ("2026-10-01", CG_LIVE))
    for day in ("2026-10-02", "2026-10-03"):
        res = pull(db, octo, f"{day} 04:23")
        rows = db.rows()
        assert {rows[d][1] for d in ("2026-09-01", "2026-10-01", day)} == {"outlier_trimmed"}, rows
        assert db.shown(day) == CG_CONSOLE, db.shown(day)
    print("NEGATIVE_OK a month rollover moves the trimmed price into the chart's history and it stays trimmed")

    db = DB(SCHEMA)
    db.seed("2026-09-01", CG_LIVE, "quarantined")  # the operator's hold on PC's live price
    db.seed("2026-09-23", CG_CONSOLE)
    for day, usd in (("2026-09-25", CG_LIVE), ("2026-09-26", 1300.0)):
        res = pull(db, chart(("2026-08-01", CG_AUG), ("2026-09-01", usd)), f"{day} 04:23")
        rows = db.rows()
        assert day not in rows and rows["2026-09-01"] == (usd, "quarantined"), rows
        assert res["livePrice"] == "quarantined_month" and db.shown(day) == CG_CONSOLE, (res, db.shown(day))
    print("NEGATIVE_OK a quarantined month-1st row holds the live price: no dated row passes it")

    db = DB(SCHEMA)
    db.seed("2026-09-10", CG_CONSOLE)
    db.seed("2026-09-20", 1290.0, ext="cg-1st-edition")  # another item's row on this SKU
    pull(db, chart(("2026-09-01", CG_LIVE),), "2026-09-25 04:23")
    assert db.rows()["2026-09-25"] == (CG_LIVE, "outlier_trimmed"), db.rows()
    print("NEGATIVE_OK another item's row is never the point the live price is judged against")

    db = DB(SCHEMA)
    db.seed("2026-09-10", CG_LIVE * comp.MARKET_TRIM_RATIO * 0.99)
    pull(db, chart(("2026-09-01", CG_LIVE),), "2026-09-25 04:23")
    assert db.rows()["2026-09-25"][1] == "ok", db.rows()
    db = DB(SCHEMA)
    pull(db, chart(("2026-08-01", 1250.0), ("2026-09-01", CG_LIVE)), "2026-09-25 04:23")
    assert db.rows()["2026-09-25"] == (CG_LIVE, "ok"), db.rows()
    db = DB(SCHEMA)
    pull(db, sep, "2026-09-25 04:23")
    assert db.rows() == {"2026-08-01": (CG_AUG, "ok"), "2026-09-01": (CG_LIVE, "outlier_trimmed"),
                         "2026-09-25": (CG_LIVE, "outlier_trimmed")}, db.rows()
    print("POSITIVE_OK a move under 5x stays ok; a first pull judges the live price against the chart's own last month")

    db = DB(SCHEMA)
    res = pull(db, chart(("2026-09-01", CG_AUG), ("2026-10-01", CG_LIVE)), "2026-10-01 01:00")
    assert db.rows() == {"2026-09-01": (CG_AUG, "ok"), "2026-10-01": (CG_LIVE, "outlier_trimmed")}, db.rows()
    res = pull(db, chart(("2026-07-01", 1250.0), ("2026-08-01", 1289.0)), "2026-09-02 01:00")
    assert res["livePrice"] == "none" and "2026-09-02" not in db.rows(), (res, db.rows())
    print("NEGATIVE_OK fetched on the 1st writes one row; a chart whose last point is from an earlier month writes no live row")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("ALL_OK sealed_pc live row")
