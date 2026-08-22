-- CARDZ 054: the source registry declares its own discovery lane and route
-- priority, so the orchestrator stops naming providers.
--
-- 051 gave every source a row but no way to say "I am the browser identity
-- lane" or "route me at priority 20". daily_chain_v2.py therefore hard-coded
-- the ('http','browser') lane pair and its two concurrency groups, and the
-- registry stage could not give a newly registered quote source any route
-- policy row at all. Both columns below are declarations, not behaviour: the
-- adapter's SourceSpec carries the same two fields, pipelines/
-- daily_chain_v2_db.py:sync_source_registry writes them here, and
-- daily_chain_v2_contract.py:identity_lanes / route_policy_upserts read them.
--
-- Additive and idempotent (the runner replays a half-applied file). Nothing is
-- dropped, no existing market_quote_route_policy priority is rewritten: the
-- backfill only records the priorities 051 already granted.

SET @have_lane := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_source_registry'
      AND COLUMN_NAME = 'identity_lane'
);
SET @ddl := IF(
    @have_lane > 0,
    'SELECT 1',
    'ALTER TABLE market_source_registry
       ADD COLUMN identity_lane varchar(32) NULL AFTER required_class,
       ADD COLUMN route_priority int unsigned NULL AFTER identity_lane'
);
PREPARE add_source_lanes FROM @ddl;
EXECUTE add_source_lanes;
DEALLOCATE PREPARE add_source_lanes;

-- Today's two lanes, stated as data. gemrate and fx stay NULL: they publish no
-- discovery lane, which is exactly why the literal pair was wrong.
UPDATE market_source_registry
SET identity_lane='http'
WHERE source_code='snkrdunk' AND identity_lane IS NULL;

UPDATE market_source_registry
SET identity_lane='browser'
WHERE source_code='pricecharting' AND identity_lane IS NULL;

-- The '*' priorities cardz-route-v1 already granted, so a re-registration can
-- never re-price an existing source.
UPDATE market_source_registry
SET route_priority=10
WHERE source_code IN ('snkrdunk','snk_psa10','snk') AND route_priority IS NULL;

UPDATE market_source_registry
SET route_priority=20
WHERE source_code='pricecharting' AND route_priority IS NULL;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('054');
