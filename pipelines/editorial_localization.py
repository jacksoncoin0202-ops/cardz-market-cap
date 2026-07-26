#!/usr/bin/env python3
"""把已有嘅譯名／譯文貼返落公開 snapshot。

點解要有呢個檔：三份譯文資產一路都喺 repo 入面，但冇一份接入 producer——

  * `apps/web/src/lib/card-names.ts`（414 行官方譯名）只喺前端 `snapshot.ts`
    render 嗰陣先貼上去；
  * `data/editorial/top100-stories.json`（100 條四語故事）由頭到尾冇任何
    pipeline 讀過；
  * set 名根本冇譯本。

後果係 `canonical_public_snapshot.py:259/277/278` 三個欄位永遠寫 null，
`validate.ts:280` 嘅 production 檢查一次過報 708 個 error，而 `/api/v1/*`
出返俾外部消費者嘅 `names` / `sets` / `stories` 全部係 null——網頁睇落冇事
純粹因為前端自己補返，API 消費者冇呢層補救。

呢個模組就係嗰條線：譯文檔 → snapshot → 前端同 API 兩邊都攞到同一份文字。

## 三個欄位嘅 fallback policy 唔同，唔好一刀切

`names` / `sets` 揾唔到譯名就**保留英文**。呢個唔係填假值，而係前端
`localizedCardName()` 由第一日起就係咁做（揾唔到就 return englishName），
呢度只係把同一個行為搬前到 producer，令 API 消費者同網頁見到同一樣嘢。

`stories` 揾唔到就**留 null**。故事係編輯內容唔係識別文字，冇得用英文頂
——寧願前端唔顯示，都好過扮咗有四語內容。而且 `validate.ts:284` 要求四語
互不相同，塞英文入去即刻違規。

## 故事讀 DB 優先，JSON 做 fallback

`data/editorial/top100-stories.json` 用公開 `cmc_*` id 做 key，而 `cmc_*` =
sha256(tcg, language, set_name, collector, name)，catalog 一改名就漂移
（實測 100 條得 50 條仲對得返現行 top100）。永久 key 係 `catalog_variant.id`，
所以四語文正源改咗做 `catalog_variant_locale`，由
`pipelines/editorial_locale_sync.py` 灌入，喺呢度即場 join
`catalog_variant.opaque_id` 出當日嘅公開 id ——**join 喺讀嗰刻先做，所以冇得漂**。
JSON 保留做 fallback，DB 有嗰條就贏。

## 英文行 lead，翻譯原封

DB 嘅 `en` 行係 G10 研究全文（442–4,888 字，5 段：開場敘述 + Basic Info /
Community Pulse / Card Fun Facts / Summary (TLDR) 四個結構化段）。結構化段係
研究筆記唔係出街文案，直接 ship 落 `story` 會連 markdown 標題一齊出街。
`story_lead()` 切喺第一個已知標題之前 —— 對英文係真切（→ 442–934 字，中位
619），對翻譯係 no-op（翻譯本身就係開場段譯本，冇標題），四語長度自然對齊。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CARD_NAMES = ROOT / "data" / "editorial" / "card-names.json"
SET_NAMES = ROOT / "data" / "editorial" / "set-names.json"
STORIES = ROOT / "data" / "editorial" / "top100-stories.json"

TRANSLATED_LOCALES = ("zhTW", "zhCN", "ja")
STORY_LOCALES = ("en", "zhTW", "zhCN", "ja")
STORY_MIN_CHARS = 80

# 336 條 G10 研究文實測得呢四個標題，每條都齊，而且第一個一定喺第 2 段。
# 用明確詞彙唔用「似標題」啟發式：啟發式會誤中 Basic Info 入面
# `Main subject / card theme:** Rayquaza V` 呢類粗體 bullet（實測 5 條）。
# 將來出現新標題最多係 lead 長咗一段，唔會切錯位。
STORY_SECTION_HEADS = frozenset(
    {"basic info", "community pulse", "card fun facts", "summary (tldr)", "summary", "tldr"}
)
_BLANK_LINE = re.compile(r"\n\s*\n")


def story_lead(text: str) -> str:
    """切走研究文嘅結構化段落，淨返開場敘述；冇標題就原文返返。"""

    blocks = [block.strip() for block in _BLANK_LINE.split(text.strip()) if block.strip()]
    if not blocks:
        return ""
    kept: list[str] = []
    for block in blocks:
        head = block.splitlines()[0].strip().strip("#*_ \t:").strip().casefold()
        if head in STORY_SECTION_HEADS:
            break
        kept.append(block)
    lead = "\n\n".join(kept)
    # 切到得返一撮碎片就唔切 —— 寧願出全文，都好過整出一條過唔到
    # `validate.ts:283` 80 字閘嘅故事。
    return lead if len(lead) >= STORY_MIN_CHARS else text.strip()


def _entries(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    document = json.loads(path.read_text(encoding="utf-8"))
    return dict(document.get("entries") or {})


def load_tables(connection: Any | None = None) -> dict[str, Any]:
    stories, sources = _story_table(connection)
    return {
        "names": _entries(CARD_NAMES),
        "sets": _entries(SET_NAMES),
        "stories": stories,
        "storySources": sources,
    }


def _accept(candidate: Mapping[str, Any]) -> dict[str, str] | None:
    """四語齊、每語 >=80 字、互不相同先收貨，否則掉。

    `validate.ts:283-284` 就係量呢三樣。喺呢度先隔走唔合格嘅，好過寫入
    snapshot 之後先喺 validator 度爆——嗰陣已經唔知邊條記錄衰咗。
    """

    values = [candidate.get(locale) for locale in STORY_LOCALES]
    if not all(isinstance(value, str) and value.strip() for value in values):
        return None
    trimmed = [story_lead(str(value)) for value in values]
    if any(len(value) < STORY_MIN_CHARS for value in trimmed):
        return None
    if len(set(trimmed)) != len(STORY_LOCALES):
        return None
    return dict(zip(STORY_LOCALES, trimmed))


def _db_stories(connection: Any) -> dict[str, dict[str, str]]:
    """由 `catalog_variant_locale` 讀四語，即場 join 出當日 `opaque_id` 做 key。"""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT v.opaque_id, l.locale_code, l.market_story
            FROM catalog_variant_locale l
            JOIN catalog_variant v ON v.id = l.variant_id
            WHERE l.market_story IS NOT NULL AND l.locale_code IN ('en', 'zhTW', 'zhCN', 'ja')
            """
        )
        rows = cursor.fetchall()

    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        opaque, locale, story = (row["opaque_id"], row["locale_code"], row["market_story"]) if isinstance(row, Mapping) else row
        grouped.setdefault(str(opaque), {})[str(locale)] = str(story)

    table: dict[str, dict[str, str]] = {}
    for opaque, candidate in grouped.items():
        accepted = _accept(candidate)
        if accepted:
            table[opaque] = accepted
    return table


def _json_stories() -> dict[str, dict[str, str]]:
    if not STORIES.is_file():
        return {}
    document = json.loads(STORIES.read_text(encoding="utf-8"))
    table: dict[str, dict[str, str]] = {}
    for entry in document.get("entries") or []:
        accepted = _accept(entry.get("stories") or {})
        if accepted:
            table[str(entry["id"])] = accepted
    return table


def _story_table(connection: Any | None = None) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    table = _db_stories(connection) if connection is not None else {}
    sources = {key: "db" for key in table}
    for key, value in _json_stories().items():
        if key not in table:
            table[key] = value
            sources[key] = "json"
    return table, sources


def _differs(field: Mapping[str, Any], english: str) -> bool:
    return any(field.get(locale) != english for locale in TRANSLATED_LOCALES)


def localize_cards(
    cards: Iterable[Mapping[str, Any]],
    tables: Mapping[str, Any] | None = None,
    *,
    connection: Any | None = None,
    story_keys: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """就地填 names / sets / stories，返回覆蓋率統計。

    `story_keys` 由已出版卡 id 對返當日 canonical `opaque_id`。presentation pack
    會將舊 id 凍住抬過嚟，而 `opaque_id` 係 name/set/collector 嘅 hash —— 執過一
    次卡名就重新 hash 過，舊 id 即刻查唔返 `catalog_variant`。實測 251 張出版卡
    得 75 張 id 仲對得返 DB，其餘 176 張連自己張表都揾唔到，故事就係咁樣「明明
    入咗庫但出唔到街」。冇傳就照舊用卡自己個 id。
    """

    tables = tables or load_tables(connection)
    names, sets_, stories = tables.get("names") or {}, tables.get("sets") or {}, tables.get("stories") or {}
    story_sources = tables.get("storySources") or {}
    coverage = {
        "cards": 0,
        "nameTranslated": 0,
        "nameFallbackEnglish": 0,
        "setTranslated": 0,
        "setFallbackEnglish": 0,
        "storyAttached": 0,
        "storyMissing": 0,
        "storyFromDb": 0,
        "storyFromJson": 0,
    }

    for card in cards:
        coverage["cards"] += 1

        english_name = str(card["names"].get("en") or "")
        translated = names.get(english_name) or {}
        for locale in TRANSLATED_LOCALES:
            card["names"][locale] = str(translated.get(locale) or english_name)
        # 量「有冇真係譯到」要對比英文，唔可以淨係睇張表有冇 entry ——
        # 譯名表本身收錄咗 8 個揾唔到官方譯名、值就係英文嘅 entry，
        # 當佢哋做 translated 就係自己呃自己個覆蓋率。
        coverage["nameTranslated" if _differs(card["names"], english_name) else "nameFallbackEnglish"] += 1

        english_set = str(card["sets"].get("en") or "")
        translated_set = sets_.get(english_set) or {}
        for locale in TRANSLATED_LOCALES:
            card["sets"][locale] = str(translated_set.get(locale) or english_set)
        coverage["setTranslated" if _differs(card["sets"], english_set) else "setFallbackEnglish"] += 1

        # 兩個 key 都試：DB 表用 `opaque_id`，JSON fallback 表用出版 `cmc_*` id。
        # 淨試其中一個就會攞另一邊嘅 match 嚟換 —— 實測淨試 opaque_id 會蝕返
        # 2 條 JSON 故事。canonical 優先，查唔到先回落出版 id。
        published_id = str(card.get("id") or "")
        card_id = str((story_keys or {}).get(published_id) or published_id)
        story = stories.get(card_id)
        if story is None and card_id != published_id:
            card_id = published_id
            story = stories.get(card_id)
        if story:
            for locale in STORY_LOCALES:
                card["stories"][locale] = story[locale]
            coverage["storyAttached"] += 1
            coverage["storyFromJson" if story_sources.get(card_id) == "json" else "storyFromDb"] += 1
        else:
            coverage["storyMissing"] += 1

        # `image.alt` 同 names 共用同一組文字，唔一齊更新就會出現「卡名中文、
        # alt 英文」嘅唔一致，screen reader 同 SEO 兩邊都食虧。
        alt = card.get("image", {}).get("alt")
        if isinstance(alt, dict):
            for locale in TRANSLATED_LOCALES:
                alt[locale] = card["names"][locale]

    return coverage


def coverage_summary(coverage: Mapping[str, Any]) -> str:
    return (
        "editorial localization: {cards} cards "
        "(names {nameTranslated} translated / {nameFallbackEnglish} english-fallback, "
        "sets {setTranslated} translated / {setFallbackEnglish} english-fallback, "
        "stories {storyAttached} attached [{storyFromDb} db / {storyFromJson} json] "
        "/ {storyMissing} missing)"
    ).format(**coverage)
