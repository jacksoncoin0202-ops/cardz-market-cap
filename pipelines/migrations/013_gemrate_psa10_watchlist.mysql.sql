-- Provider discovery table for the one CARDZ Market Cap standard:
-- GemRate, PSA, grade 10, population strictly greater than 1000.
-- Unmapped provider identities remain visible without guessing a canonical card.

CREATE TABLE IF NOT EXISTS market_gemrate_psa10_watchlist (
    gemrate_id CHAR(40) NOT NULL,
    variant_id BIGINT UNSIGNED NULL,
    gemrate_checklist_id CHAR(40) NULL,
    card_name VARCHAR(255) NOT NULL,
    set_name VARCHAR(255) NOT NULL,
    collector_number VARCHAR(96) NOT NULL,
    release_year SMALLINT UNSIGNED NULL,
    psa10_population INT UNSIGNED NOT NULL,
    psa_total_population INT UNSIGNED NULL,
    population_as_of DATE NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    first_seen_at DATETIME(6) NOT NULL,
    last_seen_at DATETIME(6) NOT NULL,
    PRIMARY KEY (gemrate_id),
    KEY ix_gemrate_psa10_watchlist_population (psa10_population),
    KEY ix_gemrate_psa10_watchlist_variant (variant_id),
    CONSTRAINT fk_gemrate_psa10_watchlist_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('013');
