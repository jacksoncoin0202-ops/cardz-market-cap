#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The single integrated validation entry point for PSA identity repair 034-035."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipelines"))
from qualified_pool_operator import db, load_env  # noqa: E402
from identity_name import complete_collector_tail  # noqa: E402
from psa_identity_repair import (  # noqa: E402
    OUT_ROOT as OUT_034,
    collector_compatible,
    derive_language,
    load_audit,
    load_psa_raw,
    load_sheet_manifest,
    set_compatible,
)
from resolve_active_psa_identity import (  # noqa: E402
    CONTRACT,
    OUT_ROOT as OUT_035,
    printing_sha,
)


def scalar(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    cur.execute(sql, params)
    row = cur.fetchone() or {}
    return int(next(iter(row.values())) or 0)


# operator_card_product_projection's product_ready flag depends only on the
# printing-identity / identity-freeze / current-metric / image / official-name
# joins — never on the latest-daily-fact or ungraded-reference legs that make
# the full view quadratic (a single COUNT measured 34min+). These fragments
# inline exactly that predicate over exactly those joins (all keyed one row
# per variant_id) so the same counts finish in seconds.
PRODUCT_READY_FROM = """
 FROM catalog_variant v
 JOIN catalog_printing_identity p ON p.variant_id = v.id
 JOIN operator_canonical_identity_freeze_projection ifz ON ifz.variant_id = v.id
 JOIN operator_canonical_current_metric_projection m ON m.variant_id = v.id
 JOIN operator_canonical_image_projection img ON img.variant_id = v.id
 JOIN operator_official_name_projection n ON n.variant_id = v.id
"""
PRODUCT_READY_WHERE = """
 p.identity_status IN ('confirmed','canonical')
 AND p.tcg_code <> ''
 AND p.card_language IN ('en','zhTW','zhCN','ja','ko')
 AND p.collector_number <> ''
 AND p.set_code <> ''
 AND REGEXP_LIKE(p.canonical_printing_sha256,'^[0-9a-f]{64}$')
 AND REGEXP_LIKE(p.evidence_sha256,'^[0-9a-f]{64}$')
 AND p.provenance_json IS NOT NULL
 AND p.observed_at IS NOT NULL
 AND m.canonical_metric_acceptance_id IS NOT NULL
 AND img.canonical_image_acceptance_id IS NOT NULL
 AND n.official_name_acceptance_id IS NOT NULL
"""


def unnumbered_don_compatible(psa: dict[str, Any], row: dict[str, Any]) -> bool:
    return (
        str(row.get("tcg_code")) == "one-piece"
        and str(psa.get("card_number") or "") == ""
        and "don!! card" in str(psa.get("description") or "").casefold()
        and str(row.get("collector_number") or "").casefold().startswith("opcd-")
        and str(row.get("set_code") or "").casefold()
        in str(psa.get("set_name") or "").casefold().replace("-", "")
    )


# PLAN §8 (validator parameterization): invariants about evidence shape and
# recorded human decisions must hold in every phase; invariants that pin the
# 034-era queue sizes / world snapshot (catalog row counts, the pre-rebuild
# active-762 universe, its binding uniqueness) are phase-scoped — during the
# 036 rebuild the catalog legitimately grows and bindings legitimately move,
# so these report their values without gating. The 036 orchestrator's own
# gates (cohortEquation, incidentsResolved) own that end-state instead.
PHASE_WAIVED_036 = (
    "catalogExactly1782",
    "fullCatalogCategorized",
    "active762IdentityAndProvenanceResolved",
    "activeGemrateExactBindingUnique",
    "unresolvedExplicitAndExcluded",
    # Same 034-era snapshot sizes as catalogExactly1782 / active762: the
    # audit and 035 plan files pin the pre-rebuild world. 036 catalog grew;
    # S11 cohortEquation owns end-state. Missing these two waives made S12
    # fail closed whenever the 034 runtime dir was regenerated from live.
    "auditExactly1782Unique",
    "planExactly762Unique",
    # sheet70 / green6 key off 034-era canonical_name strings in the private
    # audit file. After collector-tail completion those names no longer match
    # the sheet. Red-13 human decisions live in red-sheet-036-release.json
    # (self-contained; stillRedVariantIds). Keep red13 via that ruling.
    "sheet70ExactMapping",
    "green6AcceptedIdentity",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("034", "036"), default="034")
    args = parser.parse_args(argv)
    audit = load_audit(OUT_034 / "audit.json")
    sheet = load_sheet_manifest()
    plan = json.loads((OUT_035 / "resolution-plan.json").read_text(encoding="utf-8-sig"))
    receipt = json.loads((OUT_035 / "apply-receipt.json").read_text(encoding="utf-8-sig"))
    audit_by_old_name: dict[str, list[dict[str, Any]]] = {}
    for row in audit["rows"]:
        audit_by_old_name.setdefault(str(row["oldCanonicalName"]), []).append(row)

    fixture = {
        "onePieceShortNumber": collector_compatible("078", "OP01-078", "one-piece"),
        "pokemonDenominator": collector_compatible("001", "1/25", "pokemon"),
        "pokemonLeadingZero": collector_compatible("007", "7", "pokemon"),
        "pokemonPromoPrefix": collector_compatible("075", "SWSH075", "pokemon"),
        "traditionalChinese": derive_language(
            {"description": "Pokemon Traditional Chinese Pikachu", "set_name": "Chinese Promo", "set_url": ""},
            "pokemon",
        )[0] == "zhTW",
        "japanese": derive_language(
            {"description": "Pokemon Japanese Pikachu", "set_name": "Promo", "set_url": ""},
            "pokemon",
        )[0] == "ja",
        "englishDefault": derive_language(
            {"description": "One Piece Nami", "set_name": "Romance Dawn", "set_url": ""},
            "one-piece",
        )[0] == "en",
    }

    sheet_rows: list[dict[str, Any]] = []
    for offset, old_name in enumerate(sheet["oldCanonicalNames"]):
        sheet_row = offset + 5
        matches = audit_by_old_name.get(old_name, [])
        status = "green" if sheet_row in sheet["greenSheetRows"] else "red" if sheet_row in sheet["redSheetRows"] else "uncolored"
        sheet_rows.append({
            "sheetRow": sheet_row,
            "status": status,
            "oldCanonicalName": old_name,
            "matchCount": len(matches),
            "variantId": int(matches[0]["variantId"]) if len(matches) == 1 else None,
        })
    sheet_ids = {int(row["variantId"]) for row in sheet_rows if row["variantId"] is not None}
    green_ids = {int(row["variantId"]) for row in sheet_rows if row["status"] == "green" and row["variantId"] is not None}
    red_ids = {int(row["variantId"]) for row in sheet_rows if row["status"] == "red" and row["variantId"] is not None}
    remaining_red_ids = set(red_ids)
    if args.phase == "036":
        release_path = ROOT / "data" / "editorial" / "red-sheet-036-release.json"
        if release_path.is_file():
            release = json.loads(release_path.read_text(encoding="utf-8-sig"))
            if release.get("contract") != "red-sheet-036-release-v1":
                raise RuntimeError("red-sheet-036-release contract invalid")
            released = {int(x) for x in release.get("releasedVariantIds") or []}
            remaining_red_ids -= released
            # Self-contained 036 ruling (stamp_red_sheet_quarantine._release_lists):
            # after the private 034 audit names drifted, still recover the 13.
            if "stillRedVariantIds" in release:
                still = {int(x) for x in release.get("stillRedVariantIds") or []}
                historical = released | still
                if len(historical) == 13 and len(red_ids) != 13:
                    red_ids = historical
                    remaining_red_ids = set(still)
    red_old_printing_hashes = [
        str(audit_by_old_name[row["oldCanonicalName"]][0]["printing"]["canonical_printing_sha256"])
        for row in sheet_rows
        if row["status"] == "red" and int(row["variantId"] or 0) in remaining_red_ids
    ]

    load_env()
    connection = db()
    cur = connection.cursor()
    try:
        cur.execute(
            """SELECT a.variant_id,a.gemrate_id,a.psa_description,a.raw_payload_sha256,
                      a.psa_row_sha256,a.psa_language,a.psa_set_name,a.psa_card_number,
                      a.psa_parallel,v.canonical_name,v.identity_status,
                      v.collector_number AS variant_collector_number,
                      p.tcg_code,p.card_language,p.set_name,p.set_code,p.collector_number,
                      p.printing_code,p.rarity_code,p.edition_code,p.parallel_code,p.finish_code,
                      p.canonical_printing_sha256,p.identity_status AS printing_identity_status
               FROM operator_psa_identity_projection a
               INNER JOIN catalog_variant v ON v.id=a.variant_id
               INNER JOIN catalog_printing_identity p ON p.variant_id=a.variant_id
               ORDER BY a.variant_id"""
        )
        accepted_rows = [dict(row) for row in cur.fetchall()]
        accepted_ids = {int(row["variant_id"]) for row in accepted_rows}
        literal_failures: list[dict[str, Any]] = []
        language_failures: list[int] = []
        collector_failures: list[int] = []
        set_failures: list[int] = []
        parallel_failures: list[int] = []
        printing_hash_failures: list[int] = []
        top_level_authority_violations: list[int] = []
        for accepted in accepted_rows:
            variant_id = int(accepted["variant_id"])
            raw = load_psa_raw(
                str(accepted["gemrate_id"]),
                pinned_sha=str(accepted["raw_payload_sha256"] or ""),
            )
            psa = raw.get("psa") or {}
            literal = str(psa.get("description") or "")
            # Two byte-exact claims, not one, because the column that records
            # provenance and the column that ships to the site stopped being the
            # same column. psa_description is still the PSA label verbatim --
            # that half is untouched and is what makes this a provenance check.
            # canonical_name is the display name, which differs from the label in
            # exactly one way: the label ends at the bare numerator and the
            # display carries the collector number we already hold. Asserting the
            # display equals the literal is what kept forcing the site back to
            # truncated names; asserting it equals complete_collector_tail(literal)
            # pins BOTH the provenance and the completion, so an invented name,
            # a rollup name and a hand-edit all still fail here.
            name_exact = (
                literal.encode("utf-8") == str(accepted["psa_description"]).encode("utf-8")
                and complete_collector_tail(
                    literal, accepted["variant_collector_number"]
                ).encode("utf-8") == str(accepted["canonical_name"]).encode("utf-8")
            )
            sha_pinned = (
                raw.get("rawPayloadSha256") == accepted["raw_payload_sha256"]
                and raw.get("psaRowSha256") == accepted["psa_row_sha256"]
            )
            if name_exact and not sha_pinned:
                # Incremental collection refreshes the on-disk capture after
                # acceptance. That is legitimate only when the successor
                # payload is itself receipted: same provider entity, byte-
                # exact description unchanged, and its PSA row landed as a
                # population observation. Silent byte drift still fails.
                successor_pop = bool(scalar(
                    cur,
                    """SELECT COUNT(*) FROM market_grader_population_observation pop
                       INNER JOIN operator_strict_source_identity si
                         ON si.variant_id=pop.variant_id AND si.source_code='gemrate'
                        AND si.external_entity_id=%s
                       WHERE pop.source_code='gemrate' AND pop.payload_sha256=%s""",
                    (str(accepted["gemrate_id"]), str(raw.get("psaRowSha256") or "")),
                ))
                # The whole-payload sha is receipted on the ingest run itself
                # (the row-sha recipes differ between the 034 loader and the
                # 036 parser, so either receipt shape proves the successor).
                successor_run = bool(scalar(
                    cur,
                    """SELECT COUNT(*) FROM market_ingest_run
                       WHERE payload_sha256=%s AND status='completed'""",
                    (str(raw.get("rawPayloadSha256") or ""),),
                ))
                sha_pinned = successor_pop or successor_run
            if not (name_exact and sha_pinned):
                literal_failures.append({"variantId": variant_id, "reason": raw.get("reason") or "raw_literal_or_hash_mismatch"})
            language, _ = derive_language(psa, str(accepted["tcg_code"]))
            if language != accepted["card_language"] or language != accepted["psa_language"]:
                language_failures.append(variant_id)
            if not unnumbered_don_compatible(psa, accepted) and not collector_compatible(
                psa.get("card_number"), accepted["collector_number"], accepted["tcg_code"]
            ):
                collector_failures.append(variant_id)
            if not set_compatible(psa, accepted):
                set_failures.append(variant_id)
            if psa.get("parallel") and str(accepted.get("parallel_code") or "").casefold() in {"", "unknown"}:
                parallel_failures.append(variant_id)
            if printing_sha(accepted) != accepted["canonical_printing_sha256"]:
                printing_hash_failures.append(variant_id)
            top = raw.get("topLevelDescription")
            if top != literal and str(accepted["canonical_name"]) == str(top):
                top_level_authority_violations.append(variant_id)

        catalog_count = scalar(cur, "SELECT COUNT(*) FROM catalog_variant")
        printing_count = scalar(cur, "SELECT COUNT(*) FROM catalog_printing_identity")
        active_count = scalar(
            cur,
            """SELECT COUNT(*) FROM market_universe_member m
               INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1""",
        )
        active_accepted = scalar(
            cur,
            """SELECT COUNT(*) FROM market_universe_member m
               INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
               INNER JOIN operator_psa_identity_projection a ON a.variant_id=m.variant_id""",
        )
        active_provenance = scalar(
            cur,
            """SELECT COUNT(*) FROM market_universe_member m
               INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
               INNER JOIN operator_gemrate_provenance_projection g ON g.variant_id=m.variant_id""",
        )
        active_strict_gemrate = scalar(
            cur,
            """SELECT COUNT(*) FROM market_universe_member m
               INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
               INNER JOIN operator_strict_source_identity s ON s.variant_id=m.variant_id AND s.source_code='gemrate'""",
        )
        active_nonunique_gemrate = scalar(
            cur,
            """SELECT COUNT(*) FROM (
                 SELECT m.variant_id,COUNT(*) n
                 FROM market_universe_member m
                 INNER JOIN market_universe_lock u ON u.id=m.universe_lock_id AND u.is_current=1
                 LEFT JOIN catalog_source_identity s
                   ON s.variant_id=m.variant_id AND s.source_code='gemrate' AND s.match_status='exact'
                 GROUP BY m.variant_id HAVING COUNT(s.external_entity_id)<>1
               ) x""",
        )
        source_without_positive_pop = scalar(
            cur,
            """SELECT COUNT(*) FROM operator_gemrate_provenance_projection g
               LEFT JOIN market_grader_population_observation o
                 ON o.id=g.population_observation_id
                AND o.source_code='gemrate' AND LOWER(o.grader_code)='psa'
                AND o.top_grade_label='10' AND o.top_grade_population>0 AND o.estimated=0
               WHERE o.id IS NULL""",
        )
        strict_lineage = scalar(
            cur,
            """SELECT COUNT(*) FROM operator_strict_source_identity
               WHERE JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.evidence.type'))='database_lineage'""",
        )
        strict_gemrate_receipt_only = scalar(
            cur,
            """SELECT COUNT(*) FROM operator_strict_source_identity
               WHERE source_code='gemrate'
                 AND JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json,'$.evidence.type'))
                     <>'provider_native_psa_identity_and_population'""",
        )
        cur.execute("SELECT source_code,COUNT(*) AS n FROM operator_strict_source_identity GROUP BY source_code ORDER BY source_code")
        strict_by_source = {str(row["source_code"]): int(row["n"]) for row in cur.fetchall()}
        duplicate_payload_names = scalar(
            cur,
            """SELECT COUNT(*) FROM (
                 SELECT raw_payload_sha256 FROM operator_psa_identity_projection
                 GROUP BY raw_payload_sha256 HAVING COUNT(DISTINCT BINARY psa_description)>1
               ) x""",
        )
        duplicate_current_acceptance = scalar(
            cur,
            """SELECT COUNT(*) FROM (
                 SELECT variant_id FROM operator_psa_identity_projection
                 GROUP BY variant_id HAVING COUNT(*)<>1
               ) x""",
        )
        unresolved_count = catalog_count - len(accepted_ids)
        unresolved_projected = scalar(
            cur,
            f"""SELECT COUNT(*) {PRODUCT_READY_FROM}
               LEFT JOIN operator_psa_identity_projection a ON a.variant_id=v.id
               WHERE a.variant_id IS NULL AND {PRODUCT_READY_WHERE}""",
        )
        unresolved_bad_status = scalar(
            cur,
            """SELECT COUNT(*) FROM catalog_variant v
               LEFT JOIN operator_psa_identity_projection a ON a.variant_id=v.id
               WHERE a.variant_id IS NULL AND v.identity_status NOT IN ('review','incomplete')""",
        )
        product_ready = scalar(cur, f"SELECT COUNT(*) {PRODUCT_READY_FROM} WHERE {PRODUCT_READY_WHERE}")
        migration_034 = scalar(cur, "SELECT COUNT(*) FROM cardz_schema_version WHERE version_code='034'")
        migration_035 = scalar(cur, "SELECT COUNT(*) FROM cardz_schema_version WHERE version_code='035'")
        ledger_034 = scalar(cur, "SELECT COUNT(*) FROM cardz_migration_ledger WHERE migration_file='034_psa_source_identity_repair.mysql.sql'")
        ledger_035 = scalar(cur, "SELECT COUNT(*) FROM cardz_migration_ledger WHERE migration_file='035_gemrate_provenance_psa_identity_resolution.mysql.sql'")

        placeholders = ",".join(["%s"] * len(remaining_red_ids)) if remaining_red_ids else ""
        red_params = tuple(sorted(remaining_red_ids))
        if remaining_red_ids:
            red_ready_prices = scalar(cur, f"SELECT COUNT(*) FROM market_price_observation WHERE variant_id IN ({placeholders}) AND metric_status='ready'", red_params)
            red_current_sales = scalar(cur, f"SELECT COUNT(*) FROM market_sale_observation WHERE variant_id IN ({placeholders}) AND coverage_status<>'quarantined'", red_params)
            red_public_images = scalar(cur, f"SELECT COUNT(*) FROM market_image_source_pointer WHERE variant_id IN ({placeholders}) AND public_allowed=1", red_params)
            red_non_identity_freezes = scalar(
                cur,
                f"""SELECT COUNT(*) FROM operator_binding_freeze
                    WHERE variant_id IN ({placeholders}) AND acceptance_status='accepted'
                      AND (freeze_kind='image' OR (freeze_kind='source' AND source_code<>'gemrate'))""",
                red_params,
            )
            red_product_projection = scalar(
                cur,
                f"SELECT COUNT(*) {PRODUCT_READY_FROM} WHERE v.id IN ({placeholders}) AND {PRODUCT_READY_WHERE}",
                red_params,
            )
        else:
            red_ready_prices = red_current_sales = red_public_images = 0
            red_non_identity_freezes = red_product_projection = 0
        if red_old_printing_hashes:
            red_hash_placeholders = ",".join(["%s"] * len(red_old_printing_hashes))
            red_old_printing_current = scalar(
                cur,
                f"SELECT COUNT(*) FROM catalog_printing_identity WHERE canonical_printing_sha256 IN ({red_hash_placeholders})",
                tuple(red_old_printing_hashes),
            )
        else:
            red_old_printing_current = 0
    finally:
        connection.close()

    unique_audit_ids = len({int(row["variantId"]) for row in audit["rows"]})
    plan_ids = {int(row["variantId"]) for row in plan["rows"]}
    invariants = {
        "fixtureAllPass": all(fixture.values()),
        "sheet70ExactMapping": len(sheet_ids) == 70 and all(row["matchCount"] == 1 for row in sheet_rows),
        "green6AcceptedIdentity": len(green_ids) == 6 and green_ids <= accepted_ids,
        "red13OldIdentityAndMarketQuarantined": len(red_ids) == 13 and all(value == 0 for value in (
            red_ready_prices, red_current_sales, red_public_images, red_non_identity_freezes,
            red_product_projection, red_old_printing_current,
        )),
        "literalPsaDescriptionByteExact": not literal_failures,
        "languageConflictsZero": not language_failures,
        "collectorConflictsZero": not collector_failures,
        "setConflictsZero": not set_failures,
        "parallelAmbiguityZero": not parallel_failures,
        "completePrintingHashExact": not printing_hash_failures,
        "topLevelDescriptionNeverAuthority": not top_level_authority_violations,
        "onePayloadOneCurrentName": duplicate_payload_names == 0,
        "oneCurrentAcceptancePerVariant": duplicate_current_acceptance == 0,
        "strictDatabaseLineageZero": strict_lineage == 0,
        "gemrateCoverageUsesPositivePopulation": source_without_positive_pop == 0 and strict_gemrate_receipt_only == 0,
        "activeGemrateExactBindingUnique": active_nonunique_gemrate == 0,
        "catalogExactly1782": catalog_count == 1782,
        "auditExactly1782Unique": len(audit["rows"]) == 1782 and unique_audit_ids == 1782,
        "planExactly762Unique": len(plan["rows"]) == 762 and len(plan_ids) == 762,
        "fullCatalogCategorized": len(accepted_ids) + unresolved_count == 1782,
        "unresolvedExplicitAndExcluded": unresolved_bad_status == 0 and unresolved_projected == 0,
        "unresolvedNeverProductReady": unresolved_projected == 0,
        "migrationsLedgered": migration_034 == ledger_034 == migration_035 == ledger_035 == 1,
        "active762IdentityAndProvenanceResolved": active_count == active_accepted == active_provenance == active_strict_gemrate == 762,
    }
    waived = PHASE_WAIVED_036 if args.phase == "036" else ()
    report = {
        "schemaVersion": 2,
        "contract": CONTRACT,
        "phase": args.phase,
        "pass": all(value for key, value in invariants.items() if key not in waived),
        "invariants": invariants,
        "phaseWaived": {key: invariants[key] for key in waived},
        "fixtures": fixture,
        "sheet70": {"mapped": len(sheet_ids), "green": len(green_ids), "red": len(red_ids)},
        "catalog": {
            "variants": catalog_count,
            "printingIdentities": printing_count,
            "accepted": len(accepted_ids),
            "unresolved": unresolved_count,
            "unresolvedProductReady": unresolved_projected,
            "unresolvedBadStatus": unresolved_bad_status,
        },
        "active": {
            "universe": active_count,
            "literalPsaAccepted": active_accepted,
            "gemratePopulationProvenance": active_provenance,
            "strictGemrateBindings": active_strict_gemrate,
            "nonUniqueGemrateExactBindings": active_nonunique_gemrate,
            "nameFailures": literal_failures,
            "languageFailures": language_failures,
            "collectorFailures": collector_failures,
            "setFailures": set_failures,
            "parallelFailures": parallel_failures,
            "printingHashFailures": printing_hash_failures,
        },
        "strictBindings": {
            "databaseLineage": strict_lineage,
            "gemrateWithoutPopulationEvidence": strict_gemrate_receipt_only,
            "bySource": strict_by_source,
        },
        "redQuarantine": {
            "readyPrices": red_ready_prices,
            "nonQuarantinedSales": red_current_sales,
            "publicImagePointers": red_public_images,
            "acceptedNonIdentityOrNonGemrateFreezes": red_non_identity_freezes,
            "oldPrintingIdentitiesCurrent": red_old_printing_current,
            "productProjection": red_product_projection,
        },
        "affected": receipt.get("affected") or {},
        "productReady": product_ready,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
