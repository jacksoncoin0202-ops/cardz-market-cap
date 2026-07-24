-- Content-addressed private payload retention. Apply after 007.
-- Canonical price, population, ranking, sales and FX tables are never compacted here.

CREATE TABLE IF NOT EXISTS market_raw_payload_object (
    content_sha256 CHAR(64) NOT NULL,
    private_object_key VARCHAR(500) NOT NULL,
    byte_size BIGINT UNSIGNED NOT NULL,
    archive_manifest_sha256 CHAR(64) NOT NULL,
    archived_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (content_sha256),
    KEY ix_market_raw_payload_manifest (archive_manifest_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_retention_archive_manifest (
    manifest_sha256 CHAR(64) NOT NULL,
    private_manifest_key VARCHAR(500) NOT NULL,
    object_count INT UNSIGNED NOT NULL,
    observation_count INT UNSIGNED NOT NULL,
    archived_bytes BIGINT UNSIGNED NOT NULL,
    completed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (manifest_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_source_observation_payload_pointer (
    observation_id BIGINT UNSIGNED NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    archive_manifest_sha256 CHAR(64) NOT NULL,
    archived_at DATETIME(6) NOT NULL,
    PRIMARY KEY (observation_id),
    KEY ix_market_source_payload_pointer_content (content_sha256),
    KEY ix_market_source_payload_pointer_manifest (archive_manifest_sha256),
    CONSTRAINT fk_market_source_payload_pointer_observation
        FOREIGN KEY (observation_id) REFERENCES market_source_observation(id),
    CONSTRAINT fk_market_source_payload_pointer_content
        FOREIGN KEY (content_sha256) REFERENCES market_raw_payload_object(content_sha256),
    CONSTRAINT fk_market_source_payload_pointer_manifest
        FOREIGN KEY (archive_manifest_sha256) REFERENCES market_retention_archive_manifest(manifest_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_source_effective_observation (
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    observation_kind VARCHAR(40) NOT NULL,
    observed_date DATE NOT NULL,
    observation_id BIGINT UNSIGNED NOT NULL,
    effective_at DATETIME(6) NOT NULL,
    selected_at DATETIME(6) NOT NULL,
    PRIMARY KEY (source_code, external_entity_id, observation_kind, observed_date),
    UNIQUE KEY uq_market_source_effective_observation_id (observation_id),
    KEY ix_market_source_effective_observation_date (observed_date, effective_at),
    CONSTRAINT fk_market_source_effective_observation_source
        FOREIGN KEY (observation_id) REFERENCES market_source_observation(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('008');
