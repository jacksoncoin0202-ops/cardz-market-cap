#!/usr/bin/env python3
"""把譯好嘅卡片故事灌入 `catalog_variant_locale`，做一個唔會漂移嘅真源。

## 點解唔繼續用 `data/editorial/top100-stories.json`

嗰份檔用公開 `cmc_*` id 做 key，而

    opaque_id = sha256(tcg, language, set_name, collector.normalized, name)
    （`pipelines/g10_public_snapshot.py:219`）

即係 catalog 一改名、改 set 名或者補返 collector number，同一張實體卡就換咗
id，寫好嘅故事即刻孤立。實測 100 條故事得 50 條仲對得返現行 top100 —— 一半
內容係做咗嘢但接唔返。`catalog_variant_locale` 用 `variant_id` 做 key，
唔會隨 catalog 世代漂。

## 舊路徑淨係灌翻譯；rewrite 路徑可以灌 en + ko

舊 `temp/translate-out-*.json` 仍然只寫 zhTW / zhCN / ja。
`temp/i18n-rewrite/out/*.json`（五語 `stories`）連 `en` / `ko` 一齊灌。
`locale_code='en'` 嘅 G10 研究原文仍由 `g10_research_ingest.py` 負責首次入庫；
rewrite 係覆寫出街導言，唔係再跑研究 ingest。

英文長、譯文短，係故意嘅：`packages/market-data/src/validate.ts:284` 要四語
互不相同，而前端詳情頁食嘅係一段可讀嘅導言唔係成篇研究。出 snapshot 嗰陣
`editorial_localization.story_lead()` 會對四語一律做同一個開場段抽取 ——
對英文係真係截一段，對譯文係 no-op，出嚟四語長度自然對齊。

## 驗收條款同 validate.ts 對齊，唔過就唔寫

`validate.ts:280-297` 量三樣：四語齊、每語 >= 80 字、四語互不相同。呢度加多
兩層本地規矩：

  * **書面中文**（CLAUDE.md）—— zhTW / zhCN 見到「嘅咗喺唔係嗰咁佢哋」即拒。
    產品 i18n 文案唔准有口語句式。
  * **日文要有假名** —— 全漢字嘅「日文」通常係中文貼錯格，attach 之前捉返。

拒收唔會靜靜跳過：逐條印出 variant_id + 原因，並且計入 `rejected_count`。

用法：
    python -X utf8 pipelines/editorial_locale_sync.py            # dry-run
    python -X utf8 pipelines/editorial_locale_sync.py --write
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402

DEFAULT_INPUT_GLOB = str(ROOT / "temp" / "translate-out-*.json")
DEFAULT_REPORT_OUT = ROOT / "temp" / "editorial-locale-sync-report.json"

SOURCE_CODE = "editorial_locale"
TRANSLATED_LOCALES = ("zhTW", "zhCN", "ja")
FIVE_LOCALES = ("en", "zhTW", "zhCN", "ja", "ko")
MIN_CHARS = 80
# `catalog_variant_locale.market_story` 係 MySQL TEXT
MAX_STORY_BYTES = 65535

# `validate.ts:290` 嘅禁詞。中咗即刻整份 snapshot 出唔到，喺入庫就攔住。
BANNED = re.compile(
    r"trader market view|炒家市場觀察|受到市場關注，重點不只是單張報價",
    re.IGNORECASE,
)

# CLAUDE.md：產品 i18n 文案一律書面中文，唔准口語句式。
COLLOQUIAL_ZH = re.compile(r"[嘅咗喺嗰佢]|唔係|係咪|點解|乜嘢")

# 平假名 U+3040-309F、片假名 U+30A0-30FF。全漢字通常係中文貼錯格。
KANA = re.compile(r"[぀-ヿ]")


class TranslationContractError(ValueError):
    """批次檔唔符合 `[{variantId, translations:{zhTW,zhCN,ja}}]` 契約。"""


@dataclass(frozen=True)
class LocaleRow:
    variant_id: int
    locale_code: str
    story: str
    observed_at: datetime

    @property
    def story_sha256(self) -> str:
        return sha256_text(self.story)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def row_locales(row: Mapping[str, Any]) -> tuple[str, ...]:
    nested = row.get("stories") if isinstance(row.get("stories"), Mapping) else None
    if nested and any(nested.get(locale) for locale in ("en", "ko")):
        return FIVE_LOCALES
    return TRANSLATED_LOCALES


def read_translations(row: Mapping[str, Any], locales: Sequence[str] | None = None) -> dict[str, str]:
    """容忍三種寫法，但唔容忍缺語言。

    分批派工出去，回嚟嘅 key 有機會係 `translations`、`stories`，或者三個
    locale 直接攤平喺頂層。三種都收，之後統一驗。
    """

    wanted = tuple(locales) if locales else row_locales(row)
    for key in ("translations", "stories"):
        nested = row.get(key)
        if isinstance(nested, Mapping):
            return {locale: nested.get(locale) for locale in wanted}  # type: ignore[misc]
    return {locale: row.get(locale) for locale in wanted}  # type: ignore[misc]


def defect(variant_id: int, stories: Mapping[str, Any], english: str | None) -> str | None:
    """回 None 即係過關，否則回一句人睇得明嘅原因。"""

    for locale in TRANSLATED_LOCALES:
        value = stories.get(locale)
        if not isinstance(value, str) or not value.strip():
            return f"{locale} 空白"
        text = value.strip()
        if len(text) < MIN_CHARS:
            return f"{locale} 得 {len(text)} 字，短過 {MIN_CHARS}"
        if len(text.encode("utf-8")) > MAX_STORY_BYTES:
            return f"{locale} {len(text.encode('utf-8'))} bytes，爆 TEXT 上限"
        if BANNED.search(text):
            return f"{locale} 命中 validate.ts 禁詞"

    trimmed = {locale: str(stories[locale]).strip() for locale in TRANSLATED_LOCALES}
    if len(set(trimmed.values())) != len(TRANSLATED_LOCALES):
        return "三語有重複"
    # `validate.ts:284` 要四語互不相同，英文喺 DB 嗰行，所以要一齊比。
    if english is not None and english.strip() in set(trimmed.values()):
        return "有一語同英文原文完全一樣"

    for locale in ("zhTW", "zhCN"):
        hit = COLLOQUIAL_ZH.search(trimmed[locale])
        if hit:
            return f"{locale} 有口語字「{hit.group(0)}」，違反書面中文規矩"
    if not KANA.search(trimmed["ja"]):
        return "ja 冇任何假名，疑似中文貼錯格"
    return None


def defect_five(variant_id: int, stories: Mapping[str, Any]) -> str | None:
    """五語 rewrite 閘。`variant_id` 只為錯誤訊息保留。"""

    del variant_id
    for locale in FIVE_LOCALES:
        value = stories.get(locale)
        if not isinstance(value, str) or not value.strip():
            return f"{locale} 空白"
        text = value.strip()
        if len(text) < MIN_CHARS:
            return f"{locale} 得 {len(text)} 字，短過 {MIN_CHARS}"
        if len(text.encode("utf-8")) > MAX_STORY_BYTES:
            return f"{locale} {len(text.encode('utf-8'))} bytes，爆 TEXT 上限"
        if BANNED.search(text):
            return f"{locale} 命中 validate.ts 禁詞"
    trimmed = {locale: str(stories[locale]).strip() for locale in FIVE_LOCALES}
    if len(set(trimmed.values())) != len(FIVE_LOCALES):
        return "五語有重複"
    for locale in ("zhTW", "zhCN"):
        hit = COLLOQUIAL_ZH.search(trimmed[locale])
        if hit:
            return f"{locale} 有口語字「{hit.group(0)}」，違反書面中文規矩"
    if not KANA.search(trimmed["ja"]):
        return "ja 冇任何假名，疑似中文貼錯格"
    return None


def load_batches(
    pattern: str,
) -> tuple[dict[int, dict[str, str]], dict[int, datetime], list[int], list[str]]:
    """讀曬批次檔，回 (譯文表, 重複 variantId, 讀過嘅檔)。"""

    incoming: dict[int, dict[str, str]] = {}
    collisions: list[int] = []
    files: list[str] = []
    observed_by_variant: dict[int, datetime] = {}
    for path in sorted(glob.glob(pattern)):
        source_path = Path(path)
        files.append(source_path.name)
        observed_at = datetime.fromtimestamp(
            source_path.stat().st_mtime, tz=timezone.utc
        ).replace(tzinfo=None)
        document = json.loads(source_path.read_text(encoding="utf-8"))
        rows = document.get("cards") if isinstance(document, Mapping) else document
        if not isinstance(rows, list):
            raise TranslationContractError(f"{path}：頂層唔係 list 又冇 `cards`")
        for row in rows:
            if not isinstance(row, Mapping) or "variantId" not in row:
                raise TranslationContractError(f"{path}：有 row 冇 `variantId`")
            variant_id = int(row["variantId"])
            if variant_id in incoming:
                collisions.append(variant_id)
            incoming[variant_id] = read_translations(row)
            observed_by_variant[variant_id] = observed_at
    return incoming, observed_by_variant, collisions, files


def incoming_locales(stories: Mapping[str, Any]) -> tuple[str, ...]:
    if any(stories.get(locale) for locale in ("en", "ko")):
        return FIVE_LOCALES
    return TRANSLATED_LOCALES


def load_english(connection: Any, variant_ids: Sequence[int]) -> dict[int, str]:
    if not variant_ids:
        return {}
    placeholders = ",".join(["%s"] * len(variant_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT variant_id, market_story
            FROM catalog_variant_locale
            WHERE locale_code='en' AND variant_id IN ({placeholders})
            """,
            list(variant_ids),
        )
        return {int(r["variant_id"]): str(r["market_story"] or "") for r in cursor.fetchall()}


def known_variants(connection: Any, variant_ids: Sequence[int]) -> set[int]:
    if not variant_ids:
        return set()
    placeholders = ",".join(["%s"] * len(variant_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT id FROM catalog_variant WHERE id IN ({placeholders})", list(variant_ids)
        )
        return {int(r["id"]) for r in cursor.fetchall()}


def collect(connection: Any, pattern: str) -> tuple[list[LocaleRow], dict[str, Any]]:
    incoming, observed_by_variant, collisions, files = load_batches(pattern)
    variant_ids = sorted(incoming)
    english = load_english(connection, variant_ids)
    known = known_variants(connection, variant_ids)

    rows: list[LocaleRow] = []
    rejected: list[tuple[int, str]] = []
    accepted_cards = 0
    for variant_id in variant_ids:
        if variant_id not in known:
            rejected.append((variant_id, "catalog_variant 冇呢個 id"))
            continue
        stories = incoming[variant_id]
        locales = incoming_locales(stories)
        reason = (
            defect_five(variant_id, stories)
            if locales == FIVE_LOCALES
            else defect(variant_id, stories, english.get(variant_id))
        )
        if reason:
            rejected.append((variant_id, reason))
            continue
        accepted_cards += 1
        for locale in locales:
            rows.append(
                LocaleRow(
                    variant_id=variant_id,
                    locale_code=locale,
                    story=str(stories[locale]).strip(),
                    observed_at=observed_by_variant[variant_id],
                )
            )

    stats = {
        "files": files,
        "observed_cards": len(incoming),
        "accepted_cards": accepted_cards,
        "rejected_cards": len(rejected),
        "collisions": collisions,
        "missing_english": sorted(set(variant_ids) - set(english)),
        "rejections": [{"variantId": vid, "reason": reason} for vid, reason in rejected],
    }
    return rows, stats


def locale_counts(cursor: Any) -> dict[str, int]:
    cursor.execute(
        "SELECT locale_code, COUNT(*) AS c FROM catalog_variant_locale GROUP BY locale_code"
    )
    return {str(r["locale_code"]): int(r["c"]) for r in cursor.fetchall()}


def write_all(connection: Any, rows: Sequence[LocaleRow], stats: Mapping[str, Any]) -> dict[str, Any]:
    if not rows:
        raise ValueError("write_all called with no rows")
    started_at = datetime.now(timezone.utc).replace(tzinfo=None)
    run_key = sha256_text(f"{SOURCE_CODE}|{started_at.isoformat()}")
    payload_sha256 = sha256_text(
        "\n".join(sorted(f"{r.variant_id}:{r.locale_code}:{r.story_sha256}" for r in rows))
    )
    observed = int(stats["observed_cards"])
    rejected = int(stats["rejected_cards"])

    with connection.cursor() as cursor:
        before = locale_counts(cursor)
        cursor.execute(
            """
            INSERT INTO market_ingest_run
                (run_key, source_code, ingest_mode, effective_at, payload_sha256, manifest_sha256,
                 status, observed_count, started_at)
            VALUES (%s, %s, 'backfill', %s, %s, %s, 'running', %s, %s)
            """,
            (run_key, SOURCE_CODE, started_at, payload_sha256, payload_sha256, observed, started_at),
        )
        run_id = int(cursor.lastrowid)

        cursor.executemany(
            """
            INSERT INTO catalog_variant_locale
                (variant_id, locale_code, market_story, provenance_source_code,
                 content_sha256, observed_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                market_story=VALUES(market_story),
                provenance_source_code=VALUES(provenance_source_code),
                content_sha256=VALUES(content_sha256),
                observed_at=VALUES(observed_at)
            """,
            [
                (
                    r.variant_id, r.locale_code, r.story, SOURCE_CODE,
                    r.story_sha256, r.observed_at,
                )
                for r in rows
            ],
        )
        cursor.execute(
            """
            UPDATE market_ingest_run
            SET status='completed', observed_count=%s, accepted_count=%s,
                quarantined_count=0, rejected_count=%s, completed_at=%s
            WHERE id=%s
            """,
            (observed, int(stats["accepted_cards"]), rejected,
             datetime.now(timezone.utc).replace(tzinfo=None), run_id),
        )
        after = locale_counts(cursor)
    connection.commit()
    return {"run_id": run_id, "before": before, "after": after}


def print_report(rows: Sequence[LocaleRow], stats: Mapping[str, Any], written: Mapping[str, Any] | None) -> None:
    print(f"讀入批次 {len(stats['files'])} 個：{', '.join(stats['files']) or '（冇檔）'}")
    print(
        f"卡片 observed {stats['observed_cards']}｜accepted {stats['accepted_cards']}"
        f"｜rejected {stats['rejected_cards']}｜寫入行數 {len(rows)}"
    )
    if stats["collisions"]:
        print(f"⚠ 批次之間重複 variantId {sorted(set(stats['collisions']))}（後蓋前）")
    if stats["missing_english"]:
        print(
            f"⚠ {len(stats['missing_english'])} 張冇英文原文可對比（四語互異只驗到三語）："
            f"{stats['missing_english'][:12]}"
        )
    if stats["rejections"]:
        print(f"❌ 拒收 {len(stats['rejections'])} 張：")
        for item in stats["rejections"]:
            print(f"   variant {item['variantId']} — {item['reason']}")
    if written:
        before, after = written["before"], written["after"]
        print(f"✅ run_id {written['run_id']}｜catalog_variant_locale 逐 locale：")
        for locale in FIVE_LOCALES:
            print(f"   {locale:<6} {before.get(locale, 0):>4} → {after.get(locale, 0):>4}")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-glob", default=DEFAULT_INPUT_GLOB)
    parser.add_argument(
        "--rewrite-glob",
        action="store_true",
        help="讀 temp/i18n-rewrite/out/batch-*.json（五語，連 en/ko）",
    )
    parser.add_argument("--write", action="store_true", help="真係寫入 DB（預設 dry-run）")
    parser.add_argument("--json-out", type=Path, default=DEFAULT_REPORT_OUT)
    add_connection_args(parser)
    args = parser.parse_args(list(argv) if argv is not None else None)

    input_glob = args.input_glob
    if args.rewrite_glob and args.input_glob == DEFAULT_INPUT_GLOB:
        input_glob = str(ROOT / "temp" / "i18n-rewrite" / "out" / "batch-*.json")

    connection = connection_from_args(args)
    try:
        rows, stats = collect(connection, input_glob)
        written = None
        if args.write:
            if not rows:
                print("冇任何合格譯文，唔寫。")
                return 1
            written = write_all(connection, rows, stats)
        print_report(rows, stats, written)
    finally:
        connection.close()

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(
        json.dumps(
            {"stats": dict(stats), "written": written, "rows": len(rows)},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if not args.write:
        print(f"dry-run：冇寫 DB。報告 → {args.json_out.relative_to(ROOT)}")
    return 1 if stats["rejected_cards"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
