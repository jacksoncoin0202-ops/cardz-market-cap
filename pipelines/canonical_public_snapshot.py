#!/usr/bin/env python3
"""Export the web snapshot from validated canonical MySQL observations.

Ranking and market metrics come only from the canonical database.  The checked
public snapshot is used solely as a presentation pack for already-QC'd images,
localized identity text and editorial stories.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from math import isfinite
from pathlib import Path
from typing import Any, Iterable, Mapping, NamedTuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args
from editorial_localization import coverage_summary, localize_cards
from g10_public_snapshot import normalize_collector


WINDOWS = ("1d", "7d", "30d")
LOCALES = ("en", "zhTW", "zhCN", "ja")
IMAGE_QC_PATH = ROOT / "manifests/image-qc.json"
PUBLIC_ASSET_DIR = ROOT / "data/public/market-assets"
GRADERS = ("PSA", "BGS", "CGC", "SGC", "TAG")
# Mirrors the published-schema rule in packages/market-data/src/validate.ts: a
# gallery subset number is only publishable with its denominator.
BARE_SUBSET_NUMBER = re.compile(r"(?:GG|SV|TG|RC)\d+", re.IGNORECASE)
CURRENCIES = ("USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW")
TOP_GRADE = {"PSA": "10", "BGS": "10", "CGC": "10", "SGC": "10", "TAG": "10"}
# GemRate 每張卡嘅 3 年週線 POP 史，由 `gemrate_source.py` 落地（gitignore）。
# 缺檔／缺目錄唔係錯 —— 冇歷史就退返 DB 每日觀測，窗口自然報 accumulating。
POPULATION_HISTORY_DIR = ROOT / "data/private/gemrate/cards"
# 直連 API history payload 嘅正宗 top-grade key，鏡返 gemrate_source.py TOP_GRADE。
# TAG 唔喺 per-card population API 入面，所以冇歷史來源。
POPULATION_HISTORY_KEYS = {
    "PSA": ("psa", "psa_10"),
    "BGS": ("beckett", "beckett_10_pristine"),
    "CGC": ("cgc", "cgc_10_perfect"),
    "SGC": ("sgc", "sgc_10_pristine"),
}
POPULATION_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}
# 評級 POP 觀測超過 168 小時（7 日）當過期：成個 grader block fail-closed 出
# unavailable，唔准舊數扮新。基準係快照嘅 effective_at，唔係跑機當刻，
# 令同一個 generation 喺任何時間重跑都出一樣結果。
POPULATION_STALE_HOURS = 168
# 同 g10_ingest.derive_price_windows 一模一樣嘅容差，唔另立第二套標準：
# 錨點要真係落喺窗口附近，唔可以攞 35 日前嘅點當「30 日變動」。
POPULATION_WINDOW_TOLERANCE = {"1d": 1, "7d": 2, "30d": 3}
# The public schema remains top100 + watchlist.  These selectors only control
# how many ordered canonical ranks are materialized into that stable shape.
PRESENTATION_VIEW_LIMITS = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
    # combined Top300 ∪ 同日分榜（one-piece / pokemon）成員。分榜有卡跌出
    # combined 300 名以外（實測 07-26：3 張 One Piece，rank 304/312/336），
    # 冇聯集嘅話 /one-piece 分榜頁出唔齊自己榜嘅卡。limit 只管 core 部分。
    "top300_boards": 300,
}
# Minimum constituents a combined index snapshot must hold before it may back
# a given public view. The full Top 300 export requires complete coverage;
# smaller public views tolerate a partially-covered snapshot as long as the
# materialized rows stay rank-contiguous from 1.
PRESENTATION_VIEW_MIN_COVERAGE = {
    "top100": 100,
    "top300": 300,
    "top350": 350,
    "top100_plus_200": 300,
    "top300_boards": 300,
}


class SnapshotExportError(RuntimeError):
    """Raised before an invalid canonical generation can replace a snapshot."""


def presentation_view_limit(name: str) -> int:
    normalized = str(name).strip()
    try:
        return PRESENTATION_VIEW_LIMITS[normalized]
    except KeyError as error:
        choices = ", ".join(PRESENTATION_VIEW_LIMITS)
        raise SnapshotExportError(f"unknown public presentation view {normalized!r}; expected one of {choices}") from error


def presentation_view_min_coverage(name: str) -> int:
    return PRESENTATION_VIEW_MIN_COVERAGE[str(name).strip()]


def iso(value: Any) -> str:
    if isinstance(value, datetime):
        current = value
    elif isinstance(value, date):
        current = datetime.combine(value, time.min)
    else:
        current = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def earlier(left: str, right: str) -> str:
    """兩個 ISO 時戳返舊嗰個。

    唔可以直接 `min()` 字串：微秒位會令 `...:00.123456Z` 排喺 `...:00Z` 前面，
    新舊啱啱倒轉。
    """

    def moment(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    return left if moment(left) <= moment(right) else right


def number(value: Any) -> int | float | None:
    if value is None:
        return None
    converted = float(value)
    return int(converted) if converted.is_integer() else converted


def integer(value: Any) -> int | None:
    return None if value is None else int(value)


def js_safe_numbers(value: Any) -> Any:
    """數值正規化：令 Python 序列化文字 == JS `JSON.stringify(JSON.parse(text))`。

    validate.ts 嘅 `publicSnapshotContentSha256` 係 parse 完再 restringify 先 hash，
    所以任何「Python 寫法 ≠ JS 重寫法」嘅數值都會令 contentSha256 對唔上。
    實測出現過嘅只有整數值 float（Python `0.0` / JS `0`），呢度轉 int；
    指數寫法（Python `1e-05` / JS `0.00001`）一出現即 fail-closed，唔准靜靜出街。
    """

    if isinstance(value, float):
        if not isfinite(value):
            raise SnapshotExportError(f"snapshot contains non-finite number: {value!r}")
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
        if "e" in repr(value):
            raise SnapshotExportError(
                f"float {value!r} serializes with exponent notation; Python/JS texts diverge"
            )
        return value
    if isinstance(value, dict):
        return {key: js_safe_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [js_safe_numbers(item) for item in value]
    return value


def stable_json(value: Any) -> bytes:
    return json.dumps(
        js_safe_numbers(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def snapshot_content_sha256(snapshot: Mapping[str, Any]) -> str:
    """`generation.contentSha256` 嘅唯一計法：自身欄位當空字串再 hash 全份。

    對面 `packages/market-data/src/validate.ts` 嘅 `publicSnapshotContentSha256`
    會用同一條規則重算再比對，唔啱就 reject 成份 snapshot。所以任何改完 snapshot
    內容再寫返落磁碟嘅腳本（例如 ensure_std_card_images.py 換卡圖 block）都必須
    經呢度重算——改咗內容但唔重算，出嚟嘅 snapshot 喺 validator 眼中係壞檔。
    數值經 `js_safe_numbers()` 正規化（`stable_json` 入面），寫檔嗰邊 `atomic_json`
    用同一份正規化，hash 同磁碟文字唔會分家。
    """

    payload = dict(snapshot)
    payload["generation"] = {**snapshot.get("generation", {}), "contentSha256": ""}
    return hashlib.sha256(stable_json(payload)).hexdigest()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                js_safe_numbers(value), handle, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def metric(value: float | int | None, status: str, as_of: str | None, **extra: Any) -> dict[str, Any]:
    if status not in {"ready", "stale", "accumulating", "unavailable"}:
        status = "unavailable"
    if status in {"accumulating", "unavailable"}:
        value = None
    return {"value": value, "status": status, "asOf": as_of if value is not None else None, **extra}


def load_presentation(path: Path) -> tuple[dict[str, Any], dict[str, Mapping[str, Any]]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SnapshotExportError("presentation snapshot is invalid")
    cards = document.get("top100", []) + document.get("watchlist", [])
    indexed = {
        str(card["id"]): card
        for card in cards
        if isinstance(card, Mapping) and isinstance(card.get("id"), str)
    }
    if len(indexed) != len(cards):
        raise SnapshotExportError("presentation snapshot has duplicate or invalid card IDs")
    return dict(document), indexed


class PublicImages(NamedTuple):
    """QC-approved public artwork, indexed for both lookup directions."""

    by_public_id: dict[str, dict[str, Any]]
    allowed_sha: set[str]


def load_public_images(qc_path: Path = IMAGE_QC_PATH, asset_dir: Path = PUBLIC_ASSET_DIR) -> PublicImages:
    """Index raw-front artwork that already passed public QC and exists on disk.

    Nothing here invents an image: a record only counts when it is public
    allowed, carries a real content hash and dimensions, and its master asset
    file is present, which is the same contract publish-snapshot enforces.
    """

    try:
        document = json.loads(qc_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return PublicImages({}, set())
    by_public_id: dict[str, dict[str, Any]] = {}
    allowed_sha: set[str] = set()
    for record in document.get("records", []) if isinstance(document, Mapping) else []:
        if not isinstance(record, Mapping) or not record.get("publicAllowed"):
            continue
        if str(record.get("imageKind") or "") != "raw_front":
            continue
        sha = str(record.get("contentSha256") or "")
        width = integer(record.get("width"))
        height = integer(record.get("height"))
        if len(sha) != 64 or not width or not height:
            continue
        if not (asset_dir / f"{sha}.webp").is_file():
            continue
        allowed_sha.add(sha)
        public_id = str(record.get("publicId") or "")
        if not public_id or public_id in by_public_id:
            continue
        by_public_id[public_id] = {
            "height": height,
            "kind": "raw_front",
            "qcAt": str(record["qcAt"]) if record.get("qcAt") else None,
            "sha256": sha,
            "src": f"/market-assets/{sha}.webp",
            "variants": {
                size: f"/market-assets/{sha}_{size}.webp"
                for size in ("200", "600")
                if (asset_dir / f"{sha}_{size}.webp").is_file()
            },
            "width": width,
        }
    return PublicImages(by_public_id, allowed_sha)


def printing_key(tcg: Any, collector_normalized: Any) -> tuple[str, str]:
    """Identity key shared by the canonical catalog and the presentation pack."""

    return tuple(  # type: ignore[return-value]
        "".join(character for character in str(part or "").strip().casefold() if not character.isspace())
        for part in (tcg, collector_normalized)
    )


def row_printing_key(row: Mapping[str, Any]) -> tuple[str, str]:
    collector = normalize_collector(row.get("collector_number"))
    return printing_key(row.get("tcg_code"), collector.normalized)


def catalog_printing_key_counts(connection: Any) -> Counter:
    rows = fetchall(connection, "SELECT tcg_code,collector_number FROM catalog_variant")
    counts: Counter = Counter()
    for row in rows:
        counts[row_printing_key(row)] += 1
    return counts


def presentation_from_identity(row: Mapping[str, Any], image: Mapping[str, Any]) -> dict[str, Any] | None:
    """Build a presentation entry for a ranked card the pack has never seen.

    Returns None when the canonical catalog cannot supply a complete identity;
    callers must skip such cards instead of filling the gaps with placeholders.
    """

    name = str(row.get("canonical_name") or "").strip()
    set_name = str(row.get("set_name") or "").strip()
    tcg = str(row.get("tcg_code") or "").strip().lower()
    if not name or not set_name or not tcg:
        return None
    if str(row.get("identity_status") or "") != "confirmed":
        return None
    collector = normalize_collector(row.get("collector_number"), None, set_name)
    if not collector.complete:
        return None
    localized = {locale: (name if locale == "en" else None) for locale in LOCALES}
    return {
        "collectorNumber": {
            "complete": True,
            "display": collector.display,
            "normalized": collector.normalized,
        },
        "graderPopulations": {},
        "historyDaily": [],
        "id": str(row["opaque_id"]),
        "identityStatus": "confirmed",
        "image": {**image, "alt": dict(localized)},
        "marketCap": metric(None, "unavailable", None),
        "names": dict(localized),
        "populationPsa10": metric(None, "unavailable", None, estimated=False),
        "pricePsa10": metric(None, "unavailable", None),
        "rank": 0,
        "sets": {locale: (set_name if locale == "en" else None) for locale in LOCALES},
        "stories": {locale: None for locale in LOCALES},
        "tcg": tcg,
        "windows": {window: {} for window in WINDOWS},
    }


def resolve_presentation_entries(
    rows: Iterable[Mapping[str, Any]],
    cards_by_id: Mapping[str, Mapping[str, Any]],
    images: PublicImages,
    catalog_key_counts: Mapping[tuple[str, str], int],
) -> tuple[dict[str, Mapping[str, Any]], list[tuple[str, str]], Counter]:
    """Pair every ranked row with a presentation entry it may legally publish.

    Cards the canonical database ranks but the pack never carried used to abort
    the whole export.  They are now resolved card by card, and only the ones
    that still cannot show real artwork and a complete identity are skipped.
    """

    pack_key_counts: Counter = Counter()
    pack_by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    for card in cards_by_id.values():
        key = printing_key(card.get("tcg"), (card.get("collectorNumber") or {}).get("normalized"))
        pack_key_counts[key] += 1
        pack_by_key.setdefault(key, card)

    def image_usable(entry: Mapping[str, Any]) -> bool:
        return str((entry.get("image") or {}).get("sha256") or "") in images.allowed_sha

    def refreshed(entry: Mapping[str, Any], row: Mapping[str, Any]) -> Mapping[str, Any]:
        """Restore a gallery denominator the pack froze before it was resolvable.

        Reused pack entries otherwise carry their published collector number
        forward verbatim, so a truncated subset number such as "GG69" would
        survive every later export.
        """

        current = entry.get("collectorNumber") or {}
        if not BARE_SUBSET_NUMBER.fullmatch(str(current.get("display") or "")):
            return entry
        collector = normalize_collector(
            row.get("collector_number"), None, row.get("set_name")
        )
        if not collector.complete or collector.display == current.get("display"):
            return entry
        return {
            **entry,
            "collectorNumber": {
                **current,
                "display": collector.display,
                "normalized": collector.normalized,
            },
        }

    resolved: dict[str, Mapping[str, Any]] = {}
    skipped: list[tuple[str, str]] = []
    tiers: Counter = Counter()
    claimed: set[str] = set()
    for row in rows:
        opaque_id = str(row["opaque_id"])
        entry = cards_by_id.get(opaque_id)
        if entry is not None and opaque_id not in claimed and image_usable(entry):
            claimed.add(opaque_id)
            resolved[opaque_id] = refreshed(entry, row)
            tiers["pack_id"] += 1
            continue
        key = row_printing_key(row)
        candidate = pack_by_key.get(key)
        unique = pack_key_counts.get(key) == 1 and catalog_key_counts.get(key, 0) == 1
        if candidate is not None and unique and str(candidate["id"]) not in claimed and image_usable(candidate):
            claimed.add(str(candidate["id"]))
            resolved[opaque_id] = refreshed(candidate, row)
            tiers["relinked_printing_key"] += 1
            continue
        image = images.by_public_id.get(opaque_id)
        if image is None:
            collides = candidate is not None and (
                pack_key_counts.get(key, 0) > 1 or catalog_key_counts.get(key, 0) > 1
            )
            skipped.append((opaque_id, "ambiguous_printing_key" if collides else "image_unavailable"))
            continue
        rebuilt = presentation_from_identity(row, image)
        if rebuilt is None:
            skipped.append((opaque_id, "identity_incomplete"))
            continue
        resolved[opaque_id] = rebuilt
        tiers["new_from_catalog"] += 1
    return resolved, skipped, tiers


def resolution_summary(ranked: int, tiers: Mapping[str, int], skipped: list[tuple[str, str]]) -> str:
    reasons = Counter(reason for _, reason in skipped)
    published = sum(tiers.values())
    return (
        f"canonical export: {ranked} ranked, {published} published "
        f"(pack_id={tiers.get('pack_id', 0)}, relinked_printing_key={tiers.get('relinked_printing_key', 0)}, "
        f"new_from_catalog={tiers.get('new_from_catalog', 0)}), {len(skipped)} skipped"
        + (f" ({', '.join(f'{reason}={count}' for reason, count in sorted(reasons.items()))})" if skipped else "")
    )


def fetchall(connection: Any, query: str, args: Iterable[Any] = ()) -> list[Mapping[str, Any]]:
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(args))
        return list(cursor.fetchall())


def fetchone(connection: Any, query: str, args: Iterable[Any] = ()) -> Mapping[str, Any] | None:
    rows = fetchall(connection, query, args)
    return rows[0] if rows else None


def latest_generation(connection: Any, min_constituents: int) -> Mapping[str, Any]:
    rows = fetchall(
        connection,
        """
        SELECT s.id,s.evaluation_id,s.effective_at,s.effective_date,
               s.constituent_count,s.snapshot_sha256
        FROM market_index_snapshot s
        JOIN market_alert_evaluation e ON e.id=s.evaluation_id
        WHERE s.index_code='tcg-combined' AND e.publish_gate_status='passed'
        ORDER BY s.effective_date DESC,s.evaluation_id DESC,s.id DESC
        """,
    )
    for row in rows:
        count = int(fetchone(
            connection,
            "SELECT COUNT(*) AS n FROM market_index_constituent WHERE index_snapshot_id=%s",
            (row["id"],),
        )["n"])
        if count >= min_constituents:
            return row
    raise SnapshotExportError(
        f"no canonical combined ranking snapshot covers at least {min_constituents} constituents"
    )


BOARD_UNION_INDEX_CODES = ("one-piece", "pokemon")


def board_member_variant_ids(
    connection: Any,
    effective_date: Any,
    evaluation_id: int | None = None,
) -> set[int]:
    """同日（或最近一日）分榜成員 variant_id，top300_boards 聯集 view 用。"""
    members: set[int] = set()
    for index_code in BOARD_UNION_INDEX_CODES:
        if evaluation_id is not None:
            board = fetchone(
                connection,
                """
                SELECT id FROM market_index_snapshot
                WHERE index_code=%s AND evaluation_id=%s
                ORDER BY id DESC LIMIT 1
                """,
                (index_code, evaluation_id),
            )
        else:
            board = fetchone(
                connection,
                """
                SELECT id FROM market_index_snapshot
                WHERE index_code=%s AND effective_date<=%s
                ORDER BY effective_date DESC,id DESC LIMIT 1
                """,
                (index_code, effective_date),
            )
        if not board:
            continue
        rows = fetchall(
            connection,
            """
            SELECT variant_id FROM market_index_constituent
            WHERE index_snapshot_id=%s AND rank_position<=100
            """,
            (int(board["id"]),),
        )
        members.update(int(row["variant_id"]) for row in rows)
    return members


def generation_evaluation_id(connection: Any, generation: Mapping[str, Any]) -> int:
    """Use the exact evaluation bound to a revision; query only for legacy rows."""

    if generation.get("evaluation_id") is not None:
        return int(generation["evaluation_id"])
    evaluation = fetchone(
        connection,
        """
        SELECT id FROM market_alert_evaluation
        WHERE index_code='tcg-combined' AND effective_date<=%s
        ORDER BY effective_date DESC,id DESC LIMIT 1
        """,
        (generation["effective_date"],),
    )
    return int(evaluation["id"]) if evaluation else -1


def market_rows(
    connection: Any,
    generation: Mapping[str, Any],
    *,
    required_count: int,
    include_board_extras: bool = False,
) -> list[dict[str, Any]]:
    evaluation_id = generation_evaluation_id(connection, generation)
    row_query = """
        SELECT v.id AS variant_id,v.opaque_id,v.identity_status,
               v.canonical_name,v.set_name,v.collector_number,v.tcg_code,
               c.rank_position AS rank_position,c.reference_price_usd,c.psa10_population,
               c.market_cap_usd,c.metric_status,
               d.change_1d_pct,d.change_7d_pct,d.change_30d_pct
        FROM market_index_constituent c
        JOIN catalog_variant v ON v.id=c.variant_id
        LEFT JOIN market_candidate_daily_snapshot d
          ON d.variant_id=c.variant_id AND d.evaluation_id=%s
        WHERE c.index_snapshot_id=%s AND c.rank_position{rank_cond}
        ORDER BY c.rank_position
    """
    top = fetchall(
        connection,
        row_query.format(rank_cond="<=%s"),
        (evaluation_id, generation["id"], required_count),
    )
    if len(top) != required_count:
        raise SnapshotExportError(
            f"canonical combined ranking has {len(top)} rows for requested Top {required_count} view"
        )
    if [int(row["rank_position"]) for row in top] != list(range(1, len(top) + 1)):
        raise SnapshotExportError("canonical combined ranking has non-contiguous ranks")
    if not include_board_extras:
        return [dict(row) for row in top]
    # 聯集尾巴：combined 榜 required_count 名以外、但屬於同日分榜嘅卡，
    # 照 combined rank 排喺 core 後面。出版前 build_snapshot 會重排 1..N，
    # 所以呢度唔使（亦唔應該）連續。
    members = board_member_variant_ids(
        connection,
        generation["effective_date"],
        evaluation_id if evaluation_id >= 0 else None,
    )
    if not members:
        return [dict(row) for row in top]
    placeholders = ",".join(["%s"] * len(members))
    extras = fetchall(
        connection,
        row_query.format(rank_cond=f">%s AND c.variant_id IN ({placeholders})"),
        (evaluation_id, generation["id"], required_count, *sorted(members)),
    )
    return [dict(row) for row in [*top, *extras]]


SALES_WINDOW_DAYS = {"1d": 1, "7d": 7, "30d": 30}


def ebay_psa10_daily_rows(
    connection: Any,
    variant_ids: list[int],
    earliest: date | None = None,
    anchor: date | None = None,
) -> list[Mapping[str, Any]]:
    """eBay PSA 10 日成交，由逐筆表 market_sale_observation 即場滾出嚟。

    點解唔讀 market_daily_sales_aggregate 嘅 ebay 行：嗰批係 g10_ebay_ingest
    將全部 grade（PSA10+PSA9+CGC10+BGS10+BGS BL）滾埋一齊嘅總量，日表冇 grade
    欄分唔返開，直接用會令 PSA9/BGS/CGC 成交混入 PSA10 序列。逐筆表有
    grader_code/grade_label，先篩得出純 PSA 10（8,558 行 / 505 variant /
    2026-04-25 起，2026-07-27 實測）。
    口徑照抄 ingest 嘅日 rollup（daily_sales_rows）：sales_count = 筆數
    （quantity 全部係 1，實測），sales_value_usd = SUM(unit_price_usd)。
    coverage_status 逐筆行全部 'partial'；用 MAX() 係為咗第日混入其他值時
    揀字母序最大嗰個（'quarantined' > 'partial'），出錯方向係 fail-closed。
    """
    if not variant_ids:
        return []
    placeholders = ",".join(["%s"] * len(variant_ids))
    params: list[Any] = [*variant_ids]
    date_cond = ""
    if earliest is not None and anchor is not None:
        date_cond = "AND DATE(sold_at) BETWEEN %s AND %s"
        params.extend([earliest, anchor])
    return fetchall(
        connection,
        f"""
        SELECT variant_id, DATE(sold_at) AS observed_date,
               COUNT(*) AS sales_count, SUM(unit_price_usd) AS sales_value_usd,
               MAX(coverage_status) AS coverage_status
        FROM market_sale_observation
        WHERE variant_id IN ({placeholders})
          AND source_code='ebay' AND grader_code='psa' AND grade_label='10'
          AND sold_at IS NOT NULL {date_cond}
        GROUP BY variant_id, DATE(sold_at)
        """,
        params,
    )


def latest_sales(
    connection: Any, variant_ids: list[int], anchor: date | datetime
) -> dict[tuple[int, str], Mapping[str, Any]]:
    """1d/7d/30d 成交彙總，由 market_daily_sales_aggregate 即場滾出嚟。

    呢度以前讀 market_tracked_sales_aggregate —— 嗰張表 0 行，而每日成交
    （3592 行 / 336 variant / 2023-07 起）一直寫落 market_daily_sales_aggregate，
    即係同一個檔入面 daily_history() 讀緊嗰張。兩邊都冇 error，前端就長期
    顯示「成交數據不可用」。詳見 docs/ARCHITECTURE_CHAIN.md 斷點 #2。

    窗口 anchor 綁 snapshot generation 嘅 effective date，同價格
    change_{1,7,30}d_pct 同一個基準。成交數據落後就照樣顯示縮水 —— 唔會攞
    「該卡最後有成交嗰日」當今日嚟造靚個數。

    兩個 PSA10 成交源相加：SNKRDUNK（日表 source_code='snk_psa10'）+ eBay
    （逐筆表篩 psa/10，見 ebay_psa10_daily_rows）。日本 app 同美國市場係兩批
    唔同嘅成交件，冇 double count。日表嘅 ebay 行係全 grade 混合，唔准用。
    """
    if not variant_ids:
        return {}
    if isinstance(anchor, datetime):
        anchor = anchor.date()
    placeholders = ",".join(["%s"] * len(variant_ids))
    # 兩倍窗口長度：除咗當前窗口，仲要罩住緊貼前面嗰個同長度窗口，
    # 先至砌得出真嘅成交額環比（見下面 prev_* 欄）。
    earliest = anchor - timedelta(days=max(SALES_WINDOW_DAYS.values()) * 2 - 1)
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,sales_count,sales_value_usd,coverage_status
        FROM market_daily_sales_aggregate
        WHERE variant_id IN ({placeholders})
          AND source_code='snk_psa10'
          AND observed_date BETWEEN %s AND %s
        ORDER BY observed_date,id
        """,
        [*variant_ids, earliest, anchor],
    )
    ebay_rows = ebay_psa10_daily_rows(connection, variant_ids, earliest, anchor)
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    # 桶內係純累加（交換律），兩源行 concat 就得，唔使 pre-merge 同日行。
    # coverage_status/window_end_at 跟窗口內最新一日行走；同日兩源都係
    # 'partial'，邊個 last-wins 都一樣。
    for row in [*rows, *ebay_rows]:
        variant_id = int(row["variant_id"])
        observed = row["observed_date"]
        for window, span in SALES_WINDOW_DAYS.items():
            if observed <= anchor - timedelta(days=span * 2):
                continue
            bucket = result.setdefault(
                (variant_id, window),
                {
                    "sales_count": 0,
                    "sales_value_usd": 0.0,
                    "coverage_status": "unavailable",
                    "window_end_at": None,
                    # 前一個同長度窗口 (anchor-2*span, anchor-span]，淨係用嚟計環比。
                    "prev_sales_count": 0,
                    "prev_sales_value_usd": 0.0,
                    "prev_days": 0,
                },
            )
            if observed <= anchor - timedelta(days=span):
                bucket["prev_sales_count"] += int(row["sales_count"] or 0)
                bucket["prev_sales_value_usd"] += float(row["sales_value_usd"] or 0)
                bucket["prev_days"] += 1
                continue
            bucket["sales_count"] += int(row["sales_count"] or 0)
            bucket["sales_value_usd"] += float(row["sales_value_usd"] or 0)
            # coverage 同 window_end_at 一齊跟窗口內最新嗰日走。
            if bucket["window_end_at"] is None or observed >= bucket["window_end_at"]:
                bucket["window_end_at"] = observed
                bucket["coverage_status"] = str(row["coverage_status"])
    # 只有前窗口有成交、當前窗口冇嘅卡，會喺上面開咗個空 bucket。嗰個唔算
    # 「有成交」，收返走，唔好令 trackedSales 由 unavailable 變 ready/0。
    return {key: bucket for key, bucket in result.items() if bucket["window_end_at"] is not None}


def latest_populations(connection: Any, variant_ids: list[int]) -> dict[tuple[int, str], Mapping[str, Any]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,grader_code,top_grade_label,total_population,top_grade_population,
               estimated,effective_at,observed_date
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date DESC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    result: dict[tuple[int, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (int(row["variant_id"]), str(row["grader_code"]).upper())
        # Latest row wins for top-grade population, but a fresh row may carry a
        # null total_population (the GemRate pipeline only ships top-grade
        # counts). Fall back to the most recent non-null total so the public
        # snapshot never emits a ready/null metric.
        existing = result.get(key)
        if existing is None:
            result[key] = row
        elif existing["total_population"] is None and row["total_population"] is not None:
            merged = dict(existing)
            merged["total_population"] = row["total_population"]
            result[key] = merged
    return result


def population_series(
    connection: Any, variant_ids: list[int]
) -> dict[tuple[int, str], dict[date, int]]:
    """每張卡每個評級廠嘅 top-grade POP 日線／週線序列。

    兩個來源疊埋：DB 逐日觀測（起 2026-07-21，得日線但短）＋ GemRate 每張卡嘅
    3 年週線史（`data/private/gemrate/cards/<gemrate_id>/history_full.json`，由
    `gemrate_source.py` 落地）。DB 係當日權威，同日撞到就 DB 贏。

    冇私有歷史目錄（CI／新 clone）唔係錯，淨用 DB 就得。
    """

    if not variant_ids:
        return {}
    series: dict[tuple[int, str], dict[date, int]] = defaultdict(dict)
    placeholders = ",".join(["%s"] * len(variant_ids))
    if POPULATION_HISTORY_DIR.is_dir():
        gemrate_ids = fetchall(
            connection,
            f"""
            SELECT variant_id,external_entity_id
            FROM catalog_source_identity
            WHERE source_code='gemrate' AND variant_id IN ({placeholders})
            """,
            variant_ids,
        )
        for identity in gemrate_ids:
            path = POPULATION_HISTORY_DIR / str(identity["external_entity_id"]) / "history_full.json"
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                by_grader = payload["data"]["population"]["population_data"]["by_grader"]
            except (OSError, ValueError, KeyError, TypeError):
                # 私有落地檔壞咗唔可以拉冧成個 snapshot；冇歷史當冇歷史。
                continue
            if not isinstance(by_grader, Mapping):
                continue
            variant_id = int(identity["variant_id"])
            for grader, (source_key, grade_key) in POPULATION_HISTORY_KEYS.items():
                row = by_grader.get(source_key)
                points = row.get("history") if isinstance(row, Mapping) else None
                if not isinstance(points, list):
                    continue
                for point in points:
                    if not isinstance(point, Mapping):
                        continue
                    value = (point.get("grades") or {}).get(grade_key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        continue
                    try:
                        observed = date.fromisoformat(str(point["date"]))
                    except (KeyError, ValueError):
                        continue
                    series[(variant_id, grader)][observed] = value
    observations = fetchall(
        connection,
        f"""
        SELECT variant_id,grader_code,observed_date,top_grade_population,estimated
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY id
        """,
        variant_ids,
    )
    for row in observations:
        value = row["top_grade_population"]
        if value is None or row["estimated"]:
            continue
        observed = row["observed_date"]
        if isinstance(observed, datetime):
            observed = observed.date()
        series[(int(row["variant_id"]), str(row["grader_code"]).upper())][observed] = int(value)
    return dict(series)


def population_is_stale(observation: Mapping[str, Any], effective_at: str) -> bool:
    """POP 觀測老過 POPULATION_STALE_HOURS 就係過期，唔准入快照。

    冇 effective_at 或者 parse 唔到，一律當過期——寧願 unavailable 都唔好
    stamp 個假時間出街。
    """

    observed_at = observation.get("effective_at")
    if not isinstance(observed_at, datetime):
        return True
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    try:
        generated = datetime.fromisoformat(str(effective_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)
    return (generated - observed_at) > timedelta(hours=POPULATION_STALE_HOURS)


def top_grade_label(observation: Mapping[str, Any] | None, grader: str) -> str:
    """DB 而家 9,862 行 top_grade_label 全部係字面 'top'（ingest 層 bug，
    Part② 先修根）。快照層防守：literal 'top'／空值一律回退 TOP_GRADE 表，
    真 label（將來 ingest 修好後）原样直出。"""

    label = str(observation.get("top_grade_label") or "").strip() if observation else ""
    if not label or label.lower() == "top":
        return TOP_GRADE[grader]
    return label


def observed_day(observation: Mapping[str, Any] | None) -> date | None:
    if not observation:
        return None
    value = observation.get("observed_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def population_change_windows(
    points: Mapping[date, int], current_value: int | None, current_day: date | None, status: str
) -> dict[str, dict[str, Any]]:
    """POP 三個窗口嘅百分比變動。

    Population 係**存量**指標，只升唔跌（CLAUDE.md 數據語義）。錨點高過現值即係
    數據異常（某廠當日冇入到數就寫 0），一律當 `unavailable` 收起，**永遠唔會**
    出負 delta 或者跌箭嘴。錨點係 0 亦冇得計百分比，同樣收起。
    """

    if current_value is None or current_day is None or not points:
        return {window: metric(None, "unavailable", None) for window in WINDOWS}
    as_of = iso(datetime.combine(current_day, time.min, tzinfo=timezone.utc))
    earliest = min(points)
    result: dict[str, dict[str, Any]] = {}
    for window in WINDOWS:
        target = current_day - timedelta(days=POPULATION_WINDOW_DAYS[window])
        tolerance = POPULATION_WINDOW_TOLERANCE[window]
        candidates = [
            (abs((day - target).days), day, value)
            for day, value in points.items()
            if day < current_day and abs((day - target).days) <= tolerance
        ]
        if not candidates:
            # 有現值但歷史未夠長 = accumulating（中性「—」）；完全冇歷史先叫 unavailable。
            result[window] = metric(None, "accumulating" if earliest > target else "unavailable", None)
            continue
        # 同 derive_price_windows 一致：先揀最貼目標日，同距離下寧取目標日或之前
        # 嗰點（寧可窗口略長，唔好偷短），最後取最早嗰個穩定 tie-break。
        _, anchor_day, anchor = min(candidates, key=lambda item: (item[0], item[1] > target, item[1]))
        if anchor <= 0 or current_value < anchor:
            result[window] = metric(None, "unavailable", None)
            continue
        result[window] = metric(
            round(((current_value / anchor) - 1) * 100, 6),
            "stale" if status == "stale" else "ready",
            as_of,
            anchorAt=anchor_day.isoformat(),
        )
    return result


def compose_change_pct(base: Mapping[str, Any], factor: Mapping[str, Any]) -> dict[str, Any]:
    """市值變動 = (1+Δ價/100)(1+ΔPOP/100)−1，百分比單位。

    市值 = 價 × POP，所以市值嘅變動率**唔係**價格嘅變動率。舊版前端直接攞
    `changePct`（純價格）當市值變動用，漏咗 POP 嗰截：POP 只升唔跌，所以幅度
    永遠低估；而當價格跌、POP 升到蓋得過，乘出嚟由負變正，箭嘴會**指錯方向**。

    兩個輸入有一個唔係 ready/stale 就出 null。**唔准**退返去單用 Δ價頂替 ——
    頂替就係原本嗰個 bug 本身（CLAUDE.md 數據語義：冇對應窗口歷史數據就直話
    用戶，唔准攞另一個指標嘅 changePct 頂替）。呢個係 fail-closed，寧願空白。

    `derive.ts` 嘅 `composeChangePct()` 係同一條式，兩邊要一齊改。
    """

    live = {"ready", "stale"}
    base_ok = base.get("value") is not None and base.get("status") in live
    factor_ok = factor.get("value") is not None and factor.get("status") in live
    if not base_ok or not factor_ok:
        unavailable = base.get("status") == "unavailable" or factor.get("status") == "unavailable"
        return metric(None, "unavailable" if unavailable else "accumulating", None)
    composed = ((1 + float(base["value"]) / 100) * (1 + float(factor["value"]) / 100) - 1) * 100
    if not isfinite(composed):
        return metric(None, "unavailable", None)
    # 複合指標唔可以扮到新過佢最舊嗰個輸入。
    base_at, factor_at = base.get("asOf"), factor.get("asOf")
    as_of = earlier(base_at, factor_at) if base_at and factor_at else (base_at or factor_at)
    return metric(
        round(composed, 6),
        "stale" if "stale" in {base.get("status"), factor.get("status")} else "ready",
        as_of,
    )


def ratio_change_pct(current: float | None, previous: float | None, status: str, as_of: str | None) -> dict[str, Any]:
    """環比百分比。前期係 0 或者缺數就出 null —— 除唔到就係計唔到。"""

    if current is None or previous is None or previous <= 0:
        return metric(None, "accumulating", None)
    change = ((current / previous) - 1) * 100
    if not isfinite(change):
        return metric(None, "unavailable", None)
    return metric(round(change, 6), status, as_of)


def latest_price_at(connection: Any, variant_ids: list[int]) -> dict[int, str]:
    """每張卡最近一次**真實**價格觀測嘅 effective_at。

    公開 snapshot 嘅 `pricePsa10.asOf` 一定要用呢個，唔可以用 snapshot 自己嘅
    `generation.effectiveAt`：`validate.ts` 個 48h 新鮮度閘量度嘅正正係兩者之差，
    如果 stamp 咗 snapshot 自己嘅時間，age 由構造上永遠係 0，爬蟲死咗個閘都唔會響。

    排序同 `daily_history()` 一致，攞返建 constituent 嗰陣揀中嘅同一行觀測。
    """
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    rows = fetchall(
        connection,
        f"""
        SELECT variant_id,effective_at
        FROM market_price_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date DESC,source_priority ASC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    result: dict[int, str] = {}
    for row in rows:
        result.setdefault(int(row["variant_id"]), iso(row["effective_at"]))
    return result


def daily_history(connection: Any, variant_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    prices = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,price_usd,metric_status,source_priority,effective_at
        FROM market_price_observation
        WHERE variant_id IN ({placeholders})
        ORDER BY observed_date DESC,source_priority ASC,effective_at DESC,id DESC
        """,
        variant_ids,
    )
    # 同 latest_sales() 一樣：SNK 讀日表（source_code='snk_psa10'），eBay 由逐筆表
    # 篩 psa/10 即場滾（日表嘅 ebay 行係全 grade 混合，唔准用——詳見
    # ebay_psa10_daily_rows docstring）。同一 (variant, 日) 兩源都有成交就相加，
    # 唔准 last-wins 蓋數。
    sales = fetchall(
        connection,
        f"""
        SELECT variant_id,observed_date,sales_count,sales_value_usd,coverage_status
        FROM market_daily_sales_aggregate
        WHERE variant_id IN ({placeholders})
          AND source_code='snk_psa10'
        ORDER BY observed_date,id
        """,
        variant_ids,
    )
    ebay_sales = ebay_psa10_daily_rows(connection, variant_ids)
    sales_by_day: dict[tuple[int, str], dict[str, Any]] = {}
    for row in [*sales, *ebay_sales]:
        sale_key = (int(row["variant_id"]), str(row["observed_date"]))
        merged = sales_by_day.get(sale_key)
        if merged is None:
            sales_by_day[sale_key] = {
                "sales_count": int(row["sales_count"] or 0),
                "sales_value_usd": float(row["sales_value_usd"] or 0),
                "coverage_status": str(row["coverage_status"]),
            }
        else:
            merged["sales_count"] += int(row["sales_count"] or 0)
            merged["sales_value_usd"] += float(row["sales_value_usd"] or 0)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[int, str]] = set()
    for row in prices:
        variant_id = int(row["variant_id"])
        day = str(row["observed_date"])
        key = (variant_id, day)
        if key in seen:
            continue
        seen.add(key)
        sale = sales_by_day.get(key)
        grouped[variant_id].append(
            {
                "at": f"{day}T00:00:00Z",
                "priceUsd": number(row["price_usd"]),
                "priceStatus": str(row["metric_status"]),
                "trackedSalesValueUsd": number(sale["sales_value_usd"]) if sale else None,
                "trackedSalesCount": integer(sale["sales_count"]) if sale else None,
                "salesCoverage": str(sale["coverage_status"]) if sale else "unavailable",
            }
        )
    for variant_id, points in grouped.items():
        grouped[variant_id] = sorted(points[:90], key=lambda point: point["at"])
    return grouped


def currency_block(connection: Any, effective_at: str) -> dict[str, Any]:
    rows = fetchall(
        connection,
        """
        SELECT quote_currency,rate,effective_at
        FROM market_fx_rate_observation
        WHERE base_currency='USD'
        ORDER BY effective_at DESC,id DESC
        """,
    )
    latest: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        latest.setdefault(str(row["quote_currency"]).upper(), row)
    rates: dict[str, Any] = {"USD": metric(1, "ready", effective_at)}
    for currency in CURRENCIES[1:]:
        row = latest.get(currency)
        rates[currency] = (
            metric(number(row["rate"]), "ready", iso(row["effective_at"]))
            if row
            else metric(None, "unavailable", None)
        )
    dates = [value["asOf"] for value in rates.values() if value["asOf"]]
    return {"base": "USD", "supported": list(CURRENCIES), "rates": rates, "asOf": min(dates) if dates else None}


def card_from_row(
    row: Mapping[str, Any],
    presentation: Mapping[str, Any],
    effective_at: str,
    sales: Mapping[tuple[int, str], Mapping[str, Any]],
    populations: Mapping[tuple[int, str], Mapping[str, Any]],
    history: Mapping[int, list[dict[str, Any]]],
    price_at: Mapping[int, str],
    pop_series: Mapping[tuple[int, str], Mapping[date, int]] | None = None,
) -> dict[str, Any]:
    card = json.loads(json.dumps(presentation))
    variant_id = int(row["variant_id"])
    status = str(row.get("metric_status") or "unavailable")
    if status not in {"ready", "stale"}:
        raise SnapshotExportError(f"ranked card {row['opaque_id']} has unavailable market metrics")
    # 三個對外指標都要 stamp 返真實觀測時間。冇觀測就係數據鏈斷咗，
    # fail-closed 喺呢度嘈出嚟，好過 stamp 個假時間令 48h 閘一世唔會響。
    price_observed_at = price_at.get(variant_id)
    if price_observed_at is None:
        raise SnapshotExportError(
            f"ranked card {row['opaque_id']} has no backing price observation to date pricePsa10"
        )
    psa_population = populations.get((variant_id, "PSA"))
    if psa_population is None:
        raise SnapshotExportError(
            f"ranked card {row['opaque_id']} has no backing PSA population observation to date populationPsa10"
        )
    population_observed_at = iso(psa_population["effective_at"])
    card["rank"] = int(row["rank_position"])
    card["identityStatus"] = "confirmed"
    card["pricePsa10"] = metric(number(row["reference_price_usd"]), status, price_observed_at)
    card["populationPsa10"] = metric(
        integer(row["psa10_population"]), "ready", population_observed_at, estimated=False
    )
    # market cap = 價 × POP，兩個輸入邊個舊就跟邊個，唔可以扮返新。
    card["marketCap"] = metric(
        number(row["market_cap_usd"]), status, earlier(price_observed_at, population_observed_at)
    )
    # 市值 changePct 要 PSA top-grade POP 嘅同窗口變動做第二個因子。
    # 呢度同下面 grader loop 嘅 PSA 分支傳一模一樣嘅參數，
    # `population_change_windows()` 係純函數，兩邊必然出同一個數。
    psa_population_change = population_change_windows(
        (pop_series or {}).get((variant_id, "PSA")) or {},
        integer(psa_population["top_grade_population"]),
        observed_day(psa_population),
        "ready",
    )
    for window, column in (("1d", "change_1d_pct"), ("7d", "change_7d_pct"), ("30d", "change_30d_pct")):
        change = number(row.get(column))
        change_metric = metric(change, "ready" if change is not None else "accumulating", effective_at)
        aggregate = sales.get((variant_id, window))
        coverage = str(aggregate["coverage_status"]) if aggregate else "unavailable"
        aggregate_at = iso(aggregate["window_end_at"]) if aggregate else None
        sales_status = "ready" if aggregate and coverage == "partial" else "unavailable"
        sales_value = number(aggregate["sales_value_usd"]) if aggregate else None
        card["windows"][window] = {
            "changePct": change_metric,
            "marketCapChangePct": compose_change_pct(change_metric, psa_population_change[window]),
            # 成交額環比：呢個窗口 vs 緊貼前面同長度嗰個窗口。舊版前端攞
            # `changePct`（價格變動）當成交額變動用 —— 兩個量冇任何數學關係。
            "trackedSalesChangePct": ratio_change_pct(
                sales_value if sales_status == "ready" else None,
                number(aggregate.get("prev_sales_value_usd")) if aggregate else None,
                sales_status,
                aggregate_at,
            ),
            "trackedSales": {
                "valueUsd": metric(number(aggregate["sales_value_usd"]) if aggregate else None, sales_status, aggregate_at),
                "count": metric(integer(aggregate["sales_count"]) if aggregate else None, sales_status, aggregate_at),
                "coverage": coverage,
                "asOf": aggregate_at,
            },
        }
    for grader in GRADERS:
        observed = populations.get((variant_id, grader))
        # Date floor：過期觀測直接當冇——label／total／POP／changePct 四樣
        # 全部跟住自動歸 unavailable，唔會留低半新半舊嘅卡片。
        # （TAG 07-29 起最先觸發：freeze 後冇新觀測，就係應該熄。）
        if observed is not None and population_is_stale(observed, effective_at):
            observed = None
        observed_at = iso(observed["effective_at"]) if observed else None
        observed_status = "ready" if observed else "unavailable"
        total_value = integer(observed["total_population"]) if observed else None
        top_value = integer(observed["top_grade_population"]) if observed else None
        card["graderPopulations"][grader] = {
            "topGrade": top_grade_label(observed, grader),
            # total population is not shipped by the GemRate top-grade pipeline;
            # emit unavailable instead of ready/null so public validation holds.
            "total": metric(
                total_value,
                observed_status if total_value is not None else "unavailable",
                observed_at if total_value is not None else None,
                estimated=False,
            ),
            "topGradePopulation": metric(
                top_value,
                observed_status if top_value is not None else "unavailable",
                observed_at if top_value is not None else None,
                estimated=bool(observed["estimated"]) if observed else False,
            ),
            "topGradePopulationChangePct": population_change_windows(
                (pop_series or {}).get((variant_id, grader)) or {},
                top_value,
                observed_day(observed),
                observed_status,
            ),
        }
    card["historyDaily"] = history.get(variant_id, [])
    return card


def build_snapshot(
    connection: Any,
    presentation_path: Path,
    *,
    production: bool,
    presentation_view: str = "top300",
) -> dict[str, Any]:
    template, cards_by_id = load_presentation(presentation_path)
    required_count = presentation_view_limit(presentation_view)
    generation = latest_generation(connection, presentation_view_min_coverage(presentation_view))
    effective_at = iso(generation["effective_at"])
    rows = market_rows(
        connection,
        generation,
        required_count=required_count,
        include_board_extras=(presentation_view == "top300_boards"),
    )
    if len(rows) > 500:
        raise SnapshotExportError(
            f"{presentation_view} resolves {len(rows)} cards; the public projection limit is 500"
        )
    resolved, skipped, tiers = resolve_presentation_entries(
        rows,
        cards_by_id,
        load_public_images(),
        catalog_printing_key_counts(connection),
    )
    publishable = [row for row in rows if str(row["opaque_id"]) in resolved]
    variant_ids = [int(row["variant_id"]) for row in publishable]
    sales = latest_sales(connection, variant_ids, generation["effective_at"])
    populations = latest_populations(connection, variant_ids)
    history = daily_history(connection, variant_ids)
    price_at = latest_price_at(connection, variant_ids)
    pop_series = population_series(connection, variant_ids)
    cards = [
        card_from_row(
            row,
            resolved[str(row["opaque_id"])],
            effective_at,
            sales,
            populations,
            history,
            price_at,
            pop_series,
        )
        for row in publishable
    ]
    # Skipped cards leave holes in the canonical order; the public contract
    # requires top100 to run 1..100 and the watchlist to continue from 101.
    for position, card in enumerate(cards, start=1):
        card["rank"] = position
    # 譯名／譯文要喺呢度貼，唔可以喺 presentation_from_identity ——
    # 嗰個 function 淨係砌 tier-3 卡，tier-1/2 由 presentation pack 直接抬過嚟，
    # 帶住同一批 null。呢度係唯一見到齊全 cards list 嘅地方。
    #
    # 故事查表要用 DB 嘅 `opaque_id` 而唔係 `card["id"]`：tier-1/2 卡個 id 係由
    # presentation pack 凍住抬過嚟，而 `opaque_id` = sha256(name/set/collector)
    # —— 執過一次卡名就重新 hash 過，pack 入面嗰個舊 id 即刻查唔返
    # `catalog_variant`。實測 251 張出版卡得 75 張仲對得返，其餘 176 張明明
    # 入咗庫但出唔到街。呢度即場砌返「出版 id → 當日 opaque_id」嘅對照，
    # 公開 id 一個 bit 都唔郁。
    localization = localize_cards(
        cards,
        connection=connection,
        story_keys={
            str(card["id"]): str(row["opaque_id"]) for card, row in zip(cards, publishable)
        },
    )
    print(resolution_summary(len(rows), tiers, skipped), file=sys.stderr)
    print(coverage_summary(localization), file=sys.stderr)
    top = cards[:100]
    watch = cards[100:]
    blockers: list[str] = []
    if len(top) != 100:
        blockers.append("combined_top100_incomplete")
    if len(cards) != len(rows):
        blockers.append("presentation_assets_incomplete")
    generated_now = datetime.now(timezone.utc)
    generated_at = iso(generated_now)
    generation_id = (
        f"canonical_{str(generation['effective_date']).replace('-', '')}"
        f"_e{int(generation['evaluation_id'])}_{str(generation['snapshot_sha256'])[:12]}"
        f"_{generated_now.strftime('%Y%m%dT%H%M%S%fZ')}"
    )
    snapshot = {
        "schemaVersion": "2.0.0",
        "generation": {
            "id": generation_id,
            "generatedAt": generated_at,
            "effectiveAt": effective_at,
            "contentSha256": "",
            "mode": "production" if production else "demo",
            "productionEligible": production and not blockers,
            "blockers": blockers,
        },
        "universe": {
            "populationMin": 1000,
            "grade": "PSA 10",
            "rankingMetric": "psa10_market_cap_usd",
            "windows": list(WINDOWS),
            "salesCoverage": "partial",
        },
        "coverage": {
            "requestedView": presentation_view,
            "top100Count": len(top),
            "watchlistCount": len(watch),
            "publicTop300Count": len(cards),
            "privateReserveExcludedCount": max(0, int(generation["constituent_count"]) - len(cards)),
            "changeReady": {window: sum(card["windows"][window]["changePct"]["status"] == "ready" for card in cards) for window in WINDOWS},
            "salesReady": {window: sum(card["windows"][window]["trackedSales"]["coverage"] == "partial" for card in cards) for window in WINDOWS},
            "graderPopulationReady": {
                grader: sum(card["graderPopulations"][grader]["topGradePopulation"]["status"] == "ready" for card in cards)
                for grader in GRADERS
            },
            "graderPopulationChangeReady": {
                grader: {window: 0 for window in WINDOWS} for grader in GRADERS
            },
            "completeIdentityCount": sum(card["collectorNumber"]["complete"] for card in cards),
            "localizedStoryCount": {
                locale: sum(bool(card["stories"].get(locale)) for card in cards)
                for locale in ("en", "zhTW", "zhCN", "ja")
            },
        },
        "currencies": currency_block(connection, effective_at),
        "top100": top,
        "watchlist": watch,
    }
    snapshot["generation"]["contentSha256"] = snapshot_content_sha256(snapshot)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--presentation", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/public/seed-snapshot.json")
    parser.add_argument("--view", choices=tuple(PRESENTATION_VIEW_LIMITS), default="top300")
    parser.add_argument("--production", action="store_true")
    add_connection_args(parser)
    args = parser.parse_args()
    connection = connection_from_args(args)
    try:
        snapshot = build_snapshot(
            connection,
            args.presentation.resolve(),
            production=args.production,
            presentation_view=args.view,
        )
    finally:
        connection.close()
    atomic_json(args.output.resolve(), snapshot)
    print(
        json.dumps(
            {
                "generation": snapshot["generation"]["id"],
                "top100": len(snapshot["top100"]),
                "watchlist": len(snapshot["watchlist"]),
                "output": str(args.output.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SnapshotExportError, RuntimeError, ValueError) as error:
        print(str(error), file=os.sys.stderr)
        raise SystemExit(1) from None
