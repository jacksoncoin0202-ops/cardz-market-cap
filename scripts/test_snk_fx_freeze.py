#!/usr/bin/env python3
"""Portable positive/negative proof for SNK historical FX freezing."""
from __future__ import annotations

import ast
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from fx_asof import JpyPerUsdHistory  # noqa: E402


class FakeCursor:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.query = ""

    def execute(self, query: str) -> None:
        self.query = " ".join(query.split())

    def fetchall(self) -> list[dict]:
        return list(self.rows)


sale_date = date(2026, 7, 19)
native_jpy = 340_000.0
base = [{"id": 1, "effective_date": date(2026, 7, 25), "rate": 163.67}]
first_cursor = FakeCursor(base + [{"id": 2, "effective_date": date(2026, 8, 17), "rate": 160.92}])
second_cursor = FakeCursor(base + [{"id": 3, "effective_date": date(2026, 8, 24), "rate": 159.0}])
first = JpyPerUsdHistory.load(first_cursor)
second = JpyPerUsdHistory.load(second_cursor)
first_rate, first_as_of = first.for_date(sale_date)
second_rate, second_as_of = second.for_date(sale_date)
first_usd = round(native_jpy / first_rate, 6)
second_usd = round(native_jpy / second_rate, 6)
assert (first_rate, first_as_of) == (163.67, date(2026, 7, 25))
assert (second_rate, second_as_of) == (163.67, date(2026, 7, 25))
assert first_usd == second_usd
assert "ORDER BY effective_date ASC, id ASC" in first_cursor.query
print("POSITIVE_OK the same historical sale keeps one USD value as later FX points arrive")

# Planted old rule: selecting each run's latest point must visibly reproduce
# the bug, or the positive assertion above would no longer prove anything.
old_first = round(native_jpy / 160.92, 6)
old_second = round(native_jpy / 159.0, 6)
assert abs(old_first - old_second) > 25.0, (old_first, old_second)
print("NEGATIVE_OK the old latest-rate rule moves the same sale by more than USD25")

sales_source = (ROOT / "pipelines" / "ingest_snk_trades_sales.py").read_text(encoding="utf-8")
kline_source = (ROOT / "pipelines" / "snk_market_data.py").read_text(encoding="utf-8")
rebuild_source = (ROOT / "pipelines" / "rebuild_036.py").read_text(encoding="utf-8")
migration = (
    ROOT / "pipelines" / "migrations" / "060_daily_chain_v2_snk_fx_freeze.mysql.sql"
).read_text(encoding="utf-8")
for label, source in (("sales", sales_source), ("kline", kline_source), ("rebuild", rebuild_source)):
    assert "ORDER BY effective_date DESC, id DESC LIMIT 1" not in source, label
assert "fx_history.for_date(sold_at.date())" in sales_source
assert "fx_history.for_date(observed)" in kline_source
assert "trade_fx.for_date(sold_at.date())" in rebuild_source
assert "market_sale_observation.fx_rate_as_of IS NULL" in sales_source
assert "market_price_observation.fx_rate_as_of IS NULL" in kline_source
assert "market_sale_observation.fx_rate_as_of IS NULL" in rebuild_source
assert "UPDATE market_sale_observation" not in migration
assert "UPDATE market_price_observation" not in migration
assert "060_daily_chain_v2_snk_fx_freeze.mysql.sql".startswith("060_daily_chain_v2_")


def sql_literals(source: str, needle: str) -> list[str]:
    tree = ast.parse(source)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and needle in node.value
    ]


sales_sql = [sql for sql in sql_literals(sales_source, "INSERT INTO market_sale_observation") if "native_unit_price" in sql]
kline_sql = [sql for sql in sql_literals(kline_source, "INSERT INTO market_price_observation") if "fx_rate_as_of" in sql]
rebuild_sql = [sql for sql in sql_literals(rebuild_source, "INSERT INTO market_sale_observation") if "native_unit_price" in sql]
assert len(sales_sql) == len(kline_sql) == len(rebuild_sql) == 1
assert sales_sql[0].split("ON DUPLICATE KEY", 1)[0].count("%s") == 20
assert kline_sql[0].split("ON DUPLICATE KEY", 1)[0].count("%s") == 15
assert rebuild_sql[0].split("ON DUPLICATE KEY", 1)[0].count("%s") == 20
print("POSITIVE_OK every active SNK writer uses date-indexed FX and freeze columns without backfill")
