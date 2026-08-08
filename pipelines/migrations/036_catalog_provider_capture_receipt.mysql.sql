-- CARDZ 036: provider capture receipts. One row per (source, entity, capture
-- hash); the 037 strict-view revision proves every non-gemrate binding against
-- this table, so replay and binding share one artifact.
-- PK is composite (not capture_sha256 alone): one capture file may legitimately
-- evidence more than one external entity (e.g. an SNK API payload), and the
-- 037 predicate matches on all three columns.
CREATE TABLE IF NOT EXISTS catalog_provider_capture_receipt (
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(128) NOT NULL,
    capture_sha256 CHAR(64) NOT NULL,
    capture_path VARCHAR(500) NOT NULL,
    captured_at DATETIME(6) NOT NULL,
    generation_id VARCHAR(64) NOT NULL,
    parser_version VARCHAR(64) NOT NULL,
    PRIMARY KEY (source_code, external_entity_id, capture_sha256),
    KEY ix_capture_receipt_sha (capture_sha256),
    CONSTRAINT ck_capture_receipt_sha CHECK (capture_sha256 REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB;
