-- Bind each derived index revision to the immutable alert evaluation that
-- produced it. Historical rows remain nullable; every new writer supplies an
-- evaluation_id. Compatible with MySQL 5.7/8.x. Apply after 011.

SET @has_evaluation_column = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'market_index_snapshot'
      AND column_name = 'evaluation_id'
);
SET @add_evaluation_column = IF(
    @has_evaluation_column = 0,
    'ALTER TABLE market_index_snapshot ADD COLUMN evaluation_id BIGINT UNSIGNED NULL AFTER run_id',
    'SELECT 1'
);
PREPARE add_evaluation_column_stmt FROM @add_evaluation_column;
EXECUTE add_evaluation_column_stmt;
DEALLOCATE PREPARE add_evaluation_column_stmt;

SET @has_old_snapshot_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_index_snapshot'
      AND index_name = 'uq_market_index_snapshot'
);
SET @drop_old_snapshot_index = IF(
    @has_old_snapshot_index > 0,
    'ALTER TABLE market_index_snapshot DROP INDEX uq_market_index_snapshot',
    'SELECT 1'
);
PREPARE drop_old_snapshot_index_stmt FROM @drop_old_snapshot_index;
EXECUTE drop_old_snapshot_index_stmt;
DEALLOCATE PREPARE drop_old_snapshot_index_stmt;

SET @has_evaluation_snapshot_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_index_snapshot'
      AND index_name = 'uq_market_index_snapshot_evaluation'
);
SET @add_evaluation_snapshot_index = IF(
    @has_evaluation_snapshot_index = 0,
    'ALTER TABLE market_index_snapshot ADD UNIQUE KEY uq_market_index_snapshot_evaluation (evaluation_id, index_code, index_version)',
    'SELECT 1'
);
PREPARE add_evaluation_snapshot_index_stmt FROM @add_evaluation_snapshot_index;
EXECUTE add_evaluation_snapshot_index_stmt;
DEALLOCATE PREPARE add_evaluation_snapshot_index_stmt;

SET @has_revision_lookup_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_index_snapshot'
      AND index_name = 'ix_market_index_snapshot_revision'
);
SET @add_revision_lookup_index = IF(
    @has_revision_lookup_index = 0,
    'ALTER TABLE market_index_snapshot ADD KEY ix_market_index_snapshot_revision (index_code, index_version, effective_date, evaluation_id)',
    'SELECT 1'
);
PREPARE add_revision_lookup_index_stmt FROM @add_revision_lookup_index;
EXECUTE add_revision_lookup_index_stmt;
DEALLOCATE PREPARE add_revision_lookup_index_stmt;

SET @has_evaluation_snapshot_fk = (
    SELECT COUNT(*) FROM information_schema.referential_constraints
    WHERE constraint_schema = DATABASE()
      AND table_name = 'market_index_snapshot'
      AND constraint_name = 'fk_market_index_snapshot_evaluation'
);
SET @add_evaluation_snapshot_fk = IF(
    @has_evaluation_snapshot_fk = 0,
    'ALTER TABLE market_index_snapshot ADD CONSTRAINT fk_market_index_snapshot_evaluation FOREIGN KEY (evaluation_id) REFERENCES market_alert_evaluation(id)',
    'SELECT 1'
);
PREPARE add_evaluation_snapshot_fk_stmt FROM @add_evaluation_snapshot_fk;
EXECUTE add_evaluation_snapshot_fk_stmt;
DEALLOCATE PREPARE add_evaluation_snapshot_fk_stmt;

-- A derived revision is not publishable merely because rows were materialized.
-- run_daily moves this gate from pending to passed only after the source,
-- volume, freshness and presentation audit succeeds.
SET @has_publish_gate_column = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND column_name = 'publish_gate_status'
);
SET @add_publish_gate_column = IF(
    @has_publish_gate_column = 0,
    'ALTER TABLE market_alert_evaluation ADD COLUMN publish_gate_status VARCHAR(16) NOT NULL DEFAULT ''pending'' AFTER coverage_status',
    'SELECT 1'
);
PREPARE add_publish_gate_column_stmt FROM @add_publish_gate_column;
EXECUTE add_publish_gate_column_stmt;
DEALLOCATE PREPARE add_publish_gate_column_stmt;

SET @has_publish_gate_passed_at = (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND column_name = 'publish_gate_passed_at'
);
SET @add_publish_gate_passed_at = IF(
    @has_publish_gate_passed_at = 0,
    'ALTER TABLE market_alert_evaluation ADD COLUMN publish_gate_passed_at DATETIME(6) NULL AFTER publish_gate_status',
    'SELECT 1'
);
PREPARE add_publish_gate_passed_at_stmt FROM @add_publish_gate_passed_at;
EXECUTE add_publish_gate_passed_at_stmt;
DEALLOCATE PREPARE add_publish_gate_passed_at_stmt;

SET @has_publish_gate_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND index_name = 'ix_market_alert_evaluation_publish_gate'
);
SET @add_publish_gate_index = IF(
    @has_publish_gate_index = 0,
    'ALTER TABLE market_alert_evaluation ADD KEY ix_market_alert_evaluation_publish_gate (index_code, publish_gate_status, effective_date, id)',
    'SELECT 1'
);
PREPARE add_publish_gate_index_stmt FROM @add_publish_gate_index;
EXECUTE add_publish_gate_index_stmt;
DEALLOCATE PREPARE add_publish_gate_index_stmt;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('012');
