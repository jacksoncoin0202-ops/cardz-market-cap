-- Daily market coverage, candidate snapshots and deduplicated alert episodes.
-- Compatible with MySQL 5.7/8.x. Apply after 004.

CREATE TABLE IF NOT EXISTS market_alert_evaluation (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    universe_lock_id BIGINT UNSIGNED NOT NULL,
    index_code VARCHAR(64) NOT NULL,
    index_version VARCHAR(24) NOT NULL,
    policy_version VARCHAR(24) NOT NULL,
    effective_date DATE NOT NULL,
    discovery_sha256 CHAR(64) NOT NULL,
    input_sha256 CHAR(64) NOT NULL,
    eligible_count INT UNSIGNED NOT NULL,
    top100_cutoff_usd DECIMAL(24,6) NULL,
    unresolved_high_potential_count INT UNSIGNED NOT NULL DEFAULT 0,
    coverage_status VARCHAR(24) NOT NULL,
    completed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_alert_evaluation
        (index_code, index_version, effective_date, policy_version),
    KEY ix_market_alert_evaluation_latest (index_code, effective_date, coverage_status),
    CONSTRAINT fk_market_alert_evaluation_lock
        FOREIGN KEY (universe_lock_id) REFERENCES market_universe_lock(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_candidate_daily_snapshot (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    evaluation_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    member_role VARCHAR(16) NOT NULL,
    shadow_rank INT UNSIGNED NULL,
    eligible_rank INT UNSIGNED NULL,
    reference_price_usd DECIMAL(18,6) NULL,
    psa10_population INT UNSIGNED NULL,
    market_cap_usd DECIMAL(24,6) NULL,
    cutoff_ratio DECIMAL(12,6) NULL,
    projected_pop1000_ratio DECIMAL(12,6) NULL,
    change_1d_pct DECIMAL(12,6) NULL,
    change_7d_pct DECIMAL(12,6) NULL,
    change_30d_pct DECIMAL(12,6) NULL,
    population_change_7d_pct DECIMAL(12,6) NULL,
    population_change_30d_pct DECIMAL(12,6) NULL,
    metric_status VARCHAR(24) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_candidate_daily (evaluation_id, variant_id),
    KEY ix_market_candidate_rank (evaluation_id, shadow_rank),
    KEY ix_market_candidate_variant (variant_id, evaluation_id),
    CONSTRAINT fk_market_candidate_evaluation
        FOREIGN KEY (evaluation_id) REFERENCES market_alert_evaluation(id),
    CONSTRAINT fk_market_candidate_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_alert (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NULL,
    candidate_key VARCHAR(191) NOT NULL,
    alert_type VARCHAR(48) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    status VARCHAR(24) NOT NULL,
    active_dedupe_key CHAR(64) NULL,
    first_seen_date DATE NOT NULL,
    last_seen_date DATE NOT NULL,
    consecutive_hits INT UNSIGNED NOT NULL DEFAULT 0,
    consecutive_misses INT UNSIGNED NOT NULL DEFAULT 0,
    latest_snapshot_id BIGINT UNSIGNED NULL,
    latest_evidence_json JSON NOT NULL,
    acknowledged_at DATETIME(6) NULL,
    resolved_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_alert_active (active_dedupe_key),
    KEY ix_market_alert_operator (status, severity, last_seen_date),
    KEY ix_market_alert_variant (variant_id, alert_type),
    CONSTRAINT fk_market_alert_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_market_alert_snapshot FOREIGN KEY (latest_snapshot_id) REFERENCES market_candidate_daily_snapshot(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_alert_event (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    alert_id BIGINT UNSIGNED NOT NULL,
    evaluation_id BIGINT UNSIGNED NOT NULL,
    event_key CHAR(64) NOT NULL,
    event_type VARCHAR(24) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    payload_json JSON NOT NULL,
    delivery_status VARCHAR(24) NOT NULL DEFAULT 'pending',
    delivery_attempts INT UNSIGNED NOT NULL DEFAULT 0,
    delivered_at DATETIME(6) NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_market_alert_event (event_key),
    KEY ix_market_alert_event_delivery (delivery_status, created_at),
    CONSTRAINT fk_market_alert_event_alert FOREIGN KEY (alert_id) REFERENCES market_alert(id),
    CONSTRAINT fk_market_alert_event_evaluation FOREIGN KEY (evaluation_id) REFERENCES market_alert_evaluation(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('005');
