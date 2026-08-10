-- Covering index for the rank check inside operator_canonical_current_metric_projection.
--
-- Measured 2026-08-10: that view verifies canonical_market_rank with a
-- correlated `1 + (SELECT COUNT(0) ... WHERE market_cap_usd > ...)`, which ran
-- 44,919 times and cost 38,091 ms of stage_validate's 67,343 ms (56.6%).
-- Neither unique key carries market_cap_usd, so every loop scanned ~941 index
-- entries and hit the PK for the value. This index answers the count from the
-- index alone.
--
-- Measured after applying, same rows, same instant, IGNORE INDEX as the control:
-- 20,852 ms without -> 8,793 ms with (2.37x), both returning 9082. Same answer
-- either way is the point: an index may only change how long the truth takes.
--
-- Pure DDL, no semantic surface: an index changes which plan MySQL picks, never
-- which rows it returns. Idempotent by explicit check because MySQL 8 has no
-- CREATE INDEX IF NOT EXISTS, and this runner replays a half-applied file.
SET @have_index := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_canonical_metric_acceptance'
      AND INDEX_NAME = 'ix_canonical_metric_generation_cap'
);
SET @ddl := IF(
    @have_index > 0,
    'SELECT 1',
    'ALTER TABLE market_canonical_metric_acceptance ADD INDEX ix_canonical_metric_generation_cap (ranking_generation_sha256, market_cap_usd, variant_id, canonical_market_rank)'
);
PREPARE add_index FROM @ddl;
EXECUTE add_index;
DEALLOCATE PREPARE add_index;
