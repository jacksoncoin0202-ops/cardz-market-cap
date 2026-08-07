-- One canonical image row per variant. Exact SNK EN authority must bind to the
-- single canonical snkrdunk freeze, not the duplicate snkrdunk_en checkpoint.

CREATE OR REPLACE VIEW operator_canonical_image_projection AS
SELECT
  ca.variant_id,
  ca.id AS canonical_image_acceptance_id,
  ca.lineage_sha256 AS canonical_image_lineage_sha256,
  l.exact_item_id AS canonical_image_snk_item_id,
  l.product_url AS canonical_image_product_url,
  l.master_payload_sha256 AS canonical_image_master_payload_sha256,
  CASE WHEN l.id IS NULL THEN ca.fallback_source_path
       ELSE CONCAT('snkrdunk-en:',l.exact_item_id,':',l.default_image_url)
  END AS canonical_image_source_path,
  l.default_image_url_sha256 AS canonical_image_default_url_sha256,
  l.downloaded_bytes_sha256 AS canonical_image_downloaded_bytes_sha256,
  l.transform_sha256 AS canonical_image_transform_sha256,
  a.id AS canonical_image_asset_id,
  a.content_sha256 AS canonical_image_content_sha256,
  a.private_object_key AS canonical_image_object_key,
  a.mime_type AS canonical_image_mime_type,
  a.width_px AS canonical_image_width,
  a.height_px AS canonical_image_height,
  a.captured_at AS canonical_image_captured_at,
  q.checked_at AS canonical_image_qc_at,
  COALESCE(page.product_page_observed_at,ca.fallback_source_observed_at)
    AS canonical_image_source_observed_at,
  ca.evidence_sha256 AS canonical_image_evidence_sha256,
  ca.accepted_at AS canonical_image_accepted_at
FROM market_canonical_image_acceptance ca
LEFT JOIN market_snk_en_storefront_lineage l
  ON l.id=ca.storefront_lineage_id
 AND l.variant_id=ca.variant_id
 AND l.lineage_sha256=ca.lineage_sha256
LEFT JOIN market_snk_en_product_page_authority page
  ON page.storefront_lineage_id=l.id
 AND page.variant_id=l.variant_id
 AND page.exact_item_id=l.exact_item_id
 AND page.product_url=l.product_url
 AND page.default_image_url=l.default_image_url
 AND page.master_payload_sha256=l.master_payload_sha256
 AND page.identity_evidence_sha256=l.identity_evidence_sha256
INNER JOIN market_image_asset a
  ON a.id=ca.image_asset_id
 AND (l.id IS NULL OR a.id=l.processed_image_asset_id)
 AND a.variant_id=ca.variant_id
 AND a.image_kind='raw_front'
 AND (l.id IS NULL OR a.content_sha256=l.processed_content_sha256)
INNER JOIN market_image_qc q
  ON q.image_asset_id=a.id
 AND q.id=(
   SELECT q2.id FROM market_image_qc q2
   WHERE q2.image_asset_id=a.id
   ORDER BY q2.checked_at DESC,q2.id DESC LIMIT 1
 )
 AND q.public_allowed=1
 AND q.raw_front_confirmed=1
 AND q.card_number_match=1
 AND q.language_match=1
 AND q.tcg_match=1
 AND q.semantic_match_status IN ('human_or_vision_confirmed','accepted_freeze')
LEFT JOIN catalog_source_identity si
  ON si.variant_id=ca.variant_id
 AND si.source_code='snkrdunk'
 AND si.external_entity_id=l.exact_item_id
 AND LOWER(si.match_status)='exact'
 AND si.evidence_sha256=l.identity_evidence_sha256
INNER JOIN catalog_printing_identity p
  ON p.variant_id=ca.variant_id
 AND (l.id IS NULL OR si.bound_set_code='' OR si.bound_set_code=p.set_code)
 AND (l.id IS NULL OR si.bound_printing_code='' OR si.bound_printing_code=p.printing_code)
INNER JOIN operator_binding_freeze f
  ON f.variant_id=ca.variant_id
 AND f.freeze_kind='image'
 AND f.acceptance_status='accepted'
 AND f.canonical_image_acceptance_id=ca.id
 AND f.accepted_lineage_sha256=ca.lineage_sha256
 AND f.content_sha256=a.content_sha256
 AND (
   (
     l.id IS NOT NULL
     AND f.source_code='snkrdunk'
     AND f.external_entity_id=l.exact_item_id
   )
   OR l.id IS NULL
 )
WHERE (
    (
      l.id IS NOT NULL
      AND page.id IS NOT NULL
      AND l.source_code='snkrdunk'
      AND l.storefront_code='en'
      AND l.exact_item_id REGEXP '^[0-9]+$'
      AND l.product_url=CONCAT('https://snkrdunk.com/en/trading-cards/',l.exact_item_id)
      AND page.product_url=l.product_url
      AND page.final_url=page.product_url
      AND page.http_status=200
      AND page.product_page_payload_sha256 REGEXP '^[0-9a-f]{64}$'
      AND page.product_page_observed_at IS NOT NULL
      AND page.default_image_url=l.default_image_url
      AND page.master_payload_sha256=l.master_payload_sha256
      AND page.identity_evidence_sha256=l.identity_evidence_sha256
      AND page.authority_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.master_payload_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.default_image_url_sha256=SHA2(l.default_image_url,256)
      AND l.downloaded_bytes_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.processed_content_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.transform_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
      AND l.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
    )
    OR
    (
      l.id IS NULL
      AND ca.storefront_lineage_id IS NULL
      AND ca.fallback_source_path<>''
      AND ca.fallback_source_version_sha256 REGEXP '^[0-9a-f]{64}$'
      AND ca.fallback_source_observed_at IS NOT NULL
      AND ca.lineage_sha256=ca.fallback_source_version_sha256
      AND LOWER(ca.fallback_source_path) NOT LIKE 'snk:%'
      AND LOWER(ca.fallback_source_path) NOT LIKE 'snkrdunk:%'
      AND LOWER(ca.fallback_source_path) NOT LIKE 'snkrdunk_en:%'
      AND LOWER(ca.fallback_source_path) NOT LIKE 'snkrdunk-en:%'
      AND LOWER(ca.fallback_source_path) NOT LIKE '%snkrdunk.com%'
      AND LOWER(ca.fallback_source_path) NOT LIKE '%upload_bg_removed%'
      AND f.source_code NOT IN ('snk','snkrdunk','snkrdunk_en')
    )
  )
  AND ca.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM market_canonical_image_acceptance newer
    WHERE newer.supersedes_acceptance_id=ca.id
  );

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('030');
