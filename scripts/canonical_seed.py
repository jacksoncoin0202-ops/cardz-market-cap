#!/usr/bin/env python3
"""Create and verify a portable, logical seed of the canonical CARDZ MySQL DB.

The seed deliberately contains normalized canonical records only.  Provider raw
payloads remain in the private landing/archive layer and are never copied into
the logical seed.  Restores first apply the immutable SQL migrations, then run
this seed; this script does not emit DDL or credentials.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
SEED_SCHEMA_VERSION = "1.0.0"

# Keep FK parents before their children.  A seed restores only these normalized
# records; raw source payload JSON and its retention pointers stay outside it.
CANONICAL_TABLES: tuple[str, ...] = (
    "catalog_variant",
    "catalog_variant_alias",
    "catalog_variant_locale",
    "catalog_source_identity",
    "catalog_provider_identity_alias",
    "catalog_printing_identity",
    "market_ingest_run",
    "market_ingest_checkpoint",
    "market_grader_population_observation",
    "market_population_transport_observation",
    "catalog_story_pointer",
    "market_image_asset",
    "market_image_qc",
    "market_image_source_pointer",
    "market_sale_observation",
    "market_tracked_sales_aggregate",
    "market_price_observation",
    "market_daily_sales_aggregate",
    "market_fx_rate_observation",
    "market_universe_lock",
    "market_universe_member",
    "market_index_snapshot",
    "market_index_constituent",
    "market_identity_review_queue",
    "market_identity_review_resolution",
    "market_alert_evaluation",
    "market_candidate_daily_snapshot",
    "market_alert",
    "market_alert_event",
)

EXCLUDED_RAW_TABLES: Mapping[str, str] = {
    "cardz_schema_version": "migration-owned metadata is recreated from repository migrations",
    "cardz_migration_ledger": "migration-owned metadata is recreated from repository migrations",
    "market_source_observation": "contains raw provider payload_json; private landing is authoritative",
    "market_raw_payload_object": "private archive object catalog, not canonical replay data",
    "market_retention_archive_manifest": "private raw-retention manifest, not canonical replay data",
    "market_source_observation_payload_pointer": "points to excluded raw provider observations",
    "market_source_effective_observation": "depends on excluded raw provider observations",
}

SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|bearer|cookie|password|secret)\s*[:=]"),
    re.compile(r"(?i)(?:x-amz-(?:credential|signature)|x-goog-signature|[?&](?:access_token|token|api_key)=)"),
    re.compile(r"(?i)-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


class SeedError(RuntimeError):
    """Raised when a canonical seed cannot be safely created or verified."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _driver() -> Any:
    try:
        import pymysql  # type: ignore[import-not-found]
    except ImportError as error:
        raise SeedError("pymysql is required; install pipelines/requirements.txt") from error
    return pymysql


def connect_from_environment() -> Any:
    password = os.environ.get("CARDZ_DB_PASSWORD")
    if not password:
        raise SeedError("CARDZ_DB_PASSWORD is required to build a logical seed")
    host = os.environ.get("CARDZ_DB_HOST", "127.0.0.1")
    port = int(os.environ.get("CARDZ_DB_PORT", "3306"))
    user = os.environ.get("CARDZ_DB_USER", "cardz")
    database = os.environ.get("CARDZ_DB_NAME", "cardz_market_cap")
    ssl_ca = os.environ.get("CARDZ_DB_SSL_CA")
    ssl: dict[str, Any] | None = None
    if ssl_ca:
        ca_path = Path(ssl_ca).expanduser().resolve()
        if not ca_path.is_file():
            raise SeedError("CARDZ_DB_SSL_CA does not exist")
        ssl = {"ca": str(ca_path), "check_hostname": True}
    return _driver().connect(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
        charset="utf8mb4",
        cursorclass=_driver().cursors.DictCursor,
        read_timeout=120,
        write_timeout=120,
        connect_timeout=10,
        ssl=ssl,
    )


def quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value):
        raise SeedError(f"unsafe SQL identifier: {value!r}")
    return f"`{value}`"


def sql_value(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        value = value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, bytes):
        scan_secret_bytes(value, context="canonical value")
        return f"X'{value.hex()}'"
    if not isinstance(value, str):
        value = str(value)
    # The logical SQL representation below is hex encoded, so scan the source
    # value before encoding instead of looking only at the generated SQL.
    scan_secret_bytes(value.encode("utf-8"), context="canonical value")
    # Hex avoids SQL-mode-dependent quote/backslash escaping and preserves
    # every Unicode card name exactly under utf8mb4.
    return f"CONVERT(0x{value.encode('utf-8').hex()} USING utf8mb4)"


def scan_secret_bytes(data: bytes, *, context: str) -> None:
    text = data.decode("utf-8", errors="replace")
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(text):
            raise SeedError(f"secret-like content found in {context}")


def existing_tables(connection: Any) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute("SHOW TABLES")
        rows = cursor.fetchall()
    tables: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping) or len(row) != 1:
            raise SeedError("unexpected SHOW TABLES result")
        tables.add(str(next(iter(row.values()))))
    return tables


def table_columns(connection: Any, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SHOW COLUMNS FROM {quote_identifier(table)}")
        rows = cursor.fetchall()
    columns = [str(row.get("Field") or "") for row in rows if isinstance(row, Mapping)]
    if not columns or any(not re.fullmatch(r"[A-Za-z0-9_]+", column) for column in columns):
        raise SeedError(f"could not read columns for {table}")
    return columns


def primary_key_columns(connection: Any, table: str) -> list[str]:
    with connection.cursor() as cursor:
        cursor.execute(f"SHOW KEYS FROM {quote_identifier(table)} WHERE Key_name = 'PRIMARY'")
        rows = cursor.fetchall()
    ordered = sorted(
        (row for row in rows if isinstance(row, Mapping)),
        key=lambda row: int(row.get("Seq_in_index") or 0),
    )
    return [str(row.get("Column_name") or "") for row in ordered if row.get("Column_name")]


def iter_table_rows(connection: Any, table: str, columns: Sequence[str], order_by: Sequence[str]) -> Iterable[Mapping[str, Any]]:
    selected = ", ".join(quote_identifier(column) for column in columns)
    order = ", ".join(quote_identifier(column) for column in (order_by or columns))
    with connection.cursor() as cursor:
        cursor.execute(f"SELECT {selected} FROM {quote_identifier(table)} ORDER BY {order}")
        while rows := cursor.fetchmany(500):
            for row in rows:
                if not isinstance(row, Mapping):
                    raise SeedError(f"unexpected row format for {table}")
                yield row


def sql_row(table: str, columns: Sequence[str], row: Mapping[str, Any]) -> str:
    if set(row) != set(columns):
        raise SeedError(f"column mismatch while reading {table}")
    names = ", ".join(quote_identifier(column) for column in columns)
    values = ", ".join(sql_value(row[column]) for column in columns)
    return f"INSERT INTO {quote_identifier(table)} ({names}) VALUES ({values});\n"


def default_manifest_path(seed_path: Path) -> Path:
    name = seed_path.name
    if name.endswith(".sql.gz"):
        name = name[: -len(".sql.gz")]
    return seed_path.with_name(f"{name}.manifest.json")


def migration_hashes() -> dict[str, str]:
    migrations = ROOT / "pipelines" / "migrations"
    return {
        path.name: sha256_file(path)
        for path in sorted(migrations.glob("*.mysql.sql"))
    }


def build_seed(connection: Any, output: Path, *, manifest_path: Path | None = None, overwrite: bool = False) -> Mapping[str, Any]:
    output = output.resolve()
    manifest_path = (manifest_path or default_manifest_path(output)).resolve()
    if output.exists() and not overwrite:
        raise SeedError(f"seed already exists: {output}")
    if manifest_path.exists() and not overwrite:
        raise SeedError(f"seed manifest already exists: {manifest_path}")
    if output == manifest_path:
        raise SeedError("seed and manifest paths must differ")
    found = existing_tables(connection)
    missing = [table for table in CANONICAL_TABLES if table not in found]
    if missing:
        raise SeedError(f"canonical migrations are incomplete; missing tables: {', '.join(missing)}")

    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_name = tempfile.mkstemp(prefix="cardz-canonical-seed-", suffix=".sql", dir=str(output.parent))
    raw_path = Path(raw_name)
    os.close(fd)
    temporary_seed = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary_manifest = manifest_path.with_name(f".{manifest_path.name}.{os.getpid()}.tmp")
    table_rows: dict[str, int] = {}
    try:
        with raw_path.open("wb") as handle:
            header = (
                "-- CARDZ canonical logical seed\n"
                "-- Apply repository migrations before this file.\n"
                "-- Raw provider payload tables are intentionally excluded.\n"
            ).encode("utf-8")
            scan_secret_bytes(header, context="seed header")
            handle.write(header)
            for table in CANONICAL_TABLES:
                columns = table_columns(connection, table)
                order = primary_key_columns(connection, table)
                count = 0
                for row in iter_table_rows(connection, table, columns, order):
                    statement = sql_row(table, columns, row).encode("utf-8")
                    scan_secret_bytes(statement, context=f"canonical table {table}")
                    handle.write(statement)
                    count += 1
                table_rows[table] = count
            footer = b""
            scan_secret_bytes(footer, context="seed footer")
            handle.write(footer)
        scan_secret_bytes(raw_path.read_bytes(), context="logical seed")
        with raw_path.open("rb") as source, temporary_seed.open("wb") as target:
            with gzip.GzipFile(filename="", mode="wb", fileobj=target, mtime=0) as compressed:
                while chunk := source.read(1024 * 1024):
                    compressed.write(chunk)
        manifest: dict[str, Any] = {
            "schemaVersion": SEED_SCHEMA_VERSION,
            "kind": "cardz-canonical-mysql-logical-seed",
            "seedFile": output.name,
            "seedSha256": sha256_file(temporary_seed),
            "uncompressedSha256": sha256_file(raw_path),
            "tableRows": table_rows,
            "canonicalTables": list(CANONICAL_TABLES),
            "excludedTables": dict(EXCLUDED_RAW_TABLES),
            "migrationSha256": migration_hashes(),
        }
        manifest_bytes = canonical_json(manifest)
        scan_secret_bytes(manifest_bytes, context="seed manifest")
        temporary_manifest.write_bytes(manifest_bytes)
        os.replace(temporary_seed, output)
        os.replace(temporary_manifest, manifest_path)
        return manifest
    finally:
        raw_path.unlink(missing_ok=True)
        temporary_seed.unlink(missing_ok=True)
        temporary_manifest.unlink(missing_ok=True)


def verify_seed(seed_path: Path, manifest_path: Path | None = None) -> Mapping[str, Any]:
    seed_path = seed_path.resolve()
    manifest_path = (manifest_path or default_manifest_path(seed_path)).resolve()
    if not seed_path.is_file() or not manifest_path.is_file():
        raise SeedError("seed and detached manifest must both exist")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SeedError("seed manifest is invalid JSON") from error
    if not isinstance(manifest, Mapping):
        raise SeedError("seed manifest must be an object")
    if manifest.get("schemaVersion") != SEED_SCHEMA_VERSION or manifest.get("kind") != "cardz-canonical-mysql-logical-seed":
        raise SeedError("seed manifest schema or kind is unsupported")
    if manifest.get("seedFile") != seed_path.name or manifest.get("seedSha256") != sha256_file(seed_path):
        raise SeedError("seed file does not match detached manifest")
    scan_secret_bytes(seed_path.read_bytes(), context="compressed seed")
    try:
        raw = gzip.decompress(seed_path.read_bytes())
    except OSError as error:
        raise SeedError("seed is not a valid gzip file") from error
    scan_secret_bytes(raw, context="logical seed")
    if manifest.get("uncompressedSha256") != hashlib.sha256(raw).hexdigest():
        raise SeedError("uncompressed seed does not match detached manifest")
    rows = manifest.get("tableRows")
    if not isinstance(rows, Mapping) or set(rows) != set(CANONICAL_TABLES) or any(not isinstance(value, int) or value < 0 for value in rows.values()):
        raise SeedError("seed manifest row counts are invalid")
    if manifest.get("excludedTables") != dict(EXCLUDED_RAW_TABLES):
        raise SeedError("seed manifest raw-payload exclusion contract changed")
    migration_ledger = manifest.get("migrationSha256")
    if not isinstance(migration_ledger, Mapping) or migration_ledger != migration_hashes():
        raise SeedError("seed manifest does not match the repository migration ledger")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description="Build or verify a CARDZ canonical MySQL logical seed")
    subparsers = parser.add_subparsers(dest="action", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--manifest", type=Path)
    build.add_argument("--overwrite", action="store_true")
    verify = subparsers.add_parser("verify")
    verify.add_argument("--seed", type=Path, required=True)
    verify.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    if args.action == "build":
        connection = connect_from_environment()
        try:
            manifest = build_seed(connection, args.output, manifest_path=args.manifest, overwrite=args.overwrite)
        finally:
            connection.close()
    else:
        manifest = verify_seed(args.seed, manifest_path=args.manifest)
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SeedError as error:
        print(f"canonical seed error: {error}", file=sys.stderr)
        raise SystemExit(1) from None
