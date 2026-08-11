#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit and repair CardzMC literal names from immutable GemRate PSA raw rows.

This module never treats a GemRate top-level description as PSA authority.  A
variant is accepted only when one exact GemRate binding resolves to one raw
payload whose ``population_data`` contains exactly one ``grader=psa`` row.

Important causal boundary: failure to enter this new literal-description
acceptance is not evidence that a variant did not originate from GemRate. The
portfolio/universe provenance is established separately by GemRate PSA10 POP
observations and their external IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from identity_name import complete_collector_tail  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402

RAW_ROOT = ROOT / "data" / "private" / "gemrate" / "cards"
OUT_ROOT = ROOT / "data" / "runtime" / "operator" / "psa-identity-repair-034"
SHEET_MANIFEST = ROOT / "data" / "editorial" / "psa-identity-sheet70-034.json"
CONTRACT = "psa-source-identity-repair-034-v1"
ACTOR = "psa_identity_repair_034"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
ONE_PIECE_SET = re.compile(r"(?i)\b(?:OP|EB|ST|PRB)\s*-?\s*\d{1,2}\b")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sql_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def normalize_code(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def normalize_set_code(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())


def parse_time(value: Any, path: Path) -> datetime:
    raw = str(value or "").strip()
    if raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def derive_language(psa_row: dict[str, Any], tcg_code: str) -> tuple[str | None, list[str]]:
    identity_text = " ".join(
        str(psa_row.get(key) or "") for key in ("description", "set_name", "set_url")
    ).casefold()
    hits: list[str] = []
    explicit_traditional = "traditional chinese" in identity_text or "taiwan" in identity_text
    if explicit_traditional:
        hits.append("zhTW")
    if not explicit_traditional and (
        "simplified chinese" in identity_text or re.search(r"\bchinese\b", identity_text)
    ):
        hits.append("zhCN")
    if re.search(r"\bjapanese\b", identity_text):
        hits.append("ja")
    if re.search(r"\bkorean\b", identity_text):
        hits.append("ko")
    hits = sorted(set(hits))
    if len(hits) > 1:
        return None, hits
    if hits:
        return hits[0], hits
    if tcg_code in {"pokemon", "one-piece"}:
        return "en", []
    return None, []


def collector_compatible(psa_number: Any, collector_number: Any, tcg_code: str) -> bool:
    psa = normalize_code(psa_number)
    collector = normalize_code(collector_number)
    if not psa or not collector:
        return False
    if psa == collector:
        return True
    if psa.isdigit():
        # PSA commonly preserves three-digit display padding (007) while older
        # structured rows stored 7, and promo namespaces may store SWSH075.
        # This comparison never rewrites the PSA name or the physical number.
        local_tail = re.search(r"(\d+)$", collector)
        if local_tail and int(psa) == int(local_tail.group(1)):
            return True
    if tcg_code == "one-piece":
        # PSA often uses 078 while the physical printing stores OP01-078.
        return collector.endswith(psa) and psa.isdigit()
    if "/" in str(collector_number):
        collector_head = normalize_code(str(collector_number).split("/", 1)[0])
        if psa == collector_head:
            return True
        if psa.isdigit() and collector_head.isdigit() and int(psa) == int(collector_head):
            return True
    return False


def set_compatible(psa_row: dict[str, Any], printing: dict[str, Any]) -> bool:
    provider = normalize_code(psa_row.get("set_name"))
    local = normalize_code(printing.get("set_name"))
    if not provider or not local:
        return True
    if provider in local or local in provider:
        return True
    stop = {"one", "piece", "pokemon", "booster", "pack", "cards", "card", "the", "of", "and", "en", "jp"}
    provider_tokens = {
        token for token in re.findall(r"[a-z0-9]+", str(psa_row.get("set_name") or "").casefold())
        if len(token) > 2 and token not in stop and not re.fullmatch(r"(?:op|eb|st|prb)\d+", token)
    }
    local_tokens = {
        token for token in re.findall(r"[a-z0-9]+", str(printing.get("set_name") or "").casefold())
        if len(token) > 2 and token not in stop and not re.fullmatch(r"(?:op|eb|st|prb)\d+", token)
    }
    # A set-name conflict is deterministic only when both sides expose useful
    # tokens and share none. Physical One Piece numbers may belong to reprint sets.
    return not provider_tokens or not local_tokens or bool(provider_tokens & local_tokens)


def parallel_compatible(psa_row: dict[str, Any], printing: dict[str, Any]) -> bool:
    raw = str(psa_row.get("parallel") or "").casefold().strip()
    local = " ".join(
        str(printing.get(key) or "").casefold()
        for key in ("printing_code", "parallel_code", "rarity_code")
    )
    if not raw:
        return True
    if raw in {"base", "standard"}:
        return not any(token in local for token in ("aa", "alt", "parallel", "sp", "treasure", "manga"))
    groups = {
        "alternate": ("alternate", ("aa", "alt", "parallel", "-p", "_p")),
        "special": ("special", ("sp", "special", "parallel")),
        "treasure": ("treasure", ("tr", "treasure")),
        "manga": ("manga", ("manga", "comic", "sec-p")),
    }
    applicable = [tokens for marker, tokens in groups.values() if marker in raw]
    if not applicable:
        return True
    # Local printing codes are not a PSA vocabulary (for example Pokemon SAR
    # is often stored with printing_code=base). Absence of a mapped token is
    # ambiguous, not a deterministic conflict; do not reject it automatically.
    return True


def load_psa_raw(gemrate_id: str, pinned_sha: str | None = None) -> dict[str, Any]:
    """Resolve one card's raw GemRate capture.

    ``raw/`` is an append-only content-addressed store: an incremental refresh
    adds a new payload and re-points the receipt, it never rewrites or deletes
    the old one. Callers that already accepted a specific payload must pass its
    sha as ``pinned_sha`` so they keep reading the bytes they accepted; the
    receipt pointer only names the newest capture.
    """
    receipt_path = RAW_ROOT / gemrate_id / "card_details.raw.receipt.json"
    if not receipt_path.is_file():
        return {"error": "missing_psa_raw", "reason": "receipt_missing"}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"error": "missing_psa_raw", "reason": "receipt_invalid"}
    pinned = str(pinned_sha or "").strip().lower()
    resolved_by = "receipt_pointer"
    expected_sha = str(receipt.get("contentSha256") or "").lower()
    source_pointer = str(receipt.get("sourcePointer") or "")
    if pinned and HEX64.match(pinned) and pinned != expected_sha:
        candidate = receipt_path.parent / "raw" / f"{pinned}.json"
        if candidate.is_file():
            source_pointer = f"raw/{pinned}.json"
            expected_sha = pinned
            resolved_by = "pinned_sha"
    raw_path = (receipt_path.parent / source_pointer).resolve()
    try:
        raw_path.relative_to(receipt_path.parent.resolve())
    except ValueError:
        return {"error": "source_mismatch", "reason": "raw_pointer_escape"}
    if not raw_path.is_file():
        return {"error": "missing_psa_raw", "reason": "raw_missing"}
    raw_bytes = raw_path.read_bytes()
    raw_sha = sha256_bytes(raw_bytes)
    if raw_sha != expected_sha:
        return {"error": "source_mismatch", "reason": "raw_hash_mismatch"}
    try:
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"error": "source_mismatch", "reason": "raw_json_invalid"}
    rows = [
        row for row in (payload.get("population_data") or [])
        if isinstance(row, dict) and str(row.get("grader") or "").casefold() == "psa"
    ]
    if len(rows) != 1:
        return {"error": "ambiguous", "reason": f"psa_row_count:{len(rows)}"}
    row = rows[0]
    required = ("description", "year", "set_name")
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in required):
        return {"error": "source_mismatch", "reason": "psa_required_field_missing"}
    if not isinstance(row.get("card_number"), str):
        return {"error": "source_mismatch", "reason": "psa_card_number_not_string"}
    provider_entity_id = str(receipt.get("providerEntityGemrateId") or gemrate_id)
    if str(payload.get("gemrate_id") or provider_entity_id) != provider_entity_id:
        return {"error": "source_mismatch", "reason": "gemrate_id_mismatch"}
    return {
        "receiptPath": str(receipt_path.relative_to(ROOT)).replace("\\", "/"),
        "rawPath": str(raw_path.relative_to(ROOT)).replace("\\", "/"),
        "rawPayloadSha256": raw_sha,
        "resolvedBy": resolved_by,
        "psaRowSha256": sha256_json(row),
        "fetchedAt": receipt.get("fetchedAt"),
        "psa": row,
        "topLevelDescription": payload.get("description"),
        "providerEntityGemrateId": provider_entity_id,
    }


def fetch_catalog(cur: Any) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], set[int]]:
    cur.execute(
        """
        SELECT v.id AS variant_id,v.opaque_id,v.canonical_name,v.card_language AS variant_language,
               v.identity_status AS variant_identity_status,
               v.collector_number AS variant_collector_number,
               p.tcg_code,p.card_language,p.set_name,p.set_code,p.printing_code,p.rarity_code,
               p.collector_number,p.edition_code,p.parallel_code,p.finish_code,
               p.canonical_printing_sha256,p.identity_status AS printing_identity_status
        FROM catalog_variant v
        LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
        ORDER BY v.id
        """
    )
    variants = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        SELECT variant_id,source_code,external_entity_id,match_status,evidence_sha256,
               source_product_number,bound_tcg_code,bound_card_language,bound_collector_number,
               bound_set_code,bound_printing_code,bound_edition_code,bound_parallel_code,
               bound_finish_code,bind_evidence_json
        FROM catalog_source_identity
        WHERE source_code='gemrate'
        ORDER BY variant_id,external_entity_id
        """
    )
    bindings: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in cur.fetchall():
        bindings[int(row["variant_id"])].append(dict(row))
    cur.execute(
        """
        SELECT m.variant_id FROM market_universe_member m
        INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
        """
    )
    active = {int(row["variant_id"]) for row in cur.fetchall()}
    return variants, bindings, active


def classify_variant(variant: dict[str, Any], bindings: list[dict[str, Any]], active: bool) -> dict[str, Any]:
    result: dict[str, Any] = {
        "variantId": int(variant["variant_id"]),
        "opaqueId": variant["opaque_id"],
        "active": active,
        "oldCanonicalName": variant["canonical_name"],
        # The DISPLAY collector number, off catalog_variant. Kept apart from
        # printing.collector_number below because that one is a printing_sha
        # input and therefore frozen on the bare PSA numerator; this one is what
        # the site prints and what the display name's tail has to agree with.
        "variantCollectorNumber": variant.get("variant_collector_number"),
        "printing": {key: variant.get(key) for key in (
            "tcg_code", "card_language", "set_name", "set_code", "printing_code",
            "rarity_code", "collector_number", "edition_code", "parallel_code",
            "finish_code", "canonical_printing_sha256",
        )},
    }
    if not variant.get("canonical_printing_sha256"):
        result.update(classification="printing_mismatch", reason="canonical_printing_identity_missing")
        return result
    exact = [row for row in bindings if str(row.get("match_status") or "").casefold() == "exact"]
    if not exact:
        result.update(classification="missing_psa_raw", reason="no_exact_gemrate_binding")
        return result
    if len(exact) != 1:
        result.update(classification="ambiguous", reason=f"exact_gemrate_binding_count:{len(exact)}")
        return result
    binding = exact[0]
    result["gemrateId"] = binding["external_entity_id"]
    raw = load_psa_raw(str(binding["external_entity_id"]))
    if raw.get("error"):
        result.update(classification=raw["error"], reason=raw["reason"])
        return result
    psa = raw["psa"]
    result["rawEvidence"] = {key: value for key, value in raw.items() if key != "psa"}
    result["psa"] = {key: psa.get(key) for key in (
        "description", "year", "set_name", "card_number", "parallel", "set_url"
    )}
    language, language_hits = derive_language(psa, str(variant["tcg_code"]))
    result["derivedLanguage"] = language
    result["languageMarkers"] = language_hits
    if language is None or language != str(variant.get("card_language") or ""):
        result.update(classification="language_mismatch", reason="psa_language_conflict")
    elif not collector_compatible(psa.get("card_number"), variant.get("collector_number"), str(variant["tcg_code"])):
        result.update(classification="printing_mismatch", reason="collector_number_conflict")
    elif not set_compatible(psa, variant):
        result.update(classification="printing_mismatch", reason="set_namespace_conflict")
    elif not parallel_compatible(psa, variant):
        result.update(classification="printing_mismatch", reason="parallel_conflict")
    elif str(variant.get("canonical_name") or "").encode("utf-8") != complete_collector_tail(
        psa["description"], variant.get("variant_collector_number")
    ).encode("utf-8"):
        result.update(classification="name_mismatch", reason="canonical_name_not_literal_psa_description")
    else:
        result.update(classification="exact", reason="literal_psa_identity_matches")
    return result


def build_audit(connection: Any) -> dict[str, Any]:
    cur = connection.cursor()
    variants, bindings, active = fetch_catalog(cur)
    rows = [classify_variant(row, bindings.get(int(row["variant_id"]), []), int(row["variant_id"]) in active) for row in variants]
    counts = Counter(row["classification"] for row in rows)
    active_counts = Counter(row["classification"] for row in rows if row["active"])
    payload = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": utc_now(),
        "catalogCount": len(rows),
        "activeUniverseCount": len(active),
        "classificationCounts": dict(sorted(counts.items())),
        "activeClassificationCounts": dict(sorted(active_counts.items())),
        "rows": rows,
    }
    payload["manifestSha256"] = sha256_json({k: v for k, v in payload.items() if k not in {"generatedAt", "manifestSha256"}})
    return payload


def write_audit(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_audit(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = sha256_json({k: v for k, v in payload.items() if k not in {"generatedAt", "manifestSha256"}})
    if payload.get("contract") != CONTRACT or payload.get("manifestSha256") != expected:
        raise RuntimeError("audit_manifest_invalid")
    rows = payload.get("rows")
    if not isinstance(rows, list) or len(rows) != int(payload.get("catalogCount") or 0):
        raise RuntimeError("audit_manifest_row_count_invalid")
    return payload


def _reclassify_from_captured_evidence(row: dict[str, Any]) -> None:
    """Correct policy classification without performing a second raw audit."""

    if not row.get("psa") or not row.get("rawEvidence"):
        return
    psa = row["psa"]
    printing = row["printing"]
    language, markers = derive_language(psa, str(printing.get("tcg_code") or ""))
    row["derivedLanguage"] = language
    row["languageMarkers"] = markers
    if language is None or language != str(printing.get("card_language") or ""):
        row.update(classification="language_mismatch", reason="psa_language_conflict")
    elif not collector_compatible(psa.get("card_number"), printing.get("collector_number"), str(printing.get("tcg_code") or "")):
        row.update(classification="printing_mismatch", reason="collector_number_conflict")
    elif not set_compatible(psa, printing):
        row.update(classification="printing_mismatch", reason="set_namespace_conflict")
    elif not parallel_compatible(psa, printing):
        row.update(classification="printing_mismatch", reason="parallel_conflict")
    elif str(row.get("oldCanonicalName") or "").encode("utf-8") != complete_collector_tail(
        psa["description"], row.get("variantCollectorNumber")
    ).encode("utf-8"):
        row.update(classification="name_mismatch", reason="canonical_name_not_literal_psa_description")
    else:
        row.update(classification="exact", reason="literal_psa_identity_matches")


def complete_audit_for_apply(connection: Any, audit: dict[str, Any]) -> dict[str, Any]:
    """Complete coverage from the captured audit without re-running it.

    The single audit exposed 53 variants without a printing row because the
    original catalog query used an inner join. They are appended as explicit
    unresolved rows here, before any mutation, and the captured raw evidence
    rows are reclassified under the corrected deterministic comparison policy.
    """

    for row in audit["rows"]:
        _reclassify_from_captured_evidence(row)
    existing = {int(row["variantId"]) for row in audit["rows"]}
    cur = connection.cursor()
    variants, bindings, active = fetch_catalog(cur)
    for variant in variants:
        variant_id = int(variant["variant_id"])
        if variant_id not in existing:
            audit["rows"].append(classify_variant(variant, bindings.get(variant_id, []), variant_id in active))
    audit["rows"].sort(key=lambda row: int(row["variantId"]))
    audit["catalogCount"] = len(audit["rows"])
    audit["activeUniverseCount"] = sum(1 for row in audit["rows"] if row["active"])
    audit["classificationCounts"] = dict(sorted(Counter(row["classification"] for row in audit["rows"]).items()))
    audit["activeClassificationCounts"] = dict(sorted(Counter(row["classification"] for row in audit["rows"] if row["active"]).items()))
    audit["coverageCompletedAtApply"] = True
    audit["manifestSha256"] = sha256_json({k: v for k, v in audit.items() if k not in {"generatedAt", "manifestSha256"}})
    return audit


def load_sheet_manifest() -> dict[str, Any]:
    payload = json.loads(SHEET_MANIFEST.read_text(encoding="utf-8-sig"))
    if (
        payload.get("contract") != "psa-identity-sheet70-034-v1"
        or len(payload.get("oldCanonicalNames") or []) != 70
        or payload.get("greenSheetRows") != [5, 6, 7, 8, 9, 13]
        or payload.get("redSheetRows") != list(range(62, 75))
    ):
        raise RuntimeError("sheet70_manifest_invalid")
    return payload


def _binding_evidence(row: dict[str, Any]) -> tuple[str, str]:
    psa = row["psa"]
    printing = row["printing"]
    raw = row["rawEvidence"]
    evidence = {
        "contract": CONTRACT,
        "action": "confirm",
        "evidence": {
            "type": "provider_payload",
            "path": raw["rawPath"],
            "sha256": raw["rawPayloadSha256"],
            "rawPayloadSha256": raw["rawPayloadSha256"],
            "providerRowSha256": raw["psaRowSha256"],
        },
        "providerClaims": {
            "tcgCode": printing["tcg_code"],
            "cardLanguage": row["derivedLanguage"],
            "collectorNumber": psa["card_number"],
            "setCode": printing["set_code"],
            "printingCode": printing["printing_code"],
            "parallelCode": psa.get("parallel") or "",
            "year": psa["year"],
            "setName": psa["set_name"],
            "description": psa["description"],
        },
        "normalizedBindingClaims": {
            "collectorNumber": printing["collector_number"],
            "parallelCode": printing["parallel_code"],
            "editionCode": printing["edition_code"],
            "finishCode": printing["finish_code"],
        },
    }
    return canonical_json(evidence), sha256_json(evidence)


def _quarantine_variant(cur: Any, variant_id: int, reason: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    cur.execute("UPDATE market_price_observation SET metric_status='quarantined' WHERE variant_id=%s AND metric_status='ready'", (variant_id,))
    counts["prices"] = int(cur.rowcount)
    cur.execute("UPDATE market_sale_observation SET coverage_status='quarantined' WHERE variant_id=%s AND coverage_status<>'quarantined'", (variant_id,))
    counts["sales"] = int(cur.rowcount)
    cur.execute("UPDATE market_image_source_pointer SET public_allowed=0 WHERE variant_id=%s AND public_allowed=1", (variant_id,))
    counts["imagePointers"] = int(cur.rowcount)
    cur.execute(
        """UPDATE operator_binding_freeze SET acceptance_status='rejected',actor=%s,note=%s,accepted_at=%s
           WHERE variant_id=%s AND acceptance_status='accepted'""",
        (ACTOR, reason[:1000], sql_now(), variant_id),
    )
    counts["freezes"] = int(cur.rowcount)
    return counts


def apply_audit(connection: Any, audit: dict[str, Any]) -> dict[str, Any]:
    sheet = load_sheet_manifest()
    audit_by_old_name = {str(row["oldCanonicalName"]): row for row in audit["rows"]}
    red_ids = {
        int(audit_by_old_name[sheet["oldCanonicalNames"][sheet_row - 5]]["variantId"])
        for sheet_row in sheet["redSheetRows"]
    }
    acceptable = {"exact", "name_mismatch"}
    affected = Counter()
    accepted = 0
    unresolved = 0
    cur = connection.cursor()
    now = sql_now()
    try:
        cur.execute("SELECT GET_LOCK('cardz-market-cap:psa-identity-repair-034',0) AS acquired")
        if int((cur.fetchone() or {}).get("acquired") or 0) != 1:
            raise RuntimeError("psa_identity_repair_lock_unavailable")
        for row in audit["rows"]:
            variant_id = int(row["variantId"])
            classification = str(row["classification"])
            if classification in acceptable:
                raw = row["rawEvidence"]
                psa = row["psa"]
                printing = row["printing"]
                evidence = {
                    "contract": CONTRACT,
                    "variantId": variant_id,
                    "gemrateId": row["gemrateId"],
                    "rawPayloadSha256": raw["rawPayloadSha256"],
                    "psaRowSha256": raw["psaRowSha256"],
                    "canonicalPrintingSha256": printing["canonical_printing_sha256"],
                    "literalDescription": psa["description"],
                }
                evidence_sha = sha256_json(evidence)
                lineage_sha = sha256_json({"kind": CONTRACT, **evidence})
                cur.execute(
                    """SELECT id,lineage_sha256 FROM catalog_psa_identity_acceptance
                       WHERE variant_id=%s AND NOT EXISTS (
                         SELECT 1 FROM catalog_psa_identity_acceptance newer
                         WHERE newer.supersedes_acceptance_id=catalog_psa_identity_acceptance.id)
                       ORDER BY accepted_at DESC,id DESC LIMIT 1""",
                    (variant_id,),
                )
                current = cur.fetchone()
                supersedes = None if not current or current["lineage_sha256"] == lineage_sha else int(current["id"])
                cur.execute(
                    """INSERT INTO catalog_psa_identity_acceptance
                       (variant_id,gemrate_id,psa_description,psa_year,psa_set_name,psa_card_number,
                        psa_parallel,psa_set_url,psa_language,raw_payload_sha256,psa_row_sha256,
                        canonical_printing_sha256,evidence_sha256,lineage_sha256,source_observed_at,
                        accepted_by,accepted_at,supersedes_acceptance_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)""",
                    (
                        variant_id, row["gemrateId"], psa["description"], psa["year"], psa["set_name"],
                        psa["card_number"], psa.get("parallel") or "", psa.get("set_url") or "",
                        row["derivedLanguage"], raw["rawPayloadSha256"], raw["psaRowSha256"],
                        printing["canonical_printing_sha256"], evidence_sha, lineage_sha,
                        parse_time(raw.get("fetchedAt"), ROOT / raw["rawPath"]), ACTOR, now, supersedes,
                    ),
                )
                cur.execute(
                    "UPDATE catalog_variant SET canonical_name=%s,card_language=%s,identity_status='confirmed' WHERE id=%s",
                    (
                        # psa_description above stays the literal; this column is
                        # the display name, so only here does the truncated
                        # collector tail get completed. See identity_name.
                        complete_collector_tail(
                            psa["description"], row.get("variantCollectorNumber")
                        ),
                        row["derivedLanguage"], variant_id,
                    ),
                )
                affected["canonicalNames"] += int(cur.rowcount)
                binding_json, binding_sha = _binding_evidence(row)
                cur.execute(
                    """UPDATE catalog_source_identity
                       SET match_status='exact',evidence_sha256=%s,bind_evidence_json=%s
                       WHERE variant_id=%s AND source_code='gemrate' AND external_entity_id=%s""",
                    (binding_sha, binding_json, variant_id, row["gemrateId"]),
                )
                accepted += 1
            else:
                status = "incomplete" if classification == "missing_psa_raw" else "review"
                cur.execute("UPDATE catalog_variant SET identity_status=%s WHERE id=%s", (status, variant_id))
                cur.execute(
                    "UPDATE catalog_source_identity SET match_status='manual_review' WHERE variant_id=%s AND source_code='gemrate' AND match_status='exact'",
                    (variant_id,),
                )
                unresolved += 1

            if variant_id in red_ids or classification in {
                "language_mismatch", "printing_mismatch", "source_mismatch", "ambiguous"
            }:
                # This rejects every non-gemrate binding on the card because the
                # card's own GEMRATE identity would not resolve -- nothing here
                # examined SNKRDUNK or PriceCharting at all. Say so in the row.
                #
                # Left unstamped, these are indistinguishable from a contract
                # that read a binding and refused it, and the difference decides
                # whether a later discovery run may reconsider. On 2026-08-07 an
                # unstamped pass put 226 price-lane rows into this state; the
                # gemrate identities were repaired the next morning and 121 cards
                # at PSA10 population >= 1000 stayed off the front end looking
                # permanently ruled out. bind_evidence_json is assigned first so
                # it captures match_status before this statement overwrites it,
                # and it keeps the prior claim rather than erasing it.
                cur.execute(
                    """UPDATE catalog_source_identity
                          SET bind_evidence_json=JSON_OBJECT(
                                'contract', %s,
                                'action', 'quarantine-unresolved-variant-identity',
                                'reasonCode', %s,
                                'redListed', %s,
                                'reason', 'gemrate identity unresolved for this'
                                  ' variant; this provider binding was not examined',
                                'quarantinedAt', UTC_TIMESTAMP(),
                                'previousMatchStatus', match_status,
                                'previousEvidence', bind_evidence_json),
                              match_status='rejected'
                        WHERE variant_id=%s AND source_code<>'gemrate'
                          AND match_status<>'rejected'""",
                    (CONTRACT, classification, variant_id in red_ids, variant_id),
                )
                affected["bindingsRejected"] += int(cur.rowcount)
                affected.update(_quarantine_variant(cur, variant_id, f"034 PSA identity quarantine: {classification}"))

        # Historical database-lineage claims may remain in storage but cannot remain exact.
        cur.execute(
            """UPDATE catalog_source_identity
               SET match_status='manual_review'
               WHERE match_status='exact'
                 AND JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.evidence.type'))='database_lineage'"""
        )
        affected["databaseLineageBindingsDemoted"] += int(cur.rowcount)
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('cardz-market-cap:psa-identity-repair-034')")
        except Exception:
            pass
    return {
        "contract": CONTRACT,
        "appliedAt": utc_now(),
        "manifestSha256": audit["manifestSha256"],
        "accepted": accepted,
        "unresolved": unresolved,
        "affected": dict(sorted(affected.items())),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--output", type=Path, default=OUT_ROOT / "audit.json")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--audit", type=Path, default=OUT_ROOT / "audit.json")
    apply_parser.add_argument("--receipt", type=Path, default=OUT_ROOT / "apply-receipt.json")
    args = parser.parse_args()
    load_env()
    connection = db()
    try:
        if args.command == "audit":
            payload = build_audit(connection)
            write_audit(payload, args.output)
            print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, ensure_ascii=False, sort_keys=True))
        else:
            completed_audit = complete_audit_for_apply(connection, load_audit(args.audit))
            write_audit(completed_audit, args.audit)
            payload = apply_audit(connection, completed_audit)
            write_audit(payload, args.receipt)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
