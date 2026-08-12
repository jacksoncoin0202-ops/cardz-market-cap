-- CARDZ 049: verified append-only historical quote resolution.
--
-- 046 wrongly deleted semantic duplicate quote evidence.  The deleted ROW/FULL
-- images were restored under the original ids before this migration existed.
-- 049 never deletes or rewrites a quote revision.  It makes the mutable map an
-- explicit verified projection and gives every reader one fail-closed view.

SET @db := DATABASE();

-- A database which saw the retired 046 experiment may still carry its
-- generated owner column/index.  Remove the write restriction without touching
-- a quote row.  Clean databases never create either object.
SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_current_quote_revision'
             AND INDEX_NAME='uq_legacy_acceptance_owner'),
    'ALTER TABLE market_current_quote_revision DROP INDEX uq_legacy_acceptance_owner',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_current_quote_revision'
             AND COLUMN_NAME='legacy_acceptance_owner'),
    'ALTER TABLE market_current_quote_revision DROP COLUMN legacy_acceptance_owner',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.COLUMNS
           WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_legacy_quote_resolution'
             AND COLUMN_NAME='resolution_status'),
    'SELECT 1',
    'ALTER TABLE market_legacy_quote_resolution ADD COLUMN resolution_status VARCHAR(32) NOT NULL DEFAULT ''unverified'' AFTER resolved_at, ADD COLUMN resolver_evidence_sha256 CHAR(64) NULL AFTER resolution_status, ADD COLUMN blocker_code VARCHAR(128) NULL AFTER resolver_evidence_sha256, ADD COLUMN resolved_by VARCHAR(191) NULL AFTER blocker_code'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(SELECT 1 FROM information_schema.STATISTICS
           WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_legacy_quote_resolution'
             AND INDEX_NAME='ix_legacy_resolution_status'),
    'SELECT 1',
    'ALTER TABLE market_legacy_quote_resolution ADD INDEX ix_legacy_resolution_status (resolution_status, metric_acceptance_id)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

CREATE OR REPLACE VIEW operator_resolved_canonical_metric_quote AS
SELECT m.id AS metric_acceptance_id,
       q.id AS quote_revision_id,
       q.variant_id,
       q.source_code,
       q.source_external_entity_id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.source_observation_id,
       q.market_price_observation_id,
       q.payload_sha256,
       q.quote_lineage_sha256,
       q.reconstruction_kind,
       'direct' AS resolution_kind,
       q.quote_lineage_sha256 AS resolver_evidence_sha256
FROM market_canonical_metric_acceptance m
INNER JOIN market_metric_history_acceptance ph
  ON ph.id=m.price_history_acceptance_id
 AND ph.metric_kind='psa10_price'
 AND ph.source_record_type='market_current_quote_revision'
INNER JOIN market_current_quote_revision q
  ON q.id=ph.source_record_id
 AND q.variant_id=m.variant_id
 AND q.source_period_at=ph.observed_date
 AND q.checked_at=ph.source_effective_at
 AND q.source_external_entity_id=ph.external_entity_id
 AND (CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE q.source_code END)=ph.source_code
 AND q.payload_sha256=ph.source_payload_sha256
 AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
INNER JOIN market_metric_history_acceptance poh
  ON poh.id=m.population_history_acceptance_id
 AND poh.metric_kind='psa10_population'
INNER JOIN market_grader_population_observation pop
  ON pop.id=poh.source_record_id
 AND pop.variant_id=m.variant_id
WHERE pop.top_grade_population>0
  AND q.price_usd=ROUND(m.market_cap_usd/pop.top_grade_population,6)
UNION ALL
SELECT m.id AS metric_acceptance_id,
       q.id AS quote_revision_id,
       q.variant_id,
       q.source_code,
       q.source_external_entity_id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.source_observation_id,
       q.market_price_observation_id,
       q.payload_sha256,
       q.quote_lineage_sha256,
       q.reconstruction_kind,
       'legacy' AS resolution_kind,
       r.resolver_evidence_sha256
FROM market_canonical_metric_acceptance m
INNER JOIN market_metric_history_acceptance ph
  ON ph.id=m.price_history_acceptance_id
 AND ph.metric_kind='psa10_price'
 AND ph.source_record_type='market_price_observation'
INNER JOIN market_legacy_quote_resolution r
  ON r.metric_acceptance_id=m.id
 AND r.resolution_status='resolved'
 AND r.resolver_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
INNER JOIN market_current_quote_revision q
  ON q.id=r.quote_revision_id
 AND q.variant_id=m.variant_id
 AND q.reconstruction_kind='legacy_generation_reconstructed'
 AND q.reconstructed_from_acceptance_id=m.id
 AND q.price_usd=r.price_usd
 AND q.source_period_at=r.source_period_at
 AND q.checked_at=r.checked_at
 AND q.quote_lineage_sha256=r.quote_lineage_sha256
 AND (CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE q.source_code END)=ph.source_code
 AND q.source_external_entity_id=ph.external_entity_id
 AND q.source_period_at=ph.observed_date
 AND q.checked_at=m.accepted_at
 AND q.payload_sha256=ph.source_payload_sha256
INNER JOIN market_metric_history_acceptance poh
  ON poh.id=m.population_history_acceptance_id
 AND poh.metric_kind='psa10_population'
INNER JOIN market_grader_population_observation pop
  ON pop.id=poh.source_record_id
 AND pop.variant_id=m.variant_id
WHERE pop.top_grade_population>0
  AND m.market_cap_usd>0
  AND q.price_usd=ROUND(m.market_cap_usd/pop.top_grade_population,6)
  AND r.resolver_evidence_sha256=SHA2(CONCAT_WS('|',
        'market_legacy_quote_resolution_v2',
        m.id,
        m.price_history_acceptance_id,
        m.population_history_acceptance_id,
        m.metric_lineage_sha256,
        ph.lineage_sha256,
        poh.lineage_sha256,
        CAST(q.price_usd AS CHAR),
        pop.top_grade_population,
        q.quote_lineage_sha256
      ),256);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('049');
