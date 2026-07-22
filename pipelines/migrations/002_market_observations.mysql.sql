-- CARDZ Market Cap additive market-observation schema.
-- Target: JLP MySQL 5.7. This migration assumes 001_canonical_card_catalog.sql
-- has already created catalog_variant and its related canonical tables.

CREATE TABLE IF NOT EXISTS market_ingest_run (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_key CHAR(64) NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    ingest_mode VARCHAR(16) NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    manifest_sha256 CHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL,
    observed_count INT UNSIGNED NOT NULL DEFAULT 0,
    accepted_count INT UNSIGNED NOT NULL DEFAULT 0,
    quarantined_count INT UNSIGNED NOT NULL DEFAULT 0,
    rejected_count INT UNSIGNED NOT NULL DEFAULT 0,
    started_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6) NULL,
    error_summary VARCHAR(1000) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_ingest_run_key (run_key),
    KEY ix_market_ingest_run_effective (source_code, effective_at),
    KEY ix_market_ingest_run_status (status, started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_ingest_checkpoint (
    source_code VARCHAR(32) NOT NULL,
    stream_key VARCHAR(100) NOT NULL,
    last_effective_at DATETIME(6) NOT NULL,
    last_payload_sha256 CHAR(64) NOT NULL,
    last_run_id BIGINT UNSIGNED NOT NULL,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (source_code, stream_key),
    KEY ix_market_ingest_checkpoint_run (last_run_id),
    CONSTRAINT fk_market_ingest_checkpoint_run FOREIGN KEY (last_run_id) REFERENCES market_ingest_run(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_source_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    observation_kind VARCHAR(40) NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    observed_date DATE NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    payload_json JSON NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_source_observation (source_code, external_entity_id, observation_kind, observed_date),
    KEY ix_market_source_observation_run (run_id),
    KEY ix_market_source_observation_lookup (source_code, observation_kind, effective_at),
    CONSTRAINT fk_market_source_observation_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_index_snapshot (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    index_code VARCHAR(64) NOT NULL,
    index_version VARCHAR(24) NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    effective_date DATE NOT NULL,
    constituent_count INT UNSIGNED NOT NULL,
    total_market_cap_usd DECIMAL(24,6) NOT NULL,
    snapshot_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_index_snapshot (index_code, index_version, effective_date),
    KEY ix_market_index_snapshot_run (run_id),
    CONSTRAINT fk_market_index_snapshot_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_index_constituent (
    index_snapshot_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    rank_position INT UNSIGNED NOT NULL,
    reference_price_usd DECIMAL(18,6) NOT NULL,
    psa10_population INT UNSIGNED NOT NULL,
    market_cap_usd DECIMAL(24,6) NOT NULL,
    change_30d_pct DECIMAL(12,6) NULL,
    metric_status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (index_snapshot_id, variant_id),
    UNIQUE KEY uq_market_index_constituent_rank (index_snapshot_id, rank_position),
    KEY ix_market_index_constituent_variant (variant_id, index_snapshot_id),
    CONSTRAINT fk_market_index_constituent_snapshot FOREIGN KEY (index_snapshot_id) REFERENCES market_index_snapshot(id),
    CONSTRAINT fk_market_index_constituent_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_grader_population_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    grader_code VARCHAR(8) NOT NULL,
    top_grade_label VARCHAR(32) NOT NULL,
    total_population INT UNSIGNED NOT NULL,
    top_grade_population INT UNSIGNED NOT NULL,
    estimated TINYINT(1) NOT NULL DEFAULT 0,
    effective_at DATETIME(6) NOT NULL,
    observed_date DATE NOT NULL,
    payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_grader_population (variant_id, grader_code, source_code, observed_date),
    KEY ix_market_grader_population_run (run_id),
    KEY ix_market_grader_population_lookup (grader_code, effective_at, variant_id),
    CONSTRAINT fk_market_grader_population_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_market_grader_population_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_sale_observation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    transaction_fingerprint CHAR(64) NOT NULL,
    grader_code VARCHAR(8) NOT NULL,
    grade_label VARCHAR(32) NOT NULL,
    sold_at DATETIME(6) NULL,
    source_date_text VARCHAR(100) NOT NULL,
    fetched_at DATETIME(6) NOT NULL,
    timestamp_quality VARCHAR(24) NOT NULL,
    unit_price_usd DECIMAL(18,6) NOT NULL,
    quantity INT UNSIGNED NOT NULL,
    transaction_value_usd DECIMAL(20,6) NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    coverage_status VARCHAR(24) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_sale_observation (source_code, external_entity_id, transaction_fingerprint),
    KEY ix_market_sale_observation_run (run_id),
    KEY ix_market_sale_observation_variant_date (variant_id, grader_code, grade_label, sold_at),
    CONSTRAINT fk_market_sale_observation_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_market_sale_observation_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_tracked_sales_aggregate (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    grader_code VARCHAR(8) NOT NULL,
    grade_label VARCHAR(32) NOT NULL,
    window_code VARCHAR(8) NOT NULL,
    window_start_at DATETIME(6) NOT NULL,
    window_end_at DATETIME(6) NOT NULL,
    sales_count INT UNSIGNED NOT NULL,
    sales_value_usd DECIMAL(24,6) NOT NULL,
    coverage_status VARCHAR(24) NOT NULL,
    aggregate_sha256 CHAR(64) NOT NULL,
    computed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_tracked_sales_aggregate (variant_id, grader_code, grade_label, window_code, window_end_at, aggregate_sha256),
    KEY ix_market_tracked_sales_window (window_code, window_end_at, variant_id),
    CONSTRAINT fk_market_tracked_sales_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_image_asset (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    image_kind VARCHAR(24) NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    private_object_key VARCHAR(500) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    width_px INT UNSIGNED NOT NULL,
    height_px INT UNSIGNED NOT NULL,
    source_version_sha256 CHAR(64) NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_image_asset (variant_id, image_kind, content_sha256),
    KEY ix_market_image_asset_variant (variant_id, image_kind, captured_at),
    CONSTRAINT fk_market_image_asset_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_image_qc (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    image_asset_id BIGINT UNSIGNED NOT NULL,
    semantic_match_status VARCHAR(24) NOT NULL,
    card_number_match TINYINT(1) NOT NULL,
    language_match TINYINT(1) NOT NULL,
    tcg_match TINYINT(1) NOT NULL,
    raw_front_confirmed TINYINT(1) NOT NULL,
    public_allowed TINYINT(1) NOT NULL,
    rejection_reason VARCHAR(500) NULL,
    checked_at DATETIME(6) NOT NULL,
    qc_version VARCHAR(24) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_image_qc_version (image_asset_id, qc_version),
    KEY ix_market_image_qc_public (public_allowed, checked_at),
    CONSTRAINT fk_market_image_qc_asset FOREIGN KEY (image_asset_id) REFERENCES market_image_asset(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_identity_review_queue (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    run_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    reason_code VARCHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    resolved_variant_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at DATETIME(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_identity_review (source_code, external_entity_id, reason_code, evidence_sha256),
    KEY ix_market_identity_review_status (status, created_at),
    KEY ix_market_identity_review_run (run_id),
    CONSTRAINT fk_market_identity_review_run FOREIGN KEY (run_id) REFERENCES market_ingest_run(id),
    CONSTRAINT fk_market_identity_review_variant FOREIGN KEY (resolved_variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
