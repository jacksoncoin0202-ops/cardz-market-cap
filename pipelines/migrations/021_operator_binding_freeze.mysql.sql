-- Operator freeze ledger: once accepted, identity/source/image bindings
-- stay frozen for batch reopen. Volatile price/sales/POP remain writable.

CREATE TABLE IF NOT EXISTS operator_binding_freeze (
    variant_id BIGINT UNSIGNED NOT NULL,
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
    PRIMARY KEY (variant_id, freeze_kind, source_code),
    KEY ix_operator_binding_freeze_status (acceptance_status, freeze_kind),
    CONSTRAINT fk_operator_binding_freeze_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('021');
