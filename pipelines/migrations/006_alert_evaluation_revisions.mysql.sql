-- Permit a same-day canonical refresh to create a new immutable evaluation.
-- Exact replays still reuse the row with the same input SHA-256.
-- Compatible with MySQL 5.7/8.x. Apply after 005.

SET @has_old_alert_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND index_name = 'uq_market_alert_evaluation'
);
SET @drop_old_alert_index = IF(
    @has_old_alert_index > 0,
    'ALTER TABLE market_alert_evaluation DROP INDEX uq_market_alert_evaluation',
    'SELECT 1'
);
PREPARE drop_old_alert_index_stmt FROM @drop_old_alert_index;
EXECUTE drop_old_alert_index_stmt;
DEALLOCATE PREPARE drop_old_alert_index_stmt;

SET @has_input_alert_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND index_name = 'uq_market_alert_evaluation_input'
);
SET @add_input_alert_index = IF(
    @has_input_alert_index = 0,
    'ALTER TABLE market_alert_evaluation ADD UNIQUE KEY uq_market_alert_evaluation_input (index_code, index_version, effective_date, policy_version, input_sha256)',
    'SELECT 1'
);
PREPARE add_input_alert_index_stmt FROM @add_input_alert_index;
EXECUTE add_input_alert_index_stmt;
DEALLOCATE PREPARE add_input_alert_index_stmt;

SET @has_revision_alert_index = (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'market_alert_evaluation'
      AND index_name = 'ix_market_alert_evaluation_revision'
);
SET @add_revision_alert_index = IF(
    @has_revision_alert_index = 0,
    'ALTER TABLE market_alert_evaluation ADD KEY ix_market_alert_evaluation_revision (index_code, index_version, effective_date, policy_version, id)',
    'SELECT 1'
);
PREPARE add_revision_alert_index_stmt FROM @add_revision_alert_index;
EXECUTE add_revision_alert_index_stmt;
DEALLOCATE PREPARE add_revision_alert_index_stmt;
