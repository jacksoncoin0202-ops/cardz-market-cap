#!/usr/bin/env python3
"""Build and serve the local, hash-bound CARDZ image-review queue."""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

import pymysql

try:
    from .canonical_db_qc import ROOT, resolve_asset_path
except ImportError:  # direct script execution
    from canonical_db_qc import ROOT, resolve_asset_path


TEMPLATE = ROOT / "tools/image-review/index.html"
DEFAULT_OUTPUT_ROOT = ROOT / "data/runtime/private-reports/image-review"


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def load_db_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def fetch_review_rows(asset_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
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
                        asset.source_version_sha256,
                        variant.canonical_name,
                        variant.set_name,
                        variant.collector_number,
                        variant.card_language,
                        variant.tcg_code,
                        printing.card_language AS printing_card_language,
                        printing.edition_code,
                        printing.parallel_code,
                         printing.finish_code,
                         printing.identity_status AS printing_identity_status,
                         printing.canonical_printing_sha256,
                        pointer.source_path,
                        pointer.public_allowed AS pointer_public_allowed,
                        qc.semantic_match_status AS qc_semantic_match_status,
                        qc.public_allowed AS qc_public_allowed,
                        qc.qc_version
                    FROM market_image_asset AS asset
                    JOIN catalog_variant AS variant
                      ON variant.id=asset.variant_id
                    LEFT JOIN catalog_printing_identity AS printing
                      ON printing.variant_id=asset.variant_id
                    LEFT JOIN market_image_source_pointer AS pointer
                      ON pointer.variant_id=asset.variant_id
                     AND pointer.image_kind=asset.image_kind
                     AND pointer.source_version_sha256=
                         asset.source_version_sha256
                    LEFT JOIN market_image_qc AS qc
                      ON qc.id=(
                        SELECT newest_qc.id
                        FROM market_image_qc AS newest_qc
                        WHERE newest_qc.image_asset_id=asset.id
                        ORDER BY newest_qc.checked_at DESC,newest_qc.id DESC
                        LIMIT 1
                      )
                    WHERE asset.id IN ({marks})
                    """,
                    tuple(batch),
                )
                rows.extend(cursor.fetchall())
    finally:
        connection.close()
    return {int(row["asset_id"]): row for row in rows}


def build_dataset(
    canonical_report: Mapping[str, Any],
    prefilter_report: Mapping[str, Any],
    db_rows: Mapping[int, Mapping[str, Any]],
    *,
    assets_root: Path,
) -> dict[str, Any]:
    canonical_cards = {
        int(card["variantId"]): card
        for card in canonical_report.get("cards") or []
    }
    cards: list[dict[str, Any]] = []
    auto_rejected: list[dict[str, Any]] = []
    preserved_bindings: list[dict[str, Any]] = []
    asset_paths: dict[str, str] = {}
    for prefilter in prefilter_report.get("cards") or []:
        if str(prefilter.get("decision") or "") not in {
            "pass_sample_source",
            "pass_sample_ocr",
        }:
            variant_id = int(prefilter["variantId"])
            canonical = canonical_cards.get(variant_id) or {}
            identity = (canonical.get("facts") or {}).get("identity") or {}
            auto_rejected.append(
                {
                    "cardId": canonical.get("id")
                    or prefilter.get("cardId"),
                    "variantId": variant_id,
                    "assetId": prefilter.get("assetId"),
                    "tcg": prefilter.get("tcg"),
                    "collectorNumber": identity.get("collectorNumber"),
                    "language": identity.get("cardLanguage"),
                    "setName": identity.get("set"),
                    "decision": prefilter.get("decision"),
                    "reason": prefilter.get("reason"),
                    "sampleEvidence": prefilter.get("sampleEvidence"),
                    "expectedLanguage": prefilter.get("expectedLanguage"),
                    "sourceLanguage": prefilter.get("sourceLanguage"),
                }
            )
            continue
        variant_id = int(prefilter["variantId"])
        asset_id = int(prefilter["assetId"])
        row = db_rows.get(asset_id)
        canonical = canonical_cards.get(variant_id)
        if row is None or canonical is None:
            raise RuntimeError(
                f"review_dataset_missing_row:variant={variant_id}:asset={asset_id}"
            )
        path = resolve_asset_path(row, assets_root)
        if path is None or not path.is_file():
            raise RuntimeError(
                f"review_dataset_asset_missing:variant={variant_id}:asset={asset_id}"
            )
        content_hash = str(row.get("content_sha256") or "")
        if hashlib.sha256(path.read_bytes()).hexdigest() != content_hash:
            raise RuntimeError(
                f"review_dataset_hash_mismatch:variant={variant_id}:asset={asset_id}"
            )
        token = hashlib.sha256(
            f"{asset_id}:{content_hash}".encode("utf-8")
        ).hexdigest()[:24]
        facts = canonical.get("facts") or {}
        identity = facts.get("identity") or {}
        image_facts = facts.get("image") or {}
        canonical_printing_sha256 = str(identity.get("printingSha256") or "")
        preserve_identity_and_language = (
            not (canonical.get("blockers") or [])
            and row.get("printing_identity_status") == "canonical"
            and canonical_printing_sha256
            and str(row.get("canonical_printing_sha256") or "")
            == canonical_printing_sha256
            and prefilter.get("expectedLanguage")
            == prefilter.get("sourceLanguage")
            == row.get("card_language")
            == row.get("printing_card_language")
        )
        exact_clean = (
            preserve_identity_and_language
            and
            image_facts.get("semanticMatchStatus") == "source_id_exact"
        )
        live_confirmed = (
            preserve_identity_and_language
            and
            str(row.get("qc_semantic_match_status") or "")
            == "human_or_vision_confirmed"
            and int(row.get("qc_public_allowed") or 0) == 1
            and int(row.get("pointer_public_allowed") or 0) == 1
            and str(row.get("qc_version") or "") == "human-review-v2"
        )
        if exact_clean or live_confirmed:
            preserved_bindings.append(
                {
                    "cardId": canonical.get("id"),
                    "variantId": variant_id,
                    "assetId": asset_id,
                    "contentSha256": content_hash,
                    "tcg": row.get("tcg_code"),
                    "name": row.get("canonical_name"),
                    "collectorNumber": row.get("collector_number"),
                    "language": row.get("card_language"),
                    "reason": (
                        "live_human_or_vision_confirmed"
                        if live_confirmed
                        else "source_id_exact_clean_prefilter"
                    ),
                }
            )
            continue
        price = facts.get("price") or {}
        population = facts.get("population") or {}
        sales = facts.get("sales30d") or {}
        source_policy = prefilter.get("sourcePolicy") or {}
        cards.append(
            {
                "reviewId": token,
                "cardId": canonical.get("id"),
                "variantId": variant_id,
                "assetId": asset_id,
                "contentSha256": content_hash,
                "tcg": row.get("tcg_code"),
                "name": row.get("canonical_name"),
                "setName": row.get("set_name"),
                "collectorNumber": row.get("collector_number"),
                "language": row.get("card_language"),
                "edition": row.get("edition_code"),
                "parallel": row.get("parallel_code"),
                "finish": row.get("finish_code"),
                "canonicalPrintingSha256": identity.get("printingSha256"),
                "canonicalPrintingIdentityStatus": row.get("printing_identity_status"),
                "sourceFamily": source_policy.get("family"),
                "sourcePath": row.get("source_path"),
                "expectedLanguage": prefilter.get("expectedLanguage"),
                "sourceLanguage": prefilter.get("sourceLanguage"),
                "prefilterDecision": prefilter.get("decision"),
                "geometry": prefilter.get("geometry"),
                "marketRank": canonical.get("marketRank"),
                "priceUsd": price.get("valueUsd"),
                "populationPsa10": population.get("value"),
                "sales30d": sales.get("purePsa10Count"),
                "imageUrl": f"/asset/{token}",
            }
        )
        asset_paths[token] = str(path.resolve())
    cards.sort(
        key=lambda card: (
            card["marketRank"] is None,
            int(card["marketRank"] or 10**9),
            str(card["tcg"]),
            int(card["variantId"]),
        )
    )
    public_document = {
        "schemaVersion": 1,
        "canonicalRunId": canonical_report.get("runId"),
        "prefilterRunId": prefilter_report.get("runId"),
        "count": len(cards),
        "preservedBindingCount": len(preserved_bindings),
        "preservedBindings": preserved_bindings,
        "autoRejectedCount": len(auto_rejected),
        "autoRejectedCounts": prefilter_report.get("counts") or {},
        "autoRejected": auto_rejected,
        "cards": cards,
    }
    return {
        **public_document,
        "datasetSha256": hashlib.sha256(
            canonical_bytes(public_document)
        ).hexdigest(),
        "assetPaths": asset_paths,
    }


def public_dataset(document: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in document.items()
        if key != "assetPaths"
    }


def write_dataset(document: Mapping[str, Any], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=False)
    destination.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def serve(dataset_path: Path, *, port: int) -> None:
    document = json.loads(dataset_path.read_text(encoding="utf-8"))
    assets = {
        str(key): Path(value)
        for key, value in (document.get("assetPaths") or {}).items()
    }
    api_payload = canonical_bytes(public_dataset(document))
    template = TEMPLATE.read_bytes()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            request_path = self.path.split("?", 1)[0]
            if request_path in {"/", "/index.html"}:
                self._send(template, "text/html; charset=utf-8")
                return
            if request_path == "/api/cards":
                self._send(api_payload, "application/json; charset=utf-8")
                return
            if request_path == "/health":
                self._send(
                    canonical_bytes(
                        {
                            "status": "ok",
                            "count": document.get("count"),
                            "datasetSha256": document.get("datasetSha256"),
                        }
                    ),
                    "application/json; charset=utf-8",
                )
                return
            if request_path.startswith("/asset/"):
                token = request_path.removeprefix("/asset/")
                path = assets.get(token)
                if path is not None and path.is_file():
                    self._send(
                        path.read_bytes(),
                        mimetypes.guess_type(path.name)[0]
                        or "application/octet-stream",
                    )
                    return
            self.send_error(404)

        def _send(self, payload: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(
        json.dumps(
            {
                "url": f"http://127.0.0.1:{port}",
                "count": document.get("count"),
                "datasetSha256": document.get("datasetSha256"),
            }
        ),
        flush=True,
    )
    server.serve_forever()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--canonical-report", type=Path, required=True)
    build.add_argument("--prefilter-report", type=Path, required=True)
    build.add_argument("--run-id", required=True)
    build.add_argument("--assets-root", type=Path, default=ROOT / "data/public/market-assets")
    build.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    server = subparsers.add_parser("serve")
    server.add_argument("--dataset", type=Path, required=True)
    server.add_argument("--port", type=int, default=4177)
    args = parser.parse_args(argv)

    if args.command == "serve":
        serve(args.dataset.resolve(), port=args.port)
        return 0

    canonical_report = json.loads(args.canonical_report.read_text(encoding="utf-8"))
    prefilter_report = json.loads(args.prefilter_report.read_text(encoding="utf-8"))
    asset_ids = sorted(
        {
            int(row["assetId"])
            for row in prefilter_report.get("cards") or []
            if row.get("assetId") is not None
        }
    )
    document = build_dataset(
        canonical_report,
        prefilter_report,
        fetch_review_rows(asset_ids),
        assets_root=args.assets_root.resolve(),
    )
    destination = (
        args.output_root.resolve() / args.run_id / "review-data.json"
    )
    write_dataset(document, destination)
    print(
        json.dumps(
            {
                "count": document["count"],
                "dataset": str(destination),
                "datasetSha256": document["datasetSha256"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
