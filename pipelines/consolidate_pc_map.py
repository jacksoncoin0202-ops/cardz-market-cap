#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Consolidate active exact PriceCharting rows into the one canonical full900 map."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "data/runtime/private-source-map/c11_pc_ebay_map_full900.jsonl"
REGISTRY = ROOT / "data/runtime/operator/collect/collect_registry.jsonl"
MANIFEST = ROOT / "data/launch/pc-map-additions-20260804.json"
REPORT = ROOT / "data/runtime/operator/collect/pc_map_consolidation.json"
TRANSPORT_GLOB = "data/runtime/private-source-map/c11_pc_ebay_map_full900_shard*.jsonl"
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


def verified_binding_evidence_rows() -> list[tuple[Path, dict[str, Any]]]:
    """Project exact DB bindings only when their page receipt still verifies.

    The binding names the evidence path, its SHA-256, product id, and canonical
    URL.  The saved page must independently repeat the same product id and URL;
    a DB row by itself is never enough to materialize the canonical map.
    """

    from qualified_pool_operator import db

    product_pattern = re.compile(r"\bproduct-id=[\"'](\d+)[\"']", re.IGNORECASE)
    canonical_patterns = (
        re.compile(
            r"<link[^>]+rel=[\"']canonical[\"'][^>]+href=[\"']([^\"']+)[\"']",
            re.IGNORECASE,
        ),
        re.compile(
            r"<link[^>]+href=[\"']([^\"']+)[\"'][^>]+rel=[\"']canonical[\"']",
            re.IGNORECASE,
        ),
    )
    connection = db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT variant_id,external_entity_id,bind_evidence_json
                  FROM catalog_source_identity
                 WHERE source_code='pricecharting' AND match_status='exact'
                """
            )
            bindings = [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()

    rows: list[tuple[Path, dict[str, Any]]] = []
    root_resolved = ROOT.resolve()
    for binding in bindings:
        payload = binding.get("bind_evidence_json") or {}
        if isinstance(payload, str):
            payload = json.loads(payload)
        evidence = payload.get("evidence") or {}
        evidence_path = Path(str(evidence.get("path") or ""))
        path = evidence_path if evidence_path.is_absolute() else ROOT / evidence_path
        try:
            path.resolve().relative_to(root_resolved)
        except (OSError, ValueError):
            continue
        if not path.is_file():
            continue
        payload_bytes = path.read_bytes()
        expected_sha256 = str(evidence.get("sha256") or "").lower()
        current_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        html = payload_bytes.decode("utf-8", errors="replace")
        product_match = product_pattern.search(html)
        canonical_match = next(
            (candidate.search(html) for candidate in canonical_patterns if candidate.search(html)),
            None,
        )
        if not product_match or not canonical_match:
            continue
        try:
            canonical_url = valid_pc_url(unescape(canonical_match.group(1)))
            expected_url = valid_pc_url(unescape(str(evidence.get("canonicalUrl") or "")))
        except RuntimeError:
            continue
        product_id = str(binding.get("external_entity_id") or "")
        if product_match.group(1) != product_id or canonical_url != expected_url:
            continue
        rows.append(
            (
                path,
                {
                    "variant_id": int(binding["variant_id"]),
                    "pc_product_id": int(product_id),
                    "pc_url": canonical_url,
                    "source_page_sha256": current_sha256,
                    "binding_evidence_sha256": expected_sha256,
                    "binding_evidence_sha_stale": current_sha256 != expected_sha256,
                },
            )
        )
    return rows


def rejected_pc_binding_rows() -> list[dict[str, Any]]:
    """Every PriceCharting binding the DB holds at match_status='rejected'."""

    from qualified_pool_operator import db

    connection = db()
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT variant_id,external_entity_id,match_status,bind_evidence_json
                  FROM catalog_source_identity
                 WHERE source_code='pricecharting' AND match_status='rejected'
                """
            )
            return [dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def rejected_in_place_pairs(rows: list[dict[str, Any]]) -> set[tuple[int, str]]:
    """(variant, product) pairs somebody ruled wrong, from rejected_pc_binding_rows().

    A verdict is read with rejection_is_verdict, the predicate every lane uses
    to tell a reasoned rejection from collateral quarantine. Collateral never
    examined the product, and pc-identity-reverify may still promote it with
    the map row as its second opinion, so that row stays.
    """

    from rebuild_036 import rejection_is_verdict

    return {
        (int(row["variant_id"]), str(row.get("external_entity_id") or "").strip())
        for row in rows
        if str(row.get("match_status") or "") == "rejected"
        and rejection_is_verdict(row.get("bind_evidence_json"))
    }


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

    canonical_rows = jsonl(CANONICAL) if CANONICAL.is_file() else []
    canonical_by_variant: dict[int, dict[str, Any]] = {}
    for row in canonical_rows:
        variant_id = int(row.get("variant_id") or 0)
        if not variant_id or variant_id in canonical_by_variant:
            raise RuntimeError(f"canonical map has invalid or duplicate variant: {variant_id}")
        canonical_by_variant[variant_id] = row

    # The launch manifest is one day's hand-verified transport rows, not a
    # standing statement about the world: reverify re-binds cards after it, and
    # a human refusal retires others. So a manifest row is usable transport only
    # while it still names the active product -- the rest are history and get
    # reported, never silently used. It is also absent from checkouts that were
    # not the launch, which is not an error; the shard files still have to carry
    # every active product or the "no verified transport row" gate below fires.
    manifest_by_variant: dict[int, dict[str, Any]] = {}
    manifest_stale: list[int] = []
    if MANIFEST.is_file():
        manifest_payload = json.loads(MANIFEST.read_text(encoding="utf-8-sig"))
        manifest_rows = manifest_payload.get("rows") or []
        manifest_by_variant = {int(row["variantId"]): row for row in manifest_rows}
        if len(manifest_by_variant) != len(manifest_rows):
            raise RuntimeError("launch manifest contains duplicate variants")
        for variant_id, row in sorted(manifest_by_variant.items()):
            valid_pc_url(row.get("pcUrl"))
            if str(row.get("productId") or "") != pc_exact.get(variant_id):
                manifest_stale.append(variant_id)
        for variant_id in manifest_stale:
            manifest_by_variant.pop(variant_id)

    source_paths = list(SUPPLEMENTAL) + sorted(ROOT.glob(TRANSPORT_GLOB))
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
    binding_evidence_rows = verified_binding_evidence_rows()
    for path, row in binding_evidence_rows:
        product_id = str(row["pc_product_id"])
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

    # A PriceCharting product names exactly one card, so when a repoint hands a
    # product to another variant the loser's row is left naming a product it no
    # longer owns. Nothing above retires it -- the loop only ever visits active
    # variants -- and because pc-identity-reverify reads this file as the second
    # opinion its gate needs, that dead row vetoes the loser's next proposal for
    # good. Two owners for one product cannot both be right and the active
    # registry is what this map is defined to project, so the dead row goes.
    # Rows whose product nobody actively holds are left alone: those are cards
    # awaiting a decision, not a contradiction.
    #
    # Except when the decision was made. A binding rejected IN PLACE (exact ->
    # rejected by a verdict, e.g. apply_verified_source_bindings' reject
    # manifest) leaves its product unowned, so the rule above never fires, and
    # the dead row then vetoes the card's correct proposal with
    # map_product_mismatch. 2026-09-25: nine WOTC 1st Edition cards rejected off
    # their Unlimited products could only be rebound by pointing the judge at a
    # scratch copy of this map. The ruling on the pair is the contradiction, so
    # that row goes too.
    owner_by_product = {product_id: variant_id for variant_id, product_id in pc_exact.items()}
    rejected_pairs = rejected_in_place_pairs(rejected_pc_binding_rows())
    retired: list[dict[str, Any]] = []
    retired_rejected: list[dict[str, Any]] = []
    for variant_id, row in sorted(canonical_by_variant.items()):
        if variant_id in pc_exact:
            continue
        product_id = str(row.get("pc_product_id") or "").strip()
        if (variant_id, product_id) in rejected_pairs:
            canonical_rows.remove(row)
            retired_rejected.append({"variantId": variant_id, "productId": product_id})
            continue
        holder = owner_by_product.get(product_id)
        if holder is None or holder == variant_id:
            continue
        canonical_rows.remove(row)
        retired.append({
            "variantId": variant_id,
            "productId": str(row.get("pc_product_id") or ""),
            "nowOwnedBy": holder,
        })
    for entry in retired + retired_rejected:
        canonical_by_variant.pop(int(entry["variantId"]), None)

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
    # Bytes, not text: write_text() turns every \n into \r\n on Windows, so the
    # sha256 below described a file that was never on disk -- a receipt you
    # cannot check against the artifact is not a receipt. Re-read and compare
    # after the swap so that stays true the next time someone edits this.
    payload = serialized.encode("utf-8")
    canonical_sha256 = hashlib.sha256(payload).hexdigest()
    temporary = CANONICAL.with_name(f".{CANONICAL.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, CANONICAL)
    written_sha256 = hashlib.sha256(CANONICAL.read_bytes()).hexdigest()
    if written_sha256 != canonical_sha256:
        raise RuntimeError(
            f"canonical map on disk {written_sha256} is not what was hashed {canonical_sha256}"
        )
    report = {
        "action": "consolidate-pc-map",
        "asOf": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "activePcVariants": len(pc_exact),
        "canonicalRows": len(canonical_rows),
        "retained": retained,
        "added": added,
        "replaced": replaced,
        "manifestPresent": MANIFEST.is_file(),
        "manifestUsed": manifest_used,
        "manifestStale": manifest_stale,
        "supplementalUsed": supplemental_used,
        "verifiedBindingEvidenceRows": len(binding_evidence_rows),
        "retiredStolenProducts": retired,
        "retiredRejectedInPlace": retired_rejected,
        "activeMissing": active_missing,
        "activeProductMismatch": active_mismatch,
        "canonicalSha256": canonical_sha256,
        "sources": source_evidence,
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "sources"}, ensure_ascii=False, indent=2))
    print(f"REPORT {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
