-- CARDZ 062: a sale is counted only when the price lane would trust its identity.
--
-- Price audit 2026-09-26: operator_eligible_accepted_psa10_sales_rows (026,
-- redefined by 058) admits any sale whose binding is merely
-- match_status='exact'.  The current quote never does that: 043/050/051/053
-- all mint from operator_strict_source_identity (exact + provider-native
-- evidence + capture receipt).  The gap is exactly the dead G10 'ebay'
-- archive (collection_contract.DEAD_G10_EBAY_SOURCE_CODE; DADDY 2026-08-19:
-- the PC script is the eBay source for every daily reader): on 3308 it put
-- 2,713 rows / 70 cards / $5,115,978.75 dated 2026-04-25..2026-07-29 into
-- windows.*.trackedSales, 346 of them the same day and same price as a PC
-- row that is also counted -- one transaction, two sales.  Every one of the
-- 2,713 rows fails strict identity; every PC row passes; one SNK row
-- (variant 1204, 2025-10-17) fails too and leaves with them.
--
-- One concept, one enforcement point: the fix lives in the view that decides
-- which accepted PSA10 transactions exist, so operator_accepted_psa10_sales_
-- history, operator_card_daily_fact_* , operator_fe_export,
-- gemrate_db_completeness and live-db-snapshot.ts all inherit it unchanged.
-- No source code is named here: a binding that loses strict identity drops out
-- whatever its source, and nothing re-admits the archive by name.
--
-- The verified_zero branch of operator_accepted_psa10_sales_history is left
-- alone: it only ever emits sales_count=0, and its NOT EXISTS is keyed on any
-- psa10_sale acceptance, so a day whose only rows were archive rows vanishes
-- instead of turning into a fake verified zero.
--
-- LANDED 2026-09-26 (the ".pending" suffix dropped): the V2 migrate glob
-- applies it at the chain's next migrate stage, or run
-- db_runtime.py migrate --only <name>.

-- Byte-identical to the 058 definition except for the closing EXISTS: same
-- column list, same order, same aliases, same predicates.
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
  )
  AND EXISTS (
    SELECT 1 FROM operator_strict_source_identity osi
    WHERE osi.variant_id=si.variant_id
      AND osi.source_code=si.source_code
      AND osi.external_entity_id=si.external_entity_id
  );

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('062');
