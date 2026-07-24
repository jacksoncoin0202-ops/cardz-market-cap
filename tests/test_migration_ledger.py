from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import migrate  # noqa: E402


class FakeCursor:
    def __init__(self, ledger: dict[str, str]) -> None:
        self.ledger = ledger
        self.commands: list[tuple[str, object]] = []
        self._selected: dict[str, str] | None = None

    def __enter__(self) -> "FakeCursor":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, query: str, args: object = None) -> None:
        normalized = " ".join(query.split())
        self.commands.append((normalized, args))
        if normalized.startswith("SELECT content_sha256"):
            assert isinstance(args, tuple)
            digest = self.ledger.get(str(args[0]))
            self._selected = {"content_sha256": digest} if digest else None
        elif normalized.startswith("INSERT INTO cardz_migration_ledger"):
            assert isinstance(args, tuple)
            self.ledger[str(args[0])] = str(args[1])

    def fetchone(self) -> dict[str, str] | None:
        return self._selected


class FakeConnection:
    def __init__(self) -> None:
        self.ledger: dict[str, str] = {}
        self.cursor_value = FakeCursor(self.ledger)
        self.committed = False

    def cursor(self) -> FakeCursor:
        return self.cursor_value

    def commit(self) -> None:
        self.committed = True


class MigrationLedgerTests(unittest.TestCase):
    def write_migration(self, directory: Path, name: str, body: str) -> Path:
        path = directory / name
        path.write_text(body, encoding="utf-8")
        return path

    def test_first_replay_records_content_hash_and_second_replay_skips(self) -> None:
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as temporary:
            migrations = Path(temporary)
            self.write_migration(migrations, "001_fixture.mysql.sql", "CREATE TABLE fixture_one (id INT);")
            first = migrate(connection, migrations)
            second = migrate(connection, migrations)
        self.assertEqual(first, {"files": 1, "skipped": 0, "statements": 1})
        self.assertEqual(second, {"files": 0, "skipped": 1, "statements": 0})
        self.assertTrue(connection.committed)
        sql = "\n".join(command for command, _args in connection.cursor_value.commands)
        self.assertIn("CREATE TABLE IF NOT EXISTS cardz_migration_ledger", sql)
        self.assertIn("INSERT INTO cardz_migration_ledger", sql)

    def test_changed_applied_migration_fails_closed(self) -> None:
        connection = FakeConnection()
        with tempfile.TemporaryDirectory() as temporary:
            migrations = Path(temporary)
            path = self.write_migration(migrations, "001_fixture.mysql.sql", "CREATE TABLE fixture_one (id INT);")
            migrate(connection, migrations)
            path.write_text("CREATE TABLE fixture_two (id INT);", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "content changed"):
                migrate(connection, migrations)

    def test_ledger_migration_is_additive(self) -> None:
        document = (ROOT / "pipelines/migrations/009_migration_ledger.mysql.sql").read_text(encoding="utf-8")
        self.assertIn("cardz_migration_ledger", document)
        self.assertNotIn("DROP TABLE", document.upper())
        self.assertNotIn("DELETE FROM", document.upper())


if __name__ == "__main__":
    unittest.main()
