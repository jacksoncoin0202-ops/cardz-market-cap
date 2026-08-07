-- Immutable official-name, SNK EN storefront/image, and accepted market lineage.
-- Additive and replay-safe on MySQL 5.7/8.x. Apply after migration 025.
--
-- Acceptance tables are append-only inputs written by the operator loader.
-- This migration deliberately does not infer acceptance from legacy positive
-- rows. Views expose only hash-bound accepted rows with exact provider identity.

CREATE TABLE IF NOT EXISTS catalog_official_name_acceptance (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    official_full_name VARCHAR(500) NOT NULL,
    canonical_printing_sha256 CHAR(64) NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    lineage_sha256 CHAR(64) NOT NULL,
    source_observed_at DATETIME(6) NOT NULL,
    accepted_by VARCHAR(191) NOT NULL,
    accepted_at DATETIME(6) NOT NULL,
    supersedes_acceptance_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_official_name_lineage (lineage_sha256),
    UNIQUE KEY uq_official_name_supersedes (supersedes_acceptance_id),
    KEY ix_official_name_current (variant_id, accepted_at),
    KEY ix_official_name_source (source_code, external_entity_id),
    CONSTRAINT fk_official_name_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_official_name_supersedes FOREIGN KEY (supersedes_acceptance_id)
        REFERENCES catalog_official_name_acceptance(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_snk_en_storefront_lineage (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL DEFAULT 'snkrdunk',
    storefront_code VARCHAR(8) NOT NULL DEFAULT 'en',
    exact_item_id VARCHAR(191) NOT NULL,
    product_url VARCHAR(1000) NOT NULL,
    master_payload_sha256 CHAR(64) NOT NULL,
    default_image_url VARCHAR(1500) NOT NULL,
    default_image_url_sha256 CHAR(64) NOT NULL,
    downloaded_bytes_sha256 CHAR(64) NOT NULL,
    processed_image_asset_id BIGINT UNSIGNED NOT NULL,
    processed_content_sha256 CHAR(64) NOT NULL,
    transform_sha256 CHAR(64) NOT NULL,
    transform_json JSON NOT NULL,
    identity_evidence_sha256 CHAR(64) NOT NULL,
    lineage_sha256 CHAR(64) NOT NULL,
    source_observed_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_snk_en_storefront_lineage (lineage_sha256),
    KEY ix_snk_en_storefront_variant (variant_id, source_observed_at),
    KEY ix_snk_en_storefront_item (exact_item_id, source_observed_at),
    KEY ix_snk_en_storefront_asset (processed_image_asset_id),
    CONSTRAINT fk_snk_en_storefront_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_snk_en_storefront_asset FOREIGN KEY (processed_image_asset_id)
        REFERENCES market_image_asset(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_canonical_image_acceptance (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    storefront_lineage_id BIGINT UNSIGNED NULL,
    image_asset_id BIGINT UNSIGNED NOT NULL,
    fallback_source_path VARCHAR(1500) NULL,
    fallback_source_version_sha256 CHAR(64) NULL,
    fallback_source_observed_at DATETIME(6) NULL,
    lineage_sha256 CHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    accepted_by VARCHAR(191) NOT NULL,
    accepted_at DATETIME(6) NOT NULL,
    supersedes_acceptance_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_canonical_image_lineage (lineage_sha256),
    UNIQUE KEY uq_canonical_image_storefront (storefront_lineage_id),
    UNIQUE KEY uq_canonical_image_supersedes (supersedes_acceptance_id),
    KEY ix_canonical_image_current (variant_id, accepted_at),
    CONSTRAINT fk_canonical_image_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_canonical_image_storefront FOREIGN KEY (storefront_lineage_id)
        REFERENCES market_snk_en_storefront_lineage(id),
    CONSTRAINT fk_canonical_image_asset FOREIGN KEY (image_asset_id) REFERENCES market_image_asset(id),
    CONSTRAINT fk_canonical_image_supersedes FOREIGN KEY (supersedes_acceptance_id)
        REFERENCES market_canonical_image_acceptance(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_canonical_image_acceptance'
     AND column_name='storefront_lineage_id' AND is_nullable='NO') = 1,
  'ALTER TABLE market_canonical_image_acceptance MODIFY storefront_lineage_id BIGINT UNSIGNED NULL',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_canonical_image_acceptance'
     AND column_name='fallback_source_path') = 0,
  'ALTER TABLE market_canonical_image_acceptance ADD COLUMN fallback_source_path VARCHAR(1500) NULL AFTER image_asset_id',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_canonical_image_acceptance'
     AND column_name='fallback_source_version_sha256') = 0,
  'ALTER TABLE market_canonical_image_acceptance ADD COLUMN fallback_source_version_sha256 CHAR(64) NULL AFTER fallback_source_path',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_canonical_image_acceptance'
     AND column_name='fallback_source_observed_at') = 0,
  'ALTER TABLE market_canonical_image_acceptance ADD COLUMN fallback_source_observed_at DATETIME(6) NULL AFTER fallback_source_version_sha256',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

-- One immutable acceptance row identifies one already-existing source record.
-- metric_kind is one of psa10_price, psa10_population, psa10_sale, or
-- verified_zero_sales. source_record_type names the exact canonical table.
CREATE TABLE IF NOT EXISTS market_metric_history_acceptance (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    metric_kind VARCHAR(32) NOT NULL,
    source_record_type VARCHAR(48) NOT NULL,
    source_record_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    observed_date DATE NOT NULL,
    source_effective_at DATETIME(6) NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    identity_evidence_sha256 CHAR(64) NOT NULL,
    acceptance_evidence_sha256 CHAR(64) NOT NULL,
    lineage_sha256 CHAR(64) NOT NULL,
    accepted_by VARCHAR(191) NOT NULL,
    accepted_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_metric_history_source (source_record_type, source_record_id),
    UNIQUE KEY uq_metric_history_lineage (lineage_sha256),
    KEY ix_metric_history_daily (variant_id, metric_kind, observed_date, source_effective_at),
    KEY ix_metric_history_source_identity (source_code, external_entity_id),
    CONSTRAINT fk_metric_history_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- A current metric decision binds one accepted language-routed exact PSA 10
-- price and one accepted exact GemRate PSA 10 population. Rank belongs to the
-- same immutable generation as the selected metric lineage.
CREATE TABLE IF NOT EXISTS market_canonical_metric_acceptance (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    price_history_acceptance_id BIGINT UNSIGNED NOT NULL,
    population_history_acceptance_id BIGINT UNSIGNED NOT NULL,
    market_cap_usd DECIMAL(24,6) NOT NULL,
    canonical_market_rank INT UNSIGNED NULL,
    ranking_generation_sha256 CHAR(64) NOT NULL,
    metric_lineage_sha256 CHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    accepted_by VARCHAR(191) NOT NULL,
    accepted_at DATETIME(6) NOT NULL,
    supersedes_acceptance_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_canonical_metric_lineage (metric_lineage_sha256),
    UNIQUE KEY uq_canonical_metric_generation_variant (ranking_generation_sha256, variant_id),
    UNIQUE KEY uq_canonical_metric_generation_rank (ranking_generation_sha256, canonical_market_rank),
    UNIQUE KEY uq_canonical_metric_supersedes (supersedes_acceptance_id),
    KEY ix_canonical_metric_current (variant_id, accepted_at),
    CONSTRAINT fk_canonical_metric_variant FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_canonical_metric_price_acceptance FOREIGN KEY (price_history_acceptance_id)
        REFERENCES market_metric_history_acceptance(id),
    CONSTRAINT fk_canonical_metric_population_acceptance FOREIGN KEY (population_history_acceptance_id)
        REFERENCES market_metric_history_acceptance(id),
    CONSTRAINT fk_canonical_metric_supersedes FOREIGN KEY (supersedes_acceptance_id)
        REFERENCES market_canonical_metric_acceptance(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Existing image freezes can now bind the immutable acceptance row and the
-- exact storefront lineage hash instead of only a processed content hash.
SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='operator_binding_freeze'
     AND column_name='canonical_image_acceptance_id') = 0,
  'ALTER TABLE operator_binding_freeze ADD COLUMN canonical_image_acceptance_id BIGINT UNSIGNED NULL AFTER content_sha256',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='operator_binding_freeze'
     AND column_name='accepted_lineage_sha256') = 0,
  CONCAT('ALTER TABLE operator_binding_freeze ADD COLUMN accepted_lineage_sha256 CHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER canonical_image_acceptance_id'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='operator_binding_freeze'
     AND index_name='ix_operator_binding_freeze_image_lineage') = 0,
  'ALTER TABLE operator_binding_freeze ADD KEY ix_operator_binding_freeze_image_lineage (canonical_image_acceptance_id, accepted_lineage_sha256)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

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
  COALESCE(l.source_observed_at,ca.fallback_source_observed_at) AS canonical_image_source_observed_at,
  ca.evidence_sha256 AS canonical_image_evidence_sha256,
  ca.accepted_at AS canonical_image_accepted_at
FROM market_canonical_image_acceptance ca
LEFT JOIN market_snk_en_storefront_lineage l
  ON l.id=ca.storefront_lineage_id
 AND l.variant_id=ca.variant_id
 AND l.lineage_sha256=ca.lineage_sha256
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
WHERE (
    (
      l.id IS NOT NULL
      AND l.source_code='snkrdunk'
      AND l.storefront_code='en'
      AND l.exact_item_id REGEXP '^[0-9]+$'
      AND l.product_url=CONCAT('https://snkrdunk.com/en/trading-cards/',l.exact_item_id)
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
    )
  )
  AND ca.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM market_canonical_image_acceptance newer
    WHERE newer.supersedes_acceptance_id=ca.id
  );

CREATE OR REPLACE VIEW operator_eligible_accepted_psa10_price_history AS
SELECT
  h.id AS price_history_acceptance_id,
  p.variant_id,p.observed_date,p.price_usd,
  CASE
    WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
    ELSE p.source_code
  END AS price_source_code,
  p.source_code AS price_storage_source_code,
  p.source_external_entity_id AS price_source_external_entity_id,
  p.source_observation_id AS price_source_observation_id,
  p.payload_sha256 AS price_payload_sha256,
  p.effective_at AS price_effective_at,
  so.observed_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  CASE
    WHEN pi.card_language='en' THEN
      CASE
        WHEN p.source_code='pricecharting' THEN 10
        WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 20
        ELSE 90
      END
    ELSE
      CASE
        WHEN p.source_code IN ('snkrdunk','snk_psa10','snk') THEN 10
        WHEN p.source_code='pricecharting' THEN 20
        ELSE 90
      END
  END AS price_route_priority
FROM market_metric_history_acceptance h
INNER JOIN market_price_observation p
  ON h.source_record_type='market_price_observation'
 AND h.source_record_id=p.id
 AND h.variant_id=p.variant_id
 AND h.observed_date=p.observed_date
 AND h.source_effective_at=p.effective_at
 AND h.external_entity_id=p.source_external_entity_id
 AND h.source_payload_sha256=p.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=p.variant_id
INNER JOIN catalog_source_identity si
  ON si.variant_id=p.variant_id
 AND si.source_code=CASE
      WHEN p.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
      ELSE p.source_code
    END
 AND si.external_entity_id=p.source_external_entity_id
 AND LOWER(si.match_status)='exact'
INNER JOIN market_source_observation so
 ON so.id=p.source_observation_id
 AND so.source_code=p.source_code
 AND so.external_entity_id=p.source_external_entity_id
 AND so.payload_sha256=p.payload_sha256
 AND so.observed_date=p.observed_date
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND p.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
  AND (pi.card_language='en' OR p.source_code IN ('snkrdunk','snk_psa10','snk'))
  AND (
    (
      p.source_code IN ('snkrdunk','snk_psa10','snk')
      AND so.observation_kind='psa10_reference_price'
    )
    OR (
      p.source_code='pricecharting'
      -- 95 identifies the explicit PSA10 plan-row contract. It is an
      -- eligibility predicate only and never participates in winner ordering.
      AND p.source_priority=95
      AND so.observation_kind='psa10_price_guide'
      AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.contract'))='pc_psa10_current_price_v1'
      AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))='pricecharting'
      AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.method'))='pricecharting_explicit_psa10_field_v1'
      AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))='VGPC.chart_data.manualonly.last'
      AND JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.sourceUrl')) LIKE 'https://www.pricecharting.com/%'
      AND p.source_external_entity_id REGEXP '^[0-9]+$'
    )
  )
  AND p.price_usd>0
  AND p.metric_status='ready'
  AND so.observed_at IS NOT NULL
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$';

CREATE OR REPLACE VIEW operator_eligible_pricecharting_variant AS
SELECT DISTINCT
  variant_id,
  1 AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history
WHERE price_source_code='pricecharting';

CREATE OR REPLACE VIEW operator_accepted_psa10_price_history AS
SELECT
  candidate.*,
  candidate.price_route_priority AS selected_price_route_priority,
  CASE WHEN pc.variant_id IS NULL THEN 0 ELSE 1 END AS eligible_pricecharting_exists
FROM operator_eligible_accepted_psa10_price_history candidate
LEFT JOIN operator_eligible_pricecharting_variant pc
  ON pc.variant_id=candidate.variant_id
WHERE NOT EXISTS (
  SELECT 1
  FROM operator_eligible_accepted_psa10_price_history preferred
  WHERE preferred.variant_id=candidate.variant_id
    AND preferred.price_route_priority<candidate.price_route_priority
)
AND (
  (pc.variant_id IS NOT NULL AND candidate.price_source_code='pricecharting')
  OR
  (pc.variant_id IS NULL AND candidate.price_source_code='snkrdunk')
)
AND NOT EXISTS (
  SELECT 1
  FROM operator_eligible_accepted_psa10_price_history newer
  WHERE newer.variant_id=candidate.variant_id
    AND newer.observed_date=candidate.observed_date
    AND newer.price_route_priority=candidate.price_route_priority
    AND (
      newer.price_source_observed_at>candidate.price_source_observed_at
      OR (
        newer.price_source_observed_at=candidate.price_source_observed_at
        AND newer.price_history_acceptance_id>candidate.price_history_acceptance_id
      )
    )
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
 AND h.source_record_id=pop.id
 AND h.variant_id=pop.variant_id
 AND h.observed_date=pop.observed_date
 AND h.source_effective_at=pop.effective_at
 AND h.source_code=pop.source_code
 AND h.external_entity_id=pop.external_entity_id
 AND h.source_payload_sha256=pop.payload_sha256
INNER JOIN catalog_source_identity si
  ON si.variant_id=pop.variant_id
 AND si.source_code='gemrate'
 AND si.external_entity_id=pop.external_entity_id
 AND LOWER(si.match_status)='exact'
WHERE h.metric_kind='psa10_population'
  AND h.source_code='gemrate'
  AND pop.source_code='gemrate'
  AND UPPER(pop.grader_code)='PSA'
  AND UPPER(REPLACE(pop.top_grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
  AND pop.estimated=0
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1
    FROM market_metric_history_acceptance h2
    INNER JOIN market_grader_population_observation pop2
      ON h2.source_record_type='market_grader_population_observation'
     AND h2.source_record_id=pop2.id
     AND h2.variant_id=pop2.variant_id
     AND h2.observed_date=pop2.observed_date
    WHERE h2.metric_kind='psa10_population'
      AND h2.source_code='gemrate'
      AND h2.variant_id=h.variant_id
      AND h2.observed_date=h.observed_date
      AND (pop2.effective_at>pop.effective_at OR (pop2.effective_at=pop.effective_at AND h2.id>h.id))
  );

-- MySQL 5.7 forbids a derived table in a view's FROM clause, so accepted
-- transaction rows are a named view before the daily aggregation view.
CREATE OR REPLACE VIEW operator_eligible_accepted_psa10_sales_rows AS
SELECT
  h.id AS sales_history_acceptance_id,
  s.variant_id,DATE(s.sold_at) AS observed_date,s.quantity,s.transaction_value_usd,
  s.coverage_status,s.source_code,h.lineage_sha256,s.fetched_at AS evidence_at
FROM market_metric_history_acceptance h
INNER JOIN market_sale_observation s
  ON h.source_record_type='market_sale_observation'
 AND h.source_record_id=s.id
 AND h.variant_id=s.variant_id
 AND h.observed_date=DATE(s.sold_at)
 AND h.source_effective_at=s.sold_at
 AND h.external_entity_id=s.external_entity_id
 AND h.source_payload_sha256=s.source_payload_sha256
INNER JOIN catalog_source_identity si
  ON si.variant_id=s.variant_id
 AND si.source_code=CASE WHEN s.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE s.source_code END
 AND si.external_entity_id=s.external_entity_id
 AND LOWER(si.match_status)='exact'
WHERE h.metric_kind='psa10_sale'
  AND h.source_code=si.source_code
  AND s.sold_at IS NOT NULL
  AND s.quantity>0
  AND s.transaction_value_usd>0
  AND UPPER(s.grader_code)='PSA'
  AND UPPER(REPLACE(s.grade_label,' ','')) IN ('10','10.0','PSA10','GEMMINT10')
  AND s.timestamp_quality IN ('exact','date','timestamp','exact_date','relative_resolved','relative_subday')
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$';

CREATE OR REPLACE VIEW operator_accepted_psa10_sales_history AS
SELECT
  tx.variant_id,tx.observed_date,
  MIN(tx.sales_history_acceptance_id) AS sales_history_acceptance_id,
  GROUP_CONCAT(tx.sales_history_acceptance_id ORDER BY tx.sales_history_acceptance_id SEPARATOR ',') AS sales_history_acceptance_ids,
  SUM(tx.quantity) AS sales_count,
  SUM(tx.transaction_value_usd) AS sales_value_usd,
  CASE WHEN MIN(tx.coverage_status='complete')=1 THEN 'complete' ELSE 'partial' END AS sales_coverage_status,
  0 AS sales_verified_zero,
  GROUP_CONCAT(DISTINCT tx.source_code ORDER BY tx.source_code SEPARATOR ',') AS sales_source_codes,
  SHA2(GROUP_CONCAT(tx.lineage_sha256 ORDER BY tx.lineage_sha256 SEPARATOR '|'),256) AS sales_lineage_sha256,
  MAX(tx.evidence_at) AS sales_evidence_at
FROM operator_eligible_accepted_psa10_sales_rows tx
GROUP BY tx.variant_id,tx.observed_date
UNION ALL
SELECT
  a.variant_id,a.observed_date,h.id,CAST(h.id AS CHAR),0,CAST(0 AS DECIMAL(24,6)),
  'complete',1,a.source_code,h.lineage_sha256,r.completed_at
FROM market_metric_history_acceptance h
INNER JOIN market_daily_sales_aggregate a
  ON h.source_record_type='market_daily_sales_aggregate'
 AND h.source_record_id=a.id
 AND h.variant_id=a.variant_id
 AND h.observed_date=a.observed_date
 AND h.source_code=CASE WHEN a.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE a.source_code END
 AND h.source_payload_sha256=a.payload_sha256
INNER JOIN market_ingest_run r ON r.id=a.run_id AND r.status IN ('complete','completed')
INNER JOIN catalog_source_identity si
  ON si.variant_id=a.variant_id
 AND si.source_code=h.source_code
 AND si.external_entity_id=h.external_entity_id
 AND LOWER(si.match_status)='exact'
WHERE h.metric_kind='verified_zero_sales'
  AND a.sales_count=0
  AND a.coverage_status='complete'
  AND h.source_effective_at=r.completed_at
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1
    FROM market_metric_history_acceptance hx
    INNER JOIN market_sale_observation sx
      ON hx.source_record_type='market_sale_observation'
     AND hx.source_record_id=sx.id
     AND hx.variant_id=sx.variant_id
    WHERE hx.metric_kind='psa10_sale'
      AND hx.variant_id=a.variant_id
      AND DATE(sx.sold_at)=a.observed_date
  );

-- Keep the date spine in a named view for the same MySQL 5.7 restriction.
CREATE OR REPLACE VIEW operator_card_daily_fact_dates AS
SELECT variant_id,observed_date FROM operator_accepted_psa10_price_history
UNION
SELECT variant_id,observed_date FROM operator_accepted_psa10_population_history
UNION
SELECT variant_id,observed_date FROM operator_accepted_psa10_sales_history;

CREATE OR REPLACE VIEW operator_card_daily_fact_projection AS
SELECT
  d.variant_id,d.observed_date,
  p.price_usd,
  CASE WHEN p.variant_id IS NULL THEN 'unavailable' ELSE 'ready' END AS price_status,
  p.price_source_code,
  p.price_source_external_entity_id,
  p.price_source_observation_id,
  p.price_payload_sha256,
  p.price_effective_at,
  p.price_source_observed_at,
  p.price_history_acceptance_id,
  p.selected_price_route_priority,
  p.eligible_pricecharting_exists,
  pop.psa10_population,
  CASE WHEN pop.variant_id IS NULL THEN 'unavailable' ELSE 'ready' END AS population_status,
  pop.population_source_code,
  pop.population_source_external_entity_id,
  pop.population_payload_sha256,
  pop.population_effective_at,
  pop.population_history_acceptance_id,
  sales.sales_count,sales.sales_value_usd,
  COALESCE(sales.sales_coverage_status,'unavailable') AS sales_coverage_status,
  COALESCE(sales.sales_verified_zero,0) AS sales_verified_zero,
  sales.sales_source_codes,
  sales.sales_history_acceptance_id,
  sales.sales_history_acceptance_ids,
  sales.sales_lineage_sha256 AS sales_evidence_sha256,
  sales.sales_evidence_at,
  CASE WHEN p.price_usd IS NULL OR pop.psa10_population IS NULL THEN NULL
       ELSE p.price_usd*pop.psa10_population END AS market_cap_usd,
  NULLIF(GREATEST(
    COALESCE(p.price_effective_at,CAST('1000-01-01 00:00:00' AS DATETIME)),
    COALESCE(pop.population_effective_at,CAST('1000-01-01 00:00:00' AS DATETIME)),
    COALESCE(sales.sales_evidence_at,CAST('1000-01-01 00:00:00' AS DATETIME))
  ),CAST('1000-01-01 00:00:00' AS DATETIME)) AS fact_effective_at,
  SHA2(CONCAT_WS('|',d.variant_id,DATE_FORMAT(d.observed_date,'%Y-%m-%d'),
    COALESCE(CAST(p.price_usd AS CHAR),''),COALESCE(CAST(pop.psa10_population AS CHAR),''),
    COALESCE(CAST(sales.sales_count AS CHAR),''),COALESCE(CAST(sales.sales_value_usd AS CHAR),''),
    COALESCE(p.price_payload_sha256,''),COALESCE(pop.population_payload_sha256,''),
    COALESCE(sales.sales_lineage_sha256,'')),256) AS fact_content_sha256,
  SHA2(CONCAT_WS('|','accepted-daily-v1',d.variant_id,DATE_FORMAT(d.observed_date,'%Y-%m-%d'),
    COALESCE(CAST(p.price_history_acceptance_id AS CHAR),''),
    COALESCE(CAST(pop.population_history_acceptance_id AS CHAR),''),
    COALESCE(sales.sales_history_acceptance_ids,''),
    COALESCE(p.price_lineage_sha256,''),COALESCE(pop.population_lineage_sha256,''),
    COALESCE(sales.sales_lineage_sha256,'')),256) AS fact_lineage_sha256
FROM operator_card_daily_fact_dates d
LEFT JOIN operator_accepted_psa10_price_history p
  ON p.variant_id=d.variant_id AND p.observed_date=d.observed_date
LEFT JOIN operator_accepted_psa10_population_history pop
  ON pop.variant_id=d.variant_id AND pop.observed_date=d.observed_date
LEFT JOIN operator_accepted_psa10_sales_history sales
  ON sales.variant_id=d.variant_id AND sales.observed_date=d.observed_date;

CREATE OR REPLACE VIEW operator_canonical_current_metric_projection AS
SELECT
  a.variant_id,a.id AS canonical_metric_acceptance_id,
  ph.price_history_acceptance_id,
  poph.population_history_acceptance_id,
  ph.price_usd AS psa10_price_usd,
  ph.price_source_code AS psa10_price_source_code,
  ph.price_source_external_entity_id AS psa10_price_external_entity_id,
  ph.price_source_observation_id AS psa10_price_source_observation_id,
  ph.price_payload_sha256 AS psa10_price_payload_sha256,
  ph.price_effective_at AS psa10_price_effective_at,
  ph.price_source_observed_at AS psa10_price_source_observed_at,
  ph.selected_price_route_priority,
  ph.eligible_pricecharting_exists,
  poph.psa10_population,
  poph.population_source_code,
  poph.population_source_external_entity_id AS population_external_entity_id,
  poph.population_payload_sha256,
  poph.population_effective_at,
  a.market_cap_usd,a.canonical_market_rank,a.ranking_generation_sha256,
  a.metric_lineage_sha256,a.evidence_sha256 AS metric_evidence_sha256,
  a.accepted_at AS metric_accepted_at
FROM market_canonical_metric_acceptance a
INNER JOIN operator_accepted_psa10_price_history ph
  ON ph.price_history_acceptance_id=a.price_history_acceptance_id
 AND ph.variant_id=a.variant_id
INNER JOIN operator_accepted_psa10_population_history poph
  ON poph.population_history_acceptance_id=a.population_history_acceptance_id
 AND poph.variant_id=a.variant_id
WHERE a.market_cap_usd=ph.price_usd*poph.psa10_population
  AND NOT EXISTS (
    SELECT 1
    FROM operator_accepted_psa10_price_history preferred
    WHERE preferred.variant_id=ph.variant_id
      AND (
        preferred.selected_price_route_priority<ph.selected_price_route_priority
        OR (
          preferred.selected_price_route_priority=ph.selected_price_route_priority
          AND (
            preferred.price_source_observed_at>ph.price_source_observed_at
            OR (
              preferred.price_source_observed_at=ph.price_source_observed_at
              AND preferred.price_history_acceptance_id>ph.price_history_acceptance_id
            )
          )
        )
      )
  )
  AND NOT EXISTS (
    SELECT 1
    FROM operator_accepted_psa10_population_history newer_pop
    WHERE newer_pop.variant_id=poph.variant_id
      AND (
        newer_pop.observed_date>poph.observed_date
        OR (
          newer_pop.observed_date=poph.observed_date
          AND (
            newer_pop.population_effective_at>poph.population_effective_at
            OR (
              newer_pop.population_effective_at=poph.population_effective_at
              AND newer_pop.population_history_acceptance_id>poph.population_history_acceptance_id
            )
          )
        )
      )
  )
  AND (a.canonical_market_rank IS NULL OR a.canonical_market_rank=1+(
    SELECT COUNT(*)
    FROM market_canonical_metric_acceptance ranked
    WHERE ranked.ranking_generation_sha256=a.ranking_generation_sha256
      AND ranked.canonical_market_rank IS NOT NULL
      AND (ranked.market_cap_usd>a.market_cap_usd OR
        (ranked.market_cap_usd=a.market_cap_usd AND ranked.variant_id<a.variant_id))
  ))
  AND a.ranking_generation_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.metric_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM market_canonical_metric_acceptance newer
    WHERE newer.supersedes_acceptance_id=a.id
  );

CREATE OR REPLACE VIEW operator_latest_accepted_daily_fact_projection AS
SELECT f.*
FROM operator_card_daily_fact_projection f
WHERE NOT EXISTS (
  SELECT 1 FROM operator_card_daily_fact_projection newer
  WHERE newer.variant_id=f.variant_id
    AND (newer.observed_date>f.observed_date OR
      (newer.observed_date=f.observed_date AND newer.fact_effective_at>f.fact_effective_at))
  );

-- A legacy accepted identity row is not authority. The freeze must bind both
-- hashes of the current canonical printing row written by the 026 materializer.
CREATE OR REPLACE VIEW operator_canonical_identity_freeze_projection AS
SELECT
  f.variant_id,
  MAX(f.accepted_at) AS identity_accepted_at
FROM operator_binding_freeze f
INNER JOIN catalog_printing_identity p
  ON p.variant_id=f.variant_id
 AND f.content_sha256=p.canonical_printing_sha256
 AND f.evidence_sha256=p.evidence_sha256
WHERE f.freeze_kind='identity'
  AND f.source_code=''
  AND f.external_entity_id=''
  AND f.acceptance_status='accepted'
  AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
  AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
GROUP BY f.variant_id;

CREATE OR REPLACE VIEW operator_card_product_projection AS
SELECT
  v.id AS variant_id,v.opaque_id,
  CASE WHEN am.variant_id IS NULL THEN 0 ELSE 1 END AS active_member,
  am.universe_lock_id AS active_universe_lock_id,
  am.segment_code AS active_segment_code,
  am.member_role AS active_member_role,
  am.market_rank AS active_market_rank,
  p.tcg_code,p.card_language,p.collector_number,p.set_name AS canonical_set_name,
  p.edition_code,p.set_code,p.printing_code,p.rarity_code,p.parallel_code,p.finish_code,
  p.identity_status,p.canonical_printing_sha256,
  p.evidence_sha256 AS printing_evidence_sha256,
  identity_freeze.identity_accepted_at,
  n.official_full_name,
  n.official_full_name AS canonical_name,
  n.official_name_acceptance_id,n.official_name_evidence_sha256,
  n.official_name_observed_at,n.official_name_lineage_sha256,
  n.official_name_source_code,n.official_name_external_entity_id,
  JSON_OBJECT(
    'en',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='en'),
    'zhTW',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhTW'),
    'zhCN',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhCN'),
    'ja',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ja'),
    'ko',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ko')
  ) AS localized_names_json,
  JSON_OBJECT(
    'en',(SELECT l.localized_set_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='en'),
    'zhTW',(SELECT l.localized_set_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhTW'),
    'zhCN',(SELECT l.localized_set_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhCN'),
    'ja',(SELECT l.localized_set_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ja'),
    'ko',(SELECT l.localized_set_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ko')
  ) AS localized_set_names_json,
  JSON_OBJECT(
    'en',(SELECT l.market_story FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='en'),
    'zhTW',(SELECT l.market_story FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhTW'),
    'zhCN',(SELECT l.market_story FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhCN'),
    'ja',(SELECT l.market_story FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ja'),
    'ko',(SELECT l.market_story FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ko')
  ) AS localized_stories_json,
  (SELECT MAX(l.observed_at) FROM catalog_variant_locale l WHERE l.variant_id=v.id) AS locale_latest_observed_at,
  CASE WHEN NOT EXISTS (
    SELECT 1 FROM catalog_variant_locale l
    WHERE l.variant_id=v.id
      AND (l.localized_name IS NOT NULL OR l.localized_set_name IS NOT NULL OR l.market_story IS NOT NULL)
      AND (l.provenance_source_code='' OR l.content_sha256 NOT REGEXP '^[0-9a-f]{64}$'
        OR l.observed_at IS NULL OR l.provenance_json IS NULL)
  ) THEN 1 ELSE 0 END AS locale_evidence_complete,
  (SELECT COUNT(*) FROM catalog_source_identity si WHERE si.variant_id=v.id AND LOWER(si.match_status)='exact'
    AND (si.bound_set_code='' OR si.bound_set_code=p.set_code)
    AND (si.bound_printing_code='' OR si.bound_printing_code=p.printing_code)) AS exact_binding_count,
  (SELECT GROUP_CONCAT(DISTINCT si.source_code ORDER BY si.source_code SEPARATOR ',')
    FROM catalog_source_identity si WHERE si.variant_id=v.id AND LOWER(si.match_status)='exact'
    AND (si.bound_set_code='' OR si.bound_set_code=p.set_code)
    AND (si.bound_printing_code='' OR si.bound_printing_code=p.printing_code)) AS exact_source_codes,
  CASE WHEN NOT EXISTS (
    SELECT 1 FROM catalog_source_identity si WHERE si.variant_id=v.id AND LOWER(si.match_status)='exact'
      AND (si.bound_set_code='' OR si.bound_set_code=p.set_code)
      AND (si.bound_printing_code='' OR si.bound_printing_code=p.printing_code)
      AND (si.evidence_sha256 NOT REGEXP '^[0-9a-f]{64}$' OR si.bind_evidence_json IS NULL)
  ) THEN 1 ELSE 0 END AS exact_binding_evidence_complete,
  m.psa10_price_usd,m.psa10_price_source_code,
  m.psa10_price_external_entity_id,
  m.psa10_price_external_entity_id AS psa10_price_source_external_entity_id,
  m.psa10_price_source_observation_id,m.psa10_price_payload_sha256,
  m.psa10_price_effective_at,m.psa10_price_source_observed_at,
  m.selected_price_route_priority,m.eligible_pricecharting_exists,
  m.psa10_population,m.population_source_code,
  m.population_external_entity_id,
  m.population_external_entity_id AS population_source_external_entity_id,
  m.population_payload_sha256,m.population_effective_at,
  m.market_cap_usd,m.canonical_market_rank,m.ranking_generation_sha256,
  m.metric_lineage_sha256,m.canonical_metric_acceptance_id,
  m.price_history_acceptance_id,m.population_history_acceptance_id,
  d.sales_count AS latest_daily_sales_count,
  d.sales_value_usd AS latest_daily_sales_value_usd,
  COALESCE(d.sales_coverage_status,'unavailable') AS latest_daily_sales_coverage_status,
  d.sales_evidence_at AS latest_daily_sales_evidence_at,
  d.sales_history_acceptance_id AS latest_sales_history_acceptance_id,
  rawp.price_usd AS ungraded_reference_price_usd,
  rawp.source_code AS ungraded_reference_source_code,
  rawp.external_entity_id AS ungraded_reference_external_entity_id,
  rawp.source_payload_sha256 AS ungraded_reference_payload_sha256,
  rawp.observed_at AS ungraded_reference_observed_at,
  img.canonical_image_content_sha256,img.canonical_image_width,img.canonical_image_height,
  img.canonical_image_qc_at,img.canonical_image_source_path,img.canonical_image_source_observed_at,
  img.canonical_image_acceptance_id,img.canonical_image_lineage_sha256,
  img.canonical_image_asset_id,img.canonical_image_object_key,img.canonical_image_mime_type,
  img.canonical_image_captured_at,img.canonical_image_snk_item_id,img.canonical_image_product_url,
  img.canonical_image_master_payload_sha256,img.canonical_image_default_url_sha256,
  img.canonical_image_downloaded_bytes_sha256,img.canonical_image_transform_sha256,
  img.canonical_image_asset_id AS image_asset_id,
  img.canonical_image_content_sha256 AS image_content_sha256,
  img.canonical_image_object_key AS image_object_key,
  img.canonical_image_mime_type AS image_mime_type,
  img.canonical_image_width AS image_width_px,
  img.canonical_image_height AS image_height_px,
  img.canonical_image_captured_at AS image_captured_at,
  img.canonical_image_qc_at AS image_qc_at,
  img.canonical_image_source_path AS image_source_path,
  img.canonical_image_lineage_sha256 AS image_source_version_sha256,
  img.canonical_image_source_observed_at AS image_source_observed_at,
  img.canonical_image_asset_id AS snk_image_asset_id,
  img.canonical_image_content_sha256 AS snk_image_content_sha256,
  img.canonical_image_object_key AS snk_image_object_key,
  img.canonical_image_mime_type AS snk_image_mime_type,
  img.canonical_image_width AS snk_image_width_px,
  img.canonical_image_height AS snk_image_height_px,
  img.canonical_image_captured_at AS snk_image_captured_at,
  img.canonical_image_qc_at AS snk_image_qc_at,
  img.canonical_image_source_path AS snk_image_source_path,
  img.canonical_image_lineage_sha256 AS snk_image_source_version_sha256,
  img.canonical_image_source_observed_at AS snk_image_source_observed_at,
  CASE WHEN img.canonical_image_acceptance_id IS NULL THEN 0 ELSE 1 END AS snk_image_available,
  JSON_OBJECT(
    'en',n.official_full_name,
    'zhTW',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhTW'),
    'zhCN',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='zhCN'),
    'ja',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ja'),
    'ko',(SELECT l.localized_name FROM catalog_variant_locale l WHERE l.variant_id=v.id AND l.locale_code='ko')
  ) AS image_alt_json,
  CASE WHEN p.variant_id IS NOT NULL
    AND p.identity_status IN ('confirmed','canonical')
    AND p.tcg_code<>'' AND p.card_language IN ('en','zhTW','zhCN','ja','ko')
    AND p.collector_number<>'' AND p.set_code<>''
    AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
    AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
    AND p.provenance_json IS NOT NULL AND p.observed_at IS NOT NULL
    AND identity_freeze.variant_id IS NOT NULL
    THEN 1 ELSE 0 END AS identity_complete,
  CASE WHEN m.canonical_metric_acceptance_id IS NULL THEN 0 ELSE 1 END AS metric_complete,
  CASE WHEN img.canonical_image_acceptance_id IS NULL THEN 0 ELSE 1 END AS image_complete,
  CASE WHEN NOT EXISTS (
    SELECT 1 FROM catalog_variant_locale l
    WHERE l.variant_id=v.id
      AND (l.localized_name IS NOT NULL OR l.localized_set_name IS NOT NULL OR l.market_story IS NOT NULL)
      AND (l.provenance_source_code='' OR l.content_sha256 NOT REGEXP '^[0-9a-f]{64}$'
        OR l.observed_at IS NULL OR l.provenance_json IS NULL)
  ) THEN 1 ELSE 0 END AS locale_complete,
  CASE WHEN n.official_name_acceptance_id IS NULL THEN 0 ELSE 1 END AS official_name_complete,
  CASE WHEN m.canonical_metric_acceptance_id IS NULL THEN 0 ELSE 1 END AS canonical_metric_complete,
  CASE WHEN img.canonical_image_acceptance_id IS NULL THEN 0 ELSE 1 END AS canonical_image_complete,
  CASE WHEN m.canonical_market_rank IS NULL OR m.canonical_market_rank=0 THEN 0 ELSE 1 END AS canonical_rank_complete,
  CASE WHEN p.variant_id IS NOT NULL
    AND p.identity_status IN ('confirmed','canonical')
    AND p.tcg_code<>'' AND p.card_language IN ('en','zhTW','zhCN','ja','ko')
    AND p.collector_number<>'' AND p.set_code<>''
    AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
    AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
    AND p.provenance_json IS NOT NULL AND p.observed_at IS NOT NULL
    AND identity_freeze.variant_id IS NOT NULL
    AND m.canonical_metric_acceptance_id IS NOT NULL
    AND img.canonical_image_acceptance_id IS NOT NULL
    AND n.official_name_acceptance_id IS NOT NULL
    THEN 1 ELSE 0 END AS product_ready
FROM catalog_variant v
LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
LEFT JOIN operator_canonical_identity_freeze_projection identity_freeze
  ON identity_freeze.variant_id=v.id
LEFT JOIN market_universe_member am
  ON am.variant_id=v.id
 AND am.universe_lock_id=(SELECT u.id FROM market_universe_lock u
   WHERE u.is_current=1 ORDER BY u.effective_at DESC,u.id DESC LIMIT 1)
LEFT JOIN operator_official_name_projection n ON n.variant_id=v.id
LEFT JOIN operator_canonical_current_metric_projection m ON m.variant_id=v.id
LEFT JOIN operator_latest_accepted_daily_fact_projection d ON d.variant_id=v.id
LEFT JOIN operator_canonical_image_projection img ON img.variant_id=v.id
LEFT JOIN market_ungraded_reference_price rawp
  ON rawp.variant_id=v.id
 AND NOT EXISTS (SELECT 1 FROM market_ungraded_reference_price newer
   WHERE newer.variant_id=rawp.variant_id
     AND (newer.observed_at>rawp.observed_at OR
       (newer.observed_at=rawp.observed_at AND newer.id>rawp.id)));

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('026');
