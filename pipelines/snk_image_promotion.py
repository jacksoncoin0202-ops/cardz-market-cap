#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""SNK image promotion hard-gated workflow.

pending land → QC-ranked queue → re-verify approve → hashed receipt → DB/manifest

Hard gates (2026-07-29):
  - Queue ONLY from immutable canonical-db-qc report (+ receipt), ranked by marketRank.
  - Approve requires pending queue item; NO DB-only bypass.
  - Approve re-verifies live DB: exact-one SNK, asset/raw/sourcePath, fingerprint,
    canonical printing hash (must match queue; null printing refuses approve).
  - Receipt binds printing + qc run + card evidence + image hashes.
  - Promote only the queued assetId/sourcePath; manifest auto from promoted facts.

Usage:
  python -X utf8 pipelines\\snk_image_promotion.py multi-snk-audit
  python -X utf8 pipelines\\snk_image_promotion.py identity-quarantine-receipts --write
  python -X utf8 pipelines\\snk_image_promotion.py demote-false-public --write
  python -X utf8 pipelines\\snk_image_promotion.py enroll-from-qc --qc-run qc_20260729_full_03 --limit 34 --write
  python -X utf8 pipelines\\snk_image_promotion.py list-queue --status pending
  python -X utf8 pipelines\\snk_image_promotion.py approve --variant-id N --operator human --write
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from PIL import Image

from image_geometry_qc import inspect_path
from image_source_qc import classify_content_sha256, classify_source

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

REPORT_DIR = ROOT / "data" / "runtime" / "private-source-map" / "qualified-pool-reports"
REVIEW_DIR = ROOT / "data" / "runtime" / "private-source-map" / "snk-image-review"
QUEUE_PATH = REVIEW_DIR / "queue.jsonl"
RECEIPT_DIR = REVIEW_DIR / "receipts"
IDENTITY_RECEIPT_DIR = REVIEW_DIR / "identity-quarantine"
MANIFEST_PATH = ROOT / "manifests" / "image-qc.json"
QC_ROOT = ROOT / "data" / "runtime" / "private-reports" / "canonical-db-qc"

QC_VERSION_PENDING = "snk-image-v1"
QC_VERSION_PROMOTED = "snk-image-promoted-v1"
SEMANTIC_PENDING = "pending_review"
SEMANTIC_CONFIRMED = "human_or_vision_confirmed"

IMAGE_BLOCKER = "image_not_human_or_vision_confirmed"
PRINTING_BLOCKER = "canonical_printing_missing"

# Quick-win pilot: image gate open + optional only printing as other hard identity gap
DEFAULT_QUICK_WIN_EXTRA_ALLOWED = frozenset({PRINTING_BLOCKER})
TCG_ALIASES = {
    "op": "one-piece",
    "opcg": "one-piece",
    "one-piece": "one-piece",
    "onepiece": "one-piece",
    "one-piece-card-game": "one-piece",
    "ptcg": "pokemon",
    "pokemon": "pokemon",
    "pokémon": "pokemon",
}
PRINTING_PLACEHOLDERS = frozenset(
    {
        "unknown",
        "unspecified",
        "n/a",
        "na",
        "none",
        "null",
        "tbd",
        "placeholder",
        "sample",
    }
)


class PromotionGateError(Exception):
    """Hard gate failure — never promote."""


def assert_public_image_policy(
    item: Mapping[str, Any], *, live: Mapping[str, Any], asset_path: Path
) -> None:
    """Reject a promotion unless its immutable bytes and source pass every gate."""

    if item.get("languageMatchEvidence") is not True:
        raise PromotionGateError("source_language_evidence_missing")
    content = classify_content_sha256(str(live.get("contentSha256") or ""))
    if content.get("status") == "reject":
        raise PromotionGateError(f"content_policy_rejected:{content.get('ruleId')}")
    source = classify_source(
        str(live.get("sourcePath") or ""),
        tcg_code=str(live.get("tcg") or ""),
        width_px=live.get("width"),
        height_px=live.get("height"),
    )
    if source.get("status") == "reject":
        raise PromotionGateError(f"source_policy_rejected:{source.get('ruleId')}")
    if inspect_path(asset_path).get("status") != "passed":
        raise PromotionGateError("image_geometry_failed")
    assert_public_derivatives_ready(str(live.get("contentSha256") or ""), asset_path)


def assert_public_derivatives_ready(content_sha256: str, master_path: Path) -> None:
    """Require the exact responsive files before a pointer may become public."""

    if len(content_sha256) != 64:
        raise PromotionGateError("content_sha256_invalid")
    for suffix, expected_size in (("200", (200, 280)), ("600", (429, 600))):
        derivative = master_path.parent / f"{content_sha256}_{suffix}.webp"
        if not derivative.is_file():
            raise PromotionGateError(f"public_derivative_missing:{suffix}")
        try:
            with Image.open(derivative) as image:
                image.load()
                if image.format != "WEBP" or image.size != expected_size:
                    raise PromotionGateError(f"public_derivative_invalid:{suffix}")
        except PromotionGateError:
            raise
        except Exception as error:
            raise PromotionGateError(f"public_derivative_invalid:{suffix}") from error


def load_env() -> None:
    env_path = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\r"))


def db():
    import pymysql

    load_env()
    os.environ["CARDZ_DB_HOST"] = "127.0.0.1"
    return pymysql.connect(
        host="127.0.0.1",
        port=int(os.environ.get("CARDZ_DB_PORT", "3308")),
        user=os.environ.get("CARDZ_DB_USER", "cardz"),
        password=os.environ.get("CARDZ_DB_PASSWORD") or "",
        database=os.environ.get("CARDZ_DB_NAME", "cardz_market_cap"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def sha256_obj(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj)).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Pure hard-gate helpers (unit-tested)
# ---------------------------------------------------------------------------


def canonical_tcg(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    normalized = "-".join(value.strip().casefold().replace("_", "-").split())
    canonical = TCG_ALIASES.get(normalized)
    if canonical is None:
        raise PromotionGateError(f"unsupported_tcg_filter:{value}")
    return canonical


def canonical_printing_sha256(row: Mapping[str, Any]) -> str | None:
    fields = (
        row.get("printing_tcg_code"),
        row.get("printing_set_name"),
        row.get("printing_collector_number"),
        row.get("edition_code"),
        row.get("parallel_code"),
        row.get("finish_code"),
    )
    if str(row.get("printing_status") or "") != "canonical":
        return None
    normalized = [str(value or "").strip().casefold() for value in fields]
    if any(not value or value in PRINTING_PLACEHOLDERS for value in normalized):
        return None
    expected = hashlib.sha256("|".join(normalized).encode("utf-8")).hexdigest()
    declared = str(row.get("canonical_printing_sha256") or "").strip().casefold()
    return declared if declared == expected else None


def fingerprint_payload(
    *,
    variant_id: int,
    snk_id: str,
    content_sha256: str,
    raw_sha256: str | None,
    source_path: str | None,
    asset_id: int | None,
    canonical_printing_sha256: str | None,
    qc_run_id: str,
    qc_report_sha256: str,
    qc_receipt_sha256: str,
    card_evidence_sha256: str | None,
    snk_identity_sha256: str | None,
) -> dict[str, Any]:
    return {
        "variantId": int(variant_id),
        "snkId": str(snk_id),
        "contentSha256": str(content_sha256),
        "rawSha256": str(raw_sha256 or ""),
        "sourcePath": str(source_path or ""),
        "assetId": int(asset_id or 0),
        "canonicalPrintingSha256": str(canonical_printing_sha256 or ""),
        "qcRunId": str(qc_run_id),
        "qcReportSha256": str(qc_report_sha256),
        "qcReceiptSha256": str(qc_receipt_sha256),
        "cardEvidenceSha256": str(card_evidence_sha256 or ""),
        "snkIdentitySha256": str(snk_identity_sha256 or ""),
    }


def compute_queue_fingerprint(payload: dict[str, Any]) -> str:
    return sha256_obj(fingerprint_payload(**{
        "variant_id": payload["variantId"],
        "snk_id": payload["snkId"],
        "content_sha256": payload["contentSha256"],
        "raw_sha256": payload.get("rawSha256"),
        "source_path": payload.get("sourcePath"),
        "asset_id": payload.get("assetId"),
        "canonical_printing_sha256": payload.get("canonicalPrintingSha256"),
        "qc_run_id": payload["qcRunId"],
        "qc_report_sha256": payload["qcReportSha256"],
        "qc_receipt_sha256": payload["qcReceiptSha256"],
        "card_evidence_sha256": payload.get("cardEvidenceSha256"),
        "snk_identity_sha256": payload.get("snkIdentitySha256"),
    }))


def is_quick_win_card(
    blockers: list[str],
    *,
    require_image_blocker: bool = True,
    extra_allowed: frozenset[str] = DEFAULT_QUICK_WIN_EXTRA_ALLOWED,
) -> bool:
    """True if card is blocked only by image confirm (+ optional printing)."""
    b = set(blockers or [])
    if require_image_blocker and IMAGE_BLOCKER not in b:
        return False
    # drop image blocker; remaining must be subset of extra_allowed
    rest = b - {IMAGE_BLOCKER}
    return rest.issubset(extra_allowed)


def select_ranked_image_queue(
    cards: list[dict[str, Any]],
    *,
    limit: int,
    tcg: str | None = None,
    quick_win_only: bool = True,
    extra_allowed: frozenset[str] = DEFAULT_QUICK_WIN_EXTRA_ALLOWED,
) -> list[dict[str, Any]]:
    """Select cards from QC report, sorted by marketRank ascending (None last)."""
    tcg = canonical_tcg(tcg)
    out: list[dict[str, Any]] = []
    for c in cards:
        if tcg and str(c.get("tcg") or "") != tcg:
            continue
        blockers = list(c.get("blockers") or [])
        if quick_win_only and not is_quick_win_card(blockers, extra_allowed=extra_allowed):
            continue
        if IMAGE_BLOCKER not in blockers and not quick_win_only:
            continue
        out.append(c)

    def _rank_key(c: dict[str, Any]) -> tuple:
        mr = c.get("marketRank")
        if isinstance(mr, int):
            return (0, mr, int(c.get("variantId") or 0))
        return (1, 10**9, int(c.get("variantId") or 0))

    out.sort(key=_rank_key)
    if limit > 0:
        out = out[:limit]
    return out


def verify_live_against_queue(
    queue_item: dict[str, Any],
    live: dict[str, Any],
) -> None:
    """Raise PromotionGateError if live state does not match queued fingerprint."""
    if str(queue_item.get("status") or "") != "pending":
        raise PromotionGateError("queue_item_not_pending")

    snk_ids = live.get("snkIds") or []
    if len(snk_ids) != 1:
        raise PromotionGateError(f"multi_or_zero_snk:{len(snk_ids)}")
    if str(snk_ids[0]) != str(queue_item.get("snkId")):
        raise PromotionGateError("snk_id_drift")

    if int(live.get("assetId") or 0) != int(queue_item.get("assetId") or 0):
        raise PromotionGateError("asset_id_drift")
    if str(live.get("contentSha256") or "") != str(queue_item.get("contentSha256") or ""):
        raise PromotionGateError("content_sha_drift")
    if str(live.get("rawSha256") or "") != str(queue_item.get("rawSha256") or ""):
        raise PromotionGateError("raw_sha_drift")
    if str(live.get("sourcePath") or "") != str(queue_item.get("sourcePath") or ""):
        raise PromotionGateError("source_path_drift")

    live_print = str(live.get("canonicalPrintingSha256") or "")
    q_print = str(queue_item.get("canonicalPrintingSha256") or "")
    if not live_print:
        raise PromotionGateError("canonical_printing_missing")
    if q_print != live_print:
        raise PromotionGateError("printing_hash_drift")

    # recompute fingerprint from live + queue's qc binding
    live_fp = compute_queue_fingerprint(
        {
            "variantId": int(queue_item["variantId"]),
            "snkId": str(snk_ids[0]),
            "contentSha256": live["contentSha256"],
            "rawSha256": live.get("rawSha256"),
            "sourcePath": live.get("sourcePath"),
            "assetId": live.get("assetId"),
            "canonicalPrintingSha256": live_print or None,
            "qcRunId": queue_item.get("qcRunId"),
            "qcReportSha256": queue_item.get("qcReportSha256"),
            "qcReceiptSha256": queue_item.get("qcReceiptSha256"),
            "cardEvidenceSha256": queue_item.get("cardEvidenceSha256"),
            "snkIdentitySha256": live.get("snkIdentitySha256"),
        }
    )
    if live_fp != str(queue_item.get("queueFingerprint") or ""):
        raise PromotionGateError("queue_fingerprint_stale")


def build_approval_receipt(
    queue_item: dict[str, Any],
    *,
    operator: str,
    note: str,
    approved_at: str,
) -> dict[str, Any]:
    body = {
        "type": "snk_image_approval_receipt",
        "schemaVersion": 2,
        "variantId": int(queue_item["variantId"]),
        "opaqueId": queue_item.get("opaqueId"),
        "snkId": str(queue_item.get("snkId")),
        "assetId": int(queue_item.get("assetId") or 0),
        "contentSha256": queue_item.get("contentSha256"),
        "rawSha256": queue_item.get("rawSha256"),
        "sourcePath": queue_item.get("sourcePath"),
        "canonicalPrintingSha256": queue_item.get("canonicalPrintingSha256") or "",
        "qcRunId": queue_item.get("qcRunId"),
        "qcReportSha256": queue_item.get("qcReportSha256"),
        "qcReceiptSha256": queue_item.get("qcReceiptSha256"),
        "cardEvidenceSha256": queue_item.get("cardEvidenceSha256") or "",
        "snkIdentitySha256": queue_item.get("snkIdentitySha256") or "",
        "queueFingerprint": queue_item.get("queueFingerprint"),
        "operator": operator,
        "note": note or "",
        "approvedAt": approved_at,
        "width": queue_item.get("width"),
        "height": queue_item.get("height"),
        "marketRank": queue_item.get("marketRank"),
        "tcg": queue_item.get("tcg"),
    }
    body["receiptSha256"] = sha256_obj(body)
    return body


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------


def read_queue() -> list[dict[str, Any]]:
    if not QUEUE_PATH.is_file():
        return []
    rows = []
    for line in QUEUE_PATH.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def write_queue(rows: list[dict[str, Any]]) -> None:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    tmp = QUEUE_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
    tmp.replace(QUEUE_PATH)


def resolve_qc_run(qc_run: str | None) -> Path:
    if qc_run:
        p = QC_ROOT / qc_run
        if not p.is_dir():
            raise PromotionGateError(f"qc_run_not_found:{qc_run}")
        return p
    # Match the machine-status authority: newest immutable receipt by mtime.
    best = None
    best_m = -1.0
    if not QC_ROOT.is_dir():
        raise PromotionGateError("qc_root_missing")
    for d in QC_ROOT.iterdir():
        receipt = d / "receipt.json"
        if receipt.is_file():
            m = receipt.stat().st_mtime
            if m > best_m:
                best_m = m
                best = d
    if best is None:
        raise PromotionGateError("no_qc_report")
    return best


def load_qc_bundle(
    run_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any], str, str]:
    receipt_path = run_dir / "receipt.json"
    report_path = run_dir / "report.json"
    if not receipt_path.is_file() or not report_path.is_file():
        raise PromotionGateError(f"qc_bundle_incomplete:{run_dir}")
    receipt_bytes = receipt_path.read_bytes()
    report_bytes = report_path.read_bytes()
    try:
        receipt = json.loads(receipt_bytes)
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PromotionGateError("qc_bundle_invalid_json") from exc

    if not isinstance(receipt, dict) or not isinstance(report, dict):
        raise PromotionGateError("qc_bundle_hash_or_contract_invalid")
    report_sha = hashlib.sha256(report_bytes).hexdigest()
    receipt_sha = hashlib.sha256(receipt_bytes).hexdigest()
    counts = receipt.get("counts")
    report_counts = report.get("counts")
    valid = (
        receipt.get("runId") == run_dir.name
        and report.get("runId") == run_dir.name
        and receipt.get("database") == "cardz_market_cap"
        and receipt.get("readOnly") is True
        and receipt.get("asOf") == report.get("asOf")
        and receipt.get("status") == report.get("status")
        and isinstance(counts, dict)
        and isinstance(report_counts, dict)
        and all(report_counts.get(key) == value for key, value in counts.items())
        and receipt.get("releaseGate") == report.get("releaseGate")
        and receipt.get("reportSha256") == report_sha
    )
    if not valid:
        raise PromotionGateError("qc_bundle_hash_or_contract_invalid")
    return receipt, report, report_sha, receipt_sha


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(character in "0123456789abcdef" for character in text)


def load_identity_quarantine_state() -> tuple[
    dict[int, list[dict[str, Any]]], dict[int, dict[str, Any]]
]:
    quarantines: dict[str, dict[str, Any]] = {}
    resolutions: dict[str, dict[str, Any]] = {}
    if not IDENTITY_RECEIPT_DIR.is_dir():
        return {}, {}
    for path in sorted(IDENTITY_RECEIPT_DIR.glob("*.json")):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PromotionGateError(f"identity_quarantine_receipt_invalid:{path.name}") from exc
        declared = str(receipt.get("receiptSha256") or "")
        body = dict(receipt)
        body.pop("receiptSha256", None)
        if (
            path.stem != declared
            or sha256_obj(body) != declared
            or not isinstance(receipt.get("variantId"), int)
        ):
            raise PromotionGateError(f"identity_quarantine_receipt_invalid:{path.name}")
        if receipt.get("type") == "identity_quarantine":
            quarantines[declared] = receipt
            continue
        if receipt.get("type") != "identity_quarantine_resolution":
            raise PromotionGateError(f"identity_quarantine_receipt_invalid:{path.name}")
        supersedes = str(receipt.get("supersedesReceiptSha256") or "")
        selected_snk_id = str(receipt.get("selectedSnkId") or "")
        try:
            resolved_at = datetime.fromisoformat(
                str(receipt.get("resolvedAt") or "").replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError as exc:
            raise PromotionGateError(
                f"identity_quarantine_resolution_invalid:{path.name}"
            ) from exc
        if (
            receipt.get("schemaVersion") != 1
            or receipt.get("policyVersion") != "identity-quarantine-resolution-v1"
            or not _is_sha256(supersedes)
            or not selected_snk_id
            or not _is_sha256(receipt.get("preSourceIdentitySha256"))
            or not _is_sha256(receipt.get("postSourceIdentitySha256"))
            or not str(receipt.get("actor") or "").strip()
            or resolved_at > datetime.now(timezone.utc)
            or supersedes in resolutions
        ):
            raise PromotionGateError(
                f"identity_quarantine_resolution_invalid:{path.name}"
            )
        resolutions[supersedes] = receipt

    active: dict[int, list[dict[str, Any]]] = {}
    resolved: dict[int, dict[str, Any]] = {}
    by_variant: dict[int, list[str]] = {}
    for receipt_sha, receipt in quarantines.items():
        by_variant.setdefault(int(receipt["variantId"]), []).append(receipt_sha)
    for supersedes, resolution in resolutions.items():
        original = quarantines.get(supersedes)
        if (
            original is None
            or int(original["variantId"]) != int(resolution["variantId"])
        ):
            raise PromotionGateError(
                f"identity_quarantine_resolution_unknown_or_mismatched:{supersedes}"
            )
        original_snk_ids = sorted(
            {
                str(value)
                for value in (
                    list(original.get("snkIds") or [])
                    + ([original["snkId"]] if original.get("snkId") else [])
                )
                if str(value)
            }
        )
        expected_pre_sha = sha256_obj(
            {"variantId": int(original["variantId"]), "snkIds": original_snk_ids}
        )
        if resolution.get("preSourceIdentitySha256") != expected_pre_sha:
            raise PromotionGateError(
                f"identity_quarantine_resolution_prestate_mismatch:{supersedes}"
            )
    for variant_id, receipt_shas in by_variant.items():
        unresolved = [receipt_sha for receipt_sha in receipt_shas if receipt_sha not in resolutions]
        if unresolved:
            active[variant_id] = [quarantines[receipt_sha] for receipt_sha in unresolved]
            continue
        selected_ids = {
            str(resolutions[receipt_sha]["selectedSnkId"]) for receipt_sha in receipt_shas
        }
        post_hashes = {
            str(resolutions[receipt_sha]["postSourceIdentitySha256"])
            for receipt_sha in receipt_shas
        }
        if len(selected_ids) != 1 or len(post_hashes) != 1:
            raise PromotionGateError(
                f"identity_quarantine_resolution_conflict:{variant_id}"
            )
        resolved[variant_id] = {
            "selectedSnkId": selected_ids.pop(),
            "postSourceIdentitySha256": post_hashes.pop(),
        }
    return active, resolved


def load_active_identity_quarantine() -> dict[int, list[dict[str, Any]]]:
    return load_identity_quarantine_state()[0]


def assert_not_quarantined(variant_id: int, *, snk_id: str | None = None) -> None:
    active, resolved = load_identity_quarantine_state()
    cases = active.get(int(variant_id), [])
    if cases:
        reasons = ",".join(sorted({str(case.get("reason") or "") for case in cases}))
        raise PromotionGateError(f"identity_quarantined:{variant_id}:{reasons}")
    resolution = resolved.get(int(variant_id))
    if resolution is not None and snk_id is not None:
        if str(resolution.get("selectedSnkId") or "") != str(snk_id):
            raise PromotionGateError(f"identity_quarantine_resolution_drift:{variant_id}")


def fetch_live_image_state(cur, variant_id: int) -> dict[str, Any]:
    cur.execute(
        """
        SELECT external_entity_id, match_status FROM catalog_source_identity
        WHERE source_code='snkrdunk' AND variant_id=%s AND match_status='exact'
        ORDER BY external_entity_id
        """,
        (variant_id,),
    )
    snk_rows = list(cur.fetchall())
    if any(str(row.get("match_status") or "") != "exact" for row in snk_rows):
        raise PromotionGateError("snk_identity_not_exact")
    snk_ids = [str(row["external_entity_id"]) for row in snk_rows]

    cur.execute(
        """
        SELECT identity_status AS printing_status,
               tcg_code AS printing_tcg_code,
               set_name AS printing_set_name,
               collector_number AS printing_collector_number,
               edition_code, parallel_code, finish_code,
               canonical_printing_sha256
        FROM catalog_printing_identity WHERE variant_id=%s LIMIT 1
        """,
        (variant_id,),
    )
    pr = cur.fetchone() or {}
    printing_sha = canonical_printing_sha256(pr)

    if len(snk_ids) != 1:
        return {
            "snkIds": snk_ids,
            "canonicalPrintingSha256": printing_sha,
            "printingStatus": pr.get("printing_status"),
        }
    if not snk_ids[0].isdigit():
        raise PromotionGateError("snk_id_invalid")

    prefix = f"snkrdunk:{snk_ids[0]}:"
    cur.execute(
        """
        SELECT source_path, source_version_sha256
        FROM market_image_source_pointer
        WHERE variant_id=%s AND image_kind='raw_front'
          AND LEFT(source_path, CHAR_LENGTH(%s))=%s
        ORDER BY observed_at DESC, source_version_sha256 DESC
        LIMIT 1
        """,
        (variant_id, prefix, prefix),
    )
    pointer = cur.fetchone()
    if not pointer:
        raise PromotionGateError("snk_pointer_missing")

    cur.execute(
        """
        SELECT a.id AS asset_id, a.content_sha256, a.source_version_sha256 AS raw_sha,
               a.width_px, a.height_px, v.opaque_id, v.canonical_name,
               v.collector_number, v.tcg_code
        FROM market_image_asset a
        JOIN catalog_variant v ON v.id=a.variant_id
        WHERE a.variant_id=%s AND a.image_kind='raw_front'
          AND a.source_version_sha256=%s
        ORDER BY a.id DESC
        LIMIT 1
        """,
        (variant_id, pointer["source_version_sha256"]),
    )
    asset = cur.fetchone()
    if not asset:
        raise PromotionGateError("snk_pointer_asset_missing")

    return {
        "snkIds": snk_ids,
        "snkIdentitySha256": sha256_obj(
            {
                "variantId": variant_id,
                "sourceCode": "snkrdunk",
                "externalEntityId": snk_ids[0],
                "matchStatus": "exact",
            }
        ),
        "assetId": int(asset["asset_id"]),
        "contentSha256": asset["content_sha256"],
        "rawSha256": asset.get("raw_sha"),
        "sourcePath": pointer["source_path"],
        "width": asset.get("width_px"),
        "height": asset.get("height_px"),
        "opaqueId": asset.get("opaque_id"),
        "name": asset.get("canonical_name"),
        "collector": asset.get("collector_number"),
        "tcg": asset.get("tcg_code"),
        "canonicalPrintingSha256": printing_sha,
        "printingStatus": pr.get("printing_status"),
    }


def find_asset_file(content_sha: str) -> Path | None:
    for base in (
        ROOT / "data" / "public" / "market-assets",
        ROOT / "apps" / "web" / "public" / "market-assets",
    ):
        p = base / f"{content_sha}.webp"
        if p.is_file():
            return p
    return None


def rebuild_manifest_record_from_db(cur, variant_id: int, receipt: dict[str, Any]) -> dict[str, Any]:
    """Manifest entry is fully derived from DB + receipt — never hand-authored."""
    # Filter QC by promoted version: one asset can keep prior qc_version rows
    # (e.g. snk-image-v1 + snk-image-promoted-v1) and an unfiltered JOIN returns
    # multiple rows → false manifest_rebuild_missing_row.
    cur.execute(
        """
        SELECT v.opaque_id, a.content_sha256, a.width_px, a.height_px,
               q.semantic_match_status, q.public_allowed, q.card_number_match,
               q.language_match, q.tcg_match, q.qc_version, q.checked_at,
               p.source_path, p.public_allowed AS pointer_public_allowed,
               i.external_entity_id AS snk_id
        FROM catalog_variant v
        JOIN market_image_asset a
          ON a.id=%s AND a.variant_id=v.id AND a.image_kind='raw_front'
         AND a.source_version_sha256=%s
        JOIN market_image_qc q
          ON q.image_asset_id=a.id
         AND q.qc_version=%s
         AND q.semantic_match_status=%s
        JOIN market_image_source_pointer p
          ON p.variant_id=v.id AND p.image_kind='raw_front'
         AND p.source_version_sha256=%s AND p.source_path=%s
         AND p.public_allowed=1
        JOIN catalog_source_identity i
          ON i.variant_id=v.id AND i.source_code='snkrdunk'
         AND i.external_entity_id=%s
         AND i.match_status='exact'
        WHERE v.id=%s
        """,
        (
            int(receipt["assetId"]),
            str(receipt["rawSha256"]),
            QC_VERSION_PROMOTED,
            SEMANTIC_CONFIRMED,
            str(receipt["rawSha256"]),
            str(receipt["sourcePath"]),
            str(receipt["snkId"]),
            variant_id,
        ),
    )
    rows = list(cur.fetchall())
    if len(rows) != 1:
        raise PromotionGateError("manifest_rebuild_missing_row")
    row = rows[0]
    if (
        str(row.get("content_sha256") or "") != str(receipt.get("contentSha256") or "")
        or str(row.get("source_path") or "") != str(receipt.get("sourcePath") or "")
        or str(row.get("snk_id") or "") != str(receipt.get("snkId") or "")
        or str(row.get("semantic_match_status") or "") != SEMANTIC_CONFIRMED
        or not bool(row.get("public_allowed"))
        or not bool(row.get("pointer_public_allowed"))
        or str(row.get("qc_version") or "") != QC_VERSION_PROMOTED
    ):
        raise PromotionGateError("manifest_rebuild_evidence_mismatch")
    return {
        "cardNumberMatch": bool(row.get("card_number_match")),
        "contentSha256": row["content_sha256"],
        "height": row.get("height_px") or 600,
        "width": row.get("width_px") or 429,
        "imageKind": "raw_front",
        "languageMatch": bool(row.get("language_match")),
        "publicAllowed": bool(row.get("public_allowed")),
        "publicId": row.get("opaque_id"),
        "qcAt": str(row.get("checked_at") or receipt.get("approvedAt")),
        "qcVersion": row.get("qc_version"),
        "resolverEvidence": {
            "method": "snk_image_promotion",
            "snkId": str(receipt.get("snkId")),
            "sourcePath": receipt.get("sourcePath"),
            "receiptSha256": receipt.get("receiptSha256"),
            "canonicalPrintingSha256": receipt.get("canonicalPrintingSha256"),
            "qcRunId": receipt.get("qcRunId"),
            "operator": receipt.get("operator"),
            "collectorMatch": bool(row.get("card_number_match")),
            "tcgMetadataMatch": bool(row.get("tcg_match")),
            "languageMetadataMatch": bool(row.get("language_match")),
        },
        "semanticMatchStatus": row.get("semantic_match_status"),
        "tcgMatch": bool(row.get("tcg_match")),
        "stdCanvas": "std-429x600",
    }


def build_manifest_document(
    document: Mapping[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    doc = dict(document)
    records = list(doc.get("records") or [])
    public_id = record.get("publicId")
    if not public_id:
        raise PromotionGateError("manifest_public_id_missing")

    out: list[dict[str, Any]] = []
    inserted = False
    for existing in records:
        same_target = (
            existing.get("publicId") == public_id
            and existing.get("imageKind") == "raw_front"
        )
        if same_target:
            if not inserted:
                out.append(record)
                inserted = True
            continue
        out.append(existing)
    if not inserted:
        out.append(record)
    doc["records"] = out
    doc["generatedAt"] = utc_now()
    doc["generator"] = "snk_image_promotion.py"
    return doc


def upsert_manifest_record(record: dict[str, Any]) -> None:
    if MANIFEST_PATH.is_file():
        doc = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    else:
        doc = {"records": []}
    doc = build_manifest_document(doc, record)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = MANIFEST_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(MANIFEST_PATH)


def build_manifest_without_public_ids(
    document: Mapping[str, Any],
    public_ids: set[str],
) -> dict[str, Any]:
    doc = dict(document)
    doc["records"] = [
        record
        for record in list(doc.get("records") or [])
        if not (
            str(record.get("publicId") or "") in public_ids
            and record.get("imageKind") == "raw_front"
        )
    ]
    doc["generatedAt"] = utc_now()
    doc["generator"] = "snk_image_promotion.py"
    return doc


def remove_manifest_public_ids(public_ids: set[str]) -> None:
    targets = {str(value) for value in public_ids if str(value)}
    if not targets or not MANIFEST_PATH.is_file():
        return
    document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    updated = build_manifest_without_public_ids(document, targets)
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = MANIFEST_PATH.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(MANIFEST_PATH)


def finalize_rejection_artifacts(artifacts: Mapping[str, Any]) -> None:
    # Remove the public manifest view first. If queue persistence then fails,
    # DB + manifest remain safely demoted and the pending queue can be retried.
    remove_manifest_public_ids(
        {str(value) for value in artifacts.get("publicIds") or []}
    )
    write_queue(list(artifacts["queue"]))


def finalize_approval_artifacts(artifacts: Mapping[str, Any]) -> None:
    receipt = dict(artifacts["receipt"])
    receipt_sha = str(receipt["receiptSha256"])
    rpath = RECEIPT_DIR / f"{receipt_sha}.json"
    RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
    if rpath.is_file():
        existing = json.loads(rpath.read_text(encoding="utf-8"))
        if existing != receipt:
            raise PromotionGateError("approval_receipt_hash_collision")
    else:
        tmp = rpath.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(receipt, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(rpath)

    try:
        write_queue(list(artifacts["queue"]))
        upsert_manifest_record(dict(artifacts["manifestRecord"]))
    except Exception:
        # Keep the immutable human/vision decision receipt, but restore the
        # pending queue. Main compensates the DB promotion before surfacing the
        # failure, so this cannot become a false-green public decision.
        write_queue(list(artifacts["originalQueue"]))
        raise


def compensate_failed_approval_finalize(cur, artifacts: Mapping[str, Any]) -> None:
    receipt = artifacts["receipt"]
    asset_id = int(receipt["assetId"])
    variant_id = int(receipt["variantId"])
    raw_sha = str(receipt["rawSha256"])
    source_path = str(receipt["sourcePath"])
    cur.execute(
        """
        UPDATE market_image_qc
        SET semantic_match_status=%s, public_allowed=0,
            rejection_reason='promotion_artifact_finalize_failed',
            checked_at=%s, qc_version=%s
        WHERE image_asset_id=%s
        """,
        (SEMANTIC_PENDING, utc_naive(), QC_VERSION_PENDING, asset_id),
    )
    cur.execute(
        """
        UPDATE market_image_source_pointer
        SET public_allowed=0
        WHERE variant_id=%s AND image_kind='raw_front'
          AND source_version_sha256=%s AND source_path=%s
        """,
        (variant_id, raw_sha, source_path),
    )


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_multi_snk_audit(cur) -> dict[str, Any]:
    cur.execute(
        """
        SELECT i.variant_id, v.canonical_name, v.collector_number, v.tcg_code,
               COUNT(*) n,
               GROUP_CONCAT(i.external_entity_id ORDER BY i.external_entity_id) snk_ids,
               GROUP_CONCAT(i.match_status ORDER BY i.external_entity_id) statuses
        FROM catalog_source_identity i
        JOIN catalog_variant v ON v.id=i.variant_id
        WHERE i.source_code='snkrdunk'
        GROUP BY i.variant_id
        HAVING n > 1
        ORDER BY n DESC, i.variant_id
        """
    )
    rows = cur.fetchall()
    out = {"multiSnkVariants": len(rows), "rows": rows}
    path = (
        REPORT_DIR
        / f"snk-multi-identity-audit-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    )
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    out["report"] = str(path)
    return out


def cmd_identity_quarantine_receipts(cur, *, write: bool) -> dict[str, Any]:
    """Evidence-only receipts for Newgate + multi-SNK — does NOT rewrite binds."""
    cases = []
    # multi
    cur.execute(
        """
        SELECT i.variant_id, v.canonical_name, v.collector_number, v.tcg_code,
               GROUP_CONCAT(i.external_entity_id ORDER BY i.external_entity_id) snk_ids
        FROM catalog_source_identity i
        JOIN catalog_variant v ON v.id=i.variant_id
        WHERE i.source_code='snkrdunk'
        GROUP BY i.variant_id
        HAVING COUNT(*) > 1
        """
    )
    for r in cur.fetchall():
        cases.append(
            {
                "type": "identity_quarantine",
                "reason": "multi_snk_identity",
                "variantId": int(r["variant_id"]),
                "name": r["canonical_name"],
                "collector": r["collector_number"],
                "tcg": r["tcg_code"],
                "snkIds": str(r["snk_ids"]).split(","),
                "action": "do_not_promote_image_until_exact_one_snk",
                "recordedAt": utc_now(),
            }
        )
    # Newgate known bad bind pattern
    cur.execute(
        """
        SELECT v.id, v.canonical_name, v.collector_number, i.external_entity_id
        FROM catalog_variant v
        JOIN catalog_source_identity i ON i.variant_id=v.id AND i.source_code='snkrdunk'
        WHERE v.id=1213
        """
    )
    ng = cur.fetchone()
    if ng:
        cases.append(
            {
                "type": "identity_quarantine",
                "reason": "suspected_wrong_snk_printing",
                "variantId": 1213,
                "name": ng["canonical_name"],
                "collector": ng["collector_number"],
                "snkId": str(ng["external_entity_id"]),
                "note": "catalog ST15-002 vs historical SNK master OP12-002; image ingest title gate blocked",
                "action": "identity_review_before_any_image_approve",
                "recordedAt": utc_now(),
            }
        )

    written = []
    if write:
        IDENTITY_RECEIPT_DIR.mkdir(parents=True, exist_ok=True)
        for c in cases:
            c["receiptSha256"] = sha256_obj(c)
            path = IDENTITY_RECEIPT_DIR / f"{c['receiptSha256']}.json"
            path.write_text(json.dumps(c, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            written.append(str(path))
    return {"write": write, "cases": len(cases), "paths": written, "sample": cases[:3]}


def cmd_demote_false_public(cur, *, write: bool) -> dict[str, Any]:
    cur.execute(
        """
        SELECT q.id AS qc_id, a.variant_id, a.content_sha256, v.opaque_id
        FROM market_image_qc q
        JOIN market_image_asset a ON a.id=q.image_asset_id
        JOIN catalog_variant v ON v.id=a.variant_id
        WHERE q.qc_version=%s
          AND q.semantic_match_status <> %s
          AND (
            q.public_allowed=1
            OR q.semantic_match_status IN ('snk_master_title', 'meta_unreviewed')
          )
        """,
        (QC_VERSION_PENDING, SEMANTIC_CONFIRMED),
    )
    rows = cur.fetchall()
    n = 0
    if write:
        for r in rows:
            cur.execute(
                """
                UPDATE market_image_qc
                SET semantic_match_status=%s, language_match=0, tcg_match=0,
                    public_allowed=0,
                    rejection_reason=%s, checked_at=%s, qc_version=%s
                WHERE id=%s
                """,
                (
                    SEMANTIC_PENDING,
                    "demoted:auto_public_without_human_or_vision",
                    utc_naive(),
                    QC_VERSION_PENDING,
                    r["qc_id"],
                ),
            )
            cur.execute(
                """
                UPDATE market_image_source_pointer
                SET public_allowed=0
                WHERE variant_id=%s AND image_kind='raw_front'
                """,
                (r["variant_id"],),
            )
            n += 1
    result = {"write": write, "candidates": len(rows), "demoted": n if write else 0}
    if write:
        result["_manifestPublicIds"] = sorted(
            {str(row.get("opaque_id") or "") for row in rows if row.get("opaque_id")}
        )
    return result


def cmd_enroll_from_qc(
    cur,
    *,
    qc_run: str | None,
    limit: int,
    tcg: str | None,
    quick_win_only: bool,
    write: bool,
) -> dict[str, Any]:
    run_dir = resolve_qc_run(qc_run)
    receipt, report, report_sha, receipt_sha = load_qc_bundle(run_dir)
    authority_dir = resolve_qc_run(None)
    authority_receipt, _authority_report, authority_report_sha, _authority_receipt_sha = (
        load_qc_bundle(authority_dir)
    )
    stale_qc_run = run_dir.resolve() != authority_dir.resolve()
    if write and stale_qc_run:
        raise PromotionGateError(
            f"qc_run_not_machine_authority:{run_dir.name}:{authority_dir.name}"
        )
    run_id = str(report.get("runId") or receipt.get("runId") or run_dir.name)
    cards = list(report.get("cards") or [])
    selected = select_ranked_image_queue(
        cards, limit=limit * 3 if limit else 0, tcg=tcg, quick_win_only=quick_win_only
    )
    # oversample then filter by live eligibility

    candidates: list[dict[str, Any]] = []
    eligible: list[dict[str, Any]] = []
    skipped = {
        "multiSnk": 0,
        "noSnk": 0,
        "noAsset": 0,
        "liveError": 0,
        "dupQueue": 0,
        "quarantined": 0,
        "printingMissing": 0,
        "printingDrift": 0,
    }
    quarantine, resolved_quarantine = load_identity_quarantine_state()
    existing_pending = {
        int(r["variantId"]) for r in read_queue() if r.get("status") == "pending"
    }

    for card in selected:
        if limit > 0 and len(candidates) >= limit:
            break
        vid = int(card.get("variantId") or 0)
        if not vid:
            continue
        if vid in quarantine:
            skipped["quarantined"] += 1
            continue
        if vid in existing_pending:
            skipped["dupQueue"] += 1
            continue
        try:
            live = fetch_live_image_state(cur, vid)
        except PromotionGateError:
            skipped["liveError"] += 1
            continue
        if len(live["snkIds"]) == 0:
            skipped["noSnk"] += 1
            continue
        if len(live["snkIds"]) != 1:
            skipped["multiSnk"] += 1
            continue
        resolution = resolved_quarantine.get(vid)
        if resolution is not None and str(live["snkIds"][0]) != str(
            resolution.get("selectedSnkId") or ""
        ):
            skipped["quarantined"] += 1
            continue

        facts = card.get("facts") or {}
        identity = facts.get("identity") or {}
        report_printing_sha = str(identity.get("printingSha256") or "")
        live_printing_sha = str(live.get("canonicalPrintingSha256") or "")
        if report_printing_sha != live_printing_sha:
            skipped["printingDrift"] += 1
            continue

        item = {
            "variantId": vid,
            "opaqueId": card.get("id") or live.get("opaqueId"),
            "name": live.get("name"),
            "collector": live.get("collector") or (identity.get("collectorNumber")),
            "tcg": card.get("tcg") or live.get("tcg"),
            "snkId": live["snkIds"][0],
            "snkIdentitySha256": live["snkIdentitySha256"],
            "assetId": live["assetId"],
            "contentSha256": live["contentSha256"],
            "rawSha256": live.get("rawSha256"),
            "sourcePath": live.get("sourcePath"),
            "width": live.get("width"),
            "height": live.get("height"),
            "canonicalPrintingSha256": live_printing_sha,
            "qcRunId": run_id,
            "qcReportSha256": report_sha,
            "qcReceiptSha256": receipt_sha,
            "cardEvidenceSha256": card.get("evidenceSha256"),
            "marketRank": card.get("marketRank"),
            "blockers": list(card.get("blockers") or []),
            "status": "pending",
            "enrolledAt": utc_now(),
            "enrollSource": "canonical_db_qc",
            "qcReportPath": str(run_dir / "report.json"),
            "qcReceiptPath": str(run_dir / "receipt.json"),
        }
        item["queueFingerprint"] = compute_queue_fingerprint(item)
        item["approvalEligible"] = bool(live_printing_sha)
        candidates.append(item)
        if not live_printing_sha:
            skipped["printingMissing"] += 1
            continue
        eligible.append(item)

    if write and eligible:
        rows = [r for r in read_queue() if r.get("status") != "pending"]
        rows.extend(eligible)
        write_queue(rows)

    return {
        "write": write,
        "qcRunId": run_id,
        "qcDir": str(run_dir),
        "qcReportSha256": report_sha,
        "qcReceiptSha256": receipt_sha,
        "machineAuthorityQcRunId": authority_receipt.get("runId"),
        "machineAuthorityQcReportSha256": authority_report_sha,
        "staleQcRun": stale_qc_run,
        "selectedFromReport": len(selected),
        "liveCandidates": len(candidates),
        "queueEligible": len(eligible),
        "enrolled": len(eligible) if write else 0,
        "skipped": skipped,
        "queuePending": sum(1 for r in read_queue() if r.get("status") == "pending"),
        "sample": candidates[:5],
        "queuePath": str(QUEUE_PATH),
        "note": (
            "Dry-run may show liveCandidates, but only fresh-QC cards with complete canonical "
            "printing are queueEligible and writeable."
        ),
    }


def cmd_list_queue(*, status: str | None) -> dict[str, Any]:
    rows = read_queue()
    if status:
        rows = [r for r in rows if r.get("status") == status]
    by: dict[str, int] = {}
    for r in read_queue():
        by[str(r.get("status"))] = by.get(str(r.get("status")), 0) + 1
    return {"counts": by, "n": len(rows), "rows": rows[:40], "queuePath": str(QUEUE_PATH)}


def cmd_approve(
    cur,
    *,
    variant_id: int,
    operator: str,
    note: str,
    write: bool,
    snk_id: str | None,
) -> dict[str, Any]:
    if operator not in ("human", "vision"):
        raise PromotionGateError("operator_must_be_human_or_vision")

    queue = read_queue()
    item = None
    for r in reversed(queue):
        if int(r.get("variantId") or 0) == variant_id and r.get("status") == "pending":
            item = r
            break
    if item is None:
        raise PromotionGateError("not_in_pending_queue")

    assert_not_quarantined(variant_id, snk_id=str(item.get("snkId") or ""))

    if snk_id and str(item.get("snkId")) != str(snk_id):
        raise PromotionGateError("arg_snk_mismatch")

    live = fetch_live_image_state(cur, variant_id)
    verify_live_against_queue(item, live)

    # disk hash
    sha = str(item.get("contentSha256"))
    disk = find_asset_file(sha)
    if disk is None:
        raise PromotionGateError("asset_file_missing")
    if sha256_file(disk) != sha:
        raise PromotionGateError("disk_content_hash_mismatch")
    assert_public_image_policy(item, live=live, asset_path=disk)

    approved_at = utc_now()
    receipt = build_approval_receipt(item, operator=operator, note=note, approved_at=approved_at)

    if not write:
        return {
            "write": False,
            "wouldPromote": True,
            "receipt": receipt,
            "assetId": item.get("assetId"),
            "sourcePath": item.get("sourcePath"),
        }

    rpath = RECEIPT_DIR / f"{receipt['receiptSha256']}.json"

    now = utc_naive()
    asset_id = int(item["assetId"])
    raw_sha = str(item.get("rawSha256") or "")
    source_path = str(item.get("sourcePath") or "")
    if not raw_sha or not source_path:
        raise PromotionGateError("reviewed_asset_pointer_incomplete")

    cur.execute(
        """
        SELECT id
        FROM market_image_asset
        WHERE id=%s AND variant_id=%s AND image_kind='raw_front'
          AND source_version_sha256=%s AND content_sha256=%s
        FOR UPDATE
        """,
        (asset_id, variant_id, raw_sha, sha),
    )
    if cur.fetchone() is None:
        raise PromotionGateError("reviewed_asset_changed")
    cur.execute(
        """
        SELECT source_version_sha256
        FROM market_image_source_pointer
        WHERE variant_id=%s AND image_kind='raw_front'
          AND source_version_sha256=%s AND source_path=%s
        FOR UPDATE
        """,
        (variant_id, raw_sha, source_path),
    )
    if cur.fetchone() is None:
        raise PromotionGateError("reviewed_pointer_changed")

    # A variant may retain old source evidence, but only this exact asset/path
    # may remain public after the decision.
    cur.execute(
        """
        UPDATE market_image_qc q
        JOIN market_image_asset a ON a.id=q.image_asset_id
        SET q.public_allowed=0
        WHERE a.variant_id=%s AND a.image_kind='raw_front' AND a.id<>%s
        """,
        (variant_id, asset_id),
    )
    # Promote QC only for the reviewed asset
    cur.execute(
        """
        INSERT INTO market_image_qc
            (image_asset_id, semantic_match_status, card_number_match, language_match,
             tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
             checked_at, qc_version)
        VALUES (%s, %s, 1, 1, 1, 1, 1, NULL, %s, %s)
        ON DUPLICATE KEY UPDATE
            semantic_match_status=VALUES(semantic_match_status),
            card_number_match=1, language_match=1, tcg_match=1,
            raw_front_confirmed=1, public_allowed=1, rejection_reason=NULL,
            checked_at=VALUES(checked_at), qc_version=VALUES(qc_version)
        """,
        (asset_id, SEMANTIC_CONFIRMED, now, QC_VERSION_PROMOTED),
    )
    cur.execute(
        """
        UPDATE market_image_source_pointer
        SET public_allowed=0
        WHERE variant_id=%s AND image_kind='raw_front'
        """,
        (variant_id,),
    )
    cur.execute(
        """
        UPDATE market_image_source_pointer
        SET public_allowed=1, observed_at=%s
        WHERE variant_id=%s AND image_kind='raw_front'
          AND source_version_sha256=%s AND source_path=%s
        """,
        (now, variant_id, raw_sha, source_path),
    )
    if int(cur.rowcount) != 1:
        raise PromotionGateError("reviewed_pointer_promote_failed")

    manifest_record = rebuild_manifest_record_from_db(cur, variant_id, receipt)

    new_q = []
    found = False
    for r in queue:
        if (
            int(r.get("variantId") or 0) == variant_id
            and r.get("status") == "pending"
            and not found
        ):
            r = dict(r)
            r["status"] = "approved"
            r["receiptSha256"] = receipt["receiptSha256"]
            r["approvedAt"] = approved_at
            r["operator"] = operator
            found = True
        new_q.append(r)
    if not found:
        raise PromotionGateError("queue_update_race")

    return {
        "write": True,
        "variantId": variant_id,
        "receiptSha256": receipt["receiptSha256"],
        "receiptPath": str(rpath),
        "manifest": str(MANIFEST_PATH),
        "canonicalPrintingSha256": receipt.get("canonicalPrintingSha256"),
        "qcRunId": receipt.get("qcRunId"),
        "_artifacts": {
            "receipt": receipt,
            "manifestRecord": manifest_record,
            "queue": new_q,
            "originalQueue": queue,
        },
    }


def cmd_reject_queue(*, variant_id: int, reason: str, write: bool, cur=None) -> dict[str, Any]:
    queue = read_queue()
    n = 0
    new_q = []
    public_ids: set[str] = set()
    for r in queue:
        if int(r.get("variantId") or 0) == variant_id and r.get("status") == "pending":
            if write:
                r = dict(r)
                r["status"] = "rejected"
                r["rejectReason"] = reason
                r["rejectedAt"] = utc_now()
                n += 1
                if r.get("opaqueId"):
                    public_ids.add(str(r["opaqueId"]))
        new_q.append(r)
    if write:
        if n == 0:
            raise PromotionGateError("not_in_pending_queue")
        if cur is not None:
            cur.execute(
                """
                UPDATE market_image_qc q
                JOIN market_image_asset a ON a.id=q.image_asset_id
                SET q.public_allowed=0,
                    q.rejection_reason=%s,
                    q.semantic_match_status=%s,
                    q.checked_at=%s
                WHERE a.variant_id=%s AND a.image_kind='raw_front'
                """,
                (f"review_rejected:{reason}"[:240], SEMANTIC_PENDING, utc_naive(), variant_id),
            )
            cur.execute(
                """
                UPDATE market_image_source_pointer
                SET public_allowed=0
                WHERE variant_id=%s AND image_kind='raw_front'
                """,
                (variant_id,),
            )
    result = {
        "write": write,
        "rejected": n,
        "variantId": variant_id,
        "reason": reason,
    }
    if write:
        result["_artifacts"] = {
            "queue": new_q,
            "originalQueue": queue,
            "publicIds": sorted(public_ids),
        }
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="SNK image promotion (hard-gated)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_dem = sub.add_parser("demote-false-public")
    p_dem.add_argument("--write", action="store_true")

    p_en = sub.add_parser("enroll-from-qc", help="Ranked queue from immutable QC report")
    p_en.add_argument("--qc-run", default=None, help="e.g. qc_20260729_full_03")
    p_en.add_argument("--tcg", default=None)
    p_en.add_argument("--limit", type=int, default=34)
    p_en.add_argument("--all-image-blockers", action="store_true",
                      help="Do not restrict to quick-win (image±printing only)")
    p_en.add_argument("--write", action="store_true")

    # keep legacy name as alias error
    p_old = sub.add_parser("enroll", help="DEPRECATED — use enroll-from-qc")
    p_old.add_argument("--tcg", default=None)
    p_old.add_argument("--limit", type=int, default=34)
    p_old.add_argument("--write", action="store_true")

    p_ls = sub.add_parser("list-queue")
    p_ls.add_argument("--status", default=None)

    p_ap = sub.add_parser("approve")
    p_ap.add_argument("--variant-id", type=int, required=True)
    p_ap.add_argument("--snk-id", default=None)
    p_ap.add_argument("--operator", choices=["human", "vision"], required=True)
    p_ap.add_argument("--note", default="")
    p_ap.add_argument("--write", action="store_true")

    p_rj = sub.add_parser("reject-queue")
    p_rj.add_argument("--variant-id", type=int, required=True)
    p_rj.add_argument("--reason", required=True)
    p_rj.add_argument("--write", action="store_true")

    sub.add_parser("multi-snk-audit")
    p_iq = sub.add_parser("identity-quarantine-receipts")
    p_iq.add_argument("--write", action="store_true")

    args = ap.parse_args()
    load_env()
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    if args.cmd == "list-queue":
        print(json.dumps(cmd_list_queue(status=args.status), ensure_ascii=False, indent=2, default=str))
        return 0

    if args.cmd == "enroll":
        print(
            json.dumps(
                {
                    "error": "enroll_deprecated",
                    "use": "enroll-from-qc --qc-run <runId> --write",
                },
                indent=2,
            )
        )
        return 2

    conn = db()
    cur = conn.cursor()
    try:
        if args.cmd == "multi-snk-audit":
            out = cmd_multi_snk_audit(cur)
            conn.rollback()
        elif args.cmd == "identity-quarantine-receipts":
            out = cmd_identity_quarantine_receipts(cur, write=args.write)
            conn.rollback()
        elif args.cmd == "demote-false-public":
            out = cmd_demote_false_public(cur, write=args.write)
            if args.write:
                public_ids = set(out.pop("_manifestPublicIds"))
                conn.commit()
                remove_manifest_public_ids(public_ids)
            else:
                conn.rollback()
        elif args.cmd == "enroll-from-qc":
            out = cmd_enroll_from_qc(
                cur,
                qc_run=args.qc_run,
                limit=args.limit,
                tcg=args.tcg,
                quick_win_only=not args.all_image_blockers,
                write=args.write,
            )
            conn.rollback()
        elif args.cmd == "approve":
            out = cmd_approve(
                cur,
                variant_id=args.variant_id,
                operator=args.operator,
                note=args.note,
                write=args.write,
                snk_id=args.snk_id,
            )
            if args.write:
                artifacts = out.pop("_artifacts")
                conn.commit()
                try:
                    finalize_approval_artifacts(artifacts)
                except Exception as exc:
                    try:
                        compensate_failed_approval_finalize(cur, artifacts)
                        conn.commit()
                    except Exception as compensation_error:
                        conn.rollback()
                        raise RuntimeError(
                            "approval artifact finalize and DB compensation both failed"
                        ) from compensation_error
                    raise PromotionGateError(
                        f"approval_artifact_finalize_failed:{type(exc).__name__}"
                    ) from exc
            else:
                conn.rollback()
        elif args.cmd == "reject-queue":
            out = cmd_reject_queue(
                variant_id=args.variant_id,
                reason=args.reason,
                write=args.write,
                cur=cur if args.write else None,
            )
            if args.write:
                artifacts = out.pop("_artifacts")
                conn.commit()
                finalize_rejection_artifacts(artifacts)
            else:
                conn.rollback()
        else:
            raise SystemExit(f"unknown {args.cmd}")
        print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
        return 0
    except PromotionGateError as e:
        conn.rollback()
        print(json.dumps({"error": "promotion_gate", "reason": str(e)}, indent=2))
        return 3
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
