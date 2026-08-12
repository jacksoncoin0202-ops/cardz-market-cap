-- CARDZ 047: remove the destructive 046 single-owner guard.
--
-- 046 was already ledgered before review.  It incorrectly treated multiple
-- immutable reconstructions for one historical acceptance as rows to delete.
-- Quote revisions are append-only evidence: duplicate semantic candidates may
-- be resolved by an explicit evidence map, but they must never be deleted.
--
-- The deleted row images are restored separately from the MySQL ROW/FULL
-- binlog transaction.  This migration only returns the schema to the
-- append-only shape required for that exact replay.

SET @db := DATABASE();

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.STATISTICS
      WHERE TABLE_SCHEMA=@db
        AND TABLE_NAME='market_current_quote_revision'
        AND INDEX_NAME='uq_legacy_acceptance_owner'
    ),
    'ALTER TABLE market_current_quote_revision DROP INDEX uq_legacy_acceptance_owner',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @sql := (
  SELECT IF(
    EXISTS(
      SELECT 1 FROM information_schema.COLUMNS
      WHERE TABLE_SCHEMA=@db
        AND TABLE_NAME='market_current_quote_revision'
        AND COLUMN_NAME='legacy_acceptance_owner'
    ),
    'ALTER TABLE market_current_quote_revision DROP COLUMN legacy_acceptance_owner',
    'SELECT 1'
  )
);
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('047');
