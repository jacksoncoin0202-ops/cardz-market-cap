#!/usr/bin/env python3
"""把本機 G10 (`../grade10-scraper`) 嘅 **SNKRDUNK 非 PSA10 grade** 成交入庫。

`g10_ebay_ingest.py` 接咗 `ebay_*.json`；`market_source_observation` 嘅
`snk_psa10` 源（268,135 行 / 2023-06-19 → 2026-07-24）係 `apparel_grade_22`
嗰條線。G10 每張卡仲有另外 12 個 `apparel_grade_{N}.json` 從來冇人讀過。
呢個 loader 就係嗰 12 個檔。

## N → grade 對照表（由數據證實，唔係靠檔名估）

逐個 `apparel_grade_{N}.json` 統計 `saleHistory[].grade` 嘅實際取值分佈
（480 個 snkrdunk 目錄 + 161 個 altxyz 目錄，共 2,858 個檔 / 27,458 條成交）。
除咗 `-1` 之外，**每個 N 都係 100.0% 單一標籤**，冇一個 N 出現多過一種：

    N     saleHistory[].grade   純度      本檔 grader/grade      本 loader
    ----  --------------------  --------  ---------------------  ------------------
    20    'C'                   100.00%   raw   / C              ingest
    21    'D'                   100.00%   raw   / D              ingest
    22    'PSA10'               100.00%   psa   / 10             SKIP（= snk_psa10）
    23    'PSA9'                100.00%   psa   / 9              ingest
    24    'PSA8以下'            100.00%   psa   / 8_OR_LOWER     ingest
    25    'BGS10 BL'            100.00%   bgs   / 10_BL          ingest
    26    'BGS10 GL'            100.00%   bgs   / 10_GL          ingest
    27    'BGS9.5'              100.00%   bgs   / 9.5            ingest
    28    'BGS9以下'            100.00%   bgs   / 9_OR_LOWER     ingest
    29    'ARS10+'              100.00%   ars   / 10_PLUS        ingest
    30    'ARS10'               100.00%   ars   / 10             ingest
    31    'ARS9'                100.00%   ars   / 9              ingest
    32    'ARS8以下'            100.00%   ars   / 8_OR_LOWER     ingest
    -1    **混合 15 種標籤**    —         逐行按自己嘅 grade 欄  ingest（PSA10 除外）

`grade_label` 用 ASCII 化寫法（`8_OR_LOWER` 而唔係 `8以下`）——
`market_sale_observation.grade_label` 係 varchar(32) utf8mb4，日文入到，但
落 SQL 條件同睇 log 嗰陣全形字好易撞 collation 同 terminal 編碼，冇著數。
原文標籤保喺 `SNKRDUNK_LABELS` 做唯一真源。

## `apparel_grade_-1` 到底係咩：**唔係 raw，係「全 grade 未過濾」嘅第一頁**

個名好易當佢係「未分級」，實測唔係：

  * 佢 `saleHistory` 撈埋 15 種標籤，PSA10 佔 72.89%、PSA9 10.82%、
    raw 條件級 A/B/C/D 佔 11.75%，仲有 ARS/BGS。**冇一種標籤佔優**，
    所以佢係「唔揀 grade」嗰個 tab，唔係某一個 grade。
  * 480 個 snkrdunk 目錄入面，`-1` 得 8,491 條，而 12 個專屬 grade 檔嘅
    union 有 25,806 條。`-1` 只係 union 嘅 29.49%，**唔係 superset**
    （480 個目錄得 20 個 `-1 ⊇ union`）——即係佢俾 cap 咗，得頭 ~20 條，
    典型嘅「第一頁」行為。
  * 反方向：`-1` 有 89.6%（7,611/8,491）行喺專屬 grade 檔搵得返，
    當中 `grade_22` 有 73.86%（6,040/8,178）行喺 `-1` 出現。
    **即係照抄 `-1` 落庫會即刻同 PSA10 嗰條線撞行。**

處理方法：`-1` 逐行睇佢**自己** `grade` 欄嚟定 grader/label，唔靠檔名。
標籤係 `PSA10` 嘅一律唔要（`row_excluded_psa10`），其餘照入；同專屬 grade 檔
重覆嗰啲會因為 fingerprint 一樣而自動歸一。`-1` 淨賺嘅係 A / B / 他鑑定品
呢三種**冇專屬檔**嘅標籤。

## 日期解析

同 `g10_ebay_ingest.py` 一樣撈埋 ISO 同相對格式，但呢批檔**多咗兩種**
eBay 檔冇嘅寫法，唔加就 4.56% 解析唔到（貼住 5% 閘）：

    ISO   `YYYY-MM-DD`      23,305   84.875%
    日    `N day(s) ago`     2,900   10.562%
    時    `N hour(s) ago`    1,240    4.516%   <- eBay loader 冇呢個
    分    `N minute(s) ago`     13    0.047%   <- eBay loader 冇呢個
    解唔到                       0    0.000%

相對日期基準用**檔案 mtime 嘅 UTC 曆日**，唔用 `datetime.now()`。實測
（gap = 最舊相對日 − 最新 ISO 日，越接近 1 代表兩段接得越密冇縫）：

    base = mtime UTC 曆日   眾數 gap = 1 日（134 個檔）  <- 用呢個
    base = mtime 本機曆日   眾數 gap = 2 日（134 個檔）  <- 系統性缺一日

`N hours/minutes ago` 由 mtime 嘅 UTC **時刻**減落去再取曆日（唔係由曆日減），
因為佢本身就係一日之內嘅偏移。

## 卡片對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名。
  * `source_code='snkrdunk'` 的 `external_entity_id` = `data/cards/snkrdunk/{id}/`
  * `source_code='ebay'`     的 `external_entity_id` = `data/cards/altxyz/{UUID}/`
identity 之後擴充咗，直接重跑就會自動撿返新對到嘅卡（fingerprint 穩定，
已入嘅行唔會重複）。

## 只寫一張表：`market_sale_observation`

**唔寫** `market_daily_sales_aggregate`：`canonical_public_snapshot.py`
`daily_history()` 讀嗰張表**冇 source filter**（只 `ORDER BY observed_date,id`，
再 last-row-wins 入 `sales_by_day`），寫落去會蓋咗前端 sparkline 嘅成交數。

**唔寫** `market_price_observation`：嗰張表嘅 UNIQUE key 係
`(variant_id, source_code, observed_date)` —— **冇 grade 維度**，12 個 grade
根本冧唔入一個 source_code。而 `daily_history()` 揀價係
`ORDER BY observed_date DESC, source_priority ASC, ...` 再逐 (variant, day)
只留第一行，`source_priority` **細嘅贏**（`ebay`=100 贏 `snk_psa10`=200）。
就算我開一個 priority 900 嘅新源，priority 只喺**同一 (variant, day)** 之內
分先後：實測我呢批數據有 13,763 個 (variant, day)，其中 **4,987 個（36.2%）
喺 `market_price_observation` 完全冇任何一行**——嗰啲日子我寫落去就會變咗
嗰日**唯一**嘅價，即係將 PSA9 / BGS / ARS / raw 條件價直接當代表價塞入
sparkline 同市值。所以呢個 loader 一行 price 都唔寫。

`market_sale_observation` 有 `grader_code` + `grade_label` 兩個欄，本身就係
per-grade 嘅 fact table，而且 `canonical_public_snapshot.py` 完全冇讀佢——
12 個 grade 全部放呢度先係啱位。

## `quantity` / `transaction_value_usd`

實測 27,458 條入面 271 條 `price != txAmount`，而 **271/271 都啱
`txAmount == price × bundleSize`**。所以
`unit_price_usd=price`、`quantity=bundleSize`、`transaction_value_usd=txAmount`。
（`g10_ebay_ingest.py` 硬寫 `quantity=1` 係因為佢啲檔冇 bundleSize。）
`recentHistory` 2,858 個檔全部係空 list，唔理。

Exit codes: 0 = 正常, 1 = unparsed 爆閘 / 冇嘢做, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "cards"

# 同 `snkrdunk`（identity / market_source_observation）同 `snk_psa10`（grade_22
# 嗰條 268,135 行嘅線）都分得開，唔會撞任何現有 UNIQUE key。
SOURCE_CODE = "snk_grade"

# G10 目錄 provider → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}

# `saleHistory[].grade` 原文標籤 → (grader_code, grade_label)。
# 原文係唯一真源；ASCII 化只係為咗落 SQL / 睇 log 方便。
SNKRDUNK_LABELS: dict[str, tuple[str, str]] = {
    "PSA10": ("psa", "10"),
    "PSA9": ("psa", "9"),
    "PSA8以下": ("psa", "8_OR_LOWER"),
    "BGS10 BL": ("bgs", "10_BL"),
    "BGS10 GL": ("bgs", "10_GL"),
    "BGS9.5": ("bgs", "9.5"),
    "BGS9以下": ("bgs", "9_OR_LOWER"),
    "ARS10+": ("ars", "10_PLUS"),
    "ARS10": ("ars", "10"),
    "ARS9": ("ars", "9"),
    "ARS8以下": ("ars", "8_OR_LOWER"),
    # raw（未送評）條件級，SNKRDUNK 自己俾嘅
    "A": ("raw", "A"),
    "B": ("raw", "B"),
    "C": ("raw", "C"),
    "D": ("raw", "D"),
    # 其他評級行（PSA/BGS/ARS 以外），SNKRDUNK 唔再細分
    "他鑑定品": ("other", "OTHER"),
}

# N → 該檔應該淨係出呢個標籤（`-1` 係混合，冇 expected）。純度實測 100%，
# 所以任何唔對嘅行都當 anomaly 報出嚟，唔會夾硬歸一。
GRADE_FILE_EXPECTED_LABEL: dict[str, str] = {
    "20": "C",
    "21": "D",
    "22": "PSA10",
    "23": "PSA9",
    "24": "PSA8以下",
    "25": "BGS10 BL",
    "26": "BGS10 GL",
    "27": "BGS9.5",
    "28": "BGS9以下",
    "29": "ARS10+",
    "30": "ARS10",
    "31": "ARS9",
    "32": "ARS8以下",
}

MIXED_GRADE_KEY = "-1"
# grade_22 已經係 `snk_psa10`（268,135 行）。整個檔唔讀，而 `-1` 入面標籤
# 係 PSA10 嗰啲行亦都要剔走 —— 佢哋有 73.86% 同 grade_22 重覆。
PSA10_LABEL = "PSA10"
SKIPPED_GRADE_KEYS = frozenset({"22"})

INGEST_GRADE_KEYS: tuple[str, ...] = (MIXED_GRADE_KEY,) + tuple(
    key for key in sorted(GRADE_FILE_EXPECTED_LABEL, key=int) if key not in SKIPPED_GRADE_KEYS
)

GRADE_FILENAME = "apparel_grade_{key}.json"
GRADE_FILE_RE = re.compile(r"^apparel_grade_(-?\d+)\.json$")

ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
RELATIVE_DAYS = re.compile(r"^(\d+)\s+days?\s+ago$", re.IGNORECASE)
RELATIVE_HOURS = re.compile(r"^(\d+)\s+hours?\s+ago$", re.IGNORECASE)
RELATIVE_MINUTES = re.compile(r"^(\d+)\s+minutes?\s+ago$", re.IGNORECASE)

UNPARSED_FAIL_RATIO = 0.05
CENT = Decimal("0.000001")

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


def resolve_sale_date(source_date_text: str, fetched_at: datetime) -> tuple[datetime | None, str]:
    """回 (sold_at, timestamp_quality)。`fetched_at` 係檔案 mtime 嘅 UTC naive 時刻。

    `N day(s) ago` 由 mtime 嘅**曆日**減；`N hour(s)/minute(s) ago` 由 mtime 嘅
    **時刻**減完再取曆日 —— 佢哋本身就係一日之內嘅偏移，用曆日減會多減一日。
    """

    raw = str(source_date_text or "").strip()
    if ISO_DATE.match(raw):
        try:
            return datetime.combine(date.fromisoformat(raw), time.min), "exact_date"
        except ValueError:
            return None, "unparsed"

    days = RELATIVE_DAYS.match(raw)
    if days:
        return datetime.combine(fetched_at.date() - timedelta(days=int(days.group(1))), time.min), "relative_resolved"

    hours = RELATIVE_HOURS.match(raw)
    if hours:
        moment = fetched_at - timedelta(hours=int(hours.group(1)))
        return datetime.combine(moment.date(), time.min), "relative_subday"

    minutes = RELATIVE_MINUTES.match(raw)
    if minutes:
        moment = fetched_at - timedelta(minutes=int(minutes.group(1)))
        return datetime.combine(moment.date(), time.min), "relative_subday"

    return None, "unparsed"


def transaction_fingerprint(
    external_entity_id: str,
    grader_code: str,
    grade_label: str,
    source_date_text: str,
    unit_price_usd: Decimal,
    quantity: int,
) -> str:
    """用**原始 date 字串**，唔用解析後嘅 sold_at —— 相對日期嘅解析結果會隨 mtime 變，
    用原文先至重跑 idempotent。

    `quantity` 要入 fingerprint：同日同單價但 bundleSize 唔同係兩單唔同交易
    （實測 271 條 bundleSize > 1）。同一筆成交喺 `-1` 同專屬 grade 檔各出現一次
    嗰陣，六個欄全部一樣，自然歸一。
    """

    return sha256_text(
        f"{external_entity_id}|{grader_code}|{grade_label}|{source_date_text}"
        f"|{money(unit_price_usd)}|{quantity}"
    )


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
    quantity: int
    transaction_value_usd: Decimal
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
    fetched_at: datetime,
    payload_sha256: str,
    stats: Counter,
    seen: set[str] | None = None,
) -> list[SaleRow]:
    """由一個 `apparel_grade_{N}.json` 抽成交行。

    grader/grade **一律由每行自己嘅 `grade` 欄決定**，唔靠檔名 —— `-1` 係混合檔，
    而專屬檔嘅純度亦都靠呢度對返 `GRADE_FILE_EXPECTED_LABEL` 嚟驗。
    """

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

    expected_label = GRADE_FILE_EXPECTED_LABEL.get(grade_key)
    rows: list[SaleRow] = []
    if seen is None:
        seen = set()

    for entry in history:
        if not isinstance(entry, Mapping):
            stats["row_invalid"] += 1
            continue
        stats["row_seen"] += 1

        label = str(entry.get("grade") or "").strip()
        if expected_label is not None and label != expected_label:
            # 純度實測 100%，唔對即係源頭變咗契約。如實計數，唔夾硬歸一。
            stats["row_label_mismatch"] += 1
            stats[f"row_label_mismatch_{grade_key}"] += 1
        mapped = SNKRDUNK_LABELS.get(label)
        if mapped is None:
            stats["row_rejected_unknown_label"] += 1
            stats[f"row_unknown_label::{label}"] += 1
            continue
        grader_code, grade_label = mapped
        if label == PSA10_LABEL:
            # grade_22 = `snk_psa10` 嘅地盤（268,135 行）。`-1` 入面 73.86% PSA10
            # 行同佢重覆，照入就係 double count。
            stats["row_excluded_psa10"] += 1
            continue

        price = entry.get("price")
        if isinstance(price, bool) or not isinstance(price, (int, float)) or price <= 0:
            stats["row_rejected_price"] += 1
            continue
        currency = str(entry.get("currency") or "").strip().lower()
        if currency != "usd":
            # 唔准估匯率填 unit_price_usd（NOT NULL），寧願唔要。
            stats["row_rejected_currency"] += 1
            continue

        bundle = entry.get("bundleSize")
        if isinstance(bundle, bool) or not isinstance(bundle, int) or bundle <= 0:
            quantity = 1
            stats["row_bundle_defaulted"] += 1
        else:
            quantity = bundle

        unit_price = Decimal(str(price))
        tx_amount = entry.get("txAmount")
        if isinstance(tx_amount, bool) or not isinstance(tx_amount, (int, float)) or tx_amount <= 0:
            transaction_value = (unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)
            stats["row_txamount_derived"] += 1
        else:
            transaction_value = Decimal(str(tx_amount)).quantize(CENT, rounding=ROUND_HALF_UP)

        source_date_text = str(entry.get("date") or "").strip()[:100]
        sold_at, quality = resolve_sale_date(source_date_text, fetched_at)
        stats[f"quality_{quality}"] += 1

        fingerprint = transaction_fingerprint(
            external_entity_id, grader_code, grade_label, source_date_text, unit_price, quantity
        )
        if fingerprint in seen:
            # 同卡同 grade 同日同價同 bundle 嘅兩筆成交會撞同一個 fingerprint，
            # UNIQUE KEY 只留一行。`-1` 同專屬 grade 檔嘅重覆行亦係喺呢度歸一。
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
                quantity=quantity,
                transaction_value_usd=transaction_value,
                source_payload_sha256=payload_sha256,
            )
        )
    return rows


def load_identity_map(connection: Any) -> dict[tuple[str, str], int]:
    """(G10 provider 目錄, 目錄名) → variant_id。唯一合法對應路徑，唔准靠卡名。"""

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
        if provider is None:
            continue
        mapping[(provider, str(row["external_entity_id"]))] = int(row["variant_id"])
    return mapping


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
    grade_keys: Sequence[str],
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

        # 同一張卡跨檔共用 —— `-1` 同專屬 grade 檔嘅重覆行要喺呢度歸一。
        card_seen: set[str] = set()
        matched_any_file = False
        for grade_key in grade_keys:
            path = card_dir / GRADE_FILENAME.format(key=grade_key)
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
                    fetched_at=fetched_at,
                    payload_sha256=payload_sha256,
                    stats=stats,
                    seen=card_seen,
                )
            )
            stats[f"rows_{grade_key}"] += len(rows) - before
        if matched_any_file:
            stats["dir_with_data"] += 1
    return rows, stats, file_hashes, unmatched


def table_count(cursor: Any, table: str, source_code: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE source_code = %s", (source_code,))
    return int(cursor.fetchone()["c"])


def write_all(
    connection: Any,
    rows: Sequence[SaleRow],
    file_hashes: Sequence[str],
    stats: Mapping[str, int],
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
    effective_at = max(row.fetched_at for row in rows)
    payload_sha256 = sha256_text("\n".join(sorted(row.fingerprint for row in rows)))
    manifest_sha256 = sha256_text("\n".join(sorted(file_hashes)))

    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted
    # observed = 對到 variant 嘅檔入面掃到嘅每一條 saleHistory 記錄。
    # rejected 係「掃到但冇成行」嗰啲（PSA10 剔走 / 未知標籤 / 價唔合法 /
    # 非 USD / fingerprint 撞冧），令 observed == accepted + quarantined + rejected。
    observed = int(stats.get("row_seen", 0))
    rejected = observed - accepted - quarantined

    with connection.cursor() as cursor:
        before = {"market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE)}
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (run_key, SOURCE_CODE, effective_at, payload_sha256, manifest_sha256, observed, started_at),
        )
        run_id = int(cursor.lastrowid)

        cursor.executemany(
            """
            INSERT INTO market_sale_observation
                (run_id, variant_id, source_code, external_entity_id, transaction_fingerprint,
                 grader_code, grade_label, sold_at, source_date_text, fetched_at, timestamp_quality,
                 unit_price_usd, quantity, transaction_value_usd, source_payload_sha256, coverage_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                    row.timestamp_quality, money(row.unit_price_usd), row.quantity,
                    money(row.transaction_value_usd), row.source_payload_sha256, row.coverage_status,
                )
                for row in rows
            ],
        )
        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', observed_count=%s, accepted_count=%s,
                quarantined_count=%s, rejected_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (
                observed, accepted, quarantined, max(rejected, 0),
                datetime.now(timezone.utc).replace(tzinfo=None), run_id,
            ),
        )
        after = {"market_sale_observation": table_count(cursor, "market_sale_observation", SOURCE_CODE)}
    return {
        "run_id": run_id,
        "run_key": run_key,
        "before": before,
        "after": after,
        "observed": observed,
        "accepted": accepted,
        "quarantined": quarantined,
        "rejected": max(rejected, 0),
    }


def print_report(
    *,
    grade_keys: Sequence[str],
    stats: Counter,
    rows: Sequence[SaleRow],
    unmatched: Sequence[str],
    identity_size: int,
    unparsed_ratio: float,
    write_result: Mapping[str, Any] | None,
) -> None:
    accepted = sum(1 for row in rows if row.accepted)
    quarantined = len(rows) - accepted
    sold = [row.sold_at.date() for row in rows if row.accepted]

    print("=" * 78)
    print(f"G10 SNKRDUNK grades ingest — {'WRITE' if write_result else 'DRY-RUN'}")
    print(f"source_code={SOURCE_CODE}   grades={','.join(grade_keys)}   (grade_22=PSA10 由 snk_psa10 負責，唔掂)")
    print("=" * 78)

    print("\n[目錄掃描]")
    print(f"  catalog_source_identity 可用對應   : {identity_size}")
    print(f"  掃到目錄                           : {stats['dir_seen']}"
          f"  (altxyz {stats['dir_seen_altxyz']} / snkrdunk {stats['dir_seen_snkrdunk']})")
    print(f"  對到 variant                       : {stats['dir_matched']}"
          f"  (altxyz {stats['dir_matched_altxyz']} / snkrdunk {stats['dir_matched_snkrdunk']})")
    print(f"  對唔到 variant（skip）             : {stats['dir_unmatched']}"
          f"  (altxyz {stats['dir_unmatched_altxyz']} / snkrdunk {stats['dir_unmatched_snkrdunk']})")
    print(f"  對到而且有 grade 檔                : {stats['dir_with_data']}")
    print(f"  distinct variant 有成交            : {len({row.variant_id for row in rows})}")
    if unmatched:
        print(f"  對唔到樣本（頭 5）: {', '.join(unmatched[:5])}")

    print("\n[逐 grade 檔案]")
    print(f"  {'N':<5} {'expected label':<12} {'讀到檔':>7} {'缺檔':>7} {'成行':>8} {'標籤唔對':>9}")
    for grade_key in grade_keys:
        expected = GRADE_FILE_EXPECTED_LABEL.get(grade_key, "<mixed>")
        print(f"  {grade_key:<5} {expected:<12} {stats[f'file_read_{grade_key}']:>7} "
              f"{stats[f'file_missing_{grade_key}']:>7} {stats[f'rows_{grade_key}']:>8} "
              f"{stats[f'row_label_mismatch_{grade_key}']:>9}")
    print(f"  檔案無 saleHistory : {stats['file_no_history']}   檔案格式壞 : {stats['file_invalid']}")

    print("\n[成交記錄]")
    print(f"  saleHistory 掃到              : {stats['row_seen']}")
    print(f"  PSA10 剔走（歸 snk_psa10）    : {stats['row_excluded_psa10']}")
    print(f"  未知 grade 標籤 rejected      : {stats['row_rejected_unknown_label']}")
    print(f"  價格唔合法 rejected           : {stats['row_rejected_price']}")
    print(f"  非 USD rejected               : {stats['row_rejected_currency']}")
    print(f"  fingerprint 撞冧歸一          : {stats['row_fingerprint_collision']}")
    print(f"  bundleSize 缺 → 當 1          : {stats['row_bundle_defaulted']}")
    print(f"  txAmount 缺 → 用 price×qty    : {stats['row_txamount_derived']}")
    print(f"  最終成 row                    : {len(rows)}")
    unknown = sorted(
        ((key.split("::", 1)[1], value) for key, value in stats.items() if key.startswith("row_unknown_label::")),
        key=lambda item: -item[1],
    )
    if unknown:
        print("  未知標籤明細 : " + ", ".join(f"{label!r}×{count}" for label, count in unknown[:10]))

    print("\n[grade 分佈（最終行）]")
    per_grade: Counter = Counter()
    for row in rows:
        per_grade[(row.grader_code, row.grade_label)] += 1
    for (grader_code, grade_label), count in sorted(per_grade.items(), key=lambda item: -item[1]):
        print(f"  {grader_code:<6} {grade_label:<12} {count:>8}")

    print("\n[日期格式分佈]")
    total_quality = (
        stats["quality_exact_date"] + stats["quality_relative_resolved"]
        + stats["quality_relative_subday"] + stats["quality_unparsed"]
    )
    for label, key in (
        ("ISO exact_date", "quality_exact_date"),
        ("相對日 relative_resolved", "quality_relative_resolved"),
        ("相對時分 relative_subday", "quality_relative_subday"),
        ("解析唔到 unparsed", "quality_unparsed"),
    ):
        share = (stats[key] / total_quality * 100) if total_quality else 0.0
        print(f"  {label:<28}: {stats[key]:>7}  ({share:5.2f}%)")
    print(f"  unparsed 比例 : {unparsed_ratio * 100:.2f}%  (閘 {UNPARSED_FAIL_RATIO * 100:.0f}%)")
    if sold:
        print(f"  sold_at 範圍  : {min(sold)} → {max(sold)}   distinct 日數 {len(set(sold))}")

    print("\n[會寫入幾多行]")
    print(f"  market_sale_observation      : {len(rows)}"
          f"   (accepted {accepted} / quarantined {quarantined})")
    print("  market_daily_sales_aggregate : 0   — 唔寫，snapshot 讀嗰張冇 source filter")
    print("  market_price_observation     : 0   — 唔寫，UNIQUE key 冇 grade 維度，"
          "而 36.2% (variant,day) 冇任何其他源，寫落去會變咗代表價")

    if write_result:
        print("\n[實際入庫]")
        print(f"  run_id  : {write_result['run_id']}")
        print(f"  run_key : {write_result['run_key']}")
        print(f"  market_ingest_run counters: observed={write_result['observed']} "
              f"accepted={write_result['accepted']} quarantined={write_result['quarantined']} "
              f"rejected={write_result['rejected']}")
        for table in ("market_sale_observation",):
            before = write_result["before"][table]
            after = write_result["after"][table]
            print(f"  {table:<30} source_code='{SOURCE_CODE}': {before} → {after}  (+{after - before})")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest local G10 SNKRDUNK non-PSA10 grade sale history into CARDZ MySQL"
    )
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument(
        "--grades",
        default=",".join(INGEST_GRADE_KEYS),
        help="apparel_grade_{N} 嘅 N，逗號分隔。預設 12 個未接嘅 grade（唔包 22）。",
    )
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個卡目錄")
    add_connection_args(parser)
    args = parser.parse_args()

    grade_keys = [item.strip() for item in str(args.grades).split(",") if item.strip()]
    forbidden = [key for key in grade_keys if key in SKIPPED_GRADE_KEYS]
    if forbidden:
        print(
            f"grade {','.join(forbidden)} 係 snk_psa10 嘅地盤（268,135 行），呢個 loader 唔准掂。",
            file=sys.stderr,
        )
        return 1
    unknown = [key for key in grade_keys if key != MIXED_GRADE_KEY and key not in GRADE_FILE_EXPECTED_LABEL]
    if unknown:
        print(
            f"unknown grade key(s): {', '.join(unknown)}; valid: {', '.join(INGEST_GRADE_KEYS)}",
            file=sys.stderr,
        )
        return 1

    g10_root = args.g10_root.resolve()
    if not g10_root.is_dir():
        print(f"G10 card root not found: {g10_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        identity = load_identity_map(connection)
        rows, stats, file_hashes, unmatched = collect(g10_root, grade_keys, identity, args.limit)
        total_quality = (
            stats["quality_exact_date"] + stats["quality_relative_resolved"]
            + stats["quality_relative_subday"] + stats["quality_unparsed"]
        )
        unparsed_ratio = (stats["quality_unparsed"] / total_quality) if total_quality else 0.0

        report_kwargs = dict(
            grade_keys=grade_keys, stats=stats, rows=rows, unmatched=unmatched,
            identity_size=len(identity), unparsed_ratio=unparsed_ratio,
        )

        if not rows:
            print_report(write_result=None, **report_kwargs)
            print("\n!! 冇任何成交行，唔會寫入。", file=sys.stderr)
            return 1

        if unparsed_ratio > UNPARSED_FAIL_RATIO:
            print_report(write_result=None, **report_kwargs)
            print(
                f"\n!! FAIL-CLOSED: unparsed 比例 {unparsed_ratio * 100:.2f}% "
                f"> {UNPARSED_FAIL_RATIO * 100:.0f}%，唔入庫。日期規則要先修。",
                file=sys.stderr,
            )
            return 1

        write_result = None
        if args.write:
            try:
                write_result = write_all(connection, rows, file_hashes, stats)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        print_report(write_result=write_result, **report_kwargs)
        if not args.write:
            print("\n(dry-run — 加 --write 先真入庫)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
