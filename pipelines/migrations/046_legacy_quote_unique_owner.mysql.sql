-- CARDZ 046: freeze one legacy reconstruction per historical metric acceptance.
-- 044/045 already shipped. Do not alter them.
--
-- Problem: bootstrap reconstruct ran more than once and left multiple
-- legacy_generation_reconstructed rows per reconstructed_from_acceptance_id.
-- 045 map already points at MAX(id). This migration:
--   1) re-sync the 045 map to the newest surviving legacy quote
--   2) delete older duplicate legacy quote rows not referenced by the map
--   3) add a uniqueness guard so one acceptance cannot own two legacy freezes

-- Re-sync map to newest legacy quote per acceptance (idempotent).
INSERT INTO market_legacy_quote_resolution
  (metric_acceptance_id, quote_revision_id, price_usd, source_period_at, checked_at,
   quote_lineage_sha256, resolved_at)
SELECT q.reconstructed_from_acceptance_id,
       q.id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.quote_lineage_sha256,
       UTC_TIMESTAMP(6)
FROM market_current_quote_revision q
INNER JOIN (
  SELECT reconstructed_from_acceptance_id AS metric_acceptance_id,
         MAX(id) AS quote_revision_id
  FROM market_current_quote_revision
  WHERE reconstruction_kind='legacy_generation_reconstructed'
    AND reconstructed_from_acceptance_id IS NOT NULL
  GROUP BY reconstructed_from_acceptance_id
) latest ON latest.quote_revision_id=q.id
ON DUPLICATE KEY UPDATE
  quote_revision_id=VALUES(quote_revision_id),
  price_usd=VALUES(price_usd),
  source_period_at=VALUES(source_period_at),
  checked_at=VALUES(checked_at),
  quote_lineage_sha256=VALUES(quote_lineage_sha256),
  resolved_at=VALUES(resolved_at);

-- Drop older duplicate legacy freezes that the 045 map does not reference.
DELETE q
FROM market_current_quote_revision q
LEFT JOIN market_legacy_quote_resolution r
  ON r.quote_revision_id=q.id
WHERE q.reconstruction_kind='legacy_generation_reconstructed'
  AND q.reconstructed_from_acceptance_id IS NOT NULL
  AND r.quote_revision_id IS NULL;

-- Generated uniqueness key: one legacy freeze owner per acceptance id.
SET @db := DATABASE();
SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_current_quote_revision'
        AND COLUMN_NAME='legacy_acceptance_owner'
    ),
    'SELECT 1',
    'ALTER TABLE market_current_quote_revision ADD COLUMN legacy_acceptance_owner BIGINT UNSIGNED GENERATED ALWAYS AS (CASE WHEN reconstruction_kind=''legacy_generation_reconstructed'' THEN reconstructed_from_acceptance_id ELSE NULL END) STORED'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_current_quote_revision'
        AND INDEX_NAME='uq_legacy_acceptance_owner'
    ),
    'SELECT 1',
    'ALTER TABLE market_current_quote_revision ADD UNIQUE KEY uq_legacy_acceptance_owner (legacy_acceptance_owner)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('046');
