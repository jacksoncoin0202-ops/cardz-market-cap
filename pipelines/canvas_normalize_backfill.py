#!/usr/bin/env python3
"""Import a checked private geometry-candidate ledger without publishing it.

The former canvas backfill mutated a pointed snapshot and is intentionally
replaced by this fail-closed importer.  It accepts only named, fixed geometry
cohorts, stages geometry-passing masters as private source-map objects, and
leaves semantic/public promotion to a later review path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
from failure_ledger import record_failure  # noqa: E402
from image_geometry_qc import inspect_path  # noqa: E402


LEDGER_SCHEMA_VERSION = 1
LEDGER_RUN_ID = "pm-image-remediation-20260731-geometry"
EXPECTED_INPUT_COUNT = 380
EXPECTED_SUCCESS_COUNT = 373
EXPECTED_UNRESOLVED_COUNT = 7
V3_LEDGER_SCHEMA = "cardz.geometry-remediation.v3"
V3_LEDGER_RUN_ID = "pm-image-remediation-20260731-geometry-v3"
V3_EXPECTED_INPUT_COUNT = 7
V3_EXPECTED_SUCCESS_COUNT = 5
V3_EXPECTED_UNRESOLVED_COUNT = 2
DERIVED_ROOT = ROOT / "data/runtime/private-source-map/image-derived/geometry"
QC_VERSION = "geometry-normalized-v1"
SEMANTIC_PENDING = "geometry_normalized_pending_review"
SOURCE = "canvas_geometry_import"
STAGE = "image_geometry"


class LedgerError(ValueError):
    """The candidate evidence cannot safely enter an importer transaction."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_backend_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise LedgerError(f"candidate_path_escapes_ledger_root:{path}") from exc
    return resolved


def _required_sha(value: Any, field: str) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise LedgerError(f"invalid_{field}")
    return text


def _ledger_profile(document: Mapping[str, Any]) -> tuple[int, int, int]:
    if document.get("schemaVersion") != LEDGER_SCHEMA_VERSION:
        raise LedgerError("ledger_schema_not_named_geometry_v1")
    ledger_schema = document.get("ledgerSchema")
    run_id = document.get("runId")
    if ledger_schema in (None, "cardz.geometry-remediation.v2") and run_id == LEDGER_RUN_ID:
        return EXPECTED_INPUT_COUNT, EXPECTED_SUCCESS_COUNT, EXPECTED_UNRESOLVED_COUNT
    if ledger_schema == V3_LEDGER_SCHEMA and run_id == V3_LEDGER_RUN_ID:
        return V3_EXPECTED_INPUT_COUNT, V3_EXPECTED_SUCCESS_COUNT, V3_EXPECTED_UNRESOLVED_COUNT
    raise LedgerError("ledger_schema_not_named_geometry_v1")


def validate_ledger(path: Path, expected_sha256: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    path = path.resolve()
    if sha256_file(path) != _required_sha(expected_sha256, "ledger_sha256"):
        raise LedgerError("ledger_sha256_mismatch")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LedgerError("ledger_invalid_json") from exc
    if not isinstance(document, dict):
        raise LedgerError("ledger_not_object")
    expected_input, expected_success, expected_unresolved = _ledger_profile(document)
    if document.get("inputDecision") != "reject_geometry":
        raise LedgerError("ledger_input_decision_not_reject_geometry")
    counts = document.get("counts") or {}
    if (
        document.get("exactInputCount") != expected_input
        or document.get("uniqueVariantAssetPairs") != expected_input
        or counts.get("inputRejectGeometry") != expected_input
        or counts.get("candidateSuccess") != expected_success
        or counts.get("unresolved") != expected_unresolved
        or counts.get("postNormalizationRejectGeometry") != expected_unresolved
    ):
        raise LedgerError("ledger_fixed_cohort_counts_invalid")
    records = document.get("records")
    if not isinstance(records, list) or len(records) != expected_input:
        raise LedgerError("ledger_record_count_invalid")
    seen: set[tuple[int, int]] = set()
    success: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    for raw in records:
        if not isinstance(raw, dict):
            raise LedgerError("ledger_record_not_object")
        try:
            key = (int(raw["variantId"]), int(raw["assetId"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise LedgerError("ledger_variant_asset_invalid") from exc
        if key in seen:
            raise LedgerError("ledger_variant_asset_not_unique")
        seen.add(key)
        status = raw.get("status")
        if status == "success":
            _validate_success_record(raw, path.parent)
            success.append(dict(raw))
        elif status == "unresolved":
            if raw.get("postNormalizationGeometryClassification") != "reject_geometry":
                raise LedgerError("unresolved_geometry_classification_invalid")
            unresolved.append(dict(raw))
        else:
            raise LedgerError("ledger_status_invalid")
    if len(success) != expected_success or len(unresolved) != expected_unresolved:
        raise LedgerError("ledger_success_unresolved_counts_invalid")
    return document, success, unresolved


def _validate_success_record(record: Mapping[str, Any], ledger_root: Path) -> None:
    if record.get("postNormalizationGeometryClassification") != "pass_geometry":
        raise LedgerError("success_geometry_classification_invalid")
    before_sha = _required_sha(record.get("beforeSha256"), "before_sha256")
    if before_sha != _required_sha(record.get("reportContentSha256"), "report_content_sha256"):
        raise LedgerError("before_sha_does_not_bind_report")
    _required_sha(record.get("sourceVersionSha256"), "source_version_sha256")
    relative = record.get("candidateMaster")
    if not isinstance(relative, str) or not relative.strip():
        raise LedgerError("candidate_path_missing")
    candidate = _inside(ledger_root / relative, ledger_root)
    if not candidate.is_file():
        raise LedgerError("candidate_file_missing")
    if sha256_file(candidate) != _required_sha(record.get("afterSha256"), "after_sha256"):
        raise LedgerError("candidate_bytes_sha256_mismatch")
    geometry = inspect_path(candidate)
    if geometry.get("status") != "passed" or [geometry.get("widthPx"), geometry.get("heightPx")] != [429, 600]:
        raise LedgerError("candidate_geometry_invalid")
    if record.get("afterDimensions") != [429, 600]:
        raise LedgerError("candidate_ledger_dimensions_invalid")


def fetch_current_asset(connection: Any, record: Mapping[str, Any]) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT asset.id AS asset_id, asset.variant_id, asset.content_sha256,
                   asset.source_version_sha256, qc.card_number_match,
                   qc.language_match, qc.tcg_match, qc.raw_front_confirmed
            FROM market_image_asset AS asset
            LEFT JOIN market_image_qc AS qc ON qc.image_asset_id = asset.id
            WHERE asset.id = %s AND asset.variant_id = %s AND asset.image_kind = 'raw_front'
            ORDER BY qc.checked_at DESC, qc.id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (int(record["assetId"]), int(record["variantId"])),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def validate_current_asset(record: Mapping[str, Any], current: Mapping[str, Any] | None) -> str | None:
    if current is None:
        return "current_asset_missing"
    if int(current.get("asset_id") or 0) != int(record["assetId"]):
        return "asset_id_drift"
    if int(current.get("variant_id") or 0) != int(record["variantId"]):
        return "variant_id_drift"
    if str(current.get("content_sha256") or "").casefold() != str(record["beforeSha256"]).casefold():
        return "before_sha_drift"
    if str(current.get("source_version_sha256") or "").casefold() != str(record["sourceVersionSha256"]).casefold():
        return "source_version_drift"
    return None


def copy_candidate(record: Mapping[str, Any], ledger_root: Path) -> Path:
    source = _inside(ledger_root / str(record["candidateMaster"]), ledger_root)
    destination = DERIVED_ROOT / f"{record['afterSha256']}.webp"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        if sha256_file(destination) != str(record["afterSha256"]):
            raise RuntimeError("durable_candidate_hash_collision")
        return destination
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    shutil.copyfile(source, temporary)
    if sha256_file(temporary) != str(record["afterSha256"]):
        temporary.unlink(missing_ok=True)
        raise RuntimeError("durable_candidate_copy_hash_mismatch")
    os.replace(temporary, destination)
    return destination


def fetch_existing_derived_asset(
    connection: Any,
    record: Mapping[str, Any],
) -> dict[str, Any] | None:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT asset.id AS asset_id, asset.private_object_key, asset.mime_type,
                   asset.width_px, asset.height_px, asset.source_version_sha256,
                   qc.id AS qc_id, qc.semantic_match_status, qc.card_number_match,
                   qc.language_match, qc.tcg_match, qc.raw_front_confirmed,
                   qc.public_allowed, qc.rejection_reason
            FROM market_image_asset AS asset
            LEFT JOIN market_image_qc AS qc
              ON qc.image_asset_id = asset.id AND qc.qc_version = %s
            WHERE asset.variant_id = %s AND asset.image_kind = 'raw_front'
              AND asset.content_sha256 = %s
            FOR UPDATE
            """,
            (QC_VERSION, int(record["variantId"]), record["afterSha256"]),
        )
        row = cursor.fetchone()
    return dict(row) if row else None


def assert_existing_derived_noop(
    existing: Mapping[str, Any],
    record: Mapping[str, Any],
    current: Mapping[str, Any],
    destination: Path,
) -> None:
    expected_key = destination.relative_to(ROOT).as_posix()
    expected = {
        "private_object_key": expected_key,
        "mime_type": "image/webp",
        "width_px": 429,
        "height_px": 600,
        "source_version_sha256": record["sourceVersionSha256"],
        "semantic_match_status": SEMANTIC_PENDING,
        "card_number_match": int(bool(current.get("card_number_match"))),
        "language_match": int(bool(current.get("language_match"))),
        "tcg_match": int(bool(current.get("tcg_match"))),
        "raw_front_confirmed": int(bool(current.get("raw_front_confirmed"))),
        "public_allowed": 0,
        "rejection_reason": "geometry_normalized_pending_review",
    }
    if existing.get("qc_id") is None:
        raise RuntimeError("derived_asset_geometry_qc_missing")
    for field, value in expected.items():
        actual = existing.get(field)
        if field in {
            "width_px",
            "height_px",
            "card_number_match",
            "language_match",
            "tcg_match",
            "raw_front_confirmed",
            "public_allowed",
        }:
            actual = int(actual or 0)
        if actual != value:
            raise RuntimeError(f"derived_asset_idempotence_drift:{field}")
    if not destination.is_file():
        raise RuntimeError("derived_asset_file_missing")
    if sha256_file(destination) != str(record["afterSha256"]):
        raise RuntimeError("derived_asset_file_hash_drift")


def apply_record(connection: Any, record: Mapping[str, Any], current: Mapping[str, Any], ledger_root: Path) -> dict[str, Any]:
    destination = DERIVED_ROOT / f"{record['afterSha256']}.webp"
    existing = fetch_existing_derived_asset(connection, record)
    if existing is not None:
        assert_existing_derived_noop(existing, record, current, destination)
        return {
            "derivedAssetId": int(existing["asset_id"]),
            "privateObjectKey": destination.relative_to(ROOT).as_posix(),
            "write": False,
            "noOp": True,
        }

    destination = copy_candidate(record, ledger_root)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    relative_key = destination.relative_to(ROOT).as_posix()
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO market_image_asset
              (variant_id, image_kind, content_sha256, private_object_key, mime_type,
               width_px, height_px, source_version_sha256, captured_at)
            VALUES (%s, 'raw_front', %s, %s, 'image/webp', 429, 600, %s, %s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """,
            (int(record["variantId"]), record["afterSha256"], relative_key, record["sourceVersionSha256"], now),
        )
        derived_asset_id = int(cursor.lastrowid)
        cursor.execute(
            """
            INSERT INTO market_image_qc
              (image_asset_id, semantic_match_status, card_number_match, language_match,
               tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
               checked_at, qc_version)
            VALUES (%s, %s, %s, %s, %s, %s, 0, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
              semantic_match_status=VALUES(semantic_match_status),
              card_number_match=VALUES(card_number_match), language_match=VALUES(language_match),
              tcg_match=VALUES(tcg_match), raw_front_confirmed=VALUES(raw_front_confirmed),
              public_allowed=0, rejection_reason=VALUES(rejection_reason),
              checked_at=VALUES(checked_at)
            """,
            (
                derived_asset_id, SEMANTIC_PENDING,
                int(bool(current.get("card_number_match"))), int(bool(current.get("language_match"))),
                int(bool(current.get("tcg_match"))), int(bool(current.get("raw_front_confirmed"))),
                "geometry_normalized_pending_review", now, QC_VERSION,
            ),
        )
        cursor.execute(
            """
            SELECT asset.id AS asset_id, asset.variant_id, asset.content_sha256,
                   asset.private_object_key, asset.width_px, asset.height_px,
                   qc.semantic_match_status, qc.public_allowed, qc.card_number_match,
                   qc.language_match, qc.tcg_match, qc.raw_front_confirmed
            FROM market_image_asset AS asset
            JOIN market_image_qc AS qc ON qc.image_asset_id = asset.id
            WHERE asset.id = %s AND qc.qc_version = %s
            """,
            (derived_asset_id, QC_VERSION),
        )
        readback = cursor.fetchone()
    if not readback or str(readback.get("content_sha256") or "") != str(record["afterSha256"]):
        raise RuntimeError("derived_asset_readback_failed")
    if readback.get("semantic_match_status") != SEMANTIC_PENDING or int(readback.get("public_allowed") or 0) != 0:
        raise RuntimeError("derived_qc_approval_boundary_failed")
    return {
        "derivedAssetId": derived_asset_id,
        "privateObjectKey": relative_key,
        "write": True,
        "noOp": False,
    }


def record_failures(records: Sequence[Mapping[str, Any]], *, ledger_path: Path, run_id: str, reason: str, ledger_root: Path) -> int:
    written = 0
    for record in records:
        event = record_failure(
            source=SOURCE, stage=STAGE, script=Path(__file__),
            item_key=f"variant:{record.get('variantId')}:asset:{record.get('assetId')}",
            reason_code=reason if reason else str(record.get("unresolvedReason") or "geometry_unresolved"),
            message=str(record.get("unresolvedReason") or reason or "geometry candidate blocked"),
            retryable=True, run_id=run_id,
            context={"variantId": record.get("variantId"), "assetId": record.get("assetId"), "contentSha256": record.get("beforeSha256")},
            evidence_paths=[ledger_path], next_action="replace_or_reframe_geometry_candidate",
            ledger_root=ledger_root,
        )
        written += event is not None
    return written


def run(args: argparse.Namespace) -> dict[str, Any]:
    document, success, unresolved = validate_ledger(args.ledger, args.ledger_sha256)
    failures_written = record_failures(unresolved, ledger_path=args.ledger, run_id=str(document["runId"]), reason="", ledger_root=args.failure_ledger_root)
    load_backend_env()
    connection = db_runtime.connection_from_args(args)
    try:
        drifted: list[dict[str, Any]] = []
        current_rows: dict[tuple[int, int], dict[str, Any]] = {}
        for record in success:
            current = fetch_current_asset(connection, record)
            drift = validate_current_asset(record, current)
            if drift:
                drifted.append({**record, "unresolvedReason": drift})
            else:
                current_rows[(int(record["variantId"]), int(record["assetId"]))] = dict(current or {})
        failures_written += record_failures(drifted, ledger_path=args.ledger, run_id=str(document["runId"]), reason="", ledger_root=args.failure_ledger_root)
        if drifted:
            connection.rollback()
            return {"mode": "write" if args.write else "dry_run", "status": "blocked_drift", "candidateReady": 0, "unresolved": len(unresolved), "drifted": len(drifted), "failureEventsWritten": failures_written, "transaction": "rolled_back"}
        writes: list[dict[str, Any]] = []
        if args.write:
            for record in success:
                writes.append(apply_record(connection, record, current_rows[(int(record["variantId"]), int(record["assetId"]))], args.ledger.parent))
            connection.commit()
            transaction = "committed"
        else:
            connection.rollback()
            transaction = "rolled_back"
        return {
            "mode": "write" if args.write else "dry_run",
            "status": "candidate_ready_partial",
            "candidateReady": len(success),
            "unresolved": len(unresolved),
            "drifted": 0,
            "failureEventsWritten": failures_written,
            "transaction": transaction,
            "mutated": sum(bool(result.get("write")) for result in writes),
            "noOp": sum(bool(result.get("noOp")) for result in writes),
            "writes": writes,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--ledger-sha256", required=True)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--failure-ledger-root", type=Path, default=ROOT / "data/runtime/failures")
    db_runtime.add_connection_args(parser)
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (LedgerError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "candidate_ready_partial" else 2


if __name__ == "__main__":
    raise SystemExit(main())
