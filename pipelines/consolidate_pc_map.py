#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consolidate active exact PriceCharting rows into the one canonical full900 map."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
REGISTRY = ROOT / "data/runtime/operator/collect/collect_registry.jsonl"
MANIFEST = ROOT / "data/launch/pc-map-additions-20260804.json"
REPORT = ROOT / "data/runtime/operator/collect/pc_map_consolidation.json"
SUPPLEMENTAL = (
    ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_en23_ai.jsonl",
    ROOT / "data/runtime/private-source-map/c11_pc_ebay_map.jsonl",
)


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def valid_pc_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "www.pricecharting.com":
        raise RuntimeError(f"non-canonical PriceCharting URL: {url}")
    if not parsed.path.startswith("/game/") or parsed.query or parsed.fragment:
        raise RuntimeError(f"invalid PriceCharting product path: {url}")
    return url


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", required=True)
    args = parser.parse_args()
    if not args.write:
        raise RuntimeError("--write is required")

    registry_rows = jsonl(REGISTRY)
    pc_rows = [row for row in registry_rows if row.get("adapter") == "pc_ebay_sales"]
    en_rows = [row for row in registry_rows if row.get("adapter") == "en_price_ref"]
    pc_exact = {
        int(row["variantId"]): str(row.get("externalId") or "").strip()
        for row in pc_rows
    }
    en_exact = {
        int(row["variantId"]): str(row.get("externalId") or "").strip()
        for row in en_rows
    }
    if not pc_exact or pc_exact != en_exact:
        raise RuntimeError("active PC/eBay and EN price-reference registries are not identical")
    if any(not product_id.isdigit() for product_id in pc_exact.values()):
        raise RuntimeError("active PriceCharting registry contains a non-numeric product ID")
    meta_by_variant = {
        int(row["variantId"]): row
        for row in pc_rows
    }

    canonical_rows = jsonl(CANONICAL)
    canonical_by_variant: dict[int, dict[str, Any]] = {}
    for row in canonical_rows:
        variant_id = int(row.get("variant_id") or 0)
        if not variant_id or variant_id in canonical_by_variant:
            raise RuntimeError(f"canonical map has invalid or duplicate variant: {variant_id}")
        canonical_by_variant[variant_id] = row

    manifest_payload = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
    manifest_by_variant = {
        int(row["variantId"]): row
        for row in (manifest_payload.get("rows") or [])
    }
    if len(manifest_by_variant) != len(manifest_payload.get("rows") or []):
        raise RuntimeError("launch manifest contains duplicate variants")
    for variant_id, row in manifest_by_variant.items():
        if str(row.get("productId") or "") != pc_exact.get(variant_id):
            raise RuntimeError(f"launch manifest product ID differs from active registry: {variant_id}")
        valid_pc_url(row.get("pcUrl"))

    source_paths = list(SUPPLEMENTAL) + sorted(
        ROOT.glob("data/runtime/private-source-map/c11_pc_ebay_map_full900_shard*.jsonl")
    )
    candidate_by_product: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in source_paths:
        if not path.is_file():
            continue
        for row in jsonl(path):
            product_id = str(row.get("pc_product_id") or "").strip()
            if not product_id.isdigit() or not row.get("pc_url"):
                continue
            try:
                valid_pc_url(row.get("pc_url"))
            except RuntimeError:
                continue
            candidate_by_product.setdefault(product_id, []).append((path, row))

    added = 0
    replaced = 0
    retained = 0
    manifest_used = 0
    supplemental_used = 0
    source_evidence: list[dict[str, Any]] = []
    for variant_id, product_id in sorted(pc_exact.items()):
        existing = canonical_by_variant.get(variant_id)
        if (
            existing
            and str(existing.get("pc_product_id") or "") == product_id
            and existing.get("pc_url")
        ):
            valid_pc_url(existing["pc_url"])
            retained += 1
            continue

        source_row: dict[str, Any] | None = None
        source_note = ""
        manifest_row = manifest_by_variant.get(variant_id)
        if manifest_row:
            source_row = {
                "card_name": manifest_row.get("cardName"),
                "pc_product_id": int(product_id),
                "pc_url": manifest_row.get("pcUrl"),
            }
            source_note = MANIFEST.relative_to(ROOT).as_posix()
            manifest_used += 1
        else:
            candidates = candidate_by_product.get(product_id) or []
            if not candidates:
                raise RuntimeError(
                    f"no verified transport row for active variant={variant_id} product={product_id}"
                )
            candidates.sort(
                key=lambda item: (
                    0 if int(item[1].get("variant_id") or 0) == variant_id else 1,
                    item[0].as_posix(),
                )
            )
            source_path, source_row = candidates[0]
            source_row = dict(source_row)
            source_note = source_path.relative_to(ROOT).as_posix()
            supplemental_used += 1

        assert source_row is not None
        canonical_url = valid_pc_url(source_row.get("pc_url"))
        target = dict(source_row)
        target.update(
            {
                "variant_id": variant_id,
                "pc_product_id": int(product_id),
                "pc_url": canonical_url,
                "card_name": str(meta_by_variant[variant_id].get("name") or target.get("card_name") or ""),
                "html_path": f"data/private/pricecharting_session/html/full900/{variant_id}_{product_id}.html",
                "htmlPath": f"data/private/pricecharting_session/html/full900/{variant_id}_{product_id}.html",
                "status": "mapped",
                "ready_for_c12": True,
                "confidence": "high",
                "source": "pc_launch_active_exact_consolidation",
                "mapped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "notes": (
                    f"active exact product ID {product_id}; transport consolidated from {source_note}; "
                    "page product-id must match before ingest"
                ),
                "ebay_items_psa10": [],
                "ebay_uuid_or_item": None,
            }
        )
        if existing:
            canonical_rows[canonical_rows.index(existing)] = target
            replaced += 1
        else:
            canonical_rows.append(target)
            added += 1
        canonical_by_variant[variant_id] = target
        source_evidence.append(
            {
                "variantId": variant_id,
                "productId": int(product_id),
                "url": canonical_url,
                "source": source_note,
            }
        )

    active_missing = sorted(set(pc_exact) - set(canonical_by_variant))
    active_mismatch = sorted(
        variant_id
        for variant_id, product_id in pc_exact.items()
        if str(canonical_by_variant[variant_id].get("pc_product_id") or "") != product_id
    )
    if active_missing or active_mismatch:
        raise RuntimeError(f"canonical active gate failed missing={active_missing} mismatch={active_mismatch}")

    canonical_rows.sort(key=lambda row: int(row.get("variant_id") or 0))
    serialized = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in canonical_rows
    )
    temporary = CANONICAL.with_name(f".{CANONICAL.name}.{os.getpid()}.next")
    temporary.write_text(serialized, encoding="utf-8")
    os.replace(temporary, CANONICAL)
    report = {
        "action": "consolidate-pc-map",
        "asOf": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "activePcVariants": len(pc_exact),
        "canonicalRows": len(canonical_rows),
        "retained": retained,
        "added": added,
        "replaced": replaced,
        "manifestUsed": manifest_used,
        "supplementalUsed": supplemental_used,
        "activeMissing": active_missing,
        "activeProductMismatch": active_mismatch,
        "canonicalSha256": hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        "sources": source_evidence,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "sources"}, ensure_ascii=False, indent=2))
    print(f"REPORT {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
