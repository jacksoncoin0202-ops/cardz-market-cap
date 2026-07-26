#!/usr/bin/env python3
"""把本機 G10 (`../grade10-scraper`) 已經寫好嘅英文研究長文入庫。

點解要有呢個檔：G10 硬碟上 `data/cards/snkrdunk/{id}/summary_en.json` 已經有 **480 篇**
英文研究（共 1,281,075 字元，最短 1,938、中位 2,561、最長 5,551），而 DB 嘅
`catalog_variant_locale` 同 `catalog_story_pointer` **0 行**。內容一早寫好，冇人搬過。
呢個 loader 就係嗰條搬運線：G10 檔案樹 → 兩張 locale/provenance 表。
**唔生成、唔改寫、唔翻譯**，原文原封入庫（只 trim 頭尾空白）。

寫入兩張表：
  * `catalog_variant_locale`  locale_code='en'，`market_story` = 原文
  * `catalog_story_pointer`   provenance：G10 相對路徑 + 檔案內容 sha256 + 檔案 mtime

## `summary_jp.json` 唔係日文（實測）

480 對 `summary_en.json` / `summary_jp.json` 逐字比對，**480/480 完全一樣**，零個例外。
所以呢個 loader 只認 `summary_en.json`，只寫 `locale_code='en'`。見到檔名 `_jp` 當有日文
係錯嘅；日文要等真正翻譯步驟先有。

## 卡片對應

唯一合法路徑係 `catalog_source_identity`，唔准靠卡名（`docs/G10_BASELINE.md` §2.3：
641 個目錄得 319 個 unique 卡名，卡名 match 係假嘅）。
  * `source_code='snkrdunk'` 的 `external_entity_id` = `data/cards/snkrdunk/{id}/` 目錄名
  * `source_code='ebay'`     的 `external_entity_id` = `data/cards/altxyz/{UUID}/` 目錄名

對唔到 variant 嘅目錄 **唔估、唔猜**，計入 `quarantined` 並喺報告逐個列出。
identity 表日後擴充，本腳本重跑就會自動撿返（兩張表都係 upsert，重跑淨增 0）。

## `market_ingest_run.status` 點解係 'completed' 唔係 'complete'

`pipelines/market_alerts.py:604` 用
`SELECT id FROM market_ingest_run WHERE status='complete' ORDER BY effective_at DESC,id DESC LIMIT 1`
攞 run_id 落 `market_index_snapshot.run_id`。本 run 嘅 `effective_at` 係檔案 mtime
（最新 2026-07-25 UTC），一寫 'complete' 就會蓋過 ranking 嘅 lineage。跟 sibling
`g10_ebay_ingest.py` 用 'completed'，同 G10 家族一致，亦唔會污染其他 pipeline。

## Fail-closed

  * 故事 UTF-8 位元組超過 `market_story` (TEXT) 上限 → **reject，唔截斷**（唔准改內容）
  * rejected 比例 > 5% → 唔入庫，格式規則要先修

Exit codes: 0 = 正常, 1 = 冇嘢做 / fail-closed, 2 = G10 目錄唔見。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args

DEFAULT_G10_ROOT = ROOT.parent / "grade10-scraper" / "data" / "cards"
DEFAULT_COVERAGE_OUT = ROOT / "temp" / "g10-research-top100-coverage.json"
DEFAULT_REPORT_OUT = ROOT / "temp" / "g10-research-ingest-report.json"

SOURCE_CODE = "g10_research"
LOCALE_CODE = "en"
SUMMARY_FILENAME = "summary_en.json"
ASSET_FILENAME = "asset_info.json"
INDEX_CODE = "tcg-combined"

# G10 目錄 provider → `catalog_source_identity.source_code`
PROVIDER_IDENTITY_SOURCE = {"altxyz": "ebay", "snkrdunk": "snkrdunk"}

# `catalog_variant_locale.market_story` 係 MySQL TEXT
MAX_STORY_BYTES = 65535
# `localized_name` / `localized_set_name` 係 varchar(255)
MAX_NAME_CHARS = 255
REJECT_FAIL_RATIO = 0.05
TOP_N = 100


class StoryFormatError(ValueError):
    """`summary_en.json` 唔符合 `{"summary": "<str>"}` 契約。"""


@dataclass(frozen=True)
class StoryRow:
    provider: str
    external_entity_id: str
    variant_id: int
    source_path: str
    story: str
    story_sha256: str
    observed_at: datetime
    localized_name: str | None
    localized_set_name: str | None

    @property
    def char_count(self) -> int:
        return len(self.story)

    @property
    def byte_count(self) -> int:
        return len(self.story.encode("utf-8"))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def parse_summary_document(document: Any) -> str:
    """`{"summary": "<str>"}` → trim 過頭尾空白嘅原文。內容一個字都唔改。"""

    if not isinstance(document, Mapping):
        raise StoryFormatError(f"summary document is {type(document).__name__}, expected object")
    if "summary" not in document:
        raise StoryFormatError(f"summary key missing; keys={sorted(map(str, document))[:5]}")
    value = document["summary"]
    if not isinstance(value, str):
        raise StoryFormatError(f"summary is {type(value).__name__}, expected string")
    story = value.strip()
    if not story:
        raise StoryFormatError("summary is empty after trimming")
    byte_count = len(story.encode("utf-8"))
    if byte_count > MAX_STORY_BYTES:
        raise StoryFormatError(
            f"summary is {byte_count} utf-8 bytes, over the market_story TEXT limit {MAX_STORY_BYTES}"
        )
    return story


def read_story_file(path: Path) -> tuple[str, str, datetime]:
    """→ (原文, 檔案內容 sha256, 檔案 mtime UTC)。

    `observed_at` 一定用 mtime，唔用 `now()` —— 檔可能係幾日前抄落嚟，用 now 會做假 provenance。
    """

    payload = path.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StoryFormatError(f"unreadable json: {exc}") from exc
    story = parse_summary_document(document)
    observed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)
    return story, sha256_bytes(payload), observed_at


def read_asset_names(card_dir: Path) -> tuple[str | None, str | None]:
    """`asset_info.json` → (cardName, setName)。攞唔到或者過長就當冇，唔截斷、唔作數。"""

    path = card_dir / ASSET_FILENAME
    if not path.is_file():
        return None, None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None, None
    if not isinstance(document, Mapping):
        return None, None

    def pick(key: str) -> str | None:
        value = document.get(key)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > MAX_NAME_CHARS:
            return None
        return value

    return pick("cardName"), pick("setName")


def relative_source_path(path: Path, g10_root: Path) -> str:
    """G10 repo root 起計嘅相對路徑，例如 `data/cards/snkrdunk/100081/summary_en.json`。"""

    try:
        return path.relative_to(g10_root.parents[1]).as_posix()
    except (ValueError, IndexError):
        return Path("data/cards").joinpath(path.relative_to(g10_root)).as_posix()


def load_identity_map(connection: Any) -> dict[tuple[str, str], int]:
    """(G10 provider 目錄, 目錄名) → variant_id。唯一合法對應路徑。"""

    sources = sorted(set(PROVIDER_IDENTITY_SOURCE.values()))
    placeholders = ",".join(["%s"] * len(sources))
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT variant_id, source_code, external_entity_id FROM catalog_source_identity "
            f"WHERE source_code IN ({placeholders})",
            tuple(sources),
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
    identity: Mapping[tuple[str, str], int],
    limit: int | None = None,
) -> tuple[list[StoryRow], Counter, list[dict[str, Any]]]:
    """掃 G10 檔案樹。

    `observed` = 搵到嘅 `summary_en.json` 份數（冇檔嘅目錄唔算入分母）。
    `accepted` = 對到 variant 又過格式檢查。 `quarantined` = 對唔到 variant。
    `rejected` = 格式唔合契約 / 超出 TEXT 上限。
    """

    stats: Counter = Counter()
    rows: list[StoryRow] = []
    skips: list[dict[str, Any]] = []
    for provider, card_dir in iter_card_dirs(g10_root, limit):
        stats["dir_seen"] += 1
        stats[f"dir_seen_{provider}"] += 1
        summary_path = card_dir / SUMMARY_FILENAME
        if not summary_path.is_file():
            stats["dir_without_summary"] += 1
            stats[f"dir_without_summary_{provider}"] += 1
            skips.append(
                {
                    "provider": provider,
                    "externalEntityId": card_dir.name,
                    "reason": "no_summary_file",
                    "detail": f"{SUMMARY_FILENAME} not present",
                }
            )
            continue

        stats["observed"] += 1
        stats[f"observed_{provider}"] += 1
        try:
            story, story_sha256, observed_at = read_story_file(summary_path)
        except (StoryFormatError, OSError) as exc:
            stats["rejected"] += 1
            stats[f"rejected_{provider}"] += 1
            skips.append(
                {
                    "provider": provider,
                    "externalEntityId": card_dir.name,
                    "reason": "rejected_format",
                    "detail": str(exc),
                    "sourcePath": relative_source_path(summary_path, g10_root),
                }
            )
            continue

        variant_id = identity.get((provider, card_dir.name))
        if variant_id is None:
            stats["quarantined"] += 1
            stats[f"quarantined_{provider}"] += 1
            skips.append(
                {
                    "provider": provider,
                    "externalEntityId": card_dir.name,
                    "reason": "no_variant_identity",
                    "detail": (
                        f"catalog_source_identity has no row for "
                        f"source_code='{PROVIDER_IDENTITY_SOURCE[provider]}' "
                        f"external_entity_id='{card_dir.name}'"
                    ),
                    "sourcePath": relative_source_path(summary_path, g10_root),
                    "charCount": len(story),
                }
            )
            continue

        localized_name, localized_set_name = read_asset_names(card_dir)
        rows.append(
            StoryRow(
                provider=provider,
                external_entity_id=card_dir.name,
                variant_id=variant_id,
                source_path=relative_source_path(summary_path, g10_root),
                story=story,
                story_sha256=story_sha256,
                observed_at=observed_at,
                localized_name=localized_name,
                localized_set_name=localized_set_name,
            )
        )
        stats["accepted"] += 1
        stats[f"accepted_{provider}"] += 1
    return rows, stats, skips


def table_count(cursor: Any, table: str) -> int:
    cursor.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE locale_code = %s", (LOCALE_CODE,))
    return int(cursor.fetchone()["c"])


def write_all(connection: Any, rows: Sequence[StoryRow], stats: Mapping[str, int]) -> dict[str, Any]:
    if not rows:
        raise ValueError("write_all called with no rows")
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
    effective_at = max(row.observed_at for row in rows)
    payload_sha256 = sha256_text(
        "\n".join(sorted(f"{row.variant_id}:{row.story_sha256}" for row in rows))
    )
    manifest_sha256 = sha256_text("\n".join(sorted(f"{row.source_path}:{row.story_sha256}" for row in rows)))
    observed = int(stats.get("observed", len(rows)))
    quarantined = int(stats.get("quarantined", 0))
    rejected = int(stats.get("rejected", 0))

    with connection.cursor() as cursor:
        before = {
            "catalog_variant_locale": table_count(cursor, "catalog_variant_locale"),
            "catalog_story_pointer": table_count(cursor, "catalog_story_pointer"),
        }
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
            INSERT INTO catalog_variant_locale
                (variant_id, locale_code, localized_name, localized_set_name, market_story)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                localized_name=VALUES(localized_name),
                localized_set_name=VALUES(localized_set_name),
                market_story=VALUES(market_story)
            """,
            [
                (row.variant_id, LOCALE_CODE, row.localized_name, row.localized_set_name, row.story)
                for row in rows
            ],
        )
        cursor.executemany(
            """
            INSERT INTO catalog_story_pointer
                (variant_id, locale_code, source_path, source_version_sha256, observed_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                source_path=VALUES(source_path), observed_at=VALUES(observed_at)
            """,
            [
                (row.variant_id, LOCALE_CODE, row.source_path, row.story_sha256, row.observed_at)
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
                observed,
                len(rows),
                quarantined,
                rejected,
                datetime.now(timezone.utc).replace(tzinfo=None),
                run_id,
            ),
        )
        after = {
            "catalog_variant_locale": table_count(cursor, "catalog_variant_locale"),
            "catalog_story_pointer": table_count(cursor, "catalog_story_pointer"),
        }
    return {
        "run_id": run_id,
        "run_key": run_key,
        "before": before,
        "after": after,
        "counts": {
            "observed": observed,
            "accepted": len(rows),
            "quarantined": quarantined,
            "rejected": rejected,
        },
    }


def fetch_top_constituents(connection: Any, limit: int = TOP_N) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """公開榜 top N：最新 `tcg-combined` snapshot 嘅 `market_index_constituent` join `catalog_variant`。

    對齊 `pipelines/canonical_public_snapshot.py` 嘅 `latest_generation` —— 揀最新一個
    至少有 `limit` 個成分股嘅 snapshot；一個都冇就退返最新嗰個，並喺報告講明。
    """

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, effective_at, effective_date, constituent_count
            FROM market_index_snapshot
            WHERE index_code=%s
            ORDER BY effective_at DESC, id DESC
            """,
            (INDEX_CODE,),
        )
        snapshots = cursor.fetchall()
        if not snapshots:
            return None, []
        chosen = None
        for snapshot in snapshots:
            cursor.execute(
                "SELECT COUNT(*) AS n FROM market_index_constituent WHERE index_snapshot_id=%s",
                (snapshot["id"],),
            )
            if int(cursor.fetchone()["n"]) >= limit:
                chosen = dict(snapshot)
                break
        if chosen is None:
            chosen = dict(snapshots[0])
            chosen["under_target"] = True
        cursor.execute(
            """
            SELECT v.id AS variant_id, v.opaque_id, v.canonical_name, c.rank_position
            FROM market_index_constituent c
            JOIN catalog_variant v ON v.id = c.variant_id
            WHERE c.index_snapshot_id=%s AND c.rank_position<=%s
            ORDER BY c.rank_position
            """,
            (chosen["id"], limit),
        )
        return chosen, [dict(row) for row in cursor.fetchall()]


def build_coverage_rows(
    constituents: Sequence[Mapping[str, Any]],
    rows: Sequence[StoryRow],
) -> list[dict[str, Any]]:
    by_variant = {row.variant_id: row for row in rows}
    coverage: list[dict[str, Any]] = []
    for constituent in constituents:
        variant_id = int(constituent["variant_id"])
        story = by_variant.get(variant_id)
        coverage.append(
            {
                "variantId": variant_id,
                "opaqueId": str(constituent["opaque_id"]),
                "rank": int(constituent["rank_position"]),
                "canonicalName": str(constituent["canonical_name"]),
                "hasG10Research": story is not None,
                "g10Path": story.source_path if story else None,
                "charCount": story.char_count if story else 0,
            }
        )
    return coverage


def print_report(
    *,
    g10_root: Path,
    stats: Counter,
    rows: Sequence[StoryRow],
    skips: Sequence[Mapping[str, Any]],
    identity_size: int,
    coverage: Sequence[Mapping[str, Any]],
    snapshot: Mapping[str, Any] | None,
    write_result: Mapping[str, Any] | None,
    report_path: Path,
    coverage_path: Path,
) -> None:
    print(f"G10 root         : {g10_root}")
    print(f"identity rows    : {identity_size}  (catalog_source_identity, snkrdunk+ebay)")
    print("\n[掃描]")
    for provider in sorted(PROVIDER_IDENTITY_SOURCE):
        seen = stats[f"dir_seen_{provider}"]
        if not seen:
            continue
        print(
            f"  {provider:<9} 目錄 {seen:>4}"
            f" | 有 {SUMMARY_FILENAME} {stats[f'observed_{provider}']:>4}"
            f" | 入庫 {stats[f'accepted_{provider}']:>4}"
            f" | 對唔到 variant {stats[f'quarantined_{provider}']:>4}"
            f" | 格式 reject {stats[f'rejected_{provider}']:>4}"
        )
    print(
        f"  總計       目錄 {stats['dir_seen']:>4}"
        f" | observed {stats['observed']:>4}"
        f" | accepted {stats['accepted']:>4}"
        f" | quarantined {stats['quarantined']:>4}"
        f" | rejected {stats['rejected']:>4}"
    )
    if rows:
        chars = [row.char_count for row in rows]
        print(
            f"  入庫字數: 總 {sum(chars):,} | 最短 {min(chars):,} | 最長 {max(chars):,}"
            f" | 最大 utf-8 bytes {max(row.byte_count for row in rows):,} (上限 {MAX_STORY_BYTES:,})"
        )

    quarantined = [item for item in skips if item["reason"] == "no_variant_identity"]
    if quarantined:
        print(f"\n[對唔到 variant，唔准估，共 {len(quarantined)} 個目錄]")
        preview = quarantined[:20]
        print("  " + ", ".join(f"{item['provider']}/{item['externalEntityId']}" for item in preview))
        if len(quarantined) > len(preview):
            print(f"  ...(其餘 {len(quarantined) - len(preview)} 個喺 {report_path.name})")
    rejected = [item for item in skips if item["reason"] == "rejected_format"]
    if rejected:
        print(f"\n[格式 reject，共 {len(rejected)} 個]")
        for item in rejected[:20]:
            print(f"  {item['provider']}/{item['externalEntityId']}: {item['detail']}")

    if snapshot is not None:
        covered = sum(1 for item in coverage if item["hasG10Research"])
        note = "  (!! 呢個 snapshot 少過 100 個成分股)" if snapshot.get("under_target") else ""
        print(
            f"\n[公開榜 top{TOP_N} 覆蓋率] snapshot #{snapshot['id']} "
            f"effective_date={snapshot['effective_date']}{note}"
        )
        print(f"  {covered} / {len(coverage)} 張有 G10 英文研究，{len(coverage) - covered} 張冇")
    else:
        print(f"\n[公開榜 top{TOP_N} 覆蓋率] 搵唔到 index_code='{INDEX_CODE}' 嘅 snapshot")

    print("\n[報告檔]")
    print(f"  {report_path}")
    print(f"  {coverage_path}")

    if write_result:
        print("\n[實際入庫]")
        print(f"  run_id  : {write_result['run_id']}")
        print(f"  run_key : {write_result['run_key']}")
        counts = write_result["counts"]
        print(
            f"  counts  : observed={counts['observed']} accepted={counts['accepted']} "
            f"quarantined={counts['quarantined']} rejected={counts['rejected']}"
        )
        for table in ("catalog_variant_locale", "catalog_story_pointer"):
            before = write_result["before"][table]
            after = write_result["after"][table]
            print(f"  {table:<26} locale='{LOCALE_CODE}': {before} → {after}  (+{after - before})")
    else:
        print("\n[DRY-RUN] 未寫入。加 --write 先入庫。")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ingest local G10 English research summaries into CARDZ MySQL locale tables"
    )
    parser.add_argument("--g10-root", type=Path, default=DEFAULT_G10_ROOT)
    parser.add_argument("--write", action="store_true", help="真入庫（預設 dry-run）")
    parser.add_argument("--limit", type=int, default=None, help="只掃頭 N 個卡目錄")
    parser.add_argument("--coverage-out", type=Path, default=DEFAULT_COVERAGE_OUT)
    parser.add_argument("--report-out", type=Path, default=DEFAULT_REPORT_OUT)
    add_connection_args(parser)
    args = parser.parse_args()

    g10_root = args.g10_root.resolve()
    if not g10_root.is_dir():
        print(f"G10 card root not found: {g10_root}", file=sys.stderr)
        return 2

    connection = connection_from_args(args)
    try:
        identity = load_identity_map(connection)
        rows, stats, skips = collect(g10_root, identity, args.limit)
        snapshot, constituents = fetch_top_constituents(connection)
        coverage = build_coverage_rows(constituents, rows)

        observed = stats["observed"]
        reject_ratio = (stats["rejected"] / observed) if observed else 0.0

        coverage_path = args.coverage_out.resolve()
        report_path = args.report_out.resolve()
        coverage_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        coverage_path.write_text(json.dumps(coverage, indent=2, ensure_ascii=False), encoding="utf-8")

        write_result = None
        fail_reason = None
        if not rows:
            fail_reason = "冇任何可入庫嘅研究文字"
        elif reject_ratio > REJECT_FAIL_RATIO:
            fail_reason = (
                f"FAIL-CLOSED: rejected 比例 {reject_ratio * 100:.2f}% "
                f"> {REJECT_FAIL_RATIO * 100:.0f}%，唔入庫。格式規則要先修。"
            )
        elif args.write:
            try:
                write_result = write_all(connection, rows, stats)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        report_path.write_text(
            json.dumps(
                {
                    "g10Root": str(g10_root),
                    "localeCode": LOCALE_CODE,
                    "sourceCode": SOURCE_CODE,
                    "identityRows": len(identity),
                    "stats": dict(sorted(stats.items())),
                    "rejectRatio": round(reject_ratio, 6),
                    "skips": list(skips),
                    "indexSnapshot": snapshot,
                    "top100Covered": sum(1 for item in coverage if item["hasG10Research"]),
                    "top100Total": len(coverage),
                    "write": write_result,
                    "failReason": fail_reason,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
            encoding="utf-8",
        )

        print_report(
            g10_root=g10_root,
            stats=stats,
            rows=rows,
            skips=skips,
            identity_size=len(identity),
            coverage=coverage,
            snapshot=snapshot,
            write_result=write_result,
            report_path=report_path,
            coverage_path=coverage_path,
        )
        if fail_reason:
            print(f"\n!! {fail_reason}", file=sys.stderr)
            return 1
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
