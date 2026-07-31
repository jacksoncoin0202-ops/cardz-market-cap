#!/usr/bin/env python3
"""Quarantine one fixed polluted image-source family without deleting history.

Dry-run is the default.  The only supported family is
``limitless-one-piece-en``.  A write is allowed only when the current/latest
One Piece ``raw_front`` set contains exactly 320 policy-rejected assets,
comprising the pinned 146-card release cohort plus 174 outer/non-release
cards.  Writes append one versioned QC decision for those exact assets and
disable only their exact source pointer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
from failure_ledger import current_failures, record_failure  # noqa: E402
from image_source_qc import classify_source  # noqa: E402


FAMILY = "limitless-one-piece-en"
TCG_CODE = "one-piece"
EXPECTED_COUNT = 320
RELEASE_COHORT_COUNT = 146
OUTER_COUNT = 174
REASON = "known_sample_source_limitless_one_piece_en"
QC_VERSION = "source-ban-v1"
SEMANTIC_STATUS = "source_family_quarantined"
SOURCE = FAMILY
STAGE = "image_source_quarantine"
DEFAULT_RECEIPT_ROOT = (
    ROOT / "data/runtime/private-reports/image-source-family-quarantine"
)
DEFAULT_FAILURE_ROOT = ROOT / "data/runtime/failures"
RELEASE_COHORT_PATH = (
    ROOT
    / "data/runtime/private-reports/pm-image-remediation-20260731/"
    "limitless/wave2/replacement-manifest-wave2.json"
)
RELEASE_COHORT_SHA256 = (
    "3dd9e28b286b5efadfd51d6dbd77cb2e1e9271e5c48e1d3920a94b3229d2cb4f"
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class QuarantineError(RuntimeError):
    """The fixed-family quarantine contract failed closed."""


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def utc_text() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def utc_run_suffix() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def load_backend_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def load_release_cohort(
    path: Path = RELEASE_COHORT_PATH,
    expected_sha256: str = RELEASE_COHORT_SHA256,
) -> set[int]:
    try:
        raw = path.resolve().read_bytes()
        document = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QuarantineError("release_cohort_manifest_invalid") from error
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise QuarantineError("release_cohort_manifest_sha256_mismatch")
    counts = document.get("counts")
    if (
        not isinstance(counts, Mapping)
        or int(counts.get("total") or 0) != RELEASE_COHORT_COUNT
    ):
        raise QuarantineError("release_cohort_count_invalid")
    variant_ids: set[int] = set()
    for bucket in ("ready", "review", "reject", "unresolved"):
        records = document.get(bucket)
        if not isinstance(records, list):
            raise QuarantineError(f"release_cohort_bucket_invalid:{bucket}")
        for record in records:
            if not isinstance(record, Mapping):
                raise QuarantineError(f"release_cohort_record_invalid:{bucket}")
            variant_id = int(record.get("variantId") or 0)
            target = record.get("target")
            wave1 = (
                target.get("wave1Target")
                if isinstance(target, Mapping)
                else None
            )
            if (
                variant_id <= 0
                or not isinstance(wave1, Mapping)
                or wave1.get("rejectReason") != REASON
                or wave1.get("tcg") != TCG_CODE
            ):
                raise QuarantineError(
                    f"release_cohort_family_evidence_invalid:{variant_id}"
                )
            if variant_id in variant_ids:
                raise QuarantineError("release_cohort_variant_duplicate")
            variant_ids.add(variant_id)
    if len(variant_ids) != RELEASE_COHORT_COUNT:
        raise QuarantineError(
            f"release_cohort_variant_count_invalid:{len(variant_ids)}"
        )
    return variant_ids


def fetch_latest_one_piece_raw_fronts(connection: Any) -> list[dict[str, Any]]:
    """Return one latest raw-front asset and its exact-version pointer per card."""

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT
                asset.id AS asset_id,
                asset.variant_id,
                asset.content_sha256,
                asset.source_version_sha256,
                asset.width_px,
                asset.height_px,
                asset.captured_at,
                pointer.source_path,
                pointer.public_allowed AS pointer_public_allowed
            FROM market_image_asset AS asset
            JOIN catalog_variant AS variant ON variant.id=asset.variant_id
            LEFT JOIN market_image_source_pointer AS pointer
              ON pointer.variant_id=asset.variant_id
             AND pointer.image_kind=asset.image_kind
             AND pointer.source_version_sha256=asset.source_version_sha256
            WHERE variant.tcg_code=%s
              AND asset.image_kind='raw_front'
              AND NOT EXISTS (
                  SELECT 1
                  FROM market_image_asset AS newer
                  WHERE newer.variant_id=asset.variant_id
                    AND newer.image_kind=asset.image_kind
                    AND (
                        newer.captured_at>asset.captured_at
                        OR (
                            newer.captured_at=asset.captured_at
                            AND newer.id>asset.id
                        )
                    )
              )
            ORDER BY asset.variant_id
            """,
            (TCG_CODE,),
        )
        return [dict(row) for row in cursor.fetchall()]


def _validate_candidate(row: Mapping[str, Any]) -> dict[str, Any] | None:
    policy = classify_source(
        str(row.get("source_path") or ""),
        tcg_code=TCG_CODE,
        width_px=row.get("width_px"),
        height_px=row.get("height_px"),
    )
    if policy.get("family") != FAMILY:
        return None
    variant_id = int(row.get("variant_id") or 0)
    asset_id = int(row.get("asset_id") or 0)
    content_sha256 = str(row.get("content_sha256") or "").casefold()
    source_version_sha256 = str(row.get("source_version_sha256") or "").casefold()
    source_path = str(row.get("source_path") or "")
    if (
        variant_id <= 0
        or asset_id <= 0
        or not SHA256_RE.fullmatch(content_sha256)
        or not SHA256_RE.fullmatch(source_version_sha256)
        or not source_path
    ):
        raise QuarantineError(f"family_candidate_incomplete:{variant_id}:{asset_id}")
    if policy.get("status") != "reject" or policy.get("reason") != REASON:
        raise QuarantineError(f"family_policy_not_rejected:{variant_id}:{asset_id}")
    return {
        "variantId": variant_id,
        "assetId": asset_id,
        "contentSha256": content_sha256,
        "sourceVersionSha256": source_version_sha256,
        "sourcePath": source_path,
        "pointerPublicAllowed": int(bool(row.get("pointer_public_allowed"))),
        "policyId": policy.get("policyId"),
        "ruleId": policy.get("ruleId"),
        "family": policy.get("family"),
        "reason": policy.get("reason"),
    }


def build_plan(
    rows: Sequence[Mapping[str, Any]],
    release_variant_ids: set[int],
) -> dict[str, Any]:
    if len(release_variant_ids) != RELEASE_COHORT_COUNT:
        raise QuarantineError("release_cohort_variant_count_invalid")
    candidates = [
        candidate
        for row in rows
        if (candidate := _validate_candidate(row)) is not None
    ]
    variant_ids = [row["variantId"] for row in candidates]
    asset_ids = [row["assetId"] for row in candidates]
    if len(candidates) != EXPECTED_COUNT:
        raise QuarantineError(
            f"family_count_mismatch:expected_{EXPECTED_COUNT}:actual_{len(candidates)}"
        )
    if (
        len(set(variant_ids)) != EXPECTED_COUNT
        or len(set(asset_ids)) != EXPECTED_COUNT
    ):
        raise QuarantineError("family_candidate_identity_not_unique")
    matched_variant_ids = set(variant_ids)
    release_overlap = matched_variant_ids & release_variant_ids
    outer_variant_ids = matched_variant_ids - release_variant_ids
    if release_overlap != release_variant_ids:
        raise QuarantineError(
            "release_cohort_overlap_mismatch:"
            f"expected_{RELEASE_COHORT_COUNT}:actual_{len(release_overlap)}"
        )
    if len(outer_variant_ids) != OUTER_COUNT:
        raise QuarantineError(
            f"outer_family_count_mismatch:expected_{OUTER_COUNT}:"
            f"actual_{len(outer_variant_ids)}"
        )
    public_pointer_count = sum(row["pointerPublicAllowed"] for row in candidates)
    public_projection = [
        {
            **{
                key: row[key]
                for key in (
                    "variantId",
                    "assetId",
                    "contentSha256",
                    "sourceVersionSha256",
                    "family",
                    "reason",
                )
            },
            "releaseCohort": row["variantId"] in release_variant_ids,
        }
        for row in candidates
    ]
    plan_sha256 = sha256_json(public_projection)
    return {
        "family": FAMILY,
        "tcg": TCG_CODE,
        "expected": EXPECTED_COUNT,
        "matched": len(candidates),
        "releaseCohortOverlap": len(release_overlap),
        "outerNonRelease": len(outer_variant_ids),
        "publicPointersBefore": public_pointer_count,
        "planSha256": plan_sha256,
        "candidates": candidates,
    }


def _locked_current_row(cursor: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            asset.id AS asset_id,
            asset.variant_id,
            asset.content_sha256,
            asset.source_version_sha256,
            asset.width_px,
            asset.height_px,
            pointer.source_path,
            pointer.public_allowed AS pointer_public_allowed
        FROM market_image_asset AS asset
        JOIN catalog_variant AS variant ON variant.id=asset.variant_id
        JOIN market_image_source_pointer AS pointer
          ON pointer.variant_id=asset.variant_id
         AND pointer.image_kind=asset.image_kind
         AND pointer.source_version_sha256=asset.source_version_sha256
        WHERE asset.id=%s
          AND asset.variant_id=%s
          AND asset.image_kind='raw_front'
          AND variant.tcg_code=%s
        FOR UPDATE
        """,
        (candidate["assetId"], candidate["variantId"], TCG_CODE),
    )
    row = cursor.fetchone()
    if row is None:
        raise QuarantineError(
            f"current_family_asset_missing:{candidate['variantId']}:{candidate['assetId']}"
        )
    cursor.execute(
        """
        SELECT id
        FROM market_image_asset
        WHERE variant_id=%s AND image_kind='raw_front'
        ORDER BY captured_at DESC,id DESC
        LIMIT 1
        FOR UPDATE
        """,
        (candidate["variantId"],),
    )
    latest = cursor.fetchone()
    if latest is None or int(latest["id"]) != int(candidate["assetId"]):
        raise QuarantineError(f"latest_asset_drift:{candidate['variantId']}")
    return dict(row)


def _assert_live_candidate(
    live: Mapping[str, Any], candidate: Mapping[str, Any]
) -> None:
    exact_fields = {
        "asset_id": candidate["assetId"],
        "variant_id": candidate["variantId"],
        "content_sha256": candidate["contentSha256"],
        "source_version_sha256": candidate["sourceVersionSha256"],
        "source_path": candidate["sourcePath"],
    }
    for field, expected in exact_fields.items():
        actual: Any = live.get(field)
        if field in {"asset_id", "variant_id"}:
            actual = int(actual or 0)
        else:
            actual = str(actual or "")
            expected = str(expected)
        if actual != expected:
            raise QuarantineError(
                f"family_candidate_drift:{candidate['variantId']}:{field}"
            )
    policy = classify_source(
        str(live["source_path"]),
        tcg_code=TCG_CODE,
        width_px=live.get("width_px"),
        height_px=live.get("height_px"),
    )
    if (
        policy.get("family") != FAMILY
        or policy.get("status") != "reject"
        or policy.get("reason") != REASON
    ):
        raise QuarantineError(
            f"family_policy_drift:{candidate['variantId']}:{candidate['assetId']}"
        )


def _assert_qc_readback(row: Mapping[str, Any] | None, asset_id: int) -> None:
    if row is None:
        raise QuarantineError(f"quarantine_qc_readback_missing:{asset_id}")
    expected = {
        "semantic_match_status": SEMANTIC_STATUS,
        "card_number_match": 0,
        "language_match": 0,
        "tcg_match": 0,
        "raw_front_confirmed": 0,
        "public_allowed": 0,
        "rejection_reason": REASON,
        "qc_version": QC_VERSION,
    }
    for field, value in expected.items():
        actual = row.get(field)
        if field.endswith("_match") or field in {
            "raw_front_confirmed",
            "public_allowed",
        }:
            actual = int(actual or 0)
        if actual != value:
            raise QuarantineError(f"quarantine_qc_readback_drift:{asset_id}:{field}")


def apply_candidate(cursor: Any, candidate: Mapping[str, Any]) -> dict[str, Any]:
    live = _locked_current_row(cursor, candidate)
    _assert_live_candidate(live, candidate)
    cursor.execute(
        """
        SELECT semantic_match_status,card_number_match,language_match,tcg_match,
               raw_front_confirmed,public_allowed,rejection_reason,qc_version
        FROM market_image_qc
        WHERE image_asset_id=%s AND qc_version=%s
        FOR UPDATE
        """,
        (candidate["assetId"], QC_VERSION),
    )
    existing = cursor.fetchone()
    qc_inserted = False
    if existing is None:
        cursor.execute(
            """
            INSERT INTO market_image_qc
              (image_asset_id,semantic_match_status,card_number_match,language_match,
               tcg_match,raw_front_confirmed,public_allowed,rejection_reason,
               checked_at,qc_version)
            VALUES (%s,%s,0,0,0,0,0,%s,UTC_TIMESTAMP(6),%s)
            """,
            (candidate["assetId"], SEMANTIC_STATUS, REASON, QC_VERSION),
        )
        qc_inserted = int(cursor.rowcount or 0) == 1
    else:
        _assert_qc_readback(existing, int(candidate["assetId"]))

    pointer_disabled = False
    if int(bool(live.get("pointer_public_allowed"))):
        cursor.execute(
            """
            UPDATE market_image_source_pointer
            SET public_allowed=0
            WHERE variant_id=%s
              AND image_kind='raw_front'
              AND source_version_sha256=%s
              AND source_path=%s
              AND public_allowed<>0
            """,
            (
                candidate["variantId"],
                candidate["sourceVersionSha256"],
                candidate["sourcePath"],
            ),
        )
        if int(cursor.rowcount or 0) != 1:
            raise QuarantineError(
                f"exact_source_pointer_update_failed:{candidate['variantId']}"
            )
        pointer_disabled = True

    cursor.execute(
        """
        SELECT
            qc.semantic_match_status,qc.card_number_match,qc.language_match,
            qc.tcg_match,qc.raw_front_confirmed,qc.public_allowed,
            qc.rejection_reason,qc.qc_version,
            pointer.public_allowed AS pointer_public_allowed
        FROM market_image_qc AS qc
        JOIN market_image_asset AS asset ON asset.id=qc.image_asset_id
        JOIN market_image_source_pointer AS pointer
          ON pointer.variant_id=asset.variant_id
         AND pointer.image_kind=asset.image_kind
         AND pointer.source_version_sha256=asset.source_version_sha256
         AND pointer.source_path=%s
        WHERE qc.image_asset_id=%s AND qc.qc_version=%s
        """,
        (candidate["sourcePath"], candidate["assetId"], QC_VERSION),
    )
    readback = cursor.fetchone()
    _assert_qc_readback(readback, int(candidate["assetId"]))
    if int((readback or {}).get("pointer_public_allowed") or 0) != 0:
        raise QuarantineError(
            f"exact_source_pointer_readback_failed:{candidate['variantId']}"
        )
    return {
        "variantId": candidate["variantId"],
        "assetId": candidate["assetId"],
        "contentSha256": candidate["contentSha256"],
        "qcInserted": qc_inserted,
        "pointerDisabled": pointer_disabled,
        "noOp": not qc_inserted and not pointer_disabled,
    }


def assert_write_schema(cursor: Any) -> None:
    cursor.execute("SELECT DATABASE() AS database_name")
    row = cursor.fetchone() or {}
    if str(row.get("database_name") or "") != "cardz_market_cap":
        raise QuarantineError("quarantine_refuses_noncanonical_database")
    cursor.execute(
        """
        SELECT CHARACTER_MAXIMUM_LENGTH AS max_length
        FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA=DATABASE()
          AND TABLE_NAME='market_image_qc'
          AND COLUMN_NAME='qc_version'
        """
    )
    column = cursor.fetchone()
    if column is None or int(column.get("max_length") or 0) < len(QC_VERSION):
        raise QuarantineError(
            f"qc_version_column_too_short:required_{len(QC_VERSION)}"
        )


def apply_plan(connection: Any, plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    try:
        connection.begin()
        with connection.cursor() as cursor:
            assert_write_schema(cursor)
            results = [
                apply_candidate(cursor, candidate)
                for candidate in plan["candidates"]
            ]
            if len(results) != EXPECTED_COUNT:
                raise QuarantineError("quarantine_write_count_drift")
        connection.commit()
        return results
    except Exception:
        connection.rollback()
        raise


def _receipt_document(
    plan: Mapping[str, Any],
    *,
    mode: str,
    run_id: str,
    transaction: str,
    results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "action": "image-source-family-quarantine",
        "runId": run_id,
        "generatedAt": utc_text(),
        "mode": mode,
        "transaction": transaction,
        "database": "cardz_market_cap",
        "family": FAMILY,
        "tcg": TCG_CODE,
        "reason": REASON,
        "qcVersion": QC_VERSION,
        "expected": EXPECTED_COUNT,
        "matched": plan["matched"],
        "releaseCohortOverlap": plan["releaseCohortOverlap"],
        "outerNonRelease": plan["outerNonRelease"],
        "planSha256": plan["planSha256"],
        "qcInserted": sum(bool(row.get("qcInserted")) for row in results),
        "pointersDisabled": sum(
            bool(row.get("pointerDisabled")) for row in results
        ),
        "noOp": sum(bool(row.get("noOp")) for row in results),
        "assets": [
            {
                "variantId": row["variantId"],
                "assetId": row["assetId"],
                "contentSha256": row["contentSha256"],
            }
            for row in plan["candidates"]
        ],
    }


def write_receipt(document: Mapping[str, Any], root: Path) -> Path:
    run_id = str(document["runId"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", run_id):
        raise QuarantineError("run_id_invalid")
    destination = root.resolve() / run_id / "receipt.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    if destination.is_file():
        if destination.read_bytes() != payload:
            raise QuarantineError(f"immutable_receipt_collision:{destination}")
        return destination
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, destination)
    return destination


def record_failure_events(
    plan: Mapping[str, Any],
    *,
    run_id: str,
    receipt_path: Path,
    ledger_root: Path,
) -> int:
    existing = current_failures(
        ledger_root=ledger_root,
        source=SOURCE,
        script=Path(__file__),
        stage=STAGE,
    )
    existing_keys = {str(row.get("itemKey") or "") for row in existing}
    written = 0
    batch_key = f"family:{FAMILY}:plan:{plan['planSha256']}"
    if batch_key not in existing_keys:
        event = record_failure(
            source=SOURCE,
            stage=STAGE,
            script=Path(__file__),
            item_key=batch_key,
            reason_code=REASON,
            message=(
                "Known SAMPLE source family quarantined as one batch; "
                "replace all image sources"
            ),
            retryable=True,
            run_id=run_id,
            context={
                "family": FAMILY,
                "matched": plan["matched"],
                "releaseCohortOverlap": plan["releaseCohortOverlap"],
                "outerNonRelease": plan["outerNonRelease"],
                "planSha256": plan["planSha256"],
            },
            evidence_paths=[receipt_path],
            next_action="replace_image_source",
            ledger_root=ledger_root,
        )
        written += event is not None
    for candidate in plan["candidates"]:
        item_key = (
            f"variant:{candidate['variantId']}:asset:{candidate['assetId']}:"
            f"{candidate['contentSha256']}"
        )
        if item_key in existing_keys:
            continue
        event = record_failure(
            source=SOURCE,
            stage=STAGE,
            script=Path(__file__),
            item_key=item_key,
            reason_code=REASON,
            message="Known SAMPLE source family asset quarantined; replace the image source",
            retryable=True,
            run_id=run_id,
            context={
                "variantId": candidate["variantId"],
                "assetId": candidate["assetId"],
                "contentSha256": candidate["contentSha256"],
                "sourceVersionSha256": candidate["sourceVersionSha256"],
                "family": FAMILY,
            },
            evidence_paths=[receipt_path],
            next_action="replace_image_source",
            ledger_root=ledger_root,
        )
        written += event is not None
    return written


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.family != FAMILY:
        raise QuarantineError("unsupported_source_family")
    load_backend_env()
    connection = db_runtime.connection_from_args(args)
    try:
        rows = fetch_latest_one_piece_raw_fronts(connection)
        release_variant_ids = load_release_cohort(
            args.release_cohort,
            args.release_cohort_sha256,
        )
        plan = build_plan(rows, release_variant_ids)
        if args.write:
            results = apply_plan(connection, plan)
            transaction = "committed"
            mode = "write"
        else:
            connection.rollback()
            results = []
            transaction = "rolled_back"
            mode = "dry_run"
    finally:
        connection.close()

    run_id = args.run_id or (
        f"source_family_quarantine_{plan['planSha256'][:12]}_"
        f"{mode}_{utc_run_suffix()}"
    )
    receipt = _receipt_document(
        plan,
        mode=mode,
        run_id=run_id,
        transaction=transaction,
        results=results,
    )
    receipt_path = write_receipt(receipt, args.receipt_root)
    failure_events = (
        record_failure_events(
            plan,
            run_id=run_id,
            receipt_path=receipt_path,
            ledger_root=args.failure_ledger_root,
        )
        if args.write
        else 0
    )
    return {
        "status": "quarantined" if args.write else "candidate_ready",
        "mode": mode,
        "family": FAMILY,
        "expected": EXPECTED_COUNT,
        "matched": plan["matched"],
        "releaseCohortOverlap": plan["releaseCohortOverlap"],
        "outerNonRelease": plan["outerNonRelease"],
        "planSha256": plan["planSha256"],
        "transaction": transaction,
        "qcInserted": sum(bool(row.get("qcInserted")) for row in results),
        "pointersDisabled": sum(
            bool(row.get("pointerDisabled")) for row in results
        ),
        "noOp": sum(bool(row.get("noOp")) for row in results),
        "failureEventsWritten": failure_events,
        "receipt": str(receipt_path),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=[FAMILY], default=FAMILY)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--release-cohort",
        type=Path,
        default=RELEASE_COHORT_PATH,
    )
    parser.add_argument(
        "--release-cohort-sha256",
        default=RELEASE_COHORT_SHA256,
    )
    parser.add_argument("--receipt-root", type=Path, default=DEFAULT_RECEIPT_ROOT)
    parser.add_argument(
        "--failure-ledger-root",
        type=Path,
        default=DEFAULT_FAILURE_ROOT,
    )
    parser.add_argument("--write", action="store_true")
    db_runtime.add_connection_args(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, QuarantineError, RuntimeError) as error:
        print(
            json.dumps(
                {"status": "rejected", "error": str(error)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
