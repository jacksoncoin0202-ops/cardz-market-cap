-- CARDZ 045: historical quote resolver evidence map.
-- 044 already shipped; do not alter 044. This migration adds a 1:1 map from
-- historical metric acceptance -> frozen legacy quote revision so readers do
-- not depend on MAX(id) over duplicate reconstruction rows.

CREATE TABLE IF NOT EXISTS market_legacy_quote_resolution (
    metric_acceptance_id BIGINT UNSIGNED NOT NULL,
    quote_revision_id BIGINT UNSIGNED NOT NULL,
    price_usd DECIMAL(18,6) NOT NULL,
    source_period_at DATE NOT NULL,
    checked_at DATETIME(6) NOT NULL,
    quote_lineage_sha256 CHAR(64) NOT NULL,
    resolved_at DATETIME(6) NOT NULL,
    PRIMARY KEY (metric_acceptance_id),
    UNIQUE KEY uq_legacy_resolution_quote (quote_revision_id),
    KEY ix_legacy_resolution_checked (checked_at),
    CONSTRAINT fk_legacy_resolution_metric
        FOREIGN KEY (metric_acceptance_id) REFERENCES market_canonical_metric_acceptance(id),
    CONSTRAINT fk_legacy_resolution_quote
        FOREIGN KEY (quote_revision_id) REFERENCES market_current_quote_revision(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Keep the newest legacy reconstruction per historical metric acceptance.
INSERT INTO market_legacy_quote_resolution
  (metric_acceptance_id, quote_revision_id, price_usd, source_period_at, checked_at,
   quote_lineage_sha256, resolved_at)
SELECT q.reconstructed_from_acceptance_id,
       q.id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.quote_lineage_sha256,
       UTC_TIMESTAMP(6)
FROM market_current_quote_revision q
INNER JOIN (
  SELECT reconstructed_from_acceptance_id AS metric_acceptance_id,
         MAX(id) AS quote_revision_id
  FROM market_current_quote_revision
  WHERE reconstruction_kind='legacy_generation_reconstructed'
    AND reconstructed_from_acceptance_id IS NOT NULL
  GROUP BY reconstructed_from_acceptance_id
) latest
  ON latest.quote_revision_id=q.id
ON DUPLICATE KEY UPDATE
  quote_revision_id=VALUES(quote_revision_id),
  price_usd=VALUES(price_usd),
  source_period_at=VALUES(source_period_at),
  checked_at=VALUES(checked_at),
  quote_lineage_sha256=VALUES(quote_lineage_sha256),
  resolved_at=VALUES(resolved_at);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('045');
