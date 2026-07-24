from __future__ import annotations

import gzip
import importlib.util
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("canonical_seed", ROOT / "scripts" / "canonical_seed.py")
assert SPEC and SPEC.loader
seed = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(seed)


class FakeCursor:
    def __init__(self, database: "FakeConnection") -> None:
        self.database = database
        self.rows: list[dict[str, object]] = []

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str) -> None:
        if sql == "SHOW TABLES":
            self.rows = [{"Tables_in_cardz": table} for table in seed.CANONICAL_TABLES]
        elif sql.startswith("SHOW COLUMNS FROM"):
            self.rows = [{"Field": "id"}, {"Field": "name"}, {"Field": "observed_date"}]
        elif sql.startswith("SHOW KEYS FROM"):
            self.rows = [{"Seq_in_index": 1, "Column_name": "id"}]
        elif sql.startswith("SELECT"):
            self.rows = [{"id": 1, "name": "Pikachu 227/S-P", "observed_date": date(2026, 7, 24)}]
        else:
            raise AssertionError(sql)

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchmany(self, _size: int) -> list[dict[str, object]]:
        rows, self.rows = self.rows, []
        return rows


class FakeConnection:
    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class CanonicalSeedTests(unittest.TestCase):
    def test_seed_excludes_migration_owned_rows_that_restore_recreates(self) -> None:
        self.assertNotIn("cardz_schema_version", seed.CANONICAL_TABLES)
        self.assertNotIn("cardz_migration_ledger", seed.CANONICAL_TABLES)
        self.assertIn("cardz_schema_version", seed.EXCLUDED_RAW_TABLES)
        self.assertIn("cardz_migration_ledger", seed.EXCLUDED_RAW_TABLES)

    def test_build_and_verify_are_deterministic_and_preserve_detached_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "canonical.sql.gz"
            manifest = seed.build_seed(FakeConnection(), output)
            verified = seed.verify_seed(output)
            self.assertEqual(manifest, verified)
            self.assertEqual(verified["tableRows"]["catalog_variant"], 1)
            self.assertEqual(set(verified["excludedTables"]), set(seed.EXCLUDED_RAW_TABLES))
            self.assertEqual(verified["migrationSha256"], seed.migration_hashes())
            raw = gzip.decompress(output.read_bytes()).decode("utf-8")
            self.assertIn("50696b61636875203232372f532d50", raw)
            self.assertNotIn("CARDZ_DB_PASSWORD", raw)
            self.assertNotIn("COMMIT;", raw)
            self.assertNotIn("START TRANSACTION", raw)
            self.assertNotIn("FOREIGN_KEY_CHECKS", raw)

    def test_seed_rejects_a_secret_like_canonical_value_before_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "canonical.sql.gz"
            original = seed.iter_table_rows

            def rows_with_secret(connection: object, table: str, columns: object, order: object):
                if table == "catalog_variant":
                    return iter([{"id": 1, "name": "api_key=not-allowed", "observed_date": date(2026, 7, 24)}])
                return original(connection, table, columns, order)

            with mock.patch.object(seed, "iter_table_rows", side_effect=rows_with_secret):
                with self.assertRaisesRegex(seed.SeedError, "secret-like"):
                    seed.build_seed(FakeConnection(), output)
            self.assertFalse(output.exists())

    def test_verify_rejects_tampered_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "canonical.sql.gz"
            seed.build_seed(FakeConnection(), output)
            output.write_bytes(b"not-gzip")
            with self.assertRaises(seed.SeedError):
                seed.verify_seed(output)

    def test_sql_value_is_unicode_safe_and_never_uses_sql_string_escaping(self) -> None:
        self.assertEqual(seed.sql_value("梵高皮卡丘'\\"), "CONVERT(0xe6a2b5e9ab98e79aaee58da1e4b898275c USING utf8mb4)")


if __name__ == "__main__":
    unittest.main()
