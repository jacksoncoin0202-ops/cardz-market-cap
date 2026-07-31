-- Additive identity-convergence and manual-review audit schema.
-- Schema only: confirmed duplicate data movement is performed separately by
-- pipelines/identity_convergence.py and is dry-run by default.

CREATE TABLE IF NOT EXISTS catalog_variant_alias (
    duplicate_variant_id BIGINT UNSIGNED NOT NULL,
    canonical_variant_id BIGINT UNSIGNED NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    convergence_plan_sha256 CHAR(64) NOT NULL,
    merged_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (duplicate_variant_id),
    KEY ix_catalog_variant_alias_canonical (canonical_variant_id),
    KEY ix_catalog_variant_alias_plan (convergence_plan_sha256),
    CONSTRAINT fk_catalog_variant_alias_duplicate
        FOREIGN KEY (duplicate_variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_catalog_variant_alias_canonical
        FOREIGN KEY (canonical_variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_identity_review_resolution (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    review_id BIGINT UNSIGNED NOT NULL,
    resolution_action VARCHAR(24) NOT NULL,
    resolved_variant_id BIGINT UNSIGNED NULL,
    reason_text VARCHAR(1000) NOT NULL,
    actor VARCHAR(191) NOT NULL,
    review_evidence_sha256 CHAR(64) NOT NULL,
    outcome_sha256 CHAR(64) NOT NULL,
    resolved_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_identity_review_resolution (review_id),
    KEY ix_market_identity_review_resolution_variant (resolved_variant_id),
    CONSTRAINT fk_market_identity_review_resolution_review
        FOREIGN KEY (review_id) REFERENCES market_identity_review_queue(id),
    CONSTRAINT fk_market_identity_review_resolution_variant
        FOREIGN KEY (resolved_variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('011');
