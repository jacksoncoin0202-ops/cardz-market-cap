from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_retention import (  # noqa: E402
    POINTER_KEY,
    apply_compaction,
    build_compaction_plan,
    eligible_payload_rows,
    verify_archive_manifest,
    write_content_addressed_archive,
)


class FakeCursor:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, _args: object = None) -> None:
        self.commands.append(" ".join(query.split()))

    def fetchall(self) -> list[dict[str, int]]:
        return [{"id": 1}, {"id": 2}, {"id": 3}]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class DbRetentionTests(unittest.TestCase):
    def rows(self) -> list[dict[str, object]]:
        return [
            {"id": 1, "payload_json": '{"price":100}', "source_code": "snk", "external_entity_id": "card-1", "observation_kind": "index_constituent", "observed_date": "2026-07-20", "effective_at": "2026-07-20T00:00:00Z"},
            {"id": 2, "payload_json": '{"price":100}', "source_code": "snk", "external_entity_id": "card-2", "observation_kind": "index_constituent", "observed_date": "2026-07-21", "effective_at": "2026-07-21T00:00:00Z"},
            {"id": 3, "payload_json": '{"price":200}', "source_code": "snk", "external_entity_id": "card-3", "observation_kind": "index_constituent", "observed_date": "2026-07-22", "effective_at": "2026-07-22T00:00:00Z"},
            {"id": 4, "payload_json": json.dumps({POINTER_KEY: "a" * 64}), "source_code": "snk", "external_entity_id": "card-4", "observation_kind": "index_constituent", "observed_date": "2026-07-23", "effective_at": "2026-07-23T00:00:00Z"},
        ]

    def test_plan_deduplicates_raw_payload_and_preserves_canonical_tables(self) -> None:
        plan = build_compaction_plan(self.rows())
        self.assertEqual(plan["eligibleRows"], 3)
        self.assertEqual(plan["uniquePayloadObjects"], 2)
        self.assertEqual(plan["duplicateOccurrences"], 1)
        self.assertEqual(plan["estimatedMysqlBytesReleased"], len(b'{"price":100}') * 2 + len(b'{"price":200}'))
        self.assertIn("never recalculates", plan["fxPolicy"])
        self.assertIn("price, population, sales, ranking and FX", plan["canonicalPolicy"])
        self.assertIn("one effective row", plan["validObservationRule"])

    def test_archive_is_content_addressed_and_manifest_is_hash_verified(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            archive = write_content_addressed_archive(self.rows(), Path(temporary))
            manifest = verify_archive_manifest(Path(archive["manifestPath"]))
            self.assertEqual(manifest["state"], "complete")
            self.assertEqual(manifest["objectCount"], 2)
            self.assertEqual(manifest["observationCount"], 3)
            self.assertEqual(len(list((Path(temporary) / "objects").glob("*.json"))), 2)

    def test_duplicate_observation_id_is_rejected_before_archiving(self) -> None:
        rows = self.rows()[:1] * 2
        with self.assertRaisesRegex(ValueError, "duplicate observation id"):
            eligible_payload_rows(rows)

    def test_missing_identity_is_rejected_before_archiving(self) -> None:
        row = self.rows()[0]
        row.pop("external_entity_id")
        with self.assertRaisesRegex(ValueError, "missing canonical identity"):
            eligible_payload_rows([row])

    def test_migration_only_adds_retention_tables(self) -> None:
        migration = (ROOT / "pipelines" / "migrations" / "008_payload_retention.mysql.sql").read_text(encoding="utf-8")
        self.assertIn("market_source_effective_observation", migration)
        self.assertIn("market_source_observation_payload_pointer", migration)
        self.assertNotIn("DELETE FROM", migration.upper())
        self.assertNotIn("UPDATE market_price_observation", migration)

    def test_apply_requires_explicit_call_and_only_replaces_source_payload_with_pointer(self) -> None:
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as temporary, patch("db_retention.fetch_completed_payload_rows", return_value=self.rows()):
            result = apply_compaction(connection, Path(temporary), limit=10)
        self.assertTrue(result["applied"])
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        sql = "\n".join(connection.cursor_value.commands)
        self.assertIn("START TRANSACTION", sql)
        self.assertIn("market_source_observation_payload_pointer", sql)
        self.assertIn("market_source_effective_observation", sql)
        self.assertIn("UPDATE market_source_observation SET payload_json=JSON_OBJECT", sql)
        self.assertNotIn("DELETE FROM", sql)
        self.assertNotIn("UPDATE market_price_observation", sql)
        self.assertNotIn("UPDATE market_fx_rate_observation", sql)


if __name__ == "__main__":
    unittest.main()
