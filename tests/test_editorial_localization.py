"""`editorial_localization.py` 嘅測試 —— 呢個模組係四語文案落 snapshot 嘅唯一出口。

三個真係會靜靜地衰嘅位，所以要有測試守住：

1. `story_lead()` 切錯位 —— 切少咗會連 markdown 標題一齊出街，切多咗會出一條
   過唔到 `validate.ts:283` 80 字閘嘅故事。三種標題裝飾（`### X` / 裸 `X` /
   `**X**`）喺 336 條真研究文入面全部出現過，三種都要試。
2. DB / JSON 優先次序調轉 —— JSON 嘅 `cmc_*` key 會隨 catalog 漂移，DB 唔會。
   DB 有嗰條一定要贏，否則就係用緊漂移咗嘅舊文。
3. 覆蓋率數字呃自己 —— `_differs()` 存在就係因為譯名表收錄咗 8 個值等於英文嘅
   entry，當佢哋做「已譯」會報一個假嘅 100%。
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "editorial_localization.py"
SPEC = importlib.util.spec_from_file_location("cardz_editorial_localization", MODULE_PATH)
assert SPEC and SPEC.loader
localization = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = localization
SPEC.loader.exec_module(localization)


def lead(seed: str = "a") -> str:
    return "This card debuted in 2016 as one of the most closely watched promos of its era. " + seed * 40


def zh_tw(seed: str = "甲") -> str:
    return "這張卡片於二〇一六年推出，屬於當時最受矚目的promo系列之一。" + seed * 60


def zh_cn(seed: str = "乙") -> str:
    return "这张卡片于二〇一六年推出，属于当时最受瞩目的promo系列之一。" + seed * 60


def ja(seed: str = "丙") -> str:
    return "このカードは二〇一六年に登場し、当時もっとも注目されたプロモの一つである。" + seed * 60


def research(head: str = "### Basic Info") -> str:
    """開場段 + 結構化段 —— G10 研究文嘅真實形狀。"""

    return "\n\n".join(
        [
            lead(),
            f"{head}\nRelease year: 2016\nSet: XY Evolutions",
            "Community Pulse\nCollectors keep chasing the first-edition print run.",
            "Summary (TLDR)\nStill the benchmark promo for the era.",
        ]
    )


def four(**overrides: str) -> dict[str, str]:
    base = {"en": lead(), "zhTW": zh_tw(), "zhCN": zh_cn(), "ja": ja()}
    base.update(overrides)
    return base


class FakeCursor:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str, params: object = None) -> None:
        self.executed.append(sql)

    def fetchall(self) -> list:
        return self._rows


class FakeConnection:
    """夠用嚟行 `_db_stories()` 就得 —— 唔使真 DB。"""

    def __init__(self, rows: list) -> None:
        self.cursor_obj = FakeCursor(rows)

    @contextmanager
    def cursor(self):
        yield self.cursor_obj


def db_rows(opaque: str, stories: dict[str, str]) -> list[dict[str, str]]:
    return [
        {"opaque_id": opaque, "locale_code": locale, "market_story": text}
        for locale, text in stories.items()
    ]


class StoryLeadTests(unittest.TestCase):
    def test_cuts_at_hash_heading(self) -> None:
        self.assertEqual(localization.story_lead(research("### Basic Info")), lead())

    def test_cuts_at_bare_heading(self) -> None:
        self.assertEqual(localization.story_lead(research("Basic Info")), lead())

    def test_cuts_at_bold_heading(self) -> None:
        self.assertEqual(localization.story_lead(research("**Basic Info**")), lead())

    def test_cuts_at_trailing_colon_heading(self) -> None:
        self.assertEqual(localization.story_lead(research("**Basic Info:**")), lead())

    def test_keeps_multi_paragraph_lead(self) -> None:
        text = "\n\n".join([lead("a"), lead("b"), "### Basic Info\nRelease year: 2016"])
        self.assertEqual(localization.story_lead(text), lead("a") + "\n\n" + lead("b"))

    def test_noop_on_translation_without_headings(self) -> None:
        self.assertEqual(localization.story_lead(zh_tw()), zh_tw())

    def test_returns_whole_text_when_lead_too_short(self) -> None:
        # 切完唔夠 80 字寧願出全文 —— 唔好整出一條必定過唔到 validate.ts 嘅故事。
        text = "Too short.\n\n### Basic Info\n" + "Release year: 2016. " * 10
        self.assertEqual(localization.story_lead(text), text.strip())

    def test_bold_bullet_inside_section_is_not_a_heading(self) -> None:
        # 實測 5 條研究文有 `Main subject / card theme:** Rayquaza V` 呢類粗體
        # bullet。用「似標題」啟發式會喺呢度切錯，用明確詞彙就唔會。
        text = lead() + "\n\nMain subject / card theme:** Rayquaza V\n\n### Basic Info\nRelease year: 2016"
        self.assertEqual(localization.story_lead(text), lead() + "\n\nMain subject / card theme:** Rayquaza V")

    def test_empty_input(self) -> None:
        self.assertEqual(localization.story_lead("   \n\n  "), "")


class AcceptTests(unittest.TestCase):
    def test_accepts_four_distinct_locales(self) -> None:
        self.assertEqual(localization._accept(four()), four())

    def test_applies_story_lead_to_english(self) -> None:
        accepted = localization._accept(four(en=research()))
        assert accepted is not None
        self.assertEqual(accepted["en"], lead())

    def test_rejects_missing_locale(self) -> None:
        candidate = four()
        del candidate["ja"]
        self.assertIsNone(localization._accept(candidate))

    def test_rejects_blank_locale(self) -> None:
        self.assertIsNone(localization._accept(four(ja="   ")))

    def test_rejects_short_locale(self) -> None:
        self.assertIsNone(localization._accept(four(ja="短い。")))

    def test_rejects_duplicate_locales(self) -> None:
        self.assertIsNone(localization._accept(four(zhCN=zh_tw())))

    def test_rejects_non_string(self) -> None:
        self.assertIsNone(localization._accept(four(ja=None)))  # type: ignore[arg-type]


class DbStoriesTests(unittest.TestCase):
    def test_groups_by_opaque_id(self) -> None:
        rows = db_rows("cmc_aaa", four()) + db_rows("cmc_bbb", four(zhTW=zh_tw("丁")))
        table = localization._db_stories(FakeConnection(rows))
        self.assertEqual(set(table), {"cmc_aaa", "cmc_bbb"})
        self.assertEqual(table["cmc_bbb"]["zhTW"], zh_tw("丁"))

    def test_joins_catalog_variant_for_live_opaque_id(self) -> None:
        # 個 join 就係防漂移嘅機制本身，SQL 冇咗佢就等於返去用死 key。
        connection = FakeConnection(db_rows("cmc_aaa", four()))
        localization._db_stories(connection)
        sql = " ".join(connection.cursor_obj.executed[0].split())
        self.assertIn("JOIN catalog_variant v ON v.id = l.variant_id", sql)
        self.assertIn("v.opaque_id", sql)

    def test_drops_incomplete_variant(self) -> None:
        incomplete = {locale: text for locale, text in four().items() if locale != "ja"}
        rows = db_rows("cmc_aaa", four()) + db_rows("cmc_bbb", incomplete)
        table = localization._db_stories(FakeConnection(rows))
        self.assertEqual(set(table), {"cmc_aaa"})

    def test_accepts_tuple_rows(self) -> None:
        rows = [("cmc_aaa", locale, text) for locale, text in four().items()]
        table = localization._db_stories(FakeConnection(rows))
        self.assertEqual(set(table), {"cmc_aaa"})


class StoryTableTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.json_path = Path(self.tmp.name) / "top100-stories.json"

    def write_json(self, entries: list[dict]) -> None:
        self.json_path.write_text(json.dumps({"entries": entries}, ensure_ascii=False), encoding="utf-8")

    def test_db_wins_over_json_on_same_id(self) -> None:
        self.write_json([{"id": "cmc_aaa", "stories": four(zhTW=zh_tw("戊"))}])
        with mock.patch.object(localization, "STORIES", self.json_path):
            table, sources = localization._story_table(FakeConnection(db_rows("cmc_aaa", four())))
        self.assertEqual(table["cmc_aaa"]["zhTW"], zh_tw())
        self.assertEqual(sources["cmc_aaa"], "db")

    def test_json_fills_ids_db_lacks(self) -> None:
        self.write_json([{"id": "cmc_bbb", "stories": four()}])
        with mock.patch.object(localization, "STORIES", self.json_path):
            table, sources = localization._story_table(FakeConnection(db_rows("cmc_aaa", four())))
        self.assertEqual(sources, {"cmc_aaa": "db", "cmc_bbb": "json"})

    def test_json_only_without_connection(self) -> None:
        self.write_json([{"id": "cmc_bbb", "stories": four()}])
        with mock.patch.object(localization, "STORIES", self.json_path):
            table, sources = localization._story_table(None)
        self.assertEqual(sources, {"cmc_bbb": "json"})

    def test_missing_json_file_is_not_fatal(self) -> None:
        with mock.patch.object(localization, "STORIES", Path(self.tmp.name) / "nope.json"):
            table, sources = localization._story_table(FakeConnection(db_rows("cmc_aaa", four())))
        self.assertEqual(set(table), {"cmc_aaa"})


def card(card_id: str = "cmc_aaa", name: str = "Van Gogh Pikachu", set_name: str = "Van Gogh Museum") -> dict:
    return {
        "id": card_id,
        "names": {"en": name, "zhTW": None, "zhCN": None, "ja": None},
        "sets": {"en": set_name, "zhTW": None, "zhCN": None, "ja": None},
        "stories": {"en": None, "zhTW": None, "zhCN": None, "ja": None},
        "image": {"alt": {"en": name, "zhTW": None, "zhCN": None, "ja": None}},
    }


class LocalizeCardsTests(unittest.TestCase):
    def tables(self, **overrides) -> dict:
        base = {"names": {}, "sets": {}, "stories": {}, "storySources": {}}
        base.update(overrides)
        return base

    def test_counts_db_and_json_provenance_separately(self) -> None:
        cards = [card("cmc_aaa"), card("cmc_bbb"), card("cmc_ccc")]
        tables = self.tables(
            stories={"cmc_aaa": four(), "cmc_bbb": four()},
            storySources={"cmc_aaa": "db", "cmc_bbb": "json"},
        )
        coverage = localization.localize_cards(cards, tables)
        self.assertEqual(coverage["storyFromDb"], 1)
        self.assertEqual(coverage["storyFromJson"], 1)
        self.assertEqual(coverage["storyAttached"], 2)
        self.assertEqual(coverage["storyMissing"], 1)

    def test_attaches_all_four_locales(self) -> None:
        cards = [card()]
        localization.localize_cards(cards, self.tables(stories={"cmc_aaa": four()}, storySources={"cmc_aaa": "db"}))
        self.assertEqual(cards[0]["stories"], four())

    def test_missing_story_stays_null(self) -> None:
        # 故事冇得用英文頂 —— `validate.ts:284` 要四語互不相同。
        cards = [card()]
        localization.localize_cards(cards, self.tables())
        self.assertEqual(cards[0]["stories"], {"en": None, "zhTW": None, "zhCN": None, "ja": None})

    def test_name_falls_back_to_english(self) -> None:
        cards = [card()]
        coverage = localization.localize_cards(cards, self.tables())
        self.assertEqual(cards[0]["names"]["ja"], "Van Gogh Pikachu")
        self.assertEqual(coverage["nameFallbackEnglish"], 1)
        self.assertEqual(coverage["nameTranslated"], 0)

    def test_entry_equal_to_english_counts_as_fallback(self) -> None:
        # 譯名表有 8 個值等於英文嘅 entry；當佢哋做「已譯」就係假覆蓋率。
        names = {"Van Gogh Pikachu": {"zhTW": "Van Gogh Pikachu", "zhCN": "Van Gogh Pikachu", "ja": "Van Gogh Pikachu"}}
        coverage = localization.localize_cards([card()], self.tables(names=names))
        self.assertEqual(coverage["nameFallbackEnglish"], 1)
        self.assertEqual(coverage["nameTranslated"], 0)

    def test_partial_translation_counts_as_translated(self) -> None:
        names = {"Van Gogh Pikachu": {"ja": "ゴッホピカチュウ"}}
        cards = [card()]
        coverage = localization.localize_cards(cards, self.tables(names=names))
        self.assertEqual(coverage["nameTranslated"], 1)
        self.assertEqual(cards[0]["names"]["ja"], "ゴッホピカチュウ")
        self.assertEqual(cards[0]["names"]["zhTW"], "Van Gogh Pikachu")

    def test_image_alt_follows_names(self) -> None:
        names = {"Van Gogh Pikachu": {"zhTW": "梵谷皮卡丘", "zhCN": "梵高皮卡丘", "ja": "ゴッホピカチュウ"}}
        cards = [card()]
        localization.localize_cards(cards, self.tables(names=names))
        self.assertEqual(cards[0]["image"]["alt"]["zhTW"], "梵谷皮卡丘")
        self.assertEqual(cards[0]["image"]["alt"]["en"], "Van Gogh Pikachu")

    def test_set_translation_counted_separately(self) -> None:
        sets_ = {"Van Gogh Museum": {"zhTW": "梵谷博物館", "zhCN": "梵高博物馆", "ja": "ゴッホ美術館"}}
        coverage = localization.localize_cards([card()], self.tables(sets=sets_))
        self.assertEqual(coverage["setTranslated"], 1)
        self.assertEqual(coverage["setFallbackEnglish"], 0)

    def test_loads_tables_from_connection_when_none_given(self) -> None:
        cards = [card("cmc_aaa")]
        with mock.patch.object(localization, "STORIES", Path(tempfile.gettempdir()) / "no-such-stories.json"):
            with mock.patch.object(localization, "CARD_NAMES", Path(tempfile.gettempdir()) / "no-such-names.json"):
                with mock.patch.object(localization, "SET_NAMES", Path(tempfile.gettempdir()) / "no-such-sets.json"):
                    coverage = localization.localize_cards(cards, connection=FakeConnection(db_rows("cmc_aaa", four())))
        self.assertEqual(coverage["storyFromDb"], 1)
        self.assertEqual(cards[0]["stories"]["ja"], ja())


class CoverageSummaryTests(unittest.TestCase):
    def test_renders_provenance(self) -> None:
        coverage = localization.localize_cards([card()], {"names": {}, "sets": {}, "stories": {}, "storySources": {}})
        summary = localization.coverage_summary(coverage)
        self.assertIn("[0 db / 0 json]", summary)
        self.assertIn("1 cards", summary)


if __name__ == "__main__":
    unittest.main()
