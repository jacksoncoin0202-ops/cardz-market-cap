#!/usr/bin/env python3
"""Strict, generation-bound QC and finalization for public CARDZ snapshots.

The canonical exporter produces a candidate.  This module is the only bridge
from that candidate to a production-eligible ``Verified Top N`` snapshot:

    candidate snapshot -> immutable audit -> passed QC receipt -> final snapshot

An audit never edits the candidate.  Finalization only retains cards whose
automatic and human/vision image gates passed, binds the exact receipt bytes to
the generation, and recomputes the snapshot content hash.  It never writes a
runtime pointer.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image, UnidentifiedImageError


PIPELINES_DIR = Path(__file__).resolve().parent
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))

from canonical_public_snapshot import (
    printing_key,
    printing_key_sha256,
    qc_gate_release_image_receipts,
    snapshot_content_sha256,
)
from data_routing import DEFAULT_RELEASE_PROFILE, load_registry, load_release_profile
from sample_image_qc import SampleImageRejected, assert_raw_bytes_not_sample


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "manifests" / "image-qc.json"
DEFAULT_ASSETS = ROOT / "data" / "public" / "market-assets"
DEFAULT_ROUTING_CONFIG = ROOT / "config" / "data-routing.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
ALLOWED_IMAGE_SEMANTIC_STATUS = "human_or_vision_confirmed"
CANONICAL_DATABASE = "cardz_market_cap"
REQUESTED_COUNT = 100
PRICE_MAX_AGE_HOURS = 48
POPULATION_MAX_AGE_HOURS = 7 * 24
MIN_PURE_PSA10_SALES_30D = 10
MEDIA_SPECS = {
    "base": ("", None, None),
    "200": ("_200", 200, 280),
    "600": ("_600", 429, 600),
}


class SnapshotQcError(RuntimeError):
    """Raised when a candidate or audit cannot be safely finalized."""


def resolved_release_profile(
    release_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    if release_profile is None:
        # Direct unit callers retain strict historical expectations.  The CLI
        # always supplies the currently effective profile.
        return load_release_profile(load_registry(DEFAULT_ROUTING_CONFIG), "strict-v1")
    profile_id = str(release_profile.get("releaseProfile") or "").strip()
    policy = release_profile.get("policy")
    policy_sha256 = str(release_profile.get("policySha256") or "").strip().casefold()
    if not profile_id or not isinstance(policy, Mapping) or not SHA256_RE.fullmatch(policy_sha256):
        raise SnapshotQcError("release profile envelope is invalid")
    return {
        "releaseProfile": profile_id,
        "policy": dict(policy),
        "policySha256": policy_sha256,
    }


def _policy_int(policy: Mapping[str, Any], key: str) -> int:
    value = policy.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SnapshotQcError(f"release profile has invalid {key}")
    return value


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonical_json_bytes(value: Any, *, pretty: bool = False) -> bytes:
    if pretty:
        text = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return text.encode("utf-8") + b"\n"


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def receipt_snapshot_content_sha256(snapshot: Mapping[str, Any]) -> str:
    """Non-circular final-snapshot facts hash stored inside the QC receipt."""

    payload = copy.deepcopy(dict(snapshot))
    generation = dict(payload.get("generation") or {})
    generation["contentSha256"] = ""
    generation["qcReceiptSha256"] = ""
    payload["generation"] = generation
    return snapshot_content_sha256(payload)


def atomic_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def read_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise SnapshotQcError(f"{label} is missing or invalid: {path}") from error
    if not isinstance(value, dict):
        raise SnapshotQcError(f"{label} is not an object: {path}")
    return value


def read_mapping_bytes(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        payload = path.read_bytes()
        value = json.loads(payload.decode("utf-8-sig"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        raise SnapshotQcError(f"{label} is missing or invalid: {path}") from error
    if not isinstance(value, dict):
        raise SnapshotQcError(f"{label} is not an object: {path}")
    return value, payload


def validate_db_qc_receipt(
    receipt: Mapping[str, Any],
    receipt_bytes: bytes,
    *,
    expected_run_id: str | None = None,
    expected_release_profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate and reduce the canonical DB receipt to its public binding."""

    run_id = str(receipt.get("runId") or "")
    universe_sha256 = str(receipt.get("universeCandidateSha256") or "")
    report_sha256 = str(receipt.get("reportSha256") or "")
    counts = receipt.get("counts")
    release_gate = receipt.get("releaseGate")
    if receipt.get("schemaVersion") != 1:
        raise SnapshotQcError("canonical DB QC receipt schema is invalid")
    if not RUN_ID_RE.fullmatch(run_id):
        raise SnapshotQcError("canonical DB QC receipt runId is invalid")
    if expected_run_id is not None and run_id != expected_run_id:
        raise SnapshotQcError("canonical DB QC receipt runId does not match the pipeline run")
    if parse_time(receipt.get("asOf")) is None:
        raise SnapshotQcError("canonical DB QC receipt asOf is invalid")
    if receipt.get("status") != "passed":
        raise SnapshotQcError("canonical DB QC receipt status is not passed")
    if receipt.get("readOnly") is not True:
        raise SnapshotQcError("canonical DB QC receipt is not read-only evidence")
    if receipt.get("database") != CANONICAL_DATABASE:
        raise SnapshotQcError("canonical DB QC receipt database is invalid")
    release: dict[str, Any] | None = None
    if expected_release_profile is not None:
        release = resolved_release_profile(expected_release_profile)
        if (
            receipt.get("releaseProfile") != release["releaseProfile"]
            or receipt.get("policySha256") != release["policySha256"]
        ):
            raise SnapshotQcError("canonical DB QC receipt release profile/hash mismatch")
        if not SHA256_RE.fullmatch(str(receipt.get("databaseFingerprint") or "").casefold()):
            raise SnapshotQcError("canonical DB QC receipt database fingerprint is invalid")
        if not isinstance(receipt.get("evaluationId"), int) or isinstance(receipt.get("evaluationId"), bool):
            raise SnapshotQcError("canonical DB QC receipt evaluation ID is invalid")
    if not SHA256_RE.fullmatch(universe_sha256):
        raise SnapshotQcError("canonical DB QC receipt universe binding is invalid")
    if not SHA256_RE.fullmatch(report_sha256):
        raise SnapshotQcError("canonical DB QC receipt report hash is invalid")
    if not isinstance(counts, Mapping):
        raise SnapshotQcError("canonical DB QC receipt counts are invalid")
    required_counts = (
        "qualified",
        "monitoring",
        "releaseReadyQualified",
        "releaseBlockedQualified",
    )
    if any(
        not isinstance(counts.get(field), int)
        or isinstance(counts.get(field), bool)
        or int(counts[field]) < 0
        for field in required_counts
    ):
        raise SnapshotQcError("canonical DB QC receipt counts are invalid")
    if not isinstance(release_gate, Mapping) or release_gate.get("eligible") is not True:
        raise SnapshotQcError("canonical DB QC receipt release gate is not passed")
    if release is None:
        if (
            counts["releaseBlockedQualified"] != 0
            or counts["releaseReadyQualified"] != counts["qualified"]
            or release_gate.get("blockerCount") != 0
            or release_gate.get("blockerCardCount") != 0
            or release_gate.get("blockers") != {}
        ):
            raise SnapshotQcError("canonical DB QC receipt qualified universe is not release ready")
    else:
        policy = release["policy"]
        if not isinstance(policy, Mapping):
            raise SnapshotQcError("release profile policy is invalid")
        minimum = _policy_int(policy, "minimumVerifiedCount")
        if (
            release_gate.get("globalBlockers") != []
            or not isinstance(release_gate.get("eligibleCardCount"), int)
            or release_gate["eligibleCardCount"] < minimum
        ):
            raise SnapshotQcError("canonical DB QC receipt relaxed eligibility is insufficient")
        if release["releaseProfile"] == "relaxed-launch-v1" and release_gate.get("perCardExclude") is not True:
            raise SnapshotQcError("relaxed DB QC receipt is not card-scoped")
    binding: dict[str, Any] = {
        "runId": run_id,
        "receiptSha256": sha256_bytes(receipt_bytes),
        "database": CANONICAL_DATABASE,
        "universeCandidateSha256": universe_sha256,
    }
    if release is not None:
        binding.update(
            {
                "releaseProfile": release["releaseProfile"],
                "policySha256": release["policySha256"],
                "databaseFingerprint": str(receipt["databaseFingerprint"]),
                "evaluationId": int(receipt["evaluationId"]),
            }
        )
    return binding


def snapshot_cards(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    top = snapshot.get("top100")
    watch = snapshot.get("watchlist")
    if not isinstance(top, list) or not isinstance(watch, list):
        raise SnapshotQcError("snapshot top100/watchlist arrays are invalid")
    cards = [*top, *watch]
    if not cards or any(not isinstance(card, Mapping) for card in cards):
        raise SnapshotQcError("snapshot has no valid public candidates")
    ids = [str(card.get("id") or "") for card in cards]
    if any(not card_id for card_id in ids) or len(set(ids)) != len(ids):
        raise SnapshotQcError("snapshot card IDs are missing or duplicated")
    return cards


def media_binding(
    cards: Iterable[Mapping[str, Any]],
    assets_root: Path,
) -> dict[str, Any]:
    """Bind every referenced master and derivative by bytes and dimensions."""

    bound: dict[str, dict[str, Any]] = {}
    for card in cards:
        card_id = str(card.get("id") or "")
        image = card.get("image")
        if not isinstance(image, Mapping):
            raise SnapshotQcError(f"public image is missing for {card_id}")
        base_sha256 = str(image.get("sha256") or "")
        if not SHA256_RE.fullmatch(base_sha256):
            raise SnapshotQcError(f"public image hash is invalid for {card_id}")
        expected_base = f"/market-assets/{base_sha256}.webp"
        if image.get("src") != expected_base:
            raise SnapshotQcError(f"public image path/hash mismatch for {card_id}")
        variants = image.get("variants")
        if not isinstance(variants, Mapping):
            raise SnapshotQcError(f"public image derivatives are missing for {card_id}")
        for variant, (suffix, expected_width, expected_height) in MEDIA_SPECS.items():
            if variant == "base":
                expected_width = image.get("width")
                expected_height = image.get("height")
                if (
                    not isinstance(expected_width, int)
                    or isinstance(expected_width, bool)
                    or expected_width <= 0
                    or not isinstance(expected_height, int)
                    or isinstance(expected_height, bool)
                    or expected_height <= 0
                ):
                    raise SnapshotQcError(f"public image declared dimensions are invalid for {card_id}")
            filename = f"{base_sha256}{suffix}.webp"
            public_path = f"/market-assets/{filename}"
            if variant != "base" and variants.get(variant) != public_path:
                raise SnapshotQcError(
                    f"public image {variant}px derivative path is invalid for {card_id}"
                )
            asset_path = assets_root / filename
            if not asset_path.is_file():
                raise SnapshotQcError(f"public image asset is missing: {filename}")
            payload = asset_path.read_bytes()
            content_sha256 = sha256_bytes(payload)
            if variant == "base" and content_sha256 != base_sha256:
                raise SnapshotQcError(f"public image master hash is invalid: {filename}")
            try:
                with Image.open(asset_path) as opened:
                    opened.load()
                    image_format = opened.format
                    width, height = opened.size
            except (OSError, UnidentifiedImageError) as error:
                raise SnapshotQcError(f"public image asset is not a valid WEBP: {filename}") from error
            if image_format != "WEBP":
                raise SnapshotQcError(f"public image asset is not WEBP: {filename}")
            if (width, height) != (expected_width, expected_height):
                raise SnapshotQcError(
                    f"public image {variant} dimensions are invalid: "
                    f"{filename} is {width}x{height}, expected {expected_width}x{expected_height}"
                )
            entry = {
                "key": f"market-assets/{filename}",
                "sha256": content_sha256,
                "width": width,
                "height": height,
                "variant": variant,
                "baseSha256": base_sha256,
            }
            previous = bound.get(entry["key"])
            if previous is not None and previous != entry:
                raise SnapshotQcError(f"public image binding conflicts: {filename}")
            bound[entry["key"]] = entry
    return {
        "prefix": "market-assets/",
        "assets": [bound[key] for key in sorted(bound)],
    }


def metric_time_blockers(
    value: Any,
    *,
    effective_at: datetime,
    path: str,
) -> list[str]:
    """Reject every future ``asOf``/history timestamp in a card payload."""

    blockers: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            nested_path = f"{path}.{key}" if path else str(key)
            if key in {"asOf", "at", "anchorAt"} and nested is not None:
                observed = parse_time(nested)
                if observed is None:
                    blockers.append(f"invalid_metric_time:{nested_path}")
                elif observed > effective_at:
                    blockers.append(f"future_metric:{nested_path}")
            else:
                blockers.extend(
                    metric_time_blockers(nested, effective_at=effective_at, path=nested_path)
                )
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            blockers.extend(
                metric_time_blockers(
                    nested,
                    effective_at=effective_at,
                    path=f"{path}[{index}]",
                )
            )
    return blockers


def metric_age_hours(metric: Any, effective_at: datetime) -> float | None:
    if not isinstance(metric, Mapping):
        return None
    observed = parse_time(metric.get("asOf"))
    if observed is None:
        return None
    return (effective_at - observed).total_seconds() / 3600


def manifest_records(
    document: Mapping[str, Any],
) -> tuple[dict[tuple[str, str], Mapping[str, Any]], dict[str, list[Mapping[str, Any]]]]:
    raw = document.get("records")
    if not isinstance(raw, list):
        raise SnapshotQcError("image QC manifest has no records array")
    by_binding: dict[tuple[str, str], Mapping[str, Any]] = {}
    by_hash: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for record in raw:
        if not isinstance(record, Mapping):
            continue
        content_hash = str(record.get("contentSha256") or "")
        public_id = str(record.get("publicId") or "")
        if not SHA256_RE.fullmatch(content_hash) or not public_id:
            continue
        binding = (public_id, content_hash)
        if binding in by_binding:
            raise SnapshotQcError(
                f"image QC manifest repeats card/hash binding: {public_id}:{content_hash}"
            )
        by_binding[binding] = record
        by_hash[content_hash].append(record)
    return by_binding, by_hash


def image_path(card: Mapping[str, Any], assets_root: Path) -> Path | None:
    image = card.get("image")
    if not isinstance(image, Mapping):
        return None
    source = str(image.get("src") or "")
    expected_hash = str(image.get("sha256") or "")
    match = re.fullmatch(r"/market-assets/([0-9a-f]{64})\.webp", source)
    if match is None or match.group(1) != expected_hash:
        return None
    return assets_root / f"{expected_hash}.webp"


def image_blockers(
    card: Mapping[str, Any],
    *,
    records: Mapping[tuple[str, str], Mapping[str, Any]],
    assets_root: Path,
    release_profile_id: str = "strict-v1",
    release_image_receipts: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
    card_id = str(card.get("id") or "")
    image = card.get("image")
    if not isinstance(image, Mapping):
        return ["image_missing"]
    image_hash = str(image.get("sha256") or "")
    if not SHA256_RE.fullmatch(image_hash):
        return ["image_hash_invalid"]
    path = image_path(card, assets_root)
    if path is None:
        return ["image_path_hash_mismatch"]
    if not path.is_file():
        return ["image_asset_missing"]
    if sha256_file(path) != image_hash:
        return ["image_asset_hash_mismatch"]
    if release_profile_id == "relaxed-launch-v1":
        receipt = (release_image_receipts or {}).get(card_id)
        chosen = receipt.get("chosen") if isinstance(receipt, Mapping) else None
        if not isinstance(chosen, Mapping):
            return ["image_release_receipt_missing"]
        if (
            receipt.get("releaseProfile") != release_profile_id
            or str(receipt.get("chosenContentSha256") or "") != image_hash
            or str(chosen.get("contentSha256") or "") != image_hash
            or chosen.get("hardBlockers") != []
        ):
            return ["image_release_receipt_mismatch"]
        try:
            assert_raw_bytes_not_sample(path.read_bytes(), context=card_id)
        except SampleImageRejected:
            return ["image_sample_or_placeholder"]
        except Exception:
            return ["image_sample_scan_failed"]
        return []
    record = records.get((card_id, image_hash))
    if record is None:
        return ["image_qc_card_id_mismatch"]
    blockers: list[str] = []
    if not bool(record.get("publicAllowed")):
        blockers.append("image_not_public_allowed")
    if str(record.get("imageKind") or "") != "raw_front":
        blockers.append("image_not_raw_front")
    if str(record.get("semanticMatchStatus") or "") != ALLOWED_IMAGE_SEMANTIC_STATUS:
        blockers.append("image_not_human_or_vision_confirmed")
    for field in ("cardNumberMatch", "languageMatch", "tcgMatch"):
        if record.get(field) is not True:
            blockers.append(f"image_{field}_failed")
    resolver_evidence = record.get("resolverEvidence")
    if not isinstance(resolver_evidence, Mapping):
        blockers.append("image_resolver_evidence_missing")
    else:
        if not SHA256_RE.fullmatch(
            str(resolver_evidence.get("sourceContentSha256") or "")
        ):
            blockers.append("image_resolver_source_hash_invalid")
        for field in ("collectorMatch", "languageMetadataMatch", "tcgMetadataMatch"):
            if resolver_evidence.get(field) is not True:
                blockers.append(f"image_resolver_{field}_failed")
    # OCR is deliberately expensive.  A card already rejected by an exact
    # binding/semantic gate cannot enter the finalized snapshot, so defer the
    # SAMPLE scan until every cheaper image gate passes. Confirmed cards still
    # receive the full fail-closed scan.
    if not blockers:
        try:
            assert_raw_bytes_not_sample(path.read_bytes(), context=card_id)
        except SampleImageRejected:
            blockers.append("image_sample_or_placeholder")
        except Exception:
            blockers.append("image_sample_scan_failed")
    return blockers


def card_fact_blockers(
    card: Mapping[str, Any],
    effective_at: datetime,
    *,
    release_profile_id: str = "strict-v1",
    policy: Mapping[str, Any] | None = None,
) -> list[str]:
    blockers: list[str] = []
    allowed_identity_statuses = (
        policy.get("allowedIdentityStatuses")
        if isinstance(policy, Mapping)
        else ["confirmed"]
    )
    if (
        not isinstance(allowed_identity_statuses, list)
        or card.get("identityStatus") not in allowed_identity_statuses
    ):
        blockers.append("identity_not_confirmed")
    collector = card.get("collectorNumber")
    if (
        not isinstance(collector, Mapping)
        or collector.get("complete") is not True
        or not str(collector.get("display") or "").strip()
    ):
        blockers.append("canonical_identity_incomplete")
    if not str(card.get("tcg") or "").strip():
        blockers.append("canonical_identity_incomplete")

    printing_identity = card.get("printingIdentity")
    if not isinstance(printing_identity, Mapping):
        blockers.append("canonical_printing_identity_missing")
    else:
        key = printing_key(
            card.get("tcg"),
            printing_identity.get("setName"),
            printing_identity.get("collectorNumber"),
            printing_identity.get("editionCode"),
            printing_identity.get("parallelCode"),
            printing_identity.get("finishCode"),
            language=(
                printing_identity.get("cardLanguage")
                or card.get("cardLanguage")
                or ""
            ),
        )
        canonical_hash = str(
            printing_identity.get("canonicalPrintingSha256") or ""
        ).strip().casefold()
        evidence_hash = str(printing_identity.get("evidenceSha256") or "").strip().casefold()
        if any(not part for part in key):
            blockers.append("canonical_printing_identity_incomplete")
        if (
            not SHA256_RE.fullmatch(canonical_hash)
            or canonical_hash != printing_key_sha256(key)
        ):
            blockers.append("canonical_printing_identity_hash_mismatch")
        if not SHA256_RE.fullmatch(evidence_hash):
            blockers.append("canonical_printing_evidence_invalid")
        card_set = card.get("sets")
        set_name = card_set.get("en") if isinstance(card_set, Mapping) else None
        if str(set_name or "").strip().casefold() != key[2]:
            blockers.append("canonical_printing_set_mismatch")
        normalized_collector = (
            collector.get("normalized") if isinstance(collector, Mapping) else None
        )
        if str(normalized_collector or "").strip().casefold() != key[3]:
            blockers.append("canonical_printing_collector_mismatch")

    population = card.get("populationPsa10")
    population_value = population.get("value") if isinstance(population, Mapping) else None
    if (
        not isinstance(population_value, int)
        or isinstance(population_value, bool)
        or population_value < 1000
        or not isinstance(population, Mapping)
        or population.get("estimated") is not False
        or population.get("status") not in {"ready", "stale"}
    ):
        blockers.append("population_not_exact_gemrate_psa10_1000")
    population_age = metric_age_hours(population, effective_at)
    if population_age is None or not 0 <= population_age <= POPULATION_MAX_AGE_HOURS:
        blockers.append("population_stale_or_undated")

    price = card.get("pricePsa10")
    price_value = price.get("value") if isinstance(price, Mapping) else None
    if (
        not isinstance(price_value, (int, float))
        or isinstance(price_value, bool)
        or price_value <= 0
        or not isinstance(price, Mapping)
        or price.get("status") not in {"ready", "stale"}
    ):
        blockers.append("price_not_exact_psa10")
    price_age = metric_age_hours(price, effective_at)
    price_max_age_hours = (
        _policy_int(policy, "lastGoodMaximumHours")
        if isinstance(policy, Mapping)
        else PRICE_MAX_AGE_HOURS
    )
    if price_age is None or not 0 <= price_age <= price_max_age_hours:
        blockers.append("price_stale_or_undated")

    market_cap = card.get("marketCap")
    market_cap_value = market_cap.get("value") if isinstance(market_cap, Mapping) else None
    if (
        isinstance(price_value, (int, float))
        and not isinstance(price_value, bool)
        and isinstance(population_value, int)
        and not isinstance(population_value, bool)
        and isinstance(market_cap_value, (int, float))
        and not isinstance(market_cap_value, bool)
    ):
        expected = float(price_value) * population_value
        if abs(float(market_cap_value) - expected) >= 0.01:
            blockers.append("market_cap_formula_mismatch")
    else:
        blockers.append("market_cap_formula_unverifiable")

    windows = card.get("windows")
    thirty = windows.get("30d") if isinstance(windows, Mapping) else None
    tracked = thirty.get("trackedSales") if isinstance(thirty, Mapping) else None
    count = tracked.get("count") if isinstance(tracked, Mapping) else None
    count_value = count.get("value") if isinstance(count, Mapping) else None
    if (
        not isinstance(count_value, int)
        or isinstance(count_value, bool)
        or count_value <= 0
        or not isinstance(count, Mapping)
        or count.get("status") not in {"ready", "stale"}
        or not isinstance(tracked, Mapping)
        or tracked.get("coverage") not in {"partial", "stale"}
    ):
        blockers.append("psa10_tracked_sales_30d_missing")
    elif count_value < (
        _policy_int(policy, "trackedPsa10Sales30dMinimumInclusive")
        if isinstance(policy, Mapping)
        else MIN_PURE_PSA10_SALES_30D
    ):
        blockers.append("psa10_tracked_sales_30d_insufficient")

    blockers.extend(metric_time_blockers(card, effective_at=effective_at, path="card"))
    return blockers


def audit_snapshot(
    snapshot: Mapping[str, Any],
    manifest: Mapping[str, Any],
    assets_root: Path,
    *,
    db_qc_receipt: Mapping[str, Any],
    db_qc_receipt_bytes: bytes,
    checked_at: datetime | None = None,
    run_id: str | None = None,
    release_profile: Mapping[str, Any] | None = None,
    release_image_receipts: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    release = resolved_release_profile(release_profile)
    profile_bound = release_profile is not None
    release_profile_id = str(release["releaseProfile"])
    policy = release["policy"]
    if not isinstance(policy, Mapping):
        raise SnapshotQcError("release profile policy is invalid")
    db_qc = validate_db_qc_receipt(
        db_qc_receipt,
        db_qc_receipt_bytes,
        expected_run_id=run_id,
        expected_release_profile=release if profile_bound else None,
    )
    generation = snapshot.get("generation")
    if not isinstance(generation, Mapping):
        raise SnapshotQcError("snapshot generation is invalid")
    generation_id = str(generation.get("id") or "")
    effective_at = parse_time(generation.get("effectiveAt"))
    generated_at = parse_time(generation.get("generatedAt"))
    if not generation_id or effective_at is None or generated_at is None:
        raise SnapshotQcError("snapshot generation ID/timestamps are invalid")
    if profile_bound and (
        generation.get("releaseProfile") != release_profile_id
        or generation.get("policySha256") != release["policySha256"]
        or generation.get("dbFingerprint") != db_qc.get("databaseFingerprint")
        or generation.get("evaluationId") != db_qc.get("evaluationId")
    ):
        raise SnapshotQcError("snapshot generation release binding does not match DB QC")
    global_blockers: list[str] = []
    if effective_at > generated_at:
        global_blockers.append("generation_effective_after_generated")
    expected_content_hash = snapshot_content_sha256(snapshot)
    declared_content_hash = str(generation.get("contentSha256") or "")
    if declared_content_hash != expected_content_hash:
        global_blockers.append("snapshot_content_hash_mismatch")

    cards = snapshot_cards(snapshot)
    by_binding, _by_hash = manifest_records(manifest)
    image_owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for card in cards:
        image = card.get("image")
        image_hash = str(image.get("sha256") or "") if isinstance(image, Mapping) else ""
        image_owners[image_hash].append((str(card.get("id") or ""), str(card.get("tcg") or "")))
    duplicate_hashes = {
        image_hash: owners
        for image_hash, owners in image_owners.items()
        if image_hash and len(owners) > 1
    }

    audit_cards: list[dict[str, Any]] = []
    rejection_counts: Counter[str] = Counter()
    for position, card in enumerate(cards, start=1):
        card_id = str(card.get("id") or "")
        image = card.get("image")
        image_hash = str(image.get("sha256") or "") if isinstance(image, Mapping) else ""
        blockers = [
            *card_fact_blockers(
                card,
                effective_at,
                release_profile_id=release_profile_id,
                policy=policy if profile_bound else None,
            ),
            *image_blockers(
                card,
                records=by_binding,
                assets_root=assets_root,
                release_profile_id=release_profile_id,
                release_image_receipts=release_image_receipts,
            ),
        ]
        if image_hash in duplicate_hashes:
            owners = duplicate_hashes[image_hash]
            blockers.append(
                "cross_tcg_duplicate_image"
                if len({tcg for _owner, tcg in owners}) > 1
                else "unapproved_duplicate_image"
            )
        blockers = sorted(set(blockers))
        rejection_counts.update(blockers)
        evidence = {
            "generationId": generation_id,
            "releaseProfile": release_profile_id,
            "policySha256": release["policySha256"],
            "snapshotContentSha256": declared_content_hash,
            "dbQcReceiptSha256": db_qc["receiptSha256"],
            "cardId": card_id,
            "marketRank": card.get("marketRank", card.get("rank", position)),
            "imageSha256": image_hash,
            "blockers": blockers,
        }
        audit_cards.append(
            {
                "id": card_id,
                "marketRank": evidence["marketRank"],
                "imageSha256": image_hash,
                "decision": "passed" if not blockers and not global_blockers else "failed",
                "evidenceSha256": sha256_bytes(canonical_json_bytes(evidence)),
                "blockers": blockers,
            }
        )
    passed = [card for card in audit_cards if card["decision"] == "passed"]
    checked = checked_at or utc_now()
    minimum_verified = _policy_int(policy, "minimumVerifiedCount") if profile_bound else 1
    audit_passed = (
        len(passed) >= minimum_verified
        if release_profile_id == "relaxed-launch-v1" and profile_bound
        else bool(passed)
    )
    return {
        "schemaVersion": 1,
        "runId": db_qc["runId"],
        "releaseProfile": release_profile_id,
        "policySha256": release["policySha256"],
        "databaseFingerprint": db_qc.get("databaseFingerprint"),
        "evaluationId": db_qc.get("evaluationId"),
        "generationId": generation_id,
        "dbQc": db_qc,
        "checkedAt": iso_utc(checked),
        "snapshotContentSha256": declared_content_hash,
        "status": "passed" if audit_passed and not global_blockers else "failed",
        "requestedCount": REQUESTED_COUNT,
        "minimumVerifiedCount": minimum_verified,
        "candidateCount": len(cards),
        "verifiedCount": len(passed),
        "cards": audit_cards,
        "blockers": sorted(set(global_blockers)),
        "rejectionCounts": dict(sorted(rejection_counts.items())),
        "duplicateImageGroups": len(duplicate_hashes),
    }


def count_ready(cards: Iterable[Mapping[str, Any]], *path: str) -> int:
    count = 0
    for card in cards:
        value: Any = card
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        if isinstance(value, Mapping) and value.get("status") == "ready":
            count += 1
    return count


def refresh_coverage(
    snapshot: dict[str, Any],
    cards: list[dict[str, Any]],
    *,
    release_profile_id: str = "strict-v1",
) -> None:
    coverage = snapshot.setdefault("coverage", {})
    top_count = len(snapshot["top100"])
    coverage.update(
        {
            "claim": "verified-top-100" if top_count == REQUESTED_COUNT else "verified-top-n",
            "requestedCount": REQUESTED_COUNT,
            "verifiedCount": len(cards) if release_profile_id == "relaxed-launch-v1" else top_count,
            "top100Count": top_count,
            "watchlistCount": len(snapshot["watchlist"]),
            "publicTop300Count": len(cards),
            "publicCardCount": len(cards),
            "completeIdentityCount": sum(
                bool((card.get("collectorNumber") or {}).get("complete")) for card in cards
            ),
            "changeReady": {
                window: count_ready(cards, "windows", window, "changePct")
                for window in ("1d", "7d", "30d")
            },
            "salesReady": {
                window: sum(
                    (card.get("windows") or {}).get(window, {}).get("trackedSales", {}).get("coverage")
                    in {"partial", "stale"}
                    for card in cards
                )
                for window in ("1d", "7d", "30d")
            },
            "graderPopulationReady": {
                grader: count_ready(cards, "graderPopulations", grader, "topGradePopulation")
                for grader in ("PSA", "BGS", "CGC", "SGC", "TAG")
            },
            "graderPopulationChangeReady": {
                grader: {
                    window: count_ready(
                        cards,
                        "graderPopulations",
                        grader,
                        "topGradePopulationChangePct",
                        window,
                    )
                    for window in ("1d", "7d", "30d")
                }
                for grader in ("PSA", "BGS", "CGC", "SGC", "TAG")
            },
        }
    )


def finalize_snapshot(
    candidate: Mapping[str, Any],
    audit: Mapping[str, Any],
    assets_root: Path,
    *,
    db_qc_receipt: Mapping[str, Any],
    db_qc_receipt_bytes: bytes,
    release_profile: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], bytes]:
    release = resolved_release_profile(release_profile)
    profile_bound = release_profile is not None
    release_profile_id = str(release["releaseProfile"])
    policy = release["policy"]
    if not isinstance(policy, Mapping):
        raise SnapshotQcError("release profile policy is invalid")
    db_qc = validate_db_qc_receipt(
        db_qc_receipt,
        db_qc_receipt_bytes,
        expected_run_id=str(audit.get("runId") or ""),
        expected_release_profile=release if profile_bound else None,
    )
    generation = candidate.get("generation")
    if not isinstance(generation, Mapping):
        raise SnapshotQcError("candidate generation is invalid")
    generation_id = str(generation.get("id") or "")
    content_hash = str(generation.get("contentSha256") or "")
    if profile_bound and (
        generation.get("releaseProfile") != release_profile_id
        or generation.get("policySha256") != release["policySha256"]
        or generation.get("dbFingerprint") != db_qc.get("databaseFingerprint")
        or generation.get("evaluationId") != db_qc.get("evaluationId")
    ):
        raise SnapshotQcError("candidate generation release binding is invalid")
    if audit.get("schemaVersion") != 1:
        raise SnapshotQcError("QC audit schema is invalid")
    if audit.get("dbQc") != db_qc:
        raise SnapshotQcError("QC audit canonical DB receipt binding is invalid")
    if (
        audit.get("releaseProfile") != release_profile_id
        or audit.get("policySha256") != release["policySha256"]
    ):
        raise SnapshotQcError("QC audit release profile/hash binding is invalid")
    if audit.get("status") != "passed":
        raise SnapshotQcError("QC audit status is not passed")
    if parse_time(audit.get("checkedAt")) is None:
        raise SnapshotQcError("QC audit checkedAt is invalid")
    if audit.get("blockers") != []:
        raise SnapshotQcError("QC audit has global blockers")
    if audit.get("generationId") != generation_id:
        raise SnapshotQcError("QC audit generation does not match candidate")
    if audit.get("snapshotContentSha256") != content_hash:
        raise SnapshotQcError("QC audit content hash does not match candidate")
    if content_hash != snapshot_content_sha256(candidate):
        raise SnapshotQcError("candidate content hash is invalid")
    candidate_cards = snapshot_cards(candidate)
    if audit.get("requestedCount") != REQUESTED_COUNT:
        raise SnapshotQcError("QC audit requestedCount is invalid")
    minimum_verified = _policy_int(policy, "minimumVerifiedCount") if profile_bound else 1
    if audit.get("minimumVerifiedCount") != minimum_verified:
        raise SnapshotQcError("QC audit minimum verified count is invalid")
    if audit.get("candidateCount") != len(candidate_cards):
        raise SnapshotQcError("QC audit candidateCount does not match candidate")
    raw_audit_cards = audit.get("cards")
    if not isinstance(raw_audit_cards, list):
        raise SnapshotQcError("QC audit has no card decisions")
    if len(raw_audit_cards) != len(candidate_cards):
        raise SnapshotQcError("QC audit decisions do not exactly cover candidate cards")

    approved: list[dict[str, Any]] = []
    receipt_cards: list[dict[str, Any]] = []
    passed_count = 0
    for position, (source, row) in enumerate(
        zip(candidate_cards, raw_audit_cards, strict=True),
        start=1,
    ):
        if not isinstance(row, Mapping):
            raise SnapshotQcError("QC audit card decision is invalid")
        card_id = str(source["id"])
        image = source.get("image")
        image_hash = str(image.get("sha256") or "") if isinstance(image, Mapping) else ""
        market_rank = source.get("marketRank", source.get("rank", position))
        if row.get("id") != card_id:
            raise SnapshotQcError("QC audit card order/identity does not match candidate")
        if row.get("marketRank") != market_rank:
            raise SnapshotQcError("QC audit market rank does not match candidate")
        if row.get("imageSha256") != image_hash:
            raise SnapshotQcError("QC audit image hash does not match candidate")
        decision = row.get("decision")
        if decision not in {"passed", "failed"}:
            raise SnapshotQcError("QC audit card decision is invalid")
        if decision == "failed":
            continue
        if row.get("blockers") != []:
            raise SnapshotQcError("passed QC audit decision has blockers")
        expected_evidence = {
            "generationId": generation_id,
            "releaseProfile": release_profile_id,
            "policySha256": release["policySha256"],
            "snapshotContentSha256": content_hash,
            "dbQcReceiptSha256": db_qc["receiptSha256"],
            "cardId": card_id,
            "marketRank": market_rank,
            "imageSha256": image_hash,
            "blockers": [],
        }
        expected_evidence_hash = sha256_bytes(canonical_json_bytes(expected_evidence))
        if row.get("evidenceSha256") != expected_evidence_hash:
            raise SnapshotQcError("passed QC audit evidence hash is invalid")
        passed_count += 1
        approved.append(copy.deepcopy(dict(source)))
        receipt_cards.append(
            {
                "id": card_id,
                "imageSha256": image_hash,
                "decision": "passed",
                "evidenceSha256": expected_evidence_hash,
            }
        )
    if audit.get("verifiedCount") != passed_count:
        raise SnapshotQcError("QC audit verifiedCount does not equal passed decisions")
    if not approved:
        raise SnapshotQcError("no card passed strict QC; failed audit is preserved and no final snapshot was written")
    if release_profile_id == "relaxed-launch-v1" and profile_bound and len(approved) < minimum_verified:
        raise SnapshotQcError("relaxed QC approved fewer cards than its minimum release count")
    card_capacity = _policy_int(policy, "publicCardsMaximum")
    if len(approved) > card_capacity:
        raise SnapshotQcError("public card capacity exceeded; refusing to truncate the generation")

    final = copy.deepcopy(dict(candidate))
    for position, card in enumerate(approved, start=1):
        card["marketRank"] = int(card.get("marketRank") or card.get("rank") or position)
        card["viewRank"] = position
        card["rank"] = position
    final["top100"] = approved[:REQUESTED_COUNT]
    final["watchlist"] = approved[REQUESTED_COUNT:]
    refresh_coverage(final, approved, release_profile_id=release_profile_id)
    claim = str(final["coverage"]["claim"])
    media = media_binding(approved, assets_root)
    asset_capacity = _policy_int(policy, "publicImageAssetsMaximum")
    if len(media["assets"]) > asset_capacity:
        raise SnapshotQcError("public image asset capacity exceeded; refusing to truncate the generation")
    media_by_base: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for asset in media["assets"]:
        media_by_base[str(asset["baseSha256"])][str(asset["variant"])] = {
            "key": asset["key"],
            "sha256": asset["sha256"],
            "width": asset["width"],
            "height": asset["height"],
        }
    for card in receipt_cards:
        card["media"] = media_by_base[str(card["imageSha256"])]
    final_generation = dict(final["generation"])
    final_generation.update(
        {
            "mode": "production",
            "productionEligible": True,
            "blockers": [],
            "dbQc": db_qc,
            "releaseProfile": release_profile_id,
            "policySha256": release["policySha256"],
            "dbFingerprint": db_qc.get("databaseFingerprint"),
            "evaluationId": db_qc.get("evaluationId"),
            "qcReceiptSha256": "",
            "contentSha256": "",
        }
    )
    final["generation"] = final_generation
    receipt = {
        "schemaVersion": 1,
        "runId": db_qc["runId"],
        "releaseProfile": release_profile_id,
        "policySha256": release["policySha256"],
        "databaseFingerprint": db_qc.get("databaseFingerprint"),
        "evaluationId": db_qc.get("evaluationId"),
        "generationId": generation_id,
        "snapshotContentSha256": receipt_snapshot_content_sha256(final),
        "dbQc": db_qc,
        "dbQcReceiptSha256": db_qc["receiptSha256"],
        "universeCandidateSha256": db_qc["universeCandidateSha256"],
        "checkedAt": str(audit.get("checkedAt") or ""),
        "status": "passed",
        "claim": claim,
        "requestedCount": REQUESTED_COUNT,
        "verifiedCount": len(approved) if release_profile_id == "relaxed-launch-v1" else len(final["top100"]),
        "cards": receipt_cards,
        "media": media,
        "blockers": [],
    }
    receipt_bytes = canonical_json_bytes(receipt, pretty=True)
    final["generation"]["qcReceiptSha256"] = sha256_bytes(receipt_bytes)
    final["generation"]["contentSha256"] = snapshot_content_sha256(final)
    return final, receipt_bytes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--snapshot", type=Path, required=True)
    audit_parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    audit_parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    audit_parser.add_argument("--output", type=Path, required=True)
    audit_parser.add_argument("--run-id")
    audit_parser.add_argument("--db-qc-receipt", type=Path, required=True)
    audit_parser.add_argument("--db-qc-report", type=Path)
    audit_parser.add_argument("--routing-config", type=Path, default=DEFAULT_ROUTING_CONFIG)
    audit_parser.add_argument("--release-profile", default=DEFAULT_RELEASE_PROFILE)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--snapshot", type=Path, required=True)
    finalize_parser.add_argument("--audit", type=Path, required=True)
    finalize_parser.add_argument("--output", type=Path, required=True)
    finalize_parser.add_argument("--receipt", type=Path, required=True)
    finalize_parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    finalize_parser.add_argument("--db-qc-receipt", type=Path, required=True)
    finalize_parser.add_argument("--routing-config", type=Path, default=DEFAULT_ROUTING_CONFIG)
    finalize_parser.add_argument("--release-profile", default=DEFAULT_RELEASE_PROFILE)
    args = parser.parse_args()
    release = load_release_profile(
        load_registry(args.routing_config.resolve()), args.release_profile
    )

    if args.command == "audit":
        snapshot = read_mapping(args.snapshot.resolve(), "candidate snapshot")
        manifest = read_mapping(args.manifest.resolve(), "image QC manifest")
        db_qc_receipt, db_qc_receipt_bytes = read_mapping_bytes(
            args.db_qc_receipt.resolve(),
            "canonical DB QC receipt",
        )
        image_receipts: dict[str, Mapping[str, Any]] | None = None
        if release["releaseProfile"] == "relaxed-launch-v1":
            if args.db_qc_report is None:
                raise SnapshotQcError("relaxed audit requires --db-qc-report for immutable image receipts")
            image_receipts = qc_gate_release_image_receipts(
                args.db_qc_report.resolve(), release
            )
        report = audit_snapshot(
            snapshot,
            manifest,
            args.assets.resolve(),
            db_qc_receipt=db_qc_receipt,
            db_qc_receipt_bytes=db_qc_receipt_bytes,
            run_id=args.run_id,
            release_profile=release,
            release_image_receipts=image_receipts,
        )
        atomic_bytes(args.output.resolve(), canonical_json_bytes(report, pretty=True))
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "releaseProfile": report["releaseProfile"],
                    "policySha256": report["policySha256"],
                    "generationId": report["generationId"],
                    "candidateCount": report["candidateCount"],
                    "verifiedCount": report["verifiedCount"],
                    "rejectionCounts": report["rejectionCounts"],
                    "output": str(args.output.resolve()),
                },
                sort_keys=True,
            )
        )
        return 0 if report["status"] == "passed" else 1

    candidate = read_mapping(args.snapshot.resolve(), "candidate snapshot")
    audit = read_mapping(args.audit.resolve(), "QC audit")
    db_qc_receipt, db_qc_receipt_bytes = read_mapping_bytes(
        args.db_qc_receipt.resolve(),
        "canonical DB QC receipt",
    )
    final, receipt_bytes = finalize_snapshot(
        candidate,
        audit,
        args.assets.resolve(),
        db_qc_receipt=db_qc_receipt,
        db_qc_receipt_bytes=db_qc_receipt_bytes,
        release_profile=release,
    )
    atomic_bytes(args.receipt.resolve(), receipt_bytes)
    atomic_bytes(args.output.resolve(), canonical_json_bytes(final, pretty=True))
    print(
        json.dumps(
            {
                "status": "passed",
                "releaseProfile": final["generation"]["releaseProfile"],
                "generationId": final["generation"]["id"],
                "claim": final["coverage"]["claim"],
                "verifiedCount": final["coverage"]["verifiedCount"],
                "receiptSha256": final["generation"]["qcReceiptSha256"],
                "output": str(args.output.resolve()),
                "receipt": str(args.receipt.resolve()),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SnapshotQcError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
