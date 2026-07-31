#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Step 4: ranked printing review batch (worksheets → field-level approvals).

Does NOT write catalog_printing_identity.
Does NOT invent edition/parallel/finish.
Creates open worksheets for human/vision to fill field evidence, then seals
content-addressed receipts under printing-decisions/ for printing-plan.

Usage:
  python -X utf8 pipelines\\printing_review_batch.py open \\
    --qc-run qc_20260729_sale_contract_01 --limit 20 --write

  python -X utf8 pipelines\\printing_review_batch.py seal-worksheet \\
    --worksheet data\\runtime\\private-source-map\\printing-review-batches\\...\\worksheets\\variant_19.json \\
    --actor human --write

  python -X utf8 pipelines\\printing_review_batch.py status
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from db_runtime import (  # noqa: E402
    DEFAULT_CANONICAL_DB_QC,
    DEFAULT_IDENTITY_QUARANTINE,
    DEFAULT_PRINTING_DECISIONS,
    PRINTING_EVIDENCE_TYPES,
    PRINTING_FIELDS,
    PRINTING_PLACEHOLDERS,
    SHA256_HEX,
    load_canonical_db_qc_bundle,
    load_identity_quarantine_bundle,
    load_printing_db_state,
    load_printing_decisions,
    normalize_printing_value,
    validate_printing_field_receipt,
    sha256,
    canonical_json,
)

BATCH_ROOT = ROOT / "data" / "runtime" / "private-source-map" / "printing-review-batches"


def repo_contained_path(path: Path, *, label: str) -> Path:
    """Resolve an operator-supplied root without allowing an external path."""
    resolved = path.resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label}_outside_repository") from exc
    return resolved


def load_env() -> None:
    env = ROOT / "data" / "runtime" / "config" / "backend.env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
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
        autocommit=True,
    )


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def open_field_slot(field: str, *, base_value: str | None = None) -> dict[str, Any]:
    """Open slot — values empty unless base catalog field already known."""
    base = str(base_value or "").strip()
    return {
        "field": field,
        "rawValue": base if field in ("tcgCode", "cardLanguage", "setName", "collectorNumber") else "",
        "normalizedValue": base if field in ("tcgCode", "cardLanguage", "setName", "collectorNumber") else "",
        "evidenceType": "",  # must become human_verified_source_field | vision_verified_source_field
        "extractorVersion": "",
        "sourceCode": "",
        "externalEntityId": "",
        "sourceReceiptPath": "",
        "sourceReceiptSha256": "",
        "status": "open" if field in ("editionCode", "parallelCode", "finishCode") else "draft_base",
        "notes": "",
    }


def select_batch_cards(
    qc_cards: list[dict[str, Any]],
    *,
    limit: int,
    skip_variants: set[int],
    require_printing_blocker: bool = True,
) -> list[dict[str, Any]]:
    """Top marketRank cards still needing printing receipts."""
    rows = []
    for card in qc_cards:
        vid = int(card.get("variantId") or 0)
        if vid <= 0 or vid in skip_variants:
            continue
        blockers = set(card.get("blockers") or [])
        if require_printing_blocker and not any(
            blocker.startswith("canonical_printing_") for blocker in blockers
        ):
            continue
        rows.append(card)

    def _key(c: dict[str, Any]) -> tuple:
        mr = c.get("marketRank")
        if isinstance(mr, int):
            return (0, mr, int(c.get("variantId") or 0))
        return (1, 10**9, int(c.get("variantId") or 0))

    rows.sort(key=_key)
    return rows[:limit] if limit > 0 else rows


def current_approval_variants(
    qc_cards: list[dict[str, Any]],
    decisions: dict[int, list[dict[str, Any]]],
    *,
    qc_run: str,
) -> set[int]:
    """Return only approvals bound to this QC run and its current card evidence."""
    approved: set[int] = set()
    for card in qc_cards:
        variant_id = int(card.get("variantId") or 0)
        if variant_id <= 0:
            continue
        for item in decisions.get(variant_id, []):
            receipt = item.get("receipt")
            if not isinstance(receipt, dict):
                continue
            if (
                receipt.get("type") == "canonical_printing_approval"
                and receipt.get("schemaVersion") == 1
                and receipt.get("decision") == "approve"
                and receipt.get("qcRunId") == qc_run
                and receipt.get("opaqueId") == card.get("id")
                and receipt.get("cardEvidenceSha256") == card.get("evidenceSha256")
            ):
                approved.add(variant_id)
                break
    return approved


def build_worksheet(
    card: dict[str, Any],
    *,
    variant: dict[str, Any],
    sources: list[dict[str, Any]],
    qc_run_id: str,
) -> dict[str, Any]:
    exact = [
        s
        for s in sources
        if str(s.get("match_status") or "") == "exact"
        and str(s.get("source_code") or "")
        in ("gemrate", "snkrdunk", "pricecharting")
    ]
    gemrate = [s for s in exact if s["source_code"] == "gemrate"]
    snk = [s for s in exact if s["source_code"] == "snkrdunk"]
    pricecharting = [s for s in exact if s["source_code"] == "pricecharting"]
    all_snk = [
        s for s in sources if str(s.get("source_code") or "") == "snkrdunk"
    ]
    # v1 remains the preferred route. v2 is intentionally available only when
    # there is no SNK identity to contradict the GemRate + PriceCharting pair.
    if len(gemrate) == 1 and len(snk) == 1:
        policy_version = "canonical-printing-v1"
        route_sources = (gemrate[0], snk[0])
    elif (
        len(gemrate) == 1
        and len(pricecharting) == 1
        and str(pricecharting[0].get("external_entity_id") or "").isdigit()
        and int(str(pricecharting[0].get("external_entity_id") or "0")) > 0
        and not all_snk
    ):
        policy_version = "canonical-printing-v2-pc"
        route_sources = (gemrate[0], pricecharting[0])
    else:
        policy_version = "canonical-printing-v1"
        route_sources = ()
    bindings = [
        {
            "sourceCode": str(source["source_code"]),
            "externalEntityId": str(source["external_entity_id"]),
            "matchStatus": "exact",
        }
        for source in route_sources
    ]

    fields = {
        "tcgCode": open_field_slot("tcgCode", base_value=variant.get("tcg_code")),
        "cardLanguage": open_field_slot(
            "cardLanguage", base_value=variant.get("card_language")
        ),
        "setName": open_field_slot("setName", base_value=variant.get("set_name")),
        "collectorNumber": open_field_slot(
            "collectorNumber", base_value=variant.get("collector_number")
        ),
        "editionCode": open_field_slot("editionCode"),
        "parallelCode": open_field_slot("parallelCode"),
        "finishCode": open_field_slot("finishCode"),
    }

    return {
        "type": "canonical_printing_review_worksheet",
        "schemaVersion": 1,
        "status": "open",
        "policyVersion": policy_version,
        "variantId": int(variant["variant_id"]),
        "opaqueId": str(variant.get("opaque_id") or card.get("id") or ""),
        "name": str(
            variant.get("canonical_name")
            or (card.get("facts") or {}).get("identity", {}).get("name")
            or card.get("id")
            or ""
        ),
        "qcRunId": qc_run_id,
        "cardEvidenceSha256": str(card.get("evidenceSha256") or ""),
        "marketRank": card.get("marketRank"),
        "tcg": card.get("tcg"),
        "blockers": list(card.get("blockers") or []),
        "sourceBindings": bindings,
        "bindingReady": len(bindings) == 2,
        "fields": fields,
        "instructions": [
            "Fill cardLanguage / editionCode / parallelCode / finishCode from private source receipts only.",
            "Each field needs evidenceType human_verified_source_field or vision_verified_source_field.",
            "sourceReceiptPath must be under data/runtime/private-source-map/ (or allowed roots).",
            "sourceReceiptSha256 must equal actual file bytes.",
            "Do NOT invent base/standard/regular defaults.",
            "cardLanguage is the current authoritative catalog_variant.card_language draft; it still needs a human/vision field receipt before sealing.",
            "Run seal-worksheet after all seven fields are complete — that writes printing-decisions/<sha>.json.",
            "canonical-printing-v2-pc uses PriceCharting only as an exact identity corroborator; each field still needs its own human/vision receipt.",
            "This worksheet is NOT a printing approval receipt.",
        ],
        "openedAt": utc_now(),
    }


def cmd_open(
    *,
    qc_run: str,
    limit: int,
    write: bool,
    variant_ids: set[int] | None = None,
    qc_root: Path = DEFAULT_CANONICAL_DB_QC,
    decision_root: Path = DEFAULT_PRINTING_DECISIONS,
    batch_root: Path = BATCH_ROOT,
) -> dict[str, Any]:
    qc_root = repo_contained_path(qc_root, label="qc_root")
    decision_root = repo_contained_path(decision_root, label="decision_root")
    batch_root = repo_contained_path(batch_root, label="batch_root")
    qc = load_canonical_db_qc_bundle(qc_root=qc_root, qc_run=qc_run)
    qc_cards = [
        card
        for card in qc["cards"]
        if variant_ids is None or int(card.get("variantId") or 0) in variant_ids
    ]
    quarantine_variants, _, _ = load_identity_quarantine_bundle(DEFAULT_IDENTITY_QUARANTINE)
    decisions, _, decision_manifest = load_printing_decisions(decision_root)
    already_approved = current_approval_variants(
        qc_cards, decisions, qc_run=str(qc["runId"])
    )

    skip = set(quarantine_variants) | already_approved
    selected = select_batch_cards(
        qc_cards,
        limit=limit * 2,
        skip_variants=skip,
        require_printing_blocker=variant_ids is None,
    )

    conn = db()
    try:
        with conn.cursor() as cur:
            # materialize enough candidates after filtering multi-snk / incomplete bindings
            ids = [int(c["variantId"]) for c in selected]
            variants, sources = load_printing_db_state(cur, ids)
            if ids:
                ph = ",".join(["%s"] * len(ids))
                cur.execute(
                    f"SELECT id, canonical_name FROM catalog_variant WHERE id IN ({ph})",
                    tuple(ids),
                )
                for row in cur.fetchall():
                    vid = int(row["id"])
                    if vid in variants:
                        variants[vid]["canonical_name"] = row.get("canonical_name")
    finally:
        conn.close()

    worksheets: list[dict[str, Any]] = []
    skipped = {"quarantine": 0, "alreadyApproved": 0, "multiSnk": 0, "noExactPair": 0}

    for card in selected:
        if limit > 0 and len(worksheets) >= limit:
            break
        vid = int(card["variantId"])
        if vid in quarantine_variants:
            skipped["quarantine"] += 1
            continue
        if vid in already_approved:
            skipped["alreadyApproved"] += 1
            continue
        variant = variants.get(vid)
        if not variant:
            continue
        src = sources.get(vid, [])
        snk_exact = [
            s
            for s in src
            if s.get("source_code") == "snkrdunk" and s.get("match_status") == "exact"
        ]
        if len(snk_exact) > 1:
            skipped["multiSnk"] += 1
            continue
        gem_exact = [
            s
            for s in src
            if s.get("source_code") == "gemrate" and s.get("match_status") == "exact"
        ]
        pc_exact = [
            s
            for s in src
            if s.get("source_code") == "pricecharting"
            and s.get("match_status") == "exact"
        ]
        has_any_snk = any(s.get("source_code") == "snkrdunk" for s in src)
        v1_ready = len(snk_exact) == 1 and len(gem_exact) == 1
        v2_ready = (
            len(gem_exact) == 1
            and len(pc_exact) == 1
            and not has_any_snk
        )
        if not v1_ready and not v2_ready:
            skipped["noExactPair"] += 1
            # still open worksheet but mark bindingReady false — human may fix identity first
            pass
        ws = build_worksheet(
            card, variant=variant, sources=src, qc_run_id=str(qc["runId"])
        )
        worksheets.append(ws)

    batch_id = datetime.now(timezone.utc).strftime("batch_%Y%m%dT%H%M%SZ")
    batch_dir = batch_root / batch_id
    index = {
        "type": "canonical_printing_review_batch",
        "schemaVersion": 1,
        "batchId": batch_id,
        "qcRunId": qc["runId"],
        "qcReportSha256": qc.get("reportSha256"),
        "qcReceiptSha256": qc.get("receiptSha256") or qc.get("reportSha256"),
        "decisionManifestSha256": decision_manifest,
        "roots": {
            "qcRoot": str(qc_root.relative_to(ROOT)).replace("\\", "/"),
            "decisionRoot": str(decision_root.relative_to(ROOT)).replace("\\", "/"),
            "batchRoot": str(batch_root.relative_to(ROOT)).replace("\\", "/"),
        },
        "asOf": qc.get("asOf"),
        "limit": limit,
        "openedAt": utc_now(),
        "counts": {
            "worksheets": len(worksheets),
            "bindingReady": sum(1 for w in worksheets if w.get("bindingReady")),
            "skipped": skipped,
        },
        "worksheets": [
            {
                "variantId": w["variantId"],
                "opaqueId": w["opaqueId"],
                "marketRank": w["marketRank"],
                "name": w["name"],
                "collector": w["fields"]["collectorNumber"]["normalizedValue"],
                "bindingReady": w["bindingReady"],
                "path": f"worksheets/variant_{w['variantId']}.json",
            }
            for w in worksheets
        ],
        "next": [
            "Human/vision fills edition/parallel/finish (+ evidence receipts) on each worksheet",
            "seal-worksheet --write for each complete card → printing-decisions/<sha>.json",
            "backend.py printing-plan --qc-run " + str(qc["runId"]),
            "printing-materialize dry-run only if approvedRows > 0",
            "Do NOT --apply without DADDY approval",
        ],
    }

    if write:
        if batch_dir.exists():
            raise RuntimeError("immutable_batch_directory_exists")
        batch_root.mkdir(parents=True, exist_ok=True)
        ws_dir = batch_dir / "worksheets"
        ws_dir.mkdir(parents=True, exist_ok=True)
        worksheet_manifest = []
        for w in worksheets:
            path = ws_dir / f"variant_{w['variantId']}.json"
            body = (json.dumps(w, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
            path.write_bytes(body)
            if path.read_bytes() != body:
                raise RuntimeError("worksheet_readback_mismatch")
            worksheet_manifest.append(
                {"path": f"worksheets/{path.name}", "sha256": sha256(body)}
            )
        index["worksheetManifestSha256"] = sha256(canonical_json(worksheet_manifest))
        index["batchDir"] = str(batch_dir.relative_to(ROOT)).replace("\\", "/")
        index_path = batch_dir / "index.json"
        index_body = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        index_path.write_bytes(index_body)
        if index_path.read_bytes() != index_body:
            raise RuntimeError("batch_index_readback_mismatch")
    else:
        index["write"] = False
        index["sample"] = index["worksheets"][:5]

    return index


def _validate_field_evidence(
    field: str,
    evidence: dict[str, Any],
    roots: list[Path],
    *,
    variant_id: int,
) -> dict[str, Any]:
    raw = str(evidence.get("rawValue") or "").strip()
    norm = normalize_printing_value(evidence.get("normalizedValue"))
    if not raw or not norm or norm in PRINTING_PLACEHOLDERS:
        raise ValueError(f"{field}:empty_or_placeholder")
    et = str(evidence.get("evidenceType") or "")
    if et not in PRINTING_EVIDENCE_TYPES:
        raise ValueError(f"{field}:evidence_type_invalid")
    if not str(evidence.get("extractorVersion") or "").strip():
        raise ValueError(f"{field}:extractor_missing")
    sc = str(evidence.get("sourceCode") or "")
    eid = str(evidence.get("externalEntityId") or "")
    if not sc or not eid:
        raise ValueError(f"{field}:source_binding_missing")
    rel = str(evidence.get("sourceReceiptPath") or "").replace("\\", "/")
    if not rel:
        raise ValueError(f"{field}:source_receipt_path_missing")
    path = Path(rel)
    if not path.is_absolute():
        path = (ROOT / rel).resolve()
    allowed = False
    for root in roots:
        try:
            path.relative_to(root.resolve())
            allowed = True
            break
        except ValueError:
            continue
    if not allowed or not path.is_file():
        raise ValueError(f"{field}:evidence_path_not_allowed_or_missing")
    actual = sha256(path.read_bytes())
    declared = str(evidence.get("sourceReceiptSha256") or "").casefold()
    if declared != actual:
        raise ValueError(f"{field}:receipt_hash_mismatch")
    try:
        source_receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"{field}:field_receipt_schema_invalid") from error
    if not isinstance(source_receipt, dict):
        raise ValueError(f"{field}:field_receipt_schema_invalid")
    try:
        validate_printing_field_receipt(
            source_receipt,
            variant_id=variant_id,
            field=field,
            evidence=evidence,
        )
    except ValueError as error:
        raise ValueError(f"{field}:{error}") from error
    return {
        "rawValue": raw,
        "normalizedValue": norm,
        "evidenceType": et,
        "extractorVersion": str(evidence["extractorVersion"]),
        "sourceCode": sc,
        "externalEntityId": eid,
        "sourceReceiptPath": str(path.relative_to(ROOT)).replace("\\", "/"),
        "sourceReceiptSha256": actual,
    }


def cmd_seal_worksheet(
    *,
    worksheet_path: Path,
    actor: str,
    write: bool,
    decision_root: Path = DEFAULT_PRINTING_DECISIONS,
    batch_root: Path = BATCH_ROOT,
) -> dict[str, Any]:
    decision_root = repo_contained_path(decision_root, label="decision_root")
    batch_root = repo_contained_path(batch_root, label="batch_root")
    worksheet_path = worksheet_path.resolve()
    try:
        worksheet_path.relative_to(batch_root)
    except ValueError as exc:
        raise ValueError("worksheet_outside_batch_root") from exc
    raw = json.loads(worksheet_path.read_text(encoding="utf-8"))
    if raw.get("type") != "canonical_printing_review_worksheet":
        raise SystemExit("not_a_worksheet")
    if not actor.strip():
        raise SystemExit("actor_required")

    roots = [
        (ROOT / "data" / "runtime" / "private-source-map").resolve(),
        (ROOT / "data" / "runtime" / "private-reports").resolve(),
    ]
    fields_out: dict[str, Any] = {}
    identity: dict[str, str] = {}
    variant_id = int(raw["variantId"])
    for field in PRINTING_FIELDS:
        ev = raw.get("fields", {}).get(field)
        if not isinstance(ev, dict):
            raise SystemExit(f"missing_field:{field}")
        cleaned = _validate_field_evidence(
            field,
            ev,
            roots,
            variant_id=variant_id,
        )
        fields_out[field] = cleaned
        identity[field] = cleaned["normalizedValue"]

    bindings = raw.get("sourceBindings") or []
    policy_version = str(raw.get("policyVersion") or "")
    if policy_version not in {"canonical-printing-v1", "canonical-printing-v2-pc"}:
        raise SystemExit("printing_policy_version_invalid")
    if not isinstance(bindings, list):
        raise SystemExit("need_exact_policy_bindings")
    source_codes = {
        str(binding.get("sourceCode") or "")
        for binding in bindings
        if isinstance(binding, dict)
    }
    expected_codes = (
        {"gemrate", "snkrdunk"}
        if policy_version == "canonical-printing-v1"
        else {"gemrate", "pricecharting"}
    )
    if (
        len(bindings) != 2
        or source_codes != expected_codes
    ):
        raise SystemExit("need_exact_policy_bindings")

    receipt = {
        "type": "canonical_printing_approval",
        "schemaVersion": 1,
        "decision": "approve",
        "variantId": variant_id,
        "opaqueId": str(raw["opaqueId"]),
        "actor": actor.strip(),
        "approvedAt": utc_now(),
        "policyVersion": policy_version,
        "qcRunId": str(raw["qcRunId"]),
        "cardEvidenceSha256": str(raw["cardEvidenceSha256"]),
        "identity": identity,
        "fields": fields_out,
        "sourceBindings": [
            {
                "sourceCode": b.get("sourceCode"),
                "externalEntityId": str(b.get("externalEntityId") or ""),
            }
            for b in bindings
        ],
        "worksheetPath": str(worksheet_path.relative_to(ROOT)).replace("\\", "/"),
    }
    supersedes = str(raw.get("supersedesEvidenceSha256") or "").casefold()
    repair_reason = str(raw.get("repairReason") or "").strip()
    if supersedes or repair_reason:
        if SHA256_HEX.fullmatch(supersedes) is None or not repair_reason:
            raise SystemExit("canonical_repair_metadata_invalid")
        receipt["supersedesEvidenceSha256"] = supersedes
        receipt["repairReason"] = repair_reason
    digest = sha256(canonical_json(receipt))
    # Content-addressed file uses pretty-print bytes must match stem
    # Contract: filename stem == sha256(file bytes). So write then rename by actual hash.
    # Content-addressed: stem MUST equal sha256 of exact file bytes (LF only; no Windows CRLF).
    body_bytes = (json.dumps(receipt, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    actual = sha256(body_bytes)
    dest_dir = decision_root
    dest = dest_dir / f"{actual}.json"

    if not write:
        return {
            "write": False,
            "wouldWrite": str(dest),
            "receiptSha256": actual,
            "variantId": receipt["variantId"],
            "identity": identity,
        }

    dest_dir.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.read_bytes() != body_bytes:
        raise RuntimeError("immutable_receipt_path_already_has_different_bytes")
    if not dest.exists():
        dest.write_bytes(body_bytes)
    if dest.read_bytes() != body_bytes:
        raise RuntimeError("receipt_readback_mismatch")
    # mark worksheet sealed
    raw["status"] = "sealed"
    raw["sealedAt"] = utc_now()
    raw["approvalReceiptPath"] = str(dest.relative_to(ROOT)).replace("\\", "/")
    raw["approvalReceiptSha256"] = actual
    ws_bytes = (json.dumps(raw, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    worksheet_path.write_bytes(ws_bytes)
    return {
        "write": True,
        "path": str(dest),
        "receiptSha256": actual,
        "variantId": receipt["variantId"],
    }


def cmd_status(
    *,
    decision_root: Path = DEFAULT_PRINTING_DECISIONS,
    batch_root: Path = BATCH_ROOT,
) -> dict[str, Any]:
    decision_root = repo_contained_path(decision_root, label="decision_root")
    batch_root = repo_contained_path(batch_root, label="batch_root")
    decisions, invalid, manifest = load_printing_decisions(decision_root)
    batches = []
    if batch_root.is_dir():
        for d in sorted(batch_root.iterdir(), reverse=True):
            idx = d / "index.json"
            if idx.is_file():
                batches.append(
                    {
                        "batchId": d.name,
                        "path": str(idx),
                        "index": json.loads(idx.read_text(encoding="utf-8")).get("counts"),
                    }
                )
    return {
        "printingDecisions": len(decisions),
        "invalidDecisions": len(invalid),
        "decisionManifestSha256": manifest,
        "batches": batches[:10],
        "decisionRoot": str(decision_root),
        "batchRoot": str(batch_root),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Printing review batch (Step 4)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_open = sub.add_parser("open", help="Open ranked worksheet batch from QC")
    p_open.add_argument("--qc-run", required=True)
    p_open.add_argument("--limit", type=int, default=20)
    p_open.add_argument("--variant-id", action="append", type=int)
    p_open.add_argument("--qc-root", type=Path, default=DEFAULT_CANONICAL_DB_QC)
    p_open.add_argument("--decision-root", type=Path, default=DEFAULT_PRINTING_DECISIONS)
    p_open.add_argument("--batch-root", type=Path, default=BATCH_ROOT)
    p_open.add_argument("--write", action="store_true")

    p_seal = sub.add_parser("seal-worksheet", help="Seal filled worksheet → printing-decisions")
    p_seal.add_argument("--worksheet", type=Path, required=True)
    p_seal.add_argument("--actor", required=True)
    p_seal.add_argument("--decision-root", type=Path, default=DEFAULT_PRINTING_DECISIONS)
    p_seal.add_argument("--batch-root", type=Path, default=BATCH_ROOT)
    p_seal.add_argument("--write", action="store_true")

    p_status = sub.add_parser("status")
    p_status.add_argument("--decision-root", type=Path, default=DEFAULT_PRINTING_DECISIONS)
    p_status.add_argument("--batch-root", type=Path, default=BATCH_ROOT)

    args = ap.parse_args()
    load_env()

    if args.cmd == "open":
        out = cmd_open(
            qc_run=args.qc_run,
            limit=args.limit,
            write=args.write,
            variant_ids=set(args.variant_id) if args.variant_id else None,
            qc_root=args.qc_root,
            decision_root=args.decision_root,
            batch_root=args.batch_root,
        )
    elif args.cmd == "seal-worksheet":
        out = cmd_seal_worksheet(
            worksheet_path=args.worksheet.resolve(),
            actor=args.actor,
            write=args.write,
            decision_root=args.decision_root,
            batch_root=args.batch_root,
        )
    elif args.cmd == "status":
        out = cmd_status(
            decision_root=args.decision_root,
            batch_root=args.batch_root,
        )
    else:
        raise SystemExit(args.cmd)

    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
