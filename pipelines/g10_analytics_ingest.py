#!/usr/bin/env python3
"""把 G10 (`../grade10-scraper`) 已經算好嘅指標、逐日 K 線同指數入庫。

點解要有呢個檔：G10 `data/analytics/` 入面有 641 行卡片指標（rank / weight /
indexPrice / change1d,7d,30d / volume1d,7d,30d）同 636 個逐日 OHLC 檔（47,582 條
日線，2023-07-20 → 2026-07-25），`data/index/` 仲有三條指數嘅 value / 成份 /
走勢。呢批數就係前端而家寫死 null 嗰幾格（POP delta 除外）同 sparkline 嘅真數據。
數據一早喺本機硬碟，冇人讀過。

## 落地策略（點解唔寫現有嘅 aggregate 表）

* `market_daily_sales_aggregate` —— **唔准寫**。`canonical_public_snapshot.daily_history()`
  讀嗰張表**冇 source filter**（`canonical_public_snapshot.py:591-599`，
  `ORDER BY observed_date,id` 最後一行贏），重疊日子會令 historyDaily 靜靜地變成
  後入嗰個源。G10 K 線同 snk/ebay 成交日子大幅重疊，寫落去即刻污染。
* `market_tracked_sales_aggregate` —— **一樣唔寫**。表面睇佢啱曬（`window_code` /
  `window_start_at` / `window_end_at` / `sales_count` / `sales_value_usd` 完全對到
  `card_metrics.json` 嘅 `volume{1d,7d,30d}` + `volume{…}Usd`），但係：
  (1) 2026-07-26 已經有人將 `latest_sales()` 由呢張表改去讀
      `market_daily_sales_aggregate`，`scripts/audit_wiring_gaps.py:89` 白紙黑字
      寫住「唔准改返去讀 market_tracked_sales_aggregate」——即係佢已經冇 consumer；
  (2) 佢**冇 `source_code` 欄**，多過一個源寫落去就分唔到邊行邊個源出，正正就係
      `market_daily_sales_aggregate` 而家中緊嗰個病。
  寫落去係零收益、有風險，所以窗口成交量同樣淨係落 ledger。
  `tracked_sales_rows()` 保留住做「一旦有咗啱嘅表，欄位點對」嘅可執行規格。
* **逐日 OHLC 一樣冇合適嘅表**。`market_price_observation` 只放單一代表價，塞 OHLC
  會撞現有 `snk_psa10` / `ebay` 嘅 `uq_market_price_daily`。

所以呢個 loader **淨係寫兩張表**：`market_ingest_run`（provenance）同
`market_source_observation`（原封 payload）。新表 DDL 喺交付報告提出，唔喺度偷雞
建表。

## `carried` 唔准當成交（實測推翻直覺）

CSV 有 `tx` 同 `carried` 兩欄。直覺以為 `carried=1` ⟺ `tx=0`，**實測係錯**：
47,582 條入面 `carried=1 & tx>0` 有 **1,667 條**。而 G10 自己個
`klines/index.json` 每張卡嘅 `realDays` 加埋 = **4,357** = `carried=0` 嘅條數，
即係 **G10 本身就係用 `carried=0` 做「真成交日」嘅唯一定義**，唔係 `tx=0`。

所以呢個 loader 只認 `carried=0`（4,357 / 47,582 = 9.2%）做真成交日，
`carried=1` 全部照落 ledger 但標住 `is_carried`，唔准當成交入任何 aggregate。

## G10 id → variant 對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名（641 個目錄得 319 個 unique
卡名）。`card_metrics.json` 嘅 `source` 對 identity 嘅 `source_code`：
`snkrdunk`→`snkrdunk`、`altxyz`→`ebay`。對唔到嘅**照落 ledger**（ledger 唔需要
variant_id），計入 `quarantined_count`，等 identity 擴充之後重跑自動撿返。

`market_source_observation.external_entity_id` 沿用 DB 現行慣例
`{identity_source}:{id}`（實測 277,127 行全部係呢個格式）。

Exit codes: 0 = 正常, 1 = 冇嘢做 / 爆閘, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
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

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data"

# 呢個 loader 自己嘅 producer 名。刻意唔重用 'ebay'/'snkrdunk' —— 佢哋係原始採集
# 器，G10 analytics 係二次計算產物，混埋會令 per-source 生死診斷失真，而且新
# source_code 令下游一句 WHERE 就篩得走（`market_daily_sales_aggregate` 就係因為
# 冇得篩先出事）。
SOURCE_CODE = "g10_analytics"
INDEX_SOURCE_CODE = "g10_index"

# G10 `card_metrics.json` 嘅 source → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"snkrdunk": "snkrdunk", "altxyz": "ebay"}

KIND_CARD_METRICS = "g10_card_metrics"
KIND_KLINE_DAILY = "g10_kline_daily"
KIND_INDEX_SUMMARY = "g10_index_summary"
KIND_INDEX_STATS = "g10_index_stats"
KIND_INDEX_CONSTITUENTS = "g10_index_constituents"
KIND_INDEX_CHART = "g10_index_chart"

GRADER_CODE = "psa"
GRADE_LABEL = "10"
KLINE_SUFFIX = "_PSA_10"
KLINE_GRADE = "PSA 10"

# `card_metrics.json` 嘅窗口成交欄 → `market_tracked_sales_aggregate.window_code`
SALES_WINDOWS: dict[str, tuple[str, str, int]] = {
    "1d": ("volume1d", "volume1dUsd", 1),
    "7d": ("volume7d", "volume7dUsd", 7),
    "30d": ("volume30d", "volume30dUsd", 30),
}

INDEX_CHART_RANGES = ("1W", "1M", "3M", "6M", "YTD", "ALL")
INDEX_CODES = ("ptcg", "ptcg100", "opcg")

# 現行慣例：`canonical_public_snapshot.py:695` 要 coverage == 'partial' 先當 ready。
COVERAGE_PARTIAL = "partial"
CENT = Decimal("0.000001")
BATCH_SIZE = 2000


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def money(value: Decimal) -> str:
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def mtime_utc(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def parse_iso_datetime(value: Any) -> datetime | None:
    """食得 `2026-07-25T21:46:50.660461+00:00` 同 `2026-07-23T00:00:00.000Z`，出 naive UTC。"""

    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def external_key(provider: str, entity_id: str) -> str:
    """DB 現行慣例：`{identity_source}:{id}`（實測 ledger 277,127 行全部呢個格式）。"""

    return f"{PROVIDER_IDENTITY_SOURCE.get(provider, provider)}:{entity_id}"


def split_kline_stem(stem: str) -> tuple[str, str] | None:
    """`altxyz_02c08f51-…_PSA_10` → ('altxyz', '02c08f51-…')。唔係 PSA 10 就唔要。"""

    if not stem.endswith(KLINE_SUFFIX):
        return None
    provider, _, rest = stem.partition("_")
    if not provider or not rest:
        return None
    entity_id = rest[: -len(KLINE_SUFFIX)]
    if not entity_id or provider not in PROVIDER_IDENTITY_SOURCE:
        return None
    return provider, entity_id


@dataclass(frozen=True)
class Observation:
    source_code: str
    external_entity_id: str
    observation_kind: str
    effective_at: datetime
    observed_date: date
    payload: Mapping[str, Any]
    observed_at: datetime

    @property
    def payload_text(self) -> str:
        return canonical_json(self.payload)

    @property
    def payload_sha256(self) -> str:
        return sha256_text(self.payload_text)


@dataclass(frozen=True)
class KlineBar:
    provider: str
    entity_id: str
    bar_date: date
    open_usd: float
    high_usd: float
    low_usd: float
    close_usd: float
    tx: int
    volume_usd: float
    carried: bool

    @property
    def real_trade_day(self) -> bool:
        """G10 自己嘅 `index.json.realDays` 就係數 `carried=0`，唔係數 `tx=0`。

        實測 1,667 條 `carried=1 & tx>0` —— 用 `tx` 判斷會當多咗 1,667 個結轉日
        做真成交日。
        """

        return not self.carried


def parse_kline_row(row: Mapping[str, Any]) -> KlineBar | None:
    """CSV 一行 → KlineBar。任何欄壞咗就出 None（caller 記 rejected）。"""

    try:
        bar_date = date.fromisoformat(str(row["date"]).strip())
        open_usd = float(row["open"])
        high_usd = float(row["high"])
        low_usd = float(row["low"])
        close_usd = float(row["close"])
        tx = int(row["tx"])
        volume_usd = float(row["volumeUsd"])
        carried = str(row["carried"]).strip() == "1"
    except (KeyError, TypeError, ValueError):
        return None
    if low_usd <= 0 or tx < 0 or volume_usd < 0:
        return None
    if not (low_usd <= open_usd <= high_usd and low_usd <= close_usd <= high_usd):
        return None
    return KlineBar(
        provider="",
        entity_id="",
        bar_date=bar_date,
        open_usd=open_usd,
        high_usd=high_usd,
        low_usd=low_usd,
        close_usd=close_usd,
        tx=tx,
        volume_usd=volume_usd,
        carried=carried,
    )


def kline_payload(bar: KlineBar) -> dict[str, Any]:
    """原封保留 CSV 八欄，加返 grade context。"""

    return {
        "date": bar.bar_date.isoformat(),
        "open": bar.open_usd,
        "high": bar.high_usd,
        "low": bar.low_usd,
        "close": bar.close_usd,
        "tx": bar.tx,
        "volumeUsd": bar.volume_usd,
        "carried": 1 if bar.carried else 0,
        "grade": KLINE_GRADE,
    }


def load_kline_file(path: Path, stats: Counter) -> tuple[list[KlineBar], str]:
    """回 (bars, file_sha256)。同一日重複只留第一條（CSV 實測冇重複，防守用）。"""

    raw = path.read_bytes()
    file_sha256 = sha256_bytes(raw)
    parsed = split_kline_stem(path.stem)
    if parsed is None:
        stats["kline_file_unrecognised"] += 1
        return [], file_sha256
    provider, entity_id = parsed
    bars: list[KlineBar] = []
    seen: set[date] = set()
    reader = csv.DictReader(raw.decode("utf-8").splitlines())
    for row in reader:
        stats["kline_row_seen"] += 1
        bar = parse_kline_row(row)
        if bar is None:
            stats["kline_row_rejected"] += 1
            continue
        if bar.bar_date in seen:
            stats["kline_row_duplicate_date"] += 1
            continue
        seen.add(bar.bar_date)
        bars.append(
            KlineBar(
                provider=provider,
                entity_id=entity_id,
                bar_date=bar.bar_date,
                open_usd=bar.open_usd,
                high_usd=bar.high_usd,
                low_usd=bar.low_usd,
                close_usd=bar.close_usd,
                tx=bar.tx,
                volume_usd=bar.volume_usd,
                carried=bar.carried,
            )
        )
    return bars, file_sha256


def metrics_as_of(analytics_root: Path) -> datetime:
    """`summary.json.generatedAt` 係整批指標嘅基準時間；冇就用 card_metrics.json mtime。"""

    summary_path = analytics_root / "summary.json"
    if summary_path.is_file():
        try:
            document = json.loads(summary_path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            document = None
        if isinstance(document, Mapping):
            parsed = parse_iso_datetime(document.get("generatedAt"))
            if parsed is not None:
                return parsed
    return mtime_utc(analytics_root / "card_metrics.json")


def metric_observations(
    metrics: Sequence[Mapping[str, Any]], as_of: datetime, fetched_at: datetime, stats: Counter
) -> list[Observation]:
    observations: list[Observation] = []
    for entry in metrics:
        stats["metric_row_seen"] += 1
        if not isinstance(entry, Mapping):
            stats["metric_row_rejected"] += 1
            continue
        provider = str(entry.get("source") or "").strip()
        entity_id = str(entry.get("id") or "").strip()
        if provider not in PROVIDER_IDENTITY_SOURCE or not entity_id:
            stats["metric_row_rejected"] += 1
            continue
        observations.append(
            Observation(
                source_code=SOURCE_CODE,
                external_entity_id=external_key(provider, entity_id),
                observation_kind=KIND_CARD_METRICS,
                effective_at=as_of,
                observed_date=as_of.date(),
                payload=dict(entry),
                observed_at=fetched_at,
            )
        )
    return observations


def kline_observations(bars: Sequence[KlineBar], fetched_at: datetime) -> list[Observation]:
    """一條日線 = 一行 ledger（跟現行 `tracked_sales_daily` 嘅粒度）。

    `effective_at` = 日線自己嗰日 00:00（數據時間），`observed_at` = 檔案 mtime
    （採集時間）—— 同 `db_runtime.py:862,871` 一致。
    """

    return [
        Observation(
            source_code=SOURCE_CODE,
            external_entity_id=external_key(bar.provider, bar.entity_id),
            observation_kind=KIND_KLINE_DAILY,
            effective_at=datetime.combine(bar.bar_date, time.min),
            observed_date=bar.bar_date,
            payload=kline_payload(bar),
            observed_at=fetched_at,
        )
        for bar in bars
    ]


def index_observations(index_root: Path, stats: Counter) -> tuple[list[Observation], list[str]]:
    """三條指數嘅 summary / stats / constituents / chart_*。一個檔一行 ledger。"""

    observations: list[Observation] = []
    hashes: list[str] = []
    for code in INDEX_CODES:
        directory = index_root / code
        if not directory.is_dir():
            stats["index_dir_missing"] += 1
            continue
        stats["index_dir_seen"] += 1
        summary_path = directory / "summary.json"
        as_of: datetime | None = None
        if summary_path.is_file():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                as_of = parse_iso_datetime(summary.get("updatedAt")) if isinstance(summary, Mapping) else None
            except (UnicodeDecodeError, json.JSONDecodeError):
                as_of = None
        if as_of is None:
            as_of = mtime_utc(directory)
        fetched_at = mtime_utc(directory)

        artifacts: list[tuple[str, str, Path]] = [
            (KIND_INDEX_SUMMARY, f"index:{code}", summary_path),
            (KIND_INDEX_STATS, f"index:{code}", directory / "stats.json"),
            (KIND_INDEX_CONSTITUENTS, f"index:{code}", directory / "constituents.json"),
        ]
        artifacts.extend(
            (KIND_INDEX_CHART, f"index:{code}:{window}", directory / f"chart_{window}.json")
            for window in INDEX_CHART_RANGES
        )
        for kind, entity, path in artifacts:
            stats["index_file_seen"] += 1
            if not path.is_file():
                stats["index_file_missing"] += 1
                continue
            raw = path.read_bytes()
            hashes.append(sha256_bytes(raw))
            try:
                document = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                stats["index_file_rejected"] += 1
                continue
            if not isinstance(document, Mapping):
                stats["index_file_rejected"] += 1
                continue
            observations.append(
                Observation(
                    source_code=INDEX_SOURCE_CODE,
                    external_entity_id=entity,
                    observation_kind=kind,
                    effective_at=as_of,
                    observed_date=as_of.date(),
                    payload=dict(document),
                    observed_at=fetched_at,
                )
            )
    return observations, hashes


def window_bounds(end_at: datetime, span_days: int) -> tuple[datetime, datetime]:
    return end_at - timedelta(days=span_days), end_at


def tracked_sales_rows(
    metrics: Sequence[Mapping[str, Any]],
    identity: Mapping[tuple[str, str], int],
    as_of: datetime,
    stats: Counter,
) -> list[dict[str, Any]]:
    """`volume{1d,7d,30d}` + `volume{…}Usd` → `market_tracked_sales_aggregate`。

    只做對到 variant 嗰批（表有 FK 去 `catalog_variant`）。數量或金額任何一邊缺
    就唔寫嗰個窗口 —— 唔准填 0 當「冇成交」，缺失同零係兩件事。
    """

    rows: list[dict[str, Any]] = []
    for entry in metrics:
        if not isinstance(entry, Mapping):
            continue
        provider = str(entry.get("source") or "").strip()
        entity_id = str(entry.get("id") or "").strip()
        identity_source = PROVIDER_IDENTITY_SOURCE.get(provider)
        if identity_source is None or not entity_id:
            continue
        variant_id = identity.get((identity_source, entity_id))
        if variant_id is None:
            continue
        for window_code, (count_field, value_field, span) in SALES_WINDOWS.items():
            count = entry.get(count_field)
            value = entry.get(value_field)
            if isinstance(count, bool) or not isinstance(count, int):
                stats[f"window_missing_{window_code}"] += 1
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                stats[f"window_missing_{window_code}"] += 1
                continue
            if count < 0 or value < 0:
                stats[f"window_rejected_{window_code}"] += 1
                continue
            start_at, end_at = window_bounds(as_of, span)
            sales_value = Decimal(str(value))
            rows.append(
                {
                    "variant_id": variant_id,
                    "grader_code": GRADER_CODE,
                    "grade_label": GRADE_LABEL,
                    "window_code": window_code,
                    "window_start_at": start_at,
                    "window_end_at": end_at,
                    "sales_count": int(count),
                    "sales_value_usd": sales_value,
                    "coverage_status": COVERAGE_PARTIAL,
                    "aggregate_sha256": sha256_text(
                        canonical_json(
                            {
                                "variantId": variant_id,
                                "window": window_code,
                                "endAt": end_at.isoformat(),
                                "count": int(count),
                                "valueUsd": money(sales_value),
                                "source": SOURCE_CODE,
                            }
                        )
                    ),
                    "computed_at": as_of,
                }
            )
            stats[f"window_ready_{window_code}"] += 1
    return rows


def load_identity_map(connection: Any) -> dict[tuple[str, str], int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, source_code, external_entity_id FROM catalog_source_identity "
            "WHERE source_code IN ('ebay','snkrdunk')"
        )
        rows = cursor.fetchall()
    return {(str(row["source_code"]), str(row["external_entity_id"])): int(row["variant_id"]) for row in rows}


@dataclass
class Collected:
    observations: list[Observation]
    bars: list[KlineBar]
    metrics: list[Mapping[str, Any]]
    file_hashes: list[str]
    as_of: datetime
    unmatched_metrics: list[str]
    unmatched_klines: list[str]
    unmatched_bar_count: int


def collect(
    g10_root: Path, identity: Mapping[tuple[str, str], int], stats: Counter, *, limit: int | None
) -> Collected:
    analytics_root = g10_root / "analytics"
    metrics_path = analytics_root / "card_metrics.json"
    metrics_raw = metrics_path.read_bytes()
    metrics_document = json.loads(metrics_raw.decode("utf-8"))
    if not isinstance(metrics_document, list):
        raise ValueError(f"card_metrics.json is not a list: {metrics_path}")
    metrics: list[Mapping[str, Any]] = [entry for entry in metrics_document]

    as_of = metrics_as_of(analytics_root)
    metrics_fetched_at = mtime_utc(metrics_path)
    file_hashes = [sha256_bytes(metrics_raw)]

    observations = metric_observations(metrics, as_of, metrics_fetched_at, stats)
    unmatched_metrics: list[str] = []
    for entry in metrics:
        if not isinstance(entry, Mapping):
            continue
        identity_source = PROVIDER_IDENTITY_SOURCE.get(str(entry.get("source") or "").strip())
        entity_id = str(entry.get("id") or "").strip()
        if identity_source is None or not entity_id:
            continue
        if (identity_source, entity_id) in identity:
            stats["metric_matched"] += 1
        else:
            stats["metric_unmatched"] += 1
            unmatched_metrics.append(f"{identity_source}:{entity_id}")

    bars: list[KlineBar] = []
    unmatched_klines: list[str] = []
    unmatched_bar_count = 0
    kline_root = analytics_root / "klines"
    kline_paths = sorted(kline_root.glob("*.csv")) if kline_root.is_dir() else []
    if limit is not None:
        kline_paths = kline_paths[:limit]
    for path in kline_paths:
        stats["kline_file_seen"] += 1
        file_bars, file_sha256 = load_kline_file(path, stats)
        file_hashes.append(file_sha256)
        if not file_bars:
            stats["kline_file_empty"] += 1
            continue
        bars.extend(file_bars)
        head = file_bars[0]
        identity_source = PROVIDER_IDENTITY_SOURCE[head.provider]
        if (identity_source, head.entity_id) in identity:
            stats["kline_file_matched"] += 1
            stats["kline_bar_matched"] += len(file_bars)
        else:
            stats["kline_file_unmatched"] += 1
            unmatched_bar_count += len(file_bars)
            unmatched_klines.append(f"{identity_source}:{head.entity_id}")

    observations.extend(kline_observations(bars, mtime_utc(kline_root) if kline_root.is_dir() else as_of))

    index_rows, index_hashes = index_observations(g10_root / "index", stats)
    observations.extend(index_rows)
    file_hashes.extend(index_hashes)

    stats["kline_bar_carried"] += sum(1 for bar in bars if bar.carried)
    stats["kline_bar_real"] += sum(1 for bar in bars if bar.real_trade_day)
    stats["kline_bar_carried_with_tx"] += sum(1 for bar in bars if bar.carried and bar.tx > 0)

    return Collected(
        observations=observations,
        bars=bars,
        metrics=metrics,
        file_hashes=file_hashes,
        as_of=as_of,
        unmatched_metrics=unmatched_metrics,
        unmatched_klines=unmatched_klines,
        unmatched_bar_count=unmatched_bar_count,
    )


def chunked(rows: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


def table_count(cursor: Any, sql: str, params: Sequence[Any] = ()) -> int:
    cursor.execute(sql, params)
    return int(cursor.fetchone()["c"])


COUNT_LEDGER = (
    "SELECT COUNT(*) AS c FROM market_source_observation WHERE source_code IN (%s, %s)",
    (SOURCE_CODE, INDEX_SOURCE_CODE),
)
COUNT_TRACKED = ("SELECT COUNT(*) AS c FROM market_tracked_sales_aggregate", ())


def write_all(
    connection: Any,
    collected: Collected,
    counts: Mapping[str, int],
) -> dict[str, Any]:
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
    observations = collected.observations
    effective_at = max(observation.effective_at for observation in observations)
    payload_sha256 = sha256_text("\n".join(sorted(o.payload_sha256 for o in observations)))
    manifest_sha256 = sha256_text("\n".join(sorted(collected.file_hashes)))

    with connection.cursor() as cursor:
        before = {
            "market_source_observation": table_count(cursor, *COUNT_LEDGER),
            "market_tracked_sales_aggregate": table_count(cursor, *COUNT_TRACKED),
        }
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (
                run_key, SOURCE_CODE, effective_at, payload_sha256, manifest_sha256,
                counts["observed"], started_at,
            ),
        )
        run_id = int(cursor.lastrowid)

        # INSERT IGNORE 而唔係 ON DUPLICATE KEY UPDATE：ledger 係 append-only，
        # 重跑唔應該將舊行嘅 run_id 搶過嚟（provenance 要留返俾第一次寫嗰個 run）。
        # 同 `db_runtime.py:873` 一致。
        inserted_observations = 0
        for batch in chunked(observations, BATCH_SIZE):
            inserted_observations += cursor.executemany(
                """
                INSERT IGNORE INTO market_source_observation
                    (run_id, source_code, external_entity_id, observation_kind, effective_at,
                     observed_date, payload_sha256, payload_json, observed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        run_id, o.source_code, o.external_entity_id, o.observation_kind, o.effective_at,
                        o.observed_date, o.payload_sha256, o.payload_text, o.observed_at,
                    )
                    for o in batch
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
                counts["observed"], counts["accepted"], counts["quarantined"], counts["rejected"],
                datetime.now(timezone.utc).replace(tzinfo=None), run_id,
            ),
        )
        after = {
            "market_source_observation": table_count(cursor, *COUNT_LEDGER),
            "market_tracked_sales_aggregate": table_count(cursor, *COUNT_TRACKED),
        }
    return {
        "run_id": run_id,
        "run_key": run_key,
        "before": before,
        "after": after,
        "inserted": {"market_source_observation": inserted_observations},
    }


def run_counts(collected: Collected, stats: Counter) -> dict[str, int]:
    """`market_ingest_run` 四個數，逐個有定義，唔准全部寫 0。

    * observed    = 掃到嘅源記錄（指標行 + 日線 + 指數檔）
    * rejected    = 格式壞／驗證唔過，一行都冇寫落去
    * quarantined = 寫咗入 ledger 但**對唔到 variant**，下游用唔到
    * accepted    = observed − rejected − quarantined
    """

    observed = stats["metric_row_seen"] + stats["kline_row_seen"] + stats["index_file_seen"]
    rejected = (
        stats["metric_row_rejected"]
        + stats["kline_row_rejected"]
        + stats["kline_row_duplicate_date"]
        + stats["index_file_missing"]
        + stats["index_file_rejected"]
    )
    quarantined = stats["metric_unmatched"] + collected.unmatched_bar_count
    return {
        "observed": observed,
        "rejected": rejected,
        "quarantined": quarantined,
        "accepted": max(observed - rejected - quarantined, 0),
    }


def print_report(
    *,
    collected: Collected,
    tracked: Sequence[Mapping[str, Any]],
    stats: Counter,
    counts: Mapping[str, int],
    identity_size: int,
    write_result: Mapping[str, Any] | None,
) -> None:
    bars = collected.bars
    by_kind = Counter(o.observation_kind for o in collected.observations)
    dates = [bar.bar_date for bar in bars]

    print("=" * 76)
    print(f"G10 analytics ingest — {'WRITE' if write_result else 'DRY-RUN'}")
    print("=" * 76)

    print("\n[指標 card_metrics.json]")
    print(f"  掃到行                 : {stats['metric_row_seen']}")
    print(f"  格式壞 rejected        : {stats['metric_row_rejected']}")
    print(f"  對到 variant           : {stats['metric_matched']}")
    print(f"  對唔到 variant（照落 ledger，quarantined）: {stats['metric_unmatched']}")
    if collected.unmatched_metrics:
        print(f"  對唔到樣本（頭 3）: {', '.join(collected.unmatched_metrics[:3])}")
    print(f"  指標基準時間 as_of     : {collected.as_of.isoformat()}")

    print("\n[K 線 klines/*.csv]")
    print(f"  掃到檔                 : {stats['kline_file_seen']}"
          f"  (對到 {stats['kline_file_matched']} / 對唔到 {stats['kline_file_unmatched']})")
    print(f"  掃到日線               : {stats['kline_row_seen']}")
    print(f"  格式壞 rejected        : {stats['kline_row_rejected']}"
          f"   同日重複 : {stats['kline_row_duplicate_date']}")
    print(f"  對到 variant 嘅日線    : {stats['kline_bar_matched']}"
          f"   對唔到 : {collected.unmatched_bar_count}")
    real_pct = f" ({stats['kline_bar_real'] / len(bars) * 100:.2f}%)" if bars else ""
    print(f"  真成交日 carried=0     : {stats['kline_bar_real']}{real_pct}")
    print(f"  結轉日 carried=1       : {stats['kline_bar_carried']}"
          f"   其中 tx>0 : {stats['kline_bar_carried_with_tx']}  ← 唔准當成交")
    if dates:
        print(f"  日線範圍               : {min(dates)} → {max(dates)}   distinct 日數 {len(set(dates))}")

    print("\n[指數 data/index]")
    print(f"  目錄                   : {stats['index_dir_seen']} 見 / {stats['index_dir_missing']} 缺")
    print(f"  檔案                   : {stats['index_file_seen']} 掃到 / "
          f"{stats['index_file_missing']} 缺 / {stats['index_file_rejected']} 壞")

    print("\n[會寫入幾多行]")
    print(f"  market_source_observation      : {len(collected.observations)}")
    for kind, count in sorted(by_kind.items()):
        print(f"      {kind:<24} : {count}")
    missing = stats["window_missing_1d"] + stats["window_missing_7d"] + stats["window_missing_30d"]
    print(f"  catalog_source_identity 可用對應 : {identity_size}")
    print(f"\n[窗口成交量：算得出但冇表可以放，暫時唔寫]")
    print(f"  可填行數（如果有啱嘅表）: {len(tracked)}"
          f"   (1d {stats['window_ready_1d']} / 7d {stats['window_ready_7d']} / 30d {stats['window_ready_30d']})")
    print(f"  數據缺失填唔到          : {missing}"
          f"   (1d {stats['window_missing_1d']} / 7d {stats['window_missing_7d']} / 30d {stats['window_missing_30d']})")
    print("  ↑ market_tracked_sales_aggregate 冇 source_code 欄 + 2026-07-26 起已冇 consumer")
    print("    (scripts/audit_wiring_gaps.py:89)，寫落去零收益有風險。DDL 建議見交付報告。")

    print("\n[market_ingest_run 記帳]")
    for label in ("observed", "accepted", "quarantined", "rejected"):
        print(f"  {label:<12}: {counts[label]}")

    if write_result:
        print("\n[實際入庫]")
        print(f"  run_id  : {write_result['run_id']}")
        print(f"  run_key : {write_result['run_key']}")
        table = "market_source_observation"
        before = write_result["before"][table]
        after = write_result["after"][table]
        print(f"  {table:<32}: {before} → {after}  (淨增 +{after - before}"
              f", INSERT IGNORE 實際插入 {write_result['inserted'][table]})")
        untouched = write_result["after"]["market_tracked_sales_aggregate"]
        print(f"  {'market_tracked_sales_aggregate':<32}: {untouched} 行（冇寫，見上）")


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest local G10 analytics metrics / klines / index into CARDZ MySQL")
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT, help="G10 `data/` 目錄")
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個 kline 檔")
    add_connection_args(parser)
    args = parser.parse_args()

    g10_root = args.g10_root.resolve()
    if not (g10_root / "analytics" / "card_metrics.json").is_file():
        print(f"G10 analytics not found under: {g10_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        identity = load_identity_map(connection)
        stats: Counter = Counter()
        collected = collect(g10_root, identity, stats, limit=args.limit)
        tracked = tracked_sales_rows(collected.metrics, identity, collected.as_of, stats)
        counts = run_counts(collected, stats)

        if not collected.observations:
            print_report(
                collected=collected, tracked=tracked, stats=stats, counts=counts,
                identity_size=len(identity), write_result=None,
            )
            print("\n!! 冇任何 observation，唔會寫入。", file=sys.stderr)
            return 1

        write_result = None
        if args.write:
            try:
                write_result = write_all(connection, collected, counts)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        print_report(
            collected=collected, tracked=tracked, stats=stats, counts=counts,
            identity_size=len(identity), write_result=write_result,
        )
        if not args.write:
            print("\n(dry-run — 加 --write 先真入庫)")
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
