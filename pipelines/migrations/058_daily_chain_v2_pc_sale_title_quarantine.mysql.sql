-- CARDZ 058: the PC title<->collector-number sale quarantine becomes a DB fact.
--
-- Incident shape 29 (2026-07-14, v1326 Latias +556%): PriceCharting's own
-- fuzzy match drops another card's sold comps onto an exact product page, the
-- $91 sale is accepted, and it becomes the 30d window anchor.  The quarantine
-- ledger that fixes it has lived only in a receipt file
-- (data/runtime/operator/audit/pc_sale_title_quarantine_current.json), so it is
-- applied by exactly one reader per hand-written call site:
-- psa10_latest_sale_quote.plan() drops the sales when it mints the quote, and
-- live-db-snapshot.ts subtracted them from the day aggregate afterwards.
-- Everything else that reads the sales history -- operator_fe_export's daily
-- projection, operator_card_daily_fact_projection and the fact_content /
-- fact_lineage hashes built from it -- still counted the poisoned rows.
--
-- One concept, one enforcement point: the view that decides which accepted
-- PSA10 transactions exist at all now excludes them, so every reader inherits
-- the exclusion without knowing the receipt exists.
--
-- The discriminator is NOT reimplemented here.  The receipt is still derived by
-- pipelines/pc_sale_title_quarantine.py from
-- c11_pc_sold_ingest.title_collector_contradiction; this table is only its
-- materialisation, written by collect_control._sync_pc_sale_title_quarantine
-- under the same _db_writer_lease as the sale-quote mint, and
-- test_price_lane_contracts' DB gate keeps the receipt itself from going stale.
--
-- No foreign key to market_sale_observation on purpose: a quarantine row is
-- evidence about an id, and it must survive (and keep excluding) even if a
-- retention pass ever removes the landing row.  A dangling row excludes
-- nothing, which is the harmless direction.

CREATE TABLE IF NOT EXISTS market_pc_sale_title_quarantine (
    sale_observation_id BIGINT UNSIGNED NOT NULL,
    variant_id BIGINT UNSIGNED NOT NULL,
    reason VARCHAR(64) NOT NULL,
    receipt_sha256 CHAR(64) NOT NULL,
    written_at DATETIME(6) NOT NULL,
    PRIMARY KEY (sale_observation_id),
    KEY ix_pc_sale_title_quarantine_variant (variant_id),
    KEY ix_pc_sale_title_quarantine_receipt (receipt_sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Byte-identical to the 026 definition except for the closing NOT EXISTS:
-- same column list, same order, same aliases, same predicates.  Every existing
-- reader (operator_accepted_psa10_sales_history, and through it
-- operator_card_daily_fact_dates / operator_card_daily_fact_projection,
-- operator_fe_export and live-db-snapshot.ts) keeps compiling unchanged; the
-- only difference is that quarantined transactions stop existing for them.
-- Until the table has rows the view behaves exactly as it does today, so
-- applying this file on its own changes nothing.
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
  AND h.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM market_pc_sale_title_quarantine tq
    WHERE tq.sale_observation_id=s.id
  );

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('058');
