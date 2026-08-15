-- Sealed (原盒) catalog: sibling identity line to catalog_variant (2026-08-14).
-- Boxes never join the PSA10 GemRate gate / universe lock / latest_prices().
-- Identity grammar: sku_id = {game}:{lang}:{set_code}:{product}:{print}
-- Compatible with MySQL 5.7 and MySQL 8.x.

CREATE TABLE IF NOT EXISTS catalog_sealed_product (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sku_id VARCHAR(128) NOT NULL,
    slug VARCHAR(160) NOT NULL,
    game VARCHAR(16) NOT NULL,
    lang VARCHAR(8) NOT NULL,
    group_code VARCHAR(16) NOT NULL,
    set_code VARCHAR(48) NOT NULL,
    name_en VARCHAR(255) NOT NULL,
    name_jp VARCHAR(255) NULL,
    release_month VARCHAR(10) NULL,
    packs_per_box INT NOT NULL DEFAULT 0,
    product_kind VARCHAR(32) NOT NULL,
    print_wave VARCHAR(16) NOT NULL DEFAULT 'std',
    era VARCHAR(24) NULL,
    msrp_amount DECIMAL(12,2) NULL,
    msrp_currency VARCHAR(8) NULL,
    official_url VARCHAR(600) NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'active',
    notes VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_product_sku (sku_id),
    UNIQUE KEY uq_sealed_product_slug (slug),
    KEY ix_sealed_product_group (group_code, status),
    KEY ix_sealed_product_set (game, lang, set_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Exact source binds only. Search/bootstrap URLs live in catalog_sealed_source_hint.
CREATE TABLE IF NOT EXISTS catalog_sealed_source_identity (
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    sealed_id BIGINT UNSIGNED NOT NULL,
    canonical_url VARCHAR(600) NULL,
    match_status VARCHAR(24) NOT NULL,
    resolved TINYINT(1) NOT NULL DEFAULT 0,
    evidence_sha256 CHAR(64) NOT NULL DEFAULT '',
    note VARCHAR(500) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (source_code, external_entity_id),
    KEY ix_sealed_source_identity_product (sealed_id, source_code),
    CONSTRAINT fk_sealed_source_identity_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Bootstrap URLs (search pages, unverified candidate pages, official pages,
-- image hints). Resolver consumes hints; never treated as exact identity.
CREATE TABLE IF NOT EXISTS catalog_sealed_source_hint (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    sealed_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    hint_kind VARCHAR(24) NOT NULL,
    url VARCHAR(700) NOT NULL,
    url_sha256 CHAR(64) NOT NULL,
    note VARCHAR(500) NULL,
    consumed TINYINT(1) NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_sealed_source_hint (sealed_id, source_code, hint_kind, url_sha256),
    KEY ix_sealed_source_hint_lookup (source_code, consumed),
    CONSTRAINT fk_sealed_source_hint_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Freeze ledger mirror of operator_binding_freeze (021) for sealed products.
-- Once accepted, identity/source/image freeze; price/sales stay writable.
CREATE TABLE IF NOT EXISTS operator_sealed_binding_freeze (
    sealed_id BIGINT UNSIGNED NOT NULL,
    freeze_kind VARCHAR(32) NOT NULL,
    source_code VARCHAR(32) NOT NULL DEFAULT '',
    external_entity_id VARCHAR(191) NOT NULL DEFAULT '',
    content_sha256 CHAR(64) NOT NULL DEFAULT '',
    acceptance_status VARCHAR(16) NOT NULL,
    actor VARCHAR(191) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    note VARCHAR(1000) NULL,
    accepted_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (sealed_id, freeze_kind, source_code),
    KEY ix_sealed_binding_freeze_status (acceptance_status, freeze_kind),
    CONSTRAINT fk_sealed_binding_freeze_product
        FOREIGN KEY (sealed_id) REFERENCES catalog_sealed_product(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Read model helper (not a second authority), mirrors operator_card_product_projection.
CREATE OR REPLACE VIEW operator_sealed_product_projection AS
SELECT
  p.id AS sealed_id,
  p.sku_id,
  p.slug,
  p.game,
  p.lang,
  p.group_code,
  p.set_code,
  p.name_en,
  p.status,
  EXISTS(
    SELECT 1 FROM operator_sealed_binding_freeze f
    WHERE f.sealed_id=p.id AND f.freeze_kind='identity' AND f.acceptance_status='accepted'
  ) AS identity_frozen,
  EXISTS(
    SELECT 1 FROM operator_sealed_binding_freeze f
    WHERE f.sealed_id=p.id AND f.freeze_kind='source' AND f.acceptance_status='accepted'
  ) AS source_frozen,
  EXISTS(
    SELECT 1 FROM operator_sealed_binding_freeze f
    WHERE f.sealed_id=p.id AND f.freeze_kind='image' AND f.acceptance_status='accepted'
  ) AS image_frozen
FROM catalog_sealed_product p;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('040');
