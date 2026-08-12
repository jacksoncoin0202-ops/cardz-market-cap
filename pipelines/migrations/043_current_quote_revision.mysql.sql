-- CARDZ 043: append-only current quote revisions for daily ranking.
--
-- market_price_observation stays the history table (PC monthly series, SNK
-- daily bars). Its UNIQUE (variant_id, source_code, observed_date) means a
-- PriceCharting month-start point is rewritten every daily capture, so any
-- generation that pointed at that row silently changed price after the fact.
--
-- market_current_quote_revision is the ranking evidence layer:
--   price_usd          current quote captured this run
--   source_period_at   source chart/period date (e.g. 2026-08-01 for PC month)
--   checked_at         actual CDP/HTTP capture time (freshness clock)
-- Each capture is a new row. Idempotent only when the full lineage digest
-- matches; never ON DUPLICATE KEY UPDATE of price_usd.
--
-- Generation stays 036. No FE04 / product generation bump.

CREATE TABLE IF NOT EXISTS market_current_quote_revision (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(32) NOT NULL,
    source_external_entity_id VARCHAR(191) NOT NULL,
    price_usd DECIMAL(18,6) NOT NULL,
    source_period_at DATE NOT NULL,
    checked_at DATETIME(6) NOT NULL,
    source_observation_id BIGINT UNSIGNED NULL,
    market_price_observation_id BIGINT UNSIGNED NULL,
    payload_sha256 CHAR(64) NOT NULL,
    quote_lineage_sha256 CHAR(64) NOT NULL,
    reconstruction_kind VARCHAR(64) NULL,
    reconstructed_from_acceptance_id BIGINT UNSIGNED NULL,
    run_id BIGINT UNSIGNED NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_quote_revision_lineage (quote_lineage_sha256),
    KEY ix_quote_revision_variant_checked (variant_id, checked_at, id),
    KEY ix_quote_revision_source_entity (source_code, source_external_entity_id, checked_at),
    KEY ix_quote_revision_observation (market_price_observation_id),
    KEY ix_quote_revision_reconstruction (reconstructed_from_acceptance_id),
    CONSTRAINT fk_quote_revision_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Full catalog identity discovery ledger: every catalog record must have an
-- explicit status. Known gaps may not sit outside the cursor forever.
CREATE TABLE IF NOT EXISTS market_identity_discovery_ledger (
    variant_id BIGINT UNSIGNED NOT NULL,
    catalog_status VARCHAR(32) NOT NULL,
    discovery_status VARCHAR(64) NOT NULL,
    pc_status VARCHAR(64) NOT NULL,
    snk_status VARCHAR(64) NOT NULL,
    blocker_code VARCHAR(128) NULL,
    detail_json JSON NULL,
    last_reviewed_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (variant_id),
    KEY ix_discovery_status (discovery_status, catalog_status),
    KEY ix_discovery_blocker (blocker_code),
    CONSTRAINT fk_discovery_ledger_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Ranking eligibility over immutable quote revisions. Route priority preserves
-- the 032 contract: EN prefers PriceCharting, non-EN prefers SNK.
CREATE OR REPLACE VIEW operator_eligible_current_quote_revision AS
SELECT
  h.id AS price_history_acceptance_id,
  q.id AS quote_revision_id,
  q.variant_id,
  q.source_period_at AS observed_date,
  q.price_usd,
  q.checked_at,
  q.source_period_at,
  CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk'
       ELSE q.source_code END AS price_source_code,
  q.source_code AS price_storage_source_code,
  q.source_external_entity_id AS price_source_external_entity_id,
  q.source_observation_id AS price_source_observation_id,
  q.payload_sha256 AS price_payload_sha256,
  q.checked_at AS price_effective_at,
  q.checked_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  q.quote_lineage_sha256,
  q.reconstruction_kind,
  CASE WHEN pi.card_language='en' THEN
    CASE WHEN q.source_code='pricecharting' THEN 10
         WHEN q.source_code IN ('snkrdunk','snk_psa10','snk') THEN 20 ELSE 90 END
  ELSE
    CASE WHEN q.source_code IN ('snkrdunk','snk_psa10','snk') THEN 10
         WHEN q.source_code='pricecharting' THEN 20 ELSE 90 END
  END AS price_route_priority
FROM market_metric_history_acceptance h
INNER JOIN market_current_quote_revision q
  ON h.source_record_type='market_current_quote_revision'
 AND h.source_record_id=q.id
 AND h.variant_id=q.variant_id
 AND h.observed_date=q.source_period_at
 AND h.source_effective_at=q.checked_at
 AND h.external_entity_id=q.source_external_entity_id
 AND h.source_payload_sha256=q.payload_sha256
INNER JOIN catalog_printing_identity pi ON pi.variant_id=q.variant_id
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=q.variant_id
 AND si.source_code=CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' ELSE q.source_code END
 AND si.external_entity_id=q.source_external_entity_id
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND q.source_code IN ('snkrdunk','snk_psa10','snk','pricecharting')
  AND (pi.card_language='en' OR q.source_code IN ('snkrdunk','snk_psa10','snk'))
  AND q.price_usd>0
  AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND (q.reconstruction_kind IS NULL
       OR q.reconstruction_kind IN ('bootstrap_from_observation',''));

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('043');
