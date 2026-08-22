-- CARDZ 051: Daily Chain V2 generic sources, deterministic quote policy,
-- per-variant source state, and post-live publication outbox.
--
-- Additive only.  Legacy fixed source fields remain available during the
-- cutover and are not deleted or rewritten by this migration.

CREATE TABLE IF NOT EXISTS market_source_registry (
    source_code VARCHAR(64) NOT NULL,
    canonical_source_code VARCHAR(64) NOT NULL,
    identity_source_code VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    capabilities_json JSON NOT NULL,
    transport VARCHAR(64) NOT NULL,
    concurrency_group VARCHAR(128) NOT NULL,
    max_concurrency INT UNSIGNED NOT NULL,
    cadence VARCHAR(32) NOT NULL,
    freshness_sla_minutes INT UNSIGNED NOT NULL,
    required_class VARCHAR(16) NOT NULL,
    enabled TINYINT(1) NOT NULL DEFAULT 1,
    config_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (source_code),
    KEY ix_source_registry_enabled (enabled, required_class),
    KEY ix_source_registry_canonical (canonical_source_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_quote_route_policy (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    policy_version VARCHAR(64) NOT NULL,
    language_code VARCHAR(32) NOT NULL,
    source_code VARCHAR(64) NOT NULL,
    priority INT UNSIGNED NOT NULL,
    is_eligible TINYINT(1) NOT NULL DEFAULT 1,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    activated_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_quote_route_policy
        (policy_version, language_code, source_code),
    KEY ix_quote_route_active
        (source_code, language_code, is_active, activated_at, priority),
    CONSTRAINT fk_quote_route_source
        FOREIGN KEY (source_code) REFERENCES market_source_registry(source_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS market_variant_source_state (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    business_date DATE NOT NULL,
    run_id VARCHAR(96) NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    source_code VARCHAR(64) NOT NULL,
    capability VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    observed_at DATETIME(6) NULL,
    checked_at DATETIME(6) NOT NULL,
    native_currency CHAR(3) NULL,
    native_value DECIMAL(20,6) NULL,
    usd_value DECIMAL(20,6) NULL,
    payload_sha256 CHAR(64) NULL,
    evidence_ref VARCHAR(1024) NULL,
    error_code VARCHAR(128) NULL,
    selected_quote_revision_id BIGINT UNSIGNED NULL,
    selected_policy_version VARCHAR(64) NULL,
    is_selected TINYINT(1) NOT NULL DEFAULT 0,
    source_switched TINYINT(1) NOT NULL DEFAULT 0,
    detail_json JSON NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_variant_source_state
        (business_date, variant_id, source_code, capability),
    KEY ix_variant_source_selected
        (business_date, variant_id, is_selected),
    KEY ix_variant_source_run (run_id, source_code, status),
    CONSTRAINT fk_variant_source_variant
        FOREIGN KEY (variant_id) REFERENCES catalog_variant(id),
    CONSTRAINT fk_variant_source_registry
        FOREIGN KEY (source_code) REFERENCES market_source_registry(source_code),
    CONSTRAINT fk_variant_source_quote_revision
        FOREIGN KEY (selected_quote_revision_id) REFERENCES market_current_quote_revision(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS publication_outbox (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_key VARCHAR(160) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    business_date DATE NOT NULL,
    run_id VARCHAR(96) NOT NULL,
    generation_id VARCHAR(128) NOT NULL,
    generated_at DATETIME(6) NOT NULL,
    active_count INT UNSIGNED NOT NULL,
    degraded TINYINT(1) NOT NULL DEFAULT 0,
    source_health_json JSON NOT NULL,
    live_url VARCHAR(512) NOT NULL,
    payload_json JSON NOT NULL,
    content_sha256 CHAR(64) NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_publication_outbox_event (event_key),
    UNIQUE KEY uq_publication_outbox_business_type (business_date, event_type),
    KEY ix_publication_outbox_run (run_id, event_type)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS publication_delivery (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_id BIGINT UNSIGNED NOT NULL,
    consumer_code VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    attempt_count INT UNSIGNED NOT NULL DEFAULT 0,
    next_retry_at DATETIME(6) NULL,
    delivered_at DATETIME(6) NULL,
    receipt_json JSON NULL,
    error_code VARCHAR(128) NULL,
    error_text TEXT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (id),
    UNIQUE KEY uq_publication_delivery_consumer (event_id, consumer_code),
    KEY ix_publication_delivery_pending (consumer_code, status, next_retry_at),
    CONSTRAINT fk_publication_delivery_event
        FOREIGN KEY (event_id) REFERENCES publication_outbox(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO market_source_registry
  (source_code,canonical_source_code,identity_source_code,display_name,
   capabilities_json,transport,concurrency_group,max_concurrency,cadence,
   freshness_sla_minutes,required_class,enabled,config_json)
VALUES
  ('gemrate','gemrate','gemrate','GemRate',JSON_ARRAY('pop','identity'),
   'http+headed-browser','host:gemrate',4,'daily',405,'core',1,JSON_OBJECT()),
  ('snkrdunk','snkrdunk','snkrdunk','SNKRDUNK',JSON_ARRAY('quote','price','trades','image','identity'),
   'http','host:snkrdunk',1,'daily',405,'quote',1,JSON_OBJECT()),
  ('snk_psa10','snkrdunk','snkrdunk','SNKRDUNK PSA10',JSON_ARRAY('quote','price'),
   'http','host:snkrdunk',1,'daily',405,'quote',1,JSON_OBJECT('alias',TRUE)),
  ('snk','snkrdunk','snkrdunk','SNKRDUNK legacy storage',JSON_ARRAY('quote','price'),
   'http','host:snkrdunk',1,'daily',405,'quote',1,JSON_OBJECT('alias',TRUE)),
  ('pricecharting','pricecharting','pricecharting','PriceCharting',JSON_ARRAY('quote','price','sales','identity'),
   'cdp:9333','cdp:9333',1,'daily',405,'quote',1,JSON_OBJECT('cdpPort',9333)),
  ('fx','fx','fx','Frankfurter FX',JSON_ARRAY('rates'),
   'http','host:fx',1,'daily',405,'core',1,JSON_OBJECT())
ON DUPLICATE KEY UPDATE
  canonical_source_code=VALUES(canonical_source_code),
  identity_source_code=VALUES(identity_source_code),
  display_name=VALUES(display_name),
  capabilities_json=VALUES(capabilities_json),
  transport=VALUES(transport),
  concurrency_group=VALUES(concurrency_group),
  max_concurrency=VALUES(max_concurrency),
  cadence=VALUES(cadence),
  freshness_sla_minutes=VALUES(freshness_sla_minutes),
  required_class=VALUES(required_class),
  enabled=VALUES(enabled),
  config_json=VALUES(config_json);

INSERT INTO market_quote_route_policy
  (policy_version,language_code,source_code,priority,is_eligible,is_active,activated_at)
VALUES
  ('cardz-route-v1','*','snkrdunk',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','*','snk_psa10',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','*','snk',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','*','pricecharting',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','en','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','en','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','en','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','en','snk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh','snk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhtw','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhtw','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhtw','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhtw','snk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-tw','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-tw','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-tw','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-tw','snk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhcn','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhcn','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhcn','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zhcn','snk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-cn','pricecharting',10,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-cn','snkrdunk',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-cn','snk_psa10',20,1,1,'2026-08-20 00:00:00.000000'),
  ('cardz-route-v1','zh-cn','snk',20,1,1,'2026-08-20 00:00:00.000000')
ON DUPLICATE KEY UPDATE
  priority=VALUES(priority),is_eligible=VALUES(is_eligible),
  is_active=VALUES(is_active),activated_at=VALUES(activated_at);

-- Registry-driven eligibility.  An exact language row wins over '*'; within
-- the selected language scope, priority remains deterministic and versioned.
CREATE OR REPLACE VIEW operator_eligible_current_quote_revision AS
SELECT
  h.id AS price_history_acceptance_id,
  q.id AS quote_revision_id,
  q.variant_id,
  q.source_period_at AS observed_date,
  q.price_usd,
  q.checked_at,
  q.source_period_at,
  sr.canonical_source_code AS price_source_code,
  q.source_code AS price_storage_source_code,
  q.source_external_entity_id AS price_source_external_entity_id,
  q.source_observation_id AS price_source_observation_id,
  q.payload_sha256 AS price_payload_sha256,
  q.checked_at AS price_effective_at,
  q.checked_at AS price_source_observed_at,
  h.lineage_sha256 AS price_lineage_sha256,
  q.quote_lineage_sha256,
  q.reconstruction_kind,
  rp.priority AS price_route_priority,
  rp.policy_version AS price_policy_version
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
INNER JOIN market_source_registry sr
  ON sr.source_code=q.source_code AND sr.enabled=1
 AND JSON_CONTAINS(sr.capabilities_json,JSON_QUOTE('quote'),'$')=1
INNER JOIN operator_strict_source_identity si
  ON si.variant_id=q.variant_id
 AND si.source_code=sr.identity_source_code
 AND si.external_entity_id=q.source_external_entity_id
INNER JOIN market_quote_route_policy rp
  ON rp.id=(
    SELECT chosen.id
    FROM market_quote_route_policy chosen
    WHERE chosen.source_code=q.source_code
      AND chosen.is_active=1
      AND chosen.is_eligible=1
      AND chosen.language_code IN (
        LOWER(REPLACE(pi.card_language,'_','-')),'*'
      )
    ORDER BY
      CASE WHEN chosen.language_code=LOWER(REPLACE(pi.card_language,'_','-'))
           THEN 0 ELSE 1 END,
      chosen.activated_at DESC,
      chosen.policy_version DESC,
      chosen.priority ASC,
      chosen.id DESC
    LIMIT 1
  )
WHERE h.metric_kind='psa10_price'
  AND h.source_code=si.source_code
  AND q.price_usd>0
  AND q.payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.identity_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.acceptance_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND (q.reconstruction_kind IS NULL
       OR q.reconstruction_kind IN ('bootstrap_from_observation',''));

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('051');

