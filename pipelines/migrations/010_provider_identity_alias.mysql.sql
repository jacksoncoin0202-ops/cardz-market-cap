-- Verified provider aliases bound to one canonical CARDZ printing.
-- A GemRate universal/member/spec ID is lookup evidence, never a CARDZ ID.

CREATE TABLE IF NOT EXISTS catalog_provider_identity_alias (
    provider_code VARCHAR(32) NOT NULL,
    alias_type VARCHAR(32) NOT NULL,
    alias_value VARCHAR(191) NOT NULL,
    grader_code VARCHAR(16) NOT NULL DEFAULT '',
    requested_external_entity_id CHAR(40) NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    match_status VARCHAR(24) NOT NULL,
    receipt_payload_sha256 CHAR(64) NOT NULL,
    source_pointer VARCHAR(255) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (
        provider_code, alias_type, alias_value, grader_code,
        requested_external_entity_id
    ),
    KEY ix_provider_identity_alias_lookup (
        provider_code, alias_type, alias_value, grader_code
    ),
    KEY ix_provider_identity_alias_variant (variant_id),
    CONSTRAINT fk_provider_identity_alias_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_provider_identity_alias_requested
        FOREIGN KEY (provider_code, requested_external_entity_id)
        REFERENCES catalog_source_identity(source_code, external_entity_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('010');
