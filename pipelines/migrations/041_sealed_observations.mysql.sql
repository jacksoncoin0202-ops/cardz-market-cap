-- Sealed (原盒) observations (2026-08-14).
-- kind discipline: sold ticks -> market_sealed_sale_observation;
-- market/ask/buyback reference series -> market_sealed_price_observation.
-- Never mixed. Missing price stays missing; no fake zero.
-- Compatible with MySQL 5.7 and MySQL 8.x.

CREATE TABLE IF NOT EXISTS market_sealed_price_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sealed_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    price_kind VARCHAR(16) NOT NULL,
    observed_date DATE NOT NULL,
    native_price DECIMAL(14,2) NULL,
    native_currency VARCHAR(8) NULL,
    price_usd DECIMAL(14,2) NULL,
    external_entity_id VARCHAR(191) NOT NULL DEFAULT '',
    source_url VARCHAR(700) NULL,
    metric_status VARCHAR(32) NOT NULL DEFAULT 'ok',
    ingest_run_key VARCHAR(96) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_price (sealed_id, source_code, price_kind, observed_date),
    KEY ix_sealed_price_date (observed_date, source_code),
    CONSTRAINT fk_sealed_price_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Append-only sold prints. Dedupe key = (source_code, lot_id).
CREATE TABLE IF NOT EXISTS market_sealed_sale_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sealed_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    lot_id VARCHAR(191) NOT NULL,
    sold_at DATETIME(6) NOT NULL,
    unit_price_usd DECIMAL(14,2) NULL,
    native_price DECIMAL(14,2) NULL,
    native_currency VARCHAR(8) NULL,
    quantity INT NOT NULL DEFAULT 1,
    total_native_price DECIMAL(14,2) NULL,
    box_condition VARCHAR(24) NOT NULL DEFAULT 'unknown',
    title VARCHAR(500) NULL,
    raw_url VARCHAR(700) NULL,
    transaction_fingerprint CHAR(64) NOT NULL DEFAULT '',
    metric_status VARCHAR(32) NOT NULL DEFAULT 'ok',
    parser VARCHAR(64) NOT NULL DEFAULT '',
    ingest_run_key VARCHAR(96) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_sale_lot (source_code, lot_id),
    KEY ix_sealed_sale_product (sealed_id, source_code, sold_at),
    KEY ix_sealed_sale_date (sold_at),
    CONSTRAINT fk_sealed_sale_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Composer output: one row per sealed per day. FE historyDaily reads this.
CREATE TABLE IF NOT EXISTS market_sealed_daily_aggregate (
    sealed_id BIGINT UNSIGNED NOT NULL,
    observed_date DATE NOT NULL,
    sold_count INT NOT NULL DEFAULT 0,
    sold_value_usd DECIMAL(16,2) NULL,
    vwap_usd DECIMAL(14,2) NULL,
    composed_price_usd DECIMAL(14,2) NULL,
    composed_native_price DECIMAL(14,2) NULL,
    composed_native_currency VARCHAR(8) NULL,
    composed_kind VARCHAR(16) NULL,
    composed_source VARCHAR(32) NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (sealed_id, observed_date),
    KEY ix_sealed_daily_date (observed_date),
    CONSTRAINT fk_sealed_daily_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Pull-first wide store for sealed. Mirror of market_source_warehouse (022),
-- separate table because 022 FKs variant_id to catalog_variant.
CREATE TABLE IF NOT EXISTS market_sealed_source_warehouse (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sealed_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL DEFAULT '',
    observation_kind VARCHAR(64) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    field_map_json JSON NOT NULL,
    raw_payload_json JSON NULL,
    content_sha256 CHAR(64) NOT NULL,
    ingest_run_key VARCHAR(96) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_warehouse_hash (source_code, external_entity_id, observation_kind, content_sha256),
    KEY ix_sealed_warehouse_product (sealed_id, source_code, observed_at),
    KEY ix_sealed_warehouse_kind (observation_kind, observed_at),
    CONSTRAINT fk_sealed_warehouse_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('041');
