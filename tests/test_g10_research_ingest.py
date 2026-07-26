from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "pipelines" / "g10_research_ingest.py"
SPEC = importlib.util.spec_from_file_location("cardz_g10_research_ingest", MODULE_PATH)
assert SPEC and SPEC.loader
ingest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ingest
SPEC.loader.exec_module(ingest)


STORY = (
    "The 2022 CoroCoro Pikachu V is the rare case where a promotional insert\n"
    "outran the set it shipped with.\n\n"
    "Basic Info:\n"
    "*   Set: Start Deck 100 CoroCoro Comic ver.\n"
    "*   Number: 001/024\n\n"
    "Community Pulse:\n"
    "*   Collectors treat the CoroCoro stamp as the desirable variant.\n"
)
FIXED_MTIME = 1_753_000_000  # 2025-07-20 UTC-ish, deliberately not "now"


def write_card(
    cards_root: Path,
    provider: str,
    entity_id: str,
    *,
    summary: object | None = STORY,
    raw_summary: str | None = None,
    asset: dict | None = None,
    mtime: int = FIXED_MTIME,
) -> Path:
    card_dir = cards_root / provider / entity_id
    card_dir.mkdir(parents=True, exist_ok=True)
    if raw_summary is not None:
        (card_dir / "summary_en.json").write_text(raw_summary, encoding="utf-8")
        os.utime(card_dir / "summary_en.json", (mtime, mtime))
    elif summary is not None:
        payload = json.dumps({"summary": summary}, ensure_ascii=False)
        (card_dir / "summary_en.json").write_text(payload, encoding="utf-8")
        os.utime(card_dir / "summary_en.json", (mtime, mtime))
    if asset is not None:
        (card_dir / "asset_info.json").write_text(json.dumps(asset, ensure_ascii=False), encoding="utf-8")
    return card_dir


def make_tree(tmp: Path) -> Path:
    """`<tmp>/data/cards` —— 同真實 G10 repo 一樣嘅層級，令相對路徑可驗證。"""

    cards = tmp / "data" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    return cards


class SummaryParsingTests(unittest.TestCase):
    def test_valid_document_returns_story_verbatim_apart_from_outer_whitespace(self) -> None:
        parsed = ingest.parse_summary_document({"summary": f"\n\n  {STORY}  \n"})
        self.assertEqual(parsed, STORY.strip())
        # 內部一個字都唔准改：bullet、換行、section 標題全部保住
        self.assertIn("*   Set: Start Deck 100 CoroCoro Comic ver.", parsed)
        self.assertIn("Community Pulse:", parsed)
        self.assertEqual(parsed.count("\n"), STORY.strip().count("\n"))

    def test_rejects_documents_that_break_the_contract(self) -> None:
        for document in (
            [],
            "just a string",
            {},
            {"story": STORY},
            {"summary": None},
            {"summary": 12},
            {"summary": "   "},
        ):
            with self.subTest(document=document):
                with self.assertRaises(ingest.StoryFormatError):
                    ingest.parse_summary_document(document)

    def test_oversized_story_is_rejected_never_truncated(self) -> None:
        oversized = "x" * (ingest.MAX_STORY_BYTES + 1)
        with self.assertRaises(ingest.StoryFormatError) as ctx:
            ingest.parse_summary_document({"summary": oversized})
        self.assertIn(str(ingest.MAX_STORY_BYTES), str(ctx.exception))

    def test_multibyte_story_is_measured_in_utf8_bytes_not_characters(self) -> None:
        # 3-byte characters: 22000 chars = 66000 bytes > TEXT limit even though len() is small
        oversized = "研" * 22_000
        self.assertLess(len(oversized), ingest.MAX_STORY_BYTES)
        with self.assertRaises(ingest.StoryFormatError):
            ingest.parse_summary_document({"summary": oversized})


class StoryFileTests(unittest.TestCase):
    def test_sha256_is_of_the_raw_file_bytes_and_observed_at_is_the_file_mtime(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = make_tree(Path(tmp))
            card_dir = write_card(cards, "snkrdunk", "100081")
            path = card_dir / "summary_en.json"
            story, sha, observed_at = ingest.read_story_file(path)

            self.assertEqual(story, STORY.strip())
            self.assertEqual(sha, hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertEqual(
                observed_at,
                datetime.utcfromtimestamp(FIXED_MTIME),
                "observed_at 一定要係檔案 mtime，唔准用 now()",
            )
            self.assertLess(observed_at, datetime.utcnow())

    def test_unreadable_json_raises_story_format_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = make_tree(Path(tmp))
            card_dir = write_card(cards, "snkrdunk", "1", raw_summary="{not json")
            with self.assertRaises(ingest.StoryFormatError):
                ingest.read_story_file(card_dir / "summary_en.json")

    def test_relative_source_path_is_repo_relative_posix(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = make_tree(Path(tmp))
            card_dir = write_card(cards, "snkrdunk", "100081")
            self.assertEqual(
                ingest.relative_source_path(card_dir / "summary_en.json", cards),
                "data/cards/snkrdunk/100081/summary_en.json",
            )


class AssetNameTests(unittest.TestCase):
    def test_reads_card_and_set_names(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = make_tree(Path(tmp))
            card_dir = write_card(
                cards, "snkrdunk", "100081",
                asset={"cardName": " Pikachu V ", "setName": "Start Deck 100 CoroCoro Comic ver."},
            )
            self.assertEqual(
                ingest.read_asset_names(card_dir),
                ("Pikachu V", "Start Deck 100 CoroCoro Comic ver."),
            )

    def test_missing_or_oversized_names_become_none_never_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cards = make_tree(Path(tmp))
            bare = write_card(cards, "snkrdunk", "1")
            self.assertEqual(ingest.read_asset_names(bare), (None, None))

            long_name = write_card(
                cards, "snkrdunk", "2",
                asset={"cardName": "y" * (ingest.MAX_NAME_CHARS + 1), "setName": "  "},
            )
            self.assertEqual(ingest.read_asset_names(long_name), (None, None))


class CollectTests(unittest.TestCase):
    def build(self) -> tuple[Path, dict]:
        tmp = Path(tempfile.mkdtemp())
        cards = make_tree(tmp)
        # 對到 identity
        write_card(cards, "snkrdunk", "100081", asset={"cardName": "Pikachu V", "setName": "Start Deck"})
        write_card(cards, "altxyz", "uuid-aaa")
        # 對唔到 identity → quarantined
        write_card(cards, "snkrdunk", "999999")
        # 格式壞 → rejected
        write_card(cards, "snkrdunk", "888888", raw_summary=json.dumps({"summary": ""}))
        # 冇 summary 檔 → 唔計入 observed
        (cards / "snkrdunk" / "777777").mkdir(parents=True)
        identity = {("snkrdunk", "100081"): 11, ("altxyz", "uuid-aaa"): 22}
        return cards, identity

    def test_counts_split_into_accepted_quarantined_rejected(self) -> None:
        cards, identity = self.build()
        rows, stats, skips = ingest.collect(cards, identity)

        self.assertEqual(stats["dir_seen"], 5)
        self.assertEqual(stats["observed"], 4)
        self.assertEqual(stats["accepted"], 2)
        self.assertEqual(stats["quarantined"], 1)
        self.assertEqual(stats["rejected"], 1)
        self.assertEqual(stats["dir_without_summary"], 1)
        self.assertEqual(
            stats["observed"], stats["accepted"] + stats["quarantined"] + stats["rejected"]
        )
        self.assertEqual({row.variant_id for row in rows}, {11, 22})

    def test_every_skipped_directory_is_reported_with_a_reason(self) -> None:
        cards, identity = self.build()
        _, _, skips = ingest.collect(cards, identity)
        by_reason = {}
        for item in skips:
            by_reason.setdefault(item["reason"], []).append(item["externalEntityId"])
        self.assertEqual(by_reason["no_variant_identity"], ["999999"])
        self.assertEqual(by_reason["rejected_format"], ["888888"])
        self.assertEqual(by_reason["no_summary_file"], ["777777"])
        for item in skips:
            self.assertTrue(item["detail"])

    def test_unmatched_directories_are_never_guessed_into_a_variant(self) -> None:
        cards, identity = self.build()
        rows, _, _ = ingest.collect(cards, identity)
        self.assertNotIn("999999", {row.external_entity_id for row in rows})

    def test_accepted_rows_carry_identity_provenance_and_untouched_story(self) -> None:
        cards, identity = self.build()
        rows, _, _ = ingest.collect(cards, identity)
        row = next(row for row in rows if row.external_entity_id == "100081")
        self.assertEqual(row.variant_id, 11)
        self.assertEqual(row.source_path, "data/cards/snkrdunk/100081/summary_en.json")
        self.assertEqual(row.story, STORY.strip())
        self.assertEqual(row.localized_name, "Pikachu V")
        self.assertEqual(row.localized_set_name, "Start Deck")
        self.assertEqual(row.observed_at, datetime.utcfromtimestamp(FIXED_MTIME))
        self.assertEqual(len(row.story_sha256), 64)

    def test_limit_caps_the_number_of_directories_walked(self) -> None:
        cards, identity = self.build()
        _, stats, _ = ingest.collect(cards, identity, limit=2)
        self.assertEqual(stats["dir_seen"], 2)


class CoverageTests(unittest.TestCase):
    def rows(self) -> list:
        return [
            ingest.StoryRow(
                provider="snkrdunk",
                external_entity_id="100081",
                variant_id=11,
                source_path="data/cards/snkrdunk/100081/summary_en.json",
                story=STORY.strip(),
                story_sha256="a" * 64,
                observed_at=datetime.utcfromtimestamp(FIXED_MTIME),
                localized_name="Pikachu V",
                localized_set_name="Start Deck",
            )
        ]

    def test_coverage_rows_flag_present_and_missing_research(self) -> None:
        constituents = [
            {"variant_id": 11, "opaque_id": "opq-11", "canonical_name": "Pikachu V", "rank_position": 1},
            {"variant_id": 12, "opaque_id": "opq-12", "canonical_name": "Charizard V", "rank_position": 2},
        ]
        coverage = ingest.build_coverage_rows(constituents, self.rows())
        self.assertEqual(
            coverage,
            [
                {
                    "variantId": 11,
                    "opaqueId": "opq-11",
                    "rank": 1,
                    "canonicalName": "Pikachu V",
                    "hasG10Research": True,
                    "g10Path": "data/cards/snkrdunk/100081/summary_en.json",
                    "charCount": len(STORY.strip()),
                },
                {
                    "variantId": 12,
                    "opaqueId": "opq-12",
                    "rank": 2,
                    "canonicalName": "Charizard V",
                    "hasG10Research": False,
                    "g10Path": None,
                    "charCount": 0,
                },
            ],
        )


class FakeCursor:
    def __init__(self, log: list) -> None:
        self.log = log
        self.lastrowid = 4242
        self._last_query = ""

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def execute(self, query: str, args: object = ()) -> None:
        self._last_query = query
        self.log.append(("execute", " ".join(query.split()), args))

    def executemany(self, query: str, args: list) -> None:
        self.log.append(("executemany", " ".join(query.split()), args))

    def fetchone(self) -> dict:
        return {"c": 0}


class FakeConnection:
    def __init__(self) -> None:
        self.log: list = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self.log)


class WriteAllTests(unittest.TestCase):
    def rows(self) -> list:
        return [
            ingest.StoryRow(
                provider="snkrdunk",
                external_entity_id="100081",
                variant_id=11,
                source_path="data/cards/snkrdunk/100081/summary_en.json",
                story=STORY.strip(),
                story_sha256="a" * 64,
                observed_at=datetime.utcfromtimestamp(FIXED_MTIME),
                localized_name="Pikachu V",
                localized_set_name="Start Deck",
            )
        ]

    def run_write(self, stats: dict) -> tuple[dict, list]:
        connection = FakeConnection()
        result = ingest.write_all(connection, self.rows(), stats)
        return result, connection.log

    def test_ingest_run_records_real_quarantined_and_rejected_counts(self) -> None:
        stats = {"observed": 480, "accepted": 336, "quarantined": 144, "rejected": 0}
        result, log = self.run_write(stats)
        update = next(
            entry for entry in log
            if entry[0] == "execute" and entry[1].startswith("UPDATE market_ingest_run")
        )
        observed, accepted, quarantined, rejected, _completed, _run_id = update[2]
        self.assertEqual((observed, accepted, quarantined, rejected), (480, 1, 144, 0))
        self.assertEqual(result["counts"]["quarantined"], 144)
        self.assertNotEqual(result["counts"]["quarantined"], 0)

    def test_run_row_uses_file_mtime_as_effective_at_not_wall_clock(self) -> None:
        _, log = self.run_write({"observed": 1, "quarantined": 0, "rejected": 0})
        insert = next(
            entry for entry in log
            if entry[0] == "execute" and entry[1].startswith("INSERT INTO market_ingest_run")
        )
        run_key, source_code, effective_at, _payload, _manifest, _observed, _started = insert[2]
        self.assertEqual(source_code, ingest.SOURCE_CODE)
        self.assertEqual(len(run_key), 64)
        self.assertEqual(effective_at, datetime.utcfromtimestamp(FIXED_MTIME))

    def test_locale_and_pointer_writes_are_upserts_so_reruns_add_nothing(self) -> None:
        _, log = self.run_write({"observed": 1, "quarantined": 0, "rejected": 0})
        many = {entry[1]: entry for entry in log if entry[0] == "executemany"}
        locale_sql = next(sql for sql in many if "catalog_variant_locale" in sql)
        pointer_sql = next(sql for sql in many if "catalog_story_pointer" in sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", locale_sql)
        self.assertIn("ON DUPLICATE KEY UPDATE", pointer_sql)

    def test_story_reaches_the_database_byte_identical(self) -> None:
        _, log = self.run_write({"observed": 1, "quarantined": 0, "rejected": 0})
        locale = next(
            entry for entry in log
            if entry[0] == "executemany" and "catalog_variant_locale" in entry[1]
        )
        variant_id, locale_code, name, set_name, story = locale[2][0]
        self.assertEqual((variant_id, locale_code), (11, "en"))
        self.assertEqual((name, set_name), ("Pikachu V", "Start Deck"))
        self.assertEqual(story, STORY.strip())

    def test_pointer_carries_path_sha_and_mtime(self) -> None:
        _, log = self.run_write({"observed": 1, "quarantined": 0, "rejected": 0})
        pointer = next(
            entry for entry in log
            if entry[0] == "executemany" and "catalog_story_pointer" in entry[1]
        )
        variant_id, locale_code, path, sha, observed_at = pointer[2][0]
        self.assertEqual(variant_id, 11)
        self.assertEqual(locale_code, "en")
        self.assertEqual(path, "data/cards/snkrdunk/100081/summary_en.json")
        self.assertEqual(sha, "a" * 64)
        self.assertEqual(observed_at, datetime.utcfromtimestamp(FIXED_MTIME))

    def test_run_status_is_completed_to_stay_out_of_the_alert_run_selector(self) -> None:
        # market_alerts.py 揀 status='complete' 嘅最新 run 落 market_index_snapshot.run_id
        _, log = self.run_write({"observed": 1, "quarantined": 0, "rejected": 0})
        update = next(
            entry for entry in log
            if entry[0] == "execute" and entry[1].startswith("UPDATE market_ingest_run")
        )
        self.assertIn("status='completed'", update[1])

    def test_refuses_to_open_a_run_with_no_rows(self) -> None:
        with self.assertRaises(ValueError):
            ingest.write_all(FakeConnection(), [], {"observed": 0})


class ContractTests(unittest.TestCase):
    def test_only_english_locale_is_written(self) -> None:
        self.assertEqual(ingest.LOCALE_CODE, "en")
        self.assertEqual(ingest.SUMMARY_FILENAME, "summary_en.json")

    def test_identity_is_the_only_join_path(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("catalog_source_identity", source)
        self.assertNotIn("canonical_name =", source)

    def test_pipeline_never_updates_canonical_variant_identity(self) -> None:
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("UPDATE catalog_variant ", source)
        self.assertNotIn("INSERT INTO catalog_variant\n", source)


if __name__ == "__main__":
    unittest.main()
