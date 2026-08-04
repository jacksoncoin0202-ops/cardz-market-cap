-- New-era warehouse + banned-source ledger (2026-08-03)
-- Pull-first store; product projects few fields.

CREATE TABLE IF NOT EXISTS market_source_warehouse (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
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
    UNIQUE KEY uq_source_warehouse_hash (source_code, external_entity_id, observation_kind, content_sha256),
    KEY ix_source_warehouse_variant (variant_id, source_code, observed_at),
    KEY ix_source_warehouse_kind (observation_kind, observed_at),
    CONSTRAINT fk_source_warehouse_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_banned_source_policy (
    source_code VARCHAR(32) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    policy VARCHAR(24) NOT NULL,
    note VARCHAR(500) NULL,
    effective_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO market_banned_source_policy (source_code, reason_code, policy, note, effective_at)
VALUES
  ('g10_kline', 'banned_series', 'ignore_for_price', 'New-era: g10_kline banned for price/market-cap/sales authority', UTC_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE
  reason_code=VALUES(reason_code),
  policy=VALUES(policy),
  note=VALUES(note),
  effective_at=VALUES(effective_at);

-- Product projection helper (read model; not a second authority)
CREATE OR REPLACE VIEW operator_card_product_projection AS
SELECT
  v.id AS variant_id,
  v.opaque_id,
  v.tcg_code,
  v.card_language,
  v.canonical_name,
  v.set_name,
  v.collector_number,
  v.identity_status,
  EXISTS(
    SELECT 1 FROM operator_binding_freeze f
    WHERE f.variant_id=v.id AND f.freeze_kind='identity' AND f.acceptance_status='accepted'
  ) AS identity_frozen,
  EXISTS(
    SELECT 1 FROM operator_binding_freeze f
    WHERE f.variant_id=v.id AND f.freeze_kind='source' AND f.acceptance_status='accepted'
  ) AS source_frozen,
  EXISTS(
    SELECT 1 FROM operator_binding_freeze f
    WHERE f.variant_id=v.id AND f.freeze_kind='image' AND f.acceptance_status='accepted'
  ) AS image_frozen
FROM catalog_variant v;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('022');
