-- CARDZ 036: rebuild bookkeeping. Checkpoints live in the DB (not files)
-- because the thing being rebuilt IS the DB - per-stage atomicity comes free.
-- None of these names existed before 036 (verified 2026-08-08).

CREATE TABLE IF NOT EXISTS cardz_rebuild_generation (
    generation_id VARCHAR(64) NOT NULL PRIMARY KEY,
    created_at DATETIME(6) NOT NULL,
    policy_sha256 CHAR(64) NOT NULL,
    min_pop INT UNSIGNED NOT NULL,
    admitted_count INT UNSIGNED NULL,
    activated_at DATETIME(6) NULL,
    activation_receipt_sha256 CHAR(64) NULL,
    CONSTRAINT ck_rebuild_generation_id CHECK (generation_id REGEXP '^[0-9]{3}_[0-9]{8}T[0-9]{6}Z$')
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS cardz_rebuild_checkpoint (
    generation_id VARCHAR(64) NOT NULL,
    stage VARCHAR(32) NOT NULL,
    status VARCHAR(16) NOT NULL,
    attempt INT UNSIGNED NOT NULL DEFAULT 1,
    input_sha256 CHAR(64) NULL,
    output_sha256 CHAR(64) NULL,
    counts_json JSON NULL,
    started_at DATETIME(6) NULL,
    finished_at DATETIME(6) NULL,
    error_code VARCHAR(64) NULL,
    PRIMARY KEY (generation_id, stage),
    CONSTRAINT ck_rebuild_checkpoint_status CHECK (status IN ('pending', 'running', 'complete', 'failed'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS catalog_rebuild_member (
    generation_id VARCHAR(64) NOT NULL,
    gemrate_id CHAR(40) NOT NULL,
    variant_id BIGINT UNSIGNED NULL,
    latest_psa10_population INT UNSIGNED NOT NULL,
    cohort VARCHAR(32) NOT NULL,
    identity_pending TINYINT(1) NOT NULL DEFAULT 0,
    detail_json JSON NULL,
    computed_at DATETIME(6) NOT NULL,
    PRIMARY KEY (generation_id, gemrate_id),
    KEY ix_rebuild_member_variant (generation_id, variant_id),
    CONSTRAINT ck_rebuild_member_gemrate_id CHECK (gemrate_id REGEXP '^[0-9a-f]{40}$'),
    CONSTRAINT ck_rebuild_member_cohort CHECK (cohort IN ('qualified_identity', 'qualified_market_pending', 'product_ready', 'non_qualified'))
) ENGINE=InnoDB;

-- Real incidents only: pop decrease on one entity, requested id resettling to a
-- different canonical entity, an accepted binding moving slug/language/set/
-- parallel, one variant proving to mix printings, cross-id swaps jumping POP.
-- Two independent printings discovered by full crawl are NOT an incident, and
-- neither is POP rising on one identity.
CREATE TABLE IF NOT EXISTS catalog_population_identity_incident (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    generation_id VARCHAR(64) NOT NULL,
    gemrate_id CHAR(40) NULL,
    variant_id BIGINT UNSIGNED NULL,
    incident_kind VARCHAR(64) NOT NULL,
    detail_json JSON NULL,
    opened_at DATETIME(6) NOT NULL,
    resolved_at DATETIME(6) NULL,
    resolution VARCHAR(128) NULL,
    UNIQUE KEY uq_pop_identity_incident (generation_id, gemrate_id, incident_kind),
    KEY ix_pop_incident_variant (variant_id),
    CONSTRAINT ck_pop_incident_kind CHECK (incident_kind IN ('pop_decrease_same_entity', 'requested_id_resettled', 'accepted_binding_moved', 'variant_mixed_printings', 'cross_id_pop_jump'))
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS cardz_rebuild_prune_progress (
    generation_id VARCHAR(64) NOT NULL,
    table_name VARCHAR(64) NOT NULL,
    batch_index INT UNSIGNED NOT NULL,
    rows_deleted INT UNSIGNED NOT NULL,
    finished_at DATETIME(6) NOT NULL,
    PRIMARY KEY (generation_id, table_name, batch_index)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS cardz_rebuild_validation_receipt (
    generation_id VARCHAR(64) NOT NULL,
    receipt_sha256 CHAR(64) NOT NULL,
    passed TINYINT(1) NOT NULL,
    validator_version VARCHAR(32) NOT NULL,
    report_json JSON NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (generation_id, receipt_sha256),
    CONSTRAINT ck_validation_receipt_sha CHECK (receipt_sha256 REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB;

-- Image decisions survive variant deletion via stable content+printing keys.
-- Deliberately no FK: rows must outlive the variants they came from.
CREATE TABLE IF NOT EXISTS market_image_decision_archive (
    content_sha256 CHAR(64) NOT NULL,
    canonical_printing_sha256 CHAR(64) NOT NULL,
    decision VARCHAR(16) NOT NULL,
    original_variant_id BIGINT UNSIGNED NULL,
    source_table VARCHAR(64) NOT NULL,
    decided_at DATETIME(6) NULL,
    archived_at DATETIME(6) NOT NULL,
    generation_id VARCHAR(64) NOT NULL,
    detail_json JSON NULL,
    PRIMARY KEY (content_sha256, canonical_printing_sha256, decision),
    CONSTRAINT ck_image_decision CHECK (decision IN ('approved', 'rejected'))
) ENGINE=InnoDB;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('036');
