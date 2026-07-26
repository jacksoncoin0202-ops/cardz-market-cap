#!/usr/bin/env python3
"""砌翻譯工作隊列：由**排名榜**倒推邊張卡真係要譯。

點解要有呢個檔：第一批 86 張翻譯係照 DB 次序攞嘅，結果得 20 張真係喺出版
名單入面 —— 66 張功夫落咗喺榜外卡度。隊列一定要跟排名，唔可以由 DB 次序
順攞。

## 點解由排名榜攞而唔係由 snapshot 攞

上一版讀 `canonical_public_snapshot.py` 出嘅 JSON，攞 `card["id"]` 去 join
`catalog_variant.opaque_id`。呢個 join **結構上永遠差一截**：tier-1/2 卡個 id
係由 presentation pack 凍住抬過嚟，而 `opaque_id` = sha256(name/set/collector)，
執過一次卡名就重新 hash 過。實測 251 張出版卡得 75 張 id 仲揾得返
`catalog_variant`，其餘 176 張一律被當成「冇英文原文」—— 明明 DB 有成 2,700
字研究文，隊列照樣報 0。**同一個閘遮住咗啲字，又遮住咗解遮嘅工作。**

排名榜（`market_index_constituent`）本身就係攞 `variant_id` 做 key，冇得漂。
出版名單 = 排名榜減走影像未齊嗰批（`image_unavailable`），嗰批遲早補圖上榜
（task #18），依家譯埋唔算做嘢落榜外卡。

流程：
    editorial_translate_queue.py                         # 呢個檔：出隊列
    （翻譯 agent 寫 temp/translate-out-*.json）
    editorial_locale_sync.py --write                     # 灌返入 DB

隊列只收「DB 已經有英文研究文」嘅卡 —— 冇英文原文就唔係翻譯問題，係
`g10_research_ingest.py` 覆蓋率問題，唔好混做一件事。英文原文喺呢度已經
過咗 `story_lead()`，agent 見到嘅就係最終出街嗰段，唔使自己判斷切邊。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import add_connection_args, connection_from_args  # noqa: E402
from editorial_localization import STORY_LOCALES, story_lead  # noqa: E402

DEFAULT_INDEX_CODE = "tcg-combined"


def ranked_variants(connection: Any, index_code: str, limit: int | None) -> list[dict[str, Any]]:
    """最新一期指數成分股，跟 rank 排。"""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id FROM market_index_snapshot
            WHERE index_code=%s ORDER BY effective_at DESC, id DESC LIMIT 1
            """,
            (index_code,),
        )
        snapshot = cursor.fetchone()
        if not snapshot:
            return []
        cursor.execute(
            """
            SELECT c.rank_position AS rank_position, v.id AS variant_id,
                   v.opaque_id AS opaque_id, v.canonical_name AS canonical_name
            FROM market_index_constituent c
            JOIN catalog_variant v ON v.id = c.variant_id
            WHERE c.index_snapshot_id=%s
            ORDER BY c.rank_position
            """,
            (int(snapshot["id"]),),
        )
        rows = [dict(row) for row in cursor.fetchall()]
    return rows[:limit] if limit else rows


def locale_state(connection: Any, variant_ids: Sequence[int]) -> dict[int, dict[str, str]]:
    """每張卡而家有邊幾語（空字串當冇）。"""

    if not variant_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(variant_ids))
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT variant_id, locale_code, market_story
            FROM catalog_variant_locale
            WHERE variant_id IN ({placeholders}) AND market_story IS NOT NULL
              AND locale_code IN ('en', 'zhTW', 'zhCN', 'ja')
            """,
            list(variant_ids),
        )
        rows = cursor.fetchall()

    state: dict[int, dict[str, str]] = {}
    for row in rows:
        story = str(row["market_story"] or "").strip()
        if story:
            state.setdefault(int(row["variant_id"]), {})[str(row["locale_code"])] = story
    return state


def partition(
    ranked: Sequence[Mapping[str, Any]], state: Mapping[int, Mapping[str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """分做 (要譯, 冇英文原文, 已齊四語)。四語缺任何一語就當要譯。"""

    queue: list[dict[str, Any]] = []
    no_english: list[dict[str, Any]] = []
    complete = 0
    for row in ranked:
        locales = state.get(int(row["variant_id"]), {})
        if all(locale in locales for locale in STORY_LOCALES):
            complete += 1
            continue
        entry = {
            "rank": row["rank_position"],
            "variantId": int(row["variant_id"]),
            "opaqueId": str(row["opaque_id"]),
            "nameEn": row["canonical_name"],
        }
        english = locales.get("en")
        if not english:
            no_english.append(entry)
            continue
        queue.append({**entry, "have": sorted(locales), "leadEn": story_lead(english)})
    return queue, no_english, complete


def build(connection: Any, index_code: str, batch_size: int, limit: int | None) -> dict[str, Any]:
    ranked = ranked_variants(connection, index_code, limit)
    state = locale_state(connection, [int(row["variant_id"]) for row in ranked])
    queue, no_english, complete = partition(ranked, state)
    batches = [queue[index : index + batch_size] for index in range(0, len(queue), batch_size)]
    return {
        "ranked": len(ranked),
        "alreadyTranslated": complete,
        "queued": len(queue),
        "noEnglishResearch": len(no_english),
        "batches": batches,
        "noEnglishResearchCards": no_english,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the editorial translation queue from the ranked index")
    parser.add_argument("--index-code", default=DEFAULT_INDEX_CODE)
    parser.add_argument("--limit", type=int, default=None, help="只取頭 N 名（預設全榜）")
    parser.add_argument("--batch-size", type=int, default=15)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "temp")
    parser.add_argument("--prefix", default="translate-in")
    add_connection_args(parser)
    args = parser.parse_args(argv)

    connection = connection_from_args(args)
    try:
        report = build(connection, args.index_code, args.batch_size, args.limit)
    finally:
        connection.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index, batch in enumerate(report["batches"], start=1):
        path = args.out_dir / f"{args.prefix}-{index}.json"
        path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(str(path))

    summary = {key: report[key] for key in ("ranked", "alreadyTranslated", "queued", "noEnglishResearch")}
    summary["batchFiles"] = written
    report_path = args.out_dir / f"{args.prefix}-report.json"
    report_path.write_text(
        json.dumps({**summary, "noEnglishResearchCards": report["noEnglishResearchCards"]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"排名榜 {summary['ranked']} 張 | 已有四語 {summary['alreadyTranslated']} "
        f"| 入隊 {summary['queued']} ({len(written)} 批, 每批 {args.batch_size}) "
        f"| 冇英文原文 {summary['noEnglishResearch']}（唔係翻譯問題，係研究文覆蓋率）",
        file=sys.stderr,
    )
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
