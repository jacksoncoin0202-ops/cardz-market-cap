-- Standalone CARDZ Market Cap canonical identity schema.
-- Compatible with MySQL 5.7 and MySQL 8.x.

CREATE TABLE IF NOT EXISTS cardz_schema_version (
    version_code VARCHAR(32) NOT NULL,
    applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (version_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_variant (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    opaque_id VARCHAR(96) NOT NULL,
    tcg_code VARCHAR(32) NOT NULL,
    card_language VARCHAR(8) NOT NULL,
    canonical_name VARCHAR(255) NOT NULL,
    set_name VARCHAR(255) NOT NULL,
    collector_number VARCHAR(96) NOT NULL,
    identity_status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_catalog_variant_opaque (opaque_id),
    KEY ix_catalog_variant_printing (tcg_code, card_language, set_name, collector_number),
    KEY ix_catalog_variant_market (tcg_code, card_language, identity_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_variant_locale (
    variant_id BIGINT UNSIGNED NOT NULL,
    locale_code VARCHAR(8) NOT NULL,
    localized_name VARCHAR(255) NULL,
    localized_set_name VARCHAR(255) NULL,
    market_story TEXT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id, locale_code),
    CONSTRAINT fk_catalog_variant_locale_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS catalog_source_identity (
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    match_status VARCHAR(24) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (source_code, external_entity_id),
    KEY ix_catalog_source_identity_variant (variant_id),
    CONSTRAINT fk_catalog_source_identity_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('001');
