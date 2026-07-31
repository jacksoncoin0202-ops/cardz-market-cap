#!/usr/bin/env python3
"""Build one immutable, non-promoted publication candidate.

This helper consumes an already-exported canonical snapshot plus its strict QC
receipt.  It never exports from the database and never writes a public/runtime
``latest.json``, the tracked demo seed, or a local last-good snapshot.

The resulting candidate can be handed to ``pipelines/publish-snapshot.mjs``:

    python -X utf8 scripts/bake_publish_pack.py \
      --snapshot <snapshot.json> \
      --qc-receipt <qc-receipt.json>

Only the official publisher may create or advance a pointer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSETS_ROOT = ROOT / "data" / "public" / "market-assets"
DEFAULT_OUT_ROOT = ROOT / "data" / "public" / "publish-staging" / "candidates"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
SAFE_GENERATION = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$")
WINDOWS = ("1d", "7d", "30d")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _js_safe_numbers(value: Any) -> Any:
    """Mirror the canonical snapshot producer's Python/JS number contract."""

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"snapshot contains non-finite number: {value!r}")
        if value.is_integer() and abs(value) < 2**53:
            return int(value)
        if "e" in repr(value):
            raise ValueError(f"snapshot float uses unsupported exponent notation: {value!r}")
        return value
    if isinstance(value, dict):
        return {key: _js_safe_numbers(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_js_safe_numbers(item) for item in value]
    return value


def canonical_snapshot_sha256(snapshot: Mapping[str, Any]) -> str:
    payload = dict(snapshot)
    generation = snapshot.get("generation")
    payload["generation"] = {
        **(generation if isinstance(generation, Mapping) else {}),
        "contentSha256": "",
    }
    encoded = json.dumps(
        _js_safe_numbers(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(encoded)


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _metric_age_hours(metric: Any, effective_at: datetime | None) -> float | None:
    if not isinstance(metric, Mapping) or effective_at is None:
        return None
    observed = _parse_time(metric.get("asOf"))
    if observed is None:
        return None
    return (effective_at - observed).total_seconds() / 3600


def _looks_like_webp(payload: bytes) -> bool:
    return len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP"


def _card_image_hash(card: Any) -> str | None:
    if not isinstance(card, Mapping):
        return None
    image = card.get("image")
    if not isinstance(image, Mapping):
        return None
    value = image.get("sha256")
    return value if isinstance(value, str) and HEX64.fullmatch(value) else None


def _printing_part(value: Any) -> str:
    return str(value or "").strip().casefold()


def _printing_language_part(value: Any) -> str:
    """Match pipelines.card_identity.normalize_language for the 7-part hash segment."""

    if value is None:
        return ""
    raw = str(value).strip()
    if raw in {"en", "ja", "ko", "zhCN", "zhTW"}:
        return raw
    key = raw.casefold().replace("_", "-")
    aliases = {
        "eng": "en",
        "english": "en",
        "jp": "ja",
        "jpn": "ja",
        "japanese": "ja",
        "kr": "ko",
        "korean": "ko",
        "zh-cn": "zhCN",
        "zh-hans": "zhCN",
        "zhcn": "zhCN",
        "zh-tw": "zhTW",
        "zh-hant": "zhTW",
        "zhtw": "zhTW",
    }
    return aliases.get(key, "")


def _printing_identity_blockers(card: Mapping[str, Any], index: int) -> list[str]:
    prefix = f"public_cards[{index}].printing_identity"
    identity = card.get("printingIdentity")
    if not isinstance(identity, Mapping):
        return [f"{prefix}_missing"]
    # 7-part: tcg | language | set | collector | edition | parallel | finish
    language = _printing_language_part(identity.get("cardLanguage")) or _printing_language_part(
        card.get("cardLanguage")
    )
    key = (
        _printing_part(card.get("tcg")),
        language,
        _printing_part(identity.get("setName")),
        _printing_part(identity.get("collectorNumber")),
        _printing_part(identity.get("editionCode")),
        _printing_part(identity.get("parallelCode")),
        _printing_part(identity.get("finishCode")),
    )
    blockers: list[str] = []
    # language may be empty on legacy rows; tcg/set/collector/edition/parallel/finish required
    if any(not part for part in (key[0], key[2], key[3], key[4], key[5], key[6])):
        blockers.append(f"{prefix}_incomplete")
    canonical_hash = _printing_part(identity.get("canonicalPrintingSha256"))
    expected_hash = sha256_bytes("|".join(key).encode("utf-8"))
    if not HEX64.fullmatch(canonical_hash) or canonical_hash != expected_hash:
        blockers.append(f"{prefix}_hash_mismatch")
    if not HEX64.fullmatch(_printing_part(identity.get("evidenceSha256"))):
        blockers.append(f"{prefix}_evidence_invalid")
    sets = card.get("sets")
    set_name = sets.get("en") if isinstance(sets, Mapping) else None
    if _printing_part(set_name) != key[2]:
        blockers.append(f"{prefix}_set_mismatch")
    collector = card.get("collectorNumber")
    normalized = collector.get("normalized") if isinstance(collector, Mapping) else None
    if _printing_part(normalized) != key[3]:
        blockers.append(f"{prefix}_collector_mismatch")
    return blockers


def _validate_metric_times(value: Any, effective_at: datetime | None, path: str, blockers: list[str]) -> None:
    if isinstance(value, Mapping):
        if "asOf" in value and value.get("asOf") is not None:
            observed = _parse_time(value.get("asOf"))
            if observed is None:
                blockers.append(f"{path}.asOf_invalid")
            elif effective_at is not None and observed > effective_at:
                blockers.append(f"{path}.asOf_after_effectiveAt")
        for key, item in value.items():
            _validate_metric_times(item, effective_at, f"{path}.{key}", blockers)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _validate_metric_times(item, effective_at, f"{path}[{index}]", blockers)


def _validate_snapshot(snapshot: Any, receipt_sha256: str) -> tuple[list[str], list[Mapping[str, Any]]]:
    blockers: list[str] = []
    if not isinstance(snapshot, Mapping):
        return ["snapshot_not_object"], []

    generation = snapshot.get("generation")
    coverage = snapshot.get("coverage")
    top = snapshot.get("top100")
    watch = snapshot.get("watchlist")
    if not isinstance(generation, Mapping):
        blockers.append("generation_missing")
        generation = {}
    if not isinstance(coverage, Mapping):
        blockers.append("coverage_missing")
        coverage = {}
    if not isinstance(top, list):
        blockers.append("top100_not_array")
        top = []
    if not isinstance(watch, list):
        blockers.append("watchlist_not_array")
        watch = []

    generation_id = generation.get("id")
    if not isinstance(generation_id, str) or not SAFE_GENERATION.fullmatch(generation_id):
        blockers.append("generation_id_invalid")
    if generation.get("mode") != "production":
        blockers.append("generation_mode_not_production")
    if generation.get("productionEligible") is not True:
        blockers.append("generation_not_production_eligible")
    generation_blockers = generation.get("blockers")
    if generation_blockers != []:
        blockers.append("generation_has_blockers")

    declared_content = generation.get("contentSha256")
    if not isinstance(declared_content, str) or not HEX64.fullmatch(declared_content):
        blockers.append("generation_content_sha256_invalid")
    else:
        try:
            actual_content = canonical_snapshot_sha256(snapshot)
        except ValueError:
            actual_content = None
        if actual_content != declared_content:
            blockers.append("generation_content_sha256_mismatch")

    if generation.get("qcReceiptSha256") != receipt_sha256:
        blockers.append("generation_qc_receipt_sha256_mismatch")

    generated_at = _parse_time(generation.get("generatedAt"))
    effective_at = _parse_time(generation.get("effectiveAt"))
    if generated_at is None:
        blockers.append("generation_generatedAt_invalid")
    if effective_at is None:
        blockers.append("generation_effectiveAt_invalid")
    if generated_at is not None and effective_at is not None and effective_at > generated_at:
        blockers.append("generation_effectiveAt_after_generatedAt")

    verified_count = len(top)
    expected_claim = "verified-top-100" if verified_count == 100 else "verified-top-n"
    if not 1 <= verified_count <= 100:
        blockers.append("verified_top_count_out_of_range")
    if coverage.get("claim") != expected_claim:
        blockers.append("coverage_claim_mismatch")
    if coverage.get("requestedCount") != 100:
        blockers.append("coverage_requested_count_mismatch")
    if coverage.get("verifiedCount") != verified_count:
        blockers.append("coverage_verified_count_mismatch")
    if coverage.get("top100Count") != verified_count:
        blockers.append("coverage_top100_count_mismatch")
    if coverage.get("watchlistCount") != len(watch):
        blockers.append("coverage_watchlist_count_mismatch")

    cards: list[Mapping[str, Any]] = [
        card for card in [*top, *watch] if isinstance(card, Mapping)
    ]
    if len(cards) != len(top) + len(watch):
        blockers.append("public_card_not_object")
    ids = [card.get("id") for card in cards]
    if any(not isinstance(card_id, str) or not card_id for card_id in ids):
        blockers.append("public_card_id_invalid")
    if len(set(ids)) != len(ids):
        blockers.append("duplicate_public_card_id")

    market_ranks = [card.get("marketRank") for card in cards]
    if any(not isinstance(rank, int) or isinstance(rank, bool) or rank < 1 for rank in market_ranks):
        blockers.append("market_rank_invalid")
    if len(set(market_ranks)) != len(market_ranks):
        blockers.append("duplicate_market_rank")
    for index, card in enumerate(cards):
        blockers.extend(_printing_identity_blockers(card, index))

    for index, card in enumerate(top):
        if not isinstance(card, Mapping):
            continue
        if card.get("rank") != index + 1 or card.get("viewRank") != index + 1:
            blockers.append(f"top100[{index}].view_rank_invalid")
        population = card.get("populationPsa10")
        price = card.get("pricePsa10")
        market_cap = card.get("marketCap")
        if (
            not isinstance(population, Mapping)
            or population.get("estimated") is not False
            or population.get("status") not in {"ready", "stale"}
            or not isinstance(population.get("value"), (int, float))
            or isinstance(population.get("value"), bool)
            or population.get("value") < 1000
        ):
            blockers.append(f"top100[{index}].population_not_qualified")
        pop_age = _metric_age_hours(population, effective_at)
        if pop_age is None or pop_age < 0 or pop_age > 168:
            blockers.append(f"top100[{index}].population_stale")
        if (
            not isinstance(price, Mapping)
            or price.get("status") not in {"ready", "stale"}
            or not isinstance(price.get("value"), (int, float))
            or isinstance(price.get("value"), bool)
            or price.get("value") <= 0
        ):
            blockers.append(f"top100[{index}].price_unavailable")
        price_age = _metric_age_hours(price, effective_at)
        if price_age is None or price_age < 0 or price_age > 48:
            blockers.append(f"top100[{index}].price_stale")
        if (
            isinstance(population, Mapping)
            and isinstance(price, Mapping)
            and isinstance(market_cap, Mapping)
            and isinstance(population.get("value"), (int, float))
            and not isinstance(population.get("value"), bool)
            and isinstance(price.get("value"), (int, float))
            and not isinstance(price.get("value"), bool)
            and isinstance(market_cap.get("value"), (int, float))
            and not isinstance(market_cap.get("value"), bool)
        ):
            expected = population["value"] * price["value"]
            if abs(market_cap["value"] - expected) >= 0.01:
                blockers.append(f"top100[{index}].market_cap_formula_mismatch")
        else:
            blockers.append(f"top100[{index}].market_cap_unavailable")

        tracked = ((card.get("windows") or {}).get("30d") or {}).get("trackedSales")
        tracked_count = tracked.get("count") if isinstance(tracked, Mapping) else None
        tracked_value = tracked.get("valueUsd") if isinstance(tracked, Mapping) else None
        if (
            not isinstance(tracked, Mapping)
            or tracked.get("coverage") == "unavailable"
            or not isinstance(tracked_count, Mapping)
            or tracked_count.get("status") not in {"ready", "stale"}
            or not isinstance(tracked_count.get("value"), (int, float))
            or isinstance(tracked_count.get("value"), bool)
            or tracked_count.get("value") <= 0
            or not isinstance(tracked_value, Mapping)
            or tracked_value.get("status") not in {"ready", "stale"}
            or not isinstance(tracked_value.get("value"), (int, float))
            or isinstance(tracked_value.get("value"), bool)
            or tracked_value.get("value") <= 0
        ):
            blockers.append(f"top100[{index}].tracked_sales_30d_unqualified")

    for index, card in enumerate(watch):
        if not isinstance(card, Mapping):
            continue
        expected_rank = 101 + index
        if card.get("rank") != expected_rank or card.get("viewRank") != expected_rank:
            blockers.append(f"watchlist[{index}].view_rank_invalid")

    _validate_metric_times(snapshot, effective_at, "$", blockers)
    image_hashes = [_card_image_hash(card) for card in cards]
    if any(image_hash is None for image_hash in image_hashes):
        blockers.append("public_image_hash_invalid")
    valid_hashes = [image_hash for image_hash in image_hashes if image_hash is not None]
    if len(set(valid_hashes)) != len(valid_hashes):
        blockers.append("duplicate_public_image_sha256")
    return blockers, cards


def _validate_receipt(
    snapshot: Mapping[str, Any],
    receipt: Any,
    cards: list[Mapping[str, Any]],
) -> list[str]:
    blockers: list[str] = []
    if not isinstance(receipt, Mapping):
        return ["qc_receipt_not_object"]
    generation = snapshot.get("generation")
    coverage = snapshot.get("coverage")
    generation = generation if isinstance(generation, Mapping) else {}
    coverage = coverage if isinstance(coverage, Mapping) else {}

    if receipt.get("schemaVersion") != 1:
        blockers.append("qc_receipt_schema_invalid")
    if receipt.get("generationId") != generation.get("id"):
        blockers.append("qc_receipt_generation_mismatch")
    if _parse_time(receipt.get("checkedAt")) is None:
        blockers.append("qc_receipt_checkedAt_invalid")
    if receipt.get("status") != "passed":
        blockers.append("qc_receipt_not_passed")
    if receipt.get("claim") != coverage.get("claim"):
        blockers.append("qc_receipt_claim_mismatch")
    if receipt.get("requestedCount") != 100:
        blockers.append("qc_receipt_requested_count_mismatch")
    if receipt.get("verifiedCount") != len(snapshot.get("top100") or []):
        blockers.append("qc_receipt_verified_count_mismatch")
    if receipt.get("blockers") != []:
        blockers.append("qc_receipt_has_blockers")

    receipt_cards = receipt.get("cards")
    if not isinstance(receipt_cards, list) or len(receipt_cards) != len(cards):
        blockers.append("qc_receipt_card_set_mismatch")
        return blockers
    for index, (expected, actual) in enumerate(zip(cards, receipt_cards, strict=True)):
        expected_hash = _card_image_hash(expected)
        if (
            not isinstance(actual, Mapping)
            or actual.get("id") != expected.get("id")
            or actual.get("imageSha256") != expected_hash
        ):
            blockers.append(f"qc_receipt_cards[{index}]_identity_mismatch")
            continue
        if actual.get("decision") != "passed":
            blockers.append(f"qc_receipt_cards[{index}]_not_passed")
        evidence = actual.get("evidenceSha256")
        if not isinstance(evidence, str) or not HEX64.fullmatch(evidence):
            blockers.append(f"qc_receipt_cards[{index}]_evidence_invalid")
    return blockers


def _collect_assets(cards: list[Mapping[str, Any]], assets_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    entries: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for index, card in enumerate(cards):
        image = card.get("image")
        if not isinstance(image, Mapping):
            blockers.append(f"public_cards[{index}].image_missing")
            continue
        image_hash = _card_image_hash(card)
        if image_hash is None:
            blockers.append(f"public_cards[{index}].image_hash_invalid")
            continue
        if image.get("kind") != "raw_front":
            blockers.append(f"public_cards[{index}].image_not_raw_front")
        expected_src = f"/market-assets/{image_hash}.webp"
        if image.get("src") != expected_src:
            blockers.append(f"public_cards[{index}].image_path_hash_mismatch")
        variants = image.get("variants")
        if not isinstance(variants, Mapping):
            blockers.append(f"public_cards[{index}].image_variants_missing")
            variants = {}

        expected_files = [
            ("base", f"{image_hash}.webp", image.get("src")),
            ("200", f"{image_hash}_200.webp", variants.get("200")),
            ("600", f"{image_hash}_600.webp", variants.get("600")),
        ]
        for kind, filename, declared_path in expected_files:
            expected_path = f"/market-assets/{filename}"
            if declared_path != expected_path:
                blockers.append(f"public_cards[{index}].image_{kind}_path_invalid")
                continue
            if filename in entries:
                continue
            path = assets_root / filename
            try:
                payload = path.read_bytes()
            except OSError:
                blockers.append(f"asset_missing:{filename}")
                continue
            if not _looks_like_webp(payload):
                blockers.append(f"asset_not_webp:{filename}")
                continue
            content_sha256 = sha256_bytes(payload)
            if kind == "base" and content_sha256 != image_hash:
                blockers.append(f"asset_hash_mismatch:{filename}")
                continue
            entries[filename] = {
                "filename": filename,
                "sha256": content_sha256,
                "byteSize": len(payload),
                "baseHash": image_hash,
                "kind": kind,
                "source": path,
                "payload": payload,
            }
    return [entries[key] for key in sorted(entries)], blockers


def inspect_candidate(snapshot_path: Path, qc_receipt_path: Path, assets_root: Path) -> dict[str, Any]:
    snapshot_bytes = snapshot_path.read_bytes()
    receipt_bytes = qc_receipt_path.read_bytes()
    try:
        snapshot = json.loads(snapshot_bytes)
    except json.JSONDecodeError:
        snapshot = None
    try:
        receipt = json.loads(receipt_bytes)
    except json.JSONDecodeError:
        receipt = None

    receipt_sha256 = sha256_bytes(receipt_bytes)
    snapshot_blockers, cards = _validate_snapshot(snapshot, receipt_sha256)
    receipt_blockers = _validate_receipt(
        snapshot if isinstance(snapshot, Mapping) else {},
        receipt,
        cards,
    )
    assets, asset_blockers = _collect_assets(cards, assets_root)
    blockers = list(dict.fromkeys([*snapshot_blockers, *receipt_blockers, *asset_blockers]))
    generation = snapshot.get("generation") if isinstance(snapshot, Mapping) else {}
    coverage = snapshot.get("coverage") if isinstance(snapshot, Mapping) else {}
    return {
        "snapshot": snapshot,
        "snapshotBytes": snapshot_bytes,
        "snapshotFileSha256": sha256_bytes(snapshot_bytes),
        "receipt": receipt,
        "receiptBytes": receipt_bytes,
        "qcReceiptSha256": receipt_sha256,
        "generationId": generation.get("id") if isinstance(generation, Mapping) else None,
        "snapshotContentSha256": generation.get("contentSha256") if isinstance(generation, Mapping) else None,
        "coverage": {
            "claim": coverage.get("claim") if isinstance(coverage, Mapping) else None,
            "requestedCount": coverage.get("requestedCount") if isinstance(coverage, Mapping) else None,
            "verifiedCount": coverage.get("verifiedCount") if isinstance(coverage, Mapping) else None,
        },
        "assets": assets,
        "blockers": blockers,
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_immutable(path: Path, payload: bytes) -> None:
    if path.exists():
        if path.read_bytes() != payload:
            raise RuntimeError(f"immutable candidate mismatch: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(payload)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _candidate_report(inspection: Mapping[str, Any], *, built: bool) -> dict[str, Any]:
    assets = inspection["assets"]
    blockers = inspection["blockers"]
    return {
        "schemaVersion": 1,
        "status": "ready" if built and not blockers else "blocked",
        "built": built,
        "productionEligible": not blockers,
        "pointerPromoted": False,
        "generationId": inspection["generationId"],
        "snapshotContentSha256": inspection["snapshotContentSha256"],
        "snapshotFileSha256": inspection["snapshotFileSha256"],
        "qcReceiptSha256": inspection["qcReceiptSha256"],
        "coverage": inspection["coverage"],
        "media": {
            "fileCount": len(assets),
            "baseCount": sum(entry["kind"] == "base" for entry in assets),
            "files": [
                {
                    key: entry[key]
                    for key in ("filename", "sha256", "byteSize", "baseHash", "kind")
                }
                for entry in assets
            ],
        },
        "blockers": blockers,
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build an immutable, non-promoted publication candidate")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--qc-receipt", type=Path, required=True)
    parser.add_argument("--assets-root", type=Path, default=DEFAULT_ASSETS_ROOT)
    parser.add_argument("--out-root", type=Path, default=DEFAULT_OUT_ROOT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inspection = inspect_candidate(
        args.snapshot.resolve(),
        args.qc_receipt.resolve(),
        args.assets_root.resolve(),
    )
    generation_id = inspection["generationId"]
    safe_generation = (
        generation_id
        if isinstance(generation_id, str) and SAFE_GENERATION.fullmatch(generation_id)
        else f"blocked-{inspection['snapshotFileSha256'][:16]}"
    )

    if inspection["blockers"]:
        report = _candidate_report(inspection, built=False)
        report_bytes = _json_bytes(report)
        failed_id = sha256_bytes(report_bytes)[:16]
        candidate_dir = args.out_root.resolve() / "failed" / safe_generation / failed_id
        _write_immutable(candidate_dir / "candidate-report.json", report_bytes)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 2

    candidate_dir = args.out_root.resolve() / safe_generation
    report = _candidate_report(inspection, built=True)
    outputs: list[tuple[Path, bytes]] = [
        (candidate_dir / "snapshot.json", inspection["snapshotBytes"]),
        (candidate_dir / "qc-receipt.json", inspection["receiptBytes"]),
        (candidate_dir / "candidate-report.json", _json_bytes(report)),
    ]
    outputs.extend(
        (candidate_dir / "assets" / entry["filename"], entry["payload"])
        for entry in inspection["assets"]
    )
    for path, payload in outputs:
        if path.exists() and path.read_bytes() != payload:
            raise RuntimeError(f"immutable candidate mismatch: {path}")
    for path, payload in outputs:
        _write_immutable(path, payload)

    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
