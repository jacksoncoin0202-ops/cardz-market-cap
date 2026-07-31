-- Immutable image-review approvals and permanent variant/content rejections.
--
-- QC flags alone are mutable operational state.  These tables preserve the
-- exact printing/language/source facts that DADDY reviewed, and make a
-- rejected variant+content hash ineligible forever.

CREATE TABLE IF NOT EXISTS market_image_review_approval (
    image_asset_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    source_version_sha256 CHAR(64) NOT NULL,
    canonical_printing_sha256 CHAR(64) NOT NULL,
    expected_language VARCHAR(8) NOT NULL,
    binding_sha256 CHAR(64) NOT NULL,
    decision_code_sha256 CHAR(64) NOT NULL,
    review_run_id VARCHAR(191) NOT NULL,
    reviewed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (image_asset_id),
    UNIQUE KEY uq_image_review_approval_variant_content
        (variant_id, content_sha256),
    KEY ix_image_review_approval_printing
        (variant_id, canonical_printing_sha256, expected_language),
    CONSTRAINT fk_image_review_approval_asset
        FOREIGN KEY (image_asset_id) REFERENCES market_image_asset(id),
    CONSTRAINT fk_image_review_approval_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_image_rejection_registry (
    variant_id BIGINT UNSIGNED NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    first_image_asset_id BIGINT UNSIGNED NULL,
    rejection_reason VARCHAR(500) NOT NULL,
    decision_code_sha256 CHAR(64) NOT NULL,
    review_run_id VARCHAR(191) NOT NULL,
    rejected_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id, content_sha256),
    KEY ix_image_rejection_registry_asset (first_image_asset_id),
    CONSTRAINT fk_image_rejection_registry_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_image_rejection_registry_asset
        FOREIGN KEY (first_image_asset_id) REFERENCES market_image_asset(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Bind already-current QCv2 approvals to the exact canonical printing that
-- existed when migration 020 was installed.  Later publication still
-- recomputes and compares every field; this is not a blanket public grant.
INSERT IGNORE INTO market_image_review_approval
    (image_asset_id,variant_id,content_sha256,source_version_sha256,
     canonical_printing_sha256,expected_language,binding_sha256,
     decision_code_sha256,review_run_id,reviewed_at)
SELECT
    a.id,
    a.variant_id,
    a.content_sha256,
    a.source_version_sha256,
    p.canonical_printing_sha256,
    p.card_language,
    SHA2(CONCAT_WS('|',a.id,a.variant_id,a.content_sha256,
        a.source_version_sha256,p.canonical_printing_sha256,p.card_language),256),
    SHA2(CONCAT('migration-020-qcv2-approval:',q.id,':',a.content_sha256),256),
    'migration-020-existing-qcv2',
    q.checked_at
FROM market_image_qc AS q
JOIN market_image_asset AS a ON a.id=q.image_asset_id
JOIN catalog_printing_identity AS p
  ON p.variant_id=a.variant_id AND p.identity_status='canonical'
WHERE q.qc_version='human-review-v2'
  AND q.semantic_match_status='human_or_vision_confirmed'
  AND q.card_number_match=1
  AND q.language_match=1
  AND q.tcg_match=1
  AND q.raw_front_confirmed=1
  AND q.public_allowed=1;

INSERT IGNORE INTO market_image_rejection_registry
    (variant_id,content_sha256,first_image_asset_id,rejection_reason,
     decision_code_sha256,review_run_id,rejected_at)
SELECT
    a.variant_id,
    a.content_sha256,
    a.id,
    COALESCE(NULLIF(q.rejection_reason,''),'human_review_rejected'),
    SHA2(CONCAT('migration-020-qcv2-rejection:',q.id,':',a.content_sha256),256),
    'migration-020-existing-qcv2',
    q.checked_at
FROM market_image_qc AS q
JOIN market_image_asset AS a ON a.id=q.image_asset_id
WHERE q.qc_version='human-review-v2'
  AND q.semantic_match_status='human_rejected';

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('020');
