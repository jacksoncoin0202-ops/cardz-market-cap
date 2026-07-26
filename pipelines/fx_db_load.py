#!/usr/bin/env python3
"""把 FX 快取寫入 `market_fx_rate_observation`。

點解要有呢個檔：`fx_rates.py` 一路都有攞匯率落
`data/runtime/private-fx/latest.json`，`run_daily.py` 每日都行緊佢；但
`canonical_public_snapshot.py:596` 係讀 DB 表 `market_fx_rate_observation`。
兩邊之間從來冇人接線，所以嗰張表由頭到尾 0 行，出街 snapshot 除咗 USD
之外六隻貨幣全部 `{"value": null, "status": "unavailable"}`。前端
`format.ts:31-33` 見到一個非 finite rate 就 blank 晒嗰個幣種所有錢銀欄位——
即係訪客一撳 HKD/CNY/GBP/TWD/JPY/KRW 就成版空白。

呢個 loader 就係嗰條線：快取檔 → DB 表 → snapshot。冇新數據源，冇新契約。

USD 唔會寫入：`currency_block()` 由頭到尾自己合成 `USD = 1`，寫落去只會多一行
冇人讀嘅數據。

Exit codes: 0 = 寫咗, 2 = 快取唔見／驗唔過。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from fx_rates import DEFAULT_CACHE, validate_snapshot

SOURCE_CODE = "frankfurter"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def parse_instant(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def load_rates(connection: Any, snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Upsert one ingest run plus every non-USD quote from the FX cache."""

    payload_sha = str(snapshot["payloadSha256"])
    fetched_at = parse_instant(snapshot["fetchedAt"])
    run_key = sha256_text(json.dumps({"source": SOURCE_CODE, "payload": payload_sha}, sort_keys=True))
    quotes = {
        currency: rate
        for currency, rate in snapshot["rates"].items()
        if currency != "USD" and isinstance(rate, Mapping) and rate.get("value") is not None
    }
    if not quotes:
        raise SystemExit("FX snapshot carries no usable quote currency")
    effective_at = max(parse_instant(rate["effectiveAt"]) for rate in quotes.values())

    with connection.cursor() as cursor:
        cursor.execute("SELECT id FROM market_ingest_run WHERE run_key = %s", (run_key,))
        existing = cursor.fetchone()
        started_at = datetime.now(timezone.utc).replace(tzinfo=None)
        if existing:
            run_id = int(existing["id"])
            cursor.execute(
                "UPDATE market_ingest_run SET status='running', started_at=%s, completed_at=NULL WHERE id=%s",
                (started_at, run_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO market_ingest_run
                    (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                     status, observed_count, started_at)
                VALUES (%s, %s, 'incremental', %s, %s, %s, 'running', %s, %s)
                """,
                (run_key, SOURCE_CODE, effective_at, payload_sha, payload_sha, len(quotes), started_at),
            )
            run_id = int(cursor.lastrowid)

        written = 0
        for currency, rate in sorted(quotes.items()):
            quote_effective = parse_instant(rate["effectiveAt"])
            cursor.execute(
                """
                INSERT INTO market_fx_rate_observation
                    (run_id, base_currency, quote_currency, rate, effective_at, effective_date,
                     fetched_at, payload_sha256)
                VALUES (%s, 'USD', %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id), rate=VALUES(rate), effective_at=VALUES(effective_at),
                    fetched_at=VALUES(fetched_at), payload_sha256=VALUES(payload_sha256)
                """,
                (
                    run_id, currency, str(rate["value"]), quote_effective,
                    quote_effective.date(), fetched_at, payload_sha,
                ),
            )
            written += 1

        cursor.execute(
            "UPDATE market_ingest_run SET status='complete', accepted_count=%s, completed_at=%s WHERE id=%s",
            (written, datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
    return {
        "runId": run_id,
        "written": written,
        "currencies": sorted(quotes),
        "effectiveAt": effective_at.isoformat() + "Z",
        "payloadSha256": payload_sha,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Load the FX cache into market_fx_rate_observation")
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_CACHE)
    add_connection_args(parser)
    args = parser.parse_args()

    path = args.snapshot.resolve()
    if not path.is_file():
        print(json.dumps({"status": "missing", "snapshot": str(path)}, sort_keys=True), file=sys.stderr)
        return 2
    snapshot = validate_snapshot(json.loads(path.read_text(encoding="utf-8")))

    connection = connection_from_args(args)
    try:
        result = load_rates(connection, snapshot)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    print(json.dumps({"status": "loaded", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
