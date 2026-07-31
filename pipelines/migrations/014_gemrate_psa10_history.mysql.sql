-- Preserve the existing GemRate PSA history files inside the one operational DB.
-- A nullable variant_id keeps unmatched provider identities visible without guessing.

CREATE TABLE IF NOT EXISTS market_gemrate_psa10_history (
    gemrate_id CHAR(40) NOT NULL,
    variant_id BIGINT UNSIGNED NULL,
    observed_date DATE NOT NULL,
    psa10_population INT UNSIGNED NOT NULL,
    psa_total_population INT UNSIGNED NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    ingested_at DATETIME(6) NOT NULL,
    PRIMARY KEY (gemrate_id, observed_date),
    KEY ix_gemrate_psa10_history_variant_date (variant_id, observed_date),
    CONSTRAINT fk_gemrate_psa10_history_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('014');
