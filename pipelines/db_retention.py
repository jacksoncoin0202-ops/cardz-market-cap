#!/usr/bin/env python3
"""Plan storage, backfill effective pointers, or compact private source payloads.

The canonical metric tables remain untouched.  This tool only moves completed
``market_source_observation.payload_json`` values into a local content-addressed
private archive and replaces the in-row JSON with an archive hash pointer.
The separate ``--backfill-effective-pointers --apply`` operation only upserts
the effective-pointer table and never changes raw observations or payload JSON.
``--apply`` is deliberately required for every database write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping

if TYPE_CHECKING:
    import pymysql

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ARCHIVE_ROOT = ROOT / "data" / "runtime" / "private-payload-archive"
POINTER_KEY = "archivePayloadSha256"
CAPACITY_TABLES = (
    "market_source_observation",
    "market_source_observation_payload_pointer",
    "market_source_effective_observation",
    "market_raw_payload_object",
    "market_retention_archive_manifest",
)
IMPORT_ADVISORY_LOCK = "cardz_market_cap_import"


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def payload_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return canonical_json(value)


def is_archive_pointer(value: object) -> bool:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return False
    return isinstance(value, Mapping) and set(value) == {POINTER_KEY} and isinstance(value.get(POINTER_KEY), str)


def eligible_payload_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize completed raw-observation rows, skipping existing pointers."""

    result: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for row in rows:
        observation_id = row.get("id", row.get("observationId"))
        payload = row.get("payload_json", row.get("payloadJson"))
        if not isinstance(observation_id, int) or observation_id < 1 or payload is None or is_archive_pointer(payload):
            continue
        if observation_id in seen_ids:
            raise ValueError(f"duplicate observation id in retention input: {observation_id}")
        seen_ids.add(observation_id)
        identity = {
            "sourceCode": str(row.get("source_code", row.get("sourceCode", ""))),
            "externalEntityId": str(row.get("external_entity_id", row.get("externalEntityId", ""))),
            "observationKind": str(row.get("observation_kind", row.get("observationKind", ""))),
            "observedDate": str(row.get("observed_date", row.get("observedDate", ""))),
            "effectiveAt": str(row.get("effective_at", row.get("effectiveAt", ""))),
        }
        if not all(identity.values()):
            raise ValueError(f"retention observation {observation_id} is missing canonical identity fields")
        raw = payload_bytes(payload)
        result.append(
            {
                "id": observation_id,
                "payload": raw,
                "contentSha256": sha256_bytes(raw),
                **identity,
            }
        )
    return result


def build_compaction_plan(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Estimate dedupe effect without touching disk or a database."""

    candidates = eligible_payload_rows(rows)
    unique = {row["contentSha256"]: len(row["payload"]) for row in candidates}
    raw_bytes = sum(len(row["payload"]) for row in candidates)
    unique_bytes = sum(unique.values())
    return {
        "schemaVersion": "1.0.0",
        "mode": "dry-run",
        "eligibleRows": len(candidates),
        "uniquePayloadObjects": len(unique),
        "duplicateOccurrences": len(candidates) - len(unique),
        "rawPayloadBytes": raw_bytes,
        "contentAddressedBytes": unique_bytes,
        "estimatedMysqlBytesReleased": raw_bytes,
        "estimatedPrivateArchiveBytes": unique_bytes,
        "validObservationRule": "one effective row per card/source/metric/date via market_source_effective_observation",
        "fxPolicy": "native price and dated FX observations are retained; this tool never recalculates historical USD values",
        "canonicalPolicy": "price, population, sales, ranking and FX tables are excluded from compaction",
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(content)
    os.replace(temporary, path)


def write_content_addressed_archive(rows: Iterable[Mapping[str, Any]], archive_root: Path) -> dict[str, Any]:
    """Write immutable objects plus a hash manifest before any SQL mutation."""

    candidates = eligible_payload_rows(rows)
    captured_at = utc_now()
    objects: dict[str, int] = {}
    manifest_rows: list[dict[str, Any]] = []
    for row in candidates:
        content_hash = str(row["contentSha256"])
        object_path = archive_root / "objects" / f"{content_hash}.json"
        if object_path.is_file():
            existing = object_path.read_bytes()
            if sha256_bytes(existing) != content_hash:
                raise RuntimeError(f"content-addressed payload hash conflict: {object_path}")
        else:
            _atomic_write(object_path, row["payload"])
        objects[content_hash] = len(row["payload"])
        manifest_rows.append(
            {
                "observationId": row["id"],
                "contentSha256": content_hash,
                "sourceCode": row["sourceCode"],
                "externalEntityId": row["externalEntityId"],
                "observationKind": row["observationKind"],
                "observedDate": row["observedDate"],
                "effectiveAt": row["effectiveAt"],
            }
        )
    manifest_rows.sort(key=lambda row: int(row["observationId"]))
    manifest = {
        "schemaVersion": "1.0.0",
        "state": "complete",
        "createdAt": captured_at,
        "objectCount": len(objects),
        "observationCount": len(manifest_rows),
        "archivedBytes": sum(objects.values()),
        "objects": [{"contentSha256": key, "privateObjectKey": f"objects/{key}.json", "byteSize": objects[key]} for key in sorted(objects)],
        "observations": manifest_rows,
    }
    manifest_hash = sha256_bytes(canonical_json(manifest))
    manifest["manifestSha256"] = manifest_hash
    manifest_path = archive_root / "manifests" / f"{manifest_hash}.json"
    _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
    return {"manifest": manifest, "manifestPath": manifest_path, "candidates": candidates}


def verify_archive_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    declared = manifest.pop("manifestSha256", None)
    if not isinstance(declared, str) or declared != sha256_bytes(canonical_json(manifest)):
        raise RuntimeError(f"archive manifest hash mismatch: {path}")
    if manifest.get("state") != "complete":
        raise RuntimeError(f"archive manifest is not complete: {path}")
    return {**manifest, "manifestSha256": declared}


def _pymysql() -> Any:
    try:
        import pymysql
    except ModuleNotFoundError as error:
        raise RuntimeError("pymysql is required for DB retention; install pipelines/requirements.txt") from error
    return pymysql


def connection_from_environment() -> Any:
    password = os.environ.get("CARDZ_DB_PASSWORD")
    if not password:
        raise RuntimeError("CARDZ_DB_PASSWORD is required")
    driver = _pymysql()
    return driver.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=password,
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        autocommit=False,
        cursorclass=driver.cursors.DictCursor,
        connect_timeout=10,
        read_timeout=120,
        write_timeout=120,
    )


def fetch_completed_payload_rows(connection: Any, limit: int) -> list[dict[str, Any]]:
    if limit < 1:
        raise ValueError("retention limit must be positive")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source.id, source.payload_json, source.source_code, source.external_entity_id,
                   source.observation_kind, source.observed_date, source.effective_at
            FROM market_source_observation AS source
            INNER JOIN market_ingest_run AS run ON run.id = source.run_id AND run.status = 'complete'
            LEFT JOIN market_source_observation_payload_pointer AS pointer ON pointer.observation_id = source.id
            WHERE pointer.observation_id IS NULL
            ORDER BY source.id
            LIMIT %s
            """,
            (limit,),
        )
        return list(cursor.fetchall())


def fetch_capacity_report(connection: Any) -> dict[str, Any]:
    """Return global raw/pointer counts and approximate MySQL table capacity."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM market_source_observation) AS raw_rows,
                (SELECT COUNT(*) FROM market_source_observation_payload_pointer) AS payload_pointer_rows,
                (SELECT COUNT(*) FROM market_source_effective_observation) AS effective_pointer_rows,
                COUNT(*) AS uncompacted_rows,
                COALESCE(SUM(OCTET_LENGTH(source.payload_json)), 0) AS uncompacted_payload_bytes
            FROM market_source_observation AS source
            INNER JOIN market_ingest_run AS run ON run.id = source.run_id AND run.status = 'complete'
            LEFT JOIN market_source_observation_payload_pointer AS pointer ON pointer.observation_id = source.id
            WHERE pointer.observation_id IS NULL
            """
        )
        totals = cursor.fetchone()
        if not isinstance(totals, Mapping):
            raise RuntimeError("capacity count query did not return a row")
        placeholders = ",".join(["%s"] * len(CAPACITY_TABLES))
        cursor.execute(
            f"""
            SELECT TABLE_NAME AS table_name, TABLE_ROWS AS table_rows,
                   DATA_LENGTH AS data_length, INDEX_LENGTH AS index_length
            FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME IN ({placeholders})
            ORDER BY TABLE_NAME
            """,
            CAPACITY_TABLES,
        )
        table_rows = list(cursor.fetchall())
    tables: dict[str, dict[str, int]] = {}
    for row in table_rows:
        table_name = str(row["table_name"])
        data_bytes = int(row.get("data_length") or 0)
        index_bytes = int(row.get("index_length") or 0)
        tables[table_name] = {
            "rowsEstimate": int(row.get("table_rows") or 0),
            "dataBytes": data_bytes,
            "indexBytes": index_bytes,
            "totalBytes": data_bytes + index_bytes,
        }
    return {
        "rawRows": int(totals.get("raw_rows") or 0),
        "payloadPointers": int(totals.get("payload_pointer_rows") or 0),
        "effectivePointers": int(totals.get("effective_pointer_rows") or 0),
        "uncompactedRows": int(totals.get("uncompacted_rows") or 0),
        "uncompactedPayloadBytes": int(totals.get("uncompacted_payload_bytes") or 0),
        "tables": tables,
    }


def database_plan(connection: Any, limit: int) -> dict[str, Any]:
    rows = fetch_completed_payload_rows(connection, limit)
    plan = build_compaction_plan(rows)
    plan["requestedLimit"] = limit
    plan["capacity"] = fetch_capacity_report(connection)
    return plan


def fetch_effective_pointer_winners(connection: Any, limit: int) -> list[dict[str, Any]]:
    """Select incomplete/stale pointer keys using latest effective_at, then id."""

    if limit < 1:
        raise ValueError("effective-pointer backfill limit must be positive")
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT source.source_code, source.external_entity_id, source.observation_kind,
                   source.observed_date, source.id AS observation_id, source.effective_at
            FROM market_source_observation AS source
            INNER JOIN market_ingest_run AS run
                ON run.id = source.run_id AND run.status = 'complete'
            LEFT JOIN market_source_effective_observation AS current
                ON current.source_code = source.source_code
               AND current.external_entity_id = source.external_entity_id
               AND current.observation_kind = source.observation_kind
               AND current.observed_date = source.observed_date
            WHERE (
                    current.observation_id IS NULL
                    OR current.observation_id <> source.id
                    OR current.effective_at <> source.effective_at
                  )
              AND NOT EXISTS (
                    SELECT 1
                    FROM market_source_observation AS newer
                    INNER JOIN market_ingest_run AS newer_run
                        ON newer_run.id = newer.run_id AND newer_run.status = 'complete'
                    WHERE newer.source_code = source.source_code
                      AND newer.external_entity_id = source.external_entity_id
                      AND newer.observation_kind = source.observation_kind
                      AND newer.observed_date = source.observed_date
                      AND (
                            newer.effective_at > source.effective_at
                            OR (newer.effective_at = source.effective_at AND newer.id > source.id)
                          )
                  )
            ORDER BY source.source_code, source.external_entity_id,
                     source.observation_kind, source.observed_date
            LIMIT %s
            """,
            (limit,),
        )
        return list(cursor.fetchall())


def backfill_effective_pointers(connection: Any, *, limit: int) -> dict[str, Any]:
    """Fill every missing/stale complete-run effective pointer in bounded batches."""

    if limit < 1:
        raise ValueError("effective-pointer backfill limit must be positive")
    total = 0
    batches = 0
    acquired = False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0) AS acquired", (IMPORT_ADVISORY_LOCK,))
            row = cursor.fetchone()
            if not isinstance(row, Mapping) or int(row.get("acquired") or 0) != 1:
                raise RuntimeError("CARDZ database import writer lock is busy")
            acquired = True
        while True:
            with connection.cursor() as cursor:
                cursor.execute("START TRANSACTION")
            try:
                winners = fetch_effective_pointer_winners(connection, limit)
                if not winners:
                    connection.commit()
                    break
                selected_at = datetime.now(timezone.utc).replace(tzinfo=None)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        """
                        INSERT INTO market_source_effective_observation
                            (source_code, external_entity_id, observation_kind, observed_date,
                             observation_id, effective_at, selected_at)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            observation_id=VALUES(observation_id),
                            effective_at=VALUES(effective_at),
                            selected_at=VALUES(selected_at)
                        """,
                        [
                            (
                                winner["source_code"],
                                winner["external_entity_id"],
                                winner["observation_kind"],
                                winner["observed_date"],
                                winner["observation_id"],
                                winner["effective_at"],
                                selected_at,
                            )
                            for winner in winners
                        ],
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            total += len(winners)
            batches += 1
            if len(winners) < limit:
                break
    finally:
        if acquired:
            with connection.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (IMPORT_ADVISORY_LOCK,))
    return {
        "applied": total > 0,
        "operation": "backfill-effective-pointers",
        "backfilledPointers": total,
        "batches": batches,
        "winnerRule": "latest effective_at, then highest observation id, from complete ingest runs only",
        "rawObservationsChanged": 0,
        "payloadJsonChanged": 0,
    }


def apply_compaction(
    connection: Any,
    archive_root: Path,
    *,
    limit: int,
) -> dict[str, Any]:
    """Archive complete-run payloads then atomically replace only JSON bodies with pointers."""

    rows = fetch_completed_payload_rows(connection, limit)
    archive = write_content_addressed_archive(rows, archive_root)
    manifest_path = Path(archive["manifestPath"])
    manifest = verify_archive_manifest(manifest_path)
    candidates = archive["candidates"]
    if not candidates:
        return {"applied": False, "reason": "no_eligible_rows", "plan": build_compaction_plan([])}
    ids = [int(row["id"]) for row in candidates]
    content_by_id = {int(row["id"]): str(row["contentSha256"]) for row in candidates}
    archived_at = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION")
            placeholders = ",".join(["%s"] * len(ids))
            cursor.execute(
                f"""
                SELECT source.id
                FROM market_source_observation AS source
                INNER JOIN market_ingest_run AS run ON run.id = source.run_id AND run.status = 'complete'
                LEFT JOIN market_source_observation_payload_pointer AS pointer ON pointer.observation_id = source.id
                WHERE source.id IN ({placeholders}) AND pointer.observation_id IS NULL
                FOR UPDATE
                """,
                ids,
            )
            locked = {int(row["id"]) for row in cursor.fetchall()}
            if locked != set(ids):
                raise RuntimeError("retention candidates changed before transactional apply")
            cursor.execute(
                """
                INSERT INTO market_retention_archive_manifest
                    (manifest_sha256, private_manifest_key, object_count, observation_count, archived_bytes, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE private_manifest_key=VALUES(private_manifest_key)
                """,
                (
                    manifest["manifestSha256"],
                    str(manifest_path.relative_to(archive_root)).replace("\\", "/"),
                    manifest["objectCount"], manifest["observationCount"], manifest["archivedBytes"], archived_at,
                ),
            )
            for object_row in manifest["objects"]:
                cursor.execute(
                    """
                    INSERT IGNORE INTO market_raw_payload_object
                        (content_sha256, private_object_key, byte_size, archive_manifest_sha256, archived_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (
                        object_row["contentSha256"], object_row["privateObjectKey"], object_row["byteSize"],
                        manifest["manifestSha256"], archived_at,
                    ),
                )
            for observation_id in ids:
                content_hash = content_by_id[observation_id]
                cursor.execute(
                    """
                    INSERT INTO market_source_observation_payload_pointer
                        (observation_id, content_sha256, archive_manifest_sha256, archived_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (observation_id, content_hash, manifest["manifestSha256"], archived_at),
                )
                cursor.execute(
                    "UPDATE market_source_observation SET payload_json=JSON_OBJECT(%s, %s) WHERE id=%s",
                    (POINTER_KEY, content_hash, observation_id),
                )
            for observation in manifest["observations"]:
                cursor.execute(
                    """
                    INSERT INTO market_source_effective_observation
                        (source_code, external_entity_id, observation_kind, observed_date,
                         observation_id, effective_at, selected_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        observation_id=IF(
                            VALUES(effective_at) > effective_at
                            OR (VALUES(effective_at) = effective_at AND VALUES(observation_id) > observation_id),
                            VALUES(observation_id),
                            observation_id
                        ),
                        effective_at=GREATEST(effective_at, VALUES(effective_at)),
                        selected_at=VALUES(selected_at)
                    """,
                    (
                        observation["sourceCode"], observation["externalEntityId"], observation["observationKind"],
                        observation["observedDate"], observation["observationId"], observation["effectiveAt"], archived_at,
                    ),
                )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    return {
        "applied": True,
        "manifestSha256": manifest["manifestSha256"],
        "manifestPath": str(manifest_path),
        "compactedRows": len(ids),
        "uniquePayloadObjects": manifest["objectCount"],
        "archivedBytes": manifest["archivedBytes"],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report CARDZ DB capacity, backfill effective pointers, or explicitly compact source payloads"
    )
    parser.add_argument("--limit", type=int, default=25_000)
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--apply", action="store_true", help="write archive objects and transactional DB pointers")
    parser.add_argument(
        "--backfill-effective-pointers",
        action="store_true",
        help="with --apply, upsert complete-run effective winners only; payload compaction is not run",
    )
    args = parser.parse_args(argv)
    if args.backfill_effective_pointers and not args.apply:
        parser.error("--backfill-effective-pointers requires --apply")
    return args


def main() -> int:
    args = parse_args()
    with connection_from_environment() as connection:
        if args.backfill_effective_pointers:
            result = backfill_effective_pointers(connection, limit=args.limit)
        elif args.apply:
            result = apply_compaction(connection, args.archive_root.resolve(), limit=args.limit)
        else:
            result = database_plan(connection, args.limit)
    rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.plan_json:
        _atomic_write(args.plan_json.resolve(), rendered.encode("utf-8"))
    print(rendered)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
