-- CARDZ 047: non-destructive recovery after 046 deleted duplicate legacy quotes.
-- 046 already applied; DO NOT ALTER 046.
--
-- Facts:
-- - 046 re-synced the 045 map then DELETE'd legacy quote rows not referenced by the map.
-- - That violated append-only immutable history.
-- - Backend credentials lack REPLICATION CLIENT, so binlog row-image restore is unavailable.
--
-- 047 policy:
-- 1) Never DELETE quote revisions.
-- 2) Re-freeze historical prices from canonical acceptance lineage (cap/pop), append-only.
-- 3) Keep/refresh 045 1:1 resolution map to the freeze that matches acceptance math.
-- 4) Leave 046's uniqueness column in place (one active freeze owner per acceptance is OK);
--    recovery is semantic (price/period/check) not re-minting deleted surrogate ids.

-- Ensure 045 map table exists (no-op if present).
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
    KEY ix_legacy_resolution_checked (checked_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Refresh map from any remaining legacy freezes (append-safe; no deletes).
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
) latest ON latest.quote_revision_id=q.id
ON DUPLICATE KEY UPDATE
  quote_revision_id=VALUES(quote_revision_id),
  price_usd=VALUES(price_usd),
  source_period_at=VALUES(source_period_at),
  checked_at=VALUES(checked_at),
  quote_lineage_sha256=VALUES(quote_lineage_sha256),
  resolved_at=VALUES(resolved_at);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('047');
