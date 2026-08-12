-- CARDZ 044: discovery attempt lifecycle for identity ledger rotation.
-- Durable attempt/outcome/next-due/quarantine so daily discovery does not
-- re-select the same lowest IDs forever.

SET @db := DATABASE();

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND COLUMN_NAME='attempt_count'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN attempt_count INT UNSIGNED NOT NULL DEFAULT 0'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND COLUMN_NAME='last_attempt_at'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN last_attempt_at DATETIME(6) NULL'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND COLUMN_NAME='last_outcome'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN last_outcome VARCHAR(64) NULL'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND COLUMN_NAME='next_due_at'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN next_due_at DATETIME(6) NULL'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND COLUMN_NAME='quarantine_until'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD COLUMN quarantine_until DATETIME(6) NULL'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND INDEX_NAME='ix_discovery_next_due'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD INDEX ix_discovery_next_due (next_due_at, discovery_status)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA=@db AND TABLE_NAME='market_identity_discovery_ledger' AND INDEX_NAME='ix_discovery_quarantine'
    ),
    'SELECT 1',
    'ALTER TABLE market_identity_discovery_ledger ADD INDEX ix_discovery_quarantine (quarantine_until)'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('044');
