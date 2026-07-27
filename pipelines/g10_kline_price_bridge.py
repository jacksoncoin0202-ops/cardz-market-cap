#!/usr/bin/env python3
"""把 ledger 入面嘅 G10 日 K 線橋接落 `market_price_observation`。

點解要有呢個檔：`g10_analytics_ingest.py` 已經將 47,582 條 PSA10 日 K 線
（2023-07-20 → 2026-07-25、636 個實體）原封落咗 `market_source_observation`
（kind=`g10_kline_daily`），但全 repo 冇 reader —— snapshot producer 讀價只行
`market_price_observation`。呢個橋接就係嗰條缺咗嘅線：ebay 體系（PTCG 為主）
嘅三年價格歷史、同埋部分從未有價嘅卡，全部靠佢先接得上前端。

## 規則（全部跟已核實嘅現況，唔另立標準）

* **只認 `carried=0` 做價格觀測**（4,357 / 47,582）。`carried=1` 係 G10 機械
  延伸前收盤，唔係當日觀測；`g10_analytics_ingest.py` 文檔已實測
  `carried=1 & tx>0` 有 1,667 條，證明唔可以用 `tx` 判斷。
* **幣種 USD 已驗證**：snkrdunk:100081 K 線 2026-06-08 close=325.0，同卡同日
  `market_price_observation`（ebay 源）price_usd=325.000000，完全吻合。
* **id 對應唯一合法路徑係 `catalog_source_identity`**（entity 前綴
  `ebay:`/`snkrdunk:` 直接就係 identity 嘅 source_code），唔准靠卡名。
  對唔到嘅計 quarantined，等 identity 擴充後重跑自動撿返。
* **`source_code='g10_kline'`、`source_priority=300`**：現有直採源係
  100/150（snkrdunk/ebay 直採）同 200（snk_psa10 每日快照）。producer 揀價
  `ORDER BY observed_date DESC, source_priority ASC`，300 保證 K 線只喺
  該卡該日冇任何直採觀測時先出頭 —— 補洞，唔搶位。
* **INSERT IGNORE**：`uq_market_price_daily (variant_id, source_code,
  observed_date)` 撞到即係已入過，重跑 idempotent，唔蓋 provenance。

Exit codes: 0 = 正常, 1 = 冇嘢做, 2 = 異常。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date, datetime, time, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

SOURCE_CODE = "g10_kline"
SOURCE_PRIORITY = 300
KIND_KLINE_DAILY = "g10_kline_daily"
LEDGER_SOURCE = "g10_analytics"
BATCH_SIZE = 2000


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def chunked(rows: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def load_identity_map(cursor: Any) -> dict[tuple[str, str], int]:
    cursor.execute(
        "SELECT source_code, external_entity_id, variant_id FROM catalog_source_identity "
        "WHERE source_code IN ('ebay','snkrdunk')"
    )
    return {
        (str(r["source_code"]), str(r["external_entity_id"])): int(r["variant_id"])
        for r in cursor.fetchall()
    }


def collect(cursor: Any) -> dict[str, Any]:
    identity = load_identity_map(cursor)
    cursor.execute(
        "SELECT external_entity_id, payload_json FROM market_source_observation "
        "WHERE observation_kind=%s AND source_code=%s",
        (KIND_KLINE_DAILY, LEDGER_SOURCE),
    )
    rows: list[tuple[Any, ...]] = []
    counts = {"observed": 0, "carried_skipped": 0, "quarantined": 0, "rejected": 0, "accepted": 0}
    unmatched_entities: set[str] = set()
    for row in cursor.fetchall():
        external_entity_id = row["external_entity_id"]
        counts["observed"] += 1
        payload = json.loads(row["payload_json"])
        if payload.get("carried") != 0:
            counts["carried_skipped"] += 1
            continue
        prefix, _, external_id = str(external_entity_id).partition(":")
        variant_id = identity.get((prefix, external_id))
        if variant_id is None:
            counts["quarantined"] += 1
            unmatched_entities.add(str(external_entity_id))
            continue
        close = payload.get("close")
        day_text = payload.get("date")
        if not isinstance(close, (int, float)) or close <= 0 or not day_text:
            counts["rejected"] += 1
            continue
        observed_date = date.fromisoformat(str(day_text))
        # 日線收盤 → 當日尾；DB session 係 UTC，同 observed_date 唔跨日。
        effective_at = datetime.combine(observed_date, time(23, 59, 59))
        payload_sha = sha256_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
        rows.append((variant_id, observed_date, effective_at, round(float(close), 6), payload_sha))
        counts["accepted"] += 1
    return {"rows": rows, "counts": counts, "unmatched_entities": sorted(unmatched_entities)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="真寫入；唔俾就 dry-run")
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        with connection.cursor() as cursor:
            collected = collect(cursor)
            counts = collected["counts"]
            rows = collected["rows"]
            cursor.execute(
                "SELECT COUNT(*) AS c FROM market_price_observation WHERE source_code=%s",
                (SOURCE_CODE,),
            )
            before = int(cursor.fetchone()["c"])
            print(f"[K線橋接] ledger 讀入 {counts['observed']} | carried 跳過 {counts['carried_skipped']} | "
                  f"identity 對唔到 {counts['quarantined']}（{len(collected['unmatched_entities'])} 個實體）| "
                  f"無效 close {counts['rejected']} | 候選寫入 {counts['accepted']}")
            print(f"[K線橋接] market_price_observation source={SOURCE_CODE} 現有 {before} 行")
            if not rows:
                print("[K線橋接] 冇嘢寫")
                return 1
            if not args.write:
                print("[DRY-RUN] 未寫入。加 --write 先真寫。")
                return 0

            started_at = datetime.now(timezone.utc).replace(tzinfo=None)
            run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
            effective_at = max(r[2] for r in rows)
            manifest_sha = sha256_text("\n".join(sorted(r[4] for r in rows)))
            cursor.execute(
                """
                INSERT INTO market_ingest_run
                    (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                     status, observed_count, started_at)
                VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
                """,
                (run_key, SOURCE_CODE, effective_at, manifest_sha, manifest_sha,
                 counts["observed"], started_at),
            )
            run_id = int(cursor.lastrowid)
            inserted = 0
            for batch in chunked(rows, BATCH_SIZE):
                inserted += cursor.executemany(
                    """
                    INSERT IGNORE INTO market_price_observation
                        (run_id, variant_id, source_code, observed_date, effective_at,
                         price_usd, native_price, native_currency, source_priority,
                         metric_status, payload_sha256)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, 'USD', %s, 'ready', %s)
                    """,
                    [
                        (run_id, variant_id, SOURCE_CODE, observed_date, eff, close, close,
                         SOURCE_PRIORITY, sha)
                        for variant_id, observed_date, eff, close, sha in batch
                    ],
                )
            cursor.execute(
                """
                UPDATE market_ingest_run
                SET status='completed', observed_count=%s, accepted_count=%s,
                    quarantined_count=%s, rejected_count=%s, completed_at=%s
                WHERE id=%s
                """,
                (counts["observed"], counts["accepted"], counts["quarantined"],
                 counts["rejected"], datetime.now(timezone.utc).replace(tzinfo=None), run_id),
            )
            connection.commit()
            cursor.execute(
                "SELECT COUNT(*) AS c FROM market_price_observation WHERE source_code=%s",
                (SOURCE_CODE,),
            )
            after = int(cursor.fetchone()["c"])
            print(f"[K線橋接] run_id={run_id} 新插入 {inserted} | "
                  f"source={SOURCE_CODE} 行數 {before} → {after}")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
