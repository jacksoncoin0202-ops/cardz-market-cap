#!/usr/bin/env python3
"""把本機 G10 (`../grade10-scraper`) 嘅 eBay 成交數據入庫。

點解要有呢個檔：`docs/G10_BASELINE.md` §1 實測 G10 本機硬碟有 559 卡 / 9,489 條
eBay PSA 10 成交，而我哋 DB 嘅 `market_sale_observation` **0 行**、
`market_price_observation` 嘅 `ebay` 源得 92 行 / 59 卡。數據一早喺度，冇人讀過。
呢個 loader 就係嗰條線：G10 檔案樹 → 三張 fact table。冇新爬蟲，冇新契約。

寫入三張表：
  * `market_sale_observation`      逐筆成交（主目標）
  * `market_daily_sales_aggregate` 每日 rollup（前端斷點 #2 應該讀嗰張）
  * `market_price_observation`     每日代表價，**只寫 PSA 10**，用當日 median

## 日期解析（做錯全盤皆錯）

G10 個 90 日滾動窗口撈埋兩種格式：舊嘅落咗 ISO `YYYY-MM-DD`，最近 0-3 日仲係
`N day(s) ago`。相對日期嘅基準用**檔案 mtime 嘅 UTC 曆日**，唔用 `datetime.now()`
(檔可能係幾日前抄落嚟，用 now 會令成批日期整體偏移)。

點解係 UTC 曆日唔係本機曆日：對 563 個 PSA 10 檔逐個量「最新 ISO 日」對「最舊
相對日」嘅距離 —— UTC 基準嘅眾數係 **1 日**（164 個檔），即係 ISO 覆蓋到 D 日、
相對日由 D+1 日開始，無縫接駁；本機(JST)基準眾數係 2 日，即係中間**系統性缺一日**。
兩個基準都冇出現距離 ≤ 0（即冇矛盾重疊），所以 UTC 曆日先係啱嘅切換邊界。

解析唔到嘅照入庫（`sold_at` NULL / `timestamp_quality='unparsed'` /
`coverage_status='quarantined'`），`source_date_text` 保住原文日後可以重算，但
**唔計入兩張 aggregate 表**。unparsed 比例 > 5% 即 fail-closed。

## 卡片對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名（`docs/G10_BASELINE.md` §2.3：
卡名 exact match 報 251/251 係假嘅，641 個目錄得 319 個 unique 卡名）。
  * `source_code='ebay'`     的 `external_entity_id` = `data/cards/altxyz/{UUID}/` 目錄名
  * `source_code='snkrdunk'` 的 `external_entity_id` = `data/cards/snkrdunk/{id}/` 目錄名

Exit codes: 0 = 正常, 1 = unparsed 比例爆閘 / 冇嘢做, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "cards"
SOURCE_CODE = "ebay"

# G10 目錄 provider → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}

# grade key → (檔名, grader_code, grade_label)
GRADE_FILES: dict[str, tuple[str, str, str]] = {
    "PSA_10": ("ebay_PSA_10.json", "psa", "10"),
    "PSA_9": ("ebay_PSA_9.json", "psa", "9"),
    "BGS_10": ("ebay_BGS_10.json", "bgs", "10"),
    "BGS_BL": ("ebay_BGS_BL.json", "bgs", "BL"),
    "CGC_10": ("ebay_CGC_10.json", "cgc", "10"),
}
PRICE_GRADE_KEY = "PSA_10"

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELATIVE_DAYS = re.compile(r"^(\d+)\s+days?\s+ago$", re.IGNORECASE)
UNPARSED_FAIL_RATIO = 0.05
CENT = Decimal("0.000001")

# 已存在嘅 `ebay` 價格行用 priority 100（59 行）同 150（33 行）；snk_psa10 用 200。
# 寫 100 = 沿用大多數現行 ebay 行，同時保持「eBay = PSA10 成交唯一真源」嘅排序。
EBAY_PRICE_PRIORITY = 100
# `canonical_public_snapshot.py:695` 要 coverage == 'partial' 先當 sales ready。
ACCEPTED_COVERAGE = "partial"
QUARANTINED_COVERAGE = "quarantined"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def money(value: Decimal) -> str:
    """統一成 DECIMAL(18,6) 嘅字面值，令 fingerprint 唔受 int/float 表示影響。"""

    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def mtime_fetched_at(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def relative_base_date(fetched_at: datetime) -> date:
    return fetched_at.date()


def resolve_sale_date(source_date_text: str, base_date: date) -> tuple[datetime | None, str]:
    """回 (sold_at, timestamp_quality)。base_date 係檔案 mtime 嘅 UTC 曆日。"""

    raw = str(source_date_text or "").strip()
    if ISO_DATE.match(raw):
        try:
            return datetime.combine(date.fromisoformat(raw), time.min), "exact_date"
        except ValueError:
            return None, "unparsed"
    relative = RELATIVE_DAYS.match(raw)
    if relative:
        return datetime.combine(base_date - timedelta(days=int(relative.group(1))), time.min), "relative_resolved"
    return None, "unparsed"


def transaction_fingerprint(
    external_entity_id: str,
    grader_code: str,
    grade_label: str,
    source_date_text: str,
    unit_price_usd: Decimal,
) -> str:
    """用**原始 date 字串**，唔用解析後嘅 sold_at —— 相對日期嘅解析結果會隨 mtime 變，
    用原文先至重跑 idempotent。"""

    return sha256_text(
        f"{external_entity_id}|{grader_code}|{grade_label}|{source_date_text}|{money(unit_price_usd)}"
    )


def median_usd(values: Sequence[Decimal]) -> Decimal:
    """當日成交嘅中位數。唔用 mean —— 單筆天價會拉高。"""

    return Decimal(statistics.median(values)).quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class SaleRow:
    variant_id: int
    external_entity_id: str
    fingerprint: str
    grader_code: str
    grade_label: str
    grade_key: str
    sold_at: datetime | None
    source_date_text: str
    fetched_at: datetime
    timestamp_quality: str
    unit_price_usd: Decimal
    source_payload_sha256: str

    @property
    def accepted(self) -> bool:
        return self.sold_at is not None

    @property
    def coverage_status(self) -> str:
        return ACCEPTED_COVERAGE if self.accepted else QUARANTINED_COVERAGE


def sale_rows_from_document(
    document: Any,
    *,
    variant_id: int,
    external_entity_id: str,
    grade_key: str,
    grader_code: str,
    grade_label: str,
    fetched_at: datetime,
    base_date: date,
    payload_sha256: str,
    stats: Counter,
) -> list[SaleRow]:
    if not isinstance(document, Mapping):
        stats["file_invalid"] += 1
        return []
    history = document.get("saleHistory")
    if history is None:
        stats["file_no_history"] += 1
        return []
    if not isinstance(history, list):
        stats["file_invalid"] += 1
        return []

    rows: list[SaleRow] = []
    seen: set[str] = set()
    for entry in history:
        if not isinstance(entry, Mapping):
            stats["row_invalid"] += 1
            continue
        stats["row_seen"] += 1
        price = entry.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            stats["row_rejected_price"] += 1
            continue
        currency = str(entry.get("currency") or "").strip().lower()
        if currency != "usd":
            # 唔准估匯率填 unit_price_usd（NOT NULL），寧願唔要。
            stats["row_rejected_currency"] += 1
            continue
        source_date_text = str(entry.get("date") or "").strip()[:100]
        unit_price = Decimal(str(price))
        sold_at, quality = resolve_sale_date(source_date_text, base_date)
        stats[f"quality_{quality}"] += 1
        fingerprint = transaction_fingerprint(
            external_entity_id, grader_code, grade_label, source_date_text, unit_price
        )
        if fingerprint in seen:
            # 同一張卡同日同價嘅兩筆成交會撞同一個 fingerprint。UNIQUE KEY 只留一行，
            # 呢度先計數，報告出到俾人知漏咗幾多。
            stats["row_fingerprint_collision"] += 1
            continue
        seen.add(fingerprint)
        rows.append(
            SaleRow(
                variant_id=variant_id,
                external_entity_id=external_entity_id,
                fingerprint=fingerprint,
                grader_code=grader_code,
                grade_label=grade_label,
                grade_key=grade_key,
                sold_at=sold_at,
                source_date_text=source_date_text,
                fetched_at=fetched_at,
                timestamp_quality=quality,
                unit_price_usd=unit_price,
                source_payload_sha256=payload_sha256,
            )
        )
    return rows


def load_identity_map(
    connection: Any,
    *,
    variant_ids: set[int] | None = None,
) -> dict[tuple[str, str], int]:
    """(G10 provider 目錄, 目錄名) → variant_id。唯一合法對應路徑。

    Fail-closed: only ``match_status='exact'`` owns a directory. Rejected/derived
    rows (e.g. metal-card SNK twin 610624 vs exact 486166 on Mew ex) must not
    pull eBay comps onto the catalog variant — that caused sale_source_identity_not_exact.
    """

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COALESCE(alias.canonical_variant_id, identity.variant_id) AS variant_id, "
            "identity.source_code, identity.external_entity_id "
            "FROM catalog_source_identity AS identity "
            "LEFT JOIN catalog_variant_alias AS alias "
            "ON alias.duplicate_variant_id=identity.variant_id "
            "WHERE identity.source_code IN ('ebay','snkrdunk') "
            "AND identity.match_status='exact'"
        )
        rows = cursor.fetchall()
    by_source = {source: provider for provider, source in PROVIDER_IDENTITY_SOURCE.items()}
    mapping: dict[tuple[str, str], int] = {}
    for row in rows:
        variant_id = int(row["variant_id"])
        if variant_ids is not None and variant_id not in variant_ids:
            continue
        provider = by_source.get(str(row["source_code"]))
        if provider is None:
            continue
        mapping[(provider, str(row["external_entity_id"]))] = variant_id
    return mapping


def load_universe_variant_ids(connection: Any, universe_lock_id: int) -> set[int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id FROM market_universe_member WHERE universe_lock_id=%s",
            (universe_lock_id,),
        )
        rows = cursor.fetchall()
    variant_ids = {int(row["variant_id"]) for row in rows}
    if not variant_ids:
        raise ValueError(f"empty_or_missing_universe_lock:{universe_lock_id}")
    return variant_ids


def iter_card_dirs(g10_root: Path, limit: int | None) -> Iterable[tuple[str, Path]]:
    emitted = 0
    for provider in sorted(PROVIDER_IDENTITY_SOURCE):
        provider_root = g10_root / provider
        if not provider_root.is_dir():
            continue
        for card_dir in sorted(provider_root.iterdir()):
            if not card_dir.is_dir():
                continue
            if limit is not None and emitted >= limit:
                return
            emitted += 1
            yield provider, card_dir


def collect(
    g10_root: Path,
    grades: Sequence[str],
    identity: Mapping[tuple[str, str], int],
    limit: int | None,
) -> tuple[list[SaleRow], Counter, list[str], list[str]]:
    stats: Counter = Counter()
    rows: list[SaleRow] = []
    file_hashes: list[str] = []
    unmatched: list[str] = []
    for provider, card_dir in iter_card_dirs(g10_root, limit):
        stats["dir_seen"] += 1
        stats[f"dir_seen_{provider}"] += 1
        external_entity_id = card_dir.name
        variant_id = identity.get((provider, external_entity_id))
        if variant_id is None:
            stats["dir_unmatched"] += 1
            stats[f"dir_unmatched_{provider}"] += 1
            unmatched.append(f"{provider}/{external_entity_id}")
            continue
        stats["dir_matched"] += 1
        stats[f"dir_matched_{provider}"] += 1
        matched_any_file = False
        for grade_key in grades:
            filename, grader_code, grade_label = GRADE_FILES[grade_key]
            path = card_dir / filename
            if not path.is_file():
                stats[f"file_missing_{grade_key}"] += 1
                continue
            matched_any_file = True
            stats[f"file_read_{grade_key}"] += 1
            raw = path.read_bytes()
            payload_sha256 = sha256_bytes(raw)
            file_hashes.append(payload_sha256)
            fetched_at = mtime_fetched_at(path)
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                stats["file_invalid"] += 1
                continue
            before = len(rows)
            rows.extend(
                sale_rows_from_document(
                    document,
                    variant_id=variant_id,
                    external_entity_id=external_entity_id,
                    grade_key=grade_key,
                    grader_code=grader_code,
                    grade_label=grade_label,
                    fetched_at=fetched_at,
                    base_date=relative_base_date(fetched_at),
                    payload_sha256=payload_sha256,
                    stats=stats,
                )
            )
            stats[f"rows_{grade_key}"] += len(rows) - before
        if matched_any_file:
            stats["dir_with_data"] += 1
    return rows, stats, file_hashes, unmatched


def daily_sales_rows(rows: Iterable[SaleRow]) -> list[dict[str, Any]]:
    """逐 (variant, 日) 滾埋所選 grade 嘅成交。quarantine 行唔計。"""

    grouped: dict[tuple[int, date], list[SaleRow]] = defaultdict(list)
    for row in rows:
        if not row.accepted:
            continue
        grouped[(row.variant_id, row.sold_at.date())].append(row)
    output: list[dict[str, Any]] = []
    for (variant_id, observed_date), bucket in sorted(grouped.items()):
        total = sum((row.unit_price_usd for row in bucket), Decimal("0"))
        output.append(
            {
                "variant_id": variant_id,
                "observed_date": observed_date,
                "sales_count": len(bucket),
                "sales_value_usd": total.quantize(CENT, rounding=ROUND_HALF_UP),
                "payload_sha256": sha256_text("\n".join(sorted(row.fingerprint for row in bucket))),
            }
        )
    return output


def daily_price_rows(rows: Iterable[SaleRow]) -> list[dict[str, Any]]:
    """只用 PSA 10，價 = 當日成交 median（唔用 mean，單筆天價會拉高）。"""

    grouped: dict[tuple[int, date], list[SaleRow]] = defaultdict(list)
    for row in rows:
        if not row.accepted or row.grade_key != PRICE_GRADE_KEY:
            continue
        grouped[(row.variant_id, row.sold_at.date())].append(row)
    output: list[dict[str, Any]] = []
    for (variant_id, observed_date), bucket in sorted(grouped.items()):
        output.append(
            {
                "variant_id": variant_id,
                "observed_date": observed_date,
                "price_usd": median_usd([row.unit_price_usd for row in bucket]),
                "effective_at": max(row.fetched_at for row in bucket),
                "payload_sha256": sha256_text("\n".join(sorted(row.fingerprint for row in bucket))),
            }
        )
    return output


def table_count(cursor: Any, table: str, source_code: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE source_code = %s", (source_code,))
    return int(cursor.fetchone()["c"])


def write_all(
    connection: Any,
    rows: Sequence[SaleRow],
    sales: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    file_hashes: Sequence[str],
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"g10_ebay|{started_at.isoformat()}")
    effective_at = max(row.fetched_at for row in rows)
    payload_sha256 = sha256_text("\n".join(sorted(row.fingerprint for row in rows)))
    manifest_sha256 = sha256_text("\n".join(sorted(file_hashes)))
    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted

    with connection.cursor() as cursor:
        before = {
            "market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE),
            "market_daily_sales_aggregate": table_count(cursor, "market_daily_sales_aggregate", SOURCE_CODE),
            "market_price_observation": table_count(cursor, "market_price_observation", SOURCE_CODE),
        }
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (run_key, SOURCE_CODE, effective_at, payload_sha256, manifest_sha256, len(rows), started_at),
        )
        run_id = int(cursor.lastrowid)

        cursor.executemany(
            """
            INSERT INTO market_sale_observation
                (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
                 grader_code, grade_label, sold_at, source_date_text, fetched_at, timestamp_quality,
                 unit_price_usd, quantity, transaction_value_usd, source_payload_sha256, coverage_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                run_id=VALUES(run_id), variant_id=VALUES(variant_id), grader_code=VALUES(grader_code),
                grade_label=VALUES(grade_label), sold_at=VALUES(sold_at), fetched_at=VALUES(fetched_at),
                timestamp_quality=VALUES(timestamp_quality), unit_price_usd=VALUES(unit_price_usd),
                quantity=VALUES(quantity), transaction_value_usd=VALUES(transaction_value_usd),
                source_payload_sha256=VALUES(source_payload_sha256), coverage_status=VALUES(coverage_status)
            """,
            [
                (
                    run_id, row.variant_id, SOURCE_CODE, row.external_entity_id, row.fingerprint,
                    row.grader_code, row.grade_label, row.sold_at, row.source_date_text, row.fetched_at,
                    row.timestamp_quality, money(row.unit_price_usd), money(row.unit_price_usd),
                    row.source_payload_sha256, row.coverage_status,
                )
                for row in rows
            ],
        )
        cursor.executemany(
            """
            INSERT INTO market_daily_sales_aggregate
                (run_id, variant_id, source_code, observed_date, sales_count, sales_value_usd,
                 native_sales_value, native_currency, coverage_status, payload_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s)
            ON DUPLICATE KEY UPDATE
                run_id=VALUES(run_id), sales_count=VALUES(sales_count),
                sales_value_usd=VALUES(sales_value_usd), native_sales_value=VALUES(native_sales_value),
                native_currency=VALUES(native_currency), coverage_status=VALUES(coverage_status),
                payload_sha256=VALUES(payload_sha256)
            """,
            [
                (
                    run_id, row["variant_id"], SOURCE_CODE, row["observed_date"], row["sales_count"],
                    str(row["sales_value_usd"]), ACCEPTED_COVERAGE, row["payload_sha256"],
                )
                for row in sales
            ],
        )
        cursor.executemany(
            """
            INSERT INTO market_price_observation
                (run_id, variant_id, source_code, observed_date, effective_at, price_usd,
                 native_price, native_currency, source_priority, metric_status, payload_sha256)
            VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, 'ready', %s)
            ON DUPLICATE KEY UPDATE
                run_id=VALUES(run_id), effective_at=VALUES(effective_at), price_usd=VALUES(price_usd),
                native_price=VALUES(native_price), native_currency=VALUES(native_currency),
                source_priority=VALUES(source_priority), metric_status=VALUES(metric_status),
                payload_sha256=VALUES(payload_sha256)
            """,
            [
                (
                    run_id, row["variant_id"], SOURCE_CODE, row["observed_date"], row["effective_at"],
                    str(row["price_usd"]), EBAY_PRICE_PRIORITY, row["payload_sha256"],
                )
                for row in prices
            ],
        )
        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', observed_count=%s, accepted_count=%s,
                quarantined_count=%s, rejected_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (len(rows), accepted, quarantined, 0, datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
        after = {
            "market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE),
            "market_daily_sales_aggregate": table_count(cursor, "market_daily_sales_aggregate", SOURCE_CODE),
            "market_price_observation": table_count(cursor, "market_price_observation", SOURCE_CODE),
        }
    return {"run_id": run_id, "run_key": run_key, "before": before, "after": after}


def print_report(
    *,
    grades: Sequence[str],
    stats: Counter,
    rows: Sequence[SaleRow],
    sales: Sequence[Mapping[str, Any]],
    prices: Sequence[Mapping[str, Any]],
    unmatched: Sequence[str],
    identity_size: int,
    unparsed_ratio: float,
    write_result: Mapping[str, Any] | None,
) -> None:
    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted
    sold = [row.sold_at.date() for row in rows if row.accepted]

    print("=" * 72)
    print(f"G10 eBay ingest — {'WRITE' if write_result else 'DRY-RUN'}   grades={','.join(grades)}")
    print("=" * 72)

    print("\n[目錄掃描]")
    print(f"  catalog_source_identity 可用對應   : {identity_size}")
    print(f"  掃到目錄                           : {stats['dir_seen']}"
          f"  (altxyz {stats['dir_seen_altxyz']} / snkrdunk {stats['dir_seen_snkrdunk']})")
    print(f"  對到 variant                       : {stats['dir_matched']}"
          f"  (altxyz {stats['dir_matched_altxyz']} / snkrdunk {stats['dir_matched_snkrdunk']})")
    print(f"  對唔到 variant（skip）             : {stats['dir_unmatched']}"
          f"  (altxyz {stats['dir_unmatched_altxyz']} / snkrdunk {stats['dir_unmatched_snkrdunk']})")
    print(f"  對到而且有 eBay 檔                 : {stats['dir_with_data']}")
    print(f"  distinct variant 有成交            : {len({row.variant_id for row in rows})}")
    if unmatched:
        print(f"  對唔到樣本（頭 5）: {', '.join(unmatched[:5])}")

    print("\n[逐 grade 檔案]")
    print(f"  {'grade':<8} {'讀到檔':>7} {'缺檔':>7} {'成交行':>9}")
    for grade_key in grades:
        print(f"  {grade_key:<8} {stats[f'file_read_{grade_key}']:>7} "
              f"{stats[f'file_missing_{grade_key}']:>7} {stats[f'rows_{grade_key}']:>9}")
    print(f"  檔案無 saleHistory : {stats['file_no_history']}   檔案格式壞 : {stats['file_invalid']}")

    print("\n[成交記錄]")
    print(f"  saleHistory 掃到           : {stats['row_seen']}")
    print(f"  價格唔合法 rejected        : {stats['row_rejected_price']}")
    print(f"  非 USD rejected            : {stats['row_rejected_currency']}")
    print(f"  fingerprint 撞冧漏咗       : {stats['row_fingerprint_collision']}")
    print(f"  最終成 row                 : {len(rows)}")

    print("\n[日期格式分佈]")
    total_quality = stats["quality_exact_date"] + stats["quality_relative_resolved"] + stats["quality_unparsed"]
    for label, key in (("ISO exact_date", "quality_exact_date"),
                       ("相對 relative_resolved", "quality_relative_resolved"),
                       ("解析唔到 unparsed", "quality_unparsed")):
        share = (stats[key] / total_quality * 100) if total_quality else 0.0
        print(f"  {label:<26}: {stats[key]:>7}  ({share:5.2f}%)")
    print(f"  unparsed 比例 : {unparsed_ratio * 100:.2f}%  (閘 {UNPARSED_FAIL_RATIO * 100:.0f}%)")
    if sold:
        print(f"  sold_at 範圍  : {min(sold)} → {max(sold)}   distinct 日數 {len(set(sold))}")

    print("\n[會寫入幾多行]")
    print(f"  market_sale_observation      : {len(rows)}"
          f"   (accepted {accepted} / quarantined {quarantined})")
    print(f"  market_daily_sales_aggregate : {len(sales)}")
    print(f"  market_price_observation     : {len(prices)}   (只 PSA 10, median)")

    if write_result:
        print("\n[實際入庫]")
        print(f"  run_id  : {write_result['run_id']}")
        print(f"  run_key : {write_result['run_key']}")
        for table in ("market_sale_observation", "market_daily_sales_aggregate", "market_price_observation"):
            before = write_result["before"][table]
            after = write_result["after"][table]
            print(f"  {table:<30} source_code='{SOURCE_CODE}': {before} → {after}  (+{after - before})")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest local G10 eBay sale history into CARDZ MySQL")
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument("--grades", default=",".join(GRADE_FILES))
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個卡目錄")
    parser.add_argument(
        "--universe-lock-id",
        type=int,
        default=None,
        help="只處理指定 immutable universe lock 內的 canonical variants",
    )
    add_connection_args(parser)
    args = parser.parse_args()

    grades = [item.strip() for item in str(args.grades).split(",") if item.strip()]
    unknown = [grade for grade in grades if grade not in GRADE_FILES]
    if unknown:
        print(f"unknown grade(s): {', '.join(unknown)}; valid: {', '.join(GRADE_FILES)}", file=sys.stderr)
        return 1

    g10_root = args.g10_root.resolve()
    if not g10_root.is_dir():
        print(f"G10 card root not found: {g10_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        variant_ids = (
            load_universe_variant_ids(connection, args.universe_lock_id)
            if args.universe_lock_id is not None
            else None
        )
        identity = load_identity_map(connection, variant_ids=variant_ids)
        rows, stats, file_hashes, unmatched = collect(g10_root, grades, identity, args.limit)
        sales = daily_sales_rows(rows)
        prices = daily_price_rows(rows)
        total_quality = (
            stats["quality_exact_date"] + stats["quality_relative_resolved"] + stats["quality_unparsed"]
        )
        unparsed_ratio = (stats["quality_unparsed"] / total_quality) if total_quality else 0.0

        if not rows:
            print_report(
                grades=grades, stats=stats, rows=rows, sales=sales, prices=prices,
                unmatched=unmatched, identity_size=len(identity),
                unparsed_ratio=unparsed_ratio, write_result=None,
            )
            print("\n!! 冇任何成交行，唔會寫入。", file=sys.stderr)
            return 1

        if unparsed_ratio > UNPARSED_FAIL_RATIO:
            print_report(
                grades=grades, stats=stats, rows=rows, sales=sales, prices=prices,
                unmatched=unmatched, identity_size=len(identity),
                unparsed_ratio=unparsed_ratio, write_result=None,
            )
            print(
                f"\n!! FAIL-CLOSED: unparsed 比例 {unparsed_ratio * 100:.2f}% "
                f"> {UNPARSED_FAIL_RATIO * 100:.0f}%，唔入庫。日期規則要先修。",
                file=sys.stderr,
            )
            return 1

        write_result = None
        if args.write:
            try:
                write_result = write_all(connection, rows, sales, prices, file_hashes)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        print_report(
            grades=grades, stats=stats, rows=rows, sales=sales, prices=prices,
            unmatched=unmatched, identity_size=len(identity),
            unparsed_ratio=unparsed_ratio, write_result=write_result,
        )
        if not args.write:
            print("\n(dry-run — 加 --write 先真入庫)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
