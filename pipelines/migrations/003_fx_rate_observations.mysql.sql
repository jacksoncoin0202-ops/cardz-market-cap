-- CARDZ Market Cap daily foreign-exchange observations.
-- Compatible with standalone MySQL 5.7/8.x. Apply after 002.

CREATE TABLE IF NOT EXISTS market_fx_rate_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    base_currency CHAR(3) NOT NULL,
    quote_currency CHAR(3) NOT NULL,
    rate DECIMAL(24,10) NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    effective_date DATE NOT NULL,
    fetched_at DATETIME(6) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_fx_rate_daily (base_currency, quote_currency, effective_date),
    KEY ix_market_fx_rate_run (run_id),
    KEY ix_market_fx_rate_lookup (base_currency, quote_currency, effective_at),
    CONSTRAINT fk_market_fx_rate_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('003');
