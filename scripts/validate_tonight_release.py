#!/usr/bin/env python3
"""Single CARDZ 762 launch gate: canonical DB, receipt, snapshot, and assets."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import collect_control as collect  # noqa: E402
import operator_control as operator  # noqa: E402
from release_frontend_bundle import (  # noqa: E402
    FRONTEND_POLICY_ID,
    bundle_manifest,
)
from qualified_pool_operator import db, load_env  # noqa: E402


EXPECTED_ADAPTERS = set(collect.CHECKPOINT_ADAPTERS)


def read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_frontend_bundle(
    *,
    release_root: Path,
    frontend_receipt_path: Path,
    pass_frontend: dict[str, Any],
) -> dict[str, Any]:
    sync_receipt = read_object(frontend_receipt_path)
    source_manifest = bundle_manifest(ROOT)
    release_manifest = bundle_manifest(release_root)
    synced_source = sync_receipt.get("frontendBundle") or {}
    synced_release = sync_receipt.get("releaseBundle") or {}
    if pass_frontend.get("policyId") != FRONTEND_POLICY_ID:
        raise RuntimeError("pass receipt does not use the retired-graders frontend policy")
    if not (
        pass_frontend
        == source_manifest
        == release_manifest
        == synced_source
        == synced_release
    ):
        raise RuntimeError("pass, source and clean-release frontend bundles do not match")
    return {
        "policyId": pass_frontend["policyId"],
        "sha256": pass_frontend["sha256"],
        "fileCount": pass_frontend["fileCount"],
        "receipt": str(frontend_receipt_path.resolve()),
    }


def validate_snk_image_policy(
    *,
    snapshot: dict[str, Any],
    receipt: dict[str, Any],
    materialization_receipt: dict[str, Any],
) -> dict[str, Any]:
    policy = receipt.get("imagePolicy") or {}
    if policy.get("policyId") != operator.TOP100_SNK_IMAGE_POLICY_ID:
        raise RuntimeError("pass receipt does not use the approved Top-100 SNK policy")
    top100 = list(snapshot.get("top100") or [])
    decisions = {
        str(row.get("opaqueId")): row
        for row in (policy.get("decisions") or [])
        if isinstance(row, dict) and row.get("opaqueId")
    }
    if len(top100) != 100 or len(decisions) != 100:
        raise RuntimeError("SNK image policy does not bind all 100 displayed cards")
    mapping = materialization_receipt.get("mapping") or {}
    if not isinstance(mapping, dict):
        raise RuntimeError("asset materialization mapping must be an object")

    load_env()
    connection = db()
    try:
        cur = connection.cursor()
        universe = operator.current_universe(cur)
        metadata = operator.variant_meta(cur, universe.get("variantIds") or [])
        by_opaque = {
            str(row.get("opaque_id")): int(variant_id)
            for variant_id, row in metadata.items()
            if row.get("opaque_id")
        }
        try:
            top_variant_ids = [by_opaque[str(card.get("id"))] for card in top100]
        except KeyError as exc:
            raise RuntimeError(f"snapshot Top-100 card is not in the active universe: {exc}") from exc
        preferred = operator.image_rows(
            cur,
            top_variant_ids,
            prefer_snk_ids=top_variant_ids,
        )
    finally:
        connection.close()

    eligible = 0
    selected = 0
    for rank, (card, variant_id) in enumerate(zip(top100, top_variant_ids), start=1):
        expected = preferred.get(variant_id) or {}
        is_eligible = bool(expected.get("snk_source") and expected.get("snk_public_qc"))
        snapshot_sha = str((card.get("image") or {}).get("sha256") or "")
        expected_db_sha = str(expected.get("content_sha256") or "")
        decision = decisions.get(str(card.get("id"))) or {}
        decision_db_sha = str(decision.get("selectedSha256") or "")
        expected_public_sha = str(mapping.get(decision_db_sha) or decision_db_sha)
        if (
            int(card.get("marketRank") or -1) != rank
            or int(decision.get("marketRank") or -1) != rank
            or int(decision.get("variantId") or -1) != variant_id
            or decision.get("snkEligible") is not is_eligible
            or snapshot_sha != expected_public_sha
        ):
            raise RuntimeError(f"SNK image decision binding failed at displayed rank {rank}")
        if is_eligible:
            eligible += 1
            if decision_db_sha != expected_db_sha or decision.get("selectedSource") != "snkrdunk":
                raise RuntimeError(f"eligible SNK image was not selected at displayed rank {rank}")
            selected += 1
        elif decision.get("selectedSource") == "snkrdunk":
            raise RuntimeError(f"SNK image was selected without current eligibility at displayed rank {rank}")

    try:
        bound_counts = (
            int(policy.get("top100Cards")),
            int(policy.get("snkEligible")),
            int(policy.get("snkSelected")),
            int(policy.get("nonSnkOnlyWithoutEligible")),
        )
    except (TypeError, ValueError):
        bound_counts = (-1, -1, -1, -1)
    if bound_counts != (100, eligible, selected, 100 - eligible):
        raise RuntimeError("SNK image policy counts do not match DB-backed decisions")
    return {
        "policyId": policy["policyId"],
        "top100Cards": 100,
        "snkEligible": eligible,
        "snkSelected": selected,
        "nonSnkOnlyWithoutEligible": 100 - eligible,
    }


def freeze_rows(cur, variant_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT variant_id, freeze_kind, source_code, external_entity_id,
               content_sha256, acceptance_status
        FROM operator_binding_freeze
        WHERE acceptance_status='accepted' AND variant_id IN ({placeholders})
        """,
        variant_ids,
    )
    output: dict[int, list[dict[str, Any]]] = {}
    for row in cur.fetchall():
        output.setdefault(int(row["variant_id"]), []).append(row)
    return output


def validate_db(*, expected_cards: int, expected_backlog: int) -> dict[str, Any]:
    load_env()
    connection = db()
    try:
        cur = connection.cursor()
        universe = operator.current_universe(cur)
        variant_ids = universe["variantIds"]
        if len(variant_ids) != expected_cards or universe.get("memberCount") != expected_cards:
            raise RuntimeError("active universe is not the approved 762-card cohort")

        identities = operator.identities_by_variant(cur, variant_ids)
        prices = operator.latest_prices(cur, variant_ids)
        images = operator.image_rows(cur, variant_ids)
        freezes = freeze_rows(cur, variant_ids)
        metadata = operator.variant_meta(cur, variant_ids)
        gaps = operator.classify_gaps(
            universe.get("members") or [],
            metadata,
            identities,
            prices,
            images,
            freezes,
        )
        ready = 0
        for variant_id in variant_ids:
            kinds = {str(row["freeze_kind"]) for row in freezes.get(variant_id, [])}
            if {"identity", "source", "image"} <= kinds and variant_id in prices and variant_id in images:
                ready += 1
        if ready != expected_cards or gaps:
            raise RuntimeError(f"DB product readiness failed ready={ready} gaps={len(gaps)}")

        pops = operator.latest_psa10_pop(cur)
        qualified = {
            variant_id
            for variant_id, row in pops.items()
            if isinstance(row.get("psa10Pop"), int) and int(row["psa10Pop"]) >= 1000
        }
        backlog = len(qualified - set(variant_ids))
        if backlog != expected_backlog:
            raise RuntimeError(f"qualified candidate backlog is {backlog}, expected {expected_backlog}")

        rows = collect.load_universe_rows(cur)
        checkpoints = collect.load_checkpoints(cur)
        registry = collect.build_registry(rows, checkpoints)
        bind_due = [row for row in registry if row.get("modeNeeded") == "bind"]
        if bind_due:
            raise RuntimeError(f"active registry still has {len(bind_due)} binding gaps")
        per_adapter: dict[str, dict[str, Any]] = {}
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for adapter in collect.CHECKPOINT_ADAPTERS:
            streams = [row for row in registry if row.get("adapter") == adapter]
            missing = []
            ages = []
            for row in streams:
                checkpoint = collect._checkpoint_for(
                    checkpoints,
                    adapter,
                    int(row["variantId"]),
                    row.get("externalId"),
                )
                if checkpoint is None:
                    missing.append(int(row["variantId"]))
                    continue
                stamp = collect._parse_datetime(checkpoint.get("last_effective_at"))
                if stamp is None:
                    missing.append(int(row["variantId"]))
                    continue
                if stamp.tzinfo is not None:
                    stamp = stamp.replace(tzinfo=None)
                ages.append((now - stamp).total_seconds() / 3600)
            maximum_age = max(ages) if ages else None
            if not streams or missing or maximum_age is None or maximum_age > collect.SLA_HOURS:
                raise RuntimeError(
                    f"adapter checkpoint gate failed {adapter}: streams={len(streams)} "
                    f"missing={len(missing)} maxAgeHours={maximum_age}"
                )
            per_adapter[adapter] = {
                "streams": len(streams),
                "checkpointed": len(streams) - len(missing),
                "maxAgeHours": round(maximum_age, 2),
            }
        if set(per_adapter) != EXPECTED_ADAPTERS:
            raise RuntimeError("required adapter set is incomplete")
        return {
            "database": "cardz_market_cap",
            "universeLockId": universe["lockId"],
            "universeLockSha256": universe["lockSha256"],
            "activeCards": len(variant_ids),
            "productReady": ready,
            "gapCards": len(gaps),
            "qualifiedCandidatesBacklog": backlog,
            "adapters": per_adapter,
        }
    finally:
        connection.close()


def validate_snapshot(
    *,
    snapshot_path: Path,
    receipt_path: Path,
    assets_root: Path,
    release_root: Path,
    frontend_receipt_path: Path,
    materialization_receipt_path: Path,
    expected_cards: int,
    expected_backlog: int,
) -> dict[str, Any]:
    snapshot = read_object(snapshot_path)
    receipt_bytes = receipt_path.read_bytes()
    receipt = json.loads(receipt_bytes.decode("utf-8-sig"))
    generation = snapshot.get("generation") or {}
    cards = list(snapshot.get("top100") or []) + list(snapshot.get("watchlist") or [])
    if len(cards) != expected_cards or len(snapshot.get("top100") or []) != 100:
        raise RuntimeError("production snapshot count contract failed")
    if not (
        generation.get("mode") == "production"
        and generation.get("productionEligible") is True
        and generation.get("blockers") == []
        and generation.get("contentSha256") == operator.canonical_snapshot_sha256(snapshot)
        and generation.get("qcReceiptSha256") == hashlib.sha256(receipt_bytes).hexdigest()
    ):
        raise RuntimeError("production generation binding failed")
    if int((receipt.get("candidateCounts") or {}).get("newCandidates") or -1) != expected_backlog:
        raise RuntimeError("production receipt backlog binding failed")

    materialization_bytes = materialization_receipt_path.read_bytes()
    materialization_receipt = json.loads(materialization_bytes.decode("utf-8-sig"))
    mapping = materialization_receipt.get("mapping") or {}
    mapping_sha256 = hashlib.sha256(
        json.dumps(
            mapping,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    if not (
        generation.get("assetMaterializationReceiptSha256")
        == hashlib.sha256(materialization_bytes).hexdigest()
        and generation.get("assetMappingSha256") == mapping_sha256
        and materialization_receipt.get("mappingSha256") == mapping_sha256
    ):
        raise RuntimeError("asset materialization receipt binding failed")

    frontend = validate_frontend_bundle(
        release_root=release_root,
        frontend_receipt_path=frontend_receipt_path,
        pass_frontend=receipt.get("frontendBundle") or {},
    )
    image_policy = validate_snk_image_policy(
        snapshot=snapshot,
        receipt=receipt,
        materialization_receipt=materialization_receipt,
    )

    unique_hashes: set[str] = set()
    missing: list[str] = []
    for card in cards:
        image = card.get("image") or {}
        content_hash = str(image.get("sha256") or "")
        if len(content_hash) != 64:
            raise RuntimeError(f"invalid image hash for {card.get('id')}")
        unique_hashes.add(content_hash)
    for content_hash in sorted(unique_hashes):
        base = assets_root / f"{content_hash}.webp"
        if not base.is_file() or sha256_file(base) != content_hash:
            missing.append(base.name)
        for suffix in ("200", "600"):
            derivative = assets_root / f"{content_hash}_{suffix}.webp"
            if not derivative.is_file():
                missing.append(derivative.name)
    if missing:
        raise RuntimeError(f"release assets missing or invalid: {len(missing)}")
    return {
        "generationId": generation.get("id"),
        "contentSha256": generation.get("contentSha256"),
        "cards": len(cards),
        "top100": len(snapshot.get("top100") or []),
        "watchlist": len(snapshot.get("watchlist") or []),
        "uniqueImages": len(unique_hashes),
        "assetFiles": len(unique_hashes) * 3,
        "passReceiptSha256": generation.get("qcReceiptSha256"),
        "imagePolicy": image_policy,
        "frontendBundle": frontend,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=ROOT / "data/runtime/operator/promoted-product-snapshot.json",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "data/runtime/operator/pass_receipt.json",
    )
    parser.add_argument(
        "--assets-root",
        type=Path,
        default=ROOT / "data/public/market-assets",
    )
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument(
        "--frontend-receipt",
        type=Path,
        default=ROOT / "data/runtime/operator/frontend-bundle-receipt.json",
    )
    parser.add_argument(
        "--asset-materialization-receipt",
        type=Path,
        default=ROOT / "data/runtime/operator/asset-materialization-receipt.json",
    )
    parser.add_argument("--expected-cards", type=int, default=762)
    parser.add_argument("--expected-backlog", type=int, default=776)
    args = parser.parse_args()

    report = {
        "status": "passed",
        "asOf": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "db": validate_db(
            expected_cards=args.expected_cards,
            expected_backlog=args.expected_backlog,
        ),
        "release": validate_snapshot(
            snapshot_path=args.snapshot,
            receipt_path=args.receipt,
            assets_root=args.assets_root,
            release_root=args.release_root,
            frontend_receipt_path=args.frontend_receipt,
            materialization_receipt_path=args.asset_materialization_receipt,
            expected_cards=args.expected_cards,
            expected_backlog=args.expected_backlog,
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
