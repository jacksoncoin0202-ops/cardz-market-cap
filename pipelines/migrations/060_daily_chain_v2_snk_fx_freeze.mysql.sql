-- CARDZ 060: freeze every SNK USD conversion to the rate selected for the
-- observation's own market date.
--
-- Existing sale rows have no reversible native amount and existing price rows
-- have no record of which FX point produced price_usd. Nullable columns are
-- intentional: NULL means "written before 060 and not frozen yet". Writers
-- fill the conversion tuple on their next legitimate observation upsert. This
-- migration contains no historical backfill and is idempotent when replayed
-- after a partially completed apply.

SET @have_sale_native_price := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND COLUMN_NAME = 'native_unit_price'
);
SET @ddl := IF(
    @have_sale_native_price > 0,
    'SELECT 1',
    'ALTER TABLE market_sale_observation
       ADD COLUMN native_unit_price DECIMAL(20,6) NULL AFTER unit_price_usd'
);
PREPARE add_sale_native_price FROM @ddl;
EXECUTE add_sale_native_price;
DEALLOCATE PREPARE add_sale_native_price;

SET @have_sale_native_currency := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND COLUMN_NAME = 'native_currency'
);
SET @ddl := IF(
    @have_sale_native_currency > 0,
    'SELECT 1',
    'ALTER TABLE market_sale_observation
       ADD COLUMN native_currency CHAR(3) NULL AFTER native_unit_price'
);
PREPARE add_sale_native_currency FROM @ddl;
EXECUTE add_sale_native_currency;
DEALLOCATE PREPARE add_sale_native_currency;

SET @have_sale_fx_rate := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND COLUMN_NAME = 'fx_rate_used'
);
SET @ddl := IF(
    @have_sale_fx_rate > 0,
    'SELECT 1',
    'ALTER TABLE market_sale_observation
       ADD COLUMN fx_rate_used DECIMAL(20,8) NULL AFTER native_currency'
);
PREPARE add_sale_fx_rate FROM @ddl;
EXECUTE add_sale_fx_rate;
DEALLOCATE PREPARE add_sale_fx_rate;

SET @have_sale_fx_date := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_sale_observation'
      AND COLUMN_NAME = 'fx_rate_as_of'
);
SET @ddl := IF(
    @have_sale_fx_date > 0,
    'SELECT 1',
    'ALTER TABLE market_sale_observation
       ADD COLUMN fx_rate_as_of DATE NULL AFTER fx_rate_used'
);
PREPARE add_sale_fx_date FROM @ddl;
EXECUTE add_sale_fx_date;
DEALLOCATE PREPARE add_sale_fx_date;

-- market_price_observation already owns native_price/native_currency from 004.
SET @have_price_fx_rate := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_price_observation'
      AND COLUMN_NAME = 'fx_rate_used'
);
SET @ddl := IF(
    @have_price_fx_rate > 0,
    'SELECT 1',
    'ALTER TABLE market_price_observation
       ADD COLUMN fx_rate_used DECIMAL(20,8) NULL AFTER native_currency'
);
PREPARE add_price_fx_rate FROM @ddl;
EXECUTE add_price_fx_rate;
DEALLOCATE PREPARE add_price_fx_rate;

SET @have_price_fx_date := (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'market_price_observation'
      AND COLUMN_NAME = 'fx_rate_as_of'
);
SET @ddl := IF(
    @have_price_fx_date > 0,
    'SELECT 1',
    'ALTER TABLE market_price_observation
       ADD COLUMN fx_rate_as_of DATE NULL AFTER fx_rate_used'
);
PREPARE add_price_fx_date FROM @ddl;
EXECUTE add_price_fx_date;
DEALLOCATE PREPARE add_price_fx_date;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('060');
