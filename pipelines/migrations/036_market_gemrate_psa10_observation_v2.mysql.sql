-- CARDZ 036: PSA10-only GemRate population landing, v2. The three CHECKs make
-- the historical latest_psa10_pop corruption physically unrepresentable:
-- no snkrdunk 'top' rows, no colon-prefixed ids, no estimated rows.
-- variant_id is nullable on purpose: identity precedes binding, and collapsed
-- splits depend on that order. Unique key is (gemrate_id, observed_date), NOT
-- variant-keyed, so two ids on one variant can never overwrite each other.
CREATE TABLE IF NOT EXISTS market_gemrate_psa10_observation_v2 (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    run_id BIGINT UNSIGNED NOT NULL,
    gemrate_id CHAR(40) NOT NULL,
    variant_id BIGINT UNSIGNED NULL,
    psa10_population INT UNSIGNED NOT NULL,
    total_population INT UNSIGNED NULL,
    grader_code VARCHAR(8) NOT NULL DEFAULT 'PSA',
    top_grade_label VARCHAR(8) NOT NULL DEFAULT '10',
    estimated TINYINT(1) NOT NULL DEFAULT 0,
    effective_at DATETIME(6) NOT NULL,
    observed_date DATE NOT NULL,
    capture_path VARCHAR(500) NOT NULL,
    raw_payload_sha256 CHAR(64) NOT NULL,
    psa_row_sha256 CHAR(64) NOT NULL,
    generation_id VARCHAR(64) NOT NULL,
    UNIQUE KEY uq_gemrate_psa10_identity_day (gemrate_id, observed_date),
    KEY ix_gemrate_psa10_variant (variant_id, effective_at),
    KEY ix_gemrate_psa10_latest (gemrate_id, effective_at, id),
    CONSTRAINT fk_gemrate_psa10_run FOREIGN KEY (run_id) REFERENCES market_ingest_run (id),
    CONSTRAINT ck_gemrate_psa10_id CHECK (gemrate_id REGEXP '^[0-9a-f]{40}$'),
    CONSTRAINT ck_gemrate_psa10_grade CHECK (grader_code = 'PSA' AND top_grade_label = '10'),
    CONSTRAINT ck_gemrate_psa10_real CHECK (estimated = 0),
    CONSTRAINT ck_gemrate_psa10_raw_sha CHECK (raw_payload_sha256 REGEXP '^[0-9a-f]{64}$'),
    CONSTRAINT ck_gemrate_psa10_row_sha CHECK (psa_row_sha256 REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB;
