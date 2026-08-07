#!/usr/bin/env python3
"""Materialize snapshot-referenced public image triplets from local private bytes."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import sys
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import build_asset_derivatives as derivatives  # noqa: E402
import operator_control as operator  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402


EXPECTED_DERIVATIVE_DIMENSIONS = {
    "200": (200, 280),
    "600": (429, 600),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_snapshot(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], set[str]]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    cards = [*(payload.get("top100") or []), *(payload.get("watchlist") or [])]
    hashes = {str((card.get("image") or {}).get("sha256") or "") for card in cards}
    if any(len(value) != 64 for value in hashes):
        raise RuntimeError("snapshot contains an invalid image hash")
    generation = payload.get("generation") or {}
    if generation.get("productionEligible") is not True:
        raise RuntimeError("a promoted production snapshot is required")
    return payload, cards, hashes


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def source_candidates(private_object_key: str, assets: Path) -> list[Path]:
    key = private_object_key.strip()
    if not key:
        return []
    key_path = Path(key)
    candidates: list[Path] = []
    if key_path.is_absolute():
        candidates.append(key_path)
    else:
        if key.startswith("g10/"):
            candidates.append(ROOT / "data" / "runtime" / "private-landing" / key_path)
            candidates.append(ROOT / "integrations" / "grade10" / "data" / "images" / key_path.name)
        candidates.extend((ROOT / key_path, assets / key_path.name))
    return candidates


def asset_rebase_reasons(assets: Path, content_hash: str) -> set[str]:
    reasons: set[str] = set()
    master = assets / f"{content_hash}.webp"
    if not master.is_file():
        return {"master-missing"}
    if sha256_file(master) != content_hash:
        reasons.add("master-hash")
    try:
        with Image.open(master) as decoded:
            decoded.load()
            if decoded.format != "WEBP":
                reasons.add("master-format")
    except (OSError, UnidentifiedImageError):
        reasons.add("master-decode")
    for suffix, expected_dimensions in EXPECTED_DERIVATIVE_DIMENSIONS.items():
        derivative = assets / f"{content_hash}_{suffix}.webp"
        if not derivative.is_file():
            reasons.add(f"derivative-{suffix}-missing")
            continue
        try:
            with Image.open(derivative) as decoded:
                decoded.load()
                if decoded.format != "WEBP":
                    reasons.add(f"derivative-{suffix}-format")
                if decoded.size != expected_dimensions:
                    reasons.add(f"derivative-{suffix}-dimensions")
        except (OSError, UnidentifiedImageError):
            reasons.add(f"derivative-{suffix}-decode")
    return reasons


def canonical_master(source: Path) -> bytes:
    with Image.open(source) as opened:
        image = opened.copy()
        image.load()
        image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
    output = io.BytesIO()
    image.save(output, format="WEBP", lossless=True, method=0, exact=True)
    return output.getvalue()


def load_asset_rows(content_hashes: set[str]) -> dict[str, list[dict[str, Any]]]:
    load_env()
    connection = db()
    try:
        cursor = connection.cursor()
        placeholders = ",".join(["%s"] * len(content_hashes))
        cursor.execute(
            f"""
            SELECT id, content_sha256, private_object_key
            FROM market_image_asset
            WHERE content_sha256 IN ({placeholders})
            ORDER BY id DESC
            """,
            tuple(sorted(content_hashes)),
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in cursor.fetchall():
            grouped.setdefault(str(row["content_sha256"]), []).append(row)
        return grouped
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument(
        "--assets",
        type=Path,
        default=ROOT / "data" / "public" / "market-assets",
    )
    parser.add_argument("--output-snapshot", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()

    snapshot = args.snapshot.resolve()
    assets = args.assets.resolve()
    output_snapshot = args.output_snapshot.resolve()
    receipt_path = args.receipt.resolve()
    assets.mkdir(parents=True, exist_ok=True)
    payload, cards, wanted = read_snapshot(snapshot)
    prior_mapping: dict[str, str] = {}
    if receipt_path.is_file():
        prior_receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        candidate_mapping = prior_receipt.get("mapping") if isinstance(prior_receipt, dict) else None
        if isinstance(candidate_mapping, dict):
            prior_mapping = {
                str(source_hash): str(public_hash)
                for source_hash, public_hash in candidate_mapping.items()
            }

    mismatched = {
        content_hash
        for content_hash in wanted
        if (assets / f"{content_hash}.webp").is_file()
        and sha256_file(assets / f"{content_hash}.webp") != content_hash
    }

    missing = {
        content_hash
        for content_hash in wanted
        if not (assets / f"{content_hash}.webp").is_file()
    }
    rows = load_asset_rows(missing) if missing else {}
    resolved: dict[str, Path] = {}
    for content_hash in sorted(missing):
        for row in rows.get(content_hash, []):
            for candidate in source_candidates(str(row.get("private_object_key") or ""), assets):
                if candidate.is_file() and sha256_file(candidate) == content_hash:
                    resolved[content_hash] = candidate
                    break
            if content_hash in resolved:
                break
    unresolved = sorted(missing - set(resolved))
    if unresolved:
        raise RuntimeError(f"no hash-matched local private bytes for {len(unresolved)} masters")

    for content_hash, source in resolved.items():
        target = assets / f"{content_hash}.webp"
        shutil.copyfile(source, target)
        if sha256_file(target) != content_hash:
            raise RuntimeError(f"materialized master hash mismatch: {content_hash}")

    reusable_public_hashes = {
        content_hash: public_hash
        for content_hash, public_hash in prior_mapping.items()
        if content_hash in wanted
        and len(public_hash) == 64
        and all(
            (assets / f"{public_hash}{suffix}.webp").is_file()
            for suffix in ("", "_200", "_600")
        )
    }
    reasons_by_db_hash = {
        content_hash: (
            {"receipt-mapped-canonical"}
            if content_hash in reusable_public_hashes
            else asset_rebase_reasons(assets, content_hash)
        )
        for content_hash in sorted(wanted)
    }
    canonical_rebases = {
        content_hash
        for content_hash, reasons in reasons_by_db_hash.items()
        if reasons
    }
    reason_counts: dict[str, int] = {}
    for reasons in reasons_by_db_hash.values():
        for reason in reasons:
            reason_counts[reason] = reason_counts.get(reason, 0) + 1

    public_hash_by_db_hash: dict[str, str] = {}
    reused_canonical_rebases = 0
    derivative_sets_written = 0
    for content_hash in sorted(wanted):
        source = assets / f"{content_hash}.webp"
        if not source.is_file():
            raise RuntimeError(f"public master is still missing: {content_hash}")
        if content_hash in canonical_rebases:
            prior_public_hash = reusable_public_hashes.get(content_hash, "")
            if prior_public_hash:
                public_hash_by_db_hash[content_hash] = prior_public_hash
                reused_canonical_rebases += 1
                continue
            canonical_bytes = canonical_master(source)
            public_hash = hashlib.sha256(canonical_bytes).hexdigest()
            if public_hash == content_hash:
                raise RuntimeError(
                    f"canonical master did not create an append-only identity: {content_hash}"
                )
            target = assets / f"{public_hash}.webp"
            if target.is_file():
                if target.read_bytes() != canonical_bytes:
                    raise RuntimeError(f"canonical public hash collision: {public_hash}")
                if asset_rebase_reasons(assets, public_hash):
                    raise RuntimeError(
                        f"existing canonical public triplet is invalid: {public_hash}"
                    )
            else:
                temporary = assets / f".{public_hash}.webp.next"
                temporary.write_bytes(canonical_bytes)
                temporary.replace(target)
                derivatives.build(assets, public_hash, True)
                derivative_sets_written += 1
        else:
            public_hash = sha256_file(source)
        public_hash_by_db_hash[content_hash] = public_hash

    for card in cards:
        image = card.get("image") or {}
        db_hash = str(image.get("sha256") or "")
        public_hash = public_hash_by_db_hash[db_hash]
        image["sha256"] = public_hash
        image["src"] = f"/market-assets/{public_hash}.webp"
        image["variants"] = {
            "200": f"/market-assets/{public_hash}_200.webp",
            "600": f"/market-assets/{public_hash}_600.webp",
        }

    public_hashes = set(public_hash_by_db_hash.values())
    derivative_hashes = {
        public_hash_by_db_hash[content_hash]
        for content_hash in canonical_rebases
    }

    changed_mapping = {
        db_hash: public_hash
        for db_hash, public_hash in public_hash_by_db_hash.items()
        if db_hash != public_hash
    }
    mapping_bytes = json.dumps(
        changed_mapping,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    receipt = {
        "action": "materialize-snapshot-assets",
        "inputSnapshotSha256": sha256_file(snapshot),
        "snapshotImages": len(wanted),
        "publicImages": len(public_hashes),
        "mastersMaterializedFromPrivate": sorted(resolved),
        "rebasedMasters": len(changed_mapping),
        "canonicalRebases": len(canonical_rebases),
        "canonicalRebaseReasons": reason_counts,
        "mappingSha256": hashlib.sha256(mapping_bytes).hexdigest(),
        "mapping": changed_mapping,
        "reusedCanonicalRebases": reused_canonical_rebases,
        "derivativeSetsWritten": derivative_sets_written,
    }
    write_json(receipt_path, receipt)
    receipt_sha256 = sha256_file(receipt_path)
    payload["generation"]["assetMaterializationReceiptSha256"] = receipt_sha256
    payload["generation"]["assetMappingSha256"] = receipt["mappingSha256"]
    payload["generation"]["contentSha256"] = operator.canonical_snapshot_sha256(payload)
    write_json(output_snapshot, payload)

    print(json.dumps({
        "action": "materialize-snapshot-assets",
        "snapshotImages": len(wanted),
        "publicImages": len(public_hashes),
        "mastersMaterialized": len(resolved),
        "rebasedMasters": len(changed_mapping),
        "reusedCanonicalRebases": reused_canonical_rebases,
        "derivativeSetsWritten": derivative_sets_written,
        "assetFilesReady": len(public_hashes) * 3,
        "outputSnapshot": str(output_snapshot),
        "receipt": str(receipt_path),
        "contentSha256": payload["generation"]["contentSha256"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
