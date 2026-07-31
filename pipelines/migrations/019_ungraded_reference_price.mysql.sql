-- PriceCharting Ungraded (raw card) reference values.
--
-- This table is intentionally separate from market_price_observation: the
-- latter has no condition dimension and remains PSA 10 only.  These records
-- are optional card-detail reference evidence, never ranking or market-cap
-- inputs.

CREATE TABLE IF NOT EXISTS market_ungraded_reference_price (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    source_url VARCHAR(1000) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    price_usd DECIMAL(18,6) NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ungraded_reference_observation
        (variant_id, source_code, external_entity_id, observed_at, source_payload_sha256),
    KEY ix_ungraded_reference_latest (variant_id, observed_at),
    KEY ix_ungraded_reference_source (source_code, external_entity_id),
    CONSTRAINT fk_ungraded_reference_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('019');
