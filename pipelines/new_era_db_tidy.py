#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply new-era DB tidy rules and report warehouse state."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from identity_name import complete_collector_tail  # noqa: E402
from qualified_pool_operator import db, load_env  # noqa: E402

REQUIRED_SCHEMA_VERSIONS = (
    "022", "023", "024", "025", "026", "027", "028", "029", "030",
    "031", "032", "033", "034", "035"
)
OUT = ROOT / "data" / "runtime" / "operator"
BANNED_PRICE_SOURCES = ("g10_kline",)
EDITORIAL_NAMES = ROOT / "data" / "editorial" / "card-names.json"
EDITORIAL_SETS = ROOT / "data" / "editorial" / "set-names.json"
EDITORIAL_STORIES = ROOT / "data" / "editorial" / "top100-stories.json"
BASELINE_GENERATION_ID = "product_subset_20260804T151703Z"
BASELINE_GENERATION_ROOT = ROOT / "data" / "public" / "generations" / BASELINE_GENERATION_ID
BASELINE_SNAPSHOT = (
    BASELINE_GENERATION_ROOT / "seed-snapshot.json"
)
BASELINE_RELEASE_ROOT = ROOT / "data" / "release-locks" / BASELINE_GENERATION_ID
BASELINE_RELEASE_LOCK = BASELINE_RELEASE_ROOT / "release-lock.json"
BASELINE_ASSET_MANIFEST = BASELINE_RELEASE_ROOT / "asset-manifest.json"
BASELINE_IMAGE_LINEAGE = BASELINE_RELEASE_ROOT / "baseline-image-lineage.json"
ONE_TIME_DEPENDENCY_LOCK = BASELINE_RELEASE_ROOT / "one-time-dependency-lock.json"
PYTHON_REQUIREMENTS = ROOT / "pipelines" / "requirements.txt"
CANONICAL_IMAGE_REBASE = ROOT / "data" / "editorial" / "canonical-image-rebase.json"
CANONICAL_PRINTING_REPAIRS = (
    ROOT / "data" / "editorial" / "canonical-printing-repairs-026.json"
)
CANONICAL_GEMRATE_IDENTITY_REPAIRS = (
    ROOT / "data" / "editorial" / "canonical-gemrate-identity-repairs-026.json"
)
CANONICAL_MARKET_SOURCE_IDENTITY_REPAIRS = (
    ROOT / "data" / "editorial" / "canonical-market-source-identity-repairs-026.json"
)
CANONICAL_PC_MAP = (
    ROOT / "data" / "runtime" / "private-source-map" / "c11_pc_ebay_map_full900.jsonl"
)
PUBLIC_ASSETS = ROOT / "data" / "public" / "market-assets"
LOCALES = ("en", "zhTW", "zhCN", "ja", "ko")
ACCEPTANCE_ACTOR = "new_era_db_tidy_026"
KNOWN_UNKNOWN_PRINTING_CONTRACT = "canonical-printing-known-unknown-026-v1"
LEGACY_ROOT = ROOT.parent / "cardz-market-cap"
OFFICIAL_NAME_MAP_CANDIDATES = (
    ROOT / "data" / "runtime" / "operator" / "export" / "psa_official_name_map_latest.jsonl",
    LEGACY_ROOT / "data" / "runtime" / "operator" / "export" / "psa_official_name_map_latest.jsonl",
)
OFFICIAL_NAME_APPLY_CANDIDATES = (
    ROOT / "data" / "runtime" / "operator" / "export" / "psa_official_name_db_apply_latest.json",
    LEGACY_ROOT / "data" / "runtime" / "operator" / "export" / "psa_official_name_db_apply_latest.json",
)
GEMRATE_CARD_ROOT_CANDIDATES = (
    ROOT / "data" / "private" / "gemrate" / "cards",
    LEGACY_ROOT / "data" / "private" / "gemrate" / "cards",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def utc_now_sql() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def phase(name: str, **details) -> None:
    print(
        json.dumps({"phase": name, "at": utc_now(), **details}, ensure_ascii=False, default=str),
        flush=True,
    )


def _read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise RuntimeError(f"editorial source is not an object: {path}")
    return value


def _source_observed_at(document: dict) -> datetime:
    generation = document.get("generation") if isinstance(document.get("generation"), dict) else {}
    raw = (
        document.get("generatedAt")
        or document.get("snapshotEffectiveAt")
        or generation.get("generatedAt")
        or generation.get("effectiveAt")
    )
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("editorial source has no observed timestamp")
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _workspace_evidence_path(raw: str) -> Path:
    path = (ROOT / str(raw)).resolve()
    try:
        path.relative_to(ROOT.parent.resolve())
    except ValueError as exc:
        raise RuntimeError(f"026 source repair evidence escapes the workspace: {raw}") from exc
    if not path.is_file():
        raise RuntimeError(f"026 source repair evidence is missing: {path}")
    return path


def _validated_market_source_repair_manifest() -> tuple[dict, str, dict[int, dict]]:
    manifest = _read_json(CANONICAL_MARKET_SOURCE_IDENTITY_REPAIRS)
    entries = manifest.get("entries")
    rejects = manifest.get("rejects")
    if (
        manifest.get("schemaVersion") != 1
        or manifest.get("contract")
        != "canonical-market-source-identity-repair-026-v1"
        or not isinstance(entries, list)
        or len(entries) != 5
        or not isinstance(rejects, list)
        or len(rejects) != 2
    ):
        raise RuntimeError("026 canonical market-source repair manifest is invalid")
    manifest_sha256 = _file_sha256(CANONICAL_MARKET_SOURCE_IDENTITY_REPAIRS)
    validated: dict[int, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("026 canonical market-source repair entry is invalid")
        variant_id = int(entry.get("targetVariantId") or 0)
        source_code = str(entry.get("sourceCode") or "")
        external_id = str(entry.get("externalEntityId") or "")
        evidence = entry.get("evidence")
        expected_printing = entry.get("expectedPrinting")
        if (
            variant_id <= 0
            or variant_id in validated
            or source_code not in {"pricecharting", "snkrdunk"}
            or not external_id.isdigit()
            or not isinstance(evidence, dict)
            or not isinstance(expected_printing, dict)
        ):
            raise RuntimeError(f"026 canonical market-source repair policy is invalid: {variant_id}")
        if source_code == "pricecharting":
            if evidence.get("type") != "pricecharting_product_html":
                raise RuntimeError(f"026 PC repair evidence type is invalid: {variant_id}")
            evidence_path = _workspace_evidence_path(str(evidence.get("path") or ""))
            if _file_sha256(evidence_path) != str(evidence.get("sha256") or ""):
                raise RuntimeError(f"026 PC repair evidence hash drifted: {variant_id}")
            from pc_psa10_price_derivation import validate_pc_psa10

            exact_price, reason = validate_pc_psa10(
                {
                    "variant_id": variant_id,
                    "pc_product_id": external_id,
                    "pc_url": str(entry.get("providerUrl") or ""),
                    "html_path": str(evidence.get("path") or ""),
                }
            )
            if (
                exact_price is None
                or reason != "accepted"
                or str(exact_price.get("external_entity_id") or "") != external_id
                or str(exact_price.get("field") or "")
                != str(evidence.get("explicitField") or "")
                or str(exact_price.get("observed_date") or "")
                != str(evidence.get("observedDate") or "")
                or Decimal(str(exact_price.get("price_usd") or "0"))
                != Decimal(str(evidence.get("priceUsd") or "0"))
                or str(exact_price.get("artifact_sha256") or "")
                != str(evidence.get("sha256") or "")
            ):
                raise RuntimeError(f"026 PC repair exact evidence failed: {variant_id}:{reason}")
            validated[variant_id] = {
                "artifactSha256": str(evidence["sha256"]),
                "observedDate": str(evidence["observedDate"]),
                "priceUsd": str(evidence["priceUsd"]),
            }
        else:
            if evidence.get("type") != "snkrdunk_psa10_local_evidence":
                raise RuntimeError(f"026 SNK repair evidence type is invalid: {variant_id}")
            asset_path = _workspace_evidence_path(str(evidence.get("assetInfoPath") or ""))
            trades_path = _workspace_evidence_path(str(evidence.get("psa10TradesPath") or ""))
            kline_path = _workspace_evidence_path(str(evidence.get("klinePath") or ""))
            if (
                _file_sha256(asset_path) != str(evidence.get("assetInfoSha256") or "")
                or _file_sha256(trades_path) != str(evidence.get("psa10TradesSha256") or "")
                or _file_sha256(kline_path) != str(evidence.get("klineFileSha256") or "")
            ):
                raise RuntimeError(f"026 SNK repair evidence hash drifted: {variant_id}")
            asset = _read_json(asset_path)
            expected = expected_printing
            if (
                str((asset.get("assetQueryId") or {}).get("id") or "") != external_id
                or str(asset.get("cardId") or "") != str(expected.get("collectorNumber") or "")
                or str(asset.get("language") or "")
                != str((entry.get("providerClaims") or {}).get("language") or "")
                or "SEC-P" not in str(asset.get("cardName") or "")
            ):
                raise RuntimeError(f"026 SNK repair identity evidence failed: {variant_id}")
            kline_row = None
            for raw_line in kline_path.read_text(encoding="utf-8-sig").splitlines():
                if external_id not in raw_line:
                    continue
                candidate = json.loads(raw_line)
                if str(candidate.get("item_id") or "") == external_id:
                    kline_row = candidate
                    break
            points = (kline_row or {}).get("kline")
            latest = points[-1] if isinstance(points, list) and points else {}
            if (
                not isinstance(kline_row, dict)
                or kline_row.get("condition_filter") != "trading_card_single_psa10"
                or "SEC-P" not in str(kline_row.get("name") or "")
                or str(latest.get("date") or "") != str(evidence.get("latestKlineDate") or "")
                or Decimal(str(latest.get("price_jpy") or "0"))
                != Decimal(str(evidence.get("latestKlineJpy") or "0"))
            ):
                raise RuntimeError(f"026 SNK repair price evidence failed: {variant_id}")
            validated[variant_id] = {
                "assetInfoSha256": str(evidence["assetInfoSha256"]),
                "psa10TradesSha256": str(evidence["psa10TradesSha256"]),
                "klineFileSha256": str(evidence["klineFileSha256"]),
                "latestKlineDate": str(evidence["latestKlineDate"]),
                "latestKlineJpy": str(evidence["latestKlineJpy"]),
            }
    return manifest, manifest_sha256, validated


def _sync_026_canonical_pc_map(manifest: dict, manifest_sha256: str) -> dict:
    if not CANONICAL_PC_MAP.is_file():
        raise RuntimeError(f"canonical PC map is missing: {CANONICAL_PC_MAP}")
    existing = [
        json.loads(line)
        for line in CANONICAL_PC_MAP.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]
    repair_entries = [
        entry for entry in manifest["entries"] if entry["sourceCode"] == "pricecharting"
    ]
    target_variants = {int(entry["targetVariantId"]) for entry in repair_entries}
    external_ids = {str(entry["externalEntityId"]) for entry in repair_entries}
    retained = [
        row
        for row in existing
        if int(row.get("variant_id") or 0) not in target_variants
        and str(row.get("pc_product_id") or "") not in external_ids
    ]
    replacement = []
    for entry in repair_entries:
        mapped = entry["canonicalMap"]
        replacement.append(
            {
                "card_name": str(mapped["cardName"]),
                "collector_number": str(mapped["collectorNumber"]),
                "confidence": "high",
                "ebay_items_psa10": [],
                "ebay_uuid_or_item": None,
                "htmlPath": str(mapped["htmlPath"]),
                "html_path": str(mapped["htmlPath"]),
                "mapped_at": str(manifest["verifiedOn"]) + "T00:00:00Z",
                "notes": str(entry["reason"]),
                "pc_product_id": int(entry["externalEntityId"]),
                "pc_url": str(entry["providerUrl"]),
                "ready_for_c12": True,
                "set_name": str(mapped["setName"]),
                "source": str(manifest["contract"]),
                "status": "mapped",
                "variant_id": int(entry["targetVariantId"]),
            }
        )
    rows = sorted(
        retained + replacement,
        key=lambda row: (int(row.get("variant_id") or 0), str(row.get("pc_product_id") or "")),
    )
    encoded = "".join(
        json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    ).encode("utf-8")
    before_sha256 = _file_sha256(CANONICAL_PC_MAP)
    after_sha256 = hashlib.sha256(encoded).hexdigest()
    changed = before_sha256 != after_sha256
    if changed:
        temporary = CANONICAL_PC_MAP.with_name(
            f".{CANONICAL_PC_MAP.name}.{manifest_sha256[:12]}.next"
        )
        temporary.write_bytes(encoded)
        temporary.replace(CANONICAL_PC_MAP)
    return {
        "path": str(CANONICAL_PC_MAP),
        "manifestSha256": manifest_sha256,
        "beforeSha256": before_sha256,
        "afterSha256": after_sha256,
        "changed": changed,
        "rows": len(rows),
        "repairedVariants": sorted(target_variants),
    }


def sync_026_market_source_identity_repairs(cur) -> dict:
    manifest, manifest_sha256, validated = _validated_market_source_repair_manifest()
    rejected: list[dict] = []
    for entry in manifest["rejects"]:
        variant_id = int(entry["variantId"])
        source_code = str(entry["sourceCode"])
        external_id = str(entry["externalEntityId"])
        cur.execute(
            """
            SELECT variant_id,match_status,evidence_sha256,bind_evidence_json
            FROM catalog_source_identity
            WHERE source_code=%s AND external_entity_id=%s FOR UPDATE
            """,
            (source_code, external_id),
        )
        current = dict(cur.fetchone() or {})
        if not current or int(current.get("variant_id") or 0) != variant_id:
            raise RuntimeError(
                f"026 rejected source owner drifted: {source_code}:{external_id}"
            )
        existing_claim = current.get("bind_evidence_json")
        if isinstance(existing_claim, str):
            existing_claim = json.loads(existing_claim)
        if (
            str(current.get("match_status") or "").lower() == "rejected"
            and isinstance(existing_claim, dict)
            and existing_claim.get("contract") == manifest["contract"]
            and existing_claim.get("manifestSha256") == manifest_sha256
            and existing_claim.get("action") == "reject-wrong-printing-source"
            and int(existing_claim.get("variantId") or 0) == variant_id
            and existing_claim.get("sourceCode") == source_code
            and existing_claim.get("externalEntityId") == external_id
            and existing_claim.get("reasonCode") == str(entry["reasonCode"])
        ):
            claim = existing_claim
        else:
            claim = {
                "contract": manifest["contract"],
                "manifestSha256": manifest_sha256,
                "action": "reject-wrong-printing-source",
                "variantId": variant_id,
                "sourceCode": source_code,
                "externalEntityId": external_id,
                "reasonCode": str(entry["reasonCode"]),
                "reason": str(entry["reason"]),
                "previousEvidenceSha256": str(current.get("evidence_sha256") or ""),
            }
        evidence_sha256 = canonical_sha256(claim)
        cur.execute(
            """
            UPDATE catalog_source_identity
            SET match_status='rejected',evidence_sha256=%s,bind_evidence_json=%s
            WHERE source_code=%s AND external_entity_id=%s AND variant_id=%s
            """,
            (
                evidence_sha256,
                json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                source_code,
                external_id,
                variant_id,
            ),
        )
        cur.execute(
            """
            UPDATE operator_binding_freeze
            SET acceptance_status='rejected',evidence_sha256=%s,note=%s
            WHERE variant_id=%s AND freeze_kind='source' AND source_code=%s
              AND external_entity_id=%s AND acceptance_status='accepted'
            """,
            (evidence_sha256, str(entry["reason"]), variant_id, source_code, external_id),
        )
        rejected.append(
            {
                "variantId": variant_id,
                "sourceCode": source_code,
                "externalEntityId": external_id,
                "evidenceSha256": evidence_sha256,
            }
        )

    repaired: list[dict] = []
    for entry in manifest["entries"]:
        variant_id = int(entry["targetVariantId"])
        source_code = str(entry["sourceCode"])
        external_id = str(entry["externalEntityId"])
        expected = entry["expectedPrinting"]
        cur.execute(
            """
            SELECT v.id,v.tcg_code,p.card_language,p.collector_number,p.set_code,
                   p.edition_code,p.printing_code,p.parallel_code,p.finish_code
            FROM catalog_variant v
            INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
            WHERE v.id=%s FOR UPDATE
            """,
            (variant_id,),
        )
        printing = dict(cur.fetchone() or {})
        actual_printing = {
            "tcgCode": str(printing.get("tcg_code") or ""),
            "cardLanguage": str(printing.get("card_language") or ""),
            "collectorNumber": str(printing.get("collector_number") or ""),
            "setCode": str(printing.get("set_code") or ""),
            "printingCode": str(printing.get("printing_code") or ""),
        }
        if actual_printing != expected:
            raise RuntimeError(f"026 market-source target printing drifted: {variant_id}")
        cur.execute(
            """
            SELECT variant_id,match_status,evidence_sha256,bind_evidence_json
            FROM catalog_source_identity
            WHERE source_code=%s AND external_entity_id=%s FOR UPDATE
            """,
            (source_code, external_id),
        )
        owner = dict(cur.fetchone() or {})
        previous_owner = int(owner["variant_id"]) if owner else None
        expected_owner = entry.get("expectedPreviousOwnerVariantId")
        allowed_owners = {variant_id, None}
        if expected_owner is not None:
            allowed_owners.add(int(expected_owner))
        if previous_owner not in allowed_owners:
            raise RuntimeError(
                f"026 market-source previous owner drifted: {source_code}:{external_id}:{previous_owner}"
            )
        cur.execute(
            """
            SELECT external_entity_id FROM catalog_source_identity
            WHERE variant_id=%s AND source_code=%s AND external_entity_id<>%s
              AND LOWER(match_status)='exact'
            ORDER BY external_entity_id
            """,
            (variant_id, source_code, external_id),
        )
        conflicts = [str(row["external_entity_id"]) for row in cur.fetchall()]
        if conflicts:
            raise RuntimeError(
                f"026 market-source target still has another exact ID: {variant_id}:{source_code}:{conflicts}"
            )
        existing_claim = owner.get("bind_evidence_json")
        if isinstance(existing_claim, str):
            existing_claim = json.loads(existing_claim)
        if (
            previous_owner == variant_id
            and str(owner.get("match_status") or "").lower() == "exact"
            and isinstance(existing_claim, dict)
            and existing_claim.get("contract") == manifest["contract"]
            and existing_claim.get("manifestSha256") == manifest_sha256
            and existing_claim.get("action") == "assign-exact-source-owner"
            and int(existing_claim.get("targetVariantId") or 0) == variant_id
            and existing_claim.get("sourceCode") == source_code
            and existing_claim.get("externalEntityId") == external_id
            and existing_claim.get("providerUrl") == str(entry["providerUrl"])
            and existing_claim.get("expectedPrinting") == expected
            and existing_claim.get("localEvidence") == validated[variant_id]
        ):
            claim = existing_claim
        else:
            claim = {
                "contract": manifest["contract"],
                "manifestSha256": manifest_sha256,
                "action": "assign-exact-source-owner",
                "targetVariantId": variant_id,
                "previousOwnerVariantId": previous_owner,
                "sourceCode": source_code,
                "externalEntityId": external_id,
                "providerUrl": str(entry["providerUrl"]),
                "providerProductNumber": str(entry["providerProductNumber"]),
                "providerClaims": entry["providerClaims"],
                "expectedPrinting": expected,
                "localEvidence": validated[variant_id],
                "previousEvidenceSha256": str(owner.get("evidence_sha256") or ""),
                "reason": str(entry["reason"]),
            }
        evidence_sha256 = canonical_sha256(claim)
        bind_json = json.dumps(
            claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        if owner:
            cur.execute(
                """
                UPDATE catalog_source_identity
                SET variant_id=%s,match_status='exact',evidence_sha256=%s,
                    source_product_number=%s,bound_set_code=%s,bound_printing_code=%s,
                    bound_tcg_code=%s,bound_card_language=%s,bound_collector_number=%s,
                    bound_edition_code=%s,bound_parallel_code=%s,bound_finish_code=%s,
                    bind_evidence_json=%s
                WHERE source_code=%s AND external_entity_id=%s
                """,
                (
                    variant_id,
                    evidence_sha256,
                    str(entry["providerProductNumber"]),
                    actual_printing["setCode"],
                    actual_printing["printingCode"],
                    actual_printing["tcgCode"],
                    actual_printing["cardLanguage"],
                    actual_printing["collectorNumber"],
                    str(printing.get("edition_code") or ""),
                    str(printing.get("parallel_code") or ""),
                    str(printing.get("finish_code") or ""),
                    bind_json,
                    source_code,
                    external_id,
                ),
            )
        else:
            cur.execute(
                """
                INSERT INTO catalog_source_identity
                  (variant_id,source_code,external_entity_id,match_status,evidence_sha256,
                   source_product_number,bound_set_code,bound_printing_code,bind_evidence_json,
                   bound_tcg_code,bound_card_language,bound_collector_number,
                   bound_edition_code,bound_parallel_code,bound_finish_code)
                VALUES (%s,%s,%s,'exact',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    variant_id,
                    source_code,
                    external_id,
                    evidence_sha256,
                    str(entry["providerProductNumber"]),
                    actual_printing["setCode"],
                    actual_printing["printingCode"],
                    bind_json,
                    actual_printing["tcgCode"],
                    actual_printing["cardLanguage"],
                    actual_printing["collectorNumber"],
                    str(printing.get("edition_code") or ""),
                    str(printing.get("parallel_code") or ""),
                    str(printing.get("finish_code") or ""),
                ),
            )
        if previous_owner is not None and previous_owner != variant_id:
            cur.execute(
                """
                UPDATE operator_binding_freeze
                SET acceptance_status='rejected',evidence_sha256=%s,
                    note='026 external entity moved to its exact canonical printing'
                WHERE variant_id=%s AND freeze_kind='source' AND source_code=%s
                  AND external_entity_id=%s AND acceptance_status='accepted'
                """,
                (evidence_sha256, previous_owner, source_code, external_id),
            )
        repaired.append(
            {
                "variantId": variant_id,
                "sourceCode": source_code,
                "externalEntityId": external_id,
                "previousOwnerVariantId": previous_owner,
                "evidenceSha256": evidence_sha256,
            }
        )
    active_previous_owners = (176,)
    cur.execute(
        """
        SELECT DISTINCT p.variant_id
        FROM market_price_observation p
        INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
        INNER JOIN operator_strict_source_identity si
          ON si.variant_id=p.variant_id AND si.source_code='snkrdunk'
         AND si.external_entity_id=p.source_external_entity_id
        INNER JOIN market_source_observation so
          ON so.id=p.source_observation_id AND so.source_code=p.source_code
         AND so.external_entity_id=p.source_external_entity_id
         AND so.payload_sha256=p.payload_sha256 AND so.observed_date=p.observed_date
        WHERE p.variant_id=%s
          AND p.source_code IN ('snkrdunk','snk_psa10','snk')
          AND p.metric_status='ready' AND p.price_usd>0
          AND so.observation_kind='psa10_reference_price'
        ORDER BY p.variant_id
        """,
        active_previous_owners,
    )
    donor_coverage = [int(row["variant_id"]) for row in cur.fetchall()]
    if donor_coverage != list(active_previous_owners):
        raise RuntimeError(
            f"026 source transfer would uncover an active previous owner: {donor_coverage}"
        )
    return {
        "contract": manifest["contract"],
        "manifest": str(CANONICAL_MARKET_SOURCE_IDENTITY_REPAIRS),
        "manifestSha256": manifest_sha256,
        "repaired": repaired,
        "rejected": rejected,
        "validatedEvidence": validated,
        "activePreviousOwnerSnkCoverage": donor_coverage,
    }


def _value_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_json_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _iso_z(value: datetime) -> str:
    return value.replace(tzinfo=timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _clean_text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _locked_sha256(value, label: str) -> str:
    sha256 = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise RuntimeError(f"{label} is not a SHA-256 digest")
    return sha256


def _locked_artifact(lock: dict, key: str, expected_path: Path) -> str:
    artifact = lock.get(key) if isinstance(lock.get(key), dict) else {}
    expected_relative = expected_path.relative_to(ROOT).as_posix()
    if artifact.get("path") != expected_relative:
        raise RuntimeError(f"one-time dependency path mismatch: {key}")
    locked_sha256 = _locked_sha256(artifact.get("sha256"), f"one-time dependency {key}")
    if not expected_path.is_file() or _file_sha256(expected_path) != locked_sha256:
        raise RuntimeError(f"one-time dependency hash mismatch: {key}")
    return locked_sha256


def _baseline_image_lineage(baseline: dict, baseline_sha256: str) -> dict:
    """Reopen the immutable 025 generation and its hash-locked image lineage."""

    generation = baseline.get("generation") if isinstance(baseline.get("generation"), dict) else {}
    if generation.get("id") != BASELINE_GENERATION_ID:
        raise RuntimeError("baseline image generation id mismatch")
    if generation.get("productionEligible") is not True or list(generation.get("blockers") or []):
        raise RuntimeError("baseline image generation is not an eligible blocker-free product generation")

    dependency_lock = _read_json(ONE_TIME_DEPENDENCY_LOCK)
    if (
        int(dependency_lock.get("schemaVersion") or 0) != 1
        or dependency_lock.get("kind") != "cardz-onetime-025-dependency-lock"
        or dependency_lock.get("immutable") is not True
        or dependency_lock.get("generationId") != BASELINE_GENERATION_ID
    ):
        raise RuntimeError("one-time 025 dependency lock contract mismatch")
    locked_snapshot_sha256 = _locked_artifact(
        dependency_lock, "baselineSnapshot", BASELINE_SNAPSHOT
    )
    locked_release_sha256 = _locked_artifact(
        dependency_lock, "baselineReleaseLock", BASELINE_RELEASE_LOCK
    )
    locked_manifest_sha256 = _locked_artifact(
        dependency_lock, "baselineAssetManifest", BASELINE_ASSET_MANIFEST
    )
    locked_lineage_sha256 = _locked_artifact(
        dependency_lock, "baselineImageLineage", BASELINE_IMAGE_LINEAGE
    )
    locked_requirements_sha256 = _locked_artifact(
        dependency_lock, "pythonRequirements", PYTHON_REQUIREMENTS
    )
    if locked_snapshot_sha256 != baseline_sha256:
        raise RuntimeError("one-time dependency baseline snapshot mismatch")

    lineage = _read_json(BASELINE_IMAGE_LINEAGE)
    if (
        int(lineage.get("schemaVersion") or 0) != 1
        or lineage.get("contract") != "locked-baseline-image-lineage-recovery-v1"
        or lineage.get("generationId") != BASELINE_GENERATION_ID
        or lineage.get("baselineSnapshotSha256") != baseline_sha256
    ):
        raise RuntimeError("baseline image lineage contract mismatch")
    original_receipt_sha256 = _locked_sha256(
        lineage.get("originalMaterializationReceiptSha256"),
        "baseline original materialization receipt",
    )
    mapping = lineage.get("mapping")
    if not isinstance(mapping, dict):
        raise RuntimeError("baseline image lineage mapping is invalid")
    normalized_mapping: dict[str, str] = {}
    for source_sha256, public_sha256 in mapping.items():
        source_sha256 = str(source_sha256).strip().lower()
        public_sha256 = str(public_sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256) or not re.fullmatch(
            r"[0-9a-f]{64}", public_sha256
        ):
            raise RuntimeError("baseline image lineage contains an invalid hash mapping")
        normalized_mapping[source_sha256] = public_sha256
    mapping_sha256 = _canonical_json_sha256(normalized_mapping)
    historical_overrides = lineage.get("historicalOverrideCards")
    if (
        lineage.get("mappingSha256") != mapping_sha256
        or int(lineage.get("mappingEntries") or 0) != len(normalized_mapping)
        or len(normalized_mapping) != 623
        or int(lineage.get("mappedCards") or 0) != 643
        or int(lineage.get("directCards") or 0) != 116
        or not isinstance(historical_overrides, list)
        or len(historical_overrides) != 3
        or len(set(historical_overrides)) != 3
        or any(not re.fullmatch(r"cmc_[0-9a-f]{24}", str(value)) for value in historical_overrides)
        or int(lineage.get("mappedCards") or 0)
        + int(lineage.get("directCards") or 0)
        + len(historical_overrides)
        != 762
    ):
        raise RuntimeError("baseline image lineage digest or count mismatch")
    if (
        generation.get("assetMaterializationReceiptSha256") != original_receipt_sha256
        or generation.get("assetMappingSha256") != mapping_sha256
    ):
        raise RuntimeError("baseline snapshot materialization lineage mismatch")

    locked_lineage = (
        dependency_lock.get("baselineImageLineage")
        if isinstance(dependency_lock.get("baselineImageLineage"), dict)
        else {}
    )
    if (
        locked_lineage.get("mappingSha256") != mapping_sha256
        or locked_lineage.get("originalMaterializationReceiptSha256")
        != original_receipt_sha256
    ):
        raise RuntimeError("one-time dependency image lineage mismatch")

    release_lock = _read_json(BASELINE_RELEASE_LOCK)
    if (
        int(release_lock.get("schemaVersion") or 0) != 1
        or release_lock.get("kind") != "cardz-immutable-product-generation"
        or release_lock.get("immutable") is not True
    ):
        raise RuntimeError("baseline release lock contract mismatch")
    locked_generation = (
        release_lock.get("generation") if isinstance(release_lock.get("generation"), dict) else {}
    )
    if (
        locked_generation.get("id") != BASELINE_GENERATION_ID
        or int(locked_generation.get("cards") or 0) != 762
        or locked_generation.get("contentSha256") != generation.get("contentSha256")
    ):
        raise RuntimeError("baseline release lock generation mismatch")
    locked_snapshot = (
        release_lock.get("snapshot") if isinstance(release_lock.get("snapshot"), dict) else {}
    )
    if (
        locked_snapshot.get("path") != BASELINE_SNAPSHOT.relative_to(ROOT).as_posix()
        or locked_snapshot.get("sha256") != baseline_sha256
    ):
        raise RuntimeError("baseline release lock snapshot mismatch")
    asset_manifest = _read_json(BASELINE_ASSET_MANIFEST)
    manifest_entries = asset_manifest.get("entries")
    if (
        int(asset_manifest.get("schemaVersion") or 0) != 1
        or asset_manifest.get("kind") != "cardz-content-addressed-asset-manifest"
        or asset_manifest.get("appendOnly") is not True
        or not isinstance(manifest_entries, list)
        or int(asset_manifest.get("fileCount") or 0) != len(manifest_entries)
        or len(manifest_entries) != 2217
    ):
        raise RuntimeError("baseline release asset manifest contract mismatch")
    locked_assets = release_lock.get("assets") if isinstance(release_lock.get("assets"), dict) else {}
    if (
        locked_assets.get("manifestPath") != BASELINE_ASSET_MANIFEST.relative_to(ROOT).as_posix()
        or locked_assets.get("manifestSha256") != locked_manifest_sha256
        or int(locked_assets.get("fileCount") or 0) != len(manifest_entries)
        or locked_assets.get("appendOnly") is not True
    ):
        raise RuntimeError("baseline release asset manifest mismatch")

    return {
        "generationId": BASELINE_GENERATION_ID,
        "originalMaterializationReceiptSha256": original_receipt_sha256,
        "mappingSha256": mapping_sha256,
        "mapping": normalized_mapping,
        "releaseLockSha256": locked_release_sha256,
        "assetManifestSha256": locked_manifest_sha256,
        "baselineImageLineageSha256": locked_lineage_sha256,
        "oneTimeDependencyLockSha256": _file_sha256(ONE_TIME_DEPENDENCY_LOCK),
        "pythonRequirementsSha256": locked_requirements_sha256,
    }


def _canonical_image_rebases() -> dict:
    """Validate the three legacy accepted assets whose filename held a source hash."""

    receipt = _read_json(CANONICAL_IMAGE_REBASE)
    if (
        int(receipt.get("schemaVersion") or 0) != 1
        or receipt.get("contract") != "canonical-accepted-image-byte-rebase-v1"
    ):
        raise RuntimeError("canonical image rebase contract mismatch")
    entries = receipt.get("entries")
    if not isinstance(entries, dict) or len(entries) != 3:
        raise RuntimeError("canonical image rebase entries mismatch")
    for opaque_id, entry in entries.items():
        if not isinstance(entry, dict):
            raise RuntimeError(f"canonical image rebase entry invalid: {opaque_id}")
        public_sha256 = str(entry.get("publicContentSha256") or "").strip().lower()
        source_sha256 = str(entry.get("acceptedSourceSha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", public_sha256) or not re.fullmatch(
            r"[0-9a-f]{64}", source_sha256
        ):
            raise RuntimeError(f"canonical image rebase hash invalid: {opaque_id}")
        base = PUBLIC_ASSETS / f"{public_sha256}.webp"
        if not base.is_file() or _file_sha256(base) != public_sha256:
            raise RuntimeError(f"canonical rebased image is missing or corrupt: {opaque_id}")
        derivatives = entry.get("derivatives") if isinstance(entry.get("derivatives"), dict) else {}
        for size in ("200", "600"):
            derivative_sha256 = str(derivatives.get(size) or "").strip().lower()
            derivative = PUBLIC_ASSETS / f"{public_sha256}_{size}.webp"
            if (
                not re.fullmatch(r"[0-9a-f]{64}", derivative_sha256)
                or not derivative.is_file()
                or _file_sha256(derivative) != derivative_sha256
            ):
                raise RuntimeError(f"canonical rebased derivative is missing or corrupt: {opaque_id}/{size}")
    return {
        "contract": receipt["contract"],
        "receiptSha256": _file_sha256(CANONICAL_IMAGE_REBASE),
        "entries": entries,
    }


def _localized(value) -> dict[str, str | None]:
    if not isinstance(value, dict):
        return {locale: None for locale in LOCALES}
    return {locale: _clean_text(value.get(locale)) for locale in LOCALES}


def sync_repo_editorial(cur) -> dict:
    """Move existing editorial values into the canonical locale tables once."""

    sources = {
        "names": (EDITORIAL_NAMES, _read_json(EDITORIAL_NAMES)),
        "sets": (EDITORIAL_SETS, _read_json(EDITORIAL_SETS)),
        "stories": (EDITORIAL_STORIES, _read_json(EDITORIAL_STORIES)),
        "baseline": (BASELINE_SNAPSHOT, _read_json(BASELINE_SNAPSHOT)),
    }
    name_table = sources["names"][1].get("entries") or {}
    set_table = sources["sets"][1].get("entries") or {}
    if not isinstance(name_table, dict) or not isinstance(set_table, dict):
        raise RuntimeError("editorial name/set tables are invalid")
    story_table = {
        str(entry.get("id")): entry
        for entry in (sources["stories"][1].get("entries") or [])
        if isinstance(entry, dict) and entry.get("id")
    }
    baseline_cards = {
        str(card.get("id")): card
        for card in (
            list(sources["baseline"][1].get("top100") or [])
            + list(sources["baseline"][1].get("watchlist") or [])
        )
        if isinstance(card, dict) and card.get("id")
    }
    source_observed_at = {
        name: _source_observed_at(document)
        for name, (_, document) in sources.items()
    }

    cur.execute(
        """
        SELECT
          v.id AS variant_id,
          v.opaque_id,
          v.canonical_name,
          v.updated_at AS variant_updated_at,
          p.set_name,
          p.updated_at AS printing_updated_at
        FROM market_universe_member m
        INNER JOIN market_universe_lock u
          ON u.id=m.universe_lock_id AND u.is_current=1
        INNER JOIN catalog_variant v ON v.id=m.variant_id
        INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
        ORDER BY v.id
        """
    )
    variants = [dict(row) for row in cur.fetchall()]
    variant_ids = [int(row["variant_id"]) for row in variants]
    if not variant_ids:
        raise RuntimeError("active universe has no variants for editorial import")
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT variant_id, locale_code, localized_name, localized_set_name,
               market_story, provenance_source_code, content_sha256,
               observed_at, provenance_json
        FROM catalog_variant_locale
        WHERE variant_id IN ({placeholders})
          AND locale_code IN ('en','zhTW','zhCN','ja','ko')
        """,
        tuple(variant_ids),
    )
    existing = {
        (int(row["variant_id"]), str(row["locale_code"])): dict(row)
        for row in cur.fetchall()
    }

    rows = []
    story_pointers = []
    fields_written = {"name": 0, "set": 0, "story": 0}
    source_hashes = {name: _file_sha256(path) for name, (path, _) in sources.items()}
    for variant in variants:
        variant_id = int(variant["variant_id"])
        opaque_id = str(variant["opaque_id"])
        english_name = _clean_text(variant.get("canonical_name"))
        english_set = _clean_text(variant.get("set_name"))
        translated_names = name_table.get(english_name or "") or {}
        translated_sets = set_table.get(english_set or "") or {}
        baseline = baseline_cards.get(opaque_id) or {}
        baseline_names = _localized(baseline.get("names"))
        baseline_sets = _localized(baseline.get("sets"))
        baseline_stories = _localized(baseline.get("stories"))
        story_entry = story_table.get(opaque_id) or {}
        accepted_story = (
            story_entry.get("stories")
            if str(story_entry.get("status") or "") == "ready"
            else {}
        )
        if not isinstance(accepted_story, dict):
            accepted_story = {}

        for locale in LOCALES:
            old = existing.get((variant_id, locale)) or {}
            name = _clean_text(old.get("localized_name"))
            set_name = _clean_text(old.get("localized_set_name"))
            story = _clean_text(old.get("market_story"))
            old_provenance = old.get("provenance_json")
            if isinstance(old_provenance, str):
                try:
                    old_provenance = json.loads(old_provenance)
                except ValueError:
                    old_provenance = None
            field_sources = dict(
                (old_provenance or {}).get("fields") or {}
                if isinstance(old_provenance, dict)
                else {}
            )
            evidence_times: list[datetime] = []
            if isinstance(old.get("observed_at"), datetime):
                evidence_times.append(old["observed_at"])

            candidate_name = None
            candidate_name_source = None
            if locale == "en":
                candidate_name = english_name
                candidate_name_source = {
                    "sourceCode": "catalog_variant",
                    "valueSha256": _value_sha256(candidate_name) if candidate_name else None,
                }
            elif isinstance(translated_names, dict):
                candidate_name = _clean_text(translated_names.get(locale))
                if candidate_name:
                    candidate_name_source = {
                        "sourceCode": "repo_editorial_names_v1",
                        "path": EDITORIAL_NAMES.relative_to(ROOT).as_posix(),
                        "fileSha256": source_hashes["names"],
                        "valueSha256": _value_sha256(candidate_name),
                    }
            if not candidate_name:
                candidate_name = baseline_names.get(locale)
                if candidate_name:
                    candidate_name_source = {
                        "sourceCode": "locked_baseline_snapshot_v1",
                        "path": BASELINE_SNAPSHOT.relative_to(ROOT).as_posix(),
                        "fileSha256": source_hashes["baseline"],
                        "valueSha256": _value_sha256(candidate_name),
                    }
            if candidate_name:
                if candidate_name != name:
                    fields_written["name"] += 1
                name = candidate_name
                field_sources["localizedName"] = candidate_name_source
                evidence_times.append(
                    variant["variant_updated_at"]
                    if locale == "en"
                    else source_observed_at["names"]
                    if candidate_name_source.get("sourceCode") == "repo_editorial_names_v1"
                    else source_observed_at["baseline"]
                    if candidate_name_source.get("sourceCode") == "locked_baseline_snapshot_v1"
                    else variant["variant_updated_at"]
                )

            candidate_set = None
            candidate_set_source = None
            if locale == "en":
                candidate_set = english_set
                candidate_set_source = {
                    "sourceCode": "catalog_printing_identity",
                    "valueSha256": _value_sha256(candidate_set) if candidate_set else None,
                }
            elif isinstance(translated_sets, dict):
                candidate_set = _clean_text(translated_sets.get(locale))
                if candidate_set:
                    candidate_set_source = {
                        "sourceCode": "repo_editorial_sets_v1",
                        "path": EDITORIAL_SETS.relative_to(ROOT).as_posix(),
                        "fileSha256": source_hashes["sets"],
                        "valueSha256": _value_sha256(candidate_set),
                    }
            if not candidate_set:
                candidate_set = baseline_sets.get(locale)
                if candidate_set:
                    candidate_set_source = {
                        "sourceCode": "locked_baseline_snapshot_v1",
                        "path": BASELINE_SNAPSHOT.relative_to(ROOT).as_posix(),
                        "fileSha256": source_hashes["baseline"],
                        "valueSha256": _value_sha256(candidate_set),
                    }
            if candidate_set:
                if candidate_set != set_name:
                    fields_written["set"] += 1
                set_name = candidate_set
                field_sources["localizedSetName"] = candidate_set_source
                evidence_times.append(
                    variant["printing_updated_at"]
                    if locale == "en"
                    else source_observed_at["sets"]
                    if candidate_set_source.get("sourceCode") == "repo_editorial_sets_v1"
                    else source_observed_at["baseline"]
                    if candidate_set_source.get("sourceCode") == "locked_baseline_snapshot_v1"
                    else variant["printing_updated_at"]
                )

            candidate_story = _clean_text(accepted_story.get(locale))
            candidate_story_source = None
            if candidate_story:
                candidate_story_source = {
                    "sourceCode": "repo_editorial_stories_v1",
                    "path": EDITORIAL_STORIES.relative_to(ROOT).as_posix(),
                    "fileSha256": source_hashes["stories"],
                    "valueSha256": _value_sha256(candidate_story),
                }
            if not candidate_story and not story:
                candidate_story = baseline_stories.get(locale)
                if candidate_story:
                    candidate_story_source = {
                        "sourceCode": "locked_baseline_snapshot_v1",
                        "path": BASELINE_SNAPSHOT.relative_to(ROOT).as_posix(),
                        "fileSha256": source_hashes["baseline"],
                        "valueSha256": _value_sha256(candidate_story),
                    }
            if candidate_story:
                if candidate_story != story:
                    fields_written["story"] += 1
                story = candidate_story
                field_sources["marketStory"] = candidate_story_source
                evidence_times.append(
                    source_observed_at["stories"]
                    if candidate_story_source.get("sourceCode") == "repo_editorial_stories_v1"
                    else source_observed_at["baseline"]
                )
            if candidate_story:
                story_pointers.append(
                    (
                        variant_id,
                        locale,
                        candidate_story_source["path"],
                        candidate_story_source["fileSha256"],
                        source_observed_at["stories"]
                        if candidate_story_source.get("sourceCode") == "repo_editorial_stories_v1"
                        else source_observed_at["baseline"],
                    )
                )

            content_hash = hashlib.sha256(
                json.dumps(
                    {
                        "variantId": variant_id,
                        "locale": locale,
                        "name": name,
                        "set": set_name,
                        "story": story,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            row_observed = max(evidence_times) if evidence_times else None
            if not field_sources:
                provenance_source_code = str(old.get("provenance_source_code") or "")
                provenance_json = old_provenance
                content_hash = str(old.get("content_sha256") or content_hash)
            else:
                provenance_source_code = "canonical_locale_merge_v1"
                provenance_json = {
                    "contract": "canonical-locale-field-lineage-v1",
                    "fields": field_sources,
                    "observedAt": _iso_z(row_observed) if row_observed else None,
                }
                if old_provenance and not (old_provenance.get("fields") if isinstance(old_provenance, dict) else None):
                    provenance_json["priorRow"] = old_provenance
            rows.append(
                (
                    variant_id,
                    locale,
                    name,
                    set_name,
                    story,
                    provenance_source_code,
                    content_hash,
                    row_observed,
                    json.dumps(provenance_json, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    if provenance_json is not None
                    else None,
                )
            )

    cur.executemany(
        """
        INSERT INTO catalog_variant_locale
          (variant_id, locale_code, localized_name, localized_set_name,
           market_story, provenance_source_code, content_sha256, observed_at,
           provenance_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          localized_name=VALUES(localized_name),
          localized_set_name=VALUES(localized_set_name),
          market_story=VALUES(market_story),
          provenance_source_code=VALUES(provenance_source_code),
          content_sha256=VALUES(content_sha256),
          observed_at=VALUES(observed_at),
          provenance_json=VALUES(provenance_json)
        """,
        rows,
    )
    if story_pointers:
        cur.executemany(
            """
            INSERT IGNORE INTO catalog_story_pointer
              (variant_id, locale_code, source_path, source_version_sha256, observed_at)
            VALUES (%s,%s,%s,%s,%s)
            """,
            story_pointers,
        )
    return {
        "variants": len(variants),
        "localeRows": len(rows),
        "storyPointers": len(story_pointers),
        "fieldsWritten": fields_written,
        "sourceSha256": source_hashes,
    }


def _first_existing(paths: tuple[Path, ...], label: str) -> Path:
    for path in paths:
        if path.is_file():
            return path
    raise RuntimeError(f"{label} is missing from every approved local path")


def _evidence_datetime(value, fallback_path: Path) -> datetime:
    raw = str(value or "").strip()
    for fmt in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return datetime.fromtimestamp(fallback_path.stat().st_mtime, tz=timezone.utc).replace(tzinfo=None)


def _gemrate_page_official_name(gemrate_id: str) -> tuple[str, str, datetime] | None:
    """Return only the literal description from the unique raw PSA row."""

    for cards_root in GEMRATE_CARD_ROOT_CANDIDATES:
        receipt_path = cards_root / gemrate_id / "card_details.raw.receipt.json"
        if not receipt_path.is_file():
            continue
        receipt = _read_json(receipt_path)
        raw_path = (receipt_path.parent / str(receipt.get("sourcePointer") or "")).resolve()
        try:
            raw_path.relative_to(receipt_path.parent.resolve())
        except ValueError as exc:
            raise RuntimeError(f"GemRate raw pointer escapes card directory: {gemrate_id}") from exc
        if not raw_path.is_file():
            continue
        raw_bytes = raw_path.read_bytes()
        source_sha = hashlib.sha256(raw_bytes).hexdigest()
        if source_sha != str(receipt.get("contentSha256") or ""):
            raise RuntimeError(f"GemRate raw payload hash drifted: {gemrate_id}")
        payload = json.loads(raw_bytes.decode("utf-8-sig"))
        psa_rows = [
            row for row in (payload.get("population_data") or [])
            if isinstance(row, dict) and str(row.get("grader") or "").casefold() == "psa"
        ]
        if len(psa_rows) != 1:
            raise RuntimeError(f"GemRate raw PSA row is not unique: {gemrate_id}:{len(psa_rows)}")
        description = psa_rows[0].get("description")
        if not isinstance(description, str) or not description:
            raise RuntimeError(f"GemRate raw PSA description is missing: {gemrate_id}")
        return description, source_sha, _evidence_datetime(receipt.get("fetchedAt"), receipt_path)
    return None


def _gemrate_source_collector_number(
    gemrate_id: str,
    tcg_code: object,
    fallback_collector_number: object,
) -> str:
    """Return the canonical physical collector number.

    A GemRate One Piece ``set_name`` describes the product that supplied the
    card, not necessarily the physical collector namespace printed on the
    card.  The OP11 3rd Anniversary cards are the concrete case: their product
    is OP11 while their physical number remains OP05-119.  Never synthesise a
    collector prefix from that product set; the canonical printing identity is
    the sole authority for the full collector number.
    """

    del gemrate_id, tcg_code
    return _normalize_official_name(fallback_collector_number)


def _normalize_official_name(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_full_name(value: object, collector_number: object) -> str:
    """The provider description with its truncated collector tail completed.

    This function has been here twice. The first version did the completion with
    a literal regex, could not fold a leading zero (`079` against `79/73`), fell
    through to append and wrote "... Secret 079 79/73" on 79 rows -- so commit
    f24b2447 replaced the whole body with a passthrough. Passthrough is not
    neutral: the PSA description ends at the bare numerator, so byte-for-byte
    means every accepted official name ships truncated.

    The completion lives in identity_name now, shared with rebuild_036's bind
    transaction, so the two writers cannot drift again, and
    scripts/test_identity_name.py pins the `079` case that killed version one.
    """

    return complete_collector_tail(value, collector_number)


def sync_026_canonical_identity_repairs(cur) -> dict:
    """Apply only manifest-bound printing repairs backed by two exact providers."""

    manifest = _read_json(CANONICAL_PRINTING_REPAIRS)
    entries = manifest.get("entries")
    if (
        int(manifest.get("schemaVersion") or 0) != 1
        or manifest.get("contract") != "canonical-printing-repair-026-v1"
        or not isinstance(entries, list)
        or not entries
    ):
        raise RuntimeError("026 canonical printing repair manifest is invalid")
    manifest_sha256 = _file_sha256(CANONICAL_PRINTING_REPAIRS)

    official_map_path = _first_existing(
        OFFICIAL_NAME_MAP_CANDIDATES, "PSA official-name map"
    )
    official_rows: dict[int, dict] = {}
    with official_map_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            value = json.loads(line)
            if isinstance(value, dict) and value.get("variantId") is not None:
                official_rows[int(value["variantId"])] = value

    changed = 0
    rejected_snk = 0
    repaired: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("026 canonical printing repair entry is invalid")
        variant_id = int(entry.get("variantId") or 0)
        from_language = str(entry.get("fromCardLanguage") or "")
        to_language = str(entry.get("toCardLanguage") or "")
        required_sources = tuple(entry.get("requiredExactSources") or ())
        reject_sources = tuple(entry.get("rejectExactSources") or ())
        if (
            variant_id <= 0
            or from_language not in LOCALES
            or to_language not in LOCALES
            or from_language == to_language
            or required_sources != ("gemrate", "pricecharting")
            or any(source != "snkrdunk" for source in reject_sources)
        ):
            raise RuntimeError(f"026 canonical printing repair policy is invalid: {variant_id}")

        cur.execute(
            """
            SELECT p.*,v.opaque_id,v.card_language AS variant_card_language
            FROM catalog_printing_identity p
            INNER JOIN catalog_variant v ON v.id=p.variant_id
            INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
            INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
            WHERE p.variant_id=%s
            """,
            (variant_id,),
        )
        row = dict(cur.fetchone() or {})
        if (
            not row
            or row.get("opaque_id") != entry.get("opaqueId")
            or str(row.get("tcg_code") or "") != entry.get("tcgCode")
            or str(row.get("collector_number") or "") != entry.get("collectorNumber")
            or str(entry.get("setNameContains") or "").casefold()
            not in str(row.get("set_name") or "").casefold()
            or str(row.get("variant_card_language") or "") != to_language
            or str(row.get("card_language") or "") not in {from_language, to_language}
        ):
            raise RuntimeError(f"026 canonical printing repair target drifted: {variant_id}")

        cur.execute(
            """
            SELECT source_code,external_entity_id,evidence_sha256
            FROM catalog_source_identity
            WHERE variant_id=%s AND LOWER(match_status)='exact'
              AND source_code IN ('gemrate','pricecharting')
            ORDER BY source_code,external_entity_id
            """,
            (variant_id,),
        )
        exact_rows = [dict(value) for value in cur.fetchall()]
        exact_by_source: dict[str, list[dict]] = {}
        for value in exact_rows:
            exact_by_source.setdefault(str(value["source_code"]), []).append(value)
        if any(len(exact_by_source.get(source) or []) != 1 for source in required_sources):
            raise RuntimeError(f"026 canonical printing repair lacks unique exact sources: {variant_id}")

        gemrate_identity = exact_by_source["gemrate"][0]
        official = official_rows.get(variant_id) or {}
        if (
            str(official.get("gemrateId") or "") != gemrate_identity["external_entity_id"]
            or str(official.get("newName") or "") != entry.get("officialFullName")
            or str(official.get("nameSource") or "") == "catalog_fallback"
        ):
            raise RuntimeError(f"026 canonical printing repair lacks exact GemRate evidence: {variant_id}")

        parts = [
            str(row.get("tcg_code") or "").strip().casefold(),
            str(row.get("set_name") or "").strip().casefold(),
            str(row.get("collector_number") or "").strip().casefold(),
            to_language,
            str(row.get("edition_code") or "").strip().casefold(),
            str(row.get("parallel_code") or "").strip().casefold(),
            str(row.get("finish_code") or "").strip().casefold(),
        ]
        printing_hash = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        cur.execute(
            """
            SELECT variant_id FROM catalog_printing_identity
            WHERE variant_id<>%s AND canonical_printing_sha256=%s LIMIT 1
            """,
            (variant_id, printing_hash),
        )
        collision = cur.fetchone()
        if collision:
            raise RuntimeError(
                f"026 canonical printing repair collides with variant {collision['variant_id']}"
            )

        existing_provenance = row.get("provenance_json")
        if isinstance(existing_provenance, str):
            existing_provenance = json.loads(existing_provenance)
        already_repaired = (
            str(row.get("card_language") or "") == to_language
            and isinstance(existing_provenance, dict)
            and existing_provenance.get("contract") == manifest["contract"]
            and existing_provenance.get("manifestSha256") == manifest_sha256
        )
        if already_repaired:
            evidence = {
                key: existing_provenance.get(key)
                for key in (
                    "contract",
                    "manifestSha256",
                    "variantId",
                    "fromCardLanguage",
                    "toCardLanguage",
                    "officialFullName",
                    "exactSourceExternalEntityIds",
                    "exactSourceEvidenceSha256",
                    "priceObservationId",
                    "pricePayloadSha256",
                    "populationObservationId",
                    "populationPayloadSha256",
                )
            }
            expected_external_ids = {
                source: exact_by_source[source][0]["external_entity_id"]
                for source in required_sources
            }
            pinned_identity_evidence = evidence.get("exactSourceEvidenceSha256")
            if (
                row.get("canonical_printing_sha256") != printing_hash
                or evidence.get("contract") != manifest["contract"]
                or evidence.get("manifestSha256") != manifest_sha256
                or int(evidence.get("variantId") or 0) != variant_id
                or evidence.get("fromCardLanguage") != from_language
                or evidence.get("toCardLanguage") != to_language
                or evidence.get("officialFullName") != entry["officialFullName"]
                or evidence.get("exactSourceExternalEntityIds") != expected_external_ids
                or not isinstance(pinned_identity_evidence, dict)
                or set(pinned_identity_evidence) != set(required_sources)
                or any(
                    not re.fullmatch(r"[0-9a-f]{64}", str(value or ""))
                    for value in pinned_identity_evidence.values()
                )
                or row.get("evidence_sha256") != canonical_sha256(evidence)
            ):
                raise RuntimeError(f"026 canonical printing replay drifted: {variant_id}")
            cur.execute(
                """
                SELECT p.id,p.source_external_entity_id,p.payload_sha256,p.effective_at,
                       so.observed_at
                FROM market_price_observation p
                INNER JOIN market_source_observation so ON so.id=p.source_observation_id
                  AND so.source_code='pricecharting'
                  AND so.external_entity_id=p.source_external_entity_id
                  AND so.payload_sha256=p.payload_sha256
                  AND so.observed_date=p.observed_date
                INNER JOIN operator_strict_source_identity si ON si.variant_id=p.variant_id
                  AND si.source_code='pricecharting'
                  AND si.external_entity_id=p.source_external_entity_id
                WHERE p.id=%s AND p.variant_id=%s AND p.source_code='pricecharting'
                  AND p.payload_sha256=%s
                  AND p.metric_status='ready' AND p.price_usd>0 AND p.source_priority=95
                  AND so.observation_kind='psa10_price_guide'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE %s
                  AND p.source_external_entity_id REGEXP '^[0-9]+$'
                LIMIT 1
                """,
                (
                    int(evidence.get("priceObservationId") or 0),
                    variant_id,
                    str(evidence.get("pricePayloadSha256") or ""),
                    "%" + str(entry.get("pricechartingPathSuffix") or ""),
                ),
            )
            price_evidence = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT pop.id,pop.payload_sha256,pop.effective_at
                FROM market_grader_population_observation pop
                WHERE pop.id=%s AND pop.variant_id=%s AND pop.source_code='gemrate'
                  AND pop.external_entity_id=%s AND pop.payload_sha256=%s AND pop.estimated=0
                  AND UPPER(pop.grader_code)='PSA'
                  AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
                LIMIT 1
                """,
                (
                    int(evidence.get("populationObservationId") or 0),
                    variant_id,
                    gemrate_identity["external_entity_id"],
                    str(evidence.get("populationPayloadSha256") or ""),
                ),
            )
            pop_evidence = dict(cur.fetchone() or {})
            if not price_evidence or not pop_evidence:
                raise RuntimeError(f"026 canonical printing pinned evidence missing: {variant_id}")
            evidence_sha256 = str(row.get("evidence_sha256") or "")
            observed_at = max(
                value
                for value in (
                    price_evidence.get("observed_at"),
                    price_evidence.get("effective_at"),
                    pop_evidence.get("effective_at"),
                )
                if isinstance(value, datetime)
            )
        else:
            cur.execute(
                """
                SELECT p.id,p.source_external_entity_id,p.payload_sha256,p.effective_at,
                       so.observed_at
                FROM market_price_observation p
                INNER JOIN market_source_observation so ON so.id=p.source_observation_id
                  AND so.source_code='pricecharting'
                  AND so.external_entity_id=p.source_external_entity_id
                  AND so.payload_sha256=p.payload_sha256
                  AND so.observed_date=p.observed_date
                INNER JOIN operator_strict_source_identity si ON si.variant_id=p.variant_id
                  AND si.source_code='pricecharting'
                  AND si.external_entity_id=p.source_external_entity_id
                WHERE p.variant_id=%s AND p.source_code='pricecharting'
                  AND p.metric_status='ready' AND p.price_usd>0 AND p.source_priority=95
                  AND so.observation_kind='psa10_price_guide'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
                  AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE %s
                  AND p.source_external_entity_id REGEXP '^[0-9]+$'
                ORDER BY p.effective_at DESC,so.observed_at DESC,p.id DESC LIMIT 1
                """,
                (variant_id, "%" + str(entry.get("pricechartingPathSuffix") or "")),
            )
            price_evidence = dict(cur.fetchone() or {})
            cur.execute(
                """
                SELECT pop.id,pop.payload_sha256,pop.effective_at
                FROM market_grader_population_observation pop
                WHERE pop.variant_id=%s AND pop.source_code='gemrate'
                  AND pop.external_entity_id=%s AND pop.estimated=0
                  AND UPPER(pop.grader_code)='PSA'
                  AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
                ORDER BY pop.effective_at DESC,pop.id DESC LIMIT 1
                """,
                (variant_id, gemrate_identity["external_entity_id"]),
            )
            pop_evidence = dict(cur.fetchone() or {})
            if not price_evidence or not pop_evidence:
                raise RuntimeError(f"026 canonical printing repair lacks pinned evidence: {variant_id}")
            observed_at = max(
                value
                for value in (
                    price_evidence.get("observed_at"),
                    price_evidence.get("effective_at"),
                    pop_evidence.get("effective_at"),
                )
                if isinstance(value, datetime)
            )
            evidence = {
                "contract": manifest["contract"],
                "manifestSha256": manifest_sha256,
                "variantId": variant_id,
                "fromCardLanguage": from_language,
                "toCardLanguage": to_language,
                "officialFullName": entry["officialFullName"],
                "exactSourceExternalEntityIds": {
                    source: exact_by_source[source][0]["external_entity_id"]
                    for source in required_sources
                },
                "exactSourceEvidenceSha256": {
                    source: exact_by_source[source][0]["evidence_sha256"]
                    for source in required_sources
                },
                "priceObservationId": int(price_evidence["id"]),
                "pricePayloadSha256": price_evidence["payload_sha256"],
                "populationObservationId": int(pop_evidence["id"]),
                "populationPayloadSha256": pop_evidence["payload_sha256"],
            }
            evidence_sha256 = canonical_sha256(evidence)
            provenance = {
                **evidence,
                "manifestPath": CANONICAL_PRINTING_REPAIRS.relative_to(ROOT).as_posix(),
                "previousCanonicalPrintingSha256": row.get("canonical_printing_sha256"),
                "previousEvidenceSha256": row.get("evidence_sha256"),
            }
            cur.execute(
                """
                UPDATE catalog_printing_identity
                SET card_language=%s,canonical_printing_sha256=%s,identity_status='confirmed',
                    evidence_sha256=%s,provenance_json=%s,observed_at=%s
                WHERE variant_id=%s
                """,
                (
                    to_language, printing_hash, evidence_sha256,
                    json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                    observed_at, variant_id,
                ),
            )
            changed += int(cur.rowcount > 0)

        cur.execute(
            """
            INSERT INTO operator_binding_freeze
              (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
               acceptance_status,actor,evidence_sha256,note,accepted_at)
            VALUES (%s,'identity','','',%s,'accepted',%s,%s,
                    '026 canonical printing hash-bound identity freeze',%s)
            ON DUPLICATE KEY UPDATE
              external_entity_id='',content_sha256=VALUES(content_sha256),
              acceptance_status='accepted',actor=VALUES(actor),
              evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
              accepted_at=VALUES(accepted_at)
            """,
            (variant_id, printing_hash, ACCEPTANCE_ACTOR, evidence_sha256, observed_at),
        )

        if reject_sources:
            placeholders = ",".join(["%s"] * len(reject_sources))
            cur.execute(
                f"""
                UPDATE catalog_source_identity
                SET match_status='rejected_wrong_printing',
                    bind_evidence_json=JSON_OBJECT(
                      'contract','canonical-printing-repair-026-v1',
                      'manifestSha256',%s,'reason','different physical printing')
                WHERE variant_id=%s AND LOWER(match_status)='exact'
                  AND source_code IN ({placeholders})
                """,
                (manifest_sha256, variant_id, *reject_sources),
            )
            rejected_snk += int(cur.rowcount)
            cur.execute(
                f"""
                UPDATE operator_binding_freeze
                SET acceptance_status='rejected',actor=%s,evidence_sha256=%s,
                    note='026 rejected wrong-printing provider claim',accepted_at=%s
                WHERE variant_id=%s AND freeze_kind='source'
                  AND source_code IN ({placeholders})
                """,
                (ACCEPTANCE_ACTOR, evidence_sha256, observed_at, variant_id, *reject_sources),
            )
        repaired.append(variant_id)

    return {
        "manifest": str(CANONICAL_PRINTING_REPAIRS),
        "manifestSha256": manifest_sha256,
        "entries": len(entries),
        "changed": changed,
        "rejectedWrongSnkClaims": rejected_snk,
        "variantIds": repaired,
    }


def sync_026_known_unknown_printing_metadata(cur) -> dict:
    """Persist locally-audited missing edition/finish as explicit unknowns.

    The locked 026 evidence set does not contain an official printing-version
    or finish claim for these rows.  Empty strings are ambiguous and break the
    frontend contract; deriving either value from a card name is forbidden.
    This repair therefore records a typed known-unknown without claiming a
    finish or edition that the evidence does not support.
    """

    cur.execute(
        """
        SELECT p.*,v.opaque_id
        FROM catalog_printing_identity p
        INNER JOIN catalog_variant v ON v.id=p.variant_id
        INNER JOIN market_universe_member m ON m.variant_id=p.variant_id
        INNER JOIN market_universe_lock u
          ON u.id=m.universe_lock_id AND u.is_current=1
        WHERE COALESCE(p.edition_code,'')=''
           OR COALESCE(p.finish_code,'')=''
        ORDER BY p.variant_id
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    repaired: list[int] = []
    proposed_hashes: dict[str, int] = {}
    updates = []
    for row in rows:
        variant_id = int(row["variant_id"])
        edition_code = str(row.get("edition_code") or "").strip() or "unknown"
        finish_code = str(row.get("finish_code") or "").strip() or "unknown"
        previous_printing_hash = str(
            row.get("canonical_printing_sha256") or ""
        ).strip()
        if not re.fullmatch(r"[0-9a-f]{64}", previous_printing_hash):
            raise RuntimeError(
                f"026 known-unknown printing repair lacks prior lineage: {variant_id}"
            )
        # The legacy convergence intentionally salted duplicate physical-key
        # rows with their variant ID. Preserve that already-accepted lineage;
        # reconstructing only the seven public parts would collapse distinct
        # accepted variants onto the same UNIQUE hash.
        printing_hash = hashlib.sha256(
            (
                f"{previous_printing_hash}|edition:{edition_code.casefold()}|"
                f"finish:{finish_code.casefold()}|{KNOWN_UNKNOWN_PRINTING_CONTRACT}"
            ).encode("utf-8")
        ).hexdigest()
        collision = proposed_hashes.get(printing_hash)
        if collision is not None and collision != variant_id:
            raise RuntimeError(
                f"026 known-unknown printing repair collides: {collision}/{variant_id}"
            )
        proposed_hashes[printing_hash] = variant_id

        prior_provenance = row.get("provenance_json")
        if isinstance(prior_provenance, str):
            prior_provenance = json.loads(prior_provenance)
        evidence = {
            "contract": KNOWN_UNKNOWN_PRINTING_CONTRACT,
            "variantId": variant_id,
            "opaqueId": str(row.get("opaque_id") or ""),
            "editionCode": edition_code,
            "finishCode": finish_code,
            "reason": "no exact official edition or finish evidence in the locked local corpus",
            "inferencePolicy": "do-not-derive-from-name-or-rarity",
            "previousCanonicalPrintingSha256": previous_printing_hash,
            "previousEvidenceSha256": str(row.get("evidence_sha256") or ""),
        }
        evidence_sha256 = canonical_sha256(evidence)
        provenance = {
            **evidence,
            "priorProvenance": prior_provenance,
        }
        updates.append(
            (
                edition_code,
                finish_code,
                printing_hash,
                evidence_sha256,
                json.dumps(provenance, ensure_ascii=False, sort_keys=True),
                variant_id,
            )
        )
        repaired.append(variant_id)

    if proposed_hashes:
        placeholders = ",".join(["%s"] * len(proposed_hashes))
        cur.execute(
            f"""
            SELECT variant_id,canonical_printing_sha256
            FROM catalog_printing_identity
            WHERE canonical_printing_sha256 IN ({placeholders})
            """,
            tuple(proposed_hashes),
        )
        for existing in cur.fetchall():
            owner = proposed_hashes[str(existing["canonical_printing_sha256"])]
            if int(existing["variant_id"]) != owner:
                raise RuntimeError(
                    "026 known-unknown printing repair collides with variant "
                    f"{existing['variant_id']}"
                )

    if updates:
        cur.executemany(
            """
            UPDATE catalog_printing_identity
            SET edition_code=%s,finish_code=%s,canonical_printing_sha256=%s,
                evidence_sha256=%s,provenance_json=%s
            WHERE variant_id=%s
            """,
            updates,
        )
    return {
        "contract": KNOWN_UNKNOWN_PRINTING_CONTRACT,
        "repaired": len(repaired),
        "variantIds": repaired,
        "editionCode": "unknown",
        "finishCode": "unknown",
        "remainingEvidenceTask": "replace unknown only with exact official printing evidence",
    }


def sync_026_gemrate_identity_repairs(cur) -> dict:
    """Select one checkpointed GemRate primary while retaining raw-name lineage."""

    manifest = _read_json(CANONICAL_GEMRATE_IDENTITY_REPAIRS)
    entries = manifest.get("entries")
    if (
        int(manifest.get("schemaVersion") or 0) != 1
        or manifest.get("contract") != "canonical-gemrate-identity-repair-026-v1"
        or not isinstance(entries, list)
        or not entries
    ):
        raise RuntimeError("026 canonical GemRate identity repair manifest is invalid")
    manifest_sha256 = _file_sha256(CANONICAL_GEMRATE_IDENTITY_REPAIRS)

    map_path = _first_existing(OFFICIAL_NAME_MAP_CANDIDATES, "PSA official-name map")
    mapped: dict[tuple[int, str], dict] = {}
    with map_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            try:
                key = (int(row.get("variantId")), str(row.get("gemrateId") or ""))
            except (TypeError, ValueError):
                continue
            if key[1]:
                mapped[key] = row

    changed = 0
    rejected_freezes = 0
    repaired: list[int] = []
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("026 canonical GemRate identity repair entry is invalid")
        variant_id = int(entry.get("variantId") or 0)
        selected_id = str(entry.get("selectedGemrateId") or "").strip()
        name_evidence_id = str(entry.get("officialNameEvidenceGemrateId") or "").strip()
        rejected_ids = tuple(str(value).strip() for value in entry.get("rejectedGemrateIds") or ())
        expected_ids = {selected_id, *rejected_ids}
        if (
            variant_id <= 0
            or not selected_id
            or not name_evidence_id
            or not rejected_ids
            or len(expected_ids) != 1 + len(rejected_ids)
            or name_evidence_id not in expected_ids
        ):
            raise RuntimeError(f"026 canonical GemRate identity repair policy is invalid: {variant_id}")

        cur.execute(
            """
            SELECT v.tcg_code,p.card_language,p.collector_number,p.set_code,p.printing_code
            FROM catalog_variant v
            INNER JOIN market_universe_member am ON am.variant_id=v.id
            INNER JOIN market_universe_lock ul
              ON ul.id=am.universe_lock_id AND ul.is_current=1
            INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
            WHERE v.id=%s
            """,
            (variant_id,),
        )
        target = dict(cur.fetchone() or {})
        if (
            not target
            or str(target.get("tcg_code") or "") != str(entry.get("tcgCode") or "")
            or str(target.get("card_language") or "") != str(entry.get("cardLanguage") or "")
            or str(target.get("collector_number") or "") != str(entry.get("collectorNumber") or "")
            or str(target.get("set_code") or "") != str(entry.get("setCode") or "")
            or str(target.get("printing_code") or "") != str(entry.get("printingCode") or "")
        ):
            raise RuntimeError(f"026 canonical GemRate identity repair target drifted: {variant_id}")

        cur.execute(
            """
            SELECT external_entity_id,match_status,evidence_sha256
            FROM catalog_source_identity
            WHERE variant_id=%s AND source_code='gemrate'
            ORDER BY external_entity_id
            """,
            (variant_id,),
        )
        identity_rows = {str(row["external_entity_id"]): dict(row) for row in cur.fetchall()}
        selected = identity_rows.get(selected_id) or {}
        unknown_exact = sorted(
            external_id
            for external_id, row in identity_rows.items()
            if str(row.get("match_status") or "").lower() == "exact"
            and external_id not in expected_ids
        )
        if (
            not expected_ids.issubset(identity_rows)
            or str(selected.get("match_status") or "").lower() != "exact"
            or not re.fullmatch(r"[0-9a-f]{64}", str(selected.get("evidence_sha256") or ""))
            or unknown_exact
            or any(
                str((identity_rows.get(external_id) or {}).get("match_status") or "").lower()
                not in {"exact", "rejected_duplicate"}
                for external_id in rejected_ids
            )
        ):
            raise RuntimeError(f"026 canonical GemRate identity set drifted: {variant_id}")

        selected_map = mapped.get((variant_id, selected_id)) or {}
        evidence_map = mapped.get((variant_id, name_evidence_id)) or {}
        official_name = _normalize_official_name(entry.get("officialFullName"))
        if (
            not selected_map
            or not evidence_map
            or str(evidence_map.get("nameSource") or "") != "raw"
            or _normalize_official_name(evidence_map.get("newName")) != official_name
            or str(evidence_map.get("gemrateSet") or "") != str(entry.get("providerSet") or "")
            or str(evidence_map.get("gemrateParallel") or "")
            != str(entry.get("providerParallel") or "")
        ):
            raise RuntimeError(f"026 canonical GemRate raw-name evidence drifted: {variant_id}")

        cur.execute(
            """
            SELECT last_effective_at,last_payload_sha256,last_run_id
            FROM market_ingest_checkpoint
            WHERE source_code='gemrate_pop' AND stream_key=%s
            """,
            (f"{variant_id}:{selected_id}"[:100],),
        )
        checkpoint = dict(cur.fetchone() or {})
        checkpoint_at = checkpoint.get("last_effective_at")
        if (
            not isinstance(checkpoint_at, datetime)
            or (now - checkpoint_at).total_seconds() > 36 * 3600
            or not re.fullmatch(r"[0-9a-f]{64}", str(checkpoint.get("last_payload_sha256") or ""))
        ):
            raise RuntimeError(f"026 selected GemRate primary is not freshly checkpointed: {variant_id}")

        evidence = {
            "contract": manifest["contract"],
            "manifestSha256": manifest_sha256,
            "variantId": variant_id,
            "selectedGemrateId": selected_id,
            "officialNameEvidenceGemrateId": name_evidence_id,
            "rejectedGemrateIds": list(rejected_ids),
            "officialFullName": official_name,
            "selectedIdentityEvidenceSha256": selected["evidence_sha256"],
            "checkpointPayloadSha256": checkpoint["last_payload_sha256"],
            "checkpointEffectiveAt": checkpoint_at,
            "checkpointRunId": int(checkpoint.get("last_run_id") or 0),
            "selectedMapSha256": canonical_sha256(selected_map),
            "officialNameMapSha256": canonical_sha256(evidence_map),
            "reason": str(entry.get("reason") or ""),
        }
        evidence_sha256 = canonical_sha256(evidence)
        placeholders = ",".join(["%s"] * len(rejected_ids))
        cur.execute(
            """
            UPDATE catalog_source_identity
            SET bind_evidence_json=JSON_OBJECT(
              'contract',%s,'manifestSha256',%s,'selectedGemrateId',%s,
              'officialNameEvidenceGemrateId',%s,'officialFullName',%s,
              'rejectedGemrateIds',CAST(%s AS JSON),'reason',%s
            )
            WHERE variant_id=%s AND source_code='gemrate'
              AND external_entity_id=%s AND LOWER(match_status)='exact'
            """,
            (
                manifest["contract"], manifest_sha256, selected_id,
                name_evidence_id, official_name,
                json.dumps(list(rejected_ids), ensure_ascii=False),
                str(entry.get("reason") or ""), variant_id, selected_id,
            ),
        )
        cur.execute(
            f"""
            UPDATE catalog_source_identity
            SET match_status='rejected_duplicate',
                bind_evidence_json=JSON_OBJECT(
                  'contract',%s,'manifestSha256',%s,'selectedGemrateId',%s,
                  'officialNameEvidenceGemrateId',%s,'reason',%s
                )
            WHERE variant_id=%s AND source_code='gemrate'
              AND external_entity_id IN ({placeholders})
              AND LOWER(match_status) IN ('exact','rejected_duplicate')
            """,
            (
                manifest["contract"], manifest_sha256, selected_id,
                name_evidence_id, str(entry.get("reason") or ""), variant_id,
                *rejected_ids,
            ),
        )
        changed += int(cur.rowcount)
        cur.execute(
            f"""
            UPDATE operator_binding_freeze
            SET acceptance_status='rejected',actor=%s,evidence_sha256=%s,
                note='026 rejected duplicate GemRate identity',accepted_at=%s
            WHERE variant_id=%s AND freeze_kind='source' AND source_code='gemrate'
              AND external_entity_id IN ({placeholders})
            """,
            (ACCEPTANCE_ACTOR, evidence_sha256, checkpoint_at, variant_id, *rejected_ids),
        )
        rejected_freezes += int(cur.rowcount)
        cur.execute(
            """
            INSERT INTO operator_binding_freeze
              (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
               acceptance_status,actor,evidence_sha256,note,accepted_at)
            VALUES (%s,'source','gemrate',%s,'','accepted',%s,%s,
                    '026 canonical GemRate primary identity',%s)
            ON DUPLICATE KEY UPDATE
              external_entity_id=VALUES(external_entity_id),content_sha256='',
              acceptance_status='accepted',actor=VALUES(actor),
              evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
              accepted_at=VALUES(accepted_at)
            """,
            (variant_id, selected_id, ACCEPTANCE_ACTOR, evidence_sha256, checkpoint_at),
        )
        repaired.append(variant_id)

    return {
        "manifest": str(CANONICAL_GEMRATE_IDENTITY_REPAIRS),
        "manifestSha256": manifest_sha256,
        "entries": len(entries),
        "identityRowsRejected": changed,
        "sourceFreezesRejected": rejected_freezes,
        "variantIds": repaired,
    }


def sync_026_canonical_identity_freezes(cur) -> dict:
    """Bind every previously accepted identity freeze to its canonical printing hashes."""

    cur.execute(
        """
        UPDATE operator_binding_freeze f
        INNER JOIN catalog_printing_identity p ON p.variant_id=f.variant_id
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul
          ON ul.id=am.universe_lock_id AND ul.is_current=1
        SET f.external_entity_id='',
            f.content_sha256=p.canonical_printing_sha256,
            f.evidence_sha256=p.evidence_sha256,
            f.note='026 hash-bound migration of accepted canonical identity freeze'
        WHERE f.freeze_kind='identity' AND f.source_code=''
          AND f.acceptance_status='accepted'
          AND p.identity_status IN ('confirmed','canonical')
          AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
          AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
          AND (
            f.external_entity_id<>''
            OR f.content_sha256<>p.canonical_printing_sha256
            OR f.evidence_sha256<>p.evidence_sha256
          )
        """
    )
    migrated = int(cur.rowcount)
    cur.execute(
        """
        SELECT COUNT(*) AS bound_count
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul
          ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity p ON p.variant_id=am.variant_id
        INNER JOIN operator_binding_freeze f
          ON f.variant_id=p.variant_id
         AND f.freeze_kind='identity'
         AND f.source_code=''
         AND f.external_entity_id=''
         AND f.acceptance_status='accepted'
         AND f.content_sha256=p.canonical_printing_sha256
         AND f.evidence_sha256=p.evidence_sha256
        WHERE p.identity_status IN ('confirmed','canonical')
          AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
          AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """
    )
    bound = int((cur.fetchone() or {}).get("bound_count") or 0)
    if bound != 762:
        raise RuntimeError(f"026 canonical identity freeze coverage is {bound}/762")
    return {"bound": bound, "migrated": migrated}


def sync_official_names(cur) -> dict:
    """Materialize names only from the migration-034 PSA acceptance authority."""

    cur.execute(
        """
        SELECT COUNT(*) AS active_count
        FROM market_universe_member m
        INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
        """
    )
    active_count = int((cur.fetchone() or {}).get("active_count") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS accepted_count
        FROM market_universe_member m
        INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
        INNER JOIN operator_psa_identity_projection psa ON psa.variant_id=m.variant_id
        """
    )
    accepted_count = int((cur.fetchone() or {}).get("accepted_count") or 0)
    if accepted_count != active_count:
        raise RuntimeError(
            f"034 literal PSA identity acceptance incomplete: {accepted_count}/{active_count}"
        )
    # This used to be one SQL UPDATE setting canonical_name=psa_description. It
    # could never have fired for the name: until migration 039 the projection
    # itself joined on BINARY v.canonical_name=BINARY psa.psa_description, so the
    # WHERE clause it carried was unsatisfiable by construction -- the language
    # half was the only live branch. Now that the view no longer pins the name,
    # the same statement WOULD fire, and it would re-truncate every completed
    # name on the next tidy run. That is mechanism A1 in one line of SQL.
    #
    # So it runs in Python, through the one completion function, exactly like the
    # bind transaction: psa_description stays the PSA literal, the display name
    # is that literal with its collector tail completed.
    cur.execute(
        """
        SELECT v.id,v.canonical_name,v.card_language,v.collector_number,
               psa.psa_description,psa.psa_language
        FROM catalog_variant v
        INNER JOIN operator_psa_identity_projection psa ON psa.variant_id=v.id
        """
    )
    updated = 0
    for row in cur.fetchall():
        want_name = _canonical_full_name(row["psa_description"], row["collector_number"])
        want_language = row["psa_language"]
        if (
            str(row["canonical_name"] or "").encode("utf-8") == want_name.encode("utf-8")
            and str(row["card_language"] or "") == str(want_language or "")
        ):
            continue
        cur.execute(
            "UPDATE catalog_variant SET canonical_name=%s,card_language=%s WHERE id=%s",
            (want_name, want_language, int(row["id"])),
        )
        updated += 1
    return {
        "accepted": accepted_count,
        "updated": updated,
        "authority": "catalog_psa_identity_acceptance",
        "contract": "active-psa-identity-resolution-035-v1",
    }

    # Historical 026/031 import implementation retained below for code-lineage
    # reference only; execution returns above and it is no longer an authority.

    map_path = _first_existing(OFFICIAL_NAME_MAP_CANDIDATES, "PSA official-name map")
    apply_path = _first_existing(OFFICIAL_NAME_APPLY_CANDIDATES, "PSA official-name apply receipt")
    apply_receipt = _read_json(apply_path)
    map_observed_at = _evidence_datetime(apply_receipt.get("asOf"), apply_path)
    repair_manifest = _read_json(CANONICAL_GEMRATE_IDENTITY_REPAIRS)
    repair_manifest_sha256 = _file_sha256(CANONICAL_GEMRATE_IDENTITY_REPAIRS)
    official_overrides = {
        int(entry["variantId"]): entry
        for entry in (repair_manifest.get("entries") or [])
        if isinstance(entry, dict) and entry.get("variantId") is not None
    }
    mapped: dict[tuple[int, str], dict] = {}
    with map_path.open("r", encoding="utf-8-sig") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                continue
            try:
                variant_id = int(row.get("variantId"))
            except (TypeError, ValueError):
                continue
            external_id = str(row.get("gemrateId") or "").strip()
            name = _normalize_official_name(row.get("newName"))
            if external_id and name and str(row.get("nameSource") or "") != "catalog_fallback":
                mapped[(variant_id, external_id)] = row

    cur.execute(
        """
        SELECT v.id AS variant_id,p.canonical_printing_sha256,p.collector_number,p.tcg_code,
               si.external_entity_id,si.evidence_sha256 AS identity_evidence_sha256,
               accepted_name.external_entity_id AS accepted_name_external_id
        FROM catalog_variant v
        INNER JOIN market_universe_member am ON am.variant_id=v.id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
        INNER JOIN operator_strict_source_identity si ON si.variant_id=v.id
          AND si.source_code='gemrate'
        LEFT JOIN catalog_official_name_acceptance accepted_name
          ON accepted_name.variant_id=v.id
         AND NOT EXISTS (
           SELECT 1 FROM catalog_official_name_acceptance newer
           WHERE newer.supersedes_acceptance_id=accepted_name.id
         )
        ORDER BY v.id,si.external_entity_id
        """
    )
    active_rows = [dict(row) for row in cur.fetchall()]
    by_variant: dict[int, list[dict]] = {}
    for row in active_rows:
        by_variant.setdefault(int(row["variant_id"]), []).append(row)
    if len(by_variant) != 762:
        raise RuntimeError(f"official-name import requires 762 exact GemRate variants, got {len(by_variant)}")

    accepted = 0
    composed = 0
    missing: list[int] = []
    now = utc_now_sql()
    for variant_id, identities in sorted(by_variant.items()):
        accepted_external_id = str(identities[0].get("accepted_name_external_id") or "")
        if accepted_external_id:
            accepted_identities = [
                identity
                for identity in identities
                if str(identity["external_entity_id"]) == accepted_external_id
            ]
            if accepted_identities:
                identities = accepted_identities
        candidates: list[tuple[dict, str, str, datetime]] = []
        for identity in identities:
            external_id = str(identity["external_entity_id"])
            override = official_overrides.get(variant_id)
            if (
                override
                and external_id == str(override.get("selectedGemrateId") or "")
            ):
                selected_map = mapped.get((variant_id, external_id)) or {}
                evidence_id = str(
                    override.get("officialNameEvidenceGemrateId") or ""
                )
                evidence_map = mapped.get((variant_id, evidence_id)) or {}
                if not selected_map or not evidence_map:
                    raise RuntimeError(
                        f"canonical GemRate official-name override drifted: {variant_id}"
                    )
                candidates.append(
                    (
                        identity,
                        _normalize_official_name(override.get("officialFullName")),
                        canonical_sha256(
                            {
                                "contract": repair_manifest.get("contract"),
                                "manifestSha256": repair_manifest_sha256,
                                "selectedGemrateId": external_id,
                                "officialNameEvidenceGemrateId": evidence_id,
                                "selectedMap": selected_map,
                                "officialNameMap": evidence_map,
                            }
                        ),
                        map_observed_at,
                    )
                )
                continue
            row = mapped.get((variant_id, external_id))
            if row:
                candidates.append(
                    (
                        identity,
                        _normalize_official_name(row["newName"]),
                        canonical_sha256(row),
                        map_observed_at,
                    )
                )
                continue
            page_name = _gemrate_page_official_name(str(identity["external_entity_id"]))
            if page_name:
                page_official_name, page_payload_sha, page_observed_at = page_name
                candidates.append(
                    (
                        identity,
                        _normalize_official_name(page_official_name),
                        page_payload_sha,
                        page_observed_at,
                    )
                )
                composed += 1
        collector_number = _gemrate_source_collector_number(
            str(identities[0]["external_entity_id"]),
            identities[0].get("tcg_code"),
            identities[0].get("collector_number"),
        )
        candidates = [
            (identity, _canonical_full_name(name, collector_number), payload_sha, observed_at)
            for identity, name, payload_sha, observed_at in candidates
        ]
        distinct_names = {candidate[1] for candidate in candidates if candidate[1]}
        if len(distinct_names) > 1:
            raise RuntimeError(
                f"conflicting exact GemRate official names for variant {variant_id}: "
                f"{sorted(distinct_names)}"
            )
        if not candidates or not distinct_names:
            missing.append(variant_id)
            continue
        selected, official_name, source_payload_sha, observed_at = candidates[0]
        evidence_sha = canonical_sha256(
            {
                "policy": "psa-gemrate-official-full-name-v1",
                "variantId": variant_id,
                "gemrateId": selected["external_entity_id"],
                "canonicalPrintingSha256": selected["canonical_printing_sha256"],
                "identityEvidenceSha256": selected["identity_evidence_sha256"],
                "sourcePayloadSha256": source_payload_sha,
                "officialFullName": official_name,
            }
        )
        lineage_sha = canonical_sha256(
            {
                "kind": "catalog-official-name-acceptance-v1",
                "variantId": variant_id,
                "externalEntityId": selected["external_entity_id"],
                "officialFullName": official_name,
                "evidenceSha256": evidence_sha,
            }
        )
        cur.execute(
            """
            SELECT id,lineage_sha256 FROM catalog_official_name_acceptance
            WHERE variant_id=%s AND NOT EXISTS (
              SELECT 1 FROM catalog_official_name_acceptance newer
              WHERE newer.supersedes_acceptance_id=catalog_official_name_acceptance.id
            ) ORDER BY accepted_at DESC,id DESC LIMIT 1
            """,
            (variant_id,),
        )
        current = cur.fetchone()
        supersedes = None if not current or current.get("lineage_sha256") == lineage_sha else int(current["id"])
        cur.execute(
            """
            INSERT INTO catalog_official_name_acceptance
              (variant_id,source_code,external_entity_id,official_full_name,
               canonical_printing_sha256,source_payload_sha256,evidence_sha256,lineage_sha256,
               source_observed_at,accepted_by,accepted_at,supersedes_acceptance_id)
            VALUES (%s,'gemrate',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """,
            (
                variant_id,
                selected["external_entity_id"],
                official_name,
                selected["canonical_printing_sha256"],
                source_payload_sha,
                evidence_sha,
                lineage_sha,
                observed_at,
                ACCEPTANCE_ACTOR,
                now,
                supersedes,
            ),
        )
        cur.execute("UPDATE catalog_variant SET canonical_name=%s WHERE id=%s", (official_name, variant_id))
        accepted += 1
    if missing or accepted != 762:
        raise RuntimeError(f"official-name acceptance incomplete: accepted={accepted}, missing={missing[:20]}")
    return {
        "accepted": accepted,
        "composedFromExactGemRatePage": composed,
        "map": str(map_path),
        "mapSha256": hashlib.sha256(map_path.read_bytes()).hexdigest(),
    }


def use_current_canonical_images(cur) -> dict:
    """Use the current 3308 image inventory; SNK collection already wrote assets."""

    cur.execute(
        """
        SELECT COUNT(DISTINCT m.variant_id) AS variants
        FROM market_universe_member m
        INNER JOIN market_universe_lock u
          ON u.id=m.universe_lock_id AND u.is_current=1
        INNER JOIN market_image_asset a
          ON a.variant_id=m.variant_id AND a.image_kind='raw_front'
        """
    )
    variants = int((cur.fetchone() or {}).get("variants") or 0)
    return {
        "variants": variants,
        "canonicalImages": variants,
        "source": "current-windows-3308",
        "baselineMaterializationUsed": False,
    }


def sync_canonical_images(cur) -> dict:
    """Materialize the accepted image lineage behind the locked 762-card generation."""

    baseline = _read_json(BASELINE_SNAPSHOT)
    baseline_top100_ids = {
        str(card.get("id"))
        for card in list(baseline.get("top100") or [])
        if isinstance(card, dict) and card.get("id")
    }
    baseline_cards = {
        str(card.get("id")): card
        for card in list(baseline.get("top100") or []) + list(baseline.get("watchlist") or [])
        if isinstance(card, dict) and card.get("id")
    }
    baseline_sha256 = _file_sha256(BASELINE_SNAPSHOT)
    lineage = _baseline_image_lineage(baseline, baseline_sha256)
    canonical_rebases = _canonical_image_rebases()
    materialized_hashes = lineage["mapping"]
    baseline_observed_at = _source_observed_at(baseline)
    cur.execute(
        """
        SELECT v.id AS variant_id, v.opaque_id
        FROM market_universe_member m
        INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
        INNER JOIN catalog_variant v ON v.id=m.variant_id
        ORDER BY v.id
        """
    )
    variants = [dict(row) for row in cur.fetchall()]
    variant_ids = [int(row["variant_id"]) for row in variants]
    if len(variant_ids) != 762:
        raise RuntimeError(f"canonical image import requires 762 active variants, got {len(variant_ids)}")
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT variant_id, source_code, content_sha256, evidence_sha256, accepted_at
        FROM operator_binding_freeze
        WHERE freeze_kind='image' AND acceptance_status='accepted'
          AND variant_id IN ({placeholders})
        """,
        tuple(variant_ids),
    )
    freezes: dict[int, list[dict]] = {}
    for row in cur.fetchall():
        freezes.setdefault(int(row["variant_id"]), []).append(dict(row))

    cur.execute(
        f"""
        SELECT DISTINCT a.variant_id, a.content_sha256
        FROM market_image_asset a
        INNER JOIN market_image_qc q
          ON q.image_asset_id=a.id AND q.public_allowed=1 AND q.raw_front_confirmed=1
         AND q.card_number_match=1 AND q.language_match=1 AND q.tcg_match=1
        INNER JOIN market_image_source_pointer ptr
          ON ptr.variant_id=a.variant_id AND ptr.image_kind=a.image_kind
         AND ptr.source_version_sha256=a.source_version_sha256 AND ptr.public_allowed=1
         AND ptr.source_path LIKE 'snkrdunk:%%'
        WHERE a.image_kind='raw_front' AND a.variant_id IN ({placeholders})
          AND EXISTS (
            SELECT 1 FROM operator_strict_source_identity si
            WHERE si.variant_id=a.variant_id AND si.source_code='snkrdunk'
          )
        """,
        tuple(variant_ids),
    )
    eligible_snk_images = {
        (int(row["variant_id"]), str(row.get("content_sha256") or "").strip().lower())
        for row in cur.fetchall()
    }

    planned = []
    snk_overrides = []
    retired_snk_overrides = []
    for variant in variants:
        variant_id = int(variant["variant_id"])
        opaque_id = str(variant["opaque_id"])
        card = baseline_cards.get(opaque_id)
        if not isinstance(card, dict):
            raise RuntimeError(f"baseline image card missing: {opaque_id}")
        image = card.get("image") if isinstance(card.get("image"), dict) else {}
        baseline_content_sha256 = str(image.get("sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", baseline_content_sha256):
            raise RuntimeError(f"baseline image hash invalid: {opaque_id}")
        baseline_file = PUBLIC_ASSETS / f"{baseline_content_sha256}.webp"
        if not baseline_file.is_file():
            raise RuntimeError(f"locked baseline image asset missing: {baseline_file}")
        if _file_sha256(baseline_file) != baseline_content_sha256:
            raise RuntimeError(f"locked baseline image asset hash mismatch: {baseline_file}")

        variant_freezes = freezes.get(variant_id, [])
        canonical_rebase = None
        direct_freezes = [
            row for row in variant_freezes
            if str(row.get("content_sha256") or "").strip().lower() == baseline_content_sha256
        ]
        mapped_freezes = [
            row for row in variant_freezes
            if materialized_hashes.get(
                str(row.get("content_sha256") or "").strip().lower()
            ) == baseline_content_sha256
        ]
        direct_freezes.sort(key=lambda row: row.get("accepted_at"), reverse=True)
        mapped_freezes.sort(key=lambda row: row.get("accepted_at"), reverse=True)
        if direct_freezes:
            freeze = direct_freezes[0]
            content_sha256 = baseline_content_sha256
            source_content_sha256 = content_sha256
            requires_derived_freeze = False
            derived_freeze_source_code = None
            derived_freeze_external_id = None
        elif len(mapped_freezes) == 1:
            freeze = mapped_freezes[0]
            content_sha256 = baseline_content_sha256
            source_content_sha256 = str(freeze.get("content_sha256") or "").strip().lower()
            requires_derived_freeze = True
            derived_freeze_source_code = f"release_{baseline_sha256[:16]}"
            derived_freeze_external_id = lineage["generationId"]
        elif len(mapped_freezes) > 1:
            raise RuntimeError(f"baseline image has ambiguous materialization lineage: {opaque_id}")
        else:
            if opaque_id not in baseline_top100_ids:
                raise RuntimeError(f"non-Top-100 baseline image has no accepted lineage: {opaque_id}")
            override_row = {
                "variantId": variant_id,
                "opaqueId": opaque_id,
                "contentSha256": baseline_content_sha256,
            }
            if (variant_id, baseline_content_sha256) in eligible_snk_images:
                snk_overrides.append(override_row)
            else:
                retired_snk_overrides.append(override_row)

            # Top-100 presentation may use an exact SNK image without replacing
            # the canonical accepted freeze.  Import the current accepted freeze
            # separately so the product projection retains both authorities.
            rebase = canonical_rebases["entries"].get(opaque_id)
            if not isinstance(rebase, dict):
                raise RuntimeError(f"Top-100 canonical image rebase is missing: {opaque_id}")
            provider_source_code = str(rebase.get("providerSourceCode") or "")
            provider_external_entity_id = str(rebase.get("providerExternalEntityId") or "")
            cur.execute(
                """
                SELECT
                  imgf.source_code, imgf.content_sha256, imgf.evidence_sha256,
                  imgf.accepted_at, a.width_px, a.height_px, a.source_version_sha256
                FROM operator_binding_freeze imgf
                INNER JOIN market_image_asset a
                  ON a.variant_id=imgf.variant_id AND a.image_kind='raw_front'
                 AND a.content_sha256=imgf.content_sha256
                INNER JOIN market_image_qc q
                  ON q.image_asset_id=a.id AND q.public_allowed=1 AND q.raw_front_confirmed=1
                 AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
                WHERE imgf.variant_id=%s AND imgf.freeze_kind='image'
                  AND imgf.acceptance_status='accepted'
                  AND imgf.content_sha256=%s AND imgf.evidence_sha256=%s
                ORDER BY q.checked_at DESC, a.captured_at DESC, a.id DESC
                LIMIT 1
                """,
                (
                    variant_id,
                    str(rebase.get("acceptedSourceSha256") or ""),
                    str(rebase.get("freezeEvidenceSha256") or ""),
                ),
            )
            freeze = cur.fetchone()
            if not freeze:
                raise RuntimeError(f"Top-100 override has no current accepted image: {opaque_id}")
            source_content_sha256 = str(freeze.get("content_sha256") or "").strip().lower()
            if (
                int(rebase.get("variantId") or 0) != variant_id
                or str(rebase.get("acceptedSourceSha256") or "") != source_content_sha256
                or str(rebase.get("freezeEvidenceSha256") or "")
                != str(freeze.get("evidence_sha256") or "")
                or str(rebase.get("sourceVersionSha256") or "")
                != str(freeze.get("source_version_sha256") or "")
                or int(rebase.get("width") or 0) != int(freeze.get("width_px") or 0)
                or int(rebase.get("height") or 0) != int(freeze.get("height_px") or 0)
            ):
                raise RuntimeError(f"Top-100 canonical image rebase lineage mismatch: {opaque_id}")
            cur.execute(
                """
                SELECT
                  si.evidence_sha256 AS identity_evidence_sha256,
                  provider_img.evidence_sha256 AS provider_freeze_evidence_sha256,
                  provider_img.content_sha256 AS provider_content_sha256
                FROM operator_strict_source_identity si
                INNER JOIN operator_binding_freeze provider_img
                  ON provider_img.variant_id=si.variant_id
                 AND provider_img.freeze_kind='image'
                 AND provider_img.source_code=si.source_code
                 AND provider_img.external_entity_id=si.external_entity_id
                WHERE si.variant_id=%s AND si.source_code=%s
                  AND si.external_entity_id=%s
                LIMIT 1
                """,
                (variant_id, provider_source_code, provider_external_entity_id),
            )
            provider_lineage = cur.fetchone()
            if (
                not provider_lineage
                or str(provider_lineage.get("identity_evidence_sha256") or "")
                != str(rebase.get("exactIdentityEvidenceSha256") or "")
                or str(provider_lineage.get("provider_freeze_evidence_sha256") or "")
                != str(rebase.get("providerFreezeEvidenceSha256") or "")
                or str(provider_lineage.get("provider_content_sha256") or "")
                != source_content_sha256
            ):
                raise RuntimeError(f"Top-100 canonical image provider lineage mismatch: {opaque_id}")
            canonical_rebase = rebase
            content_sha256 = str(rebase.get("publicContentSha256") or "").strip().lower()
            requires_derived_freeze = True
            derived_freeze_source_code = f"rebase_{canonical_rebases['receiptSha256'][:16]}"
            derived_freeze_external_id = canonical_rebases["contract"]

        source_file = PUBLIC_ASSETS / f"{content_sha256}.webp"
        if not source_file.is_file():
            raise RuntimeError(f"accepted image asset missing: {source_file}")
        if _file_sha256(source_file) != content_sha256:
            raise RuntimeError(f"accepted image asset hash mismatch: {source_file}")
        if content_sha256 == baseline_content_sha256:
            width = int(image.get("width") or 0)
            height = int(image.get("height") or 0)
        else:
            width = int(freeze.get("width_px") or 0)
            height = int(freeze.get("height_px") or 0)
        if width <= 0 or height <= 0:
            raise RuntimeError(f"accepted image dimensions missing: {opaque_id}")

        if canonical_rebase:
            source_code = str(canonical_rebase["providerSourceCode"])
            source_path = (
                f"{source_code}:{canonical_rebase['providerExternalEntityId']}:"
                f"canonical-image-rebase/{source_content_sha256}->{content_sha256}"
            )
        else:
            source_code = str(freeze.get("source_code") or "accepted-freeze")
            source_path = (
                f"release:{lineage['generationId']}:{source_content_sha256}->{content_sha256}"
            )
        provenance = {
            "contract": "locked-baseline-image-lineage-v2",
            "variantId": variant_id,
            "opaqueId": opaque_id,
            "contentSha256": content_sha256,
            "baselineContentSha256": baseline_content_sha256,
            "sourceContentSha256": source_content_sha256,
            "baselineSha256": baseline_sha256,
            "freezeEvidenceSha256": str(freeze.get("evidence_sha256") or ""),
            "materializationReceiptSha256": lineage[
                "originalMaterializationReceiptSha256"
            ],
            "assetMappingSha256": lineage["mappingSha256"],
            "releaseLockSha256": lineage["releaseLockSha256"],
            "canonicalRebaseReceiptSha256": (
                canonical_rebases["receiptSha256"]
                if canonical_rebase
                else None
            ),
            "providerSourceCode": (
                canonical_rebase.get("providerSourceCode") if canonical_rebase else None
            ),
            "providerExternalEntityId": (
                canonical_rebase.get("providerExternalEntityId") if canonical_rebase else None
            ),
        }
        source_version_sha256 = _canonical_json_sha256(provenance)
        planned.append(
            {
                "variantId": variant_id,
                "opaqueId": opaque_id,
                "contentSha256": content_sha256,
                "sourceContentSha256": source_content_sha256,
                "privateObjectKey": source_file.relative_to(ROOT).as_posix(),
                "width": width,
                "height": height,
                "sourcePath": source_path,
                "sourceVersionSha256": source_version_sha256,
                "acceptedAt": baseline_observed_at,
                "requiresDerivedFreeze": requires_derived_freeze,
                "derivedFreezeSourceCode": derived_freeze_source_code,
                "derivedFreezeExternalId": derived_freeze_external_id,
            }
        )

    assets_written = 0
    pointers_written = 0
    qc_written = 0
    derived_freezes_written = 0
    for row in planned:
        if row["requiresDerivedFreeze"]:
            cur.execute(
                """
                SELECT content_sha256, acceptance_status, evidence_sha256, external_entity_id
                FROM operator_binding_freeze
                WHERE variant_id=%s AND freeze_kind='image' AND source_code=%s
                """,
                (row["variantId"], row["derivedFreezeSourceCode"]),
            )
            existing = cur.fetchone()
            if existing:
                if (
                    str(existing.get("content_sha256") or "") != row["contentSha256"]
                    or str(existing.get("acceptance_status") or "") != "accepted"
                    or str(existing.get("external_entity_id") or "")
                    != row["derivedFreezeExternalId"]
                ):
                    raise RuntimeError(
                        f"derived release image freeze drift: {row['opaqueId']}"
                    )
                # The later canonical-image acceptance phase deliberately
                # replaces evidence_sha256 with the accepted image lineage.
                # Image bytes, source contract and external ID are the replay
                # invariants here; requiring the earlier materialization hash
                # made a successful tidy impossible to replay.
            else:
                cur.execute(
                    """
                    INSERT INTO operator_binding_freeze
                      (variant_id, freeze_kind, source_code, external_entity_id,
                       content_sha256, acceptance_status, actor, evidence_sha256,
                       note, accepted_at)
                    VALUES (%s,'image',%s,%s,%s,'accepted','new_era_db_tidy',%s,%s,%s)
                    """,
                    (
                        row["variantId"], row["derivedFreezeSourceCode"],
                        row["derivedFreezeExternalId"],
                        row["contentSha256"], row["sourceVersionSha256"],
                        f"immutable materialization of accepted image {row['sourceContentSha256']}",
                        row["acceptedAt"],
                    ),
                )
                derived_freezes_written += 1
        cur.execute(
            """
            INSERT INTO market_image_asset
              (variant_id, image_kind, content_sha256, private_object_key, mime_type,
               width_px, height_px, source_version_sha256, captured_at)
            VALUES (%s,'raw_front',%s,%s,'image/webp',%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              private_object_key=VALUES(private_object_key), mime_type='image/webp',
              width_px=VALUES(width_px), height_px=VALUES(height_px),
              captured_at=GREATEST(captured_at,VALUES(captured_at))
            """,
            (
                row["variantId"], row["contentSha256"], row["privateObjectKey"],
                row["width"], row["height"], row["sourceVersionSha256"], row["acceptedAt"],
            ),
        )
        assets_written += 1
        cur.execute(
            """
            SELECT id, source_version_sha256
            FROM market_image_asset
            WHERE variant_id=%s AND image_kind='raw_front' AND content_sha256=%s
            LIMIT 1
            """,
            (row["variantId"], row["contentSha256"]),
        )
        asset = cur.fetchone()
        if not asset:
            raise RuntimeError(f"accepted image asset row missing after import: {row['variantId']}")
        source_version_sha256 = str(asset["source_version_sha256"])
        cur.execute(
            """
            INSERT INTO market_image_source_pointer
              (variant_id, image_kind, remote_url_sha256, source_path,
               source_version_sha256, public_allowed, observed_at)
            VALUES (%s,'raw_front',%s,%s,%s,1,%s)
            ON DUPLICATE KEY UPDATE
              remote_url_sha256=VALUES(remote_url_sha256), source_path=VALUES(source_path),
              public_allowed=1, observed_at=GREATEST(observed_at,VALUES(observed_at))
            """,
            (
                row["variantId"], _value_sha256(row["sourcePath"]), row["sourcePath"],
                source_version_sha256, row["acceptedAt"],
            ),
        )
        pointers_written += 1
        cur.execute(
            """
            INSERT INTO market_image_qc
              (image_asset_id, semantic_match_status, card_number_match, language_match,
               tcg_match, raw_front_confirmed, public_allowed, rejection_reason,
               checked_at, qc_version)
            VALUES (%s,'accepted_freeze',1,1,1,1,1,NULL,%s,'accepted-freeze-v1')
            ON DUPLICATE KEY UPDATE
              semantic_match_status='accepted_freeze', card_number_match=1,
              language_match=1, tcg_match=1, raw_front_confirmed=1,
              public_allowed=1, rejection_reason=NULL,
              checked_at=GREATEST(checked_at,VALUES(checked_at))
            """,
            (int(asset["id"]), row["acceptedAt"]),
        )
        qc_written += 1
    return {
        "variants": len(variants),
        "canonicalImages": len(planned),
        "snkTop100Overrides": len(snk_overrides),
        "snkTop100OverrideRows": snk_overrides,
        "retiredBaselineSnkOverrides": len(retired_snk_overrides),
        "retiredBaselineSnkOverrideRows": retired_snk_overrides,
        "assetsWritten": assets_written,
        "pointersWritten": pointers_written,
        "qcWritten": qc_written,
        "derivedFreezesWritten": derived_freezes_written,
        "baselineSha256": baseline_sha256,
        "materializationReceiptSha256": lineage[
            "originalMaterializationReceiptSha256"
        ],
        "assetMappingSha256": lineage["mappingSha256"],
        "releaseLockSha256": lineage["releaseLockSha256"],
        "assetManifestSha256": lineage["assetManifestSha256"],
        "baselineImageLineageSha256": lineage["baselineImageLineageSha256"],
        "oneTimeDependencyLockSha256": lineage["oneTimeDependencyLockSha256"],
        "pythonRequirementsSha256": lineage["pythonRequirementsSha256"],
        "canonicalRebaseReceiptSha256": canonical_rebases["receiptSha256"],
    }


def sync_026_canonical_image_acceptances(cur) -> dict:
    """Prefer accepted SNK EN lineage; otherwise bind the existing accepted freeze."""

    cur.execute(
        """
        SELECT am.variant_id
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        ORDER BY am.variant_id
        """
    )
    variant_ids = [int(row["variant_id"]) for row in cur.fetchall()]
    if len(variant_ids) != 762:
        raise RuntimeError(f"026 image acceptance requires 762 active variants, got {len(variant_ids)}")
    placeholders = ",".join(["%s"] * len(variant_ids))
    cur.execute(
        f"""
        SELECT l.*
        FROM market_snk_en_storefront_lineage l
        INNER JOIN catalog_printing_identity p ON p.variant_id=l.variant_id
        INNER JOIN operator_strict_source_identity si
          ON si.variant_id=l.variant_id
         AND si.source_code='snkrdunk'
         AND si.external_entity_id=l.exact_item_id
        WHERE l.variant_id IN ({placeholders})
          AND NOT EXISTS (
            SELECT 1 FROM market_snk_en_storefront_lineage newer
            WHERE newer.variant_id=l.variant_id
              AND newer.exact_item_id=l.exact_item_id
              AND (newer.source_observed_at>l.source_observed_at OR
                (newer.source_observed_at=l.source_observed_at AND newer.id>l.id))
          )
        """,
        tuple(variant_ids),
    )
    snk_by_variant = {int(row["variant_id"]): dict(row) for row in cur.fetchall()}
    fallback_by_variant: dict[int, dict] = {}
    fallback_variant_ids = [
        variant_id for variant_id in variant_ids if variant_id not in snk_by_variant
    ]
    if fallback_variant_ids:
        fallback_placeholders = ",".join(["%s"] * len(fallback_variant_ids))
        cur.execute(
            f"""
            SELECT f.variant_id,f.source_code,f.external_entity_id,f.content_sha256,
                   f.evidence_sha256 AS freeze_evidence_sha256,f.accepted_at,
                   a.id AS image_asset_id,a.source_version_sha256,a.captured_at,
                   COALESCE(ptr.source_path,a.private_object_key) AS source_path,
                   COALESCE(ptr.observed_at,a.captured_at) AS source_observed_at
            FROM operator_binding_freeze f
            INNER JOIN market_image_asset a ON a.variant_id=f.variant_id
              AND a.image_kind='raw_front' AND a.content_sha256=f.content_sha256
            INNER JOIN market_image_qc q ON q.image_asset_id=a.id
              AND q.public_allowed=1 AND q.raw_front_confirmed=1
              AND q.card_number_match=1 AND q.language_match=1 AND q.tcg_match=1
            LEFT JOIN market_image_source_pointer ptr ON ptr.variant_id=a.variant_id
              AND ptr.image_kind=a.image_kind AND ptr.source_version_sha256=a.source_version_sha256
            LEFT JOIN catalog_printing_identity pi ON pi.variant_id=f.variant_id
            LEFT JOIN operator_strict_source_identity image_si
              ON image_si.variant_id=f.variant_id
             AND image_si.source_code='snkrdunk'
             AND image_si.external_entity_id=f.external_entity_id
            WHERE f.variant_id IN ({fallback_placeholders}) AND f.freeze_kind='image'
              AND f.acceptance_status='accepted'
              AND f.source_code NOT IN ('snk','snkrdunk','snkrdunk_en')
            ORDER BY f.variant_id,q.checked_at DESC,q.id DESC,
                     f.accepted_at DESC,a.captured_at DESC,a.id DESC
            """,
            tuple(fallback_variant_ids),
        )
        for raw in cur.fetchall():
            row = dict(raw)
            fallback_by_variant.setdefault(int(row["variant_id"]), row)

    now = utc_now_sql()
    snk_selected = 0
    fallback_selected = 0
    missing: list[int] = []
    for variant_id in variant_ids:
        snk = snk_by_variant.get(variant_id)
        fallback = fallback_by_variant.get(variant_id)
        if snk:
            asset_id = int(snk["processed_image_asset_id"])
            lineage_sha = str(snk["lineage_sha256"])
            source_code = "snkrdunk"
            external_id = str(snk["exact_item_id"])
            content_sha = str(snk["processed_content_sha256"])
            storefront_lineage_id = int(snk["id"])
            fallback_path = None
            fallback_version = None
            fallback_observed = None
            snk_selected += 1
        elif fallback:
            asset_id = int(fallback["image_asset_id"])
            lineage_sha = canonical_sha256(
                {
                    "kind": "accepted-image-fallback-v1",
                    "variantId": variant_id,
                    "imageAssetId": asset_id,
                    "contentSha256": fallback["content_sha256"],
                    "sourceVersionSha256": fallback["source_version_sha256"],
                    "freezeEvidenceSha256": fallback["freeze_evidence_sha256"],
                }
            )
            source_code = str(fallback["source_code"] or "accepted-freeze")
            external_id = str(fallback["external_entity_id"] or "")
            content_sha = str(fallback["content_sha256"])
            storefront_lineage_id = None
            fallback_path = str(fallback["source_path"])
            fallback_version = lineage_sha
            fallback_observed = fallback["source_observed_at"]
            fallback_selected += 1
        else:
            missing.append(variant_id)
            continue

        evidence_sha = canonical_sha256(
            {
                "policy": "canonical-026-snk-en-exact-first-v1",
                "variantId": variant_id,
                "assetId": asset_id,
                "contentSha256": content_sha,
                "lineageSha256": lineage_sha,
                "storefrontLineageId": storefront_lineage_id,
            }
        )
        cur.execute(
            """
            SELECT id,lineage_sha256 FROM market_canonical_image_acceptance
            WHERE variant_id=%s AND NOT EXISTS (
              SELECT 1 FROM market_canonical_image_acceptance newer
              WHERE newer.supersedes_acceptance_id=market_canonical_image_acceptance.id
            ) ORDER BY accepted_at DESC,id DESC LIMIT 1
            """,
            (variant_id,),
        )
        current = cur.fetchone()
        supersedes = None if not current or current.get("lineage_sha256") == lineage_sha else int(current["id"])
        cur.execute(
            """
            INSERT INTO market_canonical_image_acceptance
              (variant_id,storefront_lineage_id,image_asset_id,fallback_source_path,
               fallback_source_version_sha256,fallback_source_observed_at,lineage_sha256,
               evidence_sha256,accepted_by,accepted_at,supersedes_acceptance_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """,
            (
                variant_id, storefront_lineage_id, asset_id, fallback_path,
                fallback_version, fallback_observed, lineage_sha, evidence_sha,
                ACCEPTANCE_ACTOR, now, supersedes,
            ),
        )
        acceptance_id = int(cur.lastrowid)
        if acceptance_id <= 0:
            cur.execute(
                "SELECT id FROM market_canonical_image_acceptance WHERE lineage_sha256=%s",
                (lineage_sha,),
            )
            acceptance_id = int((cur.fetchone() or {}).get("id") or 0)
        cur.execute(
            """
            INSERT INTO operator_binding_freeze
              (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
               canonical_image_acceptance_id,accepted_lineage_sha256,acceptance_status,
               actor,evidence_sha256,note,accepted_at)
            VALUES (%s,'image',%s,%s,%s,%s,%s,'accepted',%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              external_entity_id=VALUES(external_entity_id),content_sha256=VALUES(content_sha256),
              canonical_image_acceptance_id=VALUES(canonical_image_acceptance_id),
              accepted_lineage_sha256=VALUES(accepted_lineage_sha256),acceptance_status='accepted',
              actor=VALUES(actor),evidence_sha256=VALUES(evidence_sha256),
              note=VALUES(note),accepted_at=VALUES(accepted_at)
            """,
            (
                variant_id, source_code, external_id, content_sha, acceptance_id,
                lineage_sha, ACCEPTANCE_ACTOR, evidence_sha,
                "026 canonical SNK EN preference or accepted frozen image", now,
            ),
        )
    if missing or snk_selected + fallback_selected != 762:
        raise RuntimeError(f"026 canonical image acceptance incomplete: {missing[:20]}")
    return {
        "accepted": 762,
        "snkEnSelected": snk_selected,
        "acceptedFallback": fallback_selected,
    }


def sync_026_metric_history_acceptances(cur) -> dict:
    """Accept only exact raw observations; never promote a positive aggregate."""

    now = utc_now_sql()
    inserted: dict[str, int] = {}
    cur.execute(
        """
        INSERT INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT p.variant_id,'psa10_price','market_price_observation',p.id,
               CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                    ELSE p.source_code END,
               p.source_external_entity_id,p.observed_date,p.effective_at,p.payload_sha256,
               si.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-language-routed-exact-psa10-price-v1',p.id,p.variant_id,
                 CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                      ELSE p.source_code END,
                 p.source_external_entity_id,p.payload_sha256,si.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','psa10_price',p.id,p.variant_id,
                 CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
                      ELSE p.source_code END,
                 p.source_external_entity_id,p.payload_sha256,si.evidence_sha256),256),
               %s,%s
        FROM market_price_observation p
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
        INNER JOIN operator_strict_source_identity si ON si.variant_id=p.variant_id
          AND si.source_code=CASE WHEN p.source_code IN ('snk','snk_psa10')
                                  THEN 'snkrdunk' ELSE p.source_code END
          AND si.external_entity_id=p.source_external_entity_id
        INNER JOIN market_source_observation so ON so.id=p.source_observation_id
          AND so.source_code=p.source_code AND so.external_entity_id=p.source_external_entity_id
          AND so.payload_sha256=p.payload_sha256 AND so.observed_date=p.observed_date
        WHERE p.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
          AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
          AND (
            (p.source_code IN ('snkrdunk','snk_psa10','snk')
             AND so.observation_kind='psa10_reference_price')
            OR
            (p.source_code='pricecharting'
             AND p.source_priority=95
             AND so.observation_kind='psa10_price_guide'
             AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
             AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%%'
             AND p.source_external_entity_id REGEXP '^[0-9]+$'
             AND (
               (JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last')
               OR
               (JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_local_history_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_history_v1'
                AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.series')
             ))
          )
          AND p.metric_status='ready' AND p.price_usd>0
          AND p.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        ON DUPLICATE KEY UPDATE
          source_code=VALUES(source_code),external_entity_id=VALUES(external_entity_id),
          observed_date=VALUES(observed_date),source_effective_at=VALUES(source_effective_at),
          source_payload_sha256=VALUES(source_payload_sha256),
          identity_evidence_sha256=VALUES(identity_evidence_sha256),
          acceptance_evidence_sha256=VALUES(acceptance_evidence_sha256),
          lineage_sha256=VALUES(lineage_sha256),accepted_by=VALUES(accepted_by),
          accepted_at=VALUES(accepted_at)
        """,
        (ACCEPTANCE_ACTOR, now),
    )
    inserted["psa10Price"] = int(cur.rowcount)

    cur.execute(
        """
        INSERT IGNORE INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT pop.variant_id,'psa10_population','market_grader_population_observation',
               pop.id,'gemrate',pop.external_entity_id,pop.observed_date,pop.effective_at,
               pop.payload_sha256,si.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-exact-gemrate-psa10-pop-v1',pop.id,pop.variant_id,
                 pop.external_entity_id,pop.payload_sha256,si.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','psa10_population',pop.id,pop.variant_id,
                 pop.external_entity_id,pop.payload_sha256,si.evidence_sha256),256),
               %s,%s
        FROM market_grader_population_observation pop
        INNER JOIN market_universe_member am ON am.variant_id=pop.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN operator_strict_source_identity si ON si.variant_id=pop.variant_id
          AND si.source_code='gemrate' AND si.external_entity_id=pop.external_entity_id
        WHERE pop.source_code='gemrate' AND UPPER(pop.grader_code)='PSA'
          AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND pop.estimated=0 AND pop.top_grade_population>=0
          AND pop.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """,
        (ACCEPTANCE_ACTOR, now),
    )
    inserted["psa10Population"] = int(cur.rowcount)

    cur.execute(
        """
        INSERT IGNORE INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT s.variant_id,'psa10_sale','market_sale_observation',s.id,
               CASE WHEN s.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE s.source_code END,
               s.external_entity_id,DATE(s.sold_at),s.sold_at,s.source_payload_sha256,
               si.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-exact-psa10-sale-v1',s.id,s.variant_id,
                 s.source_code,s.external_entity_id,s.source_payload_sha256,si.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','psa10_sale',s.id,s.variant_id,
                 s.source_code,s.external_entity_id,s.source_payload_sha256,si.evidence_sha256),256),
               %s,%s
        FROM market_sale_observation s
        INNER JOIN market_universe_member am ON am.variant_id=s.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN operator_strict_source_identity si ON si.variant_id=s.variant_id
          AND si.source_code=CASE WHEN s.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE s.source_code END
          AND si.external_entity_id=s.external_entity_id
        WHERE s.sold_at IS NOT NULL AND s.quantity>0 AND s.transaction_value_usd>0
          AND UPPER(s.grader_code)='PSA'
          AND UPPER(REPLACE(s.grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND s.timestamp_quality IN ('exact','date','timestamp','exact_date','relative_resolved','relative_subday')
          AND s.source_payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """,
        (ACCEPTANCE_ACTOR, now),
    )
    inserted["psa10Sales"] = int(cur.rowcount)

    cur.execute(
        """
        INSERT IGNORE INTO market_metric_history_acceptance
          (variant_id,metric_kind,source_record_type,source_record_id,source_code,
           external_entity_id,observed_date,source_effective_at,source_payload_sha256,
           identity_evidence_sha256,acceptance_evidence_sha256,lineage_sha256,
           accepted_by,accepted_at)
        SELECT a.variant_id,'verified_zero_sales','market_daily_sales_aggregate',a.id,
               exact.source_code,exact.external_entity_id,a.observed_date,r.completed_at,
               a.payload_sha256,exact.evidence_sha256,
               SHA2(CONCAT_WS('|','accept-verified-zero-sales-v1',a.id,a.variant_id,
                 exact.source_code,exact.external_entity_id,a.payload_sha256,exact.evidence_sha256),256),
               SHA2(CONCAT_WS('|','metric-history-v1','verified_zero_sales',a.id,a.variant_id,
                 exact.source_code,exact.external_entity_id,a.payload_sha256,exact.evidence_sha256),256),
               %s,%s
        FROM market_daily_sales_aggregate a
        INNER JOIN market_ingest_run r ON r.id=a.run_id AND r.status IN ('complete','completed')
        INNER JOIN market_universe_member am ON am.variant_id=a.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN (
          SELECT si.variant_id,si.source_code,MIN(si.external_entity_id) AS external_entity_id,
                 MIN(si.evidence_sha256) AS evidence_sha256
          FROM operator_strict_source_identity si
          GROUP BY si.variant_id,si.source_code HAVING COUNT(*)=1
        ) exact ON exact.variant_id=a.variant_id
          AND exact.source_code=CASE WHEN a.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE a.source_code END
        WHERE a.sales_count=0 AND a.coverage_status='complete'
          AND a.payload_sha256 REGEXP '^[0-9a-f]{64}$'
          AND exact.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """,
        (ACCEPTANCE_ACTOR, now),
    )
    inserted["verifiedZeroSales"] = int(cur.rowcount)
    return {"inserted": inserted, "totalInserted": sum(inserted.values())}


def sync_026_current_metric_acceptances(cur) -> dict:
    # Keep the winner policy deterministic without asking MySQL 5.7 to run two
    # correlated anti-joins over the full price x population history product.
    # Read each accepted history lane once in winner order, then take the first
    # row per active variant in Python. This preserves the exact route/date/
    # evidence ordering while keeping runtime linear in the accepted rows.
    cur.execute(
        """
        SELECT p.variant_id,p.price_history_acceptance_id,p.price_usd,
               p.price_effective_at,p.price_source_observed_at,
               p.observed_date AS price_observed_date,
               p.price_source_code,p.price_route_priority,p.price_lineage_sha256
        FROM operator_eligible_accepted_psa10_price_history p
        INNER JOIN market_universe_member am ON am.variant_id=p.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        """
    )
    latest_price: dict[int, dict] = {}
    latest_price_keys: dict[int, tuple] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        winner_key = (
            -int(row["price_route_priority"]),
            row["price_observed_date"],
            row["price_effective_at"],
            row["price_source_observed_at"],
            int(row["price_history_acceptance_id"]),
        )
        if variant_id not in latest_price_keys or winner_key > latest_price_keys[variant_id]:
            latest_price_keys[variant_id] = winner_key
            latest_price[variant_id] = row

    cur.execute(
        """
        SELECT h.id AS population_history_acceptance_id,
               pop.variant_id,pop.observed_date AS population_observed_date,
               pop.top_grade_population AS psa10_population,
               pop.effective_at AS population_effective_at,
               h.lineage_sha256 AS population_lineage_sha256
        FROM market_metric_history_acceptance h
        INNER JOIN market_grader_population_observation pop
          ON h.source_record_type='market_grader_population_observation'
         AND h.source_record_id=pop.id
         AND h.variant_id=pop.variant_id
         AND h.observed_date=pop.observed_date
         AND h.source_effective_at=pop.effective_at
         AND h.source_code=pop.source_code
         AND h.external_entity_id=pop.external_entity_id
         AND h.source_payload_sha256=pop.payload_sha256
        INNER JOIN catalog_printing_identity pi ON pi.variant_id=pop.variant_id
        INNER JOIN operator_strict_source_identity si
          ON si.variant_id=pop.variant_id
         AND si.source_code='gemrate'
         AND si.external_entity_id=pop.external_entity_id
        INNER JOIN market_universe_member am ON am.variant_id=pop.variant_id
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        WHERE h.metric_kind='psa10_population'
          AND h.source_code='gemrate' AND pop.source_code='gemrate'
          AND UPPER(pop.grader_code)='PSA'
          AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
          AND pop.estimated=0
          AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
          AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
          AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
          AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
        """
    )
    latest_population: dict[int, dict] = {}
    latest_population_keys: dict[int, tuple] = {}
    for raw in cur.fetchall():
        row = dict(raw)
        variant_id = int(row["variant_id"])
        winner_key = (
            row["population_observed_date"],
            row["population_effective_at"],
            int(row["population_history_acceptance_id"]),
        )
        if (
            variant_id not in latest_population_keys
            or winner_key > latest_population_keys[variant_id]
        ):
            latest_population_keys[variant_id] = winner_key
            latest_population[variant_id] = row

    cur.execute(
        """
        SELECT am.variant_id FROM market_universe_member am
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        ORDER BY am.variant_id
        """
    )
    active_variant_ids = [int(row["variant_id"]) for row in cur.fetchall()]
    latest: dict[int, dict] = {}
    for variant_id in active_variant_ids:
        price = latest_price.get(variant_id)
        population = latest_population.get(variant_id)
        if price is not None and population is not None:
            latest[variant_id] = {**price, **population}
    if len(latest) != 762:
        missing = [variant_id for variant_id in active_variant_ids if variant_id not in latest]
        raise RuntimeError(
            f"canonical language-routed price/GemRate POP coverage is {len(latest)}/762; "
            f"missing={missing[:40]}"
        )

    ranked: list[tuple[int, dict, Decimal]] = []
    for variant_id, row in latest.items():
        cap = Decimal(str(row["price_usd"])) * Decimal(int(row["psa10_population"]))
        ranked.append((variant_id, row, cap))
    ranked.sort(key=lambda item: (-item[2], item[0]))
    ranking_generation_sha = canonical_sha256(
        [
            {
                "variantId": variant_id,
                "priceAcceptanceId": int(row["price_history_acceptance_id"]),
                "populationAcceptanceId": int(row["population_history_acceptance_id"]),
                "marketCapUsd": format(cap, "f"),
            }
            for variant_id, row, cap in ranked
        ]
    )
    now = utc_now_sql()
    for rank, (variant_id, row, cap) in enumerate(ranked, start=1):
        metric_lineage_sha = canonical_sha256(
            {
                "kind": "canonical-current-metric-v1",
                "variantId": variant_id,
                "priceHistoryAcceptanceId": int(row["price_history_acceptance_id"]),
                "populationHistoryAcceptanceId": int(row["population_history_acceptance_id"]),
                "marketCapUsd": format(cap, "f"),
                "canonicalMarketRank": rank,
                "rankingGenerationSha256": ranking_generation_sha,
            }
        )
        evidence_sha = canonical_sha256(
            {
                "policy": "language-routed-exact-price-times-exact-gemrate-pop-v1",
                "priceLineageSha256": row["price_lineage_sha256"],
                "populationLineageSha256": row["population_lineage_sha256"],
                "metricLineageSha256": metric_lineage_sha,
            }
        )
        cur.execute(
            """
            SELECT id,metric_lineage_sha256 FROM market_canonical_metric_acceptance
            WHERE variant_id=%s AND NOT EXISTS (
              SELECT 1 FROM market_canonical_metric_acceptance newer
              WHERE newer.supersedes_acceptance_id=market_canonical_metric_acceptance.id
            ) ORDER BY accepted_at DESC,id DESC LIMIT 1
            """,
            (variant_id,),
        )
        current = cur.fetchone()
        supersedes = None if not current or current.get("metric_lineage_sha256") == metric_lineage_sha else int(current["id"])
        cur.execute(
            """
            INSERT INTO market_canonical_metric_acceptance
              (variant_id,price_history_acceptance_id,population_history_acceptance_id,
               market_cap_usd,canonical_market_rank,ranking_generation_sha256,
               metric_lineage_sha256,evidence_sha256,accepted_by,accepted_at,
               supersedes_acceptance_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """,
            (
                variant_id, int(row["price_history_acceptance_id"]),
                int(row["population_history_acceptance_id"]), cap, rank,
                ranking_generation_sha, metric_lineage_sha, evidence_sha,
                ACCEPTANCE_ACTOR, now, supersedes,
            ),
        )
    return {
        "accepted": len(ranked),
        "rankingGenerationSha256": ranking_generation_sha,
        "top300": [variant_id for variant_id, _, _ in ranked[:300]],
    }


def quarantine_banned_prices(cur) -> dict:
    # Do not delete evidence; mark banned for composition.
    cur.execute(
        """
        UPDATE market_price_observation
        SET metric_status = 'banned_g10_kline'
        WHERE source_code IN ({})
          AND metric_status <> 'banned_g10_kline'
        """.format(",".join(["%s"] * len(BANNED_PRICE_SOURCES))),
        BANNED_PRICE_SOURCES,
    )
    price_marked = cur.rowcount
    cur.execute(
        """
        UPDATE market_source_observation
        SET observation_kind = 'quarantine_g10_kline_daily'
        WHERE observation_kind = 'g10_kline_daily'
        """
    )
    obs_marked = cur.rowcount
    return {"priceRowsMarked": int(price_marked), "sourceObservationRowsMarked": int(obs_marked)}


def ensure_banned_policy(cur) -> None:
    cur.execute(
        """
        INSERT INTO market_banned_source_policy (source_code, reason_code, policy, note, effective_at)
        VALUES ('g10_kline', 'banned_series', 'ignore_for_price', 'New-era ban', UTC_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE policy='ignore_for_price', reason_code='banned_series'
        """
    )


def warehouse_status(cur) -> dict:
    cur.execute("SELECT version_code FROM cardz_schema_version ORDER BY version_code")
    versions = [r["version_code"] for r in cur.fetchall()]
    cur.execute("SELECT source_code, policy, reason_code FROM market_banned_source_policy")
    banned = list(cur.fetchall())
    cur.execute(
        "SELECT COUNT(*) n FROM market_price_observation WHERE source_code='g10_kline' AND metric_status='banned_g10_kline'"
    )
    banned_prices = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        "SELECT COUNT(*) n FROM market_price_observation WHERE source_code='g10_kline' AND metric_status<>'banned_g10_kline'"
    )
    live_kline_prices = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        "SELECT COUNT(*) n FROM market_source_observation WHERE observation_kind IN ('g10_kline_daily','quarantine_g10_kline_daily')"
    )
    kline_obs = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute("SELECT COUNT(*) n FROM market_source_warehouse")
    warehouse_n = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT
          SUM(CASE WHEN metric_status='banned_g10_kline' THEN 0 ELSE 1 END) AS active_price_rows,
          COUNT(DISTINCT CASE WHEN metric_status='banned_g10_kline' THEN NULL ELSE variant_id END) AS active_price_variants
        FROM market_price_observation
        """
    )
    price_stats = cur.fetchone() or {}
    cur.execute(
        """
        SELECT COUNT(*) n FROM operator_binding_freeze WHERE acceptance_status='accepted' AND freeze_kind='identity'
        """
    )
    frozen_id = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul
          ON ul.id=am.universe_lock_id AND ul.is_current=1
        """
    )
    active_members = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul
          ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_printing_identity p ON p.variant_id=am.variant_id
        INNER JOIN operator_binding_freeze f
          ON f.variant_id=p.variant_id
         AND f.freeze_kind='identity' AND f.source_code=''
         AND f.external_entity_id='' AND f.acceptance_status='accepted'
         AND f.content_sha256=p.canonical_printing_sha256
         AND f.evidence_sha256=p.evidence_sha256
        WHERE p.identity_status IN ('confirmed','canonical')
          AND TRIM(p.collector_number)<>'' AND TRIM(p.set_code)<>''
          AND TRIM(p.edition_code)<>'' AND TRIM(p.finish_code)<>''
          AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
          AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
        """
    )
    identity_complete = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN market_canonical_metric_acceptance m ON m.variant_id=am.variant_id
        WHERE NOT EXISTS (
          SELECT 1 FROM market_canonical_metric_acceptance newer
          WHERE newer.supersedes_acceptance_id=m.id
        )
        """
    )
    metric_complete = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN market_canonical_image_acceptance i ON i.variant_id=am.variant_id
        WHERE NOT EXISTS (
          SELECT 1 FROM market_canonical_image_acceptance newer
          WHERE newer.supersedes_acceptance_id=i.id
        )
        """
    )
    image_complete = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM market_universe_member am
        INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
        INNER JOIN catalog_official_name_acceptance n ON n.variant_id=am.variant_id
        WHERE NOT EXISTS (
          SELECT 1 FROM catalog_official_name_acceptance newer
          WHERE newer.supersedes_acceptance_id=n.id
        )
        """
    )
    official_name_complete = int((cur.fetchone() or {}).get("n") or 0)
    cur.execute(
        """
        SELECT COUNT(*) AS n
        FROM (
          SELECT am.variant_id
          FROM market_universe_member am
          INNER JOIN market_universe_lock ul ON ul.id=am.universe_lock_id AND ul.is_current=1
          INNER JOIN catalog_variant_locale l ON l.variant_id=am.variant_id
          GROUP BY am.variant_id
          HAVING COUNT(DISTINCT l.locale_code)=%s
        ) complete_locale
        """,
        (len(LOCALES),),
    )
    locale_complete = int((cur.fetchone() or {}).get("n") or 0)
    ready_counts = (
        identity_complete,
        metric_complete,
        image_complete,
        locale_complete,
        official_name_complete,
    )
    product_ready = active_members if all(n == active_members for n in ready_counts) else 0
    projection = {
        "active_members": active_members,
        "product_ready": product_ready,
        "identity_complete": identity_complete,
        "metric_complete": metric_complete,
        "image_complete": image_complete,
        "locale_complete": locale_complete,
        "official_name_complete": official_name_complete,
        "canonical_metric_complete": metric_complete,
        "canonical_image_complete": image_complete,
        "canonical_rank_complete": metric_complete,
        "current_price_lineage": metric_complete,
    }
    return {
        "schemaVersions": versions,
        "has022": "022" in versions,
        "has023": "023" in versions,
        "has024": "024" in versions,
        "has025": "025" in versions,
        "has026": "026" in versions,
        "has027": "027" in versions,
        "has028": "028" in versions,
        "has029": "029" in versions,
        "bannedPolicies": banned,
        "g10Kline": {
            "priceRowsBanned": banned_prices,
            "priceRowsStillActive": live_kline_prices,
            "sourceObservationRows": kline_obs,
        },
        "warehouseRows": warehouse_n,
        "activePriceRows": price_stats.get("active_price_rows"),
        "activePriceVariants": price_stats.get("active_price_variants"),
        "frozenIdentityRows": frozen_id,
        "activeProjection": projection,
    }


def prepare_source_identities() -> int:
    load_env()
    conn = db()
    original_pc_map = CANONICAL_PC_MAP.read_bytes()
    pc_map_written = False
    db_committed = False
    try:
        cur = conn.cursor()
        cur.execute("SELECT CONNECTION_ID() AS connection_id")
        connection_id = int((cur.fetchone() or {}).get("connection_id") or 0)
        cur.execute(
            "SELECT COUNT(*) AS n FROM cardz_schema_version WHERE version_code IN ('026','027','028')"
        )
        if int((cur.fetchone() or {}).get("n") or 0) != 3:
            raise RuntimeError("026 source identity preparation requires migrations 026-028")
        phase("canonical-market-source-identity-prepare-start", mysqlConnectionId=connection_id)
        source_repairs = sync_026_market_source_identity_repairs(cur)
        manifest = _read_json(CANONICAL_MARKET_SOURCE_IDENTITY_REPAIRS)
        pc_map = _sync_026_canonical_pc_map(
            manifest, str(source_repairs["manifestSha256"])
        )
        pc_map_written = bool(pc_map["changed"])
        conn.commit()
        db_committed = True
        report = {
            "asOf": utc_now(),
            "action": "prepare-canonical-market-source-identities-026",
            "committed": True,
            "canonicalMarketSourceIdentityRepairs": source_repairs,
            "canonicalPcMap": pc_map,
        }
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "canonical_market_source_identity_repairs_026.json"
        temporary = path.with_name(f".{path.name}.{connection_id}.next")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        phase(
            "canonical-market-source-identity-prepare-finish",
            repaired=len(source_repairs["repaired"]),
            rejected=len(source_repairs["rejected"]),
            pcMapSha256=pc_map["afterSha256"],
        )
        return 0
    except Exception:
        conn.rollback()
        if pc_map_written and not db_committed:
            temporary = CANONICAL_PC_MAP.with_name(
                f".{CANONICAL_PC_MAP.name}.rollback.next"
            )
            temporary.write_bytes(original_pc_map)
            temporary.replace(CANONICAL_PC_MAP)
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CARDZ new-era canonical DB tidy")
    parser.add_argument(
        "--prepare-source-identities",
        action="store_true",
        help="commit only the exact 026 source-owner/map repairs needed before collection",
    )
    args = parser.parse_args(argv)
    if args.prepare_source_identities:
        return prepare_source_identities()
    load_env()
    conn = db()
    try:
        cur = conn.cursor()
        cur.execute("SET SESSION MAX_EXECUTION_TIME=120000")
        cur.execute("SELECT CONNECTION_ID() AS connection_id")
        connection_id = int((cur.fetchone() or {}).get("connection_id") or 0)
        phase("db-tidy-connected", mysqlConnectionId=connection_id, maxSelectMs=120000)
        phase("canonical-identity-freeze-sync-start")
        identity_freezes = sync_026_canonical_identity_freezes(cur)
        phase("canonical-identity-freeze-sync-finish", **identity_freezes)
        phase("official-name-sync-start")
        official_names = sync_official_names(cur)
        phase("official-name-sync-finish", **official_names)
        phase("metric-history-acceptance-start")
        metric_history = sync_026_metric_history_acceptances(cur)
        phase("metric-history-acceptance-finish", **metric_history)
        phase("current-metric-acceptance-start")
        current_metrics = sync_026_current_metric_acceptances(cur)
        phase(
            "current-metric-acceptance-finish",
            accepted=current_metrics["accepted"],
            rankingGenerationSha256=current_metrics["rankingGenerationSha256"],
        )
        phase("projection-gate-start")
        status = warehouse_status(cur)
        phase("projection-gate-finish", activeProjection=status.get("activeProjection"))
        report = {
            "asOf": utc_now(),
            "action": "db-tidy-history-rebuild",
            "canonicalIdentityFreezes": identity_freezes,
            "officialNames": official_names,
            "metricHistoryAcceptance": metric_history,
            "canonicalMetricAcceptance": current_metrics,
            "status": status,
        }
        projection = status.get("activeProjection") or {}
        gate_failed = (
            not status["has024"]
            or not status["has025"]
            or not status["has026"]
            or not status["has027"]
            or not status["has028"]
            or not status["has029"]
            or status["g10Kline"]["priceRowsStillActive"] > 0
            or int(projection.get("active_members") or 0) != 762
            or int(projection.get("product_ready") or 0) != 762
            or int(projection.get("identity_complete") or 0) != 762
            or int(projection.get("metric_complete") or 0) != 762
            or int(projection.get("image_complete") or 0) != 762
            or int(projection.get("locale_complete") or 0) != 762
            or int(projection.get("official_name_complete") or 0) != 762
            or int(projection.get("canonical_metric_complete") or 0) != 762
            or int(projection.get("canonical_image_complete") or 0) != 762
            or int(projection.get("canonical_rank_complete") or 0) != 762
            or int(projection.get("current_price_lineage") or 0) != 762
        )
        if gate_failed:
            conn.rollback()
            return 2
        conn.commit()
        phase("db-tidy-commit", acceptedVariants=762)
        report["committed"] = True
        report["committedAt"] = utc_now()
        OUT.mkdir(parents=True, exist_ok=True)
        path = OUT / "db_tidy_new_era.json"
        temporary = path.with_name(f".{path.name}.{connection_id}.next")
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(path)
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        print(f"wrote {path}")
        return 0
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
