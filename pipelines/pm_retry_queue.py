#!/usr/bin/env python3
"""Build one private, deterministic agent queue from immutable PM remediation receipts.

This is a control-plane view only.  It neither writes the failure ledger nor
touches the canonical database, public assets, pointers, schedules, or network.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "data" / "runtime" / "private-reports" / "pm-retry-queue-20260731"
IMAGE_ROOT = ROOT / "data" / "runtime" / "private-reports" / "pm-image-remediation-20260731"
LIVE_GATE_ROOT = ROOT / "data" / "runtime" / "private-reports" / "pm-live-gate-20260731"

DEFAULT_GEOMETRY = IMAGE_ROOT / "geometry" / "v3" / "geometry-remediation-ledger-v3.json"
DEFAULT_LIMITLESS = IMAGE_ROOT / "limitless" / "wave3" / "replacement-manifest-wave3.json"
DEFAULT_QUARANTINE = ROOT / "data" / "runtime" / "private-reports" / "image-source-family-quarantine" / "pm_limitless_family_quarantine_20260731_0631" / "receipt.json"
DEFAULT_OUTER_MISSING = IMAGE_ROOT / "missing" / "artifacts" / "missing-raw-front-rows.json"
DEFAULT_PRICE_SALES = LIVE_GATE_ROOT / "price-sales-gap-remediation" / "worklist.jsonl"
DEFAULT_CANONICAL_QC = (
    LIVE_GATE_ROOT
    / "canonical-db-qc-post-hardening"
    / "qc_pm_post_hardening_20260731"
    / "report.json"
)

SCHEMA_VERSION = 2
SALES_POLICY = {
    "windowDays": 30,
    "grade": "PSA 10",
    "purePsa10Minimum": 10,
    "bundleAllowed": False,
}
LIMITLESS_FAMILY = "limitless-one-piece-en"


class RetryQueueError(ValueError):
    """A supplied immutable remediation receipt does not meet this contract."""


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RetryQueueError(f"invalid JSON receipt: {path}") from error


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as error:
        raise RetryQueueError(f"invalid JSONL receipt: {path}") from error
    if not all(isinstance(row, Mapping) for row in rows):
        raise RetryQueueError(f"JSONL receipt has non-object row: {path}")
    return [dict(row) for row in rows]


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _item_key(category: str, *, variant_id: int | None = None, asset_id: int | None = None, source_id: str | None = None) -> str:
    parts = ["retry", category]
    if variant_id is not None:
        parts.append(f"variant:{variant_id}")
    if asset_id is not None:
        parts.append(f"asset:{asset_id}")
    if source_id:
        parts.append(f"source:{source_id}")
    return ":".join(parts)


def _evidence(path: Path) -> dict[str, str]:
    return {"path": _relative(path), "sha256": sha256_file(path)}


def _queue_item(
    *,
    category: str,
    cohort: str,
    status: str,
    reason: str,
    retryable: bool,
    next_action: str,
    evidence: Mapping[str, str],
    variant_id: int | None = None,
    asset_id: int | None = None,
    source_id: str | None = None,
    source_family: str | None = None,
    language: str | None = None,
    tcg: str | None = None,
    attempt: int = 1,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item = {
        "itemKey": _item_key(category, variant_id=variant_id, asset_id=asset_id, source_id=source_id),
        "category": category,
        "cohort": cohort,
        "status": status,
        "variantId": variant_id,
        "assetId": asset_id,
        "sourceId": source_id,
        "sourceFamily": source_family,
        "tcg": tcg,
        "requiredLanguage": language,
        "languageGuard": "ja_requires_ja_replacement" if language == "ja" else None,
        "reason": reason,
        "retryable": retryable,
        "nextAction": next_action,
        "attempt": attempt,
        "notBefore": None,
        "evidence": dict(evidence),
        "metadata": dict(metadata or {}),
    }
    return item


def _geometry_items(document: Mapping[str, Any], evidence: Mapping[str, str]) -> list[dict[str, Any]]:
    records = document.get("records")
    if document.get("exactInputCount") != 7 or not isinstance(records, list):
        raise RetryQueueError("geometry v3 receipt must declare the exact 7-record follow-up")
    if str(document.get("sourceLedger") or "").replace("\\", "/") != "data/runtime/private-reports/pm-image-remediation-20260731/geometry/v2/geometry-remediation-ledger-v2.json":
        raise RetryQueueError("geometry v3 receipt must retain v2 lineage")
    unresolved = [row for row in records if isinstance(row, Mapping) and row.get("status") != "success"]
    if len(unresolved) != 2 or len(records) != 7:
        raise RetryQueueError("geometry v3 receipt must contain exactly 5 success and 2 unresolved records")
    items: list[dict[str, Any]] = []
    for row in unresolved:
        variant_id, asset_id = row.get("variantId"), row.get("assetId")
        if not isinstance(variant_id, int) or not isinstance(asset_id, int):
            raise RetryQueueError("geometry unresolved record requires integer variantId and assetId")
        items.append(_queue_item(
            category="image_geometry",
            cohort="release",
            status="unresolved",
            variant_id=variant_id,
            asset_id=asset_id,
            tcg=str(row.get("tcg") or "") or None,
            language=str(row.get("expectedLanguage") or "") or None,
            reason=str(row.get("unresolvedReason") or "geometry_normalization_failed"),
            retryable=True,
            next_action="search_exact_authoritative_raw_front",
            evidence=evidence,
        ))
    return items


def _limitless_items(
    document: Mapping[str, Any],
    quarantine: Mapping[str, Any],
    evidence: Mapping[str, str],
    quarantine_evidence: Mapping[str, str],
) -> list[dict[str, Any]]:
    counts = document.get("counts")
    expected_counts = {"ready": 13, "reject": 15, "review": 10, "total": 146, "unresolved": 108}
    if not isinstance(counts, Mapping) or dict(counts) != expected_counts:
        raise RetryQueueError("Limitless wave3 receipt counts must be 13 ready, 15 reject, 10 review, 108 unresolved")
    if quarantine.get("action") != "image-source-family-quarantine" or quarantine.get("family") != LIMITLESS_FAMILY:
        raise RetryQueueError("quarantine receipt must be the Limitless One Piece family receipt")
    if quarantine.get("mode") != "write" or quarantine.get("transaction") != "committed" or quarantine.get("qcInserted") != 320 or quarantine.get("pointersDisabled") != 320:
        raise RetryQueueError("quarantine receipt must prove committed 320-QC and 320-pointer disablement")
    if quarantine.get("matched") != 320 or quarantine.get("releaseCohortOverlap") != 146 or quarantine.get("outerNonRelease") != 174:
        raise RetryQueueError("quarantine receipt must declare 320 = 146 release + 174 outer")
    assets = quarantine.get("assets")
    if not isinstance(assets, list) or len(assets) != 320:
        raise RetryQueueError("quarantine receipt must list exactly 320 family assets")
    release_rows: dict[int, Mapping[str, Any]] = {}
    for bucket in ("ready", "reject", "review", "unresolved"):
        rows = document.get(bucket)
        if not isinstance(rows, list) or len(rows) != int(counts[bucket]):
            raise RetryQueueError(f"Limitless wave3 receipt bucket invalid: {bucket}")
        for row in rows:
            if not isinstance(row, Mapping) or not isinstance(row.get("variantId"), int):
                raise RetryQueueError(f"Limitless wave3 {bucket} row requires variantId")
            variant_id = int(row["variantId"])
            if variant_id in release_rows:
                raise RetryQueueError(f"Limitless wave3 repeats variantId {variant_id}")
            release_rows[variant_id] = row
    if len(release_rows) != 146:
        raise RetryQueueError("Limitless wave3 receipt must have 146 unique release children")
    quarantine_variants: set[int] = set()
    asset_by_variant: dict[int, Mapping[str, Any]] = {}
    for asset in assets:
        if not isinstance(asset, Mapping) or not isinstance(asset.get("variantId"), int) or not isinstance(asset.get("assetId"), int):
            raise RetryQueueError("quarantine asset requires integer variantId and assetId")
        variant_id = int(asset["variantId"])
        if variant_id in quarantine_variants:
            raise RetryQueueError(f"quarantine receipt repeats variantId {variant_id}")
        quarantine_variants.add(variant_id)
        asset_by_variant[variant_id] = asset
    if not set(release_rows).issubset(quarantine_variants):
        raise RetryQueueError("quarantine receipt must contain every wave3 release child")
    items = [_queue_item(
        category="image_source_family",
        cohort="all",
        status="source_family_contaminated",
        source_id=LIMITLESS_FAMILY,
        source_family=LIMITLESS_FAMILY,
        reason="known SAMPLE source family; replace every child image, never promote this family",
        retryable=True,
        next_action="replace_all_limitless_children",
        evidence=quarantine_evidence,
        metadata={"childCount": 320, "releaseCohortOverlap": 146, "outerNonRelease": 174},
    )]
    settings = {
        "ready": ("ready_for_registered_import", False, "registered_private_import"),
        "reject": ("candidate_rejected", True, "search_other_exact_non_limitless_source"),
        "review": ("review_required", False, "agent_review_candidate"),
        "unresolved": ("unresolved", True, "search_exact_non_limitless_source"),
    }
    for bucket, (status, retryable, next_action) in settings.items():
        for row in document[bucket]:
            variant_id = int(row["variantId"])
            target = row.get("target") if isinstance(row.get("target"), Mapping) else {}
            candidate = row.get("selectedCandidate") if isinstance(row.get("selectedCandidate"), Mapping) else {}
            source = str(candidate.get("source") or "") or None
            source_id = str(candidate.get("sourceId") or "") or None
            item_evidence = dict(evidence)
            item_evidence["detailArtifact"] = str(row.get("detailArtifact") or "")
            items.append(_queue_item(
                category="image_source_family_child",
                cohort="release",
                status=status,
                variant_id=variant_id,
                asset_id=target.get("assetId") if isinstance(target.get("assetId"), int) else None,
                source_id=source_id,
                source_family=LIMITLESS_FAMILY,
                tcg=str(target.get("game") or "one-piece"),
                language=str(target.get("language") or "") or None,
                reason=str(row.get("decisionReason") or status),
                retryable=retryable,
                next_action=next_action,
                attempt=int(row.get("attemptCount") or 1),
                evidence=item_evidence,
                metadata={
                    "candidateSource": source,
                    "candidatePath": candidate.get("localPath"),
                    "nextSources": row.get("nextSources", []),
                    "candidateMustNeverBePromoted": status == "candidate_rejected",
                },
            ))
    for variant_id in sorted(quarantine_variants - set(release_rows)):
        asset = asset_by_variant[variant_id]
        items.append(_queue_item(
            category="image_source_family_child",
            cohort="outer",
            status="source_family_contaminated",
            variant_id=variant_id,
            asset_id=int(asset["assetId"]),
            source_family=LIMITLESS_FAMILY,
            tcg="one-piece",
            reason="known SAMPLE source family; expected card language must be resolved before replacement",
            retryable=True,
            next_action="resolve_exact_identity_then_search_non_limitless_raw_front",
            evidence=quarantine_evidence,
            metadata={"contentSha256": asset.get("contentSha256"), "languageGuard": "resolve_expected_language_before_replacement"},
        ))
    languages = Counter(item["requiredLanguage"] for item in items if item["category"] == "image_source_family_child")
    if languages != Counter({"en": 130, "ja": 16, None: 174}):
        raise RetryQueueError(f"Limitless language split must be en=130 ja=16, got {dict(languages)}")
    return items


def _outer_missing_items(rows: list[dict[str, Any]], evidence: Mapping[str, str]) -> list[dict[str, Any]]:
    if len(rows) != 244:
        raise RetryQueueError("outer missing raw-front receipt must contain exactly 244 rows")
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in rows:
        variant_id = row.get("variantId")
        if not isinstance(variant_id, int) or variant_id in seen:
            raise RetryQueueError("outer missing receipt has invalid or duplicate variantId")
        seen.add(variant_id)
        has_identity = bool(str(row.get("printingIdentityStatus") or ""))
        is_review = has_identity
        items.append(_queue_item(
            category="image_raw_front_missing",
            cohort="outer",
            status="review_required" if is_review else "blocked_missing_printing_identity",
            variant_id=variant_id,
            tcg=str(row.get("tcg") or "") or None,
            language=str(row.get("language") or "") or None,
            reason="missing raw-front image" if is_review else "missing canonical printing identity before image matching",
            retryable=is_review,
            next_action="search_exact_authoritative_raw_front" if is_review else "resolve_canonical_printing_identity",
            evidence=evidence,
            metadata={"opaqueId": row.get("opaqueId"), "exactSourceIdentities": row.get("exactSourceIdentities", [])},
        ))
    if sum(item["status"] == "blocked_missing_printing_identity" for item in items) != 243:
        raise RetryQueueError("outer missing receipt must preserve 243 printing-identity blocks")
    return items


def _price_sales_items(rows: list[dict[str, Any]], evidence: Mapping[str, str]) -> list[dict[str, Any]]:
    if len(rows) != 378:
        raise RetryQueueError("price/sales gap receipt must contain exactly 378 rows")
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    states: Counter[str] = Counter()
    for row in rows:
        variant_id = row.get("variantId")
        state = str(row.get("state") or "")
        if not isinstance(variant_id, int) or variant_id in seen or state not in {"review", "unresolved"}:
            raise RetryQueueError("price/sales receipt has invalid variantId or state")
        seen.add(variant_id)
        states[state] += 1
        items.append(_queue_item(
            category="psa10_price_sales_gap",
            cohort="release",
            status=state,
            variant_id=variant_id,
            tcg=str(row.get("tcg") or "") or None,
            language=str(row.get("language") or "") or None,
            reason="; ".join(sorted(set([*row.get("priceGapReasons", []), *row.get("salesGapReasons", [])]))) or "psa10_price_sales_gap",
            retryable=state == "review",
            next_action=str(row.get("nextAction") or ("agent_review_exact_binding" if state == "unresolved" else "fetch_exact_psa10")),
            evidence=evidence,
            metadata={
                "name": row.get("name"), "set": row.get("set"), "collectorNumber": row.get("collectorNumber"),
                "exactBindings": row.get("exactBindings", {}), "salesPolicy": SALES_POLICY,
            },
        ))
    if states != Counter({"review": 204, "unresolved": 174}):
        raise RetryQueueError(f"price/sales state split must be review=204 unresolved=174, got {dict(states)}")
    return items


def _canonical_release_next_action(blockers: set[str]) -> str:
    if "canonical_printing_missing" in blockers:
        return "resolve_canonical_printing_identity"
    if blockers & {
        "image_sample_or_placeholder",
        "image_source_known_sample",
        "image_card_number_mismatch",
        "image_language_match_failed",
        "image_tcg_mismatch",
    }:
        return "replace_with_exact_authoritative_raw_front"
    if "image_canvas_geometry_invalid" in blockers:
        return "normalize_or_replace_image_geometry"
    if any(blocker.startswith("image_") for blocker in blockers):
        return "complete_image_qc_and_private_derivatives"
    if blockers & {
        "exact_psa10_price_insufficient_independent_sources",
        "exact_psa10_price_missing",
        "exact_psa10_price_source_spread_gt_2x",
        "exact_psa10_price_stale",
        "price_anchor_source_method_mismatch",
        "price_source_identity_not_exact",
        "psa10_sales_30d_insufficient",
        "psa10_sales_30d_missing",
        "sale_source_identity_not_exact",
    }:
        return "fetch_and_bind_exact_psa10_evidence"
    if blockers & {"market_cap_current_price_mismatch", "market_cap_not_materialized"}:
        return "rematerialize_market_cap"
    if "gemrate_identity_ambiguous" in blockers:
        return "resolve_population_identity"
    return "agent_review_release_blockers"


def _canonical_release_items(
    document: Mapping[str, Any],
    evidence: Mapping[str, str],
) -> list[dict[str, Any]]:
    database = document.get("database")
    counts = document.get("counts")
    release_gate = document.get("releaseGate")
    cards = document.get("cards")
    if (
        document.get("readOnly") is not True
        or not isinstance(database, Mapping)
        or database.get("authority") != "canonical_mysql"
        or database.get("name") != "cardz_market_cap"
        or not isinstance(counts, Mapping)
        or not isinstance(release_gate, Mapping)
        or not isinstance(cards, list)
    ):
        raise RetryQueueError("canonical QC must be a read-only cardz_market_cap report")
    if int(counts.get("catalog") or -1) != len(cards):
        raise RetryQueueError("canonical QC catalog count does not match its card rows")

    failed = [
        row
        for row in cards
        if isinstance(row, Mapping) and row.get("decision") == "failed"
    ]
    passed = [
        row
        for row in cards
        if isinstance(row, Mapping) and row.get("decision") == "passed"
    ]
    if (
        len(failed) != int(counts.get("releaseBlockedQualified") or -1)
        or len(passed) != int(counts.get("releaseReadyQualified") or -1)
        or len(failed) != int(release_gate.get("blockerCardCount") or -1)
    ):
        raise RetryQueueError("canonical QC release counts do not reconcile")

    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in failed:
        variant_id = row.get("variantId")
        blockers_value = row.get("blockers")
        if (
            not isinstance(variant_id, int)
            or variant_id in seen
            or not isinstance(blockers_value, list)
            or not blockers_value
            or not all(isinstance(value, str) and value for value in blockers_value)
        ):
            raise RetryQueueError("canonical QC failed card has invalid variantId or blockers")
        seen.add(variant_id)
        blockers = set(blockers_value)
        facts = row.get("facts") if isinstance(row.get("facts"), Mapping) else {}
        identity = facts.get("identity") if isinstance(facts.get("identity"), Mapping) else {}
        items.append(_queue_item(
            category="canonical_release_blocker",
            cohort="release",
            status="blocked",
            variant_id=variant_id,
            tcg=str(identity.get("tcg") or row.get("tcg") or "") or None,
            language=str(identity.get("cardLanguage") or "") or None,
            reason="; ".join(sorted(blockers)),
            retryable=True,
            next_action=_canonical_release_next_action(blockers),
            evidence=evidence,
            metadata={
                "publicId": row.get("id"),
                "marketRank": row.get("marketRank"),
                "blockers": sorted(blockers),
                "printingSha256": identity.get("printingSha256"),
                "qcEvidenceSha256": row.get("evidenceSha256"),
            },
        ))
    return items


def build_queue(
    *,
    geometry_path: Path,
    limitless_path: Path,
    quarantine_path: Path,
    outer_missing_path: Path,
    price_sales_path: Path,
    canonical_qc_path: Path | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    geometry = _load_json(geometry_path)
    limitless = _load_json(limitless_path)
    quarantine = _load_json(quarantine_path)
    outer_missing = _load_json(outer_missing_path)
    if not isinstance(geometry, Mapping) or not isinstance(limitless, Mapping) or not isinstance(outer_missing, list):
        raise RetryQueueError("retry inputs must be their declared JSON shape")
    evidence = {
        "geometry": _evidence(geometry_path),
        "limitless": _evidence(limitless_path),
        "quarantine": _evidence(quarantine_path),
        "outerMissing": _evidence(outer_missing_path),
        "priceSales": _evidence(price_sales_path),
    }
    canonical_qc: Mapping[str, Any] | None = None
    if canonical_qc_path is not None:
        loaded_canonical_qc = _load_json(canonical_qc_path)
        if not isinstance(loaded_canonical_qc, Mapping):
            raise RetryQueueError("canonical QC receipt must be a JSON object")
        canonical_qc = loaded_canonical_qc
        evidence["canonicalQc"] = _evidence(canonical_qc_path)
    items = [
        *_geometry_items(geometry, evidence["geometry"]),
        *_limitless_items(limitless, quarantine, evidence["limitless"], evidence["quarantine"]),
        *_outer_missing_items([dict(row) for row in outer_missing if isinstance(row, Mapping)], evidence["outerMissing"]),
        *_price_sales_items(_load_jsonl(price_sales_path), evidence["priceSales"]),
    ]
    if canonical_qc is not None:
        items.extend(_canonical_release_items(canonical_qc, evidence["canonicalQc"]))
    keys = [str(item["itemKey"]) for item in items]
    if len(keys) != len(set(keys)):
        raise RetryQueueError("retry queue item keys must be unique")
    items.sort(key=lambda item: str(item["itemKey"]))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "cardz-pm-agent-retry-queue",
        "generatedAt": generated_at or max(
            str(geometry.get("generatedAt") or ""),
            str(limitless.get("generatedAt") or ""),
            str(quarantine.get("generatedAt") or ""),
            str(canonical_qc.get("asOf") or "") if canonical_qc is not None else "",
        ) or None,
        "sideEffects": "private_report_only",
        "salesPolicy": SALES_POLICY,
        "sourcePolicies": {LIMITLESS_FAMILY: {"forbidden": True, "quarantineChildCount": 320, "releaseCohortOverlap": 146, "outerNonRelease": 174, "jaReplacementGuard": "ja_requires_ja_replacement"}},
        "inputEvidence": evidence,
        "canonicalCoverage": {
            "blocked": int(canonical_qc["counts"]["releaseBlockedQualified"]),
            "ready": int(canonical_qc["counts"]["releaseReadyQualified"]),
            "catalog": int(canonical_qc["counts"]["catalog"]),
        } if canonical_qc is not None else None,
        "count": len(items),
        "retryableCount": sum(item["retryable"] is True for item in items),
        "byCohort": dict(sorted(Counter(str(item["cohort"]) for item in items).items())),
        "byCategory": dict(sorted(Counter(str(item["category"]) for item in items).items())),
        "byStatus": dict(sorted(Counter(str(item["status"]) for item in items).items())),
        "items": items,
    }


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.next")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def write_report(queue: Mapping[str, Any], *, output_dir: Path) -> dict[str, Any]:
    queue_path = output_dir / "agent-retry-queue.json"
    _write_json(queue_path, queue)
    report = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "cardz-pm-agent-retry-queue-report",
        "queuePath": _relative(queue_path),
        "queueSha256": sha256_file(queue_path),
        "count": queue["count"],
        "retryableCount": queue["retryableCount"],
        "byCohort": queue["byCohort"],
        "byCategory": queue["byCategory"],
        "byStatus": queue["byStatus"],
        "canonicalCoverage": queue.get("canonicalCoverage"),
        "salesPolicy": SALES_POLICY,
        "sideEffects": "private_report_only",
    }
    _write_json(output_dir / "report.json", report)
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", type=Path, default=DEFAULT_GEOMETRY)
    parser.add_argument("--limitless", type=Path, default=DEFAULT_LIMITLESS)
    parser.add_argument("--quarantine", type=Path, default=DEFAULT_QUARANTINE)
    parser.add_argument("--outer-missing", type=Path, default=DEFAULT_OUTER_MISSING)
    parser.add_argument("--price-sales", type=Path, default=DEFAULT_PRICE_SALES)
    parser.add_argument("--canonical-qc", type=Path, default=DEFAULT_CANONICAL_QC)
    parser.add_argument("--output-dir", type=Path, default=REPORT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    queue = build_queue(
        geometry_path=args.geometry.resolve(),
        limitless_path=args.limitless.resolve(),
        quarantine_path=args.quarantine.resolve(),
        outer_missing_path=args.outer_missing.resolve(),
        price_sales_path=args.price_sales.resolve(),
        canonical_qc_path=args.canonical_qc.resolve(),
    )
    report = write_report(queue, output_dir=args.output_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
