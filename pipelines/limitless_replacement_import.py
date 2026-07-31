#!/usr/bin/env python3
"""Register the fixed wave-3 replacement cohort as private image evidence.

The command is deliberately pinned to one immutable manifest and its 133-row
failure ledger.  Dry-run is the default.  ``--write`` may only copy the 13
ready candidate files into ``private-source-map`` and insert private
``raw_front`` asset/QC rows.  It never writes a source pointer, public mirror,
snapshot, deployment, or approval.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

import db_runtime  # noqa: E402
from sample_image_qc import SampleImageRejected, assert_raw_source_not_sample  # noqa: E402


LIMITLESS_ROOT = (
    ROOT
    / "data/runtime/private-reports/pm-image-remediation-20260731/limitless"
)
WAVE2_ROOT = LIMITLESS_ROOT / "wave2"
CLAIM_ROOT = LIMITLESS_ROOT / "wave3"
SOURCE_MEDIA_ROOT = WAVE2_ROOT / "media"
MANIFEST_PATH = CLAIM_ROOT / "replacement-manifest-wave3.json"
FAILURE_LEDGER_PATH = CLAIM_ROOT / "failure-ledger-wave3.json"
DURABLE_ROOT = ROOT / "data/runtime/private-source-map/image-replacements/limitless-wave3"

MANIFEST_SHA256 = "84f4c738077c20657491a7e0c2674f7d8dc15f9ebdaf9bc777e3e7abd0a76be8"
FAILURE_LEDGER_SHA256 = "e60babdd34e016c243d914b8baad642a27061d0059148be246077f47b42efbff"
WAVE2_MANIFEST_SHA256 = "3dd9e28b286b5efadfd51d6dbd77cb2e1e9271e5c48e1d3920a94b3229d2cb4f"
WAVE2_FAILURE_LEDGER_SHA256 = "d7696cc98f9fc28b43cfd46c3a6eb84ccb9cb4d9d5054c4c7ffa7822a4fa1381"
EXPECTED_COUNTS = {"ready": 13, "review": 10, "reject": 15, "unresolved": 108, "total": 146}
FAILURE_COUNT = 133
BLACKLISTED_SOURCE_FAMILY = "limitless-one-piece-en"
BLACKLISTED_SOURCE_REASON = "known_sample_source_limitless_one_piece_en"
EXPECTED_LANGUAGE_COUNTS = {"en": 130, "ja": 16}
KNOWN_SAMPLE_SHA256 = "4a53529faf845d82b7c06ab04e9fa161792b2d2d9c1e9bee01b594cfef3895a6"
FOREGROUND_REVIEW_SHA256 = "f3889ce1ca16f8ea36b2ecf5339a0b926ef5e623bdac7921703c8a2709e76b4b"
FOREGROUND_ALPHA_THRESHOLD = 8
FOREGROUND_ASPECT_MINIMUM = 0.68
FOREGROUND_ASPECT_MAXIMUM = 0.75
FOREGROUND_MAJOR_COMPONENT_MINIMUM_PIXELS = 64
SIX_PART_FIELDS = ("game", "language", "set", "collector", "parallel", "finish")
ALLOWED_SOURCES = {"snkrdunk", "pricecharting"}
SOURCE_HOSTS = {
    "snkrdunk": "snkrdunk.com",
    "pricecharting": "pricecharting.com",
}
FORMAT_META = {
    "JPEG": ("image/jpeg", "jpg"),
    "PNG": ("image/png", "png"),
    "WEBP": ("image/webp", "webp"),
}
IMAGE_KIND = "raw_front"
QC_VERSION = "limitless-wave3-v1"
SEMANTIC_PENDING = "replacement_pending_human_or_vision"


class ImportContractError(ValueError):
    """The immutable private-import contract failed closed."""


@dataclass(frozen=True)
class CandidatePlan:
    variant_id: int
    source: str
    source_id: str
    source_url: str
    source_path: Path
    content_sha256: str
    byte_count: int
    width_px: int
    height_px: int
    image_format: str
    image_mode: str
    mime_type: str
    extension: str
    source_version_sha256: str
    captured_at: datetime
    target: Mapping[str, Any]
    source_binding_evidence_sha256: str
    foreground_bbox: tuple[int, int, int, int]
    foreground_width_px: int
    foreground_height_px: int
    foreground_aspect_ratio: float
    major_component_count: int


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _required_sha(value: Any, field: str) -> str:
    text = str(value or "").strip().casefold()
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        raise ImportContractError(f"invalid_{field}")
    return text


def _inside_existing(path: Path, root: Path, label: str) -> Path:
    try:
        resolved_root = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        resolved.relative_to(resolved_root)
    except (FileNotFoundError, ValueError) as exc:
        raise ImportContractError(f"{label}_outside_registered_claim:{path}") from exc
    if path.is_symlink():
        raise ImportContractError(f"{label}_symlink_not_allowed:{path}")
    return resolved


def _inside_destination(path: Path, root: Path) -> Path:
    resolved_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    try:
        resolved.relative_to(resolved_root)
    except ValueError as exc:
        raise ImportContractError("durable_destination_outside_private_source_map") from exc
    return resolved


def _read_hashed_json(
    path: Path, expected_sha256: str, claim_root: Path, label: str
) -> dict[str, Any]:
    resolved = _inside_existing(path, claim_root, label)
    raw = resolved.read_bytes()
    if sha256_bytes(raw) != _required_sha(expected_sha256, f"{label}_sha256"):
        raise ImportContractError(f"{label}_sha256_mismatch")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImportContractError(f"{label}_invalid_json") from exc
    if not isinstance(document, dict):
        raise ImportContractError(f"{label}_not_object")
    return document


def _required_text(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ImportContractError(f"missing_{field}")
    return text


def _normal(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _parse_generated_at(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(_required_text(value, "generated_at").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ImportContractError("generated_at_invalid") from exc
    if parsed.tzinfo is None:
        raise ImportContractError("generated_at_missing_timezone")
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _major_components(
    mask: Image.Image, *, origin_x: int, origin_y: int
) -> list[dict[str, Any]]:
    """Return 8-connected foreground components large enough to be meaningful."""

    width, height = mask.size
    pixels = bytearray(mask.tobytes())
    components: list[dict[str, Any]] = []
    for start in range(len(pixels)):
        if pixels[start] == 0:
            continue
        pixels[start] = 0
        stack = [start]
        area = 0
        min_x = width
        min_y = height
        max_x = -1
        max_y = -1
        while stack:
            index = stack.pop()
            area += 1
            x = index % width
            y = index // width
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
            neighbours: list[int] = []
            if x:
                neighbours.append(index - 1)
            if x + 1 < width:
                neighbours.append(index + 1)
            if index >= width:
                neighbours.append(index - width)
                if x:
                    neighbours.append(index - width - 1)
                if x + 1 < width:
                    neighbours.append(index - width + 1)
            if index + width < len(pixels):
                neighbours.append(index + width)
                if x:
                    neighbours.append(index + width - 1)
                if x + 1 < width:
                    neighbours.append(index + width + 1)
            for neighbour in neighbours:
                if pixels[neighbour]:
                    pixels[neighbour] = 0
                    stack.append(neighbour)
        if area >= FOREGROUND_MAJOR_COMPONENT_MINIMUM_PIXELS:
            components.append(
                {
                    "areaPx": area,
                    "bbox": (
                        min_x + origin_x,
                        min_y + origin_y,
                        max_x + origin_x + 1,
                        max_y + origin_y + 1,
                    ),
                }
            )
    return sorted(components, key=lambda item: int(item["areaPx"]), reverse=True)


def inspect_foreground(image: Image.Image) -> dict[str, Any]:
    """Measure the visible card foreground, not the transparent source canvas."""

    if "A" in image.getbands():
        alpha = image.getchannel("A")
        mask = alpha.point(
            lambda value: 255 if value > FOREGROUND_ALPHA_THRESHOLD else 0,
            mode="L",
        )
        bbox = mask.getbbox()
        if bbox is None:
            raise ImportContractError("foreground_alpha_empty")
        cropped = mask.crop(bbox)
        components = _major_components(
            cropped, origin_x=int(bbox[0]), origin_y=int(bbox[1])
        )
    else:
        bbox = (0, 0, image.width, image.height)
        components = [
            {
                "areaPx": image.width * image.height,
                "bbox": bbox,
            }
        ]
    width_px = bbox[2] - bbox[0]
    height_px = bbox[3] - bbox[1]
    if width_px <= 0 or height_px <= 0:
        raise ImportContractError("foreground_dimensions_invalid")
    return {
        "bbox": tuple(int(value) for value in bbox),
        "widthPx": width_px,
        "heightPx": height_px,
        "aspectRatio": round(width_px / height_px, 6),
        "majorComponentCount": len(components),
        "majorComponentPixels": [int(item["areaPx"]) for item in components],
        "majorComponents": components,
    }


def require_foreground_pass(variant_id: int, metrics: Mapping[str, Any]) -> None:
    ratio = float(metrics["aspectRatio"])
    if not FOREGROUND_ASPECT_MINIMUM <= ratio <= FOREGROUND_ASPECT_MAXIMUM:
        raise ImportContractError(f"foreground_aspect_rejected:{variant_id}:{ratio}")
    if int(metrics["majorComponentCount"]) != 1:
        raise ImportContractError(
            f"foreground_disconnected_artifact:{variant_id}:"
            f"{metrics['majorComponentCount']}"
        )


def _validate_bucket_ids(manifest: Mapping[str, Any]) -> dict[str, set[int]]:
    bucket_ids: dict[str, set[int]] = {}
    all_ids: set[int] = set()
    for bucket in ("ready", "review", "reject", "unresolved"):
        records = manifest.get(bucket)
        if not isinstance(records, list) or len(records) != EXPECTED_COUNTS[bucket]:
            raise ImportContractError(f"manifest_{bucket}_count_invalid")
        current: set[int] = set()
        for record in records:
            if not isinstance(record, dict) or record.get("status") != bucket:
                raise ImportContractError(f"manifest_{bucket}_record_invalid")
            try:
                variant_id = int(record["variantId"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ImportContractError(f"manifest_{bucket}_variant_invalid") from exc
            if variant_id <= 0 or variant_id in current or variant_id in all_ids:
                raise ImportContractError("manifest_variant_ids_not_unique")
            target = record.get("target")
            if not isinstance(target, dict) or int(target.get("variantId") or 0) != variant_id:
                raise ImportContractError(f"manifest_variant_target_mismatch:{variant_id}")
            current.add(variant_id)
            all_ids.add(variant_id)
        bucket_ids[bucket] = current
    if len(all_ids) != EXPECTED_COUNTS["total"]:
        raise ImportContractError("manifest_total_variant_count_invalid")
    return bucket_ids


def _validate_failure_ledger(
    manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
    bucket_ids: Mapping[str, set[int]],
) -> None:
    if ledger.get("schemaVersion") != 3:
        raise ImportContractError("failure_ledger_schema_invalid")
    if ledger.get("generatedAt") != manifest.get("generatedAt"):
        raise ImportContractError("failure_ledger_generation_drift")
    summary = ledger.get("summary")
    if not isinstance(summary, dict) or any(
        int(summary.get(key) or -1) != expected
        for key, expected in {
            "entries": FAILURE_COUNT,
            "review": EXPECTED_COUNTS["review"],
            "reject": EXPECTED_COUNTS["reject"],
            "unresolved": EXPECTED_COUNTS["unresolved"],
        }.items()
    ):
        raise ImportContractError("failure_ledger_summary_invalid")
    entries = ledger.get("entries")
    if not isinstance(entries, list) or len(entries) != FAILURE_COUNT:
        raise ImportContractError("failure_ledger_entry_count_invalid")
    severity_ids: dict[str, set[int]] = {
        "review_required": set(),
        "candidate_rejected": set(),
        "unresolved": set(),
    }
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("severity") not in severity_ids:
            raise ImportContractError("failure_ledger_entry_invalid")
        try:
            variant_id = int(entry["variantId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ImportContractError("failure_ledger_variant_invalid") from exc
        severity = str(entry["severity"])
        if variant_id in severity_ids[severity]:
            raise ImportContractError("failure_ledger_variant_not_unique")
        severity_ids[severity].add(variant_id)
    expected = {
        "review_required": bucket_ids["review"],
        "candidate_rejected": bucket_ids["reject"],
        "unresolved": bucket_ids["unresolved"],
    }
    if severity_ids != expected:
        raise ImportContractError("failure_ledger_does_not_equal_non_ready_cohort")
    failure_ids = set().union(*severity_ids.values())
    if failure_ids & bucket_ids["ready"] or len(failure_ids) != FAILURE_COUNT:
        raise ImportContractError("failure_ledger_contains_ready_variant")


def _explicit_english_signal(candidate: Mapping[str, Any]) -> bool:
    title = _normal(candidate.get("title"))
    filename = PurePosixPath(urlparse(str(candidate.get("sourceUrl") or "")).path).name.casefold()
    return (
        "[en]" in title
        or "(en)" in title
        or " english " in f" {title} "
        or "-en-" in f"-{filename}-"
        or "_en_" in f"_{filename}_"
    )


def validate_blacklist_and_language_contract(
    manifest: Mapping[str, Any],
) -> dict[str, int]:
    policy = manifest.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("forbiddenFamilies") != [BLACKLISTED_SOURCE_FAMILY, "SAMPLE"]
    ):
        raise ImportContractError("limitless_source_family_blacklist_drift")
    records = [
        record
        for bucket in ("ready", "review", "reject", "unresolved")
        for record in manifest[bucket]
    ]
    languages = Counter()
    blacklist_count = 0
    ja_candidate_checks = 0
    ready_languages = Counter()
    for record in records:
        variant_id = int(record["variantId"])
        target = record["target"]
        language = str(target.get("language") or "")
        if target.get("game") != "one-piece" or language not in EXPECTED_LANGUAGE_COUNTS:
            raise ImportContractError(f"cohort_game_or_language_drift:{variant_id}")
        languages[language] += 1
        if record["status"] == "ready":
            ready_languages[language] += 1
        wave1 = target.get("wave1Target")
        if (
            not isinstance(wave1, dict)
            or wave1.get("rejectDecision") != "reject_known_sample_source"
            or wave1.get("rejectReason") != BLACKLISTED_SOURCE_REASON
            or wave1.get("tcg") != "one-piece"
            or wave1.get("language") != language
        ):
            raise ImportContractError(f"limitless_family_blacklist_drift:{variant_id}")
        blacklist_count += 1
        if language == "ja":
            candidate = record.get("selectedCandidate")
            if not isinstance(candidate, dict):
                raise ImportContractError(f"expected_ja_candidate_missing:{variant_id}")
            language_qc = (candidate.get("sixPartQc") or {}).get("language")
            if (
                not isinstance(language_qc, dict)
                or language_qc.get("status") != "pass"
                or _normal(language_qc.get("expected")) != "ja"
                or _normal(language_qc.get("observed")) != "ja"
                or _explicit_english_signal(candidate)
            ):
                raise ImportContractError(f"expected_ja_has_english_drift:{variant_id}")
            ja_candidate_checks += 1
    if dict(sorted(languages.items())) != EXPECTED_LANGUAGE_COUNTS:
        raise ImportContractError("cohort_language_counts_drift")
    if blacklist_count != EXPECTED_COUNTS["total"] or ja_candidate_checks != 16:
        raise ImportContractError("cohort_blacklist_or_ja_check_count_drift")
    return {
        "blacklisted": blacklist_count,
        "expectedEn": languages["en"],
        "expectedJa": languages["ja"],
        "jaCandidateChecks": ja_candidate_checks,
        "jaEnglishSignals": 0,
        "readyEn": ready_languages["en"],
        "readyJa": ready_languages["ja"],
    }


def _validate_six_part_evidence(
    variant_id: int, target: Mapping[str, Any], evidence: Any
) -> None:
    if not isinstance(evidence, dict) or set(evidence) != set(SIX_PART_FIELDS):
        raise ImportContractError(f"six_part_evidence_shape_invalid:{variant_id}")
    for field in SIX_PART_FIELDS:
        item = evidence[field]
        if not isinstance(item, dict) or item.get("status") != "pass":
            raise ImportContractError(f"six_part_not_pass:{variant_id}:{field}")
        if not _required_text(item.get("method"), f"six_part_{field}_method"):
            raise ImportContractError(f"six_part_method_missing:{variant_id}:{field}")
        observed = item.get("observed")
        if observed is None or observed == "" or observed == []:
            raise ImportContractError(f"six_part_observed_missing:{variant_id}:{field}")
        if field == "language" and _normal(observed) != _normal(target[field]):
            raise ImportContractError(f"six_part_language_observed_drift:{variant_id}")
        expected = item.get("expected")
        expected_values = expected if isinstance(expected, list) else [expected]
        if _normal(target[field]) not in {_normal(value) for value in expected_values}:
            raise ImportContractError(f"six_part_expected_target_drift:{variant_id}:{field}")


def _candidate_source_path(
    local_path: Any, *, repo_root: Path, claim_media_root: Path, variant_id: int
) -> Path:
    text = _required_text(local_path, "candidate_local_path")
    pure = PurePosixPath(text)
    if pure.is_absolute() or ".." in pure.parts or "\\" in text:
        raise ImportContractError(f"candidate_path_invalid:{variant_id}")
    candidate = repo_root.joinpath(*pure.parts)
    return _inside_existing(candidate, claim_media_root, f"candidate_{variant_id}")


def _record_for(
    manifest: Mapping[str, Any], bucket: str, variant_id: int
) -> Mapping[str, Any]:
    matches = [
        record
        for record in manifest[bucket]
        if int(record.get("variantId") or 0) == variant_id
    ]
    if len(matches) != 1:
        raise ImportContractError(f"wave3_reclassification_missing:{bucket}:{variant_id}")
    return matches[0]


def validate_wave3_visual_contract(
    manifest: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    manifest_sha256: str,
    repo_root: Path,
    source_media_root: Path,
    wave2_manifest_path: Path,
    wave2_failure_ledger_path: Path,
    wave2_manifest_sha256: str,
    wave2_failure_ledger_sha256: str,
) -> None:
    if sha256_file(_inside_existing(
        wave2_manifest_path, wave2_manifest_path.parent, "wave2_manifest"
    )) != wave2_manifest_sha256:
        raise ImportContractError("wave2_manifest_sha256_drift")
    if sha256_file(_inside_existing(
        wave2_failure_ledger_path,
        wave2_failure_ledger_path.parent,
        "wave2_failure_ledger",
    )) != wave2_failure_ledger_sha256:
        raise ImportContractError("wave2_failure_ledger_sha256_drift")
    if manifest.get("wave2Base") != {
        "manifestPath": "../wave2/replacement-manifest-wave2.json",
        "manifestSha256": wave2_manifest_sha256,
        "failureLedgerPath": "../wave2/failure-ledger.json",
        "failureLedgerSha256": wave2_failure_ledger_sha256,
    }:
        raise ImportContractError("wave3_base_lineage_drift")
    if ledger.get("wave2BaseFailureLedger") != {
        "path": "../wave2/failure-ledger.json",
        "sha256": wave2_failure_ledger_sha256,
        "entries": 131,
    }:
        raise ImportContractError("wave3_failure_base_lineage_drift")
    if ledger.get("wave3Manifest") != {
        "path": "replacement-manifest-wave3.json",
        "sha256": manifest_sha256,
    }:
        raise ImportContractError("wave3_failure_manifest_binding_drift")

    policy = manifest.get("policy") or {}
    expected_foreground_policy = {
        "alphaThreshold": FOREGROUND_ALPHA_THRESHOLD,
        "aspectRatioMinimum": FOREGROUND_ASPECT_MINIMUM,
        "aspectRatioMaximum": FOREGROUND_ASPECT_MAXIMUM,
        "majorComponentMinimumPixels": FOREGROUND_MAJOR_COMPONENT_MINIMUM_PIXELS,
        "requiredMajorComponentCount": 1,
        "opaqueImageUsesFullFrame": True,
        "disconnectedArtifact": (
            "reject_unless_new_deterministic_normalized_candidate_is_fully_evidenced"
        ),
    }
    if (
        policy.get("knownSampleContentSha256") != [KNOWN_SAMPLE_SHA256]
        or policy.get("foregroundGate") != expected_foreground_policy
    ):
        raise ImportContractError("wave3_visual_policy_drift")
    ready_ids = [int(record["variantId"]) for record in manifest["ready"]]
    if manifest.get("readyVariantIds") != ready_ids or {1718, 1741} & set(ready_ids):
        raise ImportContractError("wave3_ready_variant_ids_drift")

    sample_record = _record_for(manifest, "reject", 1741)
    sample_candidate = sample_record.get("selectedCandidate") or {}
    sample_media = sample_candidate.get("mediaQc") or {}
    sample_visual = sample_media.get("visualSampleQc") or {}
    known_sample = manifest.get("knownSampleReject") or {}
    if (
        sample_record.get("decisionReason") != "known_sample_visual_confirmed"
        or sample_candidate.get("decision") != "reject"
        or sample_candidate.get("integrationState") != "not_ready"
        or sample_media.get("contentSha256") != KNOWN_SAMPLE_SHA256
        or sample_media.get("sampleOcrStatus") != "clean_false_negative"
        or sample_visual.get("status") != "reject_known_sample_visual_confirmed"
        or sample_visual.get("contentSha256") != KNOWN_SAMPLE_SHA256
        or sample_visual.get("visibleToken") != "SAMPLE"
        or known_sample.get("variantId") != 1741
        or known_sample.get("contentSha256") != KNOWN_SAMPLE_SHA256
        or known_sample.get("decision") != "reject_known_sample_visual_confirmed"
        or known_sample.get("visibleToken") != "SAMPLE"
    ):
        raise ImportContractError("wave3_known_sample_evidence_drift")
    sample_path = _candidate_source_path(
        sample_candidate.get("localPath"),
        repo_root=repo_root,
        claim_media_root=source_media_root,
        variant_id=1741,
    )
    if sha256_file(sample_path) != KNOWN_SAMPLE_SHA256:
        raise ImportContractError("wave3_known_sample_bytes_drift")

    foreground_record = _record_for(manifest, "review", 1718)
    foreground_candidate = foreground_record.get("selectedCandidate") or {}
    foreground_media = foreground_candidate.get("mediaQc") or {}
    foreground_evidence = foreground_media.get("foregroundQc") or {}
    foreground_review = manifest.get("foregroundReview") or {}
    if (
        foreground_candidate.get("decision") != "review"
        or foreground_candidate.get("integrationState") != "not_ready"
        or foreground_media.get("contentSha256") != FOREGROUND_REVIEW_SHA256
        or foreground_evidence.get("status") != "reject_disconnected_artifact"
        or foreground_review.get("variantId") != 1718
        or foreground_review.get("contentSha256") != FOREGROUND_REVIEW_SHA256
        or foreground_review.get("decision") != "review_disconnected_alpha_artifact"
    ):
        raise ImportContractError("wave3_foreground_review_evidence_drift")
    foreground_path = _candidate_source_path(
        foreground_candidate.get("localPath"),
        repo_root=repo_root,
        claim_media_root=source_media_root,
        variant_id=1718,
    )
    if sha256_file(foreground_path) != FOREGROUND_REVIEW_SHA256:
        raise ImportContractError("wave3_foreground_review_bytes_drift")
    with Image.open(foreground_path) as opened:
        opened.load()
        measured = inspect_foreground(opened)
    expected_measured = {
        "bbox": tuple(foreground_review["alphaForegroundBbox"]),
        "widthPx": int(foreground_review["alphaForegroundDimensions"][0]),
        "heightPx": int(foreground_review["alphaForegroundDimensions"][1]),
        "aspectRatio": float(foreground_review["alphaForegroundAspectRatio"]),
        "majorComponentCount": int(foreground_review["majorComponentCount"]),
        "majorComponentPixels": list(foreground_review["majorComponentPixels"]),
        "majorComponents": [
            {
                "areaPx": int(component["areaPx"]),
                "bbox": tuple(component["bbox"]),
            }
            for component in foreground_review["majorComponents"]
        ],
    }
    if measured != expected_measured:
        raise ImportContractError("wave3_foreground_review_measurement_drift")
    if (
        FOREGROUND_ASPECT_MINIMUM
        <= measured["aspectRatio"]
        <= FOREGROUND_ASPECT_MAXIMUM
        and measured["majorComponentCount"] == 1
    ):
        raise ImportContractError("wave3_foreground_review_no_longer_fails_gate")

    added_entries = {
        int(entry.get("variantId") or 0): entry
        for entry in ledger["entries"]
        if int(entry.get("variantId") or 0) in {1718, 1741}
    }
    if set(added_entries) != {1718, 1741}:
        raise ImportContractError("wave3_failure_reclassification_entries_missing")
    if (
        added_entries[1718].get("severity") != "review_required"
        or (added_entries[1718].get("evidence") or {}).get("contentSha256")
        != FOREGROUND_REVIEW_SHA256
        or added_entries[1741].get("severity") != "candidate_rejected"
        or added_entries[1741].get("reason") != "known_sample_visual_confirmed"
        or (added_entries[1741].get("evidence") or {}).get("contentSha256")
        != KNOWN_SAMPLE_SHA256
    ):
        raise ImportContractError("wave3_failure_reclassification_evidence_drift")


def _validate_source(
    variant_id: int, candidate: Mapping[str, Any], media_qc: Mapping[str, Any]
) -> tuple[str, str, str]:
    source = _required_text(candidate.get("source"), "candidate_source").casefold()
    source_id = _required_text(candidate.get("sourceId"), "candidate_source_id")
    source_url = _required_text(candidate.get("sourceUrl"), "candidate_source_url")
    policy = media_qc.get("sampleSourcePolicy")
    if not isinstance(policy, dict):
        raise ImportContractError(f"candidate_source_policy_missing:{variant_id}")
    policy_family = _normal(policy.get("family"))
    policy_reason = _normal(policy.get("reason"))
    policy_rule = _normal(policy.get("ruleId"))
    if (
        "limitless" in source
        or "limitless" in source_url.casefold()
        or "limitless" in policy_family
        or "limitless" in policy_reason
        or "limitless" in policy_rule
        or policy.get("status") == "reject"
        or "sample" in policy_family
    ):
        raise ImportContractError(f"known_limitless_or_sample_source_rejected:{variant_id}")
    if source not in ALLOWED_SOURCES:
        raise ImportContractError(f"candidate_source_not_registered:{variant_id}:{source}")
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").casefold()
    expected_host = SOURCE_HOSTS[source]
    if parsed.scheme not in {"http", "https"} or not (
        host == expected_host or host.endswith(f".{expected_host}")
    ):
        raise ImportContractError(f"candidate_source_host_invalid:{variant_id}")
    if policy.get("policyId") != "cardz-source-sample-v1":
        raise ImportContractError(f"candidate_source_policy_version_invalid:{variant_id}")
    return source, source_id, source_url


def _validate_ready_candidate(
    record: Mapping[str, Any],
    *,
    repo_root: Path,
    claim_media_root: Path,
    manifest_sha256: str,
    captured_at: datetime,
    sample_gate: Callable[..., Mapping[str, object]],
) -> CandidatePlan:
    variant_id = int(record["variantId"])
    target = record["target"]
    required_target = {
        "game", "language", "set", "collector", "edition", "parallel", "finish",
        "canonicalPrintingSha256", "printingEvidenceSha256", "cardId", "assetId",
    }
    if not required_target.issubset(target) or any(
        target.get(field) in (None, "") for field in required_target
    ):
        raise ImportContractError(f"candidate_target_incomplete:{variant_id}")
    if (
        target.get("canonicalComplete") is not True
        or target.get("printingIdentityStatus") != "canonical"
    ):
        raise ImportContractError(f"candidate_target_not_canonical:{variant_id}")
    _required_sha(target["canonicalPrintingSha256"], "canonical_printing_sha256")
    _required_sha(target["printingEvidenceSha256"], "printing_evidence_sha256")

    candidate = record.get("selectedCandidate")
    if not isinstance(candidate, dict):
        raise ImportContractError(f"selected_candidate_missing:{variant_id}")
    if (
        candidate.get("decision") != "ready"
        or candidate.get("semanticState") != "pass"
        or candidate.get("acquisition")
        not in {"private_copy", "live_snk_master_image"}
        or candidate.get("integrationState")
        not in {
            "source_ready_for_registered_normalization",
            "direct_front_candidate_private_only",
        }
    ):
        raise ImportContractError(f"selected_candidate_not_ready:{variant_id}")
    _validate_six_part_evidence(variant_id, target, candidate.get("sixPartQc"))

    source_binding = candidate.get("sourceIdentityBinding")
    if (
        not isinstance(source_binding, dict)
        or source_binding.get("matchStatus") != "exact"
    ):
        raise ImportContractError(f"candidate_source_binding_not_exact:{variant_id}")
    binding_evidence = _required_sha(
        source_binding.get("evidenceSha256"), "source_binding_evidence_sha256"
    )

    media_qc = candidate.get("mediaQc")
    if not isinstance(media_qc, dict):
        raise ImportContractError(f"candidate_media_qc_missing:{variant_id}")
    if (
        media_qc.get("decodeStatus") != "pass"
        or media_qc.get("sampleOcrStatus") != "clean"
    ):
        raise ImportContractError(f"candidate_media_qc_not_clean:{variant_id}")
    source, source_id, source_url = _validate_source(variant_id, candidate, media_qc)
    source_path = _candidate_source_path(
        candidate.get("localPath"),
        repo_root=repo_root,
        claim_media_root=claim_media_root,
        variant_id=variant_id,
    )
    raw = source_path.read_bytes()
    content_sha256 = _required_sha(media_qc.get("contentSha256"), "content_sha256")
    if sha256_bytes(raw) != content_sha256:
        raise ImportContractError(f"candidate_content_sha256_mismatch:{variant_id}")
    if content_sha256 == KNOWN_SAMPLE_SHA256:
        raise ImportContractError(f"known_sample_content_sha256_rejected:{variant_id}")
    try:
        byte_count = int(media_qc["bytes"])
        expected_width = int(media_qc["widthPx"])
        expected_height = int(media_qc["heightPx"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportContractError(f"candidate_media_dimensions_invalid:{variant_id}") from exc
    if len(raw) != byte_count or byte_count <= 0:
        raise ImportContractError(f"candidate_byte_count_mismatch:{variant_id}")
    try:
        with Image.open(source_path) as opened:
            opened.load()
            width_px, height_px = opened.size
            image_format = str(opened.format or "")
            image_mode = str(opened.mode)
            foreground = inspect_foreground(opened)
    except Exception as exc:  # Pillow exposes several decode exception classes.
        raise ImportContractError(f"candidate_decode_failed:{variant_id}") from exc
    if (
        (width_px, height_px) != (expected_width, expected_height)
        or width_px <= 0
        or height_px <= 0
        or image_format != media_qc.get("format")
        or image_mode != media_qc.get("mode")
        or image_format not in FORMAT_META
    ):
        raise ImportContractError(f"candidate_media_probe_drift:{variant_id}")
    try:
        expected_ratio = float(media_qc["aspectRatio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ImportContractError(f"candidate_aspect_ratio_invalid:{variant_id}") from exc
    if round(width_px / height_px, 6) != round(expected_ratio, 6):
        raise ImportContractError(f"candidate_aspect_ratio_drift:{variant_id}")
    require_foreground_pass(variant_id, foreground)
    try:
        live_policy = sample_gate(
            raw,
            source_path=source_url,
            tcg_code=str(target["game"]),
            context=f"limitless_replacement_import:{variant_id}",
        )
    except SampleImageRejected as exc:
        raise ImportContractError(f"candidate_sample_rejected:{variant_id}") from exc
    if live_policy.get("status") == "reject":
        raise ImportContractError(f"candidate_live_source_policy_rejected:{variant_id}")

    mime_type, extension = FORMAT_META[image_format]
    source_version_sha256 = sha256_bytes(
        canonical_json(
            {
                "manifestSha256": manifest_sha256,
                "variantId": variant_id,
                "target": {field: target[field] for field in SIX_PART_FIELDS},
                "canonicalPrintingSha256": target["canonicalPrintingSha256"],
                "printingEvidenceSha256": target["printingEvidenceSha256"],
                "candidate": {
                    "source": source,
                    "sourceId": source_id,
                    "sourceUrl": source_url,
                    "sourceBindingEvidenceSha256": binding_evidence,
                    "contentSha256": content_sha256,
                    "bytes": byte_count,
                    "widthPx": width_px,
                    "heightPx": height_px,
                    "format": image_format,
                    "mode": image_mode,
                },
            }
        )
    )
    return CandidatePlan(
        variant_id=variant_id,
        source=source,
        source_id=source_id,
        source_url=source_url,
        source_path=source_path,
        content_sha256=content_sha256,
        byte_count=byte_count,
        width_px=width_px,
        height_px=height_px,
        image_format=image_format,
        image_mode=image_mode,
        mime_type=mime_type,
        extension=extension,
        source_version_sha256=source_version_sha256,
        captured_at=captured_at,
        target=dict(target),
        source_binding_evidence_sha256=binding_evidence,
        foreground_bbox=foreground["bbox"],
        foreground_width_px=int(foreground["widthPx"]),
        foreground_height_px=int(foreground["heightPx"]),
        foreground_aspect_ratio=float(foreground["aspectRatio"]),
        major_component_count=int(foreground["majorComponentCount"]),
    )


def validate_contract(
    manifest_path: Path = MANIFEST_PATH,
    failure_ledger_path: Path = FAILURE_LEDGER_PATH,
    *,
    manifest_sha256: str = MANIFEST_SHA256,
    failure_ledger_sha256: str = FAILURE_LEDGER_SHA256,
    repo_root: Path = ROOT,
    claim_root: Path = CLAIM_ROOT,
    source_media_root: Path = SOURCE_MEDIA_ROOT,
    wave2_manifest_path: Path = WAVE2_ROOT / "replacement-manifest-wave2.json",
    wave2_failure_ledger_path: Path = WAVE2_ROOT / "failure-ledger.json",
    wave2_manifest_sha256: str = WAVE2_MANIFEST_SHA256,
    wave2_failure_ledger_sha256: str = WAVE2_FAILURE_LEDGER_SHA256,
    sample_gate: Callable[..., Mapping[str, object]] = assert_raw_source_not_sample,
) -> tuple[dict[str, Any], dict[str, Any], list[CandidatePlan]]:
    manifest = _read_hashed_json(
        manifest_path, manifest_sha256, claim_root, "replacement_manifest"
    )
    ledger = _read_hashed_json(
        failure_ledger_path, failure_ledger_sha256, claim_root, "failure_ledger"
    )
    if (
        manifest.get("schemaVersion") != 3
        or manifest.get("workItemId") != "QC-A08-IMAGE-MEDIA"
        or manifest.get("agent") != "A08-LIMITLESS-WAVE3-VISUAL-GATE"
        or manifest.get("counts") != EXPECTED_COUNTS
    ):
        raise ImportContractError("manifest_registered_identity_invalid")
    policy = manifest.get("policy")
    if (
        not isinstance(policy, dict)
        or policy.get("candidateScope") != "private only"
        or policy.get("dbWriteAllowed") is not False
        or policy.get("publicPromotionAllowed") is not False
        or policy.get("uncertainLanguageOrParallel") != "fail_closed_not_ready"
        or policy.get("requiredSixPartExact") != list(SIX_PART_FIELDS)
    ):
        raise ImportContractError("manifest_private_policy_invalid")
    captured_at = _parse_generated_at(manifest.get("generatedAt"))
    bucket_ids = _validate_bucket_ids(manifest)
    _validate_failure_ledger(manifest, ledger, bucket_ids)
    validate_blacklist_and_language_contract(manifest)
    validate_wave3_visual_contract(
        manifest,
        ledger,
        manifest_sha256=manifest_sha256,
        repo_root=repo_root,
        source_media_root=source_media_root,
        wave2_manifest_path=wave2_manifest_path,
        wave2_failure_ledger_path=wave2_failure_ledger_path,
        wave2_manifest_sha256=wave2_manifest_sha256,
        wave2_failure_ledger_sha256=wave2_failure_ledger_sha256,
    )

    plans = [
        _validate_ready_candidate(
            record,
            repo_root=repo_root,
            claim_media_root=source_media_root,
            manifest_sha256=manifest_sha256,
            captured_at=captured_at,
            sample_gate=sample_gate,
        )
        for record in manifest["ready"]
    ]
    if len(plans) != EXPECTED_COUNTS["ready"]:
        raise ImportContractError("ready_candidate_count_invalid")
    if len({plan.variant_id for plan in plans}) != len(plans):
        raise ImportContractError("ready_variant_not_unique")
    if len({plan.source_path for plan in plans}) != len(plans):
        raise ImportContractError("ready_candidate_path_not_unique")
    if len({plan.content_sha256 for plan in plans}) != len(plans):
        raise ImportContractError("ready_candidate_content_not_unique")
    if len({(plan.source, plan.source_id) for plan in plans}) != len(plans):
        raise ImportContractError("ready_source_binding_not_unique")
    return manifest, ledger, plans


def fetch_db_state(
    connection: Any, plans: Sequence[CandidatePlan], *, lock: bool
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    variant_ids = sorted(plan.variant_id for plan in plans)
    placeholders = ", ".join(["%s"] * len(variant_ids))
    lock_sql = " FOR UPDATE" if lock else ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT v.id AS variant_id, v.opaque_id,
                   v.identity_status AS variant_identity_status,
                   p.tcg_code, p.card_language, p.set_name, p.collector_number,
                   p.edition_code, p.parallel_code, p.finish_code,
                   p.canonical_printing_sha256,
                   p.identity_status AS printing_identity_status,
                   p.evidence_sha256 AS printing_evidence_sha256
              FROM catalog_variant AS v
              JOIN catalog_printing_identity AS p ON p.variant_id = v.id
             WHERE v.id IN ({placeholders})
             ORDER BY v.id{lock_sql}
            """,
            variant_ids,
        )
        printing_rows = list(cursor.fetchall())
        cursor.execute(
            f"""
            SELECT source_code, external_entity_id, variant_id, match_status,
                   evidence_sha256
              FROM catalog_source_identity
             WHERE variant_id IN ({placeholders})
             ORDER BY variant_id, source_code, external_entity_id{lock_sql}
            """,
            variant_ids,
        )
        source_rows = list(cursor.fetchall())
    return printing_rows, source_rows


def compare_db_state(
    plans: Sequence[CandidatePlan],
    printing_rows: Sequence[Mapping[str, Any]],
    source_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    errors: list[str] = []
    printings = {int(row["variant_id"]): row for row in printing_rows}
    bindings = {
        (str(row["source_code"]).casefold(), str(row["external_entity_id"])): row
        for row in source_rows
    }
    if len(printings) != len(plans):
        errors.append("canonical_printing_row_count")
    db_fields = {
        "game": "tcg_code",
        "language": "card_language",
        "set": "set_name",
        "collector": "collector_number",
        "edition": "edition_code",
        "parallel": "parallel_code",
        "finish": "finish_code",
    }
    for plan in plans:
        row = printings.get(plan.variant_id)
        if row is None:
            errors.append(f"{plan.variant_id}:canonical_printing_missing")
            continue
        expected = {
            "opaque_id": plan.target["cardId"],
            "variant_identity_status": "confirmed",
            "printing_identity_status": "canonical",
            "canonical_printing_sha256": plan.target["canonicalPrintingSha256"],
            "printing_evidence_sha256": plan.target["printingEvidenceSha256"],
        }
        expected.update(
            {db_name: plan.target[target_name] for target_name, db_name in db_fields.items()}
        )
        for field, value in expected.items():
            if str(row.get(field) or "") != str(value):
                errors.append(f"{plan.variant_id}:printing_drift:{field}")
        binding = bindings.get((plan.source, plan.source_id))
        if binding is None:
            errors.append(f"{plan.variant_id}:source_binding_missing")
            continue
        if int(binding.get("variant_id") or 0) != plan.variant_id:
            errors.append(f"{plan.variant_id}:source_binding_variant_drift")
        if binding.get("match_status") != "exact":
            errors.append(f"{plan.variant_id}:source_binding_not_exact")
        if (
            str(binding.get("evidence_sha256") or "").casefold()
            != plan.source_binding_evidence_sha256
        ):
            errors.append(f"{plan.variant_id}:source_binding_evidence_drift")
    return errors


def destination_for(
    plan: CandidatePlan,
    *,
    durable_root: Path = DURABLE_ROOT,
    repo_root: Path = ROOT,
    manifest_sha256: str = MANIFEST_SHA256,
) -> tuple[Path, str]:
    destination = (
        durable_root
        / manifest_sha256
        / f"variant-{plan.variant_id}"
        / f"{plan.content_sha256}.{plan.extension}"
    )
    destination = _inside_destination(destination, durable_root)
    try:
        private_key = destination.relative_to(repo_root.resolve(strict=False)).as_posix()
    except ValueError as exc:
        raise ImportContractError("durable_root_outside_repository") from exc
    return destination, private_key


def inspect_existing_asset(
    connection: Any,
    plan: CandidatePlan,
    *,
    lock: bool,
) -> dict[str, Any]:
    lock_sql = " FOR UPDATE" if lock else ""
    with connection.cursor() as cursor:
        cursor.execute(
            f"""
            SELECT asset.id AS asset_id, asset.private_object_key, asset.mime_type,
                   asset.width_px, asset.height_px, asset.source_version_sha256,
                   asset.captured_at, qc.id AS qc_id,
                   qc.semantic_match_status, qc.card_number_match,
                   qc.language_match, qc.tcg_match, qc.raw_front_confirmed,
                   qc.public_allowed, qc.rejection_reason
              FROM market_image_asset AS asset
              LEFT JOIN market_image_qc AS qc
                ON qc.image_asset_id = asset.id AND qc.qc_version = %s
             WHERE asset.variant_id = %s AND asset.image_kind = %s
               AND asset.content_sha256 = %s{lock_sql}
            """,
            (QC_VERSION, plan.variant_id, IMAGE_KIND, plan.content_sha256),
        )
        row = cursor.fetchone()
    if not row:
        return {"asset": "insert", "qc": "insert"}
    expected_asset = {
        "mime_type": plan.mime_type,
        "width_px": plan.width_px,
        "height_px": plan.height_px,
    }
    for field, expected in expected_asset.items():
        if row.get(field) != expected:
            raise ImportContractError(f"existing_asset_metadata_drift:{plan.variant_id}:{field}")
    _required_text(row.get("private_object_key"), "existing_private_object_key")
    _required_sha(row.get("source_version_sha256"), "existing_source_version_sha256")
    if row.get("captured_at") is None:
        raise ImportContractError(f"existing_asset_captured_at_missing:{plan.variant_id}")
    if row.get("qc_id") is None:
        return {"asset": "existing", "qc": "insert", "assetId": int(row["asset_id"])}
    expected_qc = {
        "semantic_match_status": SEMANTIC_PENDING,
        "card_number_match": 1,
        "language_match": 1,
        "tcg_match": 1,
        "raw_front_confirmed": 0,
        "public_allowed": 0,
        "rejection_reason": SEMANTIC_PENDING,
    }
    for field, expected in expected_qc.items():
        if row.get(field) != expected:
            raise ImportContractError(f"existing_qc_metadata_drift:{plan.variant_id}:{field}")
    return {"asset": "existing", "qc": "existing", "assetId": int(row["asset_id"])}


def copy_candidate(
    plan: CandidatePlan,
    destination: Path,
    *,
    durable_root: Path = DURABLE_ROOT,
) -> None:
    destination = _inside_destination(destination, durable_root)
    if destination.is_file():
        if sha256_file(destination) != plan.content_sha256:
            raise ImportContractError(f"durable_candidate_hash_collision:{plan.variant_id}")
        return
    if sha256_file(plan.source_path) != plan.content_sha256:
        raise ImportContractError(f"candidate_bytes_drift_before_copy:{plan.variant_id}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.next")
    try:
        with plan.source_path.open("rb") as source, temporary.open("xb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        if sha256_file(temporary) != plan.content_sha256:
            raise ImportContractError(f"durable_candidate_copy_hash_mismatch:{plan.variant_id}")
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def apply_candidate(
    connection: Any,
    plan: CandidatePlan,
    *,
    durable_root: Path = DURABLE_ROOT,
    repo_root: Path = ROOT,
    manifest_sha256: str = MANIFEST_SHA256,
) -> int:
    destination, private_key = destination_for(
        plan,
        durable_root=durable_root,
        repo_root=repo_root,
        manifest_sha256=manifest_sha256,
    )
    copy_candidate(plan, destination, durable_root=durable_root)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT IGNORE INTO market_image_asset
              (variant_id, image_kind, content_sha256, private_object_key,
               mime_type, width_px, height_px, source_version_sha256, captured_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                plan.variant_id,
                IMAGE_KIND,
                plan.content_sha256,
                private_key,
                plan.mime_type,
                plan.width_px,
                plan.height_px,
                plan.source_version_sha256,
                plan.captured_at,
            ),
        )
        cursor.execute(
            """
            SELECT id, private_object_key, mime_type, width_px, height_px,
                   source_version_sha256, captured_at
              FROM market_image_asset
             WHERE variant_id=%s AND image_kind=%s AND content_sha256=%s
            """,
            (plan.variant_id, IMAGE_KIND, plan.content_sha256),
        )
        asset = cursor.fetchone()
        if not asset:
            raise ImportContractError(f"asset_insert_readback_missing:{plan.variant_id}")
        expected_asset = {
            "mime_type": plan.mime_type,
            "width_px": plan.width_px,
            "height_px": plan.height_px,
        }
        if any(asset.get(field) != expected for field, expected in expected_asset.items()):
            raise ImportContractError(f"asset_insert_readback_drift:{plan.variant_id}")
        _required_text(asset.get("private_object_key"), "asset_readback_private_object_key")
        _required_sha(
            asset.get("source_version_sha256"), "asset_readback_source_version_sha256"
        )
        if asset.get("captured_at") is None:
            raise ImportContractError(f"asset_insert_readback_captured_at_missing:{plan.variant_id}")
        asset_id = int(asset["id"])
        cursor.execute(
            """
            INSERT IGNORE INTO market_image_qc
              (image_asset_id, semantic_match_status, card_number_match,
               language_match, tcg_match, raw_front_confirmed, public_allowed,
               rejection_reason, checked_at, qc_version)
            VALUES (%s, %s, 1, 1, 1, 0, 0, %s, %s, %s)
            """,
            (
                asset_id,
                SEMANTIC_PENDING,
                SEMANTIC_PENDING,
                plan.captured_at,
                QC_VERSION,
            ),
        )
        cursor.execute(
            """
            SELECT semantic_match_status, card_number_match, language_match,
                   tcg_match, raw_front_confirmed, public_allowed,
                   rejection_reason
              FROM market_image_qc
             WHERE image_asset_id=%s AND qc_version=%s
            """,
            (asset_id, QC_VERSION),
        )
        qc = cursor.fetchone()
    expected_qc = {
        "semantic_match_status": SEMANTIC_PENDING,
        "card_number_match": 1,
        "language_match": 1,
        "tcg_match": 1,
        "raw_front_confirmed": 0,
        "public_allowed": 0,
        "rejection_reason": SEMANTIC_PENDING,
    }
    if not qc or any(qc.get(field) != expected for field, expected in expected_qc.items()):
        raise ImportContractError(f"qc_insert_readback_drift:{plan.variant_id}")
    return asset_id


def load_backend_env() -> None:
    path = ROOT / "data/runtime/config/backend.env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def begin_transaction(connection: Any, *, write: bool) -> None:
    with connection.cursor() as cursor:
        if write:
            cursor.execute("START TRANSACTION")
            return
        cursor.execute("SET SESSION TRANSACTION READ ONLY")
        cursor.execute("START TRANSACTION READ ONLY")
        cursor.execute("SELECT @@session.transaction_read_only AS read_only")
        row = cursor.fetchone()
        if not row or int(row.get("read_only") or 0) != 1:
            raise ImportContractError("dry_run_transaction_not_read_only")


def run(args: argparse.Namespace) -> dict[str, Any]:
    manifest, _ledger, plans = validate_contract()
    cohort_contract = validate_blacklist_and_language_contract(manifest)
    limitless_ready = sum(
        "limitless" in f"{plan.source} {plan.source_url}".casefold()
        for plan in plans
    )
    if limitless_ready:
        raise ImportContractError("limitless_ready_candidate_detected")
    connection = db_runtime.connection_from_args(args)
    try:
        begin_transaction(connection, write=bool(args.write))
        printing_rows, source_rows = fetch_db_state(
            connection, plans, lock=bool(args.write)
        )
        drift = compare_db_state(plans, printing_rows, source_rows)
        if drift:
            raise ImportContractError("db_binding_drift:" + ",".join(drift))

        preflight = Counter()
        for plan in plans:
            destination, _private_key = destination_for(plan)
            if destination.is_file() and sha256_file(destination) != plan.content_sha256:
                raise ImportContractError(
                    f"durable_candidate_hash_collision:{plan.variant_id}"
                )
            state = inspect_existing_asset(connection, plan, lock=bool(args.write))
            preflight[f"asset_{state['asset']}"] += 1
            preflight[f"qc_{state['qc']}"] += 1

        writes: list[int] = []
        if args.write:
            writes = [apply_candidate(connection, plan) for plan in plans]
            connection.commit()
            transaction = "committed"
            status = "private_imported"
        else:
            connection.rollback()
            transaction = "rolled_back"
            status = "ready_private_import"
        return {
            "mode": "write" if args.write else "dry_run",
            "status": status,
            "manifestSha256": MANIFEST_SHA256,
            "failureLedgerSha256": FAILURE_LEDGER_SHA256,
            "ready": len(plans),
            "nonReadyFailureLedger": FAILURE_COUNT,
            "dbPrintingBindings": len(printing_rows),
            "dbBindingDrift": 0,
            "candidateBytes": sum(plan.byte_count for plan in plans),
            "sources": dict(sorted(Counter(plan.source for plan in plans).items())),
            "LIMITLESS_READY": limitless_ready,
            "sourceFamilyBlacklist": {
                "family": BLACKLISTED_SOURCE_FAMILY,
                "blacklisted": cohort_contract["blacklisted"],
                "total": EXPECTED_COUNTS["total"],
            },
            "languageCheck": {
                key: cohort_contract[key]
                for key in (
                    "expectedEn",
                    "expectedJa",
                    "jaCandidateChecks",
                    "jaEnglishSignals",
                    "readyEn",
                    "readyJa",
                )
            },
            "readySourceFamilyCheck": {
                "limitless": limitless_ready,
                "nonLimitless": len(plans) - limitless_ready,
            },
            "foregroundGate": {
                "passed": len(plans),
                "aspectRatioMinimum": FOREGROUND_ASPECT_MINIMUM,
                "aspectRatioMaximum": FOREGROUND_ASPECT_MAXIMUM,
                "measuredAspectRatioMinimum": min(
                    plan.foreground_aspect_ratio for plan in plans
                ),
                "measuredAspectRatioMaximum": max(
                    plan.foreground_aspect_ratio for plan in plans
                ),
                "singleMajorComponent": sum(
                    plan.major_component_count == 1 for plan in plans
                ),
            },
            "wave3Reclassified": {
                "review": {
                    "variantId": 1718,
                    "contentSha256": FOREGROUND_REVIEW_SHA256,
                    "reason": "disconnected_alpha_artifact",
                },
                "reject": {
                    "variantId": 1741,
                    "contentSha256": KNOWN_SAMPLE_SHA256,
                    "reason": "known_sample_visual_confirmed",
                },
            },
            "assetInsertsPlanned": preflight["asset_insert"],
            "qcInsertsPlanned": preflight["qc_insert"],
            "writes": len(writes),
            "publicAllowed": 0,
            "pointerWrites": 0,
            "publicWrites": 0,
            "transaction": transaction,
            "generatedAt": manifest["generatedAt"],
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="copy and insert the fixed private cohort (default: read-only dry-run)",
    )
    db_runtime.add_connection_args(parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    load_backend_env()
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (ImportContractError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "rejected", "error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
