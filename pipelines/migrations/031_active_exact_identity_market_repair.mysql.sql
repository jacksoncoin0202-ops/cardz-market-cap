-- Active-762 exact provider identity and market-cap repair.
-- Applied migrations 023-030 remain immutable; this migration supersedes
-- their blank-as-wildcard source predicates.

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_tcg_code') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_tcg_code VARCHAR(32) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_card_language') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_card_language VARCHAR(8) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_collector_number') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_collector_number VARCHAR(96) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_edition_code') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_edition_code VARCHAR(191) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_parallel_code') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_parallel_code VARCHAR(64) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND column_name='bound_finish_code') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bound_finish_code VARCHAR(64) NOT NULL DEFAULT ''''',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity'
     AND index_name='ix_catalog_source_full_printing') = 0,
  'ALTER TABLE catalog_source_identity ADD KEY ix_catalog_source_full_printing (variant_id,source_code,match_status,bound_tcg_code,bound_card_language,bound_set_code,bound_collector_number,bound_printing_code)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl; EXECUTE cardz_stmt; DEALLOCATE PREPARE cardz_stmt;

CREATE OR REPLACE VIEW operator_strict_source_identity AS
SELECT si.*
FROM catalog_source_identity si
INNER JOIN catalog_printing_identity p ON p.variant_id=si.variant_id
WHERE LOWER(si.match_status)='exact'
  AND TRIM(si.source_product_number)<>''
  AND LOWER(TRIM(si.bound_tcg_code))=LOWER(TRIM(p.tcg_code))
  AND LOWER(TRIM(si.bound_card_language))=LOWER(TRIM(p.card_language))
  AND LOWER(TRIM(si.bound_collector_number))=LOWER(TRIM(p.collector_number))
  AND LOWER(TRIM(si.bound_set_code))=LOWER(TRIM(p.set_code))
  AND LOWER(TRIM(si.bound_printing_code))=LOWER(TRIM(p.printing_code))
  AND (LOWER(TRIM(p.edition_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_edition_code))=LOWER(TRIM(p.edition_code)))
  AND (LOWER(TRIM(p.parallel_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_parallel_code))=LOWER(TRIM(p.parallel_code)))
  AND (LOWER(TRIM(p.finish_code)) IN ('','unknown')
       OR LOWER(TRIM(si.bound_finish_code))=LOWER(TRIM(p.finish_code)))
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$';

CREATE OR REPLACE VIEW operator_official_name_projection AS
SELECT
  a.variant_id,
  a.id AS official_name_acceptance_id,
  a.official_full_name,
  a.source_code AS official_name_source_code,
  a.external_entity_id AS official_name_external_entity_id,
  a.evidence_sha256 AS official_name_evidence_sha256,
  a.source_payload_sha256 AS official_name_payload_sha256,
  a.source_observed_at AS official_name_observed_at,
  a.lineage_sha256 AS official_name_lineage_sha256
FROM catalog_official_name_acceptance a
INNER JOIN catalog_printing_identity p
  ON p.variant_id=a.variant_id
 AND p.canonical_printing_sha256=a.canonical_printing_sha256
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=a.variant_id
 AND si.source_code=LOWER(a.source_code)
 AND si.external_entity_id=a.external_entity_id
WHERE LOWER(a.source_code) IN ('gemrate','psa')
  AND a.external_entity_id<>''
  AND TRIM(a.official_full_name)<>''
  AND a.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.source_payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM catalog_official_name_acceptance newer
    WHERE newer.supersedes_acceptance_id=a.id
  );

CREATE OR REPLACE VIEW operator_eligible_accepted_psa10_price_history AS
SELECT
  h.id AS price_history_acceptance_id,
  p.variant_id,p.observed_date,p.price_usd,
  CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
       ELSE p.source_code END AS price_source_code,
  p.source_code AS price_storage_source_code,
  p.source_external_entity_id AS price_source_external_entity_id,
  p.source_observation_id AS price_source_observation_id,
  p.payload_sha256 AS price_payload_sha256,
  p.effective_at AS price_effective_at,
  so.observed_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  CASE WHEN pi.card_language='en' THEN
    CASE WHEN p.source_code='pricecharting' THEN 10
         WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 20 ELSE 90 END
  ELSE
    CASE WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 10
         WHEN p.source_code='pricecharting' THEN 20 ELSE 90 END
  END AS price_route_priority
FROM market_metric_history_acceptance h
INNER JOIN market_price_observation p
  ON h.source_record_type='market_price_observation'
 AND h.source_record_id=p.id AND h.variant_id=p.variant_id
 AND h.observed_date=p.observed_date AND h.source_effective_at=p.effective_at
 AND h.external_entity_id=p.source_external_entity_id
 AND h.source_payload_sha256=p.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=p.variant_id
 AND si.source_code=CASE WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE p.source_code END
 AND si.external_entity_id=p.source_external_entity_id
INNER JOIN market_source_observation so
  ON so.id=p.source_observation_id AND so.source_code=p.source_code
 AND so.external_entity_id=p.source_external_entity_id
 AND so.payload_sha256=p.payload_sha256 AND so.observed_date=p.observed_date
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND p.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
  AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
  AND (
    (p.source_code IN ('snkrdunk','snk_psa10','snk')
     AND so.observation_kind='psa10_reference_price')
    OR
    (p.source_code='pricecharting' AND p.source_priority=95
     AND so.observation_kind='psa10_price_guide'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
     AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%'
     AND p.source_external_entity_id REGEXP '^[0-9]+$')
  )
  AND p.price_usd>0 AND p.metric_status='ready' AND so.observed_at IS NOT NULL
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$';

CREATE OR REPLACE VIEW operator_eligible_pricecharting_variant AS
SELECT DISTINCT variant_id,1 AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history
WHERE price_source_code='pricecharting';

CREATE OR REPLACE VIEW operator_accepted_psa10_price_history AS
SELECT candidate.*,candidate.price_route_priority AS selected_price_route_priority,
       CASE WHEN pc.variant_id IS NULL THEN 0 ELSE 1 END AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history candidate
LEFT JOIN operator_eligible_pricecharting_variant pc ON pc.variant_id=candidate.variant_id
WHERE NOT EXISTS (
  SELECT 1 FROM operator_eligible_accepted_psa10_price_history preferred
  WHERE preferred.variant_id=candidate.variant_id
    AND preferred.price_route_priority<candidate.price_route_priority
)
AND ((pc.variant_id IS NOT NULL AND candidate.price_source_code='pricecharting')
  OR (pc.variant_id IS NULL AND candidate.price_source_code='snkrdunk'))
AND NOT EXISTS (
  SELECT 1 FROM operator_eligible_accepted_psa10_price_history newer
  WHERE newer.variant_id=candidate.variant_id
    AND newer.observed_date=candidate.observed_date
    AND newer.price_route_priority=candidate.price_route_priority
    AND (newer.price_effective_at>candidate.price_effective_at
      OR (newer.price_effective_at=candidate.price_effective_at
       AND (newer.price_source_observed_at>candidate.price_source_observed_at
        OR (newer.price_source_observed_at=candidate.price_source_observed_at
         AND newer.price_history_acceptance_id>candidate.price_history_acceptance_id))))
);

CREATE OR REPLACE VIEW operator_accepted_psa10_population_history AS
SELECT
  h.id AS population_history_acceptance_id,
  pop.variant_id,pop.observed_date,pop.top_grade_population AS psa10_population,
  pop.source_code AS population_source_code,
  pop.external_entity_id AS population_source_external_entity_id,
  pop.payload_sha256 AS population_payload_sha256,
  pop.effective_at AS population_effective_at,
  h.lineage_sha256 AS population_lineage_sha256
FROM market_metric_history_acceptance h
INNER JOIN market_grader_population_observation pop
  ON h.source_record_type='market_grader_population_observation'
 AND h.source_record_id=pop.id AND h.variant_id=pop.variant_id
 AND h.observed_date=pop.observed_date AND h.source_effective_at=pop.effective_at
 AND h.source_code=pop.source_code AND h.external_entity_id=pop.external_entity_id
 AND h.source_payload_sha256=pop.payload_sha256
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=pop.variant_id AND si.source_code='gemrate'
 AND si.external_entity_id=pop.external_entity_id
WHERE h.metric_kind='psa10_population' AND h.source_code='gemrate'
  AND pop.source_code='gemrate' AND UPPER(pop.grader_code)='PSA'
  AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
  AND pop.estimated=0
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM market_metric_history_acceptance h2
    INNER JOIN market_grader_population_observation pop2
      ON h2.source_record_type='market_grader_population_observation'
     AND h2.source_record_id=pop2.id AND h2.variant_id=pop2.variant_id
     AND h2.observed_date=pop2.observed_date
    WHERE h2.metric_kind='psa10_population' AND h2.source_code='gemrate'
      AND h2.variant_id=h.variant_id AND h2.observed_date=h.observed_date
      AND (pop2.effective_at>pop.effective_at
       OR (pop2.effective_at=pop.effective_at AND h2.id>h.id))
  );

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
 AND si.external_entity_id=l.exact_item_id AND si.evidence_sha256=l.identity_evidence_sha256
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
  AND NOT EXISTS (SELECT 1 FROM market_canonical_image_acceptance newer WHERE newer.supersedes_acceptance_id=ca.id);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('031');
