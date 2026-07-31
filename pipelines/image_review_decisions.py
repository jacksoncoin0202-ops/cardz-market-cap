#!/usr/bin/env python3
"""Validate pasted image-review code and CAS-bind approved assets to MySQL."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pymysql
from PIL import Image

try:
    from .card_identity import printing_key7, printing_key7_sha256
    from .canonical_db_qc import ROOT, resolve_asset_path
    from .failure_ledger import DEFAULT_LEDGER_ROOT, read_events, record_failure
    from .image_geometry_qc import inspect_path
    from .image_review_proxy import canonical_bytes
    from .image_source_qc import classify_content_sha256, classify_source
except ImportError:  # direct script execution
    from card_identity import printing_key7, printing_key7_sha256
    from canonical_db_qc import ROOT, resolve_asset_path
    from failure_ledger import DEFAULT_LEDGER_ROOT, read_events, record_failure
    from image_geometry_qc import inspect_path
    from image_review_proxy import canonical_bytes
    from image_source_qc import classify_content_sha256, classify_source


PREFIX = "CARDZ-IMG-QC1:"
QC_VERSION = "human-review-v2"
OUTPUT_ROOT = ROOT / "data/runtime/private-reports/image-review-bind"
BLACKLISTED_SOURCE_FAMILIES = {"limitless-one-piece-en"}
_HUMAN_REJECTED_ITEM_KEY = re.compile(
    r"^variant:(?P<variant_id>\d+):asset:\d+:(?P<content_sha256>[0-9a-f]{64})$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def dataset_hash(document: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in document.items()
        if key not in {"assetPaths", "datasetSha256"}
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def decode_code(value: str) -> dict[str, Any]:
    text = "".join(value.split())
    if not text.startswith(PREFIX):
        raise ValueError("image_review_code_prefix_invalid")
    encoded = text[len(PREFIX) :]
    encoded += "=" * (-len(encoded) % 4)
    try:
        raw = base64.urlsafe_b64decode(encoded.encode("ascii"))
        payload = json.loads(raw.decode("utf-8"))
    except Exception as error:
        raise ValueError("image_review_code_payload_invalid") from error
    if payload.get("v") != 2 or not isinstance(payload.get("i"), list):
        raise ValueError("image_review_code_schema_invalid")
    return payload


def historical_human_rejections(
    events: Iterable[Mapping[str, Any]],
) -> set[tuple[int, str]]:
    """Return immutable variant/hash rejections from the append-only ledger."""

    rejected: set[tuple[int, str]] = set()
    for event in events:
        if (
            str(event.get("source") or "") != "human_image_review"
            or str(event.get("reasonCode") or "") != "human_review_rejected"
        ):
            continue
        match = _HUMAN_REJECTED_ITEM_KEY.fullmatch(str(event.get("itemKey") or ""))
        if match:
            rejected.add((int(match.group("variant_id")), match.group("content_sha256")))
    return rejected


def assert_ok_allowed(
    item: Mapping[str, Any],
    *,
    source_policy: Mapping[str, Any],
    historical_rejections: set[tuple[int, str]],
) -> None:
    """Fail closed for a rejected byte-for-byte image or known bad family."""

    if item.get("decision") != "ok":
        return
    key = (int(item["variantId"]), str(item["contentSha256"]))
    if key in historical_rejections:
        raise RuntimeError("image_review_content_previously_human_rejected")
    if classify_content_sha256(item["contentSha256"])["status"] == "reject":
        raise RuntimeError("image_review_content_policy_rejected")
    if str(source_policy.get("family") or "") in BLACKLISTED_SOURCE_FAMILIES:
        raise RuntimeError("image_review_source_family_historically_rejected")
    if source_policy.get("status") == "reject":
        raise RuntimeError(f"image_review_source_now_rejected:{item['assetId']}")


def assert_public_derivatives_ready(content_sha256: str, assets_root: Path) -> None:
    """A public DB flag needs the exact responsive asset set on disk first."""

    expected = {"200": (200, 280), "600": (429, 600)}
    for suffix, size in expected.items():
        path = assets_root / f"{content_sha256}_{suffix}.webp"
        if not path.is_file():
            raise RuntimeError(f"image_review_public_derivative_missing:{suffix}")
        with Image.open(path) as image:
            image.load()
            if image.format != "WEBP" or image.size != size:
                raise RuntimeError(f"image_review_public_derivative_invalid:{suffix}")


def assert_live_printing_identity(
    row: Mapping[str, Any],
    *,
    expected_printing_sha256: str,
    expected_language: str,
) -> None:
    """Recompute the seven-part identity instead of trusting a stored hash."""

    if str(row.get("printing_identity_status") or "") != "canonical":
        raise RuntimeError("image_review_live_printing_not_canonical")
    fields = (
        row.get("printing_tcg_code"),
        row.get("printing_card_language"),
        row.get("printing_set_name"),
        row.get("printing_collector_number"),
        row.get("printing_edition_code"),
        row.get("printing_parallel_code"),
        row.get("printing_finish_code"),
    )
    key = printing_key7(*fields)
    if any(not part for part in key):
        raise RuntimeError("image_review_canonical_printing_incomplete")
    if (
        key[0] != str(row.get("tcg_code") or "").strip().casefold()
        or key[1] != str(row.get("card_language") or "").strip()
        or key[2] != str(row.get("variant_set_name") or "").strip().casefold()
        or key[3] != str(row.get("variant_collector_number") or "").strip().casefold()
    ):
        raise RuntimeError("image_review_canonical_printing_base_mismatch")
    recomputed = printing_key7_sha256(key)
    stored = str(row.get("canonical_printing_sha256") or "").strip().casefold()
    if stored != recomputed:
        raise RuntimeError("image_review_canonical_printing_hash_invalid")
    if stored != expected_printing_sha256:
        raise RuntimeError("image_review_canonical_printing_drift")
    if key[1] != expected_language:
        raise RuntimeError("image_review_language_evidence_drift")


def decision_plan(
    code: str,
    dataset: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_hash = dataset_hash(dataset)
    if str(dataset.get("datasetSha256") or "") != expected_hash:
        raise ValueError("image_review_dataset_hash_invalid")
    payload = decode_code(code)
    if payload.get("d") != expected_hash:
        raise ValueError("image_review_code_dataset_mismatch")
    cards = {
        (int(card["variantId"]), int(card["assetId"])): card
        for card in dataset.get("cards") or []
    }
    plan: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    reject_reasons = {
        "sample",
        "wrong_card",
        "language_mismatch",
        "wrong_printing",
        "slab_or_label",
        "crop_or_size",
        "duplicate_or_placeholder",
        "other",
    }
    sample_positions = {
        "",
        "center",
        "diagonal_center",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
        "whole_face",
        "unknown",
    }
    for item in payload["i"]:
        if not isinstance(item, list) or not 3 <= len(item) <= 6:
            raise ValueError("image_review_decision_invalid")
        variant_id, asset_id, raw_decision = int(item[0]), int(item[1]), item[2]
        key = (variant_id, asset_id)
        if key in seen:
            raise ValueError("image_review_decision_duplicate")
        seen.add(key)
        card = cards.get(key)
        if card is None:
            raise ValueError("image_review_decision_not_in_dataset")
        if raw_decision not in {"o", "r"}:
            raise ValueError("image_review_decision_value_invalid")
        expected_printing = ""
        expected_language = ""
        source_language = ""
        if raw_decision == "o":
            if len(item) != 6:
                raise ValueError("image_review_ok_printing_language_evidence_required")
            expected_printing = str(item[3] or "").strip().casefold()
            expected_language = str(item[4] or "").strip().casefold()
            source_language = str(item[5] or "").strip().casefold()
            if not _SHA256.fullmatch(expected_printing):
                raise ValueError("image_review_expected_printing_sha_invalid")
            if not expected_language or not source_language:
                raise ValueError("image_review_expected_language_required")
            if source_language != expected_language:
                raise ValueError("image_review_language_evidence_mismatch")
            dataset_source_language = str(
                card.get("sourceLanguage") or ""
            ).strip().casefold()
            if (
                expected_printing != str(card.get("canonicalPrintingSha256") or "").casefold()
                or expected_language != str(card.get("expectedLanguage") or "").casefold()
                or (
                    dataset_source_language
                    and source_language != dataset_source_language
                )
            ):
                raise ValueError("image_review_immutable_evidence_dataset_mismatch")
            reason = position = note = ""
        else:
            reason = str(item[3] if len(item) > 3 else "").strip()
            position = str(item[4] if len(item) > 4 else "").strip()
            note = str(item[5] if len(item) > 5 else "").strip()
        if raw_decision == "r" and reason not in reject_reasons:
            raise ValueError("image_review_reject_reason_invalid")
        if raw_decision == "o" and (reason or position or note):
            raise ValueError("image_review_ok_metadata_invalid")
        if raw_decision == "o" and str(card.get("sourceFamily") or "") in BLACKLISTED_SOURCE_FAMILIES:
            raise ValueError("image_review_source_family_historically_rejected")
        if (
            raw_decision == "o"
            and classify_content_sha256(card.get("contentSha256"))["status"]
            == "reject"
        ):
            raise ValueError("image_review_content_policy_rejected")
        if position not in sample_positions:
            raise ValueError("image_review_sample_position_invalid")
        if reason != "sample" and position:
            raise ValueError("image_review_non_sample_position_invalid")
        if len(note) > 200:
            raise ValueError("image_review_note_too_long")
        plan.append(
            {
                "variantId": variant_id,
                "assetId": asset_id,
                "contentSha256": card["contentSha256"],
                "decision": "ok" if raw_decision == "o" else "reject",
                "cardId": card["cardId"],
                "name": card["name"],
                "reason": reason or None,
                "samplePosition": position or None,
                "note": note or None,
                "expectedCanonicalPrintingSha256": expected_printing or None,
                "expectedLanguage": expected_language or None,
                "sourceLanguageEvidence": source_language or None,
            }
        )
    return plan


def load_db_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def connect() -> pymysql.Connection:
    load_db_env()
    return pymysql.connect(
        host="127.0.0.1",
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=os.environ.get("CARDZ_DB_PASSWORD", ""),
        database="cardz_market_cap",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def validate_live_asset(
    cursor: pymysql.cursors.Cursor,
    item: Mapping[str, Any],
    *,
    asset_path: Path,
    historical_rejections: set[tuple[int, str]],
) -> dict[str, Any]:
    cursor.execute(
        """
        SELECT
            asset.id AS asset_id,
            asset.variant_id,
            asset.image_kind,
            asset.content_sha256,
            asset.private_object_key,
            asset.width_px,
            asset.height_px,
            asset.source_version_sha256,
            variant.tcg_code,
            variant.card_language,
            variant.set_name AS variant_set_name,
            variant.collector_number AS variant_collector_number,
            printing.identity_status AS printing_identity_status,
            printing.canonical_printing_sha256,
            printing.tcg_code AS printing_tcg_code,
            printing.card_language AS printing_card_language,
            printing.set_name AS printing_set_name,
            printing.collector_number AS printing_collector_number,
            printing.edition_code AS printing_edition_code,
            printing.parallel_code AS printing_parallel_code,
            printing.finish_code AS printing_finish_code,
            pointer.source_path
        FROM market_image_asset AS asset
        JOIN catalog_variant AS variant ON variant.id=asset.variant_id
        LEFT JOIN catalog_printing_identity AS printing
          ON printing.variant_id=asset.variant_id
        LEFT JOIN market_image_source_pointer AS pointer
          ON pointer.variant_id=asset.variant_id
         AND pointer.image_kind=asset.image_kind
         AND pointer.source_version_sha256=asset.source_version_sha256
        WHERE asset.id=%s
        """,
        (int(item["assetId"]),),
    )
    row = cursor.fetchone()
    if row is None:
        raise RuntimeError(f"image_review_asset_missing:{item['assetId']}")
    if (
        int(row["variant_id"]) != int(item["variantId"])
        or str(row["content_sha256"]) != str(item["contentSha256"])
        or str(row["image_kind"]) != "raw_front"
    ):
        raise RuntimeError(f"image_review_asset_cas_mismatch:{item['assetId']}")
    cursor.execute(
        """
        SELECT id
        FROM market_image_asset
        WHERE variant_id=%s AND image_kind='raw_front'
        ORDER BY captured_at DESC,id DESC
        LIMIT 1
        """,
        (int(item["variantId"]),),
    )
    latest = cursor.fetchone()
    if latest is None or int(latest["id"]) != int(item["assetId"]):
        raise RuntimeError(f"image_review_asset_not_latest:{item['assetId']}")
    resolved = resolve_asset_path(row, asset_path)
    if resolved is None or not resolved.is_file():
        raise RuntimeError(f"image_review_asset_bytes_missing:{item['assetId']}")
    if hashlib.sha256(resolved.read_bytes()).hexdigest() != item["contentSha256"]:
        raise RuntimeError(f"image_review_asset_hash_mismatch:{item['assetId']}")
    geometry = inspect_path(resolved)
    source_policy = classify_source(
        str(row.get("source_path") or ""),
        tcg_code=str(row.get("tcg_code") or ""),
        width_px=row.get("width_px"),
        height_px=row.get("height_px"),
    )
    if item["decision"] == "ok":
        expected_printing = str(item.get("expectedCanonicalPrintingSha256") or "").casefold()
        expected_language = str(item.get("expectedLanguage") or "").casefold()
        if not _SHA256.fullmatch(expected_printing):
            raise RuntimeError("image_review_expected_printing_sha_missing")
        if not expected_language:
            raise RuntimeError("image_review_expected_language_missing")
        assert_live_printing_identity(
            row,
            expected_printing_sha256=expected_printing,
            expected_language=expected_language,
        )
        cursor.execute(
            """
            SELECT 1 FROM market_image_rejection_registry
            WHERE variant_id=%s AND content_sha256=%s
            UNION ALL
            SELECT 1
            FROM market_image_qc AS prior_qc
            JOIN market_image_asset AS prior_asset
              ON prior_asset.id=prior_qc.image_asset_id
            WHERE prior_asset.variant_id=%s
              AND prior_asset.content_sha256=%s
              AND prior_qc.semantic_match_status='human_rejected'
            LIMIT 1
            """,
            (
                int(item["variantId"]),
                str(item["contentSha256"]),
                int(item["variantId"]),
                str(item["contentSha256"]),
            ),
        )
        if cursor.fetchone() is not None:
            raise RuntimeError("image_review_content_previously_human_rejected")
        assert_ok_allowed(
            item,
            source_policy=source_policy,
            historical_rejections=historical_rejections,
        )
    if item["decision"] == "ok" and geometry["status"] != "passed":
        raise RuntimeError(f"image_review_geometry_changed:{item['assetId']}")
    if item["decision"] == "ok":
        assert_public_derivatives_ready(item["contentSha256"], asset_path)
    return {
        **row,
        "resolvedPath": str(resolved),
        "geometry": geometry,
        "sourcePolicy": source_policy,
    }


def write_decision(
    cursor: pymysql.cursors.Cursor,
    item: Mapping[str, Any],
    live: Mapping[str, Any],
    *,
    run_id: str,
    decision_code_sha256: str,
) -> None:
    asset_id = int(item["assetId"])
    variant_id = int(item["variantId"])
    if not run_id or len(run_id) > 191:
        raise RuntimeError("image_review_run_id_invalid")
    if not _SHA256.fullmatch(decision_code_sha256):
        raise RuntimeError("image_review_decision_code_sha_invalid")
    if item["decision"] == "ok":
        source_version_sha256 = str(
            live.get("source_version_sha256") or ""
        ).strip().lower()
        if not _SHA256.fullmatch(source_version_sha256):
            raise RuntimeError(f"image_review_source_version_invalid:{asset_id}")
        source_path = str(
            live.get("source_path") or live.get("private_object_key") or ""
        ).strip()
        if not source_path or len(source_path) > 500:
            raise RuntimeError(f"image_review_source_path_invalid:{asset_id}")
        remote_url_sha256 = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
        content_sha256 = str(live.get("content_sha256") or "").strip().lower()
        canonical_printing_sha256 = str(
            item.get("expectedCanonicalPrintingSha256") or ""
        ).strip().lower()
        expected_language = str(item.get("expectedLanguage") or "").strip().lower()
        if not _SHA256.fullmatch(content_sha256):
            raise RuntimeError(f"image_review_content_sha_invalid:{asset_id}")
        if not _SHA256.fullmatch(canonical_printing_sha256):
            raise RuntimeError(f"image_review_printing_sha_invalid:{asset_id}")
        if not expected_language or len(expected_language) > 8:
            raise RuntimeError(f"image_review_expected_language_invalid:{asset_id}")
        binding_sha256 = hashlib.sha256(
            "|".join(
                (
                    str(asset_id),
                    str(variant_id),
                    content_sha256,
                    source_version_sha256,
                    canonical_printing_sha256,
                    expected_language,
                )
            ).encode("utf-8")
        ).hexdigest()
        cursor.execute(
            """
            UPDATE market_image_qc AS qc
            JOIN market_image_asset AS asset ON asset.id=qc.image_asset_id
            SET qc.public_allowed=0
            WHERE asset.variant_id=%s AND asset.image_kind='raw_front'
              AND asset.id<>%s
            """,
            (variant_id, asset_id),
        )
        cursor.execute(
            """
            UPDATE market_image_source_pointer
            SET public_allowed=0
            WHERE variant_id=%s AND image_kind='raw_front'
            """,
            (variant_id,),
        )
        values = (
            asset_id,
            "human_or_vision_confirmed",
            1,
            1,
            1,
            1,
            1,
            None,
            QC_VERSION,
        )
        cursor.execute(
            """
            INSERT INTO market_image_source_pointer
                (variant_id,image_kind,remote_url_sha256,source_path,
                 source_version_sha256,public_allowed,observed_at)
            VALUES (%s,'raw_front',%s,%s,%s,1,UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                remote_url_sha256=VALUES(remote_url_sha256),
                source_path=VALUES(source_path),
                public_allowed=1,
                observed_at=VALUES(observed_at)
            """,
            (
                variant_id,
                remote_url_sha256,
                source_path,
                source_version_sha256,
            ),
        )
        cursor.execute(
            """
            INSERT INTO market_image_review_approval
                (image_asset_id,variant_id,content_sha256,source_version_sha256,
                 canonical_printing_sha256,expected_language,binding_sha256,
                 decision_code_sha256,review_run_id,reviewed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                binding_sha256=IF(
                    binding_sha256=VALUES(binding_sha256),
                    binding_sha256,
                    NULL
                )
            """,
            (
                asset_id,
                variant_id,
                content_sha256,
                source_version_sha256,
                canonical_printing_sha256,
                expected_language,
                binding_sha256,
                decision_code_sha256,
                run_id,
            ),
        )
    else:
        rejection_reason = "human_review_rejected"
        if item.get("reason"):
            rejection_reason += f":{item['reason']}"
        if item.get("samplePosition"):
            rejection_reason += f":{item['samplePosition']}"
        cursor.execute(
            """
            UPDATE market_image_qc
            SET public_allowed=0
            WHERE image_asset_id=%s
            """,
            (asset_id,),
        )
        values = (
            asset_id,
            "human_rejected",
            0,
            0,
            0,
            0,
            0,
            rejection_reason,
            QC_VERSION,
        )
        cursor.execute(
            """
            UPDATE market_image_source_pointer
            SET public_allowed=0
            WHERE variant_id=%s AND image_kind='raw_front'
              AND source_version_sha256=%s
            """,
            (variant_id, live["source_version_sha256"]),
        )
        cursor.execute(
            """
            INSERT INTO market_image_rejection_registry
                (variant_id,content_sha256,first_image_asset_id,rejection_reason,
                 decision_code_sha256,review_run_id,rejected_at)
            VALUES (%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                content_sha256=VALUES(content_sha256)
            """,
            (
                variant_id,
                str(live["content_sha256"]),
                asset_id,
                rejection_reason,
                decision_code_sha256,
                run_id,
            ),
        )
    cursor.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id,semantic_match_status,card_number_match,
             language_match,tcg_match,raw_front_confirmed,public_allowed,
             rejection_reason,checked_at,qc_version)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,UTC_TIMESTAMP(6),%s)
        ON DUPLICATE KEY UPDATE
            semantic_match_status=VALUES(semantic_match_status),
            card_number_match=VALUES(card_number_match),
            language_match=VALUES(language_match),
            tcg_match=VALUES(tcg_match),
            raw_front_confirmed=VALUES(raw_front_confirmed),
            public_allowed=VALUES(public_allowed),
            rejection_reason=VALUES(rejection_reason),
            checked_at=VALUES(checked_at),
            qc_version=VALUES(qc_version)
        """,
        values,
    )


def sync_historical_rejection_registry(
    cursor: pymysql.cursors.Cursor,
    rejections: Iterable[tuple[int, str]],
    *,
    run_id: str,
) -> None:
    for variant_id, content_sha256 in sorted(set(rejections)):
        if not _SHA256.fullmatch(content_sha256):
            raise RuntimeError("image_review_historical_rejection_sha_invalid")
        decision_sha256 = hashlib.sha256(
            f"human-rejection-ledger|{variant_id}|{content_sha256}".encode("utf-8")
        ).hexdigest()
        cursor.execute(
            """
            INSERT INTO market_image_rejection_registry
                (variant_id,content_sha256,first_image_asset_id,rejection_reason,
                 decision_code_sha256,review_run_id,rejected_at)
            VALUES (%s,%s,NULL,'historical_human_review_ledger',
                    %s,%s,UTC_TIMESTAMP(6))
            ON DUPLICATE KEY UPDATE
                content_sha256=VALUES(content_sha256)
            """,
            (variant_id, content_sha256, decision_sha256, run_id),
        )


def apply_plan(
    plan: Sequence[Mapping[str, Any]],
    *,
    write: bool,
    assets_root: Path,
    run_id: str,
    decision_code_sha256: str,
    failure_ledger_root: Path = DEFAULT_LEDGER_ROOT,
) -> list[dict[str, Any]]:
    connection = connect()
    results: list[dict[str, Any]] = []
    historical_rejections: set[tuple[int, str]] = set()
    if any(item.get("decision") == "ok" for item in plan):
        historical_rejections = historical_human_rejections(
            read_events(ledger_root=failure_ledger_root)
        )
    try:
        with connection.cursor() as cursor:
            if write and historical_rejections:
                sync_historical_rejection_registry(
                    cursor,
                    historical_rejections,
                    run_id=run_id,
                )
            for item in plan:
                live = validate_live_asset(
                    cursor,
                    item,
                    asset_path=assets_root,
                    historical_rejections=historical_rejections,
                )
                if write:
                    write_decision(
                        cursor,
                        item,
                        live,
                        run_id=run_id,
                        decision_code_sha256=decision_code_sha256,
                    )
                results.append(
                    {
                        **dict(item),
                        "validated": True,
                        "sourceFamily": live["sourcePolicy"]["family"],
                        "geometryPolicy": live["geometry"]["policyId"],
                    }
                )
        if write:
            connection.commit()
        else:
            connection.rollback()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--code")
    group.add_argument("--code-file", type=Path)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--assets-root", type=Path, default=ROOT / "data/public/market-assets")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    code = args.code if args.code is not None else args.code_file.read_text(encoding="utf-8")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    plan = decision_plan(code, dataset)
    decision_code_sha256 = hashlib.sha256(code.strip().encode("utf-8")).hexdigest()
    results = apply_plan(
        plan,
        write=args.write,
        assets_root=args.assets_root.resolve(),
        run_id=args.run_id,
        decision_code_sha256=decision_code_sha256,
    )
    summary = {
        "runId": args.run_id,
        "write": args.write,
        "datasetSha256": dataset["datasetSha256"],
        "decisionCodeSha256": decision_code_sha256,
        "count": len(results),
        "ok": sum(row["decision"] == "ok" for row in results),
        "reject": sum(row["decision"] == "reject" for row in results),
        "items": results,
    }
    if args.write:
        destination = OUTPUT_ROOT / args.run_id / "receipt.json"
        destination.parent.mkdir(parents=True, exist_ok=False)
        destination.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        for row in results:
            if row["decision"] == "reject":
                record_failure(
                    source="human_image_review",
                    stage="image_binding",
                    script=Path(__file__),
                    item_key=(
                        f"variant:{row['variantId']}:asset:{row['assetId']}:"
                        f"{row['contentSha256']}"
                    ),
                    reason_code="human_review_rejected",
                    message=(
                        "DADDY marked this candidate image as not usable"
                        f": {row.get('reason') or 'unspecified'}"
                    ),
                    retryable=True,
                    run_id=args.run_id,
                    context={
                        "cardId": row["cardId"],
                        "variantId": row["variantId"],
                        "assetId": row["assetId"],
                        "name": row["name"],
                        "reason": row.get("reason"),
                        "samplePosition": row.get("samplePosition"),
                        "note": row.get("note"),
                    },
                    evidence_paths=[destination],
                    next_action="replace_image_source",
                )
        summary["receipt"] = str(destination)
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
