-- CARDZ 042: a sale observation must be able to say which listing it was.
--
-- market_sale_observation has carried a transaction_fingerprint and a
-- source_payload_sha256 since 002, and both are hashes. The PriceCharting
-- parser has always produced the three things a human needs to check a sale --
-- the eBay item id, the listing URL, and the listing title
-- (pricecharting_page_parse.py:232-240) -- and both writers threw all three
-- away after folding them into those hashes. So the board could say "1,412
-- PSA 10 sales in 30 days" and nothing in the database could show you one of
-- them.
--
-- Three nullable columns, no generation bump, no backfill here: existing rows
-- get their evidence from the locally saved PriceCharting HTML by fingerprint
-- match (64,356 of 65,845 rows are recoverable that way), which is a script,
-- not a migration. NULL therefore means "landed before 042 and not yet
-- matched", which is the honest state.
--
-- Sizes follow the source: eBay item ids are 9-15 digits, PriceCharting emits
-- absolute ebay.com/itm/ URLs, and the writers already truncate titles to 200.
-- Idempotent because the runner replays a half-applied file.

SET @have_item := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND COLUMN_NAME = 'listing_item_id'
);
SET @ddl := IF(
    @have_item > 0,
    'SELECT 1',
    'ALTER TABLE market_sale_observation
       ADD COLUMN listing_item_id varchar(32) NULL AFTER coverage_status,
       ADD COLUMN listing_url varchar(512) NULL AFTER listing_item_id,
       ADD COLUMN listing_title varchar(255) NULL AFTER listing_url'
);
PREPARE add_listing_evidence FROM @ddl;
EXECUTE add_listing_evidence;
DEALLOCATE PREPARE add_listing_evidence;

-- Lets the backfill and any "show me this sale" lookup go by item id without a
-- table scan of 1.9M rows.
SET @have_index := (
    SELECT COUNT(*) FROM information_schema.STATISTICS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND INDEX_NAME = 'ix_market_sale_observation_listing_item'
);
SET @ddl := IF(
    @have_index > 0,
    'SELECT 1',
    'CREATE INDEX ix_market_sale_observation_listing_item
       ON market_sale_observation (source_code, listing_item_id)'
);
PREPARE add_listing_index FROM @ddl;
EXECUTE add_listing_index;
DEALLOCATE PREPARE add_listing_index;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('042');
