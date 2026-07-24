#!/usr/bin/env python3
"""Restore a detached CARDZ canonical seed into an empty MySQL database only.

The paired ``canonical-seed.sql.gz`` and ``canonical-seed.manifest.json`` are
created by ``canonical_seed.py``.  Migrations stay in the repository and are
recorded in the immutable migration ledger before this data-only seed runs.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import (  # noqa: E402
    DEFAULT_ACTIVE,
    DEFAULT_LANDING,
    add_connection_args,
    connection_from_args,
    import_all,
    migrate,
    split_sql,
    status,
)
from canonical_seed import (  # noqa: E402
    CANONICAL_TABLES,
    SEED_SCHEMA_VERSION,
    SeedError,
    default_manifest_path,
    verify_seed,
)


FORBIDDEN_SQL_RE = re.compile(
    r"^\s*(?:DROP|TRUNCATE|CREATE\s+DATABASE|USE\b|GRANT|REVOKE|COMMIT|ROLLBACK|START\s+TRANSACTION|"
    r"SET\s+FOREIGN_KEY_CHECKS)\b",
    re.IGNORECASE,
)
IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+`?([A-Za-z0-9_]+)`?", re.IGNORECASE)
MIGRATION_METADATA_TABLES = {"cardz_schema_version", "cardz_migration_ledger"}


class SeedRestoreError(RuntimeError):
    """Raised before an empty-DB seed restore can alter a target database."""


def ensure_empty_database(connection: Any) -> None:
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        tables = cursor.fetchall()
    if tables:
        raise SeedRestoreError("seed restore requires an empty database; refusing to overwrite existing tables")


def migration_tables(migrations: Path) -> set[str]:
    tables: set[str] = set()
    for path in sorted(migrations.glob("*.mysql.sql")):
        tables.update(CREATE_TABLE_RE.findall(path.read_text(encoding="utf-8")))
    if not tables:
        raise SeedRestoreError("repository migrations declare no tables")
    return tables


def ensure_empty_or_resumable_schema(connection: Any, migrations: Path) -> None:
    """Accept an empty DB or an empty schema created by a failed prior restore."""

    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        existing = {str(next(iter(row.values()))) for row in cursor.fetchall() if isinstance(row, Mapping) and len(row) == 1}
        if not existing:
            return
        unexpected = sorted(existing - migration_tables(migrations))
        if unexpected:
            raise SeedRestoreError(f"seed restore found non-CARDZ tables: {', '.join(unexpected)}")
        for table in sorted(existing - MIGRATION_METADATA_TABLES):
            if not IDENTIFIER_RE.fullmatch(table):
                raise SeedRestoreError(f"unsafe schema table: {table}")
            cursor.execute(f"SELECT COUNT(*) AS count FROM `{table}`")
            if int(cursor.fetchone()["count"]) != 0:
                raise SeedRestoreError("seed restore requires an empty database; refusing to overwrite existing rows")


def seed_sql(path: Path) -> list[str]:
    try:
        raw = gzip.decompress(path.read_bytes()).decode("utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SeedRestoreError("verified seed cannot be decoded as UTF-8 gzip SQL") from error
    statements = split_sql(raw)
    if not statements:
        raise SeedRestoreError("verified seed contains no SQL statements")
    for statement in statements:
        if FORBIDDEN_SQL_RE.match(statement):
            raise SeedRestoreError("seed contains a forbidden destructive or privilege statement")
    return statements


def migration_hashes(migrations: Path) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(migrations.glob("*.mysql.sql"))
    }


def verify_migration_contract(manifest: Mapping[str, Any], migrations: Path) -> None:
    expected = manifest.get("migrationSha256")
    if not isinstance(expected, Mapping) or not expected:
        raise SeedRestoreError("seed manifest has no migration hash contract")
    normalized = {str(name): str(digest) for name, digest in expected.items()}
    if normalized != migration_hashes(migrations):
        raise SeedRestoreError("repository migrations do not match the seed migration contract")


def verify_seed_database(connection: Any, manifest: Mapping[str, Any]) -> dict[str, int]:
    expected_rows = manifest.get("tableRows")
    if not isinstance(expected_rows, Mapping):
        raise SeedRestoreError("seed manifest tableRows is invalid")
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        existing = {str(next(iter(row.values()))) for row in cursor.fetchall() if isinstance(row, Mapping) and len(row) == 1}
        missing = [table for table in CANONICAL_TABLES if table not in existing]
        if missing:
            raise SeedRestoreError(f"seed restore missing canonical tables: {', '.join(missing)}")
        actual: dict[str, int] = {}
        for table in CANONICAL_TABLES:
            if not IDENTIFIER_RE.fullmatch(table):
                raise SeedRestoreError(f"unsafe seed table: {table}")
            cursor.execute(f"SELECT COUNT(*) AS count FROM `{table}`")
            count = int(cursor.fetchone()["count"])
            expected = expected_rows.get(table)
            if not isinstance(expected, int) or expected < 0 or count != expected:
                raise SeedRestoreError(f"seed row-count mismatch for {table}")
            actual[table] = count
    return actual


def restore_seed(connection: Any, seed_path: Path, *, manifest_path: Path | None = None, migrations: Path) -> Mapping[str, Any]:
    """Apply repository migrations then immutable seed data to an empty DB.

    The empty test happens before migrations, making any existing database a
    hard stop.  SQL execution and verification share one transaction; a failed
    statement rolls the whole restore back.
    """

    try:
        manifest = verify_seed(seed_path, manifest_path=manifest_path)
    except SeedError as error:
        raise SeedRestoreError(str(error)) from error
    if manifest.get("schemaVersion") != SEED_SCHEMA_VERSION:
        raise SeedRestoreError("seed manifest schema is unsupported")
    verify_migration_contract(manifest, migrations)
    ensure_empty_or_resumable_schema(connection, migrations)
    try:
        migration_report = migrate(connection, migrations)
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")
        for statement in seed_sql(seed_path):
            with connection.cursor() as cursor:
                cursor.execute(statement)
        rows = verify_seed_database(connection, manifest)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SET FOREIGN_KEY_CHECKS=1")
    return {
        "seed": seed_path.name,
        "manifest": (manifest_path or default_manifest_path(seed_path)).name,
        "migrate": migration_report,
        "rows": rows,
    }


def daily_restore_canary(
    connection: Any,
    *,
    migrations: Path,
    active_universe: Path,
    landing_root: Path,
) -> Mapping[str, Any]:
    """Replay a restored canonical seed without contacting any provider.

    A successful run proves migration ledger idempotence, source batch replay
    idempotence, and canonical status.  It is intentionally not the scheduled
    collector itself.
    """

    document = json.loads(active_universe.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise SeedRestoreError("active-universe document is invalid")
    migration_report = migrate(connection, migrations)
    import_report = import_all(connection, active_universe, landing_root)
    if int(import_report["observations"]) != 0:
        raise SeedRestoreError("restore daily canary added observations; logical seed is incomplete")
    return {"migrate": migration_report, "import": import_report, "status": status(connection, document)}


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="verify detached seed and manifest hashes")
    verify.add_argument("--seed", type=Path, required=True)
    verify.add_argument("--manifest", type=Path)
    restore = commands.add_parser("restore", help="restore only into a truly empty database")
    restore.add_argument("--seed", type=Path, required=True)
    restore.add_argument("--manifest", type=Path)
    restore.add_argument("--migrations", type=Path, default=ROOT / "pipelines/migrations")
    restore.add_argument("--allow-empty-db", action="store_true", help="explicitly confirm empty-database restore")
    add_connection_args(restore)
    canary = commands.add_parser("daily-canary", help="offline idempotent post-restore replay check")
    canary.add_argument("--migrations", type=Path, default=ROOT / "pipelines/migrations")
    canary.add_argument("--active-universe", type=Path, default=DEFAULT_ACTIVE)
    canary.add_argument("--landing-root", type=Path, default=DEFAULT_LANDING)
    add_connection_args(canary)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "verify":
        try:
            manifest = verify_seed(args.seed.resolve(), manifest_path=args.manifest.resolve() if args.manifest else None)
        except SeedError as error:
            raise SeedRestoreError(str(error)) from error
        print(json.dumps({"seed": args.seed.name, "status": "verified", "tableCount": len(manifest["tableRows"])}, sort_keys=True))
        return 0
    connection = connection_from_args(args)
    try:
        if args.command == "restore":
            if not args.allow_empty_db:
                raise SeedRestoreError("restore requires --allow-empty-db and refuses a non-empty database")
            report = restore_seed(
                connection,
                args.seed.resolve(),
                manifest_path=args.manifest.resolve() if args.manifest else None,
                migrations=args.migrations.resolve(),
            )
        else:
            report = daily_restore_canary(
                connection,
                migrations=args.migrations.resolve(),
                active_universe=args.active_universe.resolve(),
                landing_root=args.landing_root.resolve(),
            )
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SeedRestoreError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
