#!/usr/bin/env python3
"""把 G10 (`../grade10-scraper`) 嘅**累積成交史** `data/sales_cache/` 入庫。

同 `g10_ebay_ingest.py` 唔同：嗰個讀 `data/cards/*/ebay_*.json`，即係平台當下嘅
~90 日滾動窗口。呢個讀 `data/sales_cache/`，係 G10 每次爬完之後**只加唔刪**噉
累積落嚟嘅檔（641 個檔 / 19.7 MB / 160,097 條）。價值就係長度：snkrdunk 成交
由 **2022-10-05** 一路到 2026-07-25，比 eBay 嗰個 2026-04-24 起嘅窗口長成 3.8 年。

## `dt` vs `day` —— 邊個先係成交日（做錯全盤皆錯）

檔入面每筆有兩個日曆：

    {"dt": "2026-07-22T21:46:53.233995+00:00", "day": "2026-07-25", "price": ...}

實測 641 個檔 160,097 條：`day - dt.date()` **由 0 到 1384 日**，長尾散開；`day`
永遠 >= `dt.date()`（day < dt 嘅 0 條）；而 `day` 有 87.7% 集中喺 3 個值
（2026-07-22 / 07-24 / 07-25）—— 即係三次爬蟲嘅日子。

G10 自己嘅 `grade10_analytics.sale_day()` docstring 講到明：

    "A sale already has a `day` when it was seen in a previous scrape (set by
     load_sales to that scrape's date)"

`load_sales` 每次爬完，新見到嘅成交一律 `"day": today`，之後喺 cache 入面凍住。
所以 **`day` = 「呢筆成交第一次被爬到嘅日子」，係發現日曆，唔係成交日曆**。攞
`day` 做 `sold_at` 就會把 2022 年嘅成交塞落 2026-07-22，仲會喺嗰 3 日堆出一個假
成交量尖峰，成條時間線報廢。

`dt` 先係平台聲稱嘅成交時間：88.5% 係啱午夜 00:00:00（平台俾 ISO 日期），11.5%
帶時分秒（平台俾相對標籤例如 "3 days ago"，爬蟲當時用 `NOW - N` 算出嚟，所以時分
秒同 `updatedAt` 一模一樣）。

**所以 `sold_at` 一律由 `dt` 嚟**，floor 到 UTC 午夜（同已有嘅 eBay 行對齊），
`timestamp_quality` 分開兩種；`day` 唔做日期用，但連 `dt` 原文一齊擺喺
`source_date_text` 保住，日後想改規則可以重算。

## 點解淨係入 `platform='snkrdunk'`

cache 入面 63,408 條係 `platform='ebay'`，同 run 70/71（`g10_ebay_ingest`）撞：
以 (external_entity_id, dt 日期, 價) 對，**36,858/39,694 = 92.9% 完全一樣**，加埋
淨價錢對到嘅係 93.1%；反過來 DB 現有 eBay 行有 95.1% 喺 cache 搵得返。兩邊本來就
係同一批 `ebay_*.json` 嘅唔同抄本。而且 cache 嘅 eBay `dt` 只去到 2026-04-23，
喺 DB 現有下限 2026-04-24 之前得 **128 條** —— 即係入咗都唔會長返幾多歷史，淨係
會因為兩條 pipeline 嘅 fingerprint 公式唔同而變成重複成交、谷大成交額。

所以預設 `--platforms snkrdunk`：snkrdunk 嗰 96,689 條同 DB 現有行 **0% 重疊**
（實測 73,468 條對到 identity 嘅全部 no_match），係純新數據。想睇 eBay 嗰邊幾多可
以 `--platforms ebay --dry-run` 自己量，但唔建議入。

## fingerprint 一定要剔走 `day`

同一筆成交連續三日爬到，就會喺 cache 出三行（`dt`/價/grade 一樣，淨係 `day` 唔同）
—— G10 自己嘅 dedup key 係 `(day, price, grade, platform)`。實測 160,097 行只係
113,020 個 `(dt, price, grade, platform)`，即 **1.42x 膨脹**，最高一筆重複 4 次。
所以 fingerprint 用 `(variant, grader, label, platform, dt, price)`，冇 `day`，
重複抄本自動收埋一行。同一 variant 由兩個 provider 目錄嚟嘅重複亦喺記憶體收埋。

## 只寫兩張表

  * `market_sale_observation`  逐筆成交（本次目標）
  * `market_ingest_run`        run 記錄

**唔寫** `market_daily_sales_aggregate`：`canonical_public_snapshot.py:484` 讀嗰張
表冇 source filter，再寫落去會再蓋一次 sparkline。
**唔寫** `market_price_observation`：`canonical_public_snapshot.py:565/587` 係
`ORDER BY observed_date DESC, source_priority ASC` 揀價，寫多批 snkrdunk 價會改到
前端顯示緊嘅價。兩張都唔喺本次範圍。

## 卡片對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名：
  * `sales_cache/snkrdunk/{id}.json`   → `source_code='snkrdunk'`
  * `sales_cache/altxyz/{uuid}.json`   → `source_code='ebay'`
對唔到就 skip 兼列出嚟。identity 之後擴充，呢個腳本重跑會自動撿返。

Exit codes: 0 = 正常, 1 = 冇嘢做 / unparsed 爆閘 / 見到未知 grade, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "sales_cache"

# 寫入 `market_sale_observation` 嘅 source_code。用 'snkrdunk' 而唔係 'ebay'，
# 保證同 run 70/71 嘅 UNIQUE KEY (source_code, external_entity_id, fingerprint) 冇得撞。
SOURCE_CODE = "snkrdunk"

# G10 cache 目錄 → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}

DEFAULT_PLATFORMS = ("snkrdunk",)

# G10 `normalize_grade` 出嘅 16 個值 → (grader_code, grade_label)。
# 冇喺呢度嘅 grade 一律 reject 兼 fail-closed，唔准撞彩填假值。
GRADE_MAP: dict[str, tuple[str, str]] = {
    "PSA 10": ("psa", "10"),
    "PSA 9": ("psa", "9"),
    "Psa8以下": ("psa", "8_or_below"),
    "BGS 10": ("bgs", "10"),
    "BGS 9.5": ("bgs", "9.5"),
    "Bgs10 Bl": ("bgs", "BL"),
    "Bgs10 Gl": ("bgs", "GL"),
    "Bgs9以下": ("bgs", "9_or_below"),
    "CGC 10": ("cgc", "10"),
    "ARS 10": ("ars", "10"),
    "ARS 10+": ("ars", "10+"),
    "ARS 9": ("ars", "9"),
    "Ars8以下": ("ars", "8_or_below"),
    "C": ("raw", "C"),
    "D": ("raw", "D"),
    "Ungraded": ("raw", "Ungraded"),
}

UNPARSED_FAIL_RATIO = 0.05
CENT = Decimal("0.000001")
INSERT_CHUNK = 5000

# `canonical_public_snapshot.py:695` 要 coverage == 'partial' 先當 sales ready。
ACCEPTED_COVERAGE = "partial"
QUARANTINED_COVERAGE = "quarantined"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def money(value: Decimal) -> str:
    """統一成 DECIMAL(18,6) 字面值，令 fingerprint 唔受 float 表示影響。"""

    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def parse_iso_utc(raw: str) -> datetime | None:
    """`dt` / `updatedAt` 都係 ISO-8601 帶 offset。回 naive UTC。"""

    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def resolve_sale_date(dt_text: str) -> tuple[datetime | None, str]:
    """由 `dt` 定成交日。回 (sold_at, timestamp_quality)。

    `dt` 啱啱午夜 = 平台俾咗 ISO 日期 → exact_date。
    `dt` 帶時分秒 = 平台俾相對標籤，爬蟲用當時 `NOW - N` 算出嚟 → relative_resolved。
    兩種都 floor 到當日 UTC 午夜：帶住嘅時分秒係爬蟲鐘點，唔係成交鐘點。
    """

    parsed = parse_iso_utc(dt_text)
    if parsed is None:
        return None, "unparsed"
    midnight = parsed.hour == 0 and parsed.minute == 0 and parsed.second == 0 and parsed.microsecond == 0
    return datetime.combine(parsed.date(), time.min), "exact_date" if midnight else "relative_resolved"


def transaction_fingerprint(
    variant_id: int,
    grader_code: str,
    grade_label: str,
    platform: str,
    dt_text: str,
    unit_price_usd: Decimal,
) -> str:
    """**冇 `day`** —— 同一筆成交連續幾日爬到會出幾行，只係 `day` 唔同，
    帶住 `day` 就會變幾筆成交。用 cache 檔入面存住嘅 `dt` 原文（唔係解析結果），
    重跑先至穩定。"""

    return sha256_text(
        f"{variant_id}|{grader_code}|{grade_label}|{platform}|{dt_text}|{money(unit_price_usd)}"
    )


@dataclass(frozen=True)
class SaleRow:
    variant_id: int
    external_entity_id: str
    fingerprint: str
    grader_code: str
    grade_label: str
    platform: str
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


def load_identity_map(connection: Any) -> dict[tuple[str, str], int]:
    """(G10 cache 目錄, 檔名 stem) → variant_id。唯一合法對應路徑。"""

    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, source_code, external_entity_id FROM catalog_source_identity "
            "WHERE source_code IN ('ebay','snkrdunk')"
        )
        rows = cursor.fetchall()
    by_source = {source: provider for provider, source in PROVIDER_IDENTITY_SOURCE.items()}
    mapping: dict[tuple[str, str], int] = {}
    for row in rows:
        provider = by_source.get(str(row["source_code"]))
        if provider is not None:
            mapping[(provider, str(row["external_entity_id"]))] = int(row["variant_id"])
    return mapping


def iter_cache_files(g10_root: Path, limit: int | None) -> Iterable[tuple[str, Path]]:
    emitted = 0
    for provider in sorted(PROVIDER_IDENTITY_SOURCE):
        provider_root = g10_root / provider
        if not provider_root.is_dir():
            continue
        for path in sorted(provider_root.glob("*.json")):
            if limit is not None and emitted >= limit:
                return
            emitted += 1
            yield provider, path


def sale_rows_from_document(
    document: Any,
    *,
    variant_id: int,
    external_entity_id: str,
    platforms: Sequence[str],
    fetched_at: datetime,
    payload_sha256: str,
    stats: Counter,
    unknown_grades: Counter,
) -> list[SaleRow]:
    if not isinstance(document, Mapping):
        stats["file_invalid"] += 1
        return []
    sales = document.get("sales")
    if not isinstance(sales, list):
        stats["file_invalid"] += 1
        return []

    wanted = set(platforms)
    rows: list[SaleRow] = []
    for entry in sales:
        if not isinstance(entry, Mapping):
            stats["row_invalid"] += 1
            continue
        platform = str(entry.get("platform") or "").strip()
        if platform not in wanted:
            stats["row_out_of_scope_platform"] += 1
            continue
        stats["row_seen"] += 1

        price = entry.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)):
            stats["row_rejected_price"] += 1
            continue
        try:
            unit_price = Decimal(str(price))
        except InvalidOperation:
            stats["row_rejected_price"] += 1
            continue
        if unit_price <= 0:
            stats["row_rejected_price"] += 1
            continue

        grade_raw = str(entry.get("grade") or "").strip()
        mapped = GRADE_MAP.get(grade_raw)
        if mapped is None:
            stats["row_rejected_grade"] += 1
            unknown_grades[grade_raw] += 1
            continue
        grader_code, grade_label = mapped

        dt_text = str(entry.get("dt") or "").strip()
        day_text = str(entry.get("day") or "").strip()
        sold_at, quality = resolve_sale_date(dt_text)
        stats[f"quality_{quality}"] += 1
        rows.append(
            SaleRow(
                variant_id=variant_id,
                external_entity_id=external_entity_id,
                fingerprint=transaction_fingerprint(
                    variant_id, grader_code, grade_label, platform, dt_text, unit_price
                ),
                grader_code=grader_code,
                grade_label=grade_label,
                platform=platform,
                sold_at=sold_at,
                # 兩個日曆都保住：日後想改規則可以由呢度重算，唔使返去讀 G10。
                source_date_text=f"dt={dt_text}|day={day_text}"[:100],
                fetched_at=fetched_at,
                timestamp_quality=quality,
                unit_price_usd=unit_price,
                source_payload_sha256=payload_sha256,
            )
        )
    return rows


def collect(
    g10_root: Path,
    platforms: Sequence[str],
    identity: Mapping[tuple[str, str], int],
    limit: int | None,
) -> tuple[list[SaleRow], Counter, list[str], list[str], Counter]:
    stats: Counter = Counter()
    unknown_grades: Counter = Counter()
    deduped: dict[str, SaleRow] = {}
    file_hashes: list[str] = []
    unmatched: list[str] = []

    for provider, path in iter_cache_files(g10_root, limit):
        stats["file_seen"] += 1
        stats[f"file_seen_{provider}"] += 1
        external_entity_id = path.stem
        variant_id = identity.get((provider, external_entity_id))
        if variant_id is None:
            stats["file_unmatched"] += 1
            stats[f"file_unmatched_{provider}"] += 1
            unmatched.append(f"{provider}/{external_entity_id}")
            continue
        stats["file_matched"] += 1
        stats[f"file_matched_{provider}"] += 1

        raw = path.read_bytes()
        payload_sha256 = sha256_bytes(raw)
        file_hashes.append(payload_sha256)
        try:
            document = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            stats["file_invalid"] += 1
            continue

        # `updatedAt` 係檔案自己寫低嘅抄寫時間，比 mtime 可信（複製檔案唔會改到佢）。
        fetched_at = parse_iso_utc((document or {}).get("updatedAt") if isinstance(document, Mapping) else "")
        if fetched_at is None:
            fetched_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
            stats["file_updatedat_missing"] += 1

        rows = sale_rows_from_document(
            document,
            variant_id=variant_id,
            external_entity_id=external_entity_id,
            platforms=platforms,
            fetched_at=fetched_at,
            payload_sha256=payload_sha256,
            stats=stats,
            unknown_grades=unknown_grades,
        )
        if rows:
            stats["file_with_rows"] += 1
        for row in rows:
            # fingerprint 已經含 variant_id，所以同一筆成交由兩個 provider 目錄嚟
            # 都會喺呢度收埋一行，唔會因為 external_entity_id 唔同而寫兩次。
            if row.fingerprint in deduped:
                stats["row_fingerprint_collapsed"] += 1
                continue
            deduped[row.fingerprint] = row

    return list(deduped.values()), stats, file_hashes, unmatched, unknown_grades


def table_count(cursor: Any, table: str, source_code: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE source_code = %s", (source_code,))
    return int(cursor.fetchone()["c"])


def write_all(
    connection: Any,
    rows: Sequence[SaleRow],
    file_hashes: Sequence[str],
    observed_count: int,
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"g10_sales_cache|{started_at.isoformat()}")
    effective_at = max(row.fetched_at for row in rows)
    payload_sha256 = sha256_text("\n".join(sorted(row.fingerprint for row in rows)))
    manifest_sha256 = sha256_text("\n".join(sorted(file_hashes)))
    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted
    rejected = max(0, observed_count - len(rows))

    with connection.cursor() as cursor:
        before = {"market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE)}
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (run_key, SOURCE_CODE, effective_at, payload_sha256, manifest_sha256, observed_count, started_at),
        )
        run_id = int(cursor.lastrowid)

        payload = [
            (
                run_id, row.variant_id, SOURCE_CODE, row.external_entity_id, row.fingerprint,
                row.grader_code, row.grade_label, row.sold_at, row.source_date_text, row.fetched_at,
                row.timestamp_quality, money(row.unit_price_usd), money(row.unit_price_usd),
                row.source_payload_sha256, row.coverage_status,
            )
            for row in rows
        ]
        for start in range(0, len(payload), INSERT_CHUNK):
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
                payload[start:start + INSERT_CHUNK],
            )

        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', observed_count=%s, accepted_count=%s,
                quarantined_count=%s, rejected_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (
                observed_count, accepted, quarantined, rejected,
                datetime.now(timezone.utc).replace(tzinfo=None), run_id,
            ),
        )
        after = {"market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE)}
    return {
        "run_id": run_id,
        "run_key": run_key,
        "before": before,
        "after": after,
        "observed": observed_count,
        "accepted": accepted,
        "quarantined": quarantined,
        "rejected": rejected,
    }


def print_report(
    *,
    platforms: Sequence[str],
    stats: Counter,
    rows: Sequence[SaleRow],
    unmatched: Sequence[str],
    unknown_grades: Counter,
    identity_size: int,
    unparsed_ratio: float,
    observed_count: int,
    write_result: Mapping[str, Any] | None,
) -> None:
    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted
    sold = [row.sold_at.date() for row in rows if row.accepted]

    print("=" * 74)
    print(f"G10 sales_cache ingest — {'WRITE' if write_result else 'DRY-RUN'}"
          f"   platforms={','.join(platforms)}  source_code='{SOURCE_CODE}'")
    print("=" * 74)

    print("\n[檔案掃描]")
    print(f"  catalog_source_identity 可用對應 : {identity_size}")
    print(f"  掃到 cache 檔                    : {stats['file_seen']}"
          f"  (snkrdunk {stats['file_seen_snkrdunk']} / altxyz {stats['file_seen_altxyz']})")
    print(f"  對到 variant                     : {stats['file_matched']}"
          f"  (snkrdunk {stats['file_matched_snkrdunk']} / altxyz {stats['file_matched_altxyz']})")
    print(f"  對唔到 variant（skip）           : {stats['file_unmatched']}"
          f"  (snkrdunk {stats['file_unmatched_snkrdunk']} / altxyz {stats['file_unmatched_altxyz']})")
    print(f"  對到而且有 in-scope 成交         : {stats['file_with_rows']}")
    print(f"  檔案格式壞                       : {stats['file_invalid']}")
    print(f"  冇 updatedAt（退回 mtime）       : {stats['file_updatedat_missing']}")
    print(f"  distinct variant 有成交          : {len({row.variant_id for row in rows})}")
    if unmatched:
        print(f"  對唔到樣本（頭 5）: {', '.join(unmatched[:5])}")

    print("\n[成交記錄]")
    print(f"  唔喺 --platforms 範圍（skip）    : {stats['row_out_of_scope_platform']}")
    print(f"  in-scope 掃到 (= observed)       : {observed_count}")
    print(f"  價格唔合法 rejected              : {stats['row_rejected_price']}")
    print(f"  grade 對唔到 rejected            : {stats['row_rejected_grade']}")
    print(f"  entry 格式壞 rejected            : {stats['row_invalid']}")
    print(f"  同一成交重複抄本收埋             : {stats['row_fingerprint_collapsed']}")
    print(f"  最終成 row                       : {len(rows)}")
    if unknown_grades:
        print(f"  !! 未知 grade: {dict(unknown_grades.most_common(10))}")

    print("\n[日期：sold_at 由 `dt` 嚟，`day` 唔做日期用]")
    total_quality = stats["quality_exact_date"] + stats["quality_relative_resolved"] + stats["quality_unparsed"]
    for label, key in (("平台 ISO 日期 exact_date", "quality_exact_date"),
                       ("相對標籤 relative_resolved", "quality_relative_resolved"),
                       ("解析唔到 unparsed", "quality_unparsed")):
        share = (stats[key] / total_quality * 100) if total_quality else 0.0
        print(f"  {label:<30}: {stats[key]:>7}  ({share:5.2f}%)")
    print(f"  unparsed 比例 : {unparsed_ratio * 100:.2f}%  (閘 {UNPARSED_FAIL_RATIO * 100:.0f}%)")
    if sold:
        print(f"  sold_at 範圍  : {min(sold)} → {max(sold)}   distinct 日數 {len(set(sold))}"
              f"   跨度 {(max(sold) - min(sold)).days} 日")

    print("\n[會寫入幾多行]")
    print(f"  market_sale_observation      : {len(rows)}"
          f"   (accepted {accepted} / quarantined {quarantined})")
    print("  market_daily_sales_aggregate : 0  —— 本 pipeline 唔寫（會蓋 sparkline）")
    print("  market_price_observation     : 0  —— 本 pipeline 唔寫（會改前端顯示價）")

    if write_result:
        print("\n[實際入庫]")
        print(f"  run_id  : {write_result['run_id']}")
        print(f"  run_key : {write_result['run_key']}")
        print(f"  market_ingest_run 計數: observed {write_result['observed']} / "
              f"accepted {write_result['accepted']} / quarantined {write_result['quarantined']} / "
              f"rejected {write_result['rejected']}")
        table = "market_sale_observation"
        before = write_result["before"][table]
        after = write_result["after"][table]
        print(f"  {table:<30} source_code='{SOURCE_CODE}': {before} → {after}  (+{after - before})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest local G10 cumulative sales_cache history into CARDZ MySQL"
    )
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument(
        "--platforms",
        default=",".join(DEFAULT_PLATFORMS),
        help="要入嘅 platform（預設 snkrdunk；ebay 同 run 70/71 重疊 93%%，唔建議）",
    )
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個 cache 檔")
    add_connection_args(parser)
    args = parser.parse_args()

    platforms = tuple(item.strip() for item in str(args.platforms).split(",") if item.strip())
    if not platforms:
        print("--platforms 唔可以空", file=sys.stderr)
        return 1

    g10_root = args.g10_root.resolve()
    if not g10_root.is_dir():
        print(f"G10 sales_cache root not found: {g10_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        identity = load_identity_map(connection)
        rows, stats, file_hashes, unmatched, unknown_grades = collect(
            g10_root, platforms, identity, args.limit
        )
        observed_count = stats["row_seen"]
        total_quality = (
            stats["quality_exact_date"] + stats["quality_relative_resolved"] + stats["quality_unparsed"]
        )
        unparsed_ratio = (stats["quality_unparsed"] / total_quality) if total_quality else 0.0

        report = dict(
            platforms=platforms, stats=stats, rows=rows, unmatched=unmatched,
            unknown_grades=unknown_grades, identity_size=len(identity),
            unparsed_ratio=unparsed_ratio, observed_count=observed_count,
        )

        if not rows:
            print_report(write_result=None, **report)
            print("\n!! 冇任何成交行，唔會寫入。", file=sys.stderr)
            return 1
        if unknown_grades:
            print_report(write_result=None, **report)
            print(
                f"\n!! FAIL-CLOSED: 見到未知 grade {sorted(unknown_grades)}，"
                "唔入庫。要先喺 GRADE_MAP 加對應，唔准撞彩填。",
                file=sys.stderr,
            )
            return 1
        if unparsed_ratio > UNPARSED_FAIL_RATIO:
            print_report(write_result=None, **report)
            print(
                f"\n!! FAIL-CLOSED: unparsed 比例 {unparsed_ratio * 100:.2f}% "
                f"> {UNPARSED_FAIL_RATIO * 100:.0f}%，唔入庫。日期規則要先修。",
                file=sys.stderr,
            )
            return 1

        write_result = None
        if args.write:
            try:
                write_result = write_all(connection, rows, file_hashes, observed_count)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        print_report(write_result=write_result, **report)
        if not args.write:
            print("\n(dry-run — 加 --write 先真入庫)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
