-- Sealed (原盒) image assets (2026-08-14).
-- Mirror of market_image_asset; separate table because 002 FKs catalog_variant.
-- Display requires an accepted operator_sealed_binding_freeze kind='image'
-- whose external_entity_id equals the chosen content_sha256.

CREATE TABLE IF NOT EXISTS market_sealed_image_asset (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sealed_id BIGINT UNSIGNED NOT NULL,
    image_kind VARCHAR(24) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    source_code VARCHAR(32) NOT NULL DEFAULT '',
    source_url VARCHAR(700) NULL,
    mime_type VARCHAR(100) NOT NULL,
    width_px INT UNSIGNED NOT NULL,
    height_px INT UNSIGNED NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_image_asset (sealed_id, image_kind, content_sha256),
    KEY ix_sealed_image_asset (sealed_id, image_kind, captured_at),
    CONSTRAINT fk_sealed_image_asset_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('042');
