-- CARDZ 037: strict source identity, provider-native evidence on both branches.
-- STAGED: this file lives in staged/ (invisible to the migration glob) until the
-- 036 orchestrator moves it into migrations/ after S6 replay has landed capture
-- receipts. Applying it earlier would empty the PC/SNK branch (receipts absent).
--
-- Gemrate branch is NOT 035's: 036 moved the population authority to
-- market_gemrate_psa10_observation_v2 (PLAN §7.3) and forbids writing the old
-- market_grader_population_observation (§3.2), so the legacy projections the
-- 035 view proved lineage through can never cover 036-minted variants. Lineage
-- is now: binding evidence rawPayloadSha256 must be a raw-verified v2
-- observation of the same gemrate id.

CREATE OR REPLACE VIEW operator_strict_source_identity AS
SELECT si.*
FROM catalog_source_identity si
INNER JOIN catalog_printing_identity p ON p.variant_id=si.variant_id
WHERE LOWER(si.match_status)='exact'
  AND TRIM(si.source_product_number)<>''
  AND LOWER(TRIM(si.bound_tcg_code))=LOWER(TRIM(p.tcg_code))
  -- p.card_language is the one nullable identity column: NULL means the same
  -- as empty (not established) and must not NULL-poison the equality.
  AND LOWER(TRIM(si.bound_card_language))=LOWER(TRIM(COALESCE(p.card_language,'')))
  AND LOWER(TRIM(si.bound_collector_number))=LOWER(TRIM(p.collector_number))
  AND LOWER(TRIM(si.bound_set_code))=LOWER(TRIM(p.set_code))
  AND LOWER(TRIM(si.bound_printing_code))=LOWER(TRIM(p.printing_code))
  AND (LOWER(TRIM(p.edition_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_edition_code))=LOWER(TRIM(p.edition_code)))
  AND (LOWER(TRIM(p.parallel_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_parallel_code))=LOWER(TRIM(p.parallel_code)))
  AND (LOWER(TRIM(p.finish_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_finish_code))=LOWER(TRIM(p.finish_code)))
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.tcgCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.cardLanguage') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.collectorNumber') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.setCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.printingCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.parallelCode') IS NOT NULL
  AND (
    (
      si.source_code='gemrate'
      AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type'))='provider_native_psa_identity_and_population'
      AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256')) REGEXP '^[0-9a-f]{64}$'
      AND EXISTS (
        SELECT 1 FROM market_gemrate_psa10_observation_v2 v2
        WHERE v2.gemrate_id=si.external_entity_id
          AND v2.raw_payload_sha256=JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256'))
      )
    )
    OR
    (
      si.source_code<>'gemrate'
      AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type'))='provider_native_product_page'
      AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.sha256')) REGEXP '^[0-9a-f]{64}$'
      AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.path'))<>''
      AND EXISTS (
        SELECT 1 FROM catalog_provider_capture_receipt r
        WHERE r.source_code=si.source_code
          AND r.external_entity_id=si.external_entity_id
          AND r.capture_sha256=JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.sha256'))
      )
    )
  );

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('037');
