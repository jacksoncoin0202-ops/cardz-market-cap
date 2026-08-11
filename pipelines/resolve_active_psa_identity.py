#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolve the active CardzMC PSA identity blockers without conflating provenance.

GemRate source coverage comes from a positive provider-native PSA 10 population
observation. Literal canonical identity comes independently from the unique PSA
row in the exact GemRate raw payload. The apply command writes both immutable
acceptances in one transaction and never restores non-GemRate market evidence.
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
from psa_identity_repair import (  # noqa: E402
    canonical_json,
    collector_compatible,
    derive_language,
    load_psa_raw,
    parse_time,
    set_compatible,
    sha256_json,
)
from qualified_pool_operator import db, load_env  # noqa: E402

CONTRACT = "active-psa-identity-resolution-035-v1"
ACTOR = "resolve_active_psa_identity_035"
OUT_ROOT = ROOT / "data" / "runtime" / "operator" / "psa-identity-repair-035"
ID_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def sql_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def canonical_gemrate_id(value: Any) -> str | None:
    text = str(value or "").strip().casefold()
    if text.startswith("gemrate:"):
        text = text.split(":", 1)[1]
    return text if ID_PATTERN.fullmatch(text) else None


def printing_sha(printing: dict[str, Any]) -> str:
    identity = {
        key: str(printing.get(key) or "").strip().casefold()
        for key in (
            "tcg_code", "card_language", "set_name", "set_code", "collector_number",
            "printing_code", "rarity_code", "edition_code", "parallel_code", "finish_code",
        )
    }
    return hashlib.sha256(canonical_json(identity).encode("utf-8")).hexdigest()


def fetch_active(cur: Any) -> list[dict[str, Any]]:
    cur.execute(
        """
        SELECT v.id AS variant_id,v.opaque_id,v.canonical_name,
               v.card_language AS variant_language,v.set_name AS variant_set_name,
               v.identity_status AS variant_identity_status,
               v.collector_number AS variant_collector_number,
               p.tcg_code,p.card_language,p.set_name,p.set_code,p.printing_code,
               p.rarity_code,p.collector_number,p.edition_code,p.parallel_code,
               p.finish_code,p.canonical_printing_sha256,p.identity_status AS printing_identity_status
        FROM market_universe_member m
        INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
        INNER JOIN catalog_variant v ON v.id=m.variant_id
        INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
        ORDER BY v.id
        """
    )
    return [dict(row) for row in cur.fetchall()]


def fetch_current_psa(cur: Any, active_ids: list[int]) -> dict[int, dict[str, Any]]:
    placeholders = ",".join(["%s"] * len(active_ids))
    cur.execute(
        f"""
        SELECT a.* FROM catalog_psa_identity_acceptance a
        WHERE a.variant_id IN ({placeholders})
          AND NOT EXISTS (
            SELECT 1 FROM catalog_psa_identity_acceptance newer
            WHERE newer.supersedes_acceptance_id=a.id
          )
        ORDER BY a.variant_id,a.accepted_at DESC,a.id DESC
        """,
        active_ids,
    )
    result: dict[int, dict[str, Any]] = {}
    for row in cur.fetchall():
        result.setdefault(int(row["variant_id"]), dict(row))
    return result


def fetch_population(cur: Any, active_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    placeholders = ",".join(["%s"] * len(active_ids))
    cur.execute(
        f"""
        SELECT id,variant_id,external_entity_id,top_grade_population,effective_at,
               payload_sha256,observed_date
        FROM market_grader_population_observation
        WHERE variant_id IN ({placeholders})
          AND source_code='gemrate'
          AND LOWER(grader_code)='psa'
          AND top_grade_label='10'
          AND top_grade_population>0
          AND estimated=0
        ORDER BY variant_id,effective_at DESC,id DESC
        """,
        active_ids,
    )
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for source in cur.fetchall():
        row = dict(source)
        gemrate_id = canonical_gemrate_id(row["external_entity_id"])
        if gemrate_id:
            row["gemrate_id"] = gemrate_id
            result[int(row["variant_id"])].append(row)
    return result


def fetch_bindings(cur: Any, active_ids: list[int]) -> dict[int, set[str]]:
    placeholders = ",".join(["%s"] * len(active_ids))
    cur.execute(
        f"""SELECT variant_id,external_entity_id
            FROM catalog_source_identity
            WHERE variant_id IN ({placeholders}) AND source_code='gemrate'""",
        active_ids,
    )
    result: dict[int, set[str]] = defaultdict(set)
    for row in cur.fetchall():
        gemrate_id = canonical_gemrate_id(row["external_entity_id"])
        if gemrate_id:
            result[int(row["variant_id"])].add(gemrate_id)
    return result


def select_candidate(
    variant: dict[str, Any],
    current: dict[str, Any] | None,
    populations: list[dict[str, Any]],
    binding_ids: set[str],
) -> dict[str, Any]:
    observations: dict[str, dict[str, Any]] = {}
    for observation in populations:
        observations.setdefault(str(observation["gemrate_id"]), observation)
    ordered_ids: list[str] = []
    current_id = canonical_gemrate_id((current or {}).get("gemrate_id"))
    if current_id:
        ordered_ids.append(current_id)
    ordered_ids.extend(str(row["gemrate_id"]) for row in populations)
    ordered_ids.extend(sorted(binding_ids))

    seen: set[str] = set()
    considered: list[dict[str, Any]] = []
    for gemrate_id in ordered_ids:
        if gemrate_id in seen:
            continue
        seen.add(gemrate_id)
        observation = observations.get(gemrate_id)
        if not observation:
            considered.append({"gemrateId": gemrate_id, "reason": "positive_psa10_population_missing"})
            continue
        raw = load_psa_raw(gemrate_id)
        if raw.get("error"):
            considered.append({"gemrateId": gemrate_id, "reason": raw.get("reason")})
            continue
        psa = raw["psa"]
        language, markers = derive_language(psa, str(variant["tcg_code"]))
        if language is None:
            considered.append({"gemrateId": gemrate_id, "reason": f"language_ambiguous:{markers}"})
            continue
        unnumbered_don = (
            str(variant["tcg_code"]) == "one-piece"
            and str(psa.get("card_number") or "") == ""
            and "don!! card" in str(psa.get("description") or "").casefold()
            and str(variant.get("collector_number") or "").casefold().startswith("opcd-")
            and str(variant.get("set_code") or "").casefold() in str(psa.get("set_name") or "").casefold().replace("-", "")
        )
        if not unnumbered_don and not collector_compatible(psa.get("card_number"), variant.get("collector_number"), str(variant["tcg_code"])):
            considered.append({"gemrateId": gemrate_id, "reason": "collector_number_conflict"})
            continue
        return {
            "gemrateId": gemrate_id,
            "population": observation,
            "rawEvidence": {key: value for key, value in raw.items() if key != "psa"},
            "psa": {key: psa.get(key) for key in ("description", "year", "set_name", "card_number", "parallel", "set_url")},
            "derivedLanguage": language,
            "languageMarkers": markers,
            "selection": "existing_literal_acceptance" if gemrate_id == current_id else "latest_positive_population_with_exact_raw",
        }
    raise RuntimeError(
        f"active variant {variant['variant_id']} has no resolvable GemRate PSA identity: "
        + canonical_json(considered)
    )


def build_plan(connection: Any) -> dict[str, Any]:
    cur = connection.cursor()
    active = fetch_active(cur)
    active_ids = [int(row["variant_id"]) for row in active]
    current = fetch_current_psa(cur, active_ids)
    populations = fetch_population(cur, active_ids)
    bindings = fetch_bindings(cur, active_ids)
    rows: list[dict[str, Any]] = []
    for variant in active:
        variant_id = int(variant["variant_id"])
        selected = select_candidate(variant, current.get(variant_id), populations.get(variant_id, []), bindings.get(variant_id, set()))
        psa = selected["psa"]
        before = {key: variant.get(key) for key in (
            "canonical_name", "variant_language", "variant_set_name", "tcg_code", "card_language",
            "set_name", "set_code", "printing_code", "rarity_code", "collector_number",
            "edition_code", "parallel_code", "finish_code", "canonical_printing_sha256",
        )}
        after = dict(before)
        # canonical_name is the DISPLAY name; catalog_psa_identity_acceptance
        # .psa_description below keeps the PSA literal byte-for-byte and stays
        # the provenance authority validator034 re-derives from the raw payload.
        # They differ in exactly one place: the PSA label stops at the bare
        # numerator ("... Special Art Rare 110") and the display carries the
        # collector number we already hold ("110/80"). Splitting the two is the
        # point -- one column was doing both jobs, so every provenance check
        # forced the display back to truncated. canonical_name is not a
        # printing_sha input, so nothing here moves a hash.
        # variant_collector_number, NOT after["collector_number"]: the latter is
        # the printing tuple's frozen bare numerator that feeds printing_sha().
        after["canonical_name"] = complete_collector_tail(
            psa["description"], variant.get("variant_collector_number")
        )
        after["variant_language"] = selected["derivedLanguage"]
        after["card_language"] = selected["derivedLanguage"]
        if not set_compatible(psa, variant):
            after["set_name"] = psa["set_name"]
            after["variant_set_name"] = psa["set_name"]
        if psa.get("parallel") and str(before.get("parallel_code") or "").casefold() in {"", "unknown"}:
            after["parallel_code"] = psa["parallel"]
        after["canonical_printing_sha256"] = printing_sha(after)
        row = {
            "variantId": variant_id,
            "opaqueId": variant["opaque_id"],
            "before": before,
            "after": after,
            "currentPsaAcceptanceId": int(current[variant_id]["id"]) if variant_id in current else None,
            **selected,
        }
        rows.append(row)

    proposed_owners: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        proposed_owners[str(row["after"]["canonical_printing_sha256"])].append(int(row["variantId"]))
    duplicates = {key: value for key, value in proposed_owners.items() if len(value) > 1}
    if duplicates:
        raise RuntimeError("active resolution creates duplicate canonical printing hashes: " + canonical_json(duplicates))
    proposed = {key: owners[0] for key, owners in proposed_owners.items()}
    placeholders = ",".join(["%s"] * len(active_ids))
    cur.execute(
        f"""SELECT variant_id,canonical_printing_sha256 FROM catalog_printing_identity
            WHERE variant_id NOT IN ({placeholders})""",
        active_ids,
    )
    for existing in cur.fetchall():
        owner = proposed.get(str(existing["canonical_printing_sha256"]))
        if owner:
            raise RuntimeError(f"active resolution collides with variant {existing['variant_id']}: {owner}")

    counters = Counter()
    for row in rows:
        before, after = row["before"], row["after"]
        counters["canonicalNameChanges"] += before["canonical_name"] != after["canonical_name"]
        counters["languageChanges"] += before["card_language"] != after["card_language"]
        counters["setNameChanges"] += before["set_name"] != after["set_name"]
        counters["parallelChanges"] += before["parallel_code"] != after["parallel_code"]
        counters["printingHashChanges"] += before["canonical_printing_sha256"] != after["canonical_printing_sha256"]
        counters["newLiteralAcceptances"] += row["currentPsaAcceptanceId"] is None
        counters["supersededLiteralAcceptances"] += row["currentPsaAcceptanceId"] is not None
    plan = {
        "schemaVersion": 1,
        "contract": CONTRACT,
        "generatedAt": utc_now(),
        "activeCount": len(rows),
        "counts": dict(sorted(counters.items())),
        "rows": rows,
    }
    plan["manifestSha256"] = sha256_json({k: v for k, v in plan.items() if k not in {"generatedAt", "manifestSha256"}})
    return plan


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def load_plan(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    expected = sha256_json({k: v for k, v in payload.items() if k not in {"generatedAt", "manifestSha256"}})
    if payload.get("contract") != CONTRACT or payload.get("manifestSha256") != expected:
        raise RuntimeError("active PSA resolution manifest is invalid")
    if len(payload.get("rows") or []) != 762 or int(payload.get("activeCount") or 0) != 762:
        raise RuntimeError("active PSA resolution must contain exactly 762 variants")
    return payload


def acceptance_payload(row: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    raw = row["rawEvidence"]
    psa = row["psa"]
    evidence = {
        "contract": CONTRACT,
        "variantId": row["variantId"],
        "gemrateId": row["gemrateId"],
        "rawPayloadSha256": raw["rawPayloadSha256"],
        "psaRowSha256": raw["psaRowSha256"],
        "canonicalPrintingSha256": row["after"]["canonical_printing_sha256"],
        "literalDescription": psa["description"],
    }
    return evidence, sha256_json(evidence), sha256_json({"kind": "literal_psa_identity", **evidence})


def provenance_payload(row: dict[str, Any]) -> tuple[dict[str, Any], str, str]:
    population = row["population"]
    evidence = {
        "contract": CONTRACT,
        "variantId": row["variantId"],
        "gemrateId": row["gemrateId"],
        "populationObservationId": int(population["id"]),
        "populationPayloadSha256": population["payload_sha256"],
        "psa10Population": int(population["top_grade_population"]),
        "populationEffectiveAt": str(population["effective_at"]),
    }
    return evidence, sha256_json(evidence), sha256_json({"kind": "gemrate_population_provenance", **evidence})


def binding_payload(row: dict[str, Any], identity_sha: str, provenance_sha: str) -> tuple[str, str]:
    raw = row["rawEvidence"]
    psa = row["psa"]
    population = row["population"]
    printing = row["after"]
    evidence = {
        "contract": CONTRACT,
        "action": "confirm",
        "evidence": {
            "type": "provider_native_psa_identity_and_population",
            "rawPath": raw["rawPath"],
            "rawPayloadSha256": raw["rawPayloadSha256"],
            "psaRowSha256": raw["psaRowSha256"],
            "populationObservationId": int(population["id"]),
            "populationPayloadSha256": population["payload_sha256"],
            "identityAcceptanceEvidenceSha256": identity_sha,
            "provenanceAcceptanceEvidenceSha256": provenance_sha,
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
            "psa10Population": int(population["top_grade_population"]),
        },
        "normalizedBindingClaims": {
            "collectorNumber": printing["collector_number"],
            "cardLanguage": printing["card_language"],
            "setName": printing["set_name"],
            "parallelCode": printing["parallel_code"],
            "editionCode": printing["edition_code"],
            "finishCode": printing["finish_code"],
        },
    }
    return canonical_json(evidence), sha256_json(evidence)


def apply_plan(connection: Any, plan: dict[str, Any]) -> dict[str, Any]:
    cur = connection.cursor()
    now = sql_now()
    affected = Counter()
    try:
        cur.execute("SELECT GET_LOCK('cardz-market-cap:active-psa-resolution-035',0) AS acquired")
        if int((cur.fetchone() or {}).get("acquired") or 0) != 1:
            raise RuntimeError("active PSA resolution lock unavailable")
        cur.execute("SELECT COUNT(*) AS n FROM cardz_schema_version WHERE version_code='035'")
        if int((cur.fetchone() or {}).get("n") or 0) != 1:
            raise RuntimeError("migration 035 is not applied")

        for row in plan["rows"]:
            variant_id = int(row["variantId"])
            before, after = row["before"], row["after"]
            cur.execute(
                """SELECT v.canonical_name,v.card_language AS variant_language,v.set_name AS variant_set_name,
                          p.card_language,p.set_name,p.canonical_printing_sha256
                   FROM catalog_variant v INNER JOIN catalog_printing_identity p ON p.variant_id=v.id
                   WHERE v.id=%s FOR UPDATE""",
                (variant_id,),
            )
            locked = cur.fetchone()
            for key in ("canonical_name", "variant_language", "variant_set_name", "card_language", "set_name", "canonical_printing_sha256"):
                if str((locked or {}).get(key) or "") != str(before.get(key) or ""):
                    raise RuntimeError(f"variant {variant_id} changed after plan preparation: {key}")

            identity_evidence, identity_sha, identity_lineage = acceptance_payload(row)
            provenance_evidence, provenance_sha, provenance_lineage = provenance_payload(row)
            psa = row["psa"]
            raw = row["rawEvidence"]
            population = row["population"]

            cur.execute(
                """UPDATE catalog_printing_identity
                   SET card_language=%s,set_name=%s,parallel_code=%s,canonical_printing_sha256=%s,
                       identity_status='confirmed',evidence_sha256=%s
                   WHERE variant_id=%s""",
                (
                    after["card_language"], after["set_name"], after["parallel_code"],
                    after["canonical_printing_sha256"], identity_sha, variant_id,
                ),
            )
            affected["printingRows"] += int(cur.rowcount)
            cur.execute(
                """UPDATE catalog_variant
                   SET canonical_name=%s,card_language=%s,set_name=%s,identity_status='confirmed'
                   WHERE id=%s""",
                (after["canonical_name"], after["variant_language"], after["variant_set_name"], variant_id),
            )
            affected["variantRows"] += int(cur.rowcount)

            current_acceptance_id = row.get("currentPsaAcceptanceId")
            supersedes_acceptance_id = None
            if current_acceptance_id:
                cur.execute(
                    """SELECT raw_payload_sha256,psa_row_sha256,psa_description,psa_language,
                              canonical_printing_sha256
                       FROM catalog_psa_identity_acceptance WHERE id=%s""",
                    (int(current_acceptance_id),),
                )
                accepted = cur.fetchone() or {}
                expected = (
                    raw["rawPayloadSha256"], raw["psaRowSha256"], psa["description"],
                    row["derivedLanguage"], after["canonical_printing_sha256"],
                )
                actual = tuple(accepted.get(key) for key in (
                    "raw_payload_sha256", "psa_row_sha256", "psa_description",
                    "psa_language", "canonical_printing_sha256",
                ))
                if actual == expected:
                    supersedes_acceptance_id = -1
                else:
                    supersedes_acceptance_id = int(current_acceptance_id)
            if supersedes_acceptance_id != -1:
                cur.execute(
                    """INSERT INTO catalog_psa_identity_acceptance
                       (variant_id,gemrate_id,psa_description,psa_year,psa_set_name,psa_card_number,
                        psa_parallel,psa_set_url,psa_language,raw_payload_sha256,psa_row_sha256,
                        canonical_printing_sha256,evidence_sha256,lineage_sha256,source_observed_at,
                        accepted_by,accepted_at,supersedes_acceptance_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        variant_id, row["gemrateId"], psa["description"], psa["year"], psa["set_name"],
                        psa["card_number"], psa.get("parallel") or "", psa.get("set_url") or "",
                        row["derivedLanguage"], raw["rawPayloadSha256"], raw["psaRowSha256"],
                        after["canonical_printing_sha256"], identity_sha, identity_lineage,
                        parse_time(raw.get("fetchedAt"), ROOT / raw["rawPath"]), ACTOR, now,
                        supersedes_acceptance_id,
                    ),
                )
                affected["psaAcceptances"] += int(cur.rowcount)

            cur.execute(
                """SELECT id,lineage_sha256 FROM catalog_gemrate_provenance_acceptance
                   WHERE variant_id=%s AND NOT EXISTS (
                     SELECT 1 FROM catalog_gemrate_provenance_acceptance newer
                     WHERE newer.supersedes_acceptance_id=catalog_gemrate_provenance_acceptance.id)
                   ORDER BY accepted_at DESC,id DESC LIMIT 1""",
                (variant_id,),
            )
            current_provenance = cur.fetchone()
            if not current_provenance or current_provenance["lineage_sha256"] != provenance_lineage:
                cur.execute(
                    """INSERT INTO catalog_gemrate_provenance_acceptance
                       (variant_id,gemrate_id,population_observation_id,population_payload_sha256,
                        psa10_population,population_effective_at,evidence_sha256,lineage_sha256,
                        accepted_by,accepted_at,supersedes_acceptance_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        variant_id, row["gemrateId"], int(population["id"]), population["payload_sha256"],
                        int(population["top_grade_population"]), population["effective_at"],
                        provenance_sha, provenance_lineage, ACTOR, now,
                        int(current_provenance["id"]) if current_provenance else None,
                    ),
                )
                affected["provenanceAcceptances"] += int(cur.rowcount)

            binding_json, binding_sha = binding_payload(row, identity_sha, provenance_sha)
            cur.execute(
                # The label is the point, not decoration: this used to move the
                # status and leave bind_evidence_json alone, so a superseded
                # binding kept reading action='confirm' and was
                # indistinguishable from psa_identity_repair's un-examined
                # collateral. JSON_SET rather than JSON_OBJECT because the
                # losing row's own evidence is still the record of what it
                # claimed.
                """UPDATE catalog_source_identity
                      SET match_status='rejected',
                          bind_evidence_json=JSON_SET(
                            COALESCE(bind_evidence_json, JSON_OBJECT()),
                            '$.previousAction',
                              JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.action')),
                            '$.action', 'supersede-losing-gemrate-binding',
                            '$.reason', 'another gemrate id won the active PSA identity for this variant',
                            '$.supersededBy', %s,
                            '$.supersededAt', UTC_TIMESTAMP())
                    WHERE variant_id=%s AND source_code='gemrate' AND external_entity_id<>%s
                      AND match_status<>'rejected'""",
                (row["gemrateId"], variant_id, row["gemrateId"]),
            )
            affected["supersededGemrateBindings"] += int(cur.rowcount)
            cur.execute(
                """UPDATE catalog_source_identity
                   SET match_status='exact',source_product_number=%s,evidence_sha256=%s,
                       bound_tcg_code=%s,bound_card_language=%s,bound_collector_number=%s,
                       bound_set_code=%s,bound_printing_code=%s,bound_edition_code=%s,
                       bound_parallel_code=%s,bound_finish_code=%s,bind_evidence_json=%s
                   WHERE source_code='gemrate' AND external_entity_id=%s AND variant_id=%s""",
                (
                    row["gemrateId"], binding_sha, after["tcg_code"], after["card_language"],
                    after["collector_number"], after["set_code"], after["printing_code"],
                    after["edition_code"], after["parallel_code"], after["finish_code"],
                    binding_json, row["gemrateId"], variant_id,
                ),
            )
            affected["gemrateBindings"] += int(cur.rowcount)

            cur.execute(
                """INSERT INTO operator_binding_freeze
                   (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
                    acceptance_status,actor,evidence_sha256,note,accepted_at)
                   VALUES (%s,'identity','','',%s,'accepted',%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE external_entity_id=VALUES(external_entity_id),
                    content_sha256=VALUES(content_sha256),acceptance_status=VALUES(acceptance_status),
                    actor=VALUES(actor),evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
                    accepted_at=VALUES(accepted_at)""",
                (variant_id, after["canonical_printing_sha256"], ACTOR, identity_sha, "035 literal PSA identity resolved", now),
            )
            affected["identityFreezes"] += int(cur.rowcount)
            cur.execute(
                """UPDATE operator_binding_freeze SET acceptance_status='rejected',actor=%s,
                          note=%s,accepted_at=%s
                   WHERE variant_id=%s AND freeze_kind='source' AND source_code='gemrate'
                     AND external_entity_id<>%s AND acceptance_status<>'rejected'""",
                (ACTOR, "035 superseded GemRate ID", now, variant_id, row["gemrateId"]),
            )
            cur.execute(
                """INSERT INTO operator_binding_freeze
                   (variant_id,freeze_kind,source_code,external_entity_id,content_sha256,
                    acceptance_status,actor,evidence_sha256,note,accepted_at)
                   VALUES (%s,'source','gemrate',%s,%s,'accepted',%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE external_entity_id=VALUES(external_entity_id),
                    content_sha256=VALUES(content_sha256),acceptance_status=VALUES(acceptance_status),
                    actor=VALUES(actor),evidence_sha256=VALUES(evidence_sha256),note=VALUES(note),
                    accepted_at=VALUES(accepted_at)""",
                (
                    variant_id, row["gemrateId"], population["payload_sha256"], ACTOR,
                    provenance_sha, "035 positive GemRate PSA10 population provenance", now,
                ),
            )
            affected["gemrateSourceFreezes"] += int(cur.rowcount)

        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('cardz-market-cap:active-psa-resolution-035')")
        except Exception:
            pass
    return {
        "contract": CONTRACT,
        "appliedAt": utc_now(),
        "manifestSha256": plan["manifestSha256"],
        "activeResolved": len(plan["rows"]),
        "planCounts": plan["counts"],
        "affected": dict(sorted(affected.items())),
    }


def amend_parallel(connection: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply the parallel values already committed into the 035 identity hashes.

    The first 035 transaction persisted the new hash, acceptance and binding but
    omitted the corresponding printing column from its UPDATE statement. This
    amendment consumes the same immutable manifest and changes only rows whose
    before/after parallel values differ.
    """

    cur = connection.cursor()
    changed = [row for row in plan["rows"] if row["before"]["parallel_code"] != row["after"]["parallel_code"]]
    affected = 0
    try:
        cur.execute("SELECT GET_LOCK('cardz-market-cap:active-psa-resolution-035',0) AS acquired")
        if int((cur.fetchone() or {}).get("acquired") or 0) != 1:
            raise RuntimeError("active PSA resolution lock unavailable")
        for row in changed:
            cur.execute(
                """UPDATE catalog_printing_identity SET parallel_code=%s
                   WHERE variant_id=%s AND parallel_code=%s AND canonical_printing_sha256=%s""",
                (
                    row["after"]["parallel_code"], int(row["variantId"]),
                    row["before"]["parallel_code"], row["after"]["canonical_printing_sha256"],
                ),
            )
            if int(cur.rowcount) != 1:
                raise RuntimeError(f"parallel amendment precondition failed: {row['variantId']}")
            affected += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        try:
            cur.execute("SELECT RELEASE_LOCK('cardz-market-cap:active-psa-resolution-035')")
        except Exception:
            pass
    return {
        "contract": CONTRACT,
        "amendedAt": utc_now(),
        "manifestSha256": plan["manifestSha256"],
        "parallelRows": affected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--output", type=Path, default=OUT_ROOT / "resolution-plan.json")
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", type=Path, default=OUT_ROOT / "resolution-plan.json")
    apply.add_argument("--receipt", type=Path, default=OUT_ROOT / "apply-receipt.json")
    amend = sub.add_parser("amend-parallel")
    amend.add_argument("--plan", type=Path, default=OUT_ROOT / "resolution-plan.json")
    amend.add_argument("--receipt", type=Path, default=OUT_ROOT / "parallel-amend-receipt.json")
    args = parser.parse_args()
    load_env()
    connection = db()
    try:
        if args.command == "prepare":
            payload = build_plan(connection)
            write_json(args.output, payload)
            print(json.dumps({k: v for k, v in payload.items() if k != "rows"}, ensure_ascii=False, sort_keys=True))
        elif args.command == "apply":
            payload = apply_plan(connection, load_plan(args.plan))
            write_json(args.receipt, payload)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        else:
            payload = amend_parallel(connection, load_plan(args.plan))
            write_json(args.receipt, payload)
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
