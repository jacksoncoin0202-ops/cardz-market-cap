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
    backfill_effective_pointers,
    build_compaction_plan,
    eligible_payload_rows,
    fetch_capacity_report,
    fetch_effective_pointer_winners,
    main,
    parse_args,
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


class CapacityCursor:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.result: list[dict[str, object]] = []

    def __enter__(self) -> "CapacityCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, _args: object = None) -> None:
        normalized = " ".join(query.split())
        self.commands.append(normalized)
        if "AS raw_rows" in normalized:
            self.result = [
                {
                    "raw_rows": 125,
                    "payload_pointer_rows": 25,
                    "effective_pointer_rows": 40,
                    "uncompacted_rows": 100,
                    "uncompacted_payload_bytes": 12_345,
                }
            ]
        elif "information_schema.TABLES" in normalized:
            self.result = [
                {
                    "table_name": "market_source_observation",
                    "table_rows": 125,
                    "data_length": 20_000,
                    "index_length": 5_000,
                },
                {
                    "table_name": "market_source_effective_observation",
                    "table_rows": 40,
                    "data_length": 4_000,
                    "index_length": 1_000,
                },
            ]
        else:
            raise AssertionError(f"unexpected capacity query: {normalized}")

    def fetchone(self) -> dict[str, object]:
        return self.result[0]

    def fetchall(self) -> list[dict[str, object]]:
        return self.result


class CapacityConnection:
    def __init__(self) -> None:
        self.cursor_value = CapacityCursor()

    def cursor(self) -> CapacityCursor:
        return self.cursor_value


class WinnerCursor:
    def __init__(self, winners: list[dict[str, object]]) -> None:
        self.commands: list[str] = []
        self.winners = winners
        self.result: list[dict[str, object]] = []

    def __enter__(self) -> "WinnerCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, _args: object = None) -> None:
        normalized = " ".join(query.split())
        self.commands.append(normalized)
        if normalized.startswith("SELECT GET_LOCK"):
            self.result = [{"acquired": 1}]
        elif normalized.startswith("SELECT source.source_code"):
            self.result = self.winners
            self.winners = []
        else:
            self.result = []

    def executemany(self, query: str, args: object) -> None:
        self.commands.append(" ".join(query.split()))
        self.commands.append(f"executemany:{len(list(args))}")  # type: ignore[arg-type]

    def fetchone(self) -> dict[str, object] | None:
        return self.result[0] if self.result else None

    def fetchall(self) -> list[dict[str, object]]:
        return self.result


class WinnerConnection:
    def __init__(self, winners: list[dict[str, object]]) -> None:
        self.cursor_value = WinnerCursor(winners)
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> WinnerCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


class ContextConnection:
    def __init__(self) -> None:
        self.exits = 0

    def __enter__(self) -> "ContextConnection":
        return self

    def __exit__(self, *_: object) -> None:
        self.exits += 1


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

    def test_capacity_report_includes_pointer_counts_uncompacted_bytes_and_table_sizes(self) -> None:
        connection = CapacityConnection()
        report = fetch_capacity_report(connection)
        self.assertEqual(report["rawRows"], 125)
        self.assertEqual(report["payloadPointers"], 25)
        self.assertEqual(report["effectivePointers"], 40)
        self.assertEqual(report["uncompactedRows"], 100)
        self.assertEqual(report["uncompactedPayloadBytes"], 12_345)
        self.assertEqual(report["tables"]["market_source_observation"]["dataBytes"], 20_000)
        self.assertEqual(report["tables"]["market_source_observation"]["indexBytes"], 5_000)
        self.assertEqual(report["tables"]["market_source_observation"]["totalBytes"], 25_000)

    def test_effective_pointer_query_selects_latest_effective_at_then_highest_id(self) -> None:
        connection = WinnerConnection([])
        self.assertEqual(fetch_effective_pointer_winners(connection, 25_000), [])
        sql = "\n".join(connection.cursor_value.commands)
        self.assertIn("newer.effective_at > source.effective_at", sql)
        self.assertIn("newer.effective_at = source.effective_at AND newer.id > source.id", sql)
        self.assertIn("newer_run.status = 'complete'", sql)
        self.assertIn("current.observation_id <> source.id", sql)

    def test_backfill_only_upserts_effective_pointers_and_never_mutates_raw_payloads(self) -> None:
        winners = [
            {
                "source_code": "snk",
                "external_entity_id": "card-1",
                "observation_kind": "index_constituent",
                "observed_date": "2026-07-20",
                "observation_id": 17,
                "effective_at": "2026-07-20T02:00:00Z",
            },
            {
                "source_code": "gemrate",
                "external_entity_id": "card-2",
                "observation_kind": "population",
                "observed_date": "2026-07-20",
                "observation_id": 29,
                "effective_at": "2026-07-20T03:00:00Z",
            },
        ]
        connection = WinnerConnection(winners)
        result = backfill_effective_pointers(connection, limit=2)
        self.assertTrue(result["applied"])
        self.assertEqual(result["backfilledPointers"], 2)
        self.assertEqual(result["batches"], 1)
        self.assertTrue(connection.committed)
        self.assertFalse(connection.rolled_back)
        sql = "\n".join(connection.cursor_value.commands)
        self.assertIn("START TRANSACTION", sql)
        self.assertIn("SELECT GET_LOCK", sql)
        self.assertIn("SELECT RELEASE_LOCK", sql)
        self.assertIn("INSERT INTO market_source_effective_observation", sql)
        self.assertIn("executemany:2", sql)
        self.assertNotIn("UPDATE market_source_observation", sql)
        self.assertNotIn("payload_json", sql)
        self.assertNotIn("market_source_observation_payload_pointer", sql)

    def test_backfill_flag_requires_apply_and_selects_a_separate_operation(self) -> None:
        with self.assertRaises(SystemExit):
            parse_args(["--backfill-effective-pointers"])
        args = parse_args(["--backfill-effective-pointers", "--apply", "--limit", "500"])
        self.assertTrue(args.backfill_effective_pointers)
        self.assertTrue(args.apply)
        self.assertEqual(args.limit, 500)

    def test_main_leaves_connection_lifecycle_to_context_manager(self) -> None:
        connection = ContextConnection()
        with (
            patch("db_retention.connection_from_environment", return_value=connection),
            patch("db_retention.database_plan", return_value={"mode": "dry-run"}),
            patch.object(sys, "argv", ["db_retention.py"]),
        ):
            self.assertEqual(main(), 0)
        self.assertEqual(connection.exits, 1)


if __name__ == "__main__":
    unittest.main()
