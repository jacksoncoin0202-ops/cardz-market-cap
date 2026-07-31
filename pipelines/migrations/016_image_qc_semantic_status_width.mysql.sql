-- Allow human_or_vision_confirmed (25 chars) and future semantic status labels.
-- Pre-016 VARCHAR(24) truncates/rejects the FE/QC gate status string.

ALTER TABLE market_image_qc
    MODIFY COLUMN semantic_match_status VARCHAR(48) NOT NULL;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('016');
