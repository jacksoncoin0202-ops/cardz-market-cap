#!/usr/bin/env python3
"""已禁用：G10 日 K 線不得寫入 canonical DB。

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

Hard policy (DADDY 2026-07-31): ``g10_kline`` is G10-derived data and must
never enter the canonical ``cardz_market_cap`` database.  This command is
kept only as an explicit, fail-closed compatibility endpoint: dry-run reports
the ban without opening a DB connection; ``--write`` exits 2 before any DB
operation.
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
CANONICAL_DB_WRITE_BANNED_REASON = (
    "G10-derived K-line data (source_code=g10_kline) is forbidden from canonical DB"
)


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
    parser.add_argument("--write", action="store_true", help="已禁用；永遠不會寫入 canonical DB")
    add_connection_args(parser)
    args = parser.parse_args()

    # Keep this gate before connection_from_args(): neither the compatibility
    # dry-run nor an accidental --write may touch canonical MySQL.
    if args.write:
        print(f"[BANNED] {CANONICAL_DB_WRITE_BANNED_REASON}", file=sys.stderr)
        return 2
    print(f"[BANNED DRY-RUN] {CANONICAL_DB_WRITE_BANNED_REASON}; no DB connection or write.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
