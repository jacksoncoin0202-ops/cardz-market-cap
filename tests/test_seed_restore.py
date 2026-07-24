from __future__ import annotations

import gzip
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("seed_restore", ROOT / "scripts" / "seed_restore.py")
assert SPEC and SPEC.loader
restore = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = restore
SPEC.loader.exec_module(restore)


class Cursor:
    def __init__(self, connection: "Connection") -> None:
        self.connection = connection
        self.rows: list[dict[str, object]] = []
        self.one: dict[str, int] | None = None

    def __enter__(self) -> "Cursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, _args: object = None) -> None:
        normalized = " ".join(query.split())
        self.connection.commands.append(normalized)
        if normalized == "SHOW TABLES":
            self.rows = [{"Tables_in_cardz": table} for table in sorted(self.connection.tables)]
        elif normalized.startswith("SELECT COUNT(*) AS count FROM"):
            table = normalized.split("`")[1]
            self.one = {"count": self.connection.tables.get(table, 0)}

    def fetchall(self) -> list[dict[str, object]]:
        return self.rows

    def fetchone(self) -> dict[str, int]:
        assert self.one is not None
        return self.one


class Connection:
    def __init__(self, tables: dict[str, int] | None = None) -> None:
        self.tables = dict(tables or {})
        self.commands: list[str] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> Cursor:
        return Cursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


def detached_seed(directory: Path) -> tuple[Path, Path]:
    seed = directory / "canonical-seed.sql.gz"
    raw = b"INSERT INTO `catalog_variant` VALUES (1);\n"
    seed.write_bytes(gzip.compress(raw, mtime=0))
    manifest = directory / "canonical-seed.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": "1.0.0",
                "kind": "cardz-canonical-mysql-logical-seed",
                "seedFile": seed.name,
                "seedSha256": hashlib.sha256(seed.read_bytes()).hexdigest(),
                "uncompressedSha256": hashlib.sha256(raw).hexdigest(),
                "tableRows": {table: 0 for table in restore.CANONICAL_TABLES},
                "canonicalTables": list(restore.CANONICAL_TABLES),
                "excludedTables": {},
                "requiredMigrations": [],
                "migrationSha256": {},
            }
        ),
        encoding="utf-8",
    )
    return seed, manifest


class SeedRestoreTests(unittest.TestCase):
    def test_empty_database_gate_refuses_existing_tables(self) -> None:
        with self.assertRaisesRegex(restore.SeedRestoreError, "empty database"):
            restore.ensure_empty_database(Connection({"catalog_variant": 1}))

    def test_restore_can_resume_its_own_empty_migrated_schema_but_not_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            migrations = Path(temporary)
            (migrations / "001.mysql.sql").write_text(
                "CREATE TABLE IF NOT EXISTS cardz_migration_ledger (id INT);"
                "CREATE TABLE IF NOT EXISTS catalog_variant (id INT);",
                encoding="utf-8",
            )
            restore.ensure_empty_or_resumable_schema(
                Connection({"cardz_migration_ledger": 1, "catalog_variant": 0}),
                migrations,
            )
            with self.assertRaisesRegex(restore.SeedRestoreError, "empty database"):
                restore.ensure_empty_or_resumable_schema(
                    Connection({"cardz_migration_ledger": 1, "catalog_variant": 1}),
                    migrations,
                )

    def test_seed_sql_refuses_destructive_statements(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "seed.sql.gz"
            path.write_bytes(gzip.compress(b"DROP TABLE catalog_variant;", mtime=0))
            with self.assertRaisesRegex(restore.SeedRestoreError, "forbidden"):
                restore.seed_sql(path)

    def test_seed_sql_refuses_embedded_transaction_control(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "seed.sql.gz"
            path.write_bytes(gzip.compress(b"INSERT INTO catalog_variant VALUES (1); COMMIT;", mtime=0))
            with self.assertRaisesRegex(restore.SeedRestoreError, "forbidden"):
                restore.seed_sql(path)

    def test_restore_verifies_detached_seed_before_any_database_operation(self) -> None:
        connection = Connection()
        with tempfile.TemporaryDirectory() as temporary:
            seed, manifest = detached_seed(Path(temporary))
            with mock.patch.object(restore, "verify_seed", side_effect=restore.SeedError("tampered")):
                with self.assertRaisesRegex(restore.SeedRestoreError, "tampered"):
                    restore.restore_seed(connection, seed, manifest_path=manifest, migrations=Path(temporary))
        self.assertEqual(connection.commands, [])

    def test_restore_requires_explicit_cli_confirmation(self) -> None:
        args = restore.parse_args(["restore", "--seed", "fixture.sql.gz"])
        self.assertFalse(args.allow_empty_db)

    def test_restore_rejects_seed_when_repository_migrations_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            migrations = Path(temporary)
            (migrations / "001.mysql.sql").write_text("CREATE TABLE a (id INT);", encoding="utf-8")
            with self.assertRaisesRegex(restore.SeedRestoreError, "migration contract"):
                restore.verify_migration_contract({"migrationSha256": {"001.mysql.sql": "0" * 64}}, migrations)

    def test_canary_rejects_any_new_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            active = Path(temporary) / "active.json"
            active.write_text("{}", encoding="utf-8")
            with (
                mock.patch.object(restore, "migrate", return_value={"files": 0}),
                mock.patch.object(restore, "import_all", return_value={"observations": 1}),
            ):
                with self.assertRaisesRegex(restore.SeedRestoreError, "added observations"):
                    restore.daily_restore_canary(
                        Connection(), migrations=Path(temporary), active_universe=active, landing_root=Path(temporary)
                    )


if __name__ == "__main__":
    unittest.main()
