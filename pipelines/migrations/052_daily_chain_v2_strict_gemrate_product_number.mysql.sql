-- Restore the provider product number for already-proven exact GemRate rows.
--
-- A blank source_product_number kept otherwise-complete native evidence out of
-- operator_strict_source_identity.  The value below is not inferred from a
-- display name: it is copied only when every bound printing field and the
-- immutable provider-native observation already satisfy the strict contract.

UPDATE catalog_source_identity si
INNER JOIN catalog_printing_identity p ON p.variant_id=si.variant_id
SET si.source_product_number=p.collector_number,
    si.updated_at=UTC_TIMESTAMP(6)
WHERE si.source_code='gemrate'
  AND LOWER(si.match_status)='exact'
  AND TRIM(si.source_product_number)=''
  AND TRIM(p.collector_number)<>''
  AND LOWER(TRIM(si.bound_tcg_code))=LOWER(TRIM(p.tcg_code))
  AND LOWER(TRIM(si.bound_card_language))=LOWER(TRIM(COALESCE(p.card_language,'')))
  AND LOWER(TRIM(si.bound_set_code))=LOWER(TRIM(p.set_code))
  AND LOWER(TRIM(si.bound_collector_number))=LOWER(TRIM(p.collector_number))
  AND LOWER(TRIM(si.bound_printing_code))=LOWER(TRIM(p.printing_code))
  AND (LOWER(TRIM(p.edition_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_edition_code))=LOWER(TRIM(p.edition_code)))
  AND (LOWER(TRIM(p.parallel_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_parallel_code))=LOWER(TRIM(p.parallel_code)))
  AND (LOWER(TRIM(p.finish_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_finish_code))=LOWER(TRIM(p.finish_code)))
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.type'))
      ='provider_native_psa_identity_and_population'
  AND JSON_UNQUOTE(JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256'))
      REGEXP '^[0-9a-f]{64}$'
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.tcgCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.cardLanguage') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.collectorNumber') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.setCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.printingCode') IS NOT NULL
  AND JSON_EXTRACT(si.bind_evidence_json,'$.providerClaims.parallelCode') IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM market_gemrate_psa10_observation_v2 obs
    WHERE obs.gemrate_id=si.external_entity_id
      AND obs.raw_payload_sha256=JSON_UNQUOTE(
        JSON_EXTRACT(si.bind_evidence_json,'$.evidence.rawPayloadSha256')
      )
  );
