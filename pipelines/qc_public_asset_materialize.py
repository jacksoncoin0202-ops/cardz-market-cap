#!/usr/bin/env python3
"""Materialise only already-passed canonical-QC card assets.

This is deliberately a narrow file writer: it never changes MySQL, QC,
pointers, or public snapshots.  Dry-run is the default and a write is
content-addressed, idempotent, and fails closed on any mismatch.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from PIL import Image, ImageChops, ImageStat

PIPELINES = Path(__file__).resolve().parent
ROOT = PIPELINES.parent
if str(PIPELINES) not in sys.path:
    sys.path.insert(0, str(PIPELINES))

import g10_public_snapshot as g10  # noqa: E402
from canonical_db_qc import (  # noqa: E402
    CANONICAL_DATABASE,
    SHA256_RE,
    begin_read_only_snapshot,
    connection_values,
    resolve_asset_path,
    sha256_file,
)
from image_geometry_qc import inspect_path  # noqa: E402
from image_source_qc import classify_content_sha256, classify_source  # noqa: E402
from universe_authority import _printing_row_identity, connect_from_values  # noqa: E402

DEFAULT_ASSETS = ROOT / "data" / "public" / "market-assets"
DEFAULT_OUTPUT = ROOT / "data" / "runtime" / "private-reports" / "qc-public-asset-materialize"


def stable_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def passed_targets(report: Mapping[str, Any]) -> dict[int, dict[str, Any]]:
    database = report.get("database")
    counts = report.get("counts")
    cards = report.get("cards")
    if (
        report.get("schemaVersion") != 1
        or report.get("readOnly") is not True
        or not isinstance(database, Mapping)
        or database.get("authority") != "canonical_mysql"
        or database.get("name") != CANONICAL_DATABASE
        or not isinstance(counts, Mapping)
        or not isinstance(cards, list)
        or counts.get("catalog") != len(cards)
    ):
        raise ValueError("canonical_db_qc_report_invalid")
    targets: dict[int, dict[str, Any]] = {}
    passed_qualified = 0
    for row in cards:
        if not isinstance(row, Mapping):
            raise ValueError("canonical_db_qc_card_invalid")
        if not isinstance(row, Mapping) or row.get("decision") != "passed":
            continue
        value = row.get("variantId")
        facts = row.get("facts")
        image = facts.get("image") if isinstance(facts, Mapping) else None
        identity = facts.get("identity") if isinstance(facts, Mapping) else None
        if (
            row.get("segment") != "qualified"
            or not isinstance(value, int) or value <= 0 or value in targets
            or not isinstance(image, Mapping) or not isinstance(identity, Mapping)
            or identity.get("variantId") != value
            or not isinstance(image.get("assetId"), int) or image["assetId"] <= 0
            or not SHA256_RE.fullmatch(str(image.get("contentSha256") or ""))
            or not SHA256_RE.fullmatch(str(identity.get("printingSha256") or ""))
            or not isinstance(identity.get("cardLanguage"), str)
            or not str(identity["cardLanguage"]).strip()
        ):
            raise ValueError("canonical_db_qc_passed_variant_invalid")
        targets[value] = {
            "assetId": int(image["assetId"]),
            "contentSha256": str(image["contentSha256"]).casefold(),
            "printingSha256": str(identity["printingSha256"]).casefold(),
            "cardLanguage": str(identity["cardLanguage"]).strip().casefold(),
        }
        passed_qualified += 1
    if counts.get("releaseReadyQualified") != passed_qualified:
        raise ValueError("canonical_db_qc_ready_count_mismatch")
    return targets


def _fetch_rows(connection: Any, variant_ids: Sequence[int]) -> list[dict[str, Any]]:
    if not variant_ids:
        return []
    marks = ",".join(["%s"] * len(variant_ids))
    sql = f"""
        SELECT v.id AS variant_id, v.tcg_code, v.card_language, v.set_name,
               v.collector_number,
               p.tcg_code AS printing_tcg_code, p.card_language AS printing_card_language,
               p.set_name AS printing_set_name, p.collector_number AS printing_collector_number,
               p.edition_code, p.parallel_code, p.finish_code, p.canonical_printing_sha256,
               p.identity_status AS printing_identity_status,
               (SELECT COUNT(*) FROM catalog_printing_identity ph
                 WHERE ph.canonical_printing_sha256 = p.canonical_printing_sha256) AS printing_hash_count,
               (SELECT COUNT(*) FROM catalog_printing_identity pt
                 WHERE LOWER(TRIM(pt.tcg_code)) = LOWER(TRIM(p.tcg_code))
                   AND LOWER(TRIM(pt.card_language)) = LOWER(TRIM(p.card_language))
                   AND LOWER(TRIM(pt.set_name)) = LOWER(TRIM(p.set_name))
                   AND LOWER(TRIM(pt.collector_number)) = LOWER(TRIM(p.collector_number))
                   AND LOWER(TRIM(pt.edition_code)) = LOWER(TRIM(p.edition_code))
                   AND LOWER(TRIM(pt.parallel_code)) = LOWER(TRIM(p.parallel_code))
                   AND LOWER(TRIM(pt.finish_code)) = LOWER(TRIM(p.finish_code))) AS printing_tuple_count,
               a.id AS asset_id, a.image_kind, a.content_sha256, a.private_object_key,
               a.width_px, a.height_px, a.source_version_sha256,
               q.id AS qc_id, q.semantic_match_status, q.card_number_match,
               q.language_match, q.tcg_match, q.raw_front_confirmed, q.public_allowed,
               q.checked_at, q.qc_version,
               pointer.source_path, pointer.public_allowed AS pointer_public_allowed
        FROM catalog_variant v
        JOIN catalog_printing_identity p ON p.variant_id = v.id
        JOIN market_image_asset a ON a.variant_id = v.id AND a.image_kind = 'raw_front'
        JOIN market_image_qc q ON q.id = (
          SELECT latest_qc.id FROM market_image_qc latest_qc
          WHERE latest_qc.image_asset_id = a.id
          ORDER BY latest_qc.checked_at DESC, latest_qc.id DESC LIMIT 1
        )
        JOIN market_image_source_pointer pointer ON pointer.variant_id = v.id
          AND pointer.image_kind = a.image_kind
          AND pointer.source_version_sha256 = a.source_version_sha256
        WHERE v.id IN ({marks})
        ORDER BY v.id, q.checked_at DESC, q.id DESC, a.id DESC
    """
    with connection.cursor() as cursor:
        cursor.execute(sql, list(variant_ids))
        return [dict(row) for row in cursor.fetchall()]


def row_rejection(
    row: Mapping[str, Any], target: Mapping[str, Any], source_assets: Path
) -> tuple[Path | None, str | None]:
    if int(row.get("asset_id") or 0) != target["assetId"]:
        return None, "report_asset_id_mismatch"
    if str(row.get("content_sha256") or "").casefold() != target["contentSha256"]:
        return None, "report_content_hash_mismatch"
    if str(row.get("canonical_printing_sha256") or "").casefold() != target["printingSha256"]:
        return None, "report_printing_hash_mismatch"
    if (
        str(row.get("card_language") or "").strip().casefold() != target["cardLanguage"]
        or str(row.get("printing_card_language") or "").strip().casefold()
        != target["cardLanguage"]
    ):
        return None, "report_language_mismatch"
    identity = _printing_row_identity(row)
    if identity is None:
        return None, "printing_identity_invalid"
    if str(row.get("image_kind") or "") != "raw_front":
        return None, "image_kind_invalid"
    digest = str(row.get("content_sha256") or "").casefold()
    if not SHA256_RE.fullmatch(digest):
        return None, "content_hash_invalid"
    if classify_content_sha256(digest).get("status") == "reject":
        return None, "content_policy_rejected"
    required = (
        row.get("semantic_match_status") == "human_or_vision_confirmed",
        bool(row.get("card_number_match")), bool(row.get("language_match")),
        bool(row.get("tcg_match")), bool(row.get("raw_front_confirmed")),
        bool(row.get("public_allowed")), bool(row.get("pointer_public_allowed")),
    )
    if not all(required):
        return None, "db_qc_or_pointer_not_public"
    source = classify_source(str(row.get("source_path") or ""), tcg_code=str(row.get("tcg_code") or ""), width_px=row.get("width_px"), height_px=row.get("height_px"))
    if source.get("status") == "reject":
        return None, "source_policy_rejected"
    path = resolve_asset_path(row, source_assets)
    if path is None or not path.is_file():
        return None, "master_missing"
    if sha256_file(path) != digest:
        return None, "master_hash_mismatch"
    try:
        if inspect_path(path).get("status") != "passed":
            return None, "master_geometry_invalid"
    except (OSError, ValueError):
        return None, "master_decode_failed"
    return path, None


def build_plan(connection: Any, report: Mapping[str, Any], source_assets: Path) -> dict[str, Any]:
    targets = passed_targets(report)
    target_ids = sorted(targets)
    rows = _fetch_rows(connection, target_ids)
    by_variant: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        by_variant.setdefault(int(row["variant_id"]), []).append(row)
    items: list[dict[str, Any]] = []
    for variant_id in target_ids:
        chosen: dict[str, Any] | None = None
        reasons: list[str] = []
        for row in by_variant.get(variant_id, []):
            source, reason = row_rejection(row, targets[variant_id], source_assets)
            if reason is None and source is not None:
                chosen = {"variantId": variant_id, "assetId": int(row["asset_id"]), "contentSha256": str(row["content_sha256"]), "source": str(source)}
                break
            if reason:
                reasons.append(reason)
        items.append(chosen or {"variantId": variant_id, "status": "blocked", "reasons": sorted(set(reasons or ["no_matching_db_asset"]))})
    return {"schemaVersion": 1, "type": "qc-public-asset-materialize-plan/v1", "canonicalDbQcRunId": report.get("runId"), "reportSha256": sha256_bytes(stable_bytes(report)), "targetPassedVariants": len(target_ids), "items": items}


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _equivalent_derivative(path: Path, blob: bytes, expected_size: tuple[int, int]) -> bool:
    """Accept an older WebP encoding only when decoded pixels remain near-identical."""

    try:
        with Image.open(path) as existing:
            existing.load()
            if existing.format != "WEBP" or existing.size != expected_size:
                return False
            left = existing.convert("RGBA")
        with Image.open(io.BytesIO(blob)) as generated:
            generated.load()
            if generated.format != "WEBP" or generated.size != expected_size:
                return False
            right = generated.convert("RGBA")
    except (OSError, ValueError):
        return False
    statistics = ImageStat.Stat(ImageChops.difference(left, right))
    return max(statistics.mean) <= 3.0 and max(statistics.rms) <= 12.0


def materialize(plan: Mapping[str, Any], assets: Path) -> dict[str, int]:
    written = noop = blocked = 0
    for item in plan["items"]:
        if not isinstance(item, Mapping) or not item.get("contentSha256"):
            blocked += 1; continue
        digest = str(item["contentSha256"])
        source = Path(str(item["source"]))
        target = assets / f"{digest}.webp"
        master_exists = target.exists()
        if target.exists():
            if sha256_file(target) != digest:
                raise ValueError(f"existing_master_conflict:{digest}")
        # Verify again immediately before writing, then encode exact public variants.
        raw = source.read_bytes()
        if sha256_bytes(raw) != digest:
            raise ValueError(f"source_master_drift:{digest}")
        with Image.open(io.BytesIO(raw)) as opened:
            image = opened.copy(); image.load()
        if inspect_path(source).get("status") != "passed":
            raise ValueError(f"source_geometry_drift:{digest}")
        derivatives = g10.encode_derivatives(image)
        expected = {"200": (200, 280), "600": (429, 600)}
        encoded: dict[Path, bytes] = {target: raw}
        for suffix, blob in derivatives.items():
            with Image.open(io.BytesIO(blob)) as decoded:
                decoded.load()
                if decoded.format != "WEBP" or decoded.size != expected[suffix]:
                    raise ValueError(f"derivative_invalid:{digest}:{suffix}")
            encoded[assets / f"{digest}_{suffix}.webp"] = blob
        # Preflight every destination before the first mutation.
        for destination, blob in encoded.items():
            if not destination.exists() or destination.read_bytes() == blob:
                continue
            if destination == target:
                raise ValueError(f"existing_asset_conflict:{destination.name}")
            suffix = destination.stem.rsplit("_", 1)[-1]
            if suffix not in expected or not _equivalent_derivative(
                destination, blob, expected[suffix]
            ):
                raise ValueError(f"existing_asset_conflict:{destination.name}")
        if master_exists and all(destination.exists() for destination in encoded):
            noop += 1
            continue
        for destination, blob in encoded.items():
            if not destination.exists(): _atomic_write(destination, blob)
        written += 1
    return {"written": written, "noOp": noop, "blocked": blocked}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--report", type=Path, required=True)
    value.add_argument("--source-assets", type=Path, default=DEFAULT_ASSETS)
    value.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    value.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT)
    value.add_argument("--write", action="store_true")
    value.add_argument("--config", type=Path, default=ROOT / "data/runtime/config/backend.env")
    value.add_argument("--host"); value.add_argument("--port", type=int); value.add_argument("--database"); value.add_argument("--user"); value.add_argument("--password")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    values = connection_values(args)
    connection = connect_from_values(values, read_only=True)
    try:
        begin_read_only_snapshot(connection)
        plan = build_plan(connection, report, args.source_assets)
    finally:
        connection.close()
    plan_bytes = stable_bytes(plan)
    receipt: dict[str, Any] = {"schemaVersion": 1, "type": "qc-public-asset-materialize-receipt/v1", "dryRun": not args.write, "planSha256": sha256_bytes(plan_bytes), "result": {"written": 0, "noOp": 0, "blocked": sum(1 for item in plan["items"] if "contentSha256" not in item)}}
    if args.write:
        receipt["dryRun"] = False
        receipt["result"] = materialize(plan, args.assets)
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "plan.json").write_bytes(plan_bytes)
    (args.output_root / "receipt.json").write_bytes(stable_bytes(receipt))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
