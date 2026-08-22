-- CARDZ 053: V2 reconstructed quote eligibility view.
--
-- 051 already applied the generic-source tables and a strict-identity INNER JOIN
-- view. Codex later reconstructed eligibility in place (disk 051 drifted). This
-- file is that reconstruction: keep the strict-identity path, and also accept a
-- completed market_variant_source_state quote. Additive; no 051 rewrite.

-- Registry-driven eligibility.  An exact language row wins over '*'; within
-- the selected language scope, priority remains deterministic and versioned.
CREATE OR REPLACE VIEW operator_eligible_current_quote_revision AS
SELECT
  h.id AS price_history_acceptance_id,
  q.id AS quote_revision_id,
  q.variant_id,
  q.source_period_at AS observed_date,
  q.price_usd,
  q.checked_at,
  q.source_period_at,
  sr.canonical_source_code AS price_source_code,
  q.source_code AS price_storage_source_code,
  q.source_external_entity_id AS price_source_external_entity_id,
  q.source_observation_id AS price_source_observation_id,
  q.payload_sha256 AS price_payload_sha256,
  q.checked_at AS price_effective_at,
  q.checked_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  q.quote_lineage_sha256,
  q.reconstruction_kind,
  rp.priority AS price_route_priority,
  rp.policy_version AS price_policy_version
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
INNER JOIN market_source_registry sr
  ON sr.source_code=q.source_code AND sr.enabled=1
 AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
INNER JOIN market_quote_route_policy rp
  ON rp.id=(
    SELECT chosen.id
    FROM market_quote_route_policy chosen
    WHERE chosen.source_code=q.source_code
      AND chosen.is_active=1
      AND chosen.is_eligible=1
      AND chosen.language_code IN (
        LOWER(REPLACE(pi.card_language,'_','-')),'*'
      )
    ORDER BY
      CASE WHEN chosen.language_code=LOWER(REPLACE(pi.card_language,'_','-'))
           THEN 0 ELSE 1 END,
      chosen.activated_at DESC,
      chosen.policy_version DESC,
      chosen.priority ASC,
      chosen.id DESC
    LIMIT 1
  )
WHERE h.metric_kind='psa10_price'
  AND h.source_code=sr.canonical_source_code
  AND (
    EXISTS (
      SELECT 1 FROM operator_strict_source_identity si
      WHERE si.variant_id=q.variant_id
        AND si.source_code=sr.identity_source_code
        AND si.external_entity_id=q.source_external_entity_id
    )
    OR EXISTS (
      SELECT 1 FROM market_variant_source_state source_state
      WHERE source_state.variant_id=q.variant_id
        AND source_state.source_code=sr.canonical_source_code
        AND source_state.capability='quote'
        AND source_state.status='completed'
        AND source_state.selected_quote_revision_id=q.id
        AND source_state.payload_sha256=q.payload_sha256
        AND source_state.evidence_ref=CONCAT('market_current_quote_revision:',q.id)
    )
  )
  AND q.price_usd>0
  AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND (q.reconstruction_kind IS NULL
       OR q.reconstruction_kind IN ('bootstrap_from_observation',''));

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('053');
