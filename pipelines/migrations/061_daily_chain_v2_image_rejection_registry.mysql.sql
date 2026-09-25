-- CARDZ 061: a human image rejection is obeyed where the FE reads the image.
--
-- LANDED 2026-09-26 (the ".pending" suffix dropped after the go/no-go query
-- below read 0): the V2 task runs scripts/cardz_daily_v2_launcher.ps1 out of
-- THIS working tree, and pipelines/daily_chain_v2_contract.py:V2_MIGRATION_GLOB
-- turns every visible "0[5-9][0-9]_daily_chain_v2_*.mysql.sql" into a
-- schema-NNN migrate stage on the next tick -- committed or not.
-- scripts/test_image_rejection_registry.py accepts the parked or landed state.
--
--   SELECT COUNT(*) FROM operator_canonical_image_projection p
--   WHERE EXISTS (SELECT 1 FROM market_image_rejection_registry rej
--                 WHERE rej.variant_id=p.variant_id
--                   AND rej.content_sha256=p.canonical_image_content_sha256);
--
-- A non-zero count is the number of cards that would fall to
-- /card-placeholder.svg, which fails the public asset sync and so the bake --
-- a rejection goes in with its replacement (pipelines/pin_human_card_image.py).
--
-- Why: market_image_rejection_registry is the human image verdict (42 rows on
-- 2026-09-25: historical_human_review_ledger, human_review_rejected:*,
-- human_designated_override:*) and has lived only in the live DB -- no file
-- in this repo created it -- and only the auto WRITERS consulted it
-- (rebuild_036 image-bind, the SNK EN freeze).  The FE READ never did, so any
-- lane that re-accepted a rejected (variant, content) put it straight back on
-- the site.  Human reject is hard authority (DADDY 2026-09-26: the right card
-- first; a SAMPLE-watermarked image of the right card may stay up).
--
-- The table is SHOW CREATE TABLE of the live 3308 table (2026-09-25), so on
-- that database CREATE TABLE IF NOT EXISTS is a no-op and a fresh database
-- gets the same keys and foreign keys.
--
-- The view is byte-identical to 033 except the closing NOT EXISTS: same
-- columns, order, aliases and predicates, so every reader keeps compiling and
-- only a registry-rejected (variant, content) stops existing for it.  The
-- freeze fallback in apps/web/src/lib/live-db-snapshot.ts (and its Python
-- mirror rebuild_036._fe_image_state) carries the same predicate text;
-- scripts/test_image_rejection_registry.py pins all three to one string.
--
-- Rows are written only by pipelines/pin_human_card_image.py, which puts the
-- replacement in the same transaction: a rejection alone would leave the card
-- imageless.  Nothing here deletes a row.

CREATE TABLE IF NOT EXISTS market_image_rejection_registry (
    variant_id BIGINT UNSIGNED NOT NULL,
    content_sha256 CHAR(64) COLLATE utf8mb4_unicode_ci NOT NULL,
    first_image_asset_id BIGINT UNSIGNED DEFAULT NULL,
    rejection_reason VARCHAR(500) COLLATE utf8mb4_unicode_ci NOT NULL,
    decision_code_sha256 CHAR(64) COLLATE utf8mb4_unicode_ci NOT NULL,
    review_run_id VARCHAR(191) COLLATE utf8mb4_unicode_ci NOT NULL,
    rejected_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id, content_sha256),
    KEY ix_image_rejection_registry_asset (first_image_asset_id),
    CONSTRAINT fk_image_rejection_registry_asset
      FOREIGN KEY (first_image_asset_id) REFERENCES market_image_asset (id),
    CONSTRAINT fk_image_rejection_registry_variant
      FOREIGN KEY (variant_id) REFERENCES catalog_variant (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW operator_canonical_image_projection AS
SELECT
  ca.variant_id,ca.id AS canonical_image_acceptance_id,
  ca.lineage_sha256 AS canonical_image_lineage_sha256,
  l.exact_item_id AS canonical_image_snk_item_id,
  l.product_url AS canonical_image_product_url,
  l.master_payload_sha256 AS canonical_image_master_payload_sha256,
  CASE WHEN l.id IS NULL THEN ca.fallback_source_path
       ELSE CONCAT('snkrdunk-en:',l.exact_item_id,':',l.default_image_url) END AS canonical_image_source_path,
  l.default_image_url_sha256 AS canonical_image_default_url_sha256,
  l.downloaded_bytes_sha256 AS canonical_image_downloaded_bytes_sha256,
  l.transform_sha256 AS canonical_image_transform_sha256,
  a.id AS canonical_image_asset_id,a.content_sha256 AS canonical_image_content_sha256,
  a.private_object_key AS canonical_image_object_key,a.mime_type AS canonical_image_mime_type,
  a.width_px AS canonical_image_width,a.height_px AS canonical_image_height,
  a.captured_at AS canonical_image_captured_at,q.checked_at AS canonical_image_qc_at,
  COALESCE(page.product_page_observed_at,ca.fallback_source_observed_at) AS canonical_image_source_observed_at,
  ca.evidence_sha256 AS canonical_image_evidence_sha256,ca.accepted_at AS canonical_image_accepted_at
FROM market_canonical_image_acceptance ca
LEFT JOIN market_snk_en_storefront_lineage l
  ON l.id=ca.storefront_lineage_id AND l.variant_id=ca.variant_id AND l.lineage_sha256=ca.lineage_sha256
LEFT JOIN market_snk_en_product_page_authority page
  ON page.storefront_lineage_id=l.id AND page.variant_id=l.variant_id
 AND page.exact_item_id=l.exact_item_id AND page.product_url=l.product_url
 AND page.default_image_url=l.default_image_url AND page.master_payload_sha256=l.master_payload_sha256
 AND page.identity_evidence_sha256=l.identity_evidence_sha256
INNER JOIN market_image_asset a
  ON a.id=ca.image_asset_id AND (l.id IS NULL OR a.id=l.processed_image_asset_id)
 AND a.variant_id=ca.variant_id AND a.image_kind='raw_front'
 AND (l.id IS NULL OR a.content_sha256=l.processed_content_sha256)
INNER JOIN market_image_qc q
  ON q.image_asset_id=a.id
 AND q.id=(SELECT q2.id FROM market_image_qc q2 WHERE q2.image_asset_id=a.id ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1)
 AND q.public_allowed=1 AND q.raw_front_confirmed=1 AND q.card_number_match=1
 AND q.language_match=1 AND q.tcg_match=1
 AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
LEFT JOIN operator_strict_source_identity si
  ON si.variant_id=ca.variant_id AND si.source_code='snkrdunk'
 AND si.external_entity_id=l.exact_item_id
INNER JOIN operator_binding_freeze f
  ON f.variant_id=ca.variant_id AND f.freeze_kind='image' AND f.acceptance_status='accepted'
 AND f.canonical_image_acceptance_id=ca.id AND f.accepted_lineage_sha256=ca.lineage_sha256
 AND f.content_sha256=a.content_sha256
 AND ((l.id IS NOT NULL AND f.source_code='snkrdunk' AND f.external_entity_id=l.exact_item_id) OR l.id IS NULL)
WHERE (l.id IS NULL OR si.variant_id IS NOT NULL)
  AND ((l.id IS NOT NULL AND page.id IS NOT NULL AND l.source_code='snkrdunk'
     AND l.storefront_code='en' AND l.exact_item_id REGEXP '^[0-9]+$'
     AND l.product_url=CONCAT('https://snkrdunk.com/en/trading-cards/',l.exact_item_id)
     AND page.final_url=page.product_url AND page.http_status=200)
    OR (l.id IS NULL AND ca.storefront_lineage_id IS NULL AND ca.fallback_source_path<>''
     AND ca.fallback_source_version_sha256 REGEXP '^[0-9a-f]{64}$'
     AND ca.fallback_source_observed_at IS NOT NULL
     AND ca.lineage_sha256=ca.fallback_source_version_sha256
     AND LOWER(ca.fallback_source_path) NOT LIKE '%snkrdunk%'
     AND LOWER(ca.fallback_source_path) NOT LIKE '%upload_bg_removed%'
     AND f.source_code NOT IN ('snk','snkrdunk','snkrdunk_en')))
  AND ca.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer WHERE newer.supersedes_acceptance_id=ca.id)
  AND NOT EXISTS (SELECT 1 FROM market_image_rejection_registry rej
                  WHERE rej.variant_id=a.variant_id AND rej.content_sha256=a.content_sha256);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('061');
