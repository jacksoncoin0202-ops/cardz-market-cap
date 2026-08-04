#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply new-era DB tidy rules and report warehouse state."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402

MIGRATION = ROOT / "pipelines" / "migrations" / "022_new_era_warehouse.mysql.sql"
OUT = ROOT / "data" / "runtime" / "operator"
BANNED_PRICE_SOURCES = ("g10_kline",)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def split_sql(text: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("--"):
            continue
        buf.append(line)
        if line.rstrip().endswith(";"):
            stmt = "\n".join(buf).strip()
            if stmt:
                parts.append(stmt)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def apply_migration(cur) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    for stmt in split_sql(sql):
        cur.execute(stmt)


def quarantine_banned_prices(cur) -> dict:
    # Do not delete evidence; mark banned for composition.
    cur.execute(
        """
        UPDATE market_price_observation
        SET metric_status = 'banned_g10_kline'
        WHERE source_code IN ({})
          AND metric_status <> 'banned_g10_kline'
        """.format(",".join(["%s"] * len(BANNED_PRICE_SOURCES))),
        BANNED_PRICE_SOURCES,
    )
    price_marked = cur.rowcount
    cur.execute(
        """
        UPDATE market_source_observation
        SET observation_kind = 'quarantine_g10_kline_daily'
        WHERE observation_kind = 'g10_kline_daily'
        """
    )
    obs_marked = cur.rowcount
    return {"priceRowsMarked": int(price_marked), "sourceObservationRowsMarked": int(obs_marked)}


def ensure_banned_policy(cur) -> None:
    cur.execute(
        """
        INSERT INTO market_banned_source_policy (source_code, reason_code, policy, note, effective_at)
        VALUES ('g10_kline', 'banned_series', 'ignore_for_price', 'New-era ban', UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE policy='ignore_for_price', reason_code='banned_series'
        """
    )


def warehouse_status(cur) -> dict:
    cur.execute("SELECT version_code FROM cardz_schema_version ORDER BY version_code")
    versions = [r["version_code"] for r in cur.fetchall()]
    cur.execute("SELECT source_code, policy, reason_code FROM market_banned_source_policy")
    banned = list(cur.fetchall())
    cur.execute(
        "SELECT COUNT(*) n FROM market_price_observation WHERE source_code='g10_kline' AND metric_status='banned_g10_kline'"
    )
    banned_prices = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        "SELECT COUNT(*) n FROM market_price_observation WHERE source_code='g10_kline' AND metric_status<>'banned_g10_kline'"
    )
    live_kline_prices = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        "SELECT COUNT(*) n FROM market_source_observation WHERE observation_kind IN ('g10_kline_daily','quarantine_g10_kline_daily')"
    )
    kline_obs = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute("SELECT COUNT(*) n FROM market_source_warehouse")
    warehouse_n = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT
          SUM(CASE WHEN metric_status='banned_g10_kline' THEN 0 ELSE 1 END) AS active_price_rows,
          COUNT(DISTINCT CASE WHEN metric_status='banned_g10_kline' THEN NULL ELSE variant_id END) AS active_price_variants
        FROM market_price_observation
        """
    )
    price_stats = cur.fetchone() or {}
    cur.execute(
        """
        SELECT COUNT(*) n FROM operator_binding_freeze WHERE acceptance_status='accepted' AND freeze_kind='identity'
        """
    )
    frozen_id = int((cur.fetchone() or {}).get("n") or 0)
    return {
        "schemaVersions": versions,
        "has022": "022" in versions,
        "bannedPolicies": banned,
        "g10Kline": {
            "priceRowsBanned": banned_prices,
            "priceRowsStillActive": live_kline_prices,
            "sourceObservationRows": kline_obs,
        },
        "warehouseRows": warehouse_n,
        "activePriceRows": price_stats.get("active_price_rows"),
        "activePriceVariants": price_stats.get("active_price_variants"),
        "frozenIdentityRows": frozen_id,
    }


def main() -> int:
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        apply_migration(cur)
        ensure_banned_policy(cur)
        marked = quarantine_banned_prices(cur)
        conn.commit()
        status = warehouse_status(cur)
        OUT.mkdir(parents=True, exist_ok=True)
        report = {
            "asOf": utc_now(),
            "action": "db-tidy-new-era",
            "migration": "022_new_era_warehouse.mysql.sql",
            "quarantine": marked,
            "status": status,
            "rules": {
                "pullFirst": True,
                "productProjectsFewFields": True,
                "bannedPriceSources": list(BANNED_PRICE_SOURCES),
                "reuseOldTables": True,
                "deleteBannedEvidence": False,
            },
        }
        path = OUT / "db_tidy_new_era.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f"wrote {path}")
        if status["g10Kline"]["priceRowsStillActive"] > 0:
            return 2
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
