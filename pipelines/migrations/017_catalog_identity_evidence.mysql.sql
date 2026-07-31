-- Per-variant identity evidence ledger.
-- One opaque catalog_variant.id owns every bind / URL / POP / human verification.
-- Additive and replay-safe. Does not delete catalog_source_identity.

CREATE TABLE IF NOT EXISTS catalog_identity_evidence (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    -- bind | pop | price | sales | image | human_review | external_url | agent_receipt
    evidence_kind VARCHAR(48) NOT NULL,
    -- gemrate | snkrdunk | snk_psa10 | ebay | pricecharting | psa | tcgpricelookup | human | agent
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NULL,
    external_url VARCHAR(1000) NULL,
    -- exact | conflict | derived | verified | rejected | attached | pending
    match_status VARCHAR(24) NOT NULL DEFAULT 'attached',
    -- free-form claim: pop, grade, title, note, rank, etc. (JSON text)
    claim_json MEDIUMTEXT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    actor VARCHAR(191) NOT NULL DEFAULT 'system',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_identity_evidence_dedupe
        (variant_id, evidence_kind, source_code, evidence_sha256),
    KEY ix_identity_evidence_variant (variant_id, evidence_kind, match_status),
    KEY ix_identity_evidence_source (source_code, external_entity_id),
    CONSTRAINT fk_identity_evidence_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('017');
