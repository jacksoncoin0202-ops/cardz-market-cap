-- Canonical frontend read models and evidence lineage.
-- MySQL 8.x only. Apply after 022_new_era_warehouse.mysql.sql.
--
-- This migration formalizes columns which already exist in the operator
-- database but were missing from the immutable migration chain. Every ALTER
-- is guarded through information_schema so an interrupted or drifted live
-- database can replay the file without changing an existing column type.
-- Provider claims stay on catalog_source_identity. Public display identity is
-- read exclusively from catalog_printing_identity.

-- -------------------------------------------------------------------------
-- Canonical printing and exact provider-binding drift.
-- -------------------------------------------------------------------------

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant' AND column_name='set_code') = 0,
  CONCAT('ALTER TABLE catalog_variant ADD COLUMN set_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER set_name'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant' AND column_name='printing_code') = 0,
  CONCAT('ALTER TABLE catalog_variant ADD COLUMN printing_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER set_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant' AND column_name='rarity_code') = 0,
  CONCAT('ALTER TABLE catalog_variant ADD COLUMN rarity_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER printing_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='catalog_variant' AND index_name='ix_catalog_variant_set_identity') = 0,
  'ALTER TABLE catalog_variant ADD KEY ix_catalog_variant_set_identity (tcg_code, card_language, set_code, collector_number, printing_code)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_printing_identity' AND column_name='set_code') = 0,
  CONCAT('ALTER TABLE catalog_printing_identity ADD COLUMN set_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER set_name'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_printing_identity' AND column_name='printing_code') = 0,
  CONCAT('ALTER TABLE catalog_printing_identity ADD COLUMN printing_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER set_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_printing_identity' AND column_name='rarity_code') = 0,
  CONCAT('ALTER TABLE catalog_printing_identity ADD COLUMN rarity_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER printing_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='catalog_printing_identity' AND index_name='ix_catalog_printing_exact') = 0,
  'ALTER TABLE catalog_printing_identity ADD KEY ix_catalog_printing_exact (tcg_code, card_language, set_code, collector_number, printing_code, identity_status)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity' AND column_name='source_product_number') = 0,
  CONCAT('ALTER TABLE catalog_source_identity ADD COLUMN source_product_number VARCHAR(191) NOT NULL DEFAULT ', CHAR(39), CHAR(39)),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity' AND column_name='bound_set_code') = 0,
  CONCAT('ALTER TABLE catalog_source_identity ADD COLUMN bound_set_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39)),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity' AND column_name='bound_printing_code') = 0,
  CONCAT('ALTER TABLE catalog_source_identity ADD COLUMN bound_printing_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39)),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity' AND column_name='bind_evidence_json') = 0,
  'ALTER TABLE catalog_source_identity ADD COLUMN bind_evidence_json JSON NULL',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='catalog_source_identity' AND index_name='ix_catalog_source_exact_printing') = 0,
  'ALTER TABLE catalog_source_identity ADD KEY ix_catalog_source_exact_printing (variant_id, source_code, match_status, bound_set_code, bound_printing_code)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

-- -------------------------------------------------------------------------
-- Per-observation and per-locale evidence lineage.
-- -------------------------------------------------------------------------

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_price_observation' AND column_name='source_external_entity_id') = 0,
  CONCAT('ALTER TABLE market_price_observation ADD COLUMN source_external_entity_id VARCHAR(191) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER source_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='market_price_observation' AND column_name='source_observation_id') = 0,
  'ALTER TABLE market_price_observation ADD COLUMN source_observation_id BIGINT UNSIGNED NULL AFTER source_external_entity_id',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='market_price_observation' AND index_name='ix_market_price_source_lineage') = 0,
  'ALTER TABLE market_price_observation ADD KEY ix_market_price_source_lineage (source_code, source_external_entity_id, observed_date)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='market_price_observation' AND index_name='ix_market_price_source_observation') = 0,
  'ALTER TABLE market_price_observation ADD KEY ix_market_price_source_observation (source_observation_id)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.table_constraints
   WHERE table_schema=DATABASE() AND table_name='market_price_observation' AND constraint_name='fk_market_price_source_observation') = 0,
  'ALTER TABLE market_price_observation ADD CONSTRAINT fk_market_price_source_observation FOREIGN KEY (source_observation_id) REFERENCES market_source_observation(id)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND column_name='provenance_source_code') = 0,
  CONCAT('ALTER TABLE catalog_variant_locale ADD COLUMN provenance_source_code VARCHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER market_story'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND column_name='content_sha256') = 0,
  CONCAT('ALTER TABLE catalog_variant_locale ADD COLUMN content_sha256 CHAR(64) NOT NULL DEFAULT ', CHAR(39), CHAR(39), ' AFTER provenance_source_code'),
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND column_name='observed_at') = 0,
  'ALTER TABLE catalog_variant_locale ADD COLUMN observed_at DATETIME(6) NULL AFTER content_sha256',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.columns
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND column_name='provenance_json') = 0,
  'ALTER TABLE catalog_variant_locale ADD COLUMN provenance_json JSON NULL AFTER observed_at',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.statistics
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND index_name='ix_catalog_variant_locale_evidence') = 0,
  'ALTER TABLE catalog_variant_locale ADD KEY ix_catalog_variant_locale_evidence (provenance_source_code, observed_at)',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

-- Preserve any legacy hyphenated locale rows before moving them onto the
-- canonical DB codes. Where both aliases exist, fill only missing content on
-- the canonical row and retain the legacy payload hash in the story pointer.
INSERT IGNORE INTO catalog_story_pointer
  (variant_id, locale_code, source_path, source_version_sha256, observed_at)
SELECT
  variant_id,
  CASE locale_code WHEN 'zh-TW' THEN 'zhTW' WHEN 'zh-CN' THEN 'zhCN' ELSE locale_code END,
  CONCAT('catalog_variant_locale:migration023:', locale_code),
  CASE
    WHEN content_sha256 REGEXP '^[0-9a-f]{64}$' THEN content_sha256
    ELSE SHA2(CONCAT_WS('|', localized_name, localized_set_name, market_story), 256)
  END,
  COALESCE(observed_at, updated_at)
FROM catalog_variant_locale
WHERE locale_code IN ('zh-TW', 'zh-CN');

UPDATE catalog_variant_locale canonical
INNER JOIN catalog_variant_locale legacy
  ON legacy.variant_id=canonical.variant_id
 AND legacy.locale_code=CASE canonical.locale_code
   WHEN 'zhTW' THEN 'zh-TW'
   WHEN 'zhCN' THEN 'zh-CN'
 END
SET
  canonical.localized_name=COALESCE(canonical.localized_name, legacy.localized_name),
  canonical.localized_set_name=COALESCE(canonical.localized_set_name, legacy.localized_set_name),
  canonical.market_story=COALESCE(canonical.market_story, legacy.market_story),
  canonical.observed_at=GREATEST(
    COALESCE(canonical.observed_at, canonical.updated_at),
    COALESCE(legacy.observed_at, legacy.updated_at)
  )
WHERE canonical.locale_code IN ('zhTW', 'zhCN');

DELETE legacy
FROM catalog_variant_locale legacy
INNER JOIN catalog_variant_locale canonical
  ON canonical.variant_id=legacy.variant_id
 AND canonical.locale_code=CASE legacy.locale_code
   WHEN 'zh-TW' THEN 'zhTW'
   WHEN 'zh-CN' THEN 'zhCN'
 END
WHERE legacy.locale_code IN ('zh-TW', 'zh-CN');

UPDATE catalog_variant_locale
SET locale_code=CASE locale_code
  WHEN 'zh-TW' THEN 'zhTW'
  WHEN 'zh-CN' THEN 'zhCN'
  ELSE locale_code
END
WHERE locale_code IN ('zh-TW', 'zh-CN');

-- Existing DB editorial content is registered honestly as migrated DB-state
-- evidence. The local editorial import later replaces this provenance with
-- the exact source-file hash for rows it owns.
UPDATE catalog_variant_locale
SET
  provenance_source_code=CASE
    WHEN provenance_source_code='' THEN 'migration023_db_state'
    ELSE provenance_source_code
  END,
  content_sha256=CASE
    WHEN content_sha256 REGEXP '^[0-9a-f]{64}$' THEN content_sha256
    ELSE SHA2(
      CONCAT_WS(
        '|',
        locale_code,
        COALESCE(localized_name, ''),
        COALESCE(localized_set_name, ''),
        COALESCE(market_story, '')
      ),
      256
    )
  END,
  observed_at=COALESCE(observed_at, updated_at),
  provenance_json=COALESCE(
    provenance_json,
    JSON_OBJECT(
      'contract', 'migration023-db-state-v1',
      'sourceCode', CASE
        WHEN provenance_source_code='' THEN 'migration023_db_state'
        ELSE provenance_source_code
      END,
      'observedAt', DATE_FORMAT(COALESCE(observed_at, updated_at), '%Y-%m-%dT%H:%i:%s.%fZ')
    )
  )
WHERE locale_code IN ('en', 'zhTW', 'zhCN', 'ja', 'ko');

SET @cardz_ddl = IF(
  (SELECT COUNT(*) FROM information_schema.table_constraints
   WHERE table_schema=DATABASE() AND table_name='catalog_variant_locale' AND constraint_name='chk_catalog_variant_locale_code_v1') = 0,
  'ALTER TABLE catalog_variant_locale ADD CONSTRAINT chk_catalog_variant_locale_code_v1 CHECK (locale_code IN (''en'', ''zhTW'', ''zhCN'', ''ja'', ''ko''))',
  'DO 0'
);
PREPARE cardz_stmt FROM @cardz_ddl;
EXECUTE cardz_stmt;
DEALLOCATE PREPARE cardz_stmt;

-- Bind legacy exact rows to the already accepted provider claim. No
-- canonical display field is copied into a bound_* provider claim.
UPDATE catalog_source_identity si
INNER JOIN operator_binding_freeze sf
  ON sf.variant_id=si.variant_id
 AND sf.freeze_kind='source'
 AND sf.source_code=si.source_code
 AND sf.external_entity_id=si.external_entity_id
 AND sf.acceptance_status='accepted'
SET si.bind_evidence_json=COALESCE(
  si.bind_evidence_json,
  JSON_OBJECT(
    'contract', 'legacy-exact-freeze-v1',
    'sourceCode', si.source_code,
    'externalEntityId', si.external_entity_id,
    'bindingEvidenceSha256', si.evidence_sha256,
    'freezeEvidenceSha256', sf.evidence_sha256
  )
)
WHERE si.match_status='exact';

-- Resolve the provider entity from the unique accepted exact freeze, then
-- attach a raw source observation only when its date and payload hash match.
UPDATE market_price_observation p
INNER JOIN operator_binding_freeze sf
  ON sf.variant_id=p.variant_id
 AND sf.freeze_kind='source'
 AND sf.source_code=p.source_code
 AND sf.acceptance_status='accepted'
INNER JOIN catalog_source_identity si
  ON si.variant_id=p.variant_id
 AND si.source_code=p.source_code
 AND si.external_entity_id=sf.external_entity_id
 AND si.match_status='exact'
SET p.source_external_entity_id=sf.external_entity_id
WHERE p.source_external_entity_id='';

UPDATE market_price_observation p
SET p.source_observation_id=(
  SELECT so.id
  FROM market_source_observation so
  WHERE so.source_code=p.source_code
    AND so.external_entity_id=p.source_external_entity_id
    AND so.observed_date=p.observed_date
    AND so.payload_sha256=p.payload_sha256
  ORDER BY so.observed_at DESC, so.id DESC
  LIMIT 1
)
WHERE p.source_observation_id IS NULL
  AND p.source_external_entity_id<>''
  AND EXISTS (
    SELECT 1
    FROM market_source_observation so
    WHERE so.source_code=p.source_code
      AND so.external_entity_id=p.source_external_entity_id
      AND so.observed_date=p.observed_date
      AND so.payload_sha256=p.payload_sha256
  );

-- RAW card references remain separate from PSA 10 ranking prices.
CREATE TABLE IF NOT EXISTS market_ungraded_reference_price (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    external_entity_id VARCHAR(191) NOT NULL,
    source_url VARCHAR(1000) NOT NULL,
    observed_at DATETIME(6) NOT NULL,
    price_usd DECIMAL(18,6) NOT NULL,
    source_payload_sha256 CHAR(64) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_ungraded_reference_observation
        (variant_id, source_code, external_entity_id, observed_at, source_payload_sha256),
    KEY ix_ungraded_reference_latest (variant_id, observed_at),
    KEY ix_ungraded_reference_source (source_code, external_entity_id),
    CONSTRAINT fk_ungraded_reference_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- -------------------------------------------------------------------------
-- Canonical daily fact projection.
--
-- Positive sales come only from immutable transaction observations. A
-- numeric zero is emitted only from a complete exact-bound derived cache row.
-- Otherwise the sales fields stay NULL with coverage_status=unavailable.
-- -------------------------------------------------------------------------

CREATE OR REPLACE VIEW operator_card_daily_fact_projection AS
WITH
eligible_price AS (
  SELECT
    p.id,
    p.variant_id,
    p.source_code,
    p.source_external_entity_id,
    p.source_observation_id,
    p.observed_date,
    p.effective_at,
    COALESCE(so.observed_at, p.effective_at) AS source_observed_at,
    p.price_usd,
    p.metric_status,
    p.payload_sha256,
    ROW_NUMBER() OVER (
      PARTITION BY p.variant_id, p.observed_date
      ORDER BY p.source_priority ASC, p.effective_at DESC, p.id DESC
    ) AS source_rank
  FROM market_price_observation p
  INNER JOIN catalog_source_identity si
    ON si.variant_id=p.variant_id
   AND si.source_code=p.source_code
   AND si.external_entity_id=p.source_external_entity_id
   AND si.match_status='exact'
  INNER JOIN operator_binding_freeze sf
    ON sf.variant_id=p.variant_id
   AND sf.freeze_kind='source'
   AND sf.source_code=p.source_code
   AND sf.external_entity_id=p.source_external_entity_id
   AND sf.acceptance_status='accepted'
  LEFT JOIN market_source_observation so
    ON so.id=p.source_observation_id
   AND so.source_code=p.source_code
   AND so.external_entity_id=p.source_external_entity_id
   AND so.payload_sha256=p.payload_sha256
  LEFT JOIN market_banned_source_policy bp
    ON bp.source_code=p.source_code
   AND bp.policy='ignore_for_price'
  WHERE p.price_usd IS NOT NULL
    AND p.price_usd > 0
    AND p.metric_status='ready'
    AND p.source_external_entity_id<>''
    AND (p.source_observation_id IS NULL OR so.id IS NOT NULL)
    AND p.payload_sha256 REGEXP '^[0-9a-f]{64}$'
    AND bp.source_code IS NULL
),
daily_price AS (
  SELECT * FROM eligible_price WHERE source_rank=1
),
eligible_population AS (
  SELECT
    pop.id,
    pop.variant_id,
    pop.source_code,
    pop.external_entity_id AS source_external_entity_id,
    pop.observed_date,
    pop.effective_at,
    pop.top_grade_population AS psa10_population,
    pop.payload_sha256,
    ROW_NUMBER() OVER (
      PARTITION BY pop.variant_id, pop.observed_date
      ORDER BY pop.effective_at DESC, pop.id DESC
    ) AS source_rank
  FROM market_grader_population_observation pop
  INNER JOIN catalog_source_identity si
    ON si.variant_id=pop.variant_id
   AND si.source_code=pop.source_code
   AND si.external_entity_id=pop.external_entity_id
   AND si.match_status='exact'
  INNER JOIN operator_binding_freeze sf
    ON sf.variant_id=pop.variant_id
   AND sf.freeze_kind='source'
   AND sf.source_code=pop.source_code
   AND sf.external_entity_id=pop.external_entity_id
   AND sf.acceptance_status='accepted'
  WHERE UPPER(pop.grader_code)='PSA'
    AND pop.estimated=0
),
daily_population AS (
  SELECT * FROM eligible_population WHERE source_rank=1
),
transaction_sales AS (
  SELECT
    s.variant_id,
    DATE(s.sold_at) AS observed_date,
    SUM(s.quantity) AS sales_count,
    SUM(s.transaction_value_usd) AS sales_value_usd,
    CASE
      WHEN MIN(s.coverage_status='complete')=1 THEN 'complete'
      ELSE 'partial'
    END AS coverage_status,
    0 AS verified_zero,
    MAX(s.fetched_at) AS evidence_at,
    GROUP_CONCAT(DISTINCT s.source_code ORDER BY s.source_code SEPARATOR ',') AS source_codes,
    SHA2(
      GROUP_CONCAT(DISTINCT s.transaction_fingerprint ORDER BY s.transaction_fingerprint SEPARATOR '|'),
      256
    ) AS evidence_sha256
  FROM market_sale_observation s
  INNER JOIN catalog_source_identity si
    ON si.variant_id=s.variant_id
   AND si.source_code=s.source_code
   AND si.external_entity_id=s.external_entity_id
   AND si.match_status='exact'
  INNER JOIN operator_binding_freeze sf
    ON sf.variant_id=s.variant_id
   AND sf.freeze_kind='source'
   AND sf.source_code=s.source_code
   AND sf.external_entity_id=s.external_entity_id
   AND sf.acceptance_status='accepted'
  WHERE s.sold_at IS NOT NULL
    AND s.quantity > 0
    AND s.transaction_value_usd > 0
    AND UPPER(s.grader_code)='PSA'
    AND UPPER(REPLACE(s.grade_label, ' ', '')) IN ('10', '10.0', 'PSA10', 'GEMMINT10')
    AND s.timestamp_quality IN ('exact', 'date', 'timestamp', 'exact_date', 'relative_resolved', 'relative_subday')
  GROUP BY s.variant_id, DATE(s.sold_at)
),
verified_zero_sales AS (
  SELECT
    a.variant_id,
    a.observed_date,
    0 AS sales_count,
    CAST(0 AS DECIMAL(24,6)) AS sales_value_usd,
    'complete' AS coverage_status,
    1 AS verified_zero,
    MAX(r.completed_at) AS evidence_at,
    GROUP_CONCAT(DISTINCT a.source_code ORDER BY a.source_code SEPARATOR ',') AS source_codes,
    SHA2(GROUP_CONCAT(DISTINCT a.payload_sha256 ORDER BY a.payload_sha256 SEPARATOR '|'), 256) AS evidence_sha256
  FROM market_daily_sales_aggregate a
  INNER JOIN market_ingest_run r ON r.id=a.run_id AND r.status IN ('complete', 'completed')
  WHERE a.sales_count=0
    AND a.coverage_status='complete'
    AND EXISTS (
      SELECT 1
      FROM catalog_source_identity si
      INNER JOIN operator_binding_freeze sf
        ON sf.variant_id=si.variant_id
       AND sf.freeze_kind='source'
       AND sf.source_code=si.source_code
       AND sf.external_entity_id=si.external_entity_id
       AND sf.acceptance_status='accepted'
      WHERE si.variant_id=a.variant_id
        AND si.source_code=a.source_code
        AND si.match_status='exact'
    )
    AND NOT EXISTS (
      SELECT 1
      FROM transaction_sales tx
      WHERE tx.variant_id=a.variant_id AND tx.observed_date=a.observed_date
    )
  GROUP BY a.variant_id, a.observed_date
),
daily_sales AS (
  SELECT * FROM transaction_sales
  UNION ALL
  SELECT * FROM verified_zero_sales
),
fact_dates AS (
  SELECT variant_id, observed_date FROM daily_price
  UNION
  SELECT variant_id, observed_date FROM daily_population
  UNION
  SELECT variant_id, observed_date FROM daily_sales
)
SELECT
  d.variant_id,
  d.observed_date,
  p.price_usd,
  CASE WHEN p.variant_id IS NULL THEN 'unavailable' ELSE 'ready' END AS price_status,
  p.source_code AS price_source_code,
  p.source_external_entity_id AS price_source_external_entity_id,
  p.source_observation_id AS price_source_observation_id,
  p.payload_sha256 AS price_payload_sha256,
  p.effective_at AS price_effective_at,
  p.source_observed_at AS price_source_observed_at,
  pop.psa10_population,
  CASE WHEN pop.variant_id IS NULL THEN 'unavailable' ELSE 'ready' END AS population_status,
  pop.source_code AS population_source_code,
  pop.source_external_entity_id AS population_source_external_entity_id,
  pop.payload_sha256 AS population_payload_sha256,
  pop.effective_at AS population_effective_at,
  sales.sales_count,
  sales.sales_value_usd,
  COALESCE(sales.coverage_status, 'unavailable') AS sales_coverage_status,
  COALESCE(sales.verified_zero, 0) AS sales_verified_zero,
  sales.source_codes AS sales_source_codes,
  sales.evidence_sha256 AS sales_evidence_sha256,
  sales.evidence_at AS sales_evidence_at,
  CASE
    WHEN p.price_usd IS NULL OR pop.psa10_population IS NULL THEN NULL
    ELSE p.price_usd * pop.psa10_population
  END AS market_cap_usd,
  NULLIF(
    GREATEST(
      COALESCE(p.effective_at, CAST('1000-01-01 00:00:00' AS DATETIME)),
      COALESCE(pop.effective_at, CAST('1000-01-01 00:00:00' AS DATETIME)),
      COALESCE(sales.evidence_at, CAST('1000-01-01 00:00:00' AS DATETIME))
    ),
    CAST('1000-01-01 00:00:00' AS DATETIME)
  ) AS fact_effective_at,
  SHA2(
    CONCAT_WS(
      '|',
      d.variant_id,
      DATE_FORMAT(d.observed_date, '%Y-%m-%d'),
      COALESCE(CAST(p.price_usd AS CHAR), ''),
      COALESCE(CAST(pop.psa10_population AS CHAR), ''),
      COALESCE(CAST(sales.sales_count AS CHAR), ''),
      COALESCE(CAST(sales.sales_value_usd AS CHAR), ''),
      COALESCE(p.payload_sha256, ''),
      COALESCE(pop.payload_sha256, ''),
      COALESCE(sales.evidence_sha256, '')
    ),
    256
  ) AS fact_content_sha256
FROM fact_dates d
LEFT JOIN daily_price p
  ON p.variant_id=d.variant_id AND p.observed_date=d.observed_date
LEFT JOIN daily_population pop
  ON pop.variant_id=d.variant_id AND pop.observed_date=d.observed_date
LEFT JOIN daily_sales sales
  ON sales.variant_id=d.variant_id AND sales.observed_date=d.observed_date;

-- -------------------------------------------------------------------------
-- One-row-per-variant product projection.
--
-- No catalog_variant identity fallback and no editorial runtime overlay are
-- allowed. Missing canonical rows remain NULL so the release gate can stop.
-- -------------------------------------------------------------------------

CREATE OR REPLACE VIEW operator_card_product_projection AS
WITH
locale_rollup AS (
  SELECT
    variant_id,
    MAX(CASE WHEN locale_code='en' THEN localized_name END) AS name_en,
    MAX(CASE WHEN locale_code='zhTW' THEN localized_name END) AS name_zhTW,
    MAX(CASE WHEN locale_code='zhCN' THEN localized_name END) AS name_zhCN,
    MAX(CASE WHEN locale_code='ja' THEN localized_name END) AS name_ja,
    MAX(CASE WHEN locale_code='ko' THEN localized_name END) AS name_ko,
    MAX(CASE WHEN locale_code='en' THEN localized_set_name END) AS set_en,
    MAX(CASE WHEN locale_code='zhTW' THEN localized_set_name END) AS set_zhTW,
    MAX(CASE WHEN locale_code='zhCN' THEN localized_set_name END) AS set_zhCN,
    MAX(CASE WHEN locale_code='ja' THEN localized_set_name END) AS set_ja,
    MAX(CASE WHEN locale_code='ko' THEN localized_set_name END) AS set_ko,
    MAX(CASE WHEN locale_code='en' THEN market_story END) AS story_en,
    MAX(CASE WHEN locale_code='zhTW' THEN market_story END) AS story_zhTW,
    MAX(CASE WHEN locale_code='zhCN' THEN market_story END) AS story_zhCN,
    MAX(CASE WHEN locale_code='ja' THEN market_story END) AS story_ja,
    MAX(CASE WHEN locale_code='ko' THEN market_story END) AS story_ko,
    MIN(
      provenance_source_code<>''
      AND content_sha256 REGEXP '^[0-9a-f]{64}$'
      AND observed_at IS NOT NULL
      AND provenance_json IS NOT NULL
    ) AS locale_evidence_complete,
    MAX(observed_at) AS locale_latest_observed_at
  FROM catalog_variant_locale
  WHERE locale_code IN ('en', 'zhTW', 'zhCN', 'ja', 'ko')
  GROUP BY variant_id
),
accepted_bindings AS (
  SELECT
    si.variant_id,
    COUNT(*) AS exact_binding_count,
    GROUP_CONCAT(DISTINCT si.source_code ORDER BY si.source_code SEPARATOR ',') AS exact_source_codes,
    MIN(
      si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
      AND sf.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
      AND si.bind_evidence_json IS NOT NULL
      AND (si.bound_set_code='' OR si.bound_set_code=p.set_code)
      AND (si.bound_printing_code='' OR si.bound_printing_code=p.printing_code)
    ) AS exact_binding_evidence_complete
  FROM catalog_source_identity si
  INNER JOIN catalog_printing_identity p
    ON p.variant_id=si.variant_id
  INNER JOIN operator_binding_freeze sf
    ON sf.variant_id=si.variant_id
   AND sf.freeze_kind='source'
   AND sf.source_code=si.source_code
   AND sf.external_entity_id=si.external_entity_id
   AND sf.acceptance_status='accepted'
  WHERE si.match_status='exact'
  GROUP BY si.variant_id
),
identity_acceptance AS (
  SELECT variant_id, MAX(accepted_at) AS accepted_at
  FROM operator_binding_freeze
  WHERE freeze_kind='identity' AND acceptance_status='accepted'
  GROUP BY variant_id
),
latest_price AS (
  SELECT ranked.*
  FROM (
    SELECT
      f.*,
      ROW_NUMBER() OVER (
        PARTITION BY f.variant_id
        ORDER BY f.observed_date DESC, f.price_effective_at DESC
      ) AS metric_rank
    FROM operator_card_daily_fact_projection f
    WHERE f.price_status='ready'
      AND f.price_source_observation_id IS NOT NULL
  ) ranked
  WHERE ranked.metric_rank=1
),
latest_population AS (
  SELECT ranked.*
  FROM (
    SELECT
      f.*,
      ROW_NUMBER() OVER (
        PARTITION BY f.variant_id
        ORDER BY f.observed_date DESC, f.population_effective_at DESC
      ) AS metric_rank
    FROM operator_card_daily_fact_projection f
    WHERE f.population_status='ready'
  ) ranked
  WHERE ranked.metric_rank=1
),
latest_sales AS (
  SELECT ranked.*
  FROM (
    SELECT
      f.*,
      ROW_NUMBER() OVER (
        PARTITION BY f.variant_id
        ORDER BY f.observed_date DESC, f.sales_evidence_at DESC
      ) AS metric_rank
    FROM operator_card_daily_fact_projection f
    WHERE f.sales_coverage_status<>'unavailable'
  ) ranked
  WHERE ranked.metric_rank=1
),
accepted_ungraded AS (
  SELECT ranked.*
  FROM (
    SELECT
      u.*,
      ROW_NUMBER() OVER (
        PARTITION BY u.variant_id
        ORDER BY u.observed_at DESC, u.id DESC
      ) AS evidence_rank
    FROM market_ungraded_reference_price u
    INNER JOIN catalog_source_identity si
      ON si.variant_id=u.variant_id
     AND si.source_code=u.source_code
     AND si.external_entity_id=u.external_entity_id
     AND si.match_status='exact'
    INNER JOIN operator_binding_freeze sf
      ON sf.variant_id=u.variant_id
     AND sf.freeze_kind='source'
     AND sf.source_code=u.source_code
     AND sf.external_entity_id=u.external_entity_id
     AND sf.acceptance_status='accepted'
    WHERE u.price_usd > 0
  ) ranked
  WHERE ranked.evidence_rank=1
),
accepted_image AS (
  SELECT ranked.*
  FROM (
    SELECT
      a.variant_id,
      a.id AS image_asset_id,
      a.content_sha256 AS image_content_sha256,
      a.private_object_key AS image_object_key,
      a.mime_type AS image_mime_type,
      a.width_px AS image_width_px,
      a.height_px AS image_height_px,
      a.captured_at AS image_captured_at,
      q.checked_at AS image_qc_at,
      p.source_path AS image_source_path,
      p.source_version_sha256 AS image_source_version_sha256,
      p.observed_at AS image_source_observed_at,
      MAX(CASE WHEN p.source_path LIKE 'snkrdunk:%' THEN 1 ELSE 0 END) OVER (
        PARTITION BY a.variant_id
      ) AS snk_image_available,
      ROW_NUMBER() OVER (
        PARTITION BY a.variant_id
        ORDER BY
          CASE WHEN p.source_path LIKE 'snkrdunk:%' THEN 0 ELSE 1 END,
          q.checked_at DESC,
          a.captured_at DESC,
          a.id DESC
      ) AS image_rank
    FROM market_image_asset a
    INNER JOIN market_image_qc q
      ON q.image_asset_id=a.id
     AND q.public_allowed=1
     AND q.raw_front_confirmed=1
     AND q.semantic_match_status='human_or_vision_confirmed'
    INNER JOIN operator_binding_freeze imgf
      ON imgf.variant_id=a.variant_id
     AND imgf.freeze_kind='image'
     AND imgf.acceptance_status='accepted'
     AND imgf.content_sha256=a.content_sha256
    INNER JOIN market_image_source_pointer p
      ON p.variant_id=a.variant_id
     AND p.image_kind=a.image_kind
     AND p.source_version_sha256=a.source_version_sha256
     AND p.public_allowed=1
    WHERE a.image_kind='raw_front'
  ) ranked
  WHERE ranked.image_rank=1
),
current_universe AS (
  SELECT id
  FROM market_universe_lock
  WHERE is_current=1
  ORDER BY effective_at DESC, id DESC
  LIMIT 1
),
active_members AS (
  SELECT
    m.universe_lock_id,
    m.variant_id,
    m.segment_code,
    m.member_role,
    m.market_rank
  FROM market_universe_member m
  INNER JOIN current_universe u ON u.id=m.universe_lock_id
)
SELECT
  v.id AS variant_id,
  v.opaque_id,
  CASE WHEN am.variant_id IS NULL THEN 0 ELSE 1 END AS active_member,
  am.universe_lock_id AS active_universe_lock_id,
  am.segment_code AS active_segment_code,
  am.member_role AS active_member_role,
  am.market_rank AS active_market_rank,
  p.tcg_code,
  p.card_language,
  p.collector_number,
  p.set_name AS canonical_set_name,
  p.edition_code,
  p.set_code,
  p.printing_code,
  p.rarity_code,
  p.parallel_code,
  p.finish_code,
  p.identity_status,
  p.canonical_printing_sha256,
  p.evidence_sha256 AS printing_evidence_sha256,
  ia.accepted_at AS identity_accepted_at,
  JSON_OBJECT(
    'en', l.name_en,
    'zhTW', l.name_zhTW,
    'zhCN', l.name_zhCN,
    'ja', l.name_ja,
    'ko', l.name_ko
  ) AS localized_names_json,
  JSON_OBJECT(
    'en', l.set_en,
    'zhTW', l.set_zhTW,
    'zhCN', l.set_zhCN,
    'ja', l.set_ja,
    'ko', l.set_ko
  ) AS localized_set_names_json,
  JSON_OBJECT(
    'en', l.story_en,
    'zhTW', l.story_zhTW,
    'zhCN', l.story_zhCN,
    'ja', l.story_ja,
    'ko', l.story_ko
  ) AS localized_stories_json,
  l.locale_latest_observed_at,
  COALESCE(l.locale_evidence_complete, 0) AS locale_evidence_complete,
  COALESCE(b.exact_binding_count, 0) AS exact_binding_count,
  b.exact_source_codes,
  COALESCE(b.exact_binding_evidence_complete, 0) AS exact_binding_evidence_complete,
  price.price_usd AS psa10_price_usd,
  price.price_source_code AS psa10_price_source_code,
  price.price_source_external_entity_id AS psa10_price_source_external_entity_id,
  price.price_source_observation_id AS psa10_price_source_observation_id,
  price.price_payload_sha256 AS psa10_price_payload_sha256,
  price.price_effective_at AS psa10_price_effective_at,
  price.price_source_observed_at AS psa10_price_source_observed_at,
  pop.psa10_population,
  pop.population_source_code,
  pop.population_source_external_entity_id,
  pop.population_payload_sha256,
  pop.population_effective_at,
  CASE
    WHEN price.price_usd IS NULL OR pop.psa10_population IS NULL THEN NULL
    ELSE price.price_usd * pop.psa10_population
  END AS market_cap_usd,
  sales.sales_count AS latest_daily_sales_count,
  sales.sales_value_usd AS latest_daily_sales_value_usd,
  COALESCE(sales.sales_coverage_status, 'unavailable') AS latest_daily_sales_coverage_status,
  sales.sales_evidence_at AS latest_daily_sales_evidence_at,
  rawp.price_usd AS ungraded_reference_price_usd,
  rawp.source_code AS ungraded_reference_source_code,
  rawp.external_entity_id AS ungraded_reference_external_entity_id,
  rawp.source_payload_sha256 AS ungraded_reference_payload_sha256,
  rawp.observed_at AS ungraded_reference_observed_at,
  img.image_asset_id,
  img.image_content_sha256,
  img.image_object_key,
  img.image_mime_type,
  img.image_width_px,
  img.image_height_px,
  img.image_captured_at,
  img.image_qc_at,
  img.image_source_path,
  img.image_source_version_sha256,
  img.image_source_observed_at,
  COALESCE(img.snk_image_available, 0) AS snk_image_available,
  JSON_OBJECT(
    'en', CONCAT_WS(' · ', l.name_en, p.collector_number, p.edition_code, p.set_code, p.finish_code),
    'zhTW', CONCAT_WS(' · ', l.name_zhTW, p.collector_number, p.edition_code, p.set_code, p.finish_code),
    'zhCN', CONCAT_WS(' · ', l.name_zhCN, p.collector_number, p.edition_code, p.set_code, p.finish_code),
    'ja', CONCAT_WS(' · ', l.name_ja, p.collector_number, p.edition_code, p.set_code, p.finish_code),
    'ko', CONCAT_WS(' · ', l.name_ko, p.collector_number, p.edition_code, p.set_code, p.finish_code)
  ) AS image_alt_json,
  CASE
    WHEN p.variant_id IS NOT NULL
     AND p.identity_status IN ('confirmed', 'canonical')
     AND p.tcg_code<>''
     AND p.card_language IN ('en', 'zhTW', 'zhCN', 'ja', 'ko')
     AND p.collector_number<>''
     AND p.edition_code<>''
     AND p.set_code<>''
     AND p.finish_code<>''
     AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
     AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
     AND ia.variant_id IS NOT NULL
     AND COALESCE(b.exact_binding_count, 0) > 0
    THEN 1 ELSE 0
  END AS identity_complete,
  CASE
    WHEN price.price_usd IS NOT NULL AND pop.psa10_population IS NOT NULL
    THEN 1 ELSE 0
  END AS metric_complete,
  CASE WHEN img.image_asset_id IS NOT NULL THEN 1 ELSE 0 END AS image_complete,
  CASE
    WHEN l.name_en IS NOT NULL AND l.name_zhTW IS NOT NULL AND l.name_zhCN IS NOT NULL
     AND l.name_ja IS NOT NULL AND l.name_ko IS NOT NULL
     AND l.set_en IS NOT NULL AND l.set_zhTW IS NOT NULL AND l.set_zhCN IS NOT NULL
     AND l.set_ja IS NOT NULL AND l.set_ko IS NOT NULL
     AND l.story_en IS NOT NULL AND l.story_zhTW IS NOT NULL AND l.story_zhCN IS NOT NULL
     AND l.story_ja IS NOT NULL AND l.story_ko IS NOT NULL
     AND COALESCE(l.locale_evidence_complete, 0)=1
    THEN 1 ELSE 0
  END AS locale_complete,
  CASE
    WHEN p.variant_id IS NOT NULL
     AND p.identity_status IN ('confirmed', 'canonical')
     AND p.tcg_code<>''
     AND p.card_language IN ('en', 'zhTW', 'zhCN', 'ja', 'ko')
     AND p.collector_number<>''
     AND p.edition_code<>''
     AND p.set_code<>''
     AND p.finish_code<>''
     AND p.canonical_printing_sha256 REGEXP '^[0-9a-f]{64}$'
     AND p.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
     AND ia.variant_id IS NOT NULL
     AND COALESCE(b.exact_binding_count, 0) > 0
     AND COALESCE(b.exact_binding_evidence_complete, 0)=1
     AND price.price_usd IS NOT NULL
     AND pop.psa10_population IS NOT NULL
     AND img.image_asset_id IS NOT NULL
     AND l.name_en IS NOT NULL AND l.name_zhTW IS NOT NULL AND l.name_zhCN IS NOT NULL
     AND l.name_ja IS NOT NULL AND l.name_ko IS NOT NULL
     AND l.set_en IS NOT NULL AND l.set_zhTW IS NOT NULL AND l.set_zhCN IS NOT NULL
     AND l.set_ja IS NOT NULL AND l.set_ko IS NOT NULL
     AND l.story_en IS NOT NULL AND l.story_zhTW IS NOT NULL AND l.story_zhCN IS NOT NULL
     AND l.story_ja IS NOT NULL AND l.story_ko IS NOT NULL
     AND COALESCE(l.locale_evidence_complete, 0)=1
    THEN 1 ELSE 0
  END AS product_ready
FROM catalog_variant v
LEFT JOIN active_members am ON am.variant_id=v.id
LEFT JOIN catalog_printing_identity p ON p.variant_id=v.id
LEFT JOIN locale_rollup l ON l.variant_id=v.id
LEFT JOIN accepted_bindings b ON b.variant_id=v.id
LEFT JOIN identity_acceptance ia ON ia.variant_id=v.id
LEFT JOIN latest_price price ON price.variant_id=v.id
LEFT JOIN latest_population pop ON pop.variant_id=v.id
LEFT JOIN latest_sales sales ON sales.variant_id=v.id
LEFT JOIN accepted_ungraded rawp ON rawp.variant_id=v.id
LEFT JOIN accepted_image img ON img.variant_id=v.id;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('023');
