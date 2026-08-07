#!/usr/bin/env python3
"""Apply a reviewed external-source binding manifest as one DB transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))

from qualified_pool_operator import db, load_env  # noqa: E402


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def upsert_evidence(
    cur,
    *,
    variant_id: int,
    evidence_kind: str,
    source_code: str,
    external_entity_id: str | None,
    external_url: str | None,
    match_status: str,
    claim: dict[str, Any] | None,
    actor: str,
) -> str:
    """Write the binding's required lineage without a separate helper runtime."""

    claim_json = canonical_json(claim or {})
    evidence_sha256 = sha256_text(
        "|".join(
            [
                str(variant_id),
                evidence_kind,
                source_code,
                external_entity_id or "",
                external_url or "",
                match_status,
                claim_json,
            ]
        )
    )
    cur.execute(
        """
        INSERT INTO catalog_identity_evidence
          (variant_id,evidence_kind,source_code,external_entity_id,external_url,
           match_status,claim_json,evidence_sha256,observed_at,actor)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          external_entity_id=VALUES(external_entity_id),
          external_url=VALUES(external_url),match_status=VALUES(match_status),
          claim_json=VALUES(claim_json),observed_at=VALUES(observed_at),
          actor=VALUES(actor),updated_at=CURRENT_TIMESTAMP
        """,
        (
            variant_id,
            evidence_kind,
            source_code,
            external_entity_id,
            external_url,
            match_status,
            claim_json,
            evidence_sha256,
            datetime.now(timezone.utc).replace(tzinfo=None),
            actor,
        ),
    )
    return evidence_sha256


def normalized_collector(value: Any) -> str:
    normalized = re.sub(r"[^A-Z0-9]", "", str(value or "").upper())
    if normalized.isdigit():
        return str(int(normalized))
    return normalized


def snk_owned_https(value: str) -> bool:
    parsed = urlsplit(value)
    hostname = str(parsed.hostname or "").lower()
    return (
        parsed.scheme == "https"
        and (hostname == "snkrdunk.com" or hostname.endswith(".snkrdunk.com"))
        and parsed.username is None
        and parsed.password is None
        and parsed.port in (None, 443)
        and bool(parsed.path)
        and not parsed.query
        and not parsed.fragment
    )


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
                or parsed.path != f"/en/trading-cards/{external_id}"
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"binding_url_invalid:{external_url}")
            catalog = raw.get("catalogEvidence") or {}
            default_image_url = str(catalog.get("defaultImageUrl") or "").strip()
            if (
                str(catalog.get("itemId") or "") != external_id
                or normalized_collector(catalog.get("productNumber")) != expected_collector
                or not str(catalog.get("name") or "").strip()
                or str(catalog.get("language") or "").strip().lower() != "en"
                or not str(catalog.get("setCode") or "").strip()
                or not str(catalog.get("printingCode") or "").strip()
                or not snk_owned_https(default_image_url)
                or re.fullmatch(
                    r"[0-9a-f]{64}",
                    str(catalog.get("productPageSha256") or "").strip().lower(),
                )
                is None
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
    canonical_name_refreshes: list[dict[str, Any]] = []
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
            expected_canonical_name = str(raw.get("expectedCanonicalName") or "").strip()
            canonical_name = str(variant.get("canonical_name") or "").strip()
            if expected_canonical_name and canonical_name != expected_canonical_name:
                # The canonical PSA/GemRate name is owned by catalog_variant.
                # SNK bindings prove the physical printing tuple below; their
                # old editorial display name must not block a re-run after the
                # canonical PSA title has been completed or corrected.
                if source_code != "snkrdunk":
                    raise ValueError(f"binding_db_name_mismatch:{variant_id}")
                raw["expectedCanonicalName"] = canonical_name
                canonical_name_refreshes.append(
                    {
                        "variantId": variant_id,
                        "from": expected_canonical_name,
                        "to": canonical_name,
                    }
                )
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
            if source_code == "snkrdunk":
                catalog = raw.get("catalogEvidence") or {}
                cursor.execute(
                    """
                    SELECT set_code,printing_code,parallel_code,collector_number,
                           card_language,identity_status
                    FROM catalog_printing_identity
                    WHERE variant_id=%s
                    FOR UPDATE
                    """,
                    (variant_id,),
                )
                printing_rows = list(cursor.fetchall())
                if len(printing_rows) != 1:
                    raise ValueError(f"binding_db_printing_count_invalid:{variant_id}")
                printing = printing_rows[0]
                if (
                    str(printing.get("identity_status") or "")
                    not in {"confirmed", "canonical"}
                    or normalized_collector(printing.get("collector_number"))
                    != expected_collector
                    or str(printing.get("card_language") or "").strip().lower() != "en"
                    or str(printing.get("set_code") or "").strip()
                    != str(catalog.get("setCode") or "").strip()
                    or str(printing.get("printing_code") or "").strip()
                    != str(catalog.get("printingCode") or "").strip()
                    or str(printing.get("parallel_code") or "").strip()
                    != str(raw.get("expectedPrintingParallelCode") or "").strip()
                ):
                    raise ValueError(f"binding_db_snk_printing_mismatch:{variant_id}")

            if source_code == "snkrdunk":
                provider_claims = dict(raw.get("catalogEvidence") or {})
            elif source_code == "gemrate":
                provider_claims = dict(raw.get("searchEvidence") or {})
            else:
                provider_claims = {
                    "pageHeading": raw.get("pageHeading"),
                    "pageTitle": raw.get("pageTitle"),
                    # This value was already proven present in the provider page
                    # heading by load_manifest; it is not copied from catalog_variant.
                    "productNumber": raw.get("expectedCollectorNumber"),
                }
            source_product_number = str(
                provider_claims.get("productNumber")
                or provider_claims.get("cardNumber")
                or ""
            ).strip()
            bound_set_code = str(provider_claims.get("setCode") or "").strip()
            bound_printing_code = str(provider_claims.get("printingCode") or "").strip()
            if (
                len(source_product_number) > 191
                or len(bound_set_code) > 64
                or len(bound_printing_code) > 64
            ):
                raise ValueError(f"binding_provider_claim_too_long:{variant_id}")
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
                "providerClaims": provider_claims,
            }
            bind_evidence_json = canonical_json(evidence_claim)
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

            if source_code == "snkrdunk":
                cursor.execute(
                    """
                    SELECT external_entity_id
                    FROM catalog_source_identity
                    WHERE variant_id=%s
                      AND source_code IN ('snk','snkrdunk','snkrdunk_en')
                      AND LOWER(match_status)='exact'
                    FOR UPDATE
                    """,
                    (variant_id,),
                )
                exact_snk_ids = {
                    str(row.get("external_entity_id") or "").strip()
                    for row in cursor.fetchall()
                }
                if len(exact_snk_ids) > 1 or (
                    exact_snk_ids and exact_snk_ids != {external_id}
                ):
                    raise ValueError(
                        f"binding_variant_multiple_exact_snk_ids:{variant_id}:"
                        + ",".join(sorted(exact_snk_ids))
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
                    SET variant_id=%s, match_status='exact', evidence_sha256=%s,
                        source_product_number=CASE WHEN %s<>'' THEN %s ELSE source_product_number END,
                        bound_set_code=CASE WHEN %s<>'' THEN %s ELSE bound_set_code END,
                        bound_printing_code=CASE WHEN %s<>'' THEN %s ELSE bound_printing_code END,
                        bind_evidence_json=%s
                    WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
                    """,
                    (
                        variant_id, identity_sha, source_product_number, source_product_number,
                        bound_set_code, bound_set_code, bound_printing_code,
                        bound_printing_code, bind_evidence_json, source_code,
                        external_id, previous_owner,
                    ),
                )
                action = "reassigned_from_inactive"
            elif external_id in existing_ids:
                cursor.execute(
                    """
                    UPDATE catalog_source_identity
                    SET match_status='exact', evidence_sha256=%s,
                        source_product_number=CASE WHEN %s<>'' THEN %s ELSE source_product_number END,
                        bound_set_code=CASE WHEN %s<>'' THEN %s ELSE bound_set_code END,
                        bound_printing_code=CASE WHEN %s<>'' THEN %s ELSE bound_printing_code END,
                        bind_evidence_json=%s
                    WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
                    """,
                    (
                        identity_sha, source_product_number, source_product_number,
                        bound_set_code, bound_set_code, bound_printing_code,
                        bound_printing_code, bind_evidence_json, source_code, external_id,
                        variant_id,
                    ),
                )
                action = "confirmed"
            else:
                cursor.execute(
                    """
                    INSERT INTO catalog_source_identity
                      (variant_id, source_code, external_entity_id, match_status, evidence_sha256,
                       source_product_number, bound_set_code, bound_printing_code, bind_evidence_json)
                    VALUES (%s, %s, %s, 'exact', %s, %s, %s, %s, %s)
                    """,
                    (
                        variant_id, source_code, external_id, identity_sha,
                        source_product_number, bound_set_code, bound_printing_code,
                        bind_evidence_json,
                    ),
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
        if canonical_name_refreshes:
            path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
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
        "canonicalNameRefreshes": canonical_name_refreshes,
    }


STRICT_CLAIM_FIELDS = (
    "tcgCode",
    "cardLanguage",
    "collectorNumber",
    "setCode",
    "editionCode",
    "printingCode",
    "parallelCode",
    "finishCode",
)


def _canonical_printing(cur, variant_id: int) -> dict[str, str]:
    cur.execute(
        """
        SELECT tcg_code,card_language,collector_number,set_code,edition_code,
               printing_code,parallel_code,finish_code
        FROM catalog_printing_identity
        WHERE variant_id=%s
        FOR UPDATE
        """,
        (variant_id,),
    )
    rows = list(cur.fetchall())
    if len(rows) != 1:
        raise ValueError(f"decision_printing_count_invalid:{variant_id}:{len(rows)}")
    row = rows[0]
    return {
        "tcgCode": str(row.get("tcg_code") or "").strip(),
        "cardLanguage": str(row.get("card_language") or "").strip(),
        "collectorNumber": str(row.get("collector_number") or "").strip(),
        "setCode": str(row.get("set_code") or "").strip(),
        "editionCode": str(row.get("edition_code") or "").strip(),
        "printingCode": str(row.get("printing_code") or "").strip(),
        "parallelCode": str(row.get("parallel_code") or "").strip(),
        "finishCode": str(row.get("finish_code") or "").strip(),
    }


def _strict_claim_values(claim: dict[str, Any]) -> tuple[str, ...]:
    values = tuple(str(claim.get(field) or "").strip() for field in STRICT_CLAIM_FIELDS)
    if any(not values[index] for index in (0, 1, 2, 3)):
        raise ValueError("decision_required_provider_claim_missing")
    return values


def _apply_canonical_corrections(
    cur, *, payload: dict[str, Any], manifest_sha: str
) -> list[dict[str, Any]]:
    """Apply the reviewed full physical-number corrections before source binding."""

    corrections = payload.get("canonicalCorrections") or []
    if not isinstance(corrections, list):
        raise ValueError("decision_canonical_corrections_invalid")
    applied: list[dict[str, Any]] = []
    for correction in corrections:
        if not isinstance(correction, dict):
            raise ValueError("decision_canonical_correction_invalid")
        variant_id = int(correction.get("variantId") or 0)
        before = str(correction.get("fromCollectorNumber") or "").strip()
        after = str(correction.get("toCollectorNumber") or "").strip()
        if variant_id <= 0 or not before or not after or before == after:
            raise ValueError(f"decision_canonical_correction_invalid:{variant_id}")
        cur.execute(
            "SELECT * FROM catalog_printing_identity WHERE variant_id=%s FOR UPDATE",
            (variant_id,),
        )
        row = dict(cur.fetchone() or {})
        if not row:
            raise ValueError(f"decision_canonical_correction_missing:{variant_id}")
        current = str(row.get("collector_number") or "").strip()
        if current not in {before, after}:
            raise ValueError(
                f"decision_canonical_correction_drift:{variant_id}:{current}"
            )
        parts = [
            str(row.get("tcg_code") or "").strip().casefold(),
            str(row.get("set_name") or "").strip().casefold(),
            after.casefold(),
            str(row.get("card_language") or "").strip().casefold(),
            str(row.get("edition_code") or "").strip().casefold(),
            str(row.get("parallel_code") or "").strip().casefold(),
            str(row.get("finish_code") or "").strip().casefold(),
        ]
        printing_sha = sha256_text("|".join(parts))
        evidence = {
            "contract": payload["contract"],
            "manifestSha256": manifest_sha,
            "variantId": variant_id,
            "fromCollectorNumber": before,
            "toCollectorNumber": after,
            "localEvidence": correction.get("localEvidence"),
            "previousCanonicalPrintingSha256": row.get("canonical_printing_sha256"),
            "previousEvidenceSha256": row.get("evidence_sha256"),
        }
        evidence_sha = sha256_text(canonical_json(evidence))
        cur.execute(
            """
            UPDATE catalog_printing_identity
            SET collector_number=%s,canonical_printing_sha256=%s,
                evidence_sha256=%s,provenance_json=%s,identity_status='confirmed'
            WHERE variant_id=%s
            """,
            (
                after,
                printing_sha,
                evidence_sha,
                canonical_json(evidence),
                variant_id,
            ),
        )
        cur.execute(
            "UPDATE catalog_variant SET collector_number=%s WHERE id=%s",
            (after, variant_id),
        )
        applied.append(
            {
                "variantId": variant_id,
                "collectorNumber": after,
                "canonicalPrintingSha256": printing_sha,
            }
        )
    return applied


def _verify_decision_evidence(decision: dict[str, Any]) -> dict[str, Any]:
    evidence = dict(decision.get("evidence") or {})
    evidence_type = str(evidence.get("type") or "").strip()
    if evidence_type == "database_lineage":
        if not re.fullmatch(r"[0-9a-f]{64}", str(evidence.get("sha256") or "")):
            raise ValueError("decision_database_lineage_invalid")
        return evidence
    raw_path = str(evidence.get("path") or "").strip()
    expected_sha = str(evidence.get("sha256") or "").strip().lower()
    if not raw_path or not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise ValueError("decision_file_evidence_invalid")
    path = Path(raw_path)
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        raise ValueError(f"decision_file_evidence_mismatch:{raw_path}")
    evidence["path"] = str(path)
    return evidence


def _quarantine_binding_observations(
    cur, *, variant_id: int, source_code: str, external_id: str
) -> int:
    storage_sources = (
        ("snkrdunk", "snk", "snk_psa10")
        if source_code == "snkrdunk"
        else (source_code,)
    )
    placeholders = ",".join(["%s"] * len(storage_sources))
    cur.execute(
        f"""
        UPDATE market_price_observation
        SET metric_status='quarantined'
        WHERE variant_id=%s AND source_external_entity_id=%s
          AND source_code IN ({placeholders}) AND metric_status='ready'
        """,
        (variant_id, external_id, *storage_sources),
    )
    return int(cur.rowcount)


def apply_decision_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schemaVersion") != 2:
        raise ValueError("decision_manifest_schema_invalid")
    if payload.get("contract") != "active-762-exact-identity-repair-031-v1":
        raise ValueError("decision_manifest_contract_invalid")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("decision_manifest_empty")

    manifest_sha = sha256_text(canonical_json(payload))
    connection = db()
    applied: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    try:
        cur = connection.cursor()
        canonical_corrections = _apply_canonical_corrections(
            cur, payload=payload, manifest_sha=manifest_sha
        )
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("decision_row_invalid")
            variant_id = int(decision.get("variantId") or 0)
            source_code = str(decision.get("sourceCode") or "").strip().lower()
            external_id = str(decision.get("externalId") or "").strip()
            action = str(decision.get("action") or "").strip().lower()
            key = (variant_id, source_code, external_id)
            if (
                variant_id <= 0
                or source_code not in {"gemrate", "pricecharting", "snkrdunk"}
                or not external_id
                or action not in {"confirm", "reject", "replace"}
                or key in seen
            ):
                raise ValueError(f"decision_identity_invalid:{key}:{action}")
            seen.add(key)
            canonical = _canonical_printing(cur, variant_id)
            expected = dict(decision.get("canonical") or {})
            if any(str(expected.get(field) or "").strip() != canonical[field] for field in STRICT_CLAIM_FIELDS):
                raise ValueError(f"decision_canonical_drift:{variant_id}:{source_code}")
            evidence = _verify_decision_evidence(decision)

            rejected = 0
            quarantined = 0
            replaces = str(decision.get("replacesExternalId") or "").strip()
            if action in {"reject", "replace"}:
                reject_id = external_id if action == "reject" else replaces
                if not reject_id:
                    raise ValueError(f"decision_replace_target_missing:{variant_id}:{source_code}")
                cur.execute(
                    """
                    SELECT match_status,bind_evidence_json
                    FROM catalog_source_identity
                    WHERE variant_id=%s AND source_code=%s AND external_entity_id=%s
                    FOR UPDATE
                    """,
                    (variant_id, source_code, reject_id),
                )
                reject_target = dict(cur.fetchone() or {})
                prior_claim = reject_target.get("bind_evidence_json")
                if isinstance(prior_claim, str):
                    prior_claim = json.loads(prior_claim)
                already_rejected = (
                    str(reject_target.get("match_status") or "").lower() == "rejected"
                    and isinstance(prior_claim, dict)
                    and prior_claim.get("contract") == payload["contract"]
                    and prior_claim.get("manifestSha256") == manifest_sha
                    and prior_claim.get("action") == "reject"
                )
                cur.execute(
                    """
                    UPDATE catalog_source_identity
                    SET match_status='rejected',
                        bind_evidence_json=%s,
                        updated_at=UTC_TIMESTAMP()
                    WHERE variant_id=%s AND source_code=%s AND external_entity_id=%s
                      AND LOWER(match_status)='exact'
                    """,
                    (
                        canonical_json({
                            "contract": payload["contract"],
                            "action": "reject",
                            "reason": decision.get("reason"),
                            "evidence": evidence,
                            "manifestSha256": manifest_sha,
                        }),
                        variant_id,
                        source_code,
                        reject_id,
                    ),
                )
                rejected = int(cur.rowcount)
                if rejected != 1 and not already_rejected:
                    raise ValueError(
                        f"decision_reject_target_not_exact:{variant_id}:{source_code}:{reject_id}"
                    )
                quarantined = _quarantine_binding_observations(
                    cur,
                    variant_id=variant_id,
                    source_code=source_code,
                    external_id=reject_id,
                )
                if action == "reject":
                    applied.append({
                        "variantId": variant_id,
                        "sourceCode": source_code,
                        "externalId": external_id,
                        "action": action,
                        "rejected": rejected,
                        "quarantinedPriceObservations": quarantined,
                    })
                    continue

            provider = dict(decision.get("provider") or {})
            provider_values = _strict_claim_values(provider)
            if any(provider[field] != canonical[field] for field in STRICT_CLAIM_FIELDS):
                raise ValueError(f"decision_provider_mismatch:{variant_id}:{source_code}")
            source_product_number = str(decision.get("sourceProductNumber") or "").strip()
            if not source_product_number:
                raise ValueError(f"decision_source_product_number_missing:{variant_id}:{source_code}")
            identity_sha = sha256_text(canonical_json({
                "contract": payload["contract"],
                "variantId": variant_id,
                "sourceCode": source_code,
                "externalId": external_id,
                "provider": provider,
                "evidence": evidence,
                "manifestSha256": manifest_sha,
            }))
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                  (source_code,external_entity_id,variant_id,match_status,evidence_sha256,
                   source_product_number,bound_set_code,bound_printing_code,bind_evidence_json,
                   bound_tcg_code,bound_card_language,bound_collector_number,
                   bound_edition_code,bound_parallel_code,bound_finish_code)
                VALUES (%s,%s,%s,'exact',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                  variant_id=VALUES(variant_id),match_status='exact',
                  evidence_sha256=CASE
                    WHEN catalog_source_identity.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
                    THEN catalog_source_identity.evidence_sha256
                    ELSE VALUES(evidence_sha256)
                  END,
                  source_product_number=VALUES(source_product_number),
                  bound_set_code=VALUES(bound_set_code),
                  bound_printing_code=VALUES(bound_printing_code),
                  bind_evidence_json=VALUES(bind_evidence_json),
                  bound_tcg_code=VALUES(bound_tcg_code),
                  bound_card_language=VALUES(bound_card_language),
                  bound_collector_number=VALUES(bound_collector_number),
                  bound_edition_code=VALUES(bound_edition_code),
                  bound_parallel_code=VALUES(bound_parallel_code),
                  bound_finish_code=VALUES(bound_finish_code)
                """,
                (
                    source_code,
                    external_id,
                    variant_id,
                    identity_sha,
                    source_product_number,
                    provider_values[3],
                    provider_values[5],
                    canonical_json({
                        "contract": payload["contract"],
                        "action": action,
                        "providerClaims": provider,
                        "evidence": evidence,
                        "manifestSha256": manifest_sha,
                    }),
                    provider_values[0],
                    provider_values[1],
                    provider_values[2],
                    provider_values[4],
                    provider_values[6],
                    provider_values[7],
                ),
            )
            applied.append({
                "variantId": variant_id,
                "sourceCode": source_code,
                "externalId": external_id,
                "action": action,
                "rejected": rejected,
                "quarantinedPriceObservations": quarantined,
            })

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return {
        "action": "apply_active_762_identity_decisions",
        "manifest": str(path),
        "manifestSha256": manifest_sha,
        "canonicalCorrections": canonical_corrections,
        "count": len(applied),
        "applied": applied,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    load_env()
    manifest_path = args.manifest.resolve()
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    result = (
        apply_decision_manifest(manifest_path)
        if raw.get("schemaVersion") == 2
        else apply_manifest(manifest_path)
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
