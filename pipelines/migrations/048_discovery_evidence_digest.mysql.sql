-- CARDZ 048: durable provider-evidence digest for discovery rotation.
--
-- Attempt_count alone cannot distinguish a repeated miss against identical
-- provider evidence from a genuinely new provider result.  Keep the canonical
-- digest of the last real attempt on the ledger so retry/quarantine policy can
-- advance only when the evidence is actually the same.

SET @db := DATABASE();

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db
        AND TABLE_NAME='market_identity_discovery_ledger'
        AND COLUMN_NAME='last_evidence_sha256'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN last_evidence_sha256 CHAR(64) NULL AFTER last_outcome'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db
        AND TABLE_NAME='market_identity_discovery_ledger'
        AND COLUMN_NAME='consecutive_same_evidence_count'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN consecutive_same_evidence_count INT UNSIGNED NOT NULL DEFAULT 0 AFTER last_evidence_sha256'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('048');
