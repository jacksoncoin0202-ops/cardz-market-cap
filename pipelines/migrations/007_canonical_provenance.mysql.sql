-- Complete canonical printing identity and preserve population transport provenance.
-- Additive and replay-safe on MySQL 5.7/8.x. Apply after 006.

CREATE TABLE IF NOT EXISTS catalog_printing_identity (
    variant_id BIGINT UNSIGNED NOT NULL,
    tcg_code VARCHAR(32) NOT NULL,
    set_name VARCHAR(255) NOT NULL,
    collector_number VARCHAR(96) NOT NULL,
    card_language VARCHAR(8) NOT NULL,
    edition_code VARCHAR(96) NOT NULL DEFAULT '',
    parallel_code VARCHAR(96) NOT NULL DEFAULT '',
    finish_code VARCHAR(96) NOT NULL DEFAULT '',
    canonical_printing_sha256 CHAR(64) NOT NULL,
    identity_status VARCHAR(24) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id),
    UNIQUE KEY uq_catalog_printing_identity_sha (canonical_printing_sha256),
    KEY ix_catalog_printing_identity_market (tcg_code, identity_status),
    CONSTRAINT fk_catalog_printing_identity_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_population_transport_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    authority_code VARCHAR(32) NOT NULL,
    transport_code VARCHAR(48) NOT NULL,
    grader_code VARCHAR(8) NOT NULL,
    grade_label VARCHAR(32) NOT NULL,
    population_value INT UNSIGNED NOT NULL,
    effective_date DATE NOT NULL,
    fetched_at DATETIME(6) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_population_transport_daily
        (variant_id, authority_code, transport_code, grader_code, grade_label, effective_date),
    KEY ix_population_transport_resolve (variant_id, grader_code, effective_date),
    CONSTRAINT fk_population_transport_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_population_transport_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_story_pointer (
    variant_id BIGINT UNSIGNED NOT NULL,
    locale_code VARCHAR(8) NOT NULL,
    source_path VARCHAR(500) NOT NULL,
    source_version_sha256 CHAR(64) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id, locale_code, source_version_sha256),
    KEY ix_catalog_story_pointer_latest (variant_id, locale_code, observed_at),
    CONSTRAINT fk_catalog_story_pointer_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_image_source_pointer (
    variant_id BIGINT UNSIGNED NOT NULL,
    image_kind VARCHAR(24) NOT NULL,
    remote_url_sha256 CHAR(64) NOT NULL,
    source_path VARCHAR(500) NOT NULL,
    source_version_sha256 CHAR(64) NOT NULL,
    public_allowed TINYINT(1) NOT NULL DEFAULT 0,
    observed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id, image_kind, source_version_sha256),
    KEY ix_market_image_source_pointer_latest (variant_id, image_kind, observed_at),
    CONSTRAINT fk_market_image_source_pointer_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('007');
