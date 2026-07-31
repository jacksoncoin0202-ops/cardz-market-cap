"""Verify public snapshot images and terminal-classify media candidates.

Owned by A08 (`image-media-domain`). Public candidates must never include SAMPLE
or cross-card images. Every classification decision binds card identity and the
approved image policy hash.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
PIPELINES_DIR = Path(__file__).resolve().parent
if str(PIPELINES_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINES_DIR))

from image_geometry_qc import POLICY_ID as GEOMETRY_POLICY_ID, inspect_image
from image_geometry_qc import inspect_path
from image_source_qc import classify_content_sha256, classify_source

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Approved public image policy. Changing rules must bump policyId / schemaVersion
# so decisions re-bind to a new imagePolicySha256.
APPROVED_IMAGE_POLICY: dict[str, Any] = {
    "schemaVersion": 1,
    "policyId": "cardz-public-image-policy-v1",
    "rules": {
        "publicImageKind": "raw_front",
        "requiredSemanticMatchStatus": "human_or_vision_confirmed",
        "requirePublicAllowed": True,
        "requireCardBoundBinding": True,
        "requireCardNumberMatch": True,
        "requireTcgMatch": True,
        "requireLanguageMatch": True,
        "requireResolverEvidence": {
            "sourceContentSha256": True,
            "collectorMatch": True,
            "languageMetadataMatch": True,
            "tcgMetadataMatch": True,
        },
        "sampleOrPlaceholderNeverPublic": True,
        "crossCardDuplicateNeverPublic": True,
        "crossTcgDuplicateNeverPublic": True,
        "contentAddressedFilename": True,
        "noSlabNoGraderLabelPublic": True,
        "geometry": {
            "policyId": GEOMETRY_POLICY_ID,
            "canvas": [429, 600],
            "requireTransparentCanvas": True,
            "rejectShrunkenCard": True,
            "requireCenteredCard": True,
        },
        "derivatives": {
            "base": {"suffix": "", "width": None, "height": None},
            "200": {"suffix": "_200", "width": 200, "height": 280},
            "600": {"suffix": "_600", "width": 429, "height": 600},
        },
    },
    "authority": {
        "dataContract": "docs/DATA_CONTRACT.md#images",
        "securityPolicy": "docs/SECURITY.md",
        "sampleGate": "pipelines/sample_image_qc.py",
        "publicQc": "pipelines/public_snapshot_qc.py",
    },
}

PUBLIC_TERMINAL = "public_candidate"
FORBIDDEN_PUBLIC_TERMINALS = frozenset(
    {
        "reject_sample_or_placeholder",
        "reject_cross_card_duplicate",
        "reject_cross_tcg_duplicate",
    }
)

# QC gate human_or_vision_confirmed maps to market_image_qc + manifest fields.
# Promotion may only elevate these evidence grades — never bulk meta_unreviewed.
QC_GATE_COLUMN_MAP: dict[str, Any] = {
    "publicQcReasonCode": "image_not_human_or_vision_confirmed",
    "requiredSemanticMatchStatus": "human_or_vision_confirmed",
    "dbTable": "market_image_qc",
    "dbColumns": {
        "semantic_match_status": "human_or_vision_confirmed",
        "public_allowed": 1,
        "raw_front_confirmed": 1,
        "card_number_match": 1,
        "tcg_match": 1,
        "language_match": 1,
        "image_kind_via_asset": "raw_front",
    },
    "manifestFields": {
        "semanticMatchStatus": "human_or_vision_confirmed",
        "publicAllowed": True,
        "imageKind": "raw_front",
        "cardNumberMatch": True,
        "tcgMatch": True,
        "languageMatch": True,
        "resolverEvidence": [
            "sourceContentSha256",
            "collectorMatch",
            "languageMetadataMatch",
            "tcgMetadataMatch",
        ],
    },
    "pipelines": [
        "pipelines/verify_images.py",
        "pipelines/public_snapshot_qc.py",
        "pipelines/canonical_db_qc.py",
        "pipelines/snk_image_promotion.py",
    ],
}

# Evidence grades eligible for controlled promote → human_or_vision_confirmed.
# meta_unreviewed / pending_review / metadata_exact_unreviewed are NEVER bulk-flipped.
EVIDENCE_PROMOTE_GRADES = frozenset(
    {
        "source_id_exact",
        "snk_item_exact",
        "human_or_vision_confirmed",
    }
)
SEMANTIC_CONFIRMED = "human_or_vision_confirmed"
QC_VERSION_EVIDENCE_PROMOTED = "a08-evidence-promoted-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        + b"\n"
    )


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def image_policy_sha256(policy: Mapping[str, Any] | None = None) -> str:
    """Stable hash of the approved image policy bound into every decision."""
    return sha256_bytes(canonical_json_bytes(policy if policy is not None else APPROVED_IMAGE_POLICY))


def referenced_asset_names(snapshot: dict) -> set[str]:
    """每張卡引用嘅 market-assets 檔名：master 加埋全部衍生尺寸。

    每個 image block 除咗 `src` 仲有 `variants`（`{"200": "...", "600": "..."}`，
    由 g10.DERIVATIVE_SPECS 決定）。只數 `src` 嘅話，360 張卡得 360 個檔名算「有人
    引用」，但實際落地係 1080 個——720 個生效中嘅衍生圖會被當成孤兒：verify 報
    unreferenced、quarantine 直接搬走佢哋，全站卡圖嘅細尺寸即刻爛。
    所以呢度由 snapshot 本身讀 variants，唔好 hardcode suffix 集，將來加尺寸
    唔使兩邊同步。
    """

    names: set[str] = set()
    for card in [*snapshot.get("top100", []), *snapshot.get("watchlist", [])]:
        image = card.get("image") or {}
        candidates = [image.get("src")]
        variants = image.get("variants")
        if isinstance(variants, dict):
            candidates.extend(variants.values())
        for reference in candidates:
            if not isinstance(reference, str) or not reference:
                continue
            name = Path(reference).name
            if name and name not in {".", ".."}:
                names.add(name)
    return names


def index_manifest_records(
    records: Iterable[Mapping[str, Any]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    """Index image-qc records by (publicId, contentSha256) and by publicId.

    When multiple records share a binding, keep the newest qcAt.
    """
    by_binding: dict[tuple[str, str], dict[str, Any]] = {}
    by_card: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        public_id = str(raw.get("publicId") or "").strip()
        content_sha = str(raw.get("contentSha256") or "").strip().casefold()
        if not public_id or not SHA256_RE.fullmatch(content_sha):
            continue
        record = dict(raw)
        record["contentSha256"] = content_sha
        record["publicId"] = public_id
        key = (public_id, content_sha)
        prev = by_binding.get(key)
        if prev is None or str(record.get("qcAt") or "") >= str(prev.get("qcAt") or ""):
            by_binding[key] = record
        by_card[public_id].append(record)
    for public_id, rows in by_card.items():
        rows.sort(key=lambda row: str(row.get("qcAt") or ""), reverse=True)
    return by_binding, dict(by_card)


def build_image_owners(
    cards: Iterable[Mapping[str, Any]],
) -> dict[str, list[tuple[str, str]]]:
    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for card in cards:
        content_sha = str(
            card.get("imageSha256")
            or card.get("contentSha256")
            or ((card.get("facts") or {}).get("image") or {}).get("contentSha256")
            or ((card.get("image") or {}).get("sha256") if isinstance(card.get("image"), Mapping) else "")
            or ""
        ).strip().casefold()
        if not content_sha:
            continue
        owners[content_sha].append(
            (str(card.get("id") or card.get("publicId") or ""), str(card.get("tcg") or ""))
        )
    return dict(owners)


def card_content_sha256(card: Mapping[str, Any]) -> str:
    return str(
        card.get("imageSha256")
        or card.get("contentSha256")
        or ((card.get("facts") or {}).get("image") or {}).get("contentSha256")
        or (
            (card.get("image") or {}).get("sha256")
            if isinstance(card.get("image"), Mapping)
            else ""
        )
        or ""
    ).strip().casefold()


def _db_connect():
    """Read/write live cardz_market_cap MySQL via backend.env (operator path)."""
    import os

    import pymysql

    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\r"))
    return pymysql.connect(
        host=os.environ.get("CARDZ_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=os.environ.get("CARDZ_DB_PASSWORD") or "",
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def db_rows_to_manifest_records(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Shape market_image_asset+qc rows into image-qc manifest records."""
    records: list[dict[str, Any]] = []
    for row in rows:
        content_sha = str(row.get("content_sha256") or "").strip().casefold()
        public_id = str(row.get("opaque_id") or row.get("publicId") or "").strip()
        if not public_id or not SHA256_RE.fullmatch(content_sha):
            continue
        semantic = str(row.get("semantic_match_status") or "")
        card_number_match = bool(row.get("card_number_match"))
        language_match = bool(row.get("language_match"))
        tcg_match = bool(row.get("tcg_match"))
        source_version = str(row.get("source_version_sha256") or "").strip().casefold()
        resolver_source = source_version if SHA256_RE.fullmatch(source_version) else content_sha
        records.append(
            {
                "publicId": public_id,
                "contentSha256": content_sha,
                "imageKind": str(row.get("image_kind") or "raw_front"),
                "publicAllowed": bool(row.get("public_allowed")),
                "semanticMatchStatus": semantic,
                "cardNumberMatch": card_number_match,
                "languageMatch": language_match,
                "tcgMatch": tcg_match,
                "rawFrontConfirmed": bool(row.get("raw_front_confirmed")),
                "qcVersion": str(row.get("qc_version") or ""),
                "qcAt": str(row.get("checked_at") or row.get("created_at") or ""),
                "width": row.get("width_px"),
                "height": row.get("height_px"),
                "variantId": int(row["variant_id"]) if row.get("variant_id") is not None else None,
                "assetId": int(row["asset_id"]) if row.get("asset_id") is not None else None,
                "rejectionReason": row.get("rejection_reason"),
                "resolverEvidence": {
                    "method": "db_market_image_qc",
                    "sourceContentSha256": resolver_source,
                    "collectorMatch": card_number_match,
                    "languageMetadataMatch": language_match,
                    "tcgMetadataMatch": tcg_match,
                    "qcVersion": str(row.get("qc_version") or ""),
                    "assetId": int(row["asset_id"]) if row.get("asset_id") is not None else None,
                },
            }
        )
    return records


def load_db_image_records_for_variants(variant_ids: list[int]) -> list[dict[str, Any]]:
    """Load raw_front asset+qc rows for the given variant ids as manifest records."""
    if not variant_ids:
        return []
    ids = sorted({int(v) for v in variant_ids})
    placeholders = ", ".join(["%s"] * len(ids))
    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT v.opaque_id, a.variant_id, a.id AS asset_id, a.content_sha256,
                       a.image_kind, a.width_px, a.height_px, a.source_version_sha256,
                       v.tcg_code, p.source_path,
                       q.semantic_match_status, q.public_allowed, q.raw_front_confirmed,
                       q.card_number_match, q.language_match, q.tcg_match,
                       q.qc_version, q.checked_at, q.rejection_reason, q.created_at
                FROM market_image_asset a
                JOIN catalog_variant v ON v.id = a.variant_id
                LEFT JOIN market_image_qc q ON q.image_asset_id = a.id
                WHERE a.variant_id IN ({placeholders})
                  AND a.image_kind = 'raw_front'
                """,
                ids,
            )
            rows = list(cur.fetchall())
    finally:
        conn.close()
    return db_rows_to_manifest_records(rows)


def merge_manifest_records(
    primary: Iterable[Mapping[str, Any]],
    overlay: Iterable[Mapping[str, Any]],
    *,
    prefer_overlay: bool = True,
) -> list[dict[str, Any]]:
    """Merge image-qc records by (publicId, contentSha256). Overlay wins when prefer_overlay."""
    by_binding: dict[tuple[str, str], dict[str, Any]] = {}
    for source, is_overlay in ((primary, False), (overlay, True)):
        for raw in source:
            if not isinstance(raw, Mapping):
                continue
            public_id = str(raw.get("publicId") or "").strip()
            content_sha = str(raw.get("contentSha256") or "").strip().casefold()
            if not public_id or not SHA256_RE.fullmatch(content_sha):
                continue
            key = (public_id, content_sha)
            if key in by_binding and not (prefer_overlay and is_overlay):
                continue
            if key in by_binding and prefer_overlay and is_overlay:
                by_binding[key] = dict(raw)
                by_binding[key]["contentSha256"] = content_sha
                by_binding[key]["publicId"] = public_id
                continue
            if key not in by_binding:
                record = dict(raw)
                record["contentSha256"] = content_sha
                record["publicId"] = public_id
                by_binding[key] = record
    return list(by_binding.values())


def build_hash_owner_index(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, set[str]]:
    owners: dict[str, set[str]] = defaultdict(set)
    for raw in records:
        content_sha = str(raw.get("contentSha256") or "").strip().casefold()
        public_id = str(raw.get("publicId") or "").strip()
        if content_sha and public_id and SHA256_RE.fullmatch(content_sha):
            owners[content_sha].add(public_id)
    return dict(owners)


def evidence_promote_blockers(
    record: Mapping[str, Any],
    *,
    hash_owners: Mapping[str, set[str]] | None = None,
    assets_root: Path | None = None,
) -> list[str]:
    """Return blockers preventing evidence-grade promote to human_or_vision_confirmed.

    Never promotes meta_unreviewed / pending_review. Cross-card hashes blocked.
    """
    blockers: list[str] = []
    semantic = str(record.get("semanticMatchStatus") or "")
    if semantic not in EVIDENCE_PROMOTE_GRADES:
        blockers.append(f"semantic_not_evidence_grade:{semantic or 'missing'}")
    if str(record.get("imageKind") or "") != "raw_front":
        blockers.append("image_not_raw_front")
    if record.get("cardNumberMatch") is not True:
        blockers.append("card_number_match_failed")
    if record.get("tcgMatch") is not True:
        blockers.append("tcg_match_failed")
    if record.get("languageMatch") is not True:
        blockers.append("language_match_failed")
    # rawFrontConfirmed may be absent on older manifest rows; prefer True when present.
    if "rawFrontConfirmed" in record and record.get("rawFrontConfirmed") is not True:
        blockers.append("raw_front_not_confirmed")
    content_sha = str(record.get("contentSha256") or "").strip().casefold()
    if not SHA256_RE.fullmatch(content_sha):
        blockers.append("invalid_content_sha256")
    else:
        owners = (hash_owners or {}).get(content_sha) or set()
        if len(owners) > 1:
            blockers.append("cross_card_duplicate_hash")
        if assets_root is not None:
            master = assets_root / f"{content_sha}.webp"
            if not master.is_file():
                blockers.append("public_asset_missing")
            elif sha256_file(master) != content_sha:
                blockers.append("public_asset_hash_mismatch")
    return blockers


def select_card_binding(
    card: Mapping[str, Any],
    *,
    by_binding: Mapping[tuple[str, str], Mapping[str, Any]],
    by_card: Mapping[str, list[Mapping[str, Any]]],
    hash_owners: Mapping[str, set[str]],
    assets_root: Path | None = None,
) -> dict[str, Any]:
    """Choose card-bound content hash: report hash if bound, else best rebind.

    Rebind prefers evidence-grade unique on-disk assets over unbound report hashes.
    Never rebinds onto a cross-card duplicate hash.
    """
    card_id = str(card.get("id") or card.get("publicId") or "")
    report_sha = card_content_sha256(card)
    report_record = by_binding.get((card_id, report_sha)) if report_sha else None
    card_records = list(by_card.get(card_id) or [])

    def _score(record: Mapping[str, Any]) -> tuple[int, int, int, int, int, str]:
        semantic = str(record.get("semanticMatchStatus") or "")
        content_sha = str(record.get("contentSha256") or "").strip().casefold()
        owners = hash_owners.get(content_sha) or set()
        unique = 1 if len(owners) <= 1 else 0
        evidence = 1 if semantic in EVIDENCE_PROMOTE_GRADES else 0
        confirmed = 1 if semantic == SEMANTIC_CONFIRMED else 0
        blockers = evidence_promote_blockers(
            record, hash_owners=hash_owners, assets_root=assets_root
        )
        clean = 1 if not blockers else 0
        public_allowed = 1 if record.get("publicAllowed") is True else 0
        # Prefer confirmed/clean/evidence/unique/public; then stable sha.
        return (confirmed, clean, evidence, unique, public_allowed, content_sha)

    # Unique non-cross-card candidates for this card.
    candidates: list[Mapping[str, Any]] = []
    for record in card_records:
        content_sha = str(record.get("contentSha256") or "").strip().casefold()
        if not SHA256_RE.fullmatch(content_sha):
            continue
        owners = hash_owners.get(content_sha) or set()
        if len(owners) > 1:
            continue
        candidates.append(record)

    report_unique = False
    if report_record is not None and report_sha:
        owners = hash_owners.get(report_sha) or {card_id}
        report_unique = len(owners) <= 1

    # Prefer report binding when unique AND (confirmed OR no stronger evidence candidate).
    if report_record is not None and report_unique:
        report_blockers = evidence_promote_blockers(
            report_record, hash_owners=hash_owners, assets_root=assets_root
        )
        report_semantic = str(report_record.get("semanticMatchStatus") or "")
        better = None
        if candidates:
            best = max(candidates, key=_score)
            best_sha = str(best.get("contentSha256") or "").strip().casefold()
            best_blockers = evidence_promote_blockers(
                best, hash_owners=hash_owners, assets_root=assets_root
            )
            best_semantic = str(best.get("semanticMatchStatus") or "")
            # Rebind only when best is evidence-grade clean (or already confirmed)
            # and strictly better than the report binding.
            if (
                best_sha != report_sha
                and not best_blockers
                and best_semantic in EVIDENCE_PROMOTE_GRADES
                and (
                    report_semantic not in EVIDENCE_PROMOTE_GRADES
                    or bool(report_blockers)
                    or (
                        best_semantic == SEMANTIC_CONFIRMED
                        and report_semantic != SEMANTIC_CONFIRMED
                    )
                )
            ):
                better = best
        if better is None:
            return {
                "contentSha256": report_sha,
                "manifestRecord": dict(report_record),
                "rebound": False,
                "rebindReason": None,
                "reportContentSha256": report_sha,
            }
        best_sha = str(better.get("contentSha256") or "").strip().casefold()
        return {
            "contentSha256": best_sha,
            "manifestRecord": dict(better),
            "rebound": True,
            "rebindReason": "prefer_evidence_grade_over_report_hash",
            "reportContentSha256": report_sha,
        }

    # Report hash missing or cross-card: try rebind to best unique card record.
    if candidates:
        best = max(candidates, key=_score)
        best_sha = str(best.get("contentSha256") or "").strip().casefold()
        rebound = best_sha != report_sha
        return {
            "contentSha256": best_sha,
            "manifestRecord": dict(best),
            "rebound": rebound,
            "rebindReason": (
                "report_hash_not_bound_to_card"
                if report_record is None
                else "report_hash_cross_card_duplicate"
            )
            if rebound
            else None,
            "reportContentSha256": report_sha or None,
        }

    return {
        "contentSha256": report_sha or None,
        "manifestRecord": dict(report_record) if report_record else None,
        "rebound": False,
        "rebindReason": None if report_record else "no_card_bound_record",
        "reportContentSha256": report_sha or None,
    }


def classify_cohort_media_with_rebind(
    cards: Iterable[Mapping[str, Any]],
    *,
    manifest_records: Iterable[Mapping[str, Any]],
    assets_root: Path | None = None,
    scan_sample_if_public_path: bool = True,
    image_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify cohort using select_card_binding rebind before terminal classify."""
    policy = dict(image_policy) if image_policy is not None else APPROVED_IMAGE_POLICY
    policy_hash = image_policy_sha256(policy)
    card_list = list(cards)
    records = [dict(r) for r in manifest_records if isinstance(r, Mapping)]
    by_binding, by_card = index_manifest_records(records)
    hash_owners = build_hash_owner_index(records)

    # Seed owners from report hashes, then override with selected bindings.
    owners: dict[str, list[tuple[str, str]]] = defaultdict(list)
    selections: list[dict[str, Any]] = []
    for card in card_list:
        card_id = str(card.get("id") or card.get("publicId") or "")
        tcg = str(card.get("tcg") or "")
        selection = select_card_binding(
            card,
            by_binding=by_binding,
            by_card=by_card,
            hash_owners=hash_owners,
            assets_root=assets_root,
        )
        selections.append(selection)
        content_sha = selection.get("contentSha256")
        if content_sha:
            owners[str(content_sha)].append((card_id, tcg))

    decisions: list[dict[str, Any]] = []
    rebind_count = 0
    for card, selection in zip(card_list, selections):
        card_id = str(card.get("id") or card.get("publicId") or "")
        tcg = str(card.get("tcg") or "")
        content_sha = selection.get("contentSha256")
        identity = {
            "variantId": card.get("variantId"),
            "segment": card.get("segment"),
            "marketRank": card.get("marketRank"),
            "reportContentSha256": selection.get("reportContentSha256"),
            "rebound": bool(selection.get("rebound")),
            "rebindReason": selection.get("rebindReason"),
        }
        facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else {}
        if isinstance(facts.get("identity"), Mapping):
            identity["printing"] = dict(facts["identity"])
        if isinstance(facts.get("image"), Mapping):
            identity["imageFacts"] = {
                "assetId": facts["image"].get("assetId"),
                "qcVersion": facts["image"].get("qcVersion"),
                "semanticMatchStatus": facts["image"].get("semanticMatchStatus"),
                "status": facts["image"].get("status"),
            }
        decision = classify_card_media(
            card_id=card_id,
            tcg=tcg,
            content_sha256=content_sha,
            identity=identity,
            manifest_record=selection.get("manifestRecord"),
            card_records=by_card.get(card_id),
            image_owners=owners,
            assets_root=assets_root,
            scan_sample_if_public_path=scan_sample_if_public_path,
            image_policy=policy,
        )
        decision["binding"] = {
            "rebound": bool(selection.get("rebound")),
            "rebindReason": selection.get("rebindReason"),
            "reportContentSha256": selection.get("reportContentSha256"),
            "selectedContentSha256": content_sha,
        }
        if selection.get("rebound"):
            rebind_count += 1
        # Attach promote eligibility for fill shard consumers.
        record = selection.get("manifestRecord") or {}
        promote_blockers = (
            evidence_promote_blockers(
                record, hash_owners=hash_owners, assets_root=assets_root
            )
            if record
            else ["no_manifest_record"]
        )
        decision["evidencePromote"] = {
            "eligible": not promote_blockers
            and str(record.get("semanticMatchStatus") or "") in EVIDENCE_PROMOTE_GRADES
            and str(record.get("semanticMatchStatus") or "") != SEMANTIC_CONFIRMED,
            "alreadyConfirmed": str(record.get("semanticMatchStatus") or "")
            == SEMANTIC_CONFIRMED,
            "blockers": promote_blockers,
            "semanticMatchStatus": record.get("semanticMatchStatus"),
            "assetId": record.get("assetId"),
            "variantId": record.get("variantId") or card.get("variantId"),
        }
        decisions.append(decision)

    decisions.sort(key=lambda row: (row.get("cardId") or "", row.get("contentSha256") or ""))
    public_rows = [row for row in decisions if row.get("publicCandidate")]
    review_queue = [
        {
            "cardId": row["cardId"],
            "tcg": row["tcg"],
            "contentSha256": row["contentSha256"],
            "cardIdentitySha256": row["cardIdentitySha256"],
            "imagePolicySha256": row["imagePolicySha256"],
            "terminalStatus": row["terminalStatus"],
            "reviewReasons": row["reviewReasons"],
            "rightsFlags": row["rightsFlags"],
            "reasonCodes": row["reasonCodes"],
            "semanticMatchStatus": row["manifest"]["semanticMatchStatus"],
            "decisionSha256": row["decisionSha256"],
            "binding": row.get("binding"),
            "evidencePromote": row.get("evidencePromote"),
        }
        for row in decisions
        if row.get("reviewReasons") or row.get("rightsFlags") or not row.get("publicCandidate")
    ]

    invariant_violations: list[str] = []
    for row in public_rows:
        if row["terminalStatus"] != PUBLIC_TERMINAL:
            invariant_violations.append(f"{row['cardId']}: public without public_candidate terminal")
        if row["duplicates"]["crossCard"] or row["duplicates"]["crossTcg"]:
            invariant_violations.append(f"{row['cardId']}: public with cross-card/cross-tcg image")
        if row["sample"]["status"] == "rejected":
            invariant_violations.append(f"{row['cardId']}: public with SAMPLE rejection")
        if row.get("imagePolicySha256") != policy_hash:
            invariant_violations.append(f"{row['cardId']}: imagePolicySha256 mismatch")
        if not row.get("cardIdentitySha256"):
            invariant_violations.append(f"{row['cardId']}: missing cardIdentitySha256")

    terminal_counts = Counter(row["terminalStatus"] for row in decisions)
    promote_eligible = [
        row
        for row in decisions
        if (row.get("evidencePromote") or {}).get("eligible")
    ]
    already_confirmed = [
        row
        for row in decisions
        if (row.get("evidencePromote") or {}).get("alreadyConfirmed")
    ]
    return {
        "schemaVersion": 1,
        "kind": "cardz-media-candidate-shard",
        "imagePolicySha256": policy_hash,
        "imagePolicyId": policy.get("policyId"),
        "cardCount": len(decisions),
        "publicCandidateCount": len(public_rows),
        "reviewQueueCount": len(review_queue),
        "rebindCount": rebind_count,
        "evidencePromoteEligibleCount": len(promote_eligible),
        "alreadyConfirmedCount": len(already_confirmed),
        "terminalStatusCounts": dict(sorted(terminal_counts.items())),
        "invariantViolations": invariant_violations,
        "publicCandidatesZeroSampleAndCrossCard": (
            len(public_rows) == 0
            or (
                not invariant_violations
                and all(
                    not row["duplicates"]["crossCard"]
                    and not row["duplicates"]["crossTcg"]
                    and row["sample"]["status"] != "rejected"
                    for row in public_rows
                )
            )
        ),
        "qcGateColumnMap": QC_GATE_COLUMN_MAP,
        "decisions": decisions,
        "publicCandidates": public_rows,
        "semanticRightsReviewQueue": review_queue,
        "evidencePromoteEligible": promote_eligible,
        "alreadyConfirmed": already_confirmed,
    }


def promote_evidence_grade_in_db(
    *,
    asset_id: int,
    variant_id: int,
    content_sha256: str,
    operator: str,
    note: str,
    write: bool,
    assets_root: Path | None = None,
) -> dict[str, Any]:
    """Promote one asset to human_or_vision_confirmed when evidence grade supports it.

    Hard gates:
    - operator must be human or vision
    - live row semantic must be source_id_exact / snk_item_exact / already confirmed
    - match flags + unique hash + public asset present
    - never promote meta_unreviewed / pending_review
    - never promote SAMPLE or cross-card hashes
    """
    if operator not in ("human", "vision"):
        raise ValueError("operator_must_be_human_or_vision")
    content_sha = str(content_sha256 or "").strip().casefold()
    if not SHA256_RE.fullmatch(content_sha):
        raise ValueError("invalid_content_sha256")

    conn = _db_connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT v.opaque_id, a.variant_id, a.id AS asset_id, a.content_sha256,
                       a.image_kind, a.width_px, a.height_px, a.source_version_sha256,
                       q.id AS qc_id, q.semantic_match_status, q.public_allowed,
                       q.raw_front_confirmed, q.card_number_match, q.language_match,
                       q.tcg_match, q.qc_version, q.checked_at, q.rejection_reason
                FROM market_image_asset a
                JOIN catalog_variant v ON v.id = a.variant_id
                JOIN market_image_qc q ON q.image_asset_id = a.id
                LEFT JOIN market_image_source_pointer p
                  ON p.variant_id=a.variant_id AND p.image_kind=a.image_kind
                 AND p.source_version_sha256=a.source_version_sha256
                WHERE a.id = %s AND a.variant_id = %s AND a.image_kind = 'raw_front'
                FOR UPDATE
                """,
                (int(asset_id), int(variant_id)),
            )
            row = cur.fetchone()
            if row is None:
                raise ValueError("asset_qc_row_missing")
            if str(row.get("content_sha256") or "").casefold() != content_sha:
                raise ValueError("content_sha256_mismatch")

            # Global cross-card ownership check.
            cur.execute(
                """
                SELECT COUNT(DISTINCT variant_id) AS n
                FROM market_image_asset
                WHERE content_sha256 = %s AND image_kind = 'raw_front'
                """,
                (content_sha,),
            )
            owner_n = int((cur.fetchone() or {}).get("n") or 0)
            if owner_n > 1:
                raise ValueError("cross_card_duplicate_hash")

            records = db_rows_to_manifest_records([row])
            if not records:
                raise ValueError("record_shape_failed")
            record = records[0]
            hash_owners = {content_sha: {str(row.get("opaque_id") or "")}}
            blockers = evidence_promote_blockers(
                record, hash_owners=hash_owners, assets_root=assets_root
            )
            semantic = str(row.get("semantic_match_status") or "")
            if semantic == SEMANTIC_CONFIRMED:
                return {
                    "write": False,
                    "alreadyConfirmed": True,
                    "assetId": int(asset_id),
                    "variantId": int(variant_id),
                    "contentSha256": content_sha,
                    "publicId": row.get("opaque_id"),
                    "semanticMatchStatus": semantic,
                }
            if blockers:
                return {
                    "write": False,
                    "promoted": False,
                    "blockers": blockers,
                    "assetId": int(asset_id),
                    "variantId": int(variant_id),
                    "contentSha256": content_sha,
                    "publicId": row.get("opaque_id"),
                    "semanticMatchStatus": semantic,
                }

            # SAMPLE hard gate before any write.
            master = None
            if assets_root is not None:
                master = assets_root / f"{content_sha}.webp"
            if master is None or not master.is_file():
                for base in (
                    ROOT / "data" / "public" / "market-assets",
                    ROOT / "apps" / "web" / "public" / "market-assets",
                ):
                    candidate = base / f"{content_sha}.webp"
                    if candidate.is_file():
                        master = candidate
                        break
            if master is None or not master.is_file():
                raise ValueError("public_asset_missing")
            if classify_content_sha256(content_sha).get("status") == "reject":
                raise ValueError("content_policy_rejected")
            source_policy = classify_source(
                str(row.get("source_path") or ""),
                tcg_code=str(row.get("tcg_code") or ""),
                width_px=row.get("width_px"),
                height_px=row.get("height_px"),
            )
            if source_policy.get("status") == "reject":
                raise ValueError("source_policy_rejected")
            if inspect_path(master).get("status") != "passed":
                raise ValueError("image_geometry_failed")
            sample_status, sample_reason = _scan_sample(master)
            if sample_status == "rejected":
                raise ValueError(f"sample_or_placeholder:{sample_reason}")
            if sample_status in {"scan_failed", "scan_unavailable"}:
                raise ValueError(f"sample_scan_failed:{sample_reason}")

            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).replace(tzinfo=None)
            receipt = {
                "kind": "a08-evidence-grade-promotion",
                "operator": operator,
                "note": note,
                "assetId": int(asset_id),
                "variantId": int(variant_id),
                "contentSha256": content_sha,
                "publicId": row.get("opaque_id"),
                "priorSemanticMatchStatus": semantic,
                "promotedSemanticMatchStatus": SEMANTIC_CONFIRMED,
                "qcVersion": QC_VERSION_EVIDENCE_PROMOTED,
                "sampleStatus": sample_status,
                "imagePolicySha256": image_policy_sha256(),
                "imagePolicyId": APPROVED_IMAGE_POLICY["policyId"],
            }
            receipt["receiptSha256"] = sha256_bytes(canonical_json_bytes(receipt))

            if not write:
                return {
                    "write": False,
                    "wouldPromote": True,
                    "receipt": receipt,
                    "assetId": int(asset_id),
                    "variantId": int(variant_id),
                    "contentSha256": content_sha,
                    "publicId": row.get("opaque_id"),
                }

            # Demote other raw_front QC rows for this variant; promote only this asset.
            cur.execute(
                """
                UPDATE market_image_qc q
                JOIN market_image_asset a ON a.id = q.image_asset_id
                SET q.public_allowed = 0
                WHERE a.variant_id = %s AND a.image_kind = 'raw_front' AND a.id <> %s
                """,
                (int(variant_id), int(asset_id)),
            )
            cur.execute(
                """
                UPDATE market_image_qc
                SET semantic_match_status = %s,
                    card_number_match = 1,
                    language_match = 1,
                    tcg_match = 1,
                    raw_front_confirmed = 1,
                    public_allowed = 1,
                    rejection_reason = NULL,
                    checked_at = %s,
                    qc_version = %s
                WHERE image_asset_id = %s
                """,
                (SEMANTIC_CONFIRMED, now, QC_VERSION_EVIDENCE_PROMOTED, int(asset_id)),
            )
            if int(cur.rowcount) != 1:
                raise ValueError("qc_promote_rowcount_mismatch")
            conn.commit()
            return {
                "write": True,
                "promoted": True,
                "receipt": receipt,
                "assetId": int(asset_id),
                "variantId": int(variant_id),
                "contentSha256": content_sha,
                "publicId": row.get("opaque_id"),
                "semanticMatchStatus": SEMANTIC_CONFIRMED,
            }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _resolver_blockers(record: Mapping[str, Any]) -> list[str]:
    evidence = record.get("resolverEvidence")
    if not isinstance(evidence, Mapping):
        return ["resolver_evidence_missing"]
    blockers: list[str] = []
    source = str(evidence.get("sourceContentSha256") or "").strip().casefold()
    if not SHA256_RE.fullmatch(source):
        blockers.append("resolver_source_hash_invalid")
    for field in ("collectorMatch", "languageMetadataMatch", "tcgMetadataMatch"):
        if evidence.get(field) is not True:
            blockers.append(f"resolver_{field}_failed")
    return blockers


def _derivative_status(content_sha: str, assets_root: Path | None) -> dict[str, Any]:
    specs = APPROVED_IMAGE_POLICY["rules"]["derivatives"]
    if assets_root is None:
        return {
            "status": "assets_root_not_checked",
            "present": {},
            "missing": sorted(specs.keys()),
        }
    present: dict[str, bool] = {}
    missing: list[str] = []
    for name, spec in specs.items():
        suffix = str(spec["suffix"])
        path = assets_root / f"{content_sha}{suffix}.webp"
        ok = path.is_file()
        present[name] = ok
        if not ok:
            missing.append(name)
    return {
        "status": "complete" if not missing else "incomplete",
        "present": present,
        "missing": missing,
    }


def _scan_sample(path: Path) -> tuple[str, str | None]:
    """Return (sampleStatus, reason). Only used on public-path candidates."""
    try:
        from sample_image_qc import SampleImageRejected, assert_raw_bytes_not_sample
    except Exception as exc:  # pragma: no cover - import environment
        return "scan_unavailable", f"sample_image_qc_import_failed:{exc}"
    try:
        assert_raw_bytes_not_sample(path.read_bytes(), context=path.name)
    except SampleImageRejected as exc:
        return "rejected", str(exc)
    except Exception as exc:
        return "scan_failed", str(exc)
    return "clean", None


def classify_card_media(
    *,
    card_id: str,
    tcg: str,
    content_sha256: str | None,
    identity: Mapping[str, Any] | None = None,
    manifest_record: Mapping[str, Any] | None = None,
    card_records: list[Mapping[str, Any]] | None = None,
    image_owners: Mapping[str, list[tuple[str, str]]] | None = None,
    assets_root: Path | None = None,
    scan_sample_if_public_path: bool = True,
    image_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Terminal-classify one card's media against the approved image policy.

    Every decision binds card identity, content hash (when present), and
    imagePolicySha256. Public candidates never include SAMPLE or cross-card images.
    """
    policy = dict(image_policy) if image_policy is not None else APPROVED_IMAGE_POLICY
    policy_hash = image_policy_sha256(policy)
    content_sha = str(content_sha256 or "").strip().casefold()
    owners = list((image_owners or {}).get(content_sha, [])) if content_sha else []
    if not owners and content_sha:
        owners = [(card_id, tcg)]
    owner_ids = sorted({owner for owner, _tcg in owners if owner})
    owner_tcgs = sorted({owner_tcg for _owner, owner_tcg in owners if owner_tcg})
    cross_card = len(owner_ids) > 1
    cross_tcg = len(owner_tcgs) > 1

    identity_payload = {
        "cardId": card_id,
        "tcg": tcg,
        **(dict(identity) if identity else {}),
    }
    identity_sha = sha256_bytes(canonical_json_bytes(identity_payload))

    reason_codes: list[str] = []
    review_reasons: list[str] = []
    rights_flags: list[str] = []
    sample_status = "not_scanned_non_public_path"
    sample_reason: str | None = None
    terminal = "reject_missing_content_hash"
    public_candidate = False

    derivatives = _derivative_status(content_sha, assets_root) if content_sha else {
        "status": "no_content_hash",
        "present": {},
        "missing": sorted(policy["rules"]["derivatives"].keys()),
    }

    master_path: Path | None = None
    asset_hash_ok: bool | None = None
    if content_sha and assets_root is not None:
        master_path = assets_root / f"{content_sha}.webp"
        if master_path.is_file():
            asset_hash_ok = sha256_file(master_path) == content_sha
        else:
            asset_hash_ok = False

    if not content_sha or not SHA256_RE.fullmatch(content_sha):
        terminal = "reject_missing_content_hash"
        reason_codes.append("missing_or_invalid_content_sha256")
    elif cross_tcg:
        terminal = "reject_cross_tcg_duplicate"
        reason_codes.append("cross_tcg_duplicate_image")
    elif cross_card:
        terminal = "reject_cross_card_duplicate"
        reason_codes.append("cross_card_duplicate_image")
    elif manifest_record is None:
        if card_records:
            terminal = "reject_hash_not_bound_to_card"
            reason_codes.append("content_hash_not_bound_to_card")
            review_reasons.append("semantic_rebind_required")
        else:
            terminal = "reject_missing_manifest_binding"
            reason_codes.append("no_image_qc_manifest_record")
            review_reasons.append("missing_image_evidence")
    else:
        if str(manifest_record.get("imageKind") or "") != policy["rules"]["publicImageKind"]:
            reason_codes.append("image_not_raw_front")
        if manifest_record.get("publicAllowed") is not True:
            reason_codes.append("image_not_public_allowed")
            rights_flags.append("public_allowed_false")
        semantic = str(manifest_record.get("semanticMatchStatus") or "")
        if semantic != policy["rules"]["requiredSemanticMatchStatus"]:
            reason_codes.append("image_not_human_or_vision_confirmed")
            review_reasons.append(f"semantic:{semantic or 'missing'}")
        if policy["rules"]["requireCardNumberMatch"] and manifest_record.get("cardNumberMatch") is not True:
            reason_codes.append("image_cardNumberMatch_failed")
            review_reasons.append("card_number_match_failed")
        if policy["rules"]["requireTcgMatch"] and manifest_record.get("tcgMatch") is not True:
            reason_codes.append("image_tcgMatch_failed")
            review_reasons.append("tcg_match_failed")
        if policy["rules"]["requireLanguageMatch"] and manifest_record.get("languageMatch") is not True:
            # languageMatch may be absent on some records; treat non-True as review.
            if "languageMatch" in manifest_record and manifest_record.get("languageMatch") is not True:
                reason_codes.append("image_languageMatch_failed")
                review_reasons.append("language_match_failed")
        resolver_blockers = _resolver_blockers(manifest_record)
        if resolver_blockers:
            reason_codes.extend(resolver_blockers)
            rights_flags.extend(resolver_blockers)
            review_reasons.append("resolver_evidence_incomplete")
        if assets_root is not None:
            if asset_hash_ok is False and master_path is not None and not master_path.is_file():
                reason_codes.append("image_asset_missing")
            elif asset_hash_ok is False:
                reason_codes.append("image_asset_hash_mismatch")
            if derivatives["status"] == "incomplete":
                reason_codes.append("image_derivatives_incomplete")
                review_reasons.append("derivatives_incomplete")

        if reason_codes:
            if "image_not_raw_front" in reason_codes:
                terminal = "reject_not_raw_front"
            elif "image_not_public_allowed" in reason_codes and semantic == policy["rules"]["requiredSemanticMatchStatus"]:
                terminal = "reject_not_public_allowed"
            elif "image_not_human_or_vision_confirmed" in reason_codes:
                terminal = "reject_semantic_unconfirmed"
            elif any(code.startswith("resolver_") for code in reason_codes):
                terminal = "reject_resolver_evidence_incomplete"
            elif "image_asset_missing" in reason_codes:
                terminal = "reject_asset_missing"
            elif "image_asset_hash_mismatch" in reason_codes:
                terminal = "reject_asset_hash_mismatch"
            elif "image_derivatives_incomplete" in reason_codes:
                terminal = "reject_derivatives_incomplete"
            else:
                terminal = "reject_match_fields_failed"
        else:
            # Public path open — SAMPLE hard gate before public_candidate.
            if scan_sample_if_public_path and master_path is not None and master_path.is_file():
                sample_status, sample_reason = _scan_sample(master_path)
            elif scan_sample_if_public_path:
                sample_status, sample_reason = "scan_failed", "master_asset_missing_for_sample_scan"
            else:
                sample_status, sample_reason = "scan_skipped_by_caller", None

            if sample_status == "rejected":
                terminal = "reject_sample_or_placeholder"
                reason_codes.append("image_sample_or_placeholder")
                public_candidate = False
            elif sample_status in {"scan_failed", "scan_unavailable"}:
                terminal = "reject_sample_scan_failed"
                reason_codes.append("image_sample_scan_failed")
                public_candidate = False
            else:
                terminal = PUBLIC_TERMINAL
                public_candidate = True
                sample_status = sample_status or "clean"

    # Hard invariant: never label SAMPLE / cross-card as public.
    if terminal in FORBIDDEN_PUBLIC_TERMINALS:
        public_candidate = False
    if public_candidate and (
        terminal != PUBLIC_TERMINAL
        or cross_card
        or cross_tcg
        or sample_status == "rejected"
    ):
        public_candidate = False
        if terminal == PUBLIC_TERMINAL:
            terminal = "reject_invariant_guard"

    decision = {
        "cardId": card_id,
        "tcg": tcg,
        "contentSha256": content_sha or None,
        "cardIdentitySha256": identity_sha,
        "imagePolicySha256": policy_hash,
        "imagePolicyId": policy.get("policyId"),
        "terminalStatus": terminal,
        "publicCandidate": public_candidate,
        "reasonCodes": sorted(set(reason_codes)),
        "reviewReasons": sorted(set(review_reasons)),
        "rightsFlags": sorted(set(rights_flags)),
        "sample": {
            "status": sample_status,
            "reason": sample_reason,
        },
        "duplicates": {
            "crossCard": cross_card,
            "crossTcg": cross_tcg,
            "ownerCardIds": owner_ids,
            "ownerTcgs": owner_tcgs,
        },
        "manifest": {
            "bound": manifest_record is not None,
            "publicAllowed": (
                bool(manifest_record.get("publicAllowed")) if manifest_record else False
            ),
            "imageKind": (
                str(manifest_record.get("imageKind") or "") if manifest_record else None
            ),
            "semanticMatchStatus": (
                str(manifest_record.get("semanticMatchStatus") or "")
                if manifest_record
                else None
            ),
            "qcVersion": (
                str(manifest_record.get("qcVersion") or "") if manifest_record else None
            ),
            "alternateRecordCount": len(card_records or []),
        },
        "derivatives": derivatives,
        "asset": {
            "masterPresent": bool(master_path and master_path.is_file()) if master_path else None,
            "contentHashMatchesFile": asset_hash_ok,
        },
        "identity": identity_payload,
    }
    decision["decisionSha256"] = sha256_bytes(canonical_json_bytes(decision))
    return decision


def classify_cohort_media(
    cards: Iterable[Mapping[str, Any]],
    *,
    manifest_records: Iterable[Mapping[str, Any]],
    assets_root: Path | None = None,
    scan_sample_if_public_path: bool = True,
    image_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify a full cohort. Returns shard rows + review queue + summary."""
    policy = dict(image_policy) if image_policy is not None else APPROVED_IMAGE_POLICY
    policy_hash = image_policy_sha256(policy)
    card_list = list(cards)
    by_binding, by_card = index_manifest_records(manifest_records)
    owners = build_image_owners(card_list)

    decisions: list[dict[str, Any]] = []
    for card in card_list:
        card_id = str(card.get("id") or card.get("publicId") or "")
        tcg = str(card.get("tcg") or "")
        content_sha = str(
            card.get("imageSha256")
            or card.get("contentSha256")
            or ((card.get("facts") or {}).get("image") or {}).get("contentSha256")
            or (
                (card.get("image") or {}).get("sha256")
                if isinstance(card.get("image"), Mapping)
                else ""
            )
            or ""
        ).strip().casefold()
        identity = {
            "variantId": card.get("variantId"),
            "segment": card.get("segment"),
            "marketRank": card.get("marketRank"),
        }
        facts = card.get("facts") if isinstance(card.get("facts"), Mapping) else {}
        if isinstance(facts.get("identity"), Mapping):
            identity["printing"] = dict(facts["identity"])
        if isinstance(facts.get("image"), Mapping):
            identity["imageFacts"] = {
                "assetId": facts["image"].get("assetId"),
                "qcVersion": facts["image"].get("qcVersion"),
                "semanticMatchStatus": facts["image"].get("semanticMatchStatus"),
                "status": facts["image"].get("status"),
            }
        record = by_binding.get((card_id, content_sha)) if content_sha else None
        decision = classify_card_media(
            card_id=card_id,
            tcg=tcg,
            content_sha256=content_sha or None,
            identity=identity,
            manifest_record=record,
            card_records=by_card.get(card_id),
            image_owners=owners,
            assets_root=assets_root,
            scan_sample_if_public_path=scan_sample_if_public_path,
            image_policy=policy,
        )
        decisions.append(decision)

    decisions.sort(key=lambda row: (row.get("cardId") or "", row.get("contentSha256") or ""))
    public_rows = [row for row in decisions if row.get("publicCandidate")]
    review_queue = [
        {
            "cardId": row["cardId"],
            "tcg": row["tcg"],
            "contentSha256": row["contentSha256"],
            "cardIdentitySha256": row["cardIdentitySha256"],
            "imagePolicySha256": row["imagePolicySha256"],
            "terminalStatus": row["terminalStatus"],
            "reviewReasons": row["reviewReasons"],
            "rightsFlags": row["rightsFlags"],
            "reasonCodes": row["reasonCodes"],
            "semanticMatchStatus": row["manifest"]["semanticMatchStatus"],
            "decisionSha256": row["decisionSha256"],
        }
        for row in decisions
        if row.get("reviewReasons") or row.get("rightsFlags") or not row.get("publicCandidate")
    ]

    # Acceptance invariants for public candidates.
    invariant_violations: list[str] = []
    for row in public_rows:
        if row["terminalStatus"] != PUBLIC_TERMINAL:
            invariant_violations.append(f"{row['cardId']}: public without public_candidate terminal")
        if row["duplicates"]["crossCard"] or row["duplicates"]["crossTcg"]:
            invariant_violations.append(f"{row['cardId']}: public with cross-card/cross-tcg image")
        if row["sample"]["status"] == "rejected":
            invariant_violations.append(f"{row['cardId']}: public with SAMPLE rejection")
        if row.get("imagePolicySha256") != policy_hash:
            invariant_violations.append(f"{row['cardId']}: imagePolicySha256 mismatch")
        if not row.get("cardIdentitySha256"):
            invariant_violations.append(f"{row['cardId']}: missing cardIdentitySha256")

    terminal_counts = Counter(row["terminalStatus"] for row in decisions)
    return {
        "schemaVersion": 1,
        "kind": "cardz-media-candidate-shard",
        "imagePolicySha256": policy_hash,
        "imagePolicyId": policy.get("policyId"),
        "cardCount": len(decisions),
        "publicCandidateCount": len(public_rows),
        "reviewQueueCount": len(review_queue),
        "terminalStatusCounts": dict(sorted(terminal_counts.items())),
        "invariantViolations": invariant_violations,
        "publicCandidatesZeroSampleAndCrossCard": (
            len(public_rows) == 0
            or (
                not invariant_violations
                and all(
                    not row["duplicates"]["crossCard"]
                    and not row["duplicates"]["crossTcg"]
                    and row["sample"]["status"] != "rejected"
                    for row in public_rows
                )
            )
        ),
        "decisions": decisions,
        "publicCandidates": public_rows,
        "semanticRightsReviewQueue": review_queue,
    }


def verify(
    snapshot_path: Path,
    assets_path: Path,
    manifest_path: Path | None = None,
    strict_semantic: bool = False,
    allow_unreferenced: bool = False,
) -> list[str]:
    """`allow_unreferenced` 淨係俾 quarantine 之前嗰一 pass 用。

    assets 目錄係 content-addressed 累積落嚟嘅：每次卡圖重算都會留低舊 sha 嘅檔，
    所以「未引用檔 > 0」係 quarantine 未行之前嘅正常狀態，唔應該當成 snapshot 壞。
    但每張卡本身（檔存在／hash／尺寸／QC 記錄）一定要喺搬任何檔之前驗清楚。
    """
    errors: list[str] = []
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    cards = [*snapshot.get("top100", []), *snapshot.get("watchlist", [])]
    top_count = len(snapshot.get("top100", []))
    if not 1 <= top_count <= 100:
        errors.append("top100 does not contain between 1 and 100 cards")

    seen: set[str] = set()
    image_owners: dict[str, list[tuple[str, str]]] = {}
    qc_by_binding: dict[tuple[str, str], dict] = {}
    if manifest_path is not None:
        if not manifest_path.is_file():
            errors.append("image QC manifest is missing")
        else:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            qc_by_binding = {
                (str(record.get("publicId")), str(record.get("contentSha256"))): record
                for record in manifest.get("records", [])
                if (
                    isinstance(record, dict)
                    and record.get("publicAllowed")
                    and record.get("publicId")
                    and record.get("contentSha256")
                )
            }
    for card in cards:
        image = card.get("image", {})
        filename = Path(image.get("src", "")).name
        image_hash = str(image.get("sha256") or "")
        image_owners.setdefault(image_hash, []).append(
            (str(card.get("id") or ""), str(card.get("tcg") or ""))
        )
        if not filename or filename in {".", ".."}:
            errors.append(f"{card.get('id')}: invalid image path")
            continue
        if filename in seen:
            continue
        seen.add(filename)
        path = assets_path / filename
        if not path.is_file():
            errors.append(f"{card.get('id')}: image is missing")
            continue
        actual_sha = sha256_file(path)
        if actual_sha != image.get("sha256"):
            errors.append(f"{card.get('id')}: image hash mismatch")
        if not filename.startswith(actual_sha):
            errors.append(f"{card.get('id')}: image is not content-addressed")
        if manifest_path is not None:
            qc = qc_by_binding.get((str(card.get("id") or ""), actual_sha))
            if qc is None:
                errors.append(f"{card.get('id')}: image has no card-bound public-allowed QC record")
            elif strict_semantic and qc.get("semanticMatchStatus") != "human_or_vision_confirmed":
                errors.append(f"{card.get('id')}: image semantic QC is not human_or_vision_confirmed")
        try:
            with Image.open(path) as opened:
                if list(opened.size) != [image.get("width"), image.get("height")]:
                    errors.append(f"{card.get('id')}: image dimensions do not match")
                if strict_semantic:
                    geometry = inspect_image(opened)
                    if geometry["status"] != "passed":
                        errors.append(
                            f"{card.get('id')}: image canvas geometry invalid "
                            f"({','.join(geometry['reasons'])})"
                        )
        except Exception:
            errors.append(f"{card.get('id')}: image cannot be decoded")

    if strict_semantic:
        for image_hash, owners in sorted(image_owners.items()):
            if image_hash and len(owners) > 1:
                tcgs = {tcg for _card_id, tcg in owners}
                label = "cross-TCG" if len(tcgs) > 1 else "unapproved"
                errors.append(
                    f"{label} duplicate public image {image_hash}: "
                    + ",".join(card_id for card_id, _tcg in owners)
                )

    for name in sorted(referenced_asset_names(snapshot)):
        if not (assets_path / name).is_file():
            errors.append(f"referenced generation asset is missing: {name}")

    # `seen` 淨係用嚟避免同一張 master 驗兩次，唔可以攞嚟判斷「有冇人引用」——
    # 佢淨係收 src，唔包 variants。
    if not allow_unreferenced:
        referenced = referenced_asset_names(snapshot)
        extras = sorted(
            path.name
            for path in assets_path.glob("*")
            if path.is_file() and path.name not in referenced
        )
        if extras:
            errors.append(f"public image directory contains {len(extras)} unreferenced files")
    return errors


def _cmd_verify(args: argparse.Namespace) -> int:
    errors = verify(
        args.snapshot,
        args.assets,
        args.manifest,
        args.strict_semantic,
        args.allow_unreferenced,
    )
    print(
        json.dumps(
            {"imagesVerified": len(list(args.assets.glob("*"))), "errors": errors},
            sort_keys=True,
        )
    )
    return 1 if errors else 0


def _write_classify_outputs(result: Mapping[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    decisions = list(result["decisions"])
    shard_path = output_dir / "media-candidate-shard.jsonl"
    with shard_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in decisions:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    envelope = {
        key: value
        for key, value in result.items()
        if key
        not in {
            "decisions",
            "publicCandidates",
            "semanticRightsReviewQueue",
            "evidencePromoteEligible",
            "alreadyConfirmed",
        }
    }
    envelope["shardPath"] = str(shard_path.as_posix())
    envelope["publicCandidateCardIds"] = [
        row["cardId"] for row in result.get("publicCandidates") or []
    ]
    (output_dir / "media-candidate-shard.json").write_text(
        json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "semantic-rights-review-queue.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "cardz-semantic-rights-review-queue",
                "imagePolicySha256": result["imagePolicySha256"],
                "imagePolicyId": result["imagePolicyId"],
                "count": len(result["semanticRightsReviewQueue"]),
                "items": result["semanticRightsReviewQueue"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"shardPath": shard_path, "envelope": envelope}


def _cmd_classify_cohort(args: argparse.Namespace) -> int:
    cohort = json.loads(args.cohort_report.read_text(encoding="utf-8"))
    cards = cohort.get("cards") if isinstance(cohort, Mapping) else None
    if not isinstance(cards, list):
        raise SystemExit("cohort report must contain a cards array")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    records = manifest.get("records") if isinstance(manifest, Mapping) else manifest
    if not isinstance(records, list):
        raise SystemExit("image-qc manifest must contain records")
    result = classify_cohort_media(
        cards,
        manifest_records=records,
        assets_root=args.assets,
        scan_sample_if_public_path=not args.skip_sample_scan,
    )
    _write_classify_outputs(result, args.output_dir)
    print(
        json.dumps(
            {
                "cardCount": result["cardCount"],
                "publicCandidateCount": result["publicCandidateCount"],
                "reviewQueueCount": result["reviewQueueCount"],
                "terminalStatusCounts": result["terminalStatusCounts"],
                "publicCandidatesZeroSampleAndCrossCard": result[
                    "publicCandidatesZeroSampleAndCrossCard"
                ],
                "invariantViolations": result["invariantViolations"],
                "imagePolicySha256": result["imagePolicySha256"],
                "outputDir": str(args.output_dir),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if result["invariantViolations"] else 0


def _cmd_fill_cohort(args: argparse.Namespace) -> int:
    """Fill-path classify: merge DB image QC + manifest, rebind, optional evidence promote."""
    cohort = json.loads(args.cohort_report.read_text(encoding="utf-8"))
    cards = cohort.get("cards") if isinstance(cohort, Mapping) else None
    if not isinstance(cards, list):
        raise SystemExit("cohort report must contain a cards array")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    file_records = manifest.get("records") if isinstance(manifest, Mapping) else manifest
    if not isinstance(file_records, list):
        raise SystemExit("image-qc manifest must contain records")

    variant_ids = [
        int(card["variantId"])
        for card in cards
        if card.get("variantId") is not None
    ]
    db_records = load_db_image_records_for_variants(variant_ids)
    merged = merge_manifest_records(file_records, db_records, prefer_overlay=True)

    before = classify_cohort_media(
        cards,
        manifest_records=file_records,
        assets_root=args.assets,
        scan_sample_if_public_path=False,
    )
    result = classify_cohort_media_with_rebind(
        cards,
        manifest_records=merged,
        assets_root=args.assets,
        scan_sample_if_public_path=not args.skip_sample_scan,
    )

    promotions: list[dict[str, Any]] = []
    if args.write_promotions:
        for row in result.get("evidencePromoteEligible") or []:
            ep = row.get("evidencePromote") or {}
            asset_id = ep.get("assetId")
            variant_id = ep.get("variantId")
            content_sha = row.get("contentSha256")
            if asset_id is None or variant_id is None or not content_sha:
                promotions.append(
                    {
                        "cardId": row.get("cardId"),
                        "promoted": False,
                        "blockers": ["missing_asset_or_variant_id"],
                    }
                )
                continue
            try:
                outcome = promote_evidence_grade_in_db(
                    asset_id=int(asset_id),
                    variant_id=int(variant_id),
                    content_sha256=str(content_sha),
                    operator=args.operator,
                    note=args.note,
                    write=True,
                    assets_root=args.assets,
                )
            except Exception as exc:  # noqa: BLE001 — per-card isolation
                outcome = {
                    "cardId": row.get("cardId"),
                    "promoted": False,
                    "error": str(exc),
                }
            outcome["cardId"] = row.get("cardId")
            promotions.append(outcome)

        # Re-load DB after promotions and re-classify.
        if promotions:
            db_records = load_db_image_records_for_variants(variant_ids)
            merged = merge_manifest_records(file_records, db_records, prefer_overlay=True)
            result = classify_cohort_media_with_rebind(
                cards,
                manifest_records=merged,
                assets_root=args.assets,
                scan_sample_if_public_path=not args.skip_sample_scan,
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    _write_classify_outputs(result, args.output_dir)

    # Stratified remaining review queue (non-public).
    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in result.get("semanticRightsReviewQueue") or []:
        semantic = str(row.get("semanticMatchStatus") or "missing")
        terminal = str(row.get("terminalStatus") or "")
        if terminal == "reject_cross_card_duplicate" or terminal == "reject_cross_tcg_duplicate":
            bucket = "cross_card_or_tcg"
        elif semantic in EVIDENCE_PROMOTE_GRADES:
            bucket = "evidence_grade_incomplete"
        elif semantic in {"meta_unreviewed", "metadata_exact_unreviewed"}:
            bucket = "meta_unreviewed_needs_human_or_vision"
        elif semantic == "pending_review":
            bucket = "pending_review_snk_queue"
        elif terminal == "reject_hash_not_bound_to_card":
            bucket = "hash_not_bound"
        elif terminal == "reject_missing_manifest_binding":
            bucket = "missing_manifest"
        else:
            bucket = "other"
        strata[bucket].append(row)

    stratified = {
        "schemaVersion": 1,
        "kind": "cardz-image-remaining-review-queue",
        "imagePolicySha256": result["imagePolicySha256"],
        "counts": {key: len(value) for key, value in sorted(strata.items())},
        "buckets": {key: value for key, value in sorted(strata.items())},
    }
    (args.output_dir / "remaining-review-queue.json").write_text(
        json.dumps(stratified, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # A11 generation media bind prep list: FE-safe public candidates only.
    a11_hashes = sorted(
        {
            str(row.get("contentSha256") or "").casefold()
            for row in result.get("publicCandidates") or []
            if SHA256_RE.fullmatch(str(row.get("contentSha256") or "").casefold())
        }
    )
    a11_payload = {
        "schemaVersion": 1,
        "kind": "cardz-generation-media-bind-prep",
        "note": "content_sha256 list for A11 generation media bind; only public_candidate FE-safe",
        "imagePolicySha256": result["imagePolicySha256"],
        "count": len(a11_hashes),
        "contentSha256": a11_hashes,
        "cards": [
            {
                "cardId": row["cardId"],
                "contentSha256": row["contentSha256"],
                "variantId": (row.get("identity") or {}).get("variantId"),
                "decisionSha256": row["decisionSha256"],
            }
            for row in result.get("publicCandidates") or []
        ],
    }
    (args.output_dir / "a11-generation-media-bind-prep.json").write_text(
        json.dumps(a11_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    # Proposed manifest overlay for promoted / already confirmed public candidates.
    overlay_records = []
    for row in result.get("publicCandidates") or []:
        # Reconstruct from decision manifest fields + binding.
        overlay_records.append(
            {
                "publicId": row["cardId"],
                "contentSha256": row["contentSha256"],
                "imageKind": row["manifest"].get("imageKind") or "raw_front",
                "publicAllowed": True,
                "semanticMatchStatus": SEMANTIC_CONFIRMED,
                "qcVersion": row["manifest"].get("qcVersion"),
                "cardIdentitySha256": row["cardIdentitySha256"],
                "imagePolicySha256": row["imagePolicySha256"],
                "decisionSha256": row["decisionSha256"],
            }
        )
    (args.output_dir / "manifest-overlay-public-candidates.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "kind": "cardz-image-qc-manifest-overlay",
                "count": len(overlay_records),
                "records": overlay_records,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    if promotions:
        (args.output_dir / "evidence-promotions.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "kind": "cardz-evidence-grade-promotions",
                    "operator": args.operator,
                    "note": args.note,
                    "count": len(promotions),
                    "promoted": sum(1 for p in promotions if p.get("promoted")),
                    "items": promotions,
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    summary = {
        "cardCount": result["cardCount"],
        "beforeTerminalStatusCounts": before["terminalStatusCounts"],
        "afterTerminalStatusCounts": result["terminalStatusCounts"],
        "beforePublicCandidateCount": before["publicCandidateCount"],
        "afterPublicCandidateCount": result["publicCandidateCount"],
        "rebindCount": result.get("rebindCount"),
        "evidencePromoteEligibleCount": result.get("evidencePromoteEligibleCount"),
        "alreadyConfirmedCount": result.get("alreadyConfirmedCount"),
        "promotionsWritten": sum(1 for p in promotions if p.get("promoted")),
        "reviewQueueCount": result["reviewQueueCount"],
        "remainingReviewBuckets": stratified["counts"],
        "a11MediaBindPrepCount": len(a11_hashes),
        "dbRecordCount": len(db_records),
        "mergedRecordCount": len(merged),
        "publicCandidatesZeroSampleAndCrossCard": result[
            "publicCandidatesZeroSampleAndCrossCard"
        ],
        "invariantViolations": result["invariantViolations"],
        "imagePolicySha256": result["imagePolicySha256"],
        "qcGateColumnMap": QC_GATE_COLUMN_MAP,
        "outputDir": str(args.output_dir),
    }
    (args.output_dir / "fill-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 1 if result["invariantViolations"] else 0


def _cmd_promote_evidence(args: argparse.Namespace) -> int:
    try:
        outcome = promote_evidence_grade_in_db(
            asset_id=int(args.asset_id),
            variant_id=int(args.variant_id),
            content_sha256=str(args.content_sha256),
            operator=args.operator,
            note=args.note,
            write=bool(args.write),
            assets_root=args.assets,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps({"ok": True, **outcome}, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if outcome.get("promoted") or outcome.get("wouldPromote") or outcome.get("alreadyConfirmed") else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")

    verify_parser = sub.add_parser("verify", help="verify a public snapshot image set")
    verify_parser.add_argument(
        "--snapshot", type=Path, default=ROOT / "data" / "public" / "seed-snapshot.json"
    )
    verify_parser.add_argument(
        "--assets", type=Path, default=ROOT / "data" / "public" / "market-assets"
    )
    verify_parser.add_argument(
        "--manifest", type=Path, default=ROOT / "manifests" / "image-qc.json"
    )
    verify_parser.add_argument("--strict-semantic", action="store_true")
    verify_parser.add_argument(
        "--allow-unreferenced",
        action="store_true",
        help="quarantine 之前嗰一 pass 用：只驗卡圖本身，唔理目錄有幾多舊 sha 檔",
    )
    verify_parser.set_defaults(func=_cmd_verify)

    classify_parser = sub.add_parser(
        "classify-cohort",
        help="terminal-classify cohort media into private candidate shard + review queue",
    )
    classify_parser.add_argument("--cohort-report", type=Path, required=True)
    classify_parser.add_argument(
        "--manifest", type=Path, default=ROOT / "manifests" / "image-qc.json"
    )
    classify_parser.add_argument(
        "--assets", type=Path, default=ROOT / "data" / "public" / "market-assets"
    )
    classify_parser.add_argument("--output-dir", type=Path, required=True)
    classify_parser.add_argument(
        "--skip-sample-scan",
        action="store_true",
        help="skip OCR SAMPLE scan even on public-path candidates (tests only)",
    )
    classify_parser.set_defaults(func=_cmd_classify_cohort)

    fill_parser = sub.add_parser(
        "fill-cohort",
        help="DB-merge + rebind fill classify; optional evidence-grade promote only",
    )
    fill_parser.add_argument("--cohort-report", type=Path, required=True)
    fill_parser.add_argument(
        "--manifest", type=Path, default=ROOT / "manifests" / "image-qc.json"
    )
    fill_parser.add_argument(
        "--assets", type=Path, default=ROOT / "data" / "public" / "market-assets"
    )
    fill_parser.add_argument("--output-dir", type=Path, required=True)
    fill_parser.add_argument(
        "--skip-sample-scan",
        action="store_true",
        help="skip OCR SAMPLE scan even on public-path candidates (tests only)",
    )
    fill_parser.add_argument(
        "--write-promotions",
        action="store_true",
        help="write DB promotions for evidence-grade eligible rows only",
    )
    fill_parser.add_argument(
        "--operator",
        choices=("human", "vision"),
        default="vision",
        help="promotion operator label (human|vision); required for writes",
    )
    fill_parser.add_argument(
        "--note",
        default="a08-fill-evidence-grade-promote",
        help="promotion receipt note",
    )
    fill_parser.set_defaults(func=_cmd_fill_cohort)

    promote_parser = sub.add_parser(
        "promote-evidence",
        help="promote one source_id_exact/snk_item_exact asset (never meta_unreviewed)",
    )
    promote_parser.add_argument("--asset-id", type=int, required=True)
    promote_parser.add_argument("--variant-id", type=int, required=True)
    promote_parser.add_argument("--content-sha256", required=True)
    promote_parser.add_argument("--operator", choices=("human", "vision"), required=True)
    promote_parser.add_argument("--note", default="a08-evidence-grade-promote")
    promote_parser.add_argument(
        "--assets", type=Path, default=ROOT / "data" / "public" / "market-assets"
    )
    promote_parser.add_argument("--write", action="store_true")
    promote_parser.set_defaults(func=_cmd_promote_evidence)

    # Backward-compatible flat flags (existing npm scripts / run_daily).
    parser.add_argument("--snapshot", type=Path, default=None)
    parser.add_argument("--assets", type=Path, default=None)
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--strict-semantic", action="store_true")
    parser.add_argument("--allow-unreferenced", action="store_true")

    args = parser.parse_args()
    if args.command:
        return int(args.func(args))

    # Legacy invocation: pipelines/verify_images.py --snapshot ...
    snapshot = args.snapshot or (ROOT / "data" / "public" / "seed-snapshot.json")
    assets = args.assets or (ROOT / "data" / "public" / "market-assets")
    manifest = args.manifest or (ROOT / "manifests" / "image-qc.json")
    legacy = argparse.Namespace(
        snapshot=snapshot,
        assets=assets,
        manifest=manifest,
        strict_semantic=args.strict_semantic,
        allow_unreferenced=args.allow_unreferenced,
    )
    return _cmd_verify(legacy)


if __name__ == "__main__":
    raise SystemExit(main())
