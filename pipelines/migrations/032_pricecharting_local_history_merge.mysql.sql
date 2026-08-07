-- CARDZ 032: exact local PriceCharting PSA10 history is the same provider
-- identity as the current PC guide; only the chart point date differs.
CREATE OR REPLACE VIEW operator_eligible_accepted_psa10_price_history AS
SELECT
  h.id AS price_history_acceptance_id,
  p.variant_id,p.observed_date,p.price_usd,
  CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
       ELSE p.source_code END AS price_source_code,
  p.source_code AS price_storage_source_code,
  p.source_external_entity_id AS price_source_external_entity_id,
  p.source_observation_id AS price_source_observation_id,
  p.payload_sha256 AS price_payload_sha256,
  p.effective_at AS price_effective_at,
  so.observed_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  CASE WHEN pi.card_language='en' THEN
    CASE WHEN p.source_code='pricecharting' THEN 10
         WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 20 ELSE 90 END
  ELSE
    CASE WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 10
         WHEN p.source_code='pricecharting' THEN 20 ELSE 90 END
  END AS price_route_priority
FROM market_metric_history_acceptance h
INNER JOIN market_price_observation p
  ON h.source_record_type='market_price_observation'
 AND h.source_record_id=p.id AND h.variant_id=p.variant_id
 AND h.observed_date=p.observed_date AND h.source_effective_at=p.effective_at
 AND h.external_entity_id=p.source_external_entity_id
 AND h.source_payload_sha256=p.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=p.variant_id
 AND si.source_code=CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE p.source_code END
 AND si.external_entity_id=p.source_external_entity_id
INNER JOIN market_source_observation so
  ON so.id=p.source_observation_id AND so.source_code=p.source_code
 AND so.external_entity_id=p.source_external_entity_id
 AND so.payload_sha256=p.payload_sha256 AND so.observed_date=p.observed_date
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND p.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
  AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
  AND (
    (p.source_code IN ('snkrdunk','snk_psa10','snk')
     AND so.observation_kind='psa10_reference_price')
    OR
    (p.source_code='pricecharting' AND p.source_priority=95
     AND so.observation_kind='psa10_price_guide'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%'
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
  AND p.price_usd>0 AND p.metric_status='ready' AND so.observed_at IS NOT NULL
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$';

CREATE OR REPLACE VIEW operator_eligible_pricecharting_variant AS
SELECT DISTINCT variant_id,1 AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history
WHERE price_source_code='pricecharting';

CREATE OR REPLACE VIEW operator_accepted_psa10_price_history AS
SELECT candidate.*,candidate.price_route_priority AS selected_price_route_priority,
       CASE WHEN pc.variant_id IS NULL THEN 0 ELSE 1 END AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history candidate
LEFT JOIN operator_eligible_pricecharting_variant pc ON pc.variant_id=candidate.variant_id
WHERE NOT EXISTS (
  SELECT 1 FROM operator_eligible_accepted_psa10_price_history preferred
  WHERE preferred.variant_id=candidate.variant_id
    AND preferred.price_route_priority<candidate.price_route_priority
)
AND ((pc.variant_id IS NOT NULL AND candidate.price_source_code='pricecharting')
  OR (pc.variant_id IS NULL AND candidate.price_source_code='snkrdunk'))
AND NOT EXISTS (
  SELECT 1 FROM operator_eligible_accepted_psa10_price_history newer
  WHERE newer.variant_id=candidate.variant_id
    AND newer.observed_date=candidate.observed_date
    AND newer.price_route_priority=candidate.price_route_priority
    AND (newer.price_effective_at>candidate.price_effective_at
      OR (newer.price_effective_at=candidate.price_effective_at
       AND (newer.price_source_observed_at>candidate.price_source_observed_at
        OR (newer.price_source_observed_at=candidate.price_source_observed_at
         AND newer.price_history_acceptance_id>candidate.price_history_acceptance_id))))
);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('032');
