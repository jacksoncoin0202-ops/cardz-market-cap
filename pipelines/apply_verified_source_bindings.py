#!/usr/bin/env python3
"""Apply a reviewed external-source binding manifest as one DB transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from identity_evidence_ledger import upsert_evidence  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalized_collector(value: Any) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if normalized.isdigit():
        return str(int(normalized))
    return normalized


def load_manifest(path: Path) -> tuple[dict[str, Any], str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 1:
        raise ValueError("unsupported_binding_manifest_schema")
    rows = payload.get("bindings")
    if not isinstance(rows, list) or not rows:
        raise ValueError("binding_manifest_empty")

    variants: set[int] = set()
    source_entities: set[tuple[str, str]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("binding_manifest_row_invalid")
        variant_id = int(raw.get("variantId") or 0)
        source_code = str(raw.get("sourceCode") or "").strip()
        external_id = str(raw.get("externalId") or "").strip()
        expected_collector = normalized_collector(raw.get("expectedCollectorNumber"))
        external_url = str(raw.get("externalUrl") or "").strip()
        parsed = urlsplit(external_url)
        if variant_id <= 0 or variant_id in variants:
            raise ValueError(f"binding_variant_invalid:{variant_id}")
        if source_code not in {"pricecharting", "gemrate", "snkrdunk"}:
            raise ValueError(f"binding_source_invalid:{source_code}")
        if (source_code, external_id) in source_entities:
            raise ValueError(f"binding_external_duplicate:{source_code}:{external_id}")
        if source_code == "pricecharting":
            if not external_id.isdigit():
                raise ValueError(f"binding_external_invalid:{source_code}:{external_id}")
            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.pricecharting.com"
                or not parsed.path.startswith("/game/")
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"binding_url_invalid:{external_url}")
            heading_collector = normalized_collector(raw.get("pageHeading"))
            if not expected_collector or expected_collector not in heading_collector:
                raise ValueError(f"binding_heading_collector_mismatch:{variant_id}")
            local_evidence = str(raw.get("localEvidencePath") or "").strip()
            if local_evidence:
                evidence_path = (ROOT / local_evidence).resolve()
                try:
                    evidence_path.relative_to(ROOT.resolve())
                except ValueError as exc:
                    raise ValueError(f"binding_local_evidence_outside_project:{variant_id}") from exc
                if not evidence_path.is_file():
                    raise ValueError(f"binding_local_evidence_missing:{variant_id}")
                evidence_html = evidence_path.read_text(encoding="utf-8", errors="replace")
                product_ids = set(
                    re.findall(
                        r'(?:data-product-id|id="product_name"[^>]*title)=["\'](\d+)["\']',
                        evidence_html,
                        re.I,
                    )
                )
                flattened = re.sub(r"\s+", " ", evidence_html)
                if (
                    external_id not in product_ids
                    or external_url not in evidence_html
                    or str(raw.get("pageTitle") or "").strip() not in flattened
                ):
                    raise ValueError(f"binding_local_evidence_mismatch:{variant_id}")
        if source_code == "gemrate":
            if not re.fullmatch(r"[0-9a-f]{40}", external_id):
                raise ValueError(f"binding_external_invalid:{source_code}:{external_id}")
            if (
                parsed.scheme != "https"
                or parsed.netloc != "www.gemrate.com"
                or parsed.path != "/universal-search"
                or parsed.query != f"gemrate_id={external_id}"
                or parsed.fragment
            ):
                raise ValueError(f"binding_url_invalid:{external_url}")
            search = raw.get("searchEvidence") or {}
            if (
                normalized_collector(search.get("cardNumber")) != expected_collector
                or str(search.get("name") or "").strip() != str(raw.get("expectedCanonicalName") or "").strip()
                or str(search.get("parallel") or "").strip() != "Base"
            ):
                raise ValueError(f"binding_gemrate_evidence_mismatch:{variant_id}")
        if source_code == "snkrdunk":
            if not external_id.isdigit():
                raise ValueError(f"binding_external_invalid:{source_code}:{external_id}")
            if (
                parsed.scheme != "https"
                or parsed.netloc != "snkrdunk.com"
                or parsed.path != f"/app/products/{external_id}"
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"binding_url_invalid:{external_url}")
            catalog = raw.get("catalogEvidence") or {}
            if (
                str(catalog.get("itemId") or "") != external_id
                or normalized_collector(catalog.get("productNumber")) != expected_collector
                or not str(catalog.get("name") or "").strip()
                or int(catalog.get("tradesIngested") or 0) <= 0
            ):
                raise ValueError(f"binding_snkrdunk_evidence_mismatch:{variant_id}")
        language_after = str(raw.get("setLanguage") or "").strip()
        if language_after:
            if source_code != "pricecharting" or language_after != "en":
                raise ValueError(f"binding_language_change_invalid:{variant_id}")
            if str(raw.get("expectedLanguageBefore") or "").strip() != "ja":
                raise ValueError(f"binding_language_precondition_invalid:{variant_id}")
        variants.add(variant_id)
        source_entities.add((source_code, external_id))

    manifest_sha = sha256_text(canonical_json(payload))
    return payload, manifest_sha


def apply_manifest(path: Path) -> dict[str, Any]:
    manifest, manifest_sha = load_manifest(path)
    connection = db()
    applied: list[dict[str, Any]] = []
    try:
        cursor = connection.cursor()
        cursor.execute(
            """
            SELECT m.variant_id
            FROM market_universe_lock l
            JOIN market_universe_member m ON m.universe_lock_id=l.id
            WHERE l.is_current=1
            """
        )
        active_ids = {int(row["variant_id"]) for row in cursor.fetchall()}
        for raw in manifest["bindings"]:
            variant_id = int(raw["variantId"])
            source_code = str(raw["sourceCode"])
            external_id = str(raw["externalId"])
            expected_collector = normalized_collector(raw["expectedCollectorNumber"])
            cursor.execute(
                """
                SELECT id, opaque_id, canonical_name, set_name, collector_number, card_language
                FROM catalog_variant
                WHERE id=%s
                FOR UPDATE
                """,
                (variant_id,),
            )
            variant = cursor.fetchone()
            if variant is None:
                raise ValueError(f"binding_variant_missing:{variant_id}")
            if normalized_collector(variant.get("collector_number")) != expected_collector:
                raise ValueError(f"binding_db_collector_mismatch:{variant_id}")
            expected_language = str(raw.get("expectedLanguageBefore") or "").strip()
            if expected_language and str(variant.get("card_language") or "").strip() != expected_language:
                raise ValueError(f"binding_db_language_mismatch:{variant_id}")
            if raw.get("expectedCanonicalName") is not None and str(
                variant.get("canonical_name") or ""
            ).strip() != str(raw["expectedCanonicalName"]).strip():
                raise ValueError(f"binding_db_name_mismatch:{variant_id}")
            if raw.get("expectedSetName") is not None and str(
                variant.get("set_name") or ""
            ).strip() != str(raw["expectedSetName"]).strip():
                raise ValueError(f"binding_db_set_mismatch:{variant_id}")
            if raw.get("expectedPrintingParallelCode") is not None:
                cursor.execute(
                    """
                    SELECT parallel_code
                    FROM catalog_printing_identity
                    WHERE variant_id=%s
                    FOR UPDATE
                    """,
                    (variant_id,),
                )
                printing = cursor.fetchone()
                if printing is None or str(printing.get("parallel_code") or "").strip() != str(
                    raw["expectedPrintingParallelCode"]
                ).strip():
                    raise ValueError(f"binding_db_parallel_mismatch:{variant_id}")

            evidence_claim = {
                "externalUrl": raw["externalUrl"],
                "expectedCollectorNumber": raw["expectedCollectorNumber"],
                "manifestSha256": manifest_sha,
                "pageHeading": raw.get("pageHeading"),
                "pageTitle": raw.get("pageTitle"),
                "localEvidencePath": raw.get("localEvidencePath"),
                "searchEvidence": raw.get("searchEvidence"),
                "catalogEvidence": raw.get("catalogEvidence"),
                "languageCorrection": (
                    {
                        "before": raw.get("expectedLanguageBefore"),
                        "after": raw.get("setLanguage"),
                    }
                    if raw.get("setLanguage")
                    else None
                ),
                "verificationMethod": raw["verificationMethod"],
                "verifiedOn": manifest["verifiedOn"],
            }
            identity_sha = sha256_text(
                canonical_json(
                    {
                        "source": source_code,
                        "externalId": external_id,
                        "variantId": variant_id,
                        **evidence_claim,
                    }
                )
            )
            cursor.execute(
                """
                SELECT external_entity_id, match_status
                FROM catalog_source_identity
                WHERE variant_id=%s AND source_code=%s
                FOR UPDATE
                """,
                (variant_id, source_code),
            )
            existing_rows = list(cursor.fetchall())
            existing_ids = [str(row["external_entity_id"]) for row in existing_rows]
            active_existing_ids = [
                str(row["external_entity_id"])
                for row in existing_rows
                if str(row.get("match_status") or "") not in {"conflict", "rejected"}
            ]
            other_active_ids = [value for value in active_existing_ids if value != external_id]
            if other_active_ids:
                raise ValueError(
                    f"binding_variant_source_active_conflict:{variant_id}:{source_code}:"
                    + ",".join(sorted(other_active_ids))
                )

            cursor.execute(
                """
                SELECT variant_id, match_status
                FROM catalog_source_identity
                WHERE source_code=%s AND external_entity_id=%s
                FOR UPDATE
                """,
                (source_code, external_id),
            )
            owner = cursor.fetchone()
            previous_owner = int(owner["variant_id"]) if owner is not None else None
            if previous_owner is not None and previous_owner != variant_id and previous_owner in active_ids:
                raise ValueError(
                    f"binding_external_active_owned:{source_code}:{external_id}:{previous_owner}"
                )

            if previous_owner is not None and previous_owner != variant_id:
                cursor.execute(
                    """
                    UPDATE catalog_source_identity
                    SET variant_id=%s, match_status='exact', evidence_sha256=%s
                    WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
                    """,
                    (variant_id, identity_sha, source_code, external_id, previous_owner),
                )
                action = "reassigned_from_inactive"
            elif external_id in existing_ids:
                cursor.execute(
                    """
                    UPDATE catalog_source_identity
                    SET match_status='exact', evidence_sha256=%s
                    WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
                    """,
                    (identity_sha, source_code, external_id, variant_id),
                )
                action = "confirmed"
            else:
                cursor.execute(
                    """
                    INSERT INTO catalog_source_identity
                      (variant_id, source_code, external_entity_id, match_status, evidence_sha256)
                    VALUES (%s, %s, %s, 'exact', %s)
                    """,
                    (variant_id, source_code, external_id, identity_sha),
                )
                action = "inserted"

            language_before = str(variant.get("card_language") or "")
            language_after = str(raw.get("setLanguage") or "").strip()
            if language_after:
                cursor.execute(
                    "UPDATE catalog_variant SET card_language=%s WHERE id=%s AND card_language=%s",
                    (language_after, variant_id, language_before),
                )
                if cursor.rowcount != 1:
                    raise ValueError(f"binding_language_update_failed:{variant_id}")

            ledger_sha = upsert_evidence(
                cursor,
                variant_id=variant_id,
                evidence_kind="bind",
                source_code=source_code,
                external_entity_id=external_id,
                external_url=raw["externalUrl"],
                match_status="exact",
                claim=evidence_claim,
                actor="codex_launch_20260804",
            )
            applied.append(
                {
                    "variantId": variant_id,
                    "sourceCode": source_code,
                    "externalId": external_id,
                    "action": action,
                    "previousOwnerVariantId": previous_owner if previous_owner != variant_id else None,
                    "languageBefore": language_before,
                    "languageAfter": language_after or language_before,
                    "identityEvidenceSha256": identity_sha,
                    "ledgerEvidenceSha256": ledger_sha,
                }
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "action": "apply_verified_source_bindings",
        "manifest": str(path),
        "manifestSha256": manifest_sha,
        "applied": applied,
        "count": len(applied),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    load_env()
    result = apply_manifest(args.manifest.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
