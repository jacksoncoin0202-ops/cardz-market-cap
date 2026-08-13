-- CARDZ 050: ranking view accepts Chinese PriceCharting quotes.
--
-- 043 hard-coded pi.card_language='en'. Python writers later accepted zhTW
-- (DADDY 2026-08-13) but this view is what S12 ranks. Code is the authority:
-- pipelines/current_quote_revision.py eligible_current_quote_revision_ddl().
-- Activate re-applies the view so this file and live DDL cannot drift.

CREATE OR REPLACE VIEW operator_eligible_current_quote_revision AS
SELECT
  h.id AS price_history_acceptance_id,
  q.id AS quote_revision_id,
  q.variant_id,
  q.source_period_at AS observed_date,
  q.price_usd,
  q.checked_at,
  q.source_period_at,
  CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
       ELSE q.source_code END AS price_source_code,
  q.source_code AS price_storage_source_code,
  q.source_external_entity_id AS price_source_external_entity_id,
  q.source_observation_id AS price_source_observation_id,
  q.payload_sha256 AS price_payload_sha256,
  q.checked_at AS price_effective_at,
  q.checked_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  q.quote_lineage_sha256,
  q.reconstruction_kind,
  CASE WHEN LOWER(REPLACE(pi.card_language, '_', '-')) IN ('en','zh','zh-cn','zh-tw','zhcn','zhtw') THEN CASE WHEN q.source_code='pricecharting' THEN 10 WHEN q.source_code IN ('snkrdunk','snk_psa10','snk') THEN 20 ELSE 90 END ELSE CASE WHEN q.source_code IN ('snkrdunk','snk_psa10','snk') THEN 10 WHEN q.source_code='pricecharting' THEN 20 ELSE 90 END END AS price_route_priority
FROM market_metric_history_acceptance h
INNER JOIN market_current_quote_revision q
  ON h.source_record_type='market_current_quote_revision'
 AND h.source_record_id=q.id
 AND h.variant_id=q.variant_id
 AND h.observed_date=q.source_period_at
 AND h.source_effective_at=q.checked_at
 AND h.external_entity_id=q.source_external_entity_id
 AND h.source_payload_sha256=q.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=q.variant_id
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=q.variant_id
 AND si.source_code=CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE q.source_code END
 AND si.external_entity_id=q.source_external_entity_id
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND q.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
  AND (LOWER(REPLACE(pi.card_language, '_', '-')) IN ('en','zh','zh-cn','zh-tw','zhcn','zhtw') OR q.source_code IN ('snkrdunk','snk_psa10','snk'))
  AND q.price_usd>0
  AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND (q.reconstruction_kind IS NULL
       OR q.reconstruction_kind IN ('bootstrap_from_observation',''));

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('050');
