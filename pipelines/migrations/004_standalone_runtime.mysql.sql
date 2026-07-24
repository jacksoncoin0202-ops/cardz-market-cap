-- Standalone active-universe and normalized daily metric tables.
-- Apply after 001, 002 and 003.

CREATE TABLE IF NOT EXISTS market_universe_lock (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    lock_sha256 CHAR(64) NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    policy_json JSON NOT NULL,
    member_count INT UNSIGNED NOT NULL,
    is_current TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_universe_lock_hash (lock_sha256),
    KEY ix_market_universe_lock_current (is_current, effective_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_universe_member (
    universe_lock_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    segment_code VARCHAR(64) NOT NULL,
    member_role VARCHAR(16) NOT NULL,
    market_rank INT UNSIGNED NULL,
    watch_position INT UNSIGNED NULL,
    watch_score DECIMAL(12,6) NULL,
    selection_signals_json JSON NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (universe_lock_id, variant_id),
    KEY ix_market_universe_member_segment (universe_lock_id, segment_code, member_role),
    CONSTRAINT fk_market_universe_member_lock FOREIGN KEY (universe_lock_id) REFERENCES market_universe_lock(id),
    CONSTRAINT fk_market_universe_member_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_price_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    observed_date DATE NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    price_usd DECIMAL(18,6) NULL,
    native_price DECIMAL(20,6) NULL,
    native_currency CHAR(3) NULL,
    source_priority SMALLINT NOT NULL DEFAULT 100,
    metric_status VARCHAR(24) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_price_daily (variant_id, source_code, observed_date),
    KEY ix_market_price_lookup (variant_id, observed_date, source_priority),
    KEY ix_market_price_run (run_id),
    CONSTRAINT fk_market_price_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_market_price_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_daily_sales_aggregate (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    observed_date DATE NOT NULL,
    sales_count INT UNSIGNED NOT NULL,
    sales_value_usd DECIMAL(24,6) NULL,
    native_sales_value DECIMAL(24,6) NULL,
    native_currency CHAR(3) NULL,
    coverage_status VARCHAR(24) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_daily_sales (variant_id, source_code, observed_date),
    KEY ix_market_daily_sales_lookup (variant_id, observed_date),
    KEY ix_market_daily_sales_run (run_id),
    CONSTRAINT fk_market_daily_sales_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_market_daily_sales_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('004');
