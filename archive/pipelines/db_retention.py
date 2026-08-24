#!/usr/bin/env python3
"""Plan and safely compact duplicated private source payloads.

The canonical metric tables remain untouched.  This tool only moves completed
``market_source_observation.payload_json`` values into a local content-addressed
private archive and replaces the in-row JSON with an archive hash pointer.
``--apply`` is deliberately required for any database write.
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


def database_plan(connection: Any, limit: int) -> dict[str, Any]:
    rows = fetch_completed_payload_rows(connection, limit)
    plan = build_compaction_plan(rows)
    plan["requestedLimit"] = limit
    return plan


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
                        observation_id=IF(VALUES(effective_at) >= effective_at, VALUES(observation_id), observation_id),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or explicitly apply CARDZ source-payload compaction")
    parser.add_argument("--limit", type=int, default=25_000)
    parser.add_argument("--plan-json", type=Path)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--apply", action="store_true", help="write archive objects and transactional DB pointers")
    args = parser.parse_args()
    with connection_from_environment() as connection:
        if args.apply:
            result = apply_compaction(connection, args.archive_root.resolve(), limit=args.limit)
        else:
            result = database_plan(connection, args.limit)
        connection.close()
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
