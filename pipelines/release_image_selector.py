"""Pure, receipt-bound release image selection.

This module does not write MySQL, assets, manifests, or snapshots.  It accepts
the image rows already assembled by ``canonical_db_qc.image_evidence``
(``latest_qc_assets`` rows work directly) and produces a deterministic,
private selection receipt.

``relaxed-launch-v1`` deliberately treats legacy ``public_allowed`` booleans
and unreviewed semantic labels as confidence signals, not a blanket rejection:
those fields historically also mean "awaiting human picker".  Explicit rights
denials, permanent human rejections, known SAMPLE content, broken assets, and
proven shared/cross-TCG hashes remain hard exclusions.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from image_geometry_qc import inspect_path
from image_source_qc import classify_content_sha256, classify_source
from sample_image_qc import SampleImageRejected, assert_raw_bytes_not_sample


ROOT = Path(__file__).resolve().parents[1]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
STRICT_PROFILE = "strict-v1"
RELAXED_PROFILE = "relaxed-launch-v1"
SUPPORTED_PROFILES = frozenset({STRICT_PROFILE, RELAXED_PROFILE})

_SOURCE_EXACT = frozenset({"source_id_exact", "snk_item_exact"})
_HUMAN_REJECTION_TOKENS = (
    "human_review_rejected",
    "historical_human_review",
    "human_rejected",
)
_PROVEN_CROSS_CARD_TOKENS = (
    "wrong_card_art",
    "identity_mismatch",
    "cross_card",
    "cross_tcg",
    "rare_candy_not_",
)
_SAMPLE_TOKENS = ("sample", "placeholder", "now designing", "now-designing")
_DENIED_RIGHTS = frozenset({"reject", "rejected", "forbidden", "denied", "disallowed", "not_public"})


def _canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha(value: Any) -> str:
    return _text(value).casefold()


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes"}
    return value is True or value == 1


def _field(row: Mapping[str, Any], snake: str, camel: str | None = None) -> Any:
    if snake in row and row.get(snake) is not None:
        return row.get(snake)
    if camel and camel in row and row.get(camel) is not None:
        return row.get(camel)
    qc = row.get("qc")
    if isinstance(qc, Mapping):
        if snake in qc and qc.get(snake) is not None:
            return qc.get(snake)
        if camel and camel in qc and qc.get(camel) is not None:
            return qc.get(camel)
    return None


def _int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _asset_path(row: Mapping[str, Any], content_sha256: str, assets_root: Path | None) -> Path | None:
    candidates: list[Path] = []
    for key in ("asset_path", "assetPath", "path"):
        value = _text(row.get(key))
        if value:
            candidates.append(Path(value))
    key = _text(row.get("private_object_key") or row.get("privateObjectKey"))
    if key:
        key_path = Path(key)
        if key_path.is_absolute():
            candidates.append(key_path)
        else:
            candidates.append(ROOT / key_path)
            if assets_root is not None:
                candidates.append(assets_root / key_path.name)
    if assets_root is not None and SHA256_RE.fullmatch(content_sha256):
        candidates.append(assets_root / f"{content_sha256}.webp")
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _owners(
    row: Mapping[str, Any],
    content_sha256: str,
    hash_owners: Mapping[str, Sequence[Any]] | None,
) -> list[tuple[str, str]]:
    raw_owners: Any = row.get("hash_owners") or row.get("hashOwners")
    if raw_owners is None and hash_owners is not None:
        raw_owners = hash_owners.get(content_sha256)
    if not isinstance(raw_owners, Sequence) or isinstance(raw_owners, (str, bytes)):
        return []
    parsed: list[tuple[str, str]] = []
    for value in raw_owners:
        if isinstance(value, Mapping):
            card_id = _text(value.get("cardId") or value.get("publicId") or value.get("opaque_id") or value.get("id"))
            tcg = _text(value.get("tcg") or value.get("tcg_code"))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            card_id = _text(value[0] if len(value) > 0 else None)
            tcg = _text(value[1] if len(value) > 1 else None)
        else:
            card_id, tcg = _text(value), ""
        if card_id or tcg:
            parsed.append((card_id, tcg))
    return parsed


def _explicit_rights_denial(row: Mapping[str, Any]) -> bool:
    for key in ("rights_public_allowed", "rightsPublicAllowed", "publication_allowed", "publicationAllowed"):
        if key in row and row.get(key) is not None and not _bool(row.get(key)):
            return True
    for key in ("rights_status", "rightsStatus", "publication_status", "publicationStatus"):
        if _text(row.get(key)).casefold() in _DENIED_RIGHTS:
            return True
    return False


def _sample_scan(path: Path, card_id: str) -> str | None:
    try:
        assert_raw_bytes_not_sample(path.read_bytes(), context=card_id)
    except SampleImageRejected:
        return "image_sample_or_placeholder"
    return None


def _stable_geometry(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "status": _text(value.get("status")) or None,
        "reasons": sorted(_text(item) for item in value.get("reasons", []) if _text(item)),
        "widthPx": _int(value.get("widthPx")),
        "heightPx": _int(value.get("heightPx")),
    }


def _candidate_decision(
    row: Mapping[str, Any],
    *,
    profile: str,
    assets_root: Path | None,
    card_id: str,
    tcg_code: str,
    hash_owners: Mapping[str, Sequence[Any]] | None,
    geometry_inspector: Callable[[Path], Mapping[str, Any]],
    sample_scanner: Callable[[Path, str], str | None],
) -> dict[str, Any]:
    asset_id = _int(_field(row, "asset_id", "assetId"))
    variant_id = _int(
        row.get("variant_id")
        or row.get("variantId")
        or row.get("asset_variant_id")
        or row.get("assetVariantId")
    )
    content_sha256 = _sha(_field(row, "content_sha256", "contentSha256"))
    image_kind = _text(_field(row, "image_kind", "imageKind"))
    semantic = _text(_field(row, "semantic_match_status", "semanticMatchStatus"))
    qc_version = _text(_field(row, "qc_version", "qcVersion"))
    source_version_sha256 = _sha(_field(row, "source_version_sha256", "sourceVersionSha256"))
    source_path = _text(row.get("source_path") or row.get("sourcePath"))
    private_key = _text(row.get("private_object_key") or row.get("privateObjectKey"))
    legacy_public_allowed = _bool(_field(row, "public_allowed", "publicAllowed"))
    legacy_pointer_public_allowed = _bool(row.get("pointer_public_allowed") or row.get("pointerPublicAllowed"))
    card_number_match = _bool(_field(row, "card_number_match", "cardNumberMatch"))
    language_match = _bool(_field(row, "language_match", "languageMatch"))
    tcg_match = _bool(_field(row, "tcg_match", "tcgMatch"))
    raw_front_confirmed = _bool(_field(row, "raw_front_confirmed", "rawFrontConfirmed"))
    rejection_reason = _text(_field(row, "rejection_reason", "rejectionReason")).casefold()
    registered_rejection = _sha(row.get("registered_rejection_content_sha256") or row.get("registeredRejectionContentSha256"))
    approval_asset_id = _int(row.get("approval_image_asset_id") or row.get("approvalImageAssetId"))

    hard_blockers: list[str] = []
    warnings: list[str] = []
    if not SHA256_RE.fullmatch(content_sha256):
        hard_blockers.append("image_hash_invalid")
    if image_kind != "raw_front":
        hard_blockers.append("image_not_raw_front")
    if _explicit_rights_denial(row):
        hard_blockers.append("image_rights_explicitly_denied")
    if registered_rejection and registered_rejection == content_sha256:
        hard_blockers.append("image_historically_human_rejected")
    if semantic.casefold() == "human_rejected" or any(token in rejection_reason for token in _HUMAN_REJECTION_TOKENS):
        hard_blockers.append("image_human_rejected")
    if any(token in rejection_reason for token in _PROVEN_CROSS_CARD_TOKENS):
        hard_blockers.append("image_proven_cross_card_or_tcg")
    if any(token in f"{private_key} {rejection_reason}".casefold() for token in _SAMPLE_TOKENS):
        hard_blockers.append("image_sample_or_placeholder")

    source_policy: dict[str, Any] | None = None
    content_policy: dict[str, Any] | None = None
    if SHA256_RE.fullmatch(content_sha256):
        content_policy = dict(classify_content_sha256(content_sha256))
        if content_policy.get("status") == "reject":
            hard_blockers.append("image_source_known_sample")
    if source_path:
        source_policy = dict(
            classify_source(
                source_path,
                tcg_code=tcg_code or _text(row.get("tcg_code") or row.get("tcg")),
                width_px=_int(row.get("width_px") or row.get("width")),
                height_px=_int(row.get("height_px") or row.get("height")),
            )
        )
        if source_policy.get("status") == "reject":
            hard_blockers.append("image_source_known_sample")

    owners = _owners(row, content_sha256, hash_owners)
    owner_ids = {owner for owner, _tcg in owners if owner}
    owner_tcgs = {owner_tcg for _owner, owner_tcg in owners if owner_tcg}
    cross_card = _bool(row.get("cross_card") or row.get("crossCard")) or len(owner_ids) > 1
    cross_tcg = _bool(row.get("cross_tcg") or row.get("crossTcg")) or len(owner_tcgs) > 1
    if cross_card or cross_tcg:
        hard_blockers.append("image_proven_cross_card_or_tcg")

    path = _asset_path(row, content_sha256, assets_root) if SHA256_RE.fullmatch(content_sha256) else None
    geometry: dict[str, Any] | None = None
    if path is None:
        hard_blockers.append("image_asset_missing")
    else:
        if _sha256_file(path) != content_sha256:
            hard_blockers.append("image_asset_hash_mismatch")
        try:
            geometry = dict(geometry_inspector(path))
        except Exception:
            hard_blockers.append("image_geometry_scan_failed")
        else:
            if _text(geometry.get("status")) != "passed":
                hard_blockers.append("image_canvas_geometry_invalid")
        try:
            sample_reason = sample_scanner(path, card_id)
        except SampleImageRejected:
            sample_reason = "image_sample_or_placeholder"
        except Exception:
            warnings.append("image_sample_scan_unavailable")
            sample_reason = None
        if sample_reason:
            hard_blockers.append("image_sample_or_placeholder")

    if profile == STRICT_PROFILE:
        if not legacy_public_allowed:
            hard_blockers.append("strict_requires_public_allowed")
        if not legacy_pointer_public_allowed:
            hard_blockers.append("strict_requires_public_source_pointer")
        if semantic != "human_or_vision_confirmed":
            hard_blockers.append("strict_requires_human_or_vision_confirmed")
        if qc_version != "human-review-v2":
            hard_blockers.append("strict_requires_current_qc_version")
        if approval_asset_id is None or approval_asset_id != asset_id:
            hard_blockers.append("strict_requires_review_binding")
        if not (card_number_match and language_match and tcg_match and raw_front_confirmed):
            hard_blockers.append("strict_requires_all_metadata_matches")
    else:
        if not legacy_public_allowed:
            warnings.append("legacy_qc_public_allowed_false")
        if not legacy_pointer_public_allowed:
            warnings.append("legacy_pointer_public_allowed_false")
        if semantic != "human_or_vision_confirmed":
            warnings.append("semantic_unreviewed")
        for label, value in (
            ("card_number", card_number_match),
            ("language", language_match),
            ("tcg", tcg_match),
            ("raw_front", raw_front_confirmed),
        ):
            if not value:
                warnings.append(f"metadata_{label}_not_true")

    resolver = row.get("resolverEvidence") if isinstance(row.get("resolverEvidence"), Mapping) else {}
    metadata_evidence_count = sum(
        (
            bool(source_path),
            SHA256_RE.fullmatch(source_version_sha256) is not None,
            SHA256_RE.fullmatch(_sha(resolver.get("sourceContentSha256"))) is not None,
        )
    )
    area = (_int(row.get("width_px") or row.get("width")) or 0) * (
        _int(row.get("height_px") or row.get("height")) or 0
    )
    score = {
        "humanOrVisionConfirmed": int(semantic == "human_or_vision_confirmed"),
        "sourceIdExact": int(semantic in _SOURCE_EXACT),
        "metadataEvidenceCount": metadata_evidence_count,
        "matchCount": sum((card_number_match, tcg_match, language_match)),
        "legacyPublicAllowed": int(legacy_public_allowed),
        "pixelArea": area,
    }
    decision = {
        "assetId": asset_id,
        "variantId": variant_id,
        "contentSha256": content_sha256 or None,
        "sourceVersionSha256": source_version_sha256 if SHA256_RE.fullmatch(source_version_sha256) else None,
        "sourcePathSha256": _sha256_bytes(source_path.encode("utf-8")) if source_path else None,
        "imageKind": image_kind or None,
        "semanticMatchStatus": semantic or None,
        "qcVersion": qc_version or None,
        "legacyPublicAllowed": legacy_public_allowed,
        "legacyPointerPublicAllowed": legacy_pointer_public_allowed,
        "matches": {
            "cardNumber": card_number_match,
            "language": language_match,
            "tcg": tcg_match,
            "rawFront": raw_front_confirmed,
        },
        "ownership": {
            "crossCard": cross_card,
            "crossTcg": cross_tcg,
            "ownerCount": len(owners),
        },
        "geometry": _stable_geometry(geometry),
        "contentPolicy": content_policy,
        "sourcePolicy": source_policy,
        "hardBlockers": sorted(set(hard_blockers)),
        "warnings": sorted(set(warnings)),
        "score": score,
    }
    decision["candidateDecisionSha256"] = _sha256_bytes(_canonical_json_bytes(decision))
    return decision


def _rank_key(decision: Mapping[str, Any]) -> tuple[Any, ...]:
    score = decision["score"]
    asset_id = decision.get("assetId")
    return (
        -int(score["humanOrVisionConfirmed"]),
        -int(score["sourceIdExact"]),
        -int(score["metadataEvidenceCount"]),
        -int(score["matchCount"]),
        -int(score["legacyPublicAllowed"]),
        -int(score["pixelArea"]),
        int(asset_id) if isinstance(asset_id, int) else 2**63 - 1,
        str(decision.get("contentSha256") or ""),
    )


def select_release_image(
    candidates: Iterable[Mapping[str, Any]],
    *,
    profile: str = RELAXED_PROFILE,
    assets_root: Path | None = None,
    card_id: str = "",
    tcg_code: str = "",
    hash_owners: Mapping[str, Sequence[Any]] | None = None,
    geometry_inspector: Callable[[Path], Mapping[str, Any]] = inspect_path,
    sample_scanner: Callable[[Path, str], str | None] = _sample_scan,
) -> dict[str, Any]:
    """Select one highest-confidence image and return an immutable receipt.

    The return value is intentionally private-operational data: it contains no
    provider URL, only its SHA-256.  Callers must pass the whole candidate set
    for one card and an asset root; an absent/corrupt file is never selected.
    """
    if profile not in SUPPORTED_PROFILES:
        raise ValueError(f"unsupported release image profile: {profile}")
    decisions = [
        _candidate_decision(
            dict(row),
            profile=profile,
            assets_root=assets_root,
            card_id=card_id,
            tcg_code=tcg_code,
            hash_owners=hash_owners,
            geometry_inspector=geometry_inspector,
            sample_scanner=sample_scanner,
        )
        for row in candidates
        if isinstance(row, Mapping)
    ]
    decisions.sort(
        key=lambda row: (
            int(row.get("assetId") or 2**63 - 1),
            str(row.get("contentSha256") or ""),
            str(row.get("candidateDecisionSha256") or ""),
        )
    )
    candidate_set_sha256 = _sha256_bytes(_canonical_json_bytes(decisions))
    eligible = [row for row in decisions if not row["hardBlockers"]]
    chosen = min(eligible, key=_rank_key) if eligible else None
    receipt = {
        "schemaVersion": 1,
        "kind": "cardz-release-image-selection",
        "releaseProfile": profile,
        "cardId": card_id or None,
        "tcgCode": tcg_code or None,
        "candidateCount": len(decisions),
        "eligibleCandidateCount": len(eligible),
        "candidateSetSha256": candidate_set_sha256,
        "chosenContentSha256": chosen.get("contentSha256") if chosen else None,
        "chosen": chosen,
        "candidates": decisions,
    }
    receipt["receiptSha256"] = _sha256_bytes(_canonical_json_bytes(receipt))
    return receipt
