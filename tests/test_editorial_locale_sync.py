from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "editorial_locale_sync.py"
SPEC = importlib.util.spec_from_file_location("cardz_editorial_locale_sync", MODULE_PATH)
assert SPEC and SPEC.loader
sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = sync
SPEC.loader.exec_module(sync)


def zh_tw(seed: str = "甲") -> str:
    return "這張卡片於二〇一六年推出，屬於當時最受矚目的promo系列之一。" + seed * 60


def zh_cn(seed: str = "乙") -> str:
    return "这张卡片于二〇一六年推出，属于当时最受瞩目的promo系列之一。" + seed * 60


def ja(seed: str = "丙") -> str:
    return "このカードは二〇一六年に登場し、当時もっとも注目されたプロモの一つである。" + seed * 60


def stories(**overrides: str) -> dict[str, str]:
    base = {"zhTW": zh_tw(), "zhCN": zh_cn(), "ja": ja()}
    base.update(overrides)
    return base


class DefectTests(unittest.TestCase):
    def test_accepts_three_distinct_locales(self) -> None:
        self.assertIsNone(sync.defect(1, stories(), "An English research dossier."))

    def test_rejects_missing_locale(self) -> None:
        broken = stories()
        del broken["ja"]
        self.assertIn("ja", str(sync.defect(1, broken, None)))

    def test_rejects_blank_locale(self) -> None:
        self.assertIn("zhTW", str(sync.defect(1, stories(zhTW="   "), None)))

    def test_rejects_short_story(self) -> None:
        """`validate.ts:286` 要每語 >= 80 字，短過就唔好入庫等佢喺 snapshot 先炸。"""
        reason = sync.defect(1, stories(zhCN="太短了。"), None)
        self.assertIn("短過 80", str(reason))

    def test_rejects_duplicate_locales(self) -> None:
        """四語互不相同係 `validate.ts:284` 硬要求。"""
        same = zh_tw()
        self.assertEqual("三語有重複", sync.defect(1, stories(zhTW=same, zhCN=same), None))

    def test_rejects_translation_equal_to_english(self) -> None:
        """英文行喺 DB 唔喺輸入檔，所以要另外攞返嚟比，否則四語互異驗漏一邊。"""
        english = ("An English research dossier long enough to clear the eighty character floor, "
                   "so the only thing left to trip on is the distinctness rule.")
        reason = sync.defect(1, stories(zhTW=english), english)
        self.assertEqual("有一語同英文原文完全一樣", reason)

    def test_english_none_still_validates_the_other_three(self) -> None:
        same = zh_tw()
        self.assertEqual("三語有重複", sync.defect(1, stories(zhTW=same, zhCN=same), None))

    def test_rejects_colloquial_cantonese(self) -> None:
        """CLAUDE.md：產品 i18n 文案一律書面中文。"""
        reason = sync.defect(1, stories(zhTW="呢張卡嘅價值好高" + "甲" * 80), None)
        self.assertIn("口語字", str(reason))

    def test_rejects_banned_phrase(self) -> None:
        """`validate.ts:290` 禁詞，喺入庫攔住好過喺出 snapshot 先炸。"""
        reason = sync.defect(1, stories(zhTW="炒家市場觀察：" + "甲" * 90), None)
        self.assertIn("禁詞", str(reason))

    def test_rejects_japanese_without_kana(self) -> None:
        """全漢字嘅「日文」通常係中文貼錯格。"""
        reason = sync.defect(1, stories(ja="此卡於二〇一六年推出，屬當時最受矚目之促銷品。" + "丁" * 60), None)
        self.assertIn("假名", str(reason))

    def test_accepts_katakana_only_japanese(self) -> None:
        katakana = "コノカードハニセンジュウロクネンニトウジョウシタプロモデアル。" + "ア" * 60
        self.assertIsNone(sync.defect(1, stories(ja=katakana), None))

    def test_rejects_story_over_text_column_limit(self) -> None:
        reason = sync.defect(1, stories(zhCN="乙" * (sync.MAX_STORY_BYTES // 3 + 10)), None)
        self.assertIn("bytes", str(reason))


class ReadTranslationsTests(unittest.TestCase):
    """三個批次 agent 回三種形狀，全部要收，但唔可以容忍缺語言。"""

    def test_reads_translations_key(self) -> None:
        payload = {"variantId": 1, "translations": {"zhTW": "a", "zhCN": "b", "ja": "c"}}
        self.assertEqual({"zhTW": "a", "zhCN": "b", "ja": "c"}, sync.read_translations(payload))

    def test_reads_stories_key(self) -> None:
        payload = {"variantId": 1, "stories": {"zhTW": "a", "zhCN": "b", "ja": "c"}}
        self.assertEqual({"zhTW": "a", "zhCN": "b", "ja": "c"}, sync.read_translations(payload))

    def test_reads_flat_shape(self) -> None:
        payload = {"variantId": 1, "zhTW": "a", "zhCN": "b", "ja": "c"}
        self.assertEqual({"zhTW": "a", "zhCN": "b", "ja": "c"}, sync.read_translations(payload))

    def test_missing_locale_surfaces_as_none_not_keyerror(self) -> None:
        payload = {"variantId": 1, "stories": {"zhTW": "a"}}
        self.assertEqual({"zhTW": "a", "zhCN": None, "ja": None}, sync.read_translations(payload))


class LoadBatchesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name: str, payload: object) -> None:
        (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def test_merges_multiple_batches(self) -> None:
        self.write("translate-out-1.json", [{"variantId": 1, "stories": stories()}])
        self.write("translate-out-2.json", [{"variantId": 2, "stories": stories()}])
        incoming, collisions, files = sync.load_batches(str(self.dir / "translate-out-*.json"))
        self.assertEqual({1, 2}, set(incoming))
        self.assertEqual([], collisions)
        self.assertEqual(2, len(files))

    def test_reports_cross_batch_collisions(self) -> None:
        """兩個 agent 派到同一張卡係派工出錯，唔好靜靜地一個蓋一個。"""
        self.write("translate-out-1.json", [{"variantId": 7, "stories": stories()}])
        self.write("translate-out-2.json", [{"variantId": 7, "stories": stories()}])
        _, collisions, _ = sync.load_batches(str(self.dir / "translate-out-*.json"))
        self.assertEqual([7], collisions)

    def test_accepts_cards_wrapper(self) -> None:
        self.write("translate-out-1.json", {"cards": [{"variantId": 3, "stories": stories()}]})
        incoming, _, _ = sync.load_batches(str(self.dir / "translate-out-*.json"))
        self.assertEqual({3}, set(incoming))

    def test_rejects_row_without_variant_id(self) -> None:
        self.write("translate-out-1.json", [{"rank": 1, "stories": stories()}])
        with self.assertRaises(sync.TranslationContractError):
            sync.load_batches(str(self.dir / "translate-out-*.json"))

    def test_rejects_unrecognised_top_level(self) -> None:
        self.write("translate-out-1.json", {"rows": []})
        with self.assertRaises(sync.TranslationContractError):
            sync.load_batches(str(self.dir / "translate-out-*.json"))

    def test_empty_glob_is_not_an_error(self) -> None:
        incoming, collisions, files = sync.load_batches(str(self.dir / "nothing-*.json"))
        self.assertEqual(({}, [], []), (incoming, collisions, files))


class LocaleRowTests(unittest.TestCase):
    def test_sha_is_content_addressed(self) -> None:
        a = sync.LocaleRow(1, "zhTW", "同一段字")
        b = sync.LocaleRow(2, "ja", "同一段字")
        self.assertEqual(a.story_sha256, b.story_sha256)

    def test_locale_set_matches_reader(self) -> None:
        """寫入端同 `editorial_localization.TRANSLATED_LOCALES` 要對齊，否則灌完讀唔到。"""
        self.assertEqual(("zhTW", "zhCN", "ja"), sync.TRANSLATED_LOCALES)


class FakeCursor:
    def __init__(self, store: dict[tuple[int, str], str], variants: set[int],
                 english: dict[int, str]) -> None:
        self.store = store
        self.variants = variants
        self.english = english
        self._rows: list[dict[str, object]] = []
        self.lastrowid = 4242
        self.statements: list[str] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, sql: str, params: object = None) -> None:
        self.statements.append(sql)
        text = " ".join(sql.split())
        if "FROM catalog_variant_locale" in text and "locale_code='en'" in text:
            ids = list(params or [])
            self._rows = [{"variant_id": v, "market_story": self.english[v]}
                          for v in ids if v in self.english]
        elif "FROM catalog_variant WHERE" in text:
            ids = list(params or [])
            self._rows = [{"id": v} for v in ids if v in self.variants]
        elif "GROUP BY locale_code" in text:
            counts: dict[str, int] = {}
            for _, locale in self.store:
                counts[locale] = counts.get(locale, 0) + 1
            for variant in self.english:
                counts["en"] = counts.get("en", 0) + 1
            self._rows = [{"locale_code": k, "c": v} for k, v in sorted(counts.items())]
        else:
            self._rows = []

    def executemany(self, sql: str, rows: list[tuple[object, ...]]) -> None:
        self.statements.append(sql)
        for variant_id, locale, story in rows:  # type: ignore[misc]
            self.store[(int(variant_id), str(locale))] = str(story)

    def fetchall(self) -> list[dict[str, object]]:
        return self._rows


class FakeConnection:
    def __init__(self, variants: set[int], english: dict[int, str] | None = None) -> None:
        self.store: dict[tuple[int, str], str] = {}
        self.variants = variants
        self.english = english or {}
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.store, self.variants, self.english)

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        return None


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, name: str, payload: object) -> None:
        (self.dir / name).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    @property
    def pattern(self) -> str:
        return str(self.dir / "translate-out-*.json")

    def test_expands_one_card_into_three_rows(self) -> None:
        self.write("translate-out-1.json", [{"variantId": 5, "stories": stories()}])
        rows, stats = sync.collect(FakeConnection({5}), self.pattern)
        self.assertEqual(3, len(rows))
        self.assertEqual({"zhTW", "zhCN", "ja"}, {r.locale_code for r in rows})
        self.assertEqual(1, stats["accepted_cards"])
        self.assertEqual(0, stats["rejected_cards"])

    def test_rejects_unknown_variant(self) -> None:
        """`variant_id` 對唔返 catalog 就係 FK 死症，寫落去會炸。"""
        self.write("translate-out-1.json", [{"variantId": 999, "stories": stories()}])
        rows, stats = sync.collect(FakeConnection({5}), self.pattern)
        self.assertEqual([], rows)
        self.assertEqual(1, stats["rejected_cards"])
        self.assertIn("catalog_variant 冇呢個 id", stats["rejections"][0]["reason"])

    def test_bad_card_does_not_drop_good_card(self) -> None:
        self.write("translate-out-1.json", [
            {"variantId": 5, "stories": stories()},
            {"variantId": 6, "stories": stories(ja="短")},
        ])
        rows, stats = sync.collect(FakeConnection({5, 6}), self.pattern)
        self.assertEqual({5}, {r.variant_id for r in rows})
        self.assertEqual(1, stats["accepted_cards"])
        self.assertEqual(1, stats["rejected_cards"])

    def test_reports_cards_without_english_row(self) -> None:
        """冇英文行 = 四語互異只驗到三語，要講出嚟唔好扮驗過。"""
        self.write("translate-out-1.json", [{"variantId": 5, "stories": stories()}])
        _, stats = sync.collect(FakeConnection({5}), self.pattern)
        self.assertEqual([5], stats["missing_english"])

    def test_english_row_participates_in_distinctness(self) -> None:
        english = ("An English dossier long enough to be a plausible translation target and to "
                   "clear the eighty character minimum on its own.")
        self.write("translate-out-1.json", [{"variantId": 5, "stories": stories(zhTW=english)}])
        rows, stats = sync.collect(FakeConnection({5}, {5: english}), self.pattern)
        self.assertEqual([], rows)
        self.assertEqual([], stats["missing_english"])
        self.assertIn("英文原文", stats["rejections"][0]["reason"])


class WriteTests(unittest.TestCase):
    def rows(self, variant_id: int = 5) -> list[sync.LocaleRow]:
        payload = stories()
        return [sync.LocaleRow(variant_id, locale, payload[locale])
                for locale in sync.TRANSLATED_LOCALES]

    def stats(self, accepted: int = 1, rejected: int = 0) -> dict[str, object]:
        return {"observed_cards": accepted + rejected, "accepted_cards": accepted,
                "rejected_cards": rejected, "files": [], "collisions": [],
                "missing_english": [], "rejections": []}

    def test_writes_every_row_and_commits(self) -> None:
        connection = FakeConnection({5})
        result = sync.write_all(connection, self.rows(), self.stats())
        self.assertEqual(3, len(connection.store))
        self.assertEqual(1, connection.commits)
        self.assertEqual(4242, result["run_id"])

    def test_rerun_overwrites_rather_than_duplicates(self) -> None:
        """PK 係 (variant_id, locale_code)，重跑要 idempotent。"""
        connection = FakeConnection({5})
        sync.write_all(connection, self.rows(), self.stats())
        sync.write_all(connection, self.rows(), self.stats())
        self.assertEqual(3, len(connection.store))

    def test_rerun_updates_changed_text_in_place(self) -> None:
        """重譯要覆蓋舊文，唔係插多一行 —— ON DUPLICATE KEY UPDATE 嘅實際效果。"""
        connection = FakeConnection({5})
        sync.write_all(connection, self.rows(), self.stats())
        revised = [sync.LocaleRow(5, locale, f"改寫版本{locale}" + "甲" * 90)
                   for locale in sync.TRANSLATED_LOCALES]
        sync.write_all(connection, revised, self.stats())
        self.assertEqual(3, len(connection.store))
        self.assertTrue(connection.store[(5, "zhTW")].startswith("改寫版本"))

    def test_rejects_empty_rows(self) -> None:
        with self.assertRaises(ValueError):
            sync.write_all(FakeConnection({5}), [], self.stats(accepted=0))

    def test_run_accounting_counts_rejections_honestly(self) -> None:
        """`market_ingest_run` 曾經 69 次 run 全部 rejected_count=0 —— 唔好再加一單。"""
        captured: list[tuple[object, ...]] = []
        connection = FakeConnection({5})
        original = connection.cursor

        def spy() -> FakeCursor:
            cursor = original()
            execute = cursor.execute

            def wrapped(sql: str, params: object = None) -> None:
                if "UPDATE market_ingest_run" in sql:
                    captured.append(tuple(params or ()))
                execute(sql, params)

            cursor.execute = wrapped  # type: ignore[method-assign]
            return cursor

        connection.cursor = spy  # type: ignore[method-assign]
        sync.write_all(connection, self.rows(), self.stats(accepted=1, rejected=4))
        self.assertEqual((5, 1, 4), captured[0][:3])


if __name__ == "__main__":
    unittest.main()
