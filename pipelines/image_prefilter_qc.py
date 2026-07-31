#!/usr/bin/env python3
"""Source -> geometry -> SAMPLE OCR funnel for the frozen image cohort.

This tool is read-only against MySQL.  It writes a private report and optional
failure-ledger events; it never changes image/QC rows or public assets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import pymysql

try:
    from .canonical_db_qc import ROOT, resolve_asset_path
    from .failure_ledger import (
        export_retry_worklist,
        record_failure,
        record_resolution,
    )
    from .image_source_qc import classify_content_sha256, classify_source
    from .sample_image_qc import detect_sample_evidence
except ImportError:  # direct script execution
    from canonical_db_qc import ROOT, resolve_asset_path
    from failure_ledger import (
        export_retry_worklist,
        record_failure,
        record_resolution,
    )
    from image_source_qc import classify_content_sha256, classify_source
    from sample_image_qc import detect_sample_evidence


SCRIPT = Path(__file__)
SOURCE = "local_image_qc"
STAGE = "image_prefilter"
DEFAULT_ASSETS_ROOT = ROOT / "data/public/market-assets"
DEFAULT_OUTPUT_ROOT = ROOT / "data/runtime/private-reports/image-prefilter"


def infer_source_language(source_path: str) -> str | None:
    """Return only high-confidence language markers encoded in the asset path."""
    filename = Path(urlsplit(source_path).path).name.upper()
    marker = re.search(r"[_-](EN|JA|JP)(?=\.[A-Z0-9]+$)", filename)
    if marker and marker.group(1) == "EN":
        return "en"
    if marker:
        return "ja"
    return None


def load_db_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def fetch_assets(asset_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
    if not asset_ids:
        return {}
    load_db_env()
    connection = pymysql.connect(
        host="127.0.0.1",
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=os.environ.get("CARDZ_DB_PASSWORD", ""),
        database="cardz_market_cap",
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )
    rows: list[dict[str, Any]] = []
    try:
        with connection.cursor() as cursor:
            for offset in range(0, len(asset_ids), 500):
                batch = asset_ids[offset : offset + 500]
                marks = ",".join(["%s"] * len(batch))
                cursor.execute(
                    f"""
                    SELECT
                        asset.id AS asset_id,
                        asset.variant_id,
                        asset.content_sha256,
                        asset.private_object_key,
                        asset.width_px,
                        asset.height_px,
                        pointer.source_path
                    FROM market_image_asset AS asset
                    LEFT JOIN market_image_source_pointer AS pointer
                      ON pointer.variant_id = asset.variant_id
                     AND pointer.image_kind = asset.image_kind
                     AND pointer.source_version_sha256 =
                         asset.source_version_sha256
                    WHERE asset.id IN ({marks})
                    """,
                    tuple(batch),
                )
                rows.extend(cursor.fetchall())
    finally:
        connection.close()
    return {int(row["asset_id"]): row for row in rows}


def item_key(card: Mapping[str, Any], asset_id: int | None) -> str:
    return (
        f"variant:{card.get('variantId')}:asset:{asset_id or 'missing'}:"
        f"{card.get('imageSha256') or 'missing'}"
    )


def evaluate_card(
    card: Mapping[str, Any],
    asset: Mapping[str, Any] | None,
    *,
    assets_root: Path,
) -> dict[str, Any]:
    image = (card.get("facts") or {}).get("image") or {}
    geometry = image.get("geometry") or {}
    asset_id = int(image["assetId"]) if image.get("assetId") is not None else None
    base = {
        "cardId": card.get("id"),
        "variantId": card.get("variantId"),
        "tcg": card.get("tcg"),
        "assetId": asset_id,
        "contentSha256": card.get("imageSha256"),
        "geometry": geometry,
        "expectedLanguage": (
            ((card.get("facts") or {}).get("identity") or {}).get(
                "cardLanguage"
            )
        ),
    }
    if asset is None:
        return {**base, "decision": "reject_asset_missing", "reason": "asset_missing"}

    source_policy = classify_source(
        str(asset.get("source_path") or ""),
        tcg_code=str(card.get("tcg") or ""),
        width_px=asset.get("width_px"),
        height_px=asset.get("height_px"),
    )
    base["sourcePolicy"] = source_policy
    source_language = infer_source_language(
        str(asset.get("source_path") or "")
    )
    base["sourceLanguage"] = source_language
    if source_policy["status"] == "reject":
        return {
            **base,
            "decision": "reject_known_sample_source",
            "reason": source_policy["reason"],
        }
    content_policy = classify_content_sha256(base["contentSha256"])
    base["contentPolicy"] = content_policy
    if content_policy["status"] == "reject":
        return {
            **base,
            "decision": "reject_known_sample_content",
            "reason": content_policy["reason"],
        }
    if (
        source_language
        and base["expectedLanguage"]
        and source_language != base["expectedLanguage"]
    ):
        return {
            **base,
            "decision": "reject_language_mismatch",
            "reason": (
                f"expected_{base['expectedLanguage']}:"
                f"source_{source_language}"
            ),
        }
    if geometry.get("status") != "passed":
        return {
            **base,
            "decision": "reject_geometry",
            "reason": ",".join(geometry.get("reasons") or ["geometry_invalid"]),
        }
    path = resolve_asset_path(asset, assets_root)
    if path is None or not path.is_file():
        return {**base, "decision": "reject_asset_missing", "reason": "asset_missing"}
    try:
        from PIL import Image

        with Image.open(path) as opened:
            sample_evidence = detect_sample_evidence(opened.copy(), deep=True)
    except Exception as error:  # noqa: BLE001 - isolated one-card scanner failure
        return {
            **base,
            "decision": "retry_sample_scan",
            "reason": type(error).__name__,
        }
    if sample_evidence:
        return {
            **base,
            "decision": "reject_sample_ocr",
            "reason": (
                f"{sample_evidence['reason']}@"
                f"{sample_evidence['position']}"
            ),
            "sampleEvidence": sample_evidence,
        }
    return {
        **base,
        "decision": "pass_sample_ocr",
        "reason": None,
        "sampleEvidence": None,
    }


def ledger_result(
    row: Mapping[str, Any],
    card: Mapping[str, Any],
    *,
    run_id: str,
    report_path: Path,
) -> None:
    decision = str(row["decision"])
    key = item_key(card, row.get("assetId"))
    context = {
        "cardId": row.get("cardId"),
        "variantId": row.get("variantId"),
        "assetId": row.get("assetId"),
        "tcg": row.get("tcg"),
        "contentSha256": row.get("contentSha256"),
        "decision": decision,
    }
    if decision.startswith("pass_"):
        record_resolution(
            source=SOURCE,
            stage=STAGE,
            script=SCRIPT,
            item_key=key,
            run_id=run_id,
            resolution=decision,
            context=context,
            evidence_paths=[report_path],
        )
        return
    next_actions = {
        "reject_geometry": "normalize_image_geometry",
        "reject_known_sample_source": "replace_image_source",
        "reject_known_sample_content": "replace_image_source",
        "reject_language_mismatch": "replace_image_same_language",
        "reject_sample_ocr": "replace_image_source",
        "reject_asset_missing": "recover_image_asset",
        "retry_sample_scan": "retry_sample_ocr",
    }
    record_failure(
        source=SOURCE,
        stage=STAGE,
        script=SCRIPT,
        item_key=key,
        reason_code=decision,
        message=str(row.get("reason") or decision),
        retryable=True,
        run_id=run_id,
        context=context,
        evidence_paths=[report_path],
        next_action=next_actions[decision],
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workers", type=int, default=4, choices=range(1, 9))
    parser.add_argument("--assets-root", type=Path, default=DEFAULT_ASSETS_ROOT)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--write-ledger", action="store_true")
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    cards = list(report.get("cards") or [])
    asset_ids = sorted(
        {
            int(image["assetId"])
            for card in cards
            if (image := (card.get("facts") or {}).get("image") or {}).get(
                "assetId"
            )
            is not None
        }
    )
    assets = fetch_assets(asset_ids)
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {}
        for card in cards:
            image = (card.get("facts") or {}).get("image") or {}
            asset_id = (
                int(image["assetId"]) if image.get("assetId") is not None else None
            )
            future = pool.submit(
                evaluate_card,
                card,
                assets.get(asset_id) if asset_id is not None else None,
                assets_root=args.assets_root.resolve(),
            )
            futures[future] = card
        for future in as_completed(futures):
            rows.append(future.result())
    rows.sort(key=lambda row: (int(row.get("variantId") or 0), int(row.get("assetId") or 0)))

    counts = Counter(str(row["decision"]) for row in rows)
    output_dir = args.output_root.resolve() / args.run_id
    output_dir.mkdir(parents=True, exist_ok=False)
    report_path = output_dir / "report.json"
    document = {
        "schemaVersion": 1,
        "runId": args.run_id,
        "sourceReport": str(args.report),
        "readOnlyDatabase": True,
        "writesPublicAssets": False,
        "writesDatabase": False,
        "counts": dict(sorted(counts.items())),
        "cards": rows,
    }
    report_path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    retry_path = output_dir / "retry-worklist.json"
    if args.write_ledger:
        cards_by_variant = {int(card["variantId"]): card for card in cards}
        for row in rows:
            ledger_result(
                row,
                cards_by_variant[int(row["variantId"])],
                run_id=args.run_id,
                report_path=report_path,
            )
        export_retry_worklist(
            retry_path,
            source=SOURCE,
            script=SCRIPT,
            stage=STAGE,
        )
    print(
        json.dumps(
            {
                "runId": args.run_id,
                "counts": dict(sorted(counts.items())),
                "report": str(report_path),
                "retryWorklist": str(retry_path) if args.write_ledger else None,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if any(not key.startswith("pass_") for key in counts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
