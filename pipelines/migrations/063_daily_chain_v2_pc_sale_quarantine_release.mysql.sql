-- CARDZ 063: a proven-true PC sale can be released from the sticky quarantine,
-- by one predicate that every reader shares.
--
-- market_pc_sale_title_quarantine (058) is upsert-only and first-reason-wins,
-- so a sale the discriminator wrongly condemned stays out of every reader
-- forever, even after the verdict is shown to be wrong.  Price audit
-- 2026-09-26: 14 of the 155 title_collector_contradiction rows are the right
-- card -- each judged "match" by a blind Opus panel (2 votes) AND by Jev
-- (p(match) >= 0.86).  Examples: "CBB3 C 0307/07 GENGAR" is Gem Pack vol 3
-- card 07, not card 307; "... PSA 10 #151" / "#177" / "#230" are seller
-- numbers after the grade, not the card number.
--
-- No structural narrowing of c11_pc_sold_ingest.title_collector_contradiction
-- frees only these 14: every candidate rule (VVNN/NN gem-pack numbers, a
-- trailing "#N" after "PSA 10") also frees look-alikes nobody proved: Jev
-- judged 2401467 and 2474447 (v2236), 2533071 and 1621171 wrong_card_or_parallel.
-- So the release is per sale id, with its evidence, never by pattern.
--
-- One concept, one enforcement point:
--   * market_pc_sale_quarantine_release: INSERT-only rows (sale id, the table
--     reason it releases, evidence sha256, evidence path, run id, written_at).
--     status 'released' -> 'revoked' is the only change a row may get; a
--     revoked row re-quarantines the sale.  Never DELETE.  CHECK constraints
--     make a release for any other reason, any other status or a non-hex
--     evidence hash impossible to write.
--   * market_pc_sale_title_quarantine_effective: the quarantine table minus
--     its live releases (same reason, status 'released').  Every reader of the
--     table reads THIS view: operator_eligible_accepted_psa10_sales_rows below,
--     pc_psa10_price_derivation.load_pc_sales, live-db-snapshot.ts
--     loadDbExcludedSaleIds, and pc_sale_title_quarantine.load_stored_rows
--     (which leaves a released sale out of the receipt unless another rule
--     condemns it; scripts/test_price_lane_contracts.py goes red if one does).
--
-- The seed releases exactly the 14 ids, and only while their table row still
-- carries the reason they were proven wrong on.  evidence_sha256 is the sha256
-- of that sale's line (bytes, no newline) in the evidence file, re-checked by
-- scripts/test_migration_063_pc_sale_quarantine_release.py together with the
-- blind-panel verdicts (final_verdicts.json, same folder).
--
-- The sales view is byte-identical to 062 except that its NOT EXISTS reads the
-- effective view instead of the raw table.
--
-- Idempotent (036+ rule): CREATE TABLE IF NOT EXISTS, CREATE OR REPLACE VIEW,
-- INSERT IGNORE.  Nothing here deletes, drops, truncates or updates a row.

CREATE TABLE IF NOT EXISTS market_pc_sale_quarantine_release (
    sale_observation_id BIGINT UNSIGNED NOT NULL,
    released_reason VARCHAR(64) NOT NULL,
    evidence_sha256 CHAR(64) NOT NULL,
    evidence_path VARCHAR(255) NOT NULL,
    release_run_id VARCHAR(191) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'released',
    written_at DATETIME(6) NOT NULL,
    PRIMARY KEY (sale_observation_id),
    CONSTRAINT ck_pc_sale_quarantine_release_reason
      CHECK (released_reason IN ('title_collector_contradiction')),
    CONSTRAINT ck_pc_sale_quarantine_release_status
      CHECK (status IN ('released','revoked')),
    CONSTRAINT ck_pc_sale_quarantine_release_evidence
      CHECK (evidence_sha256 REGEXP '^[0-9a-f]{64}$')
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE OR REPLACE VIEW market_pc_sale_title_quarantine_effective AS
SELECT tq.sale_observation_id, tq.variant_id, tq.reason, tq.receipt_sha256, tq.written_at
FROM market_pc_sale_title_quarantine tq
WHERE NOT EXISTS (
  SELECT 1 FROM market_pc_sale_quarantine_release r
  WHERE r.sale_observation_id=tq.sale_observation_id
    AND r.released_reason=tq.reason
    AND r.status='released'
);

INSERT IGNORE INTO market_pc_sale_quarantine_release
  (sale_observation_id, released_reason, evidence_sha256, evidence_path, release_run_id, status, written_at)
SELECT tq.sale_observation_id, tq.reason, ev.evidence_sha256,
       'data/private/handoff_20260926/jev/candidates_released.jsonl',
       'jev_opus_blind_review_20260926', 'released', UTC_TIMESTAMP(6)
FROM market_pc_sale_title_quarantine tq
INNER JOIN (
          SELECT 1719160 AS sale_observation_id, 'b51d39db533dedc59792b236fd65e5ced80ff745e831f387e4d4159734dcefcd' AS evidence_sha256
UNION ALL SELECT 1722157, '7d0d62555899c99c9634c195fd4619a2862bc8602412a9f92857541260c28b25'
UNION ALL SELECT 1910376, 'c79ece822d0765e6a7faaf1eb156f8b0d009a708a121d819ebb6807c1e807b43'
UNION ALL SELECT 1952512, 'a5fdeb8ba29cfbf4c4bf02a4c909e2f6ae69c6bd69154fb87d14dd6039136c35'
UNION ALL SELECT 2235333, 'c401bd9bf7d40f3f74636b0551337d868e7f004ae486234ace5ebd4fb82e6031'
UNION ALL SELECT 2251613, '8cfeea065708efc50b8230bde479d4fd654dc0c53fd2d569dd13729f111d3ca7'
UNION ALL SELECT 2251622, '55b70bc95d485678352397d70f5129260074b7606306baa02191da87f5a0e6cf'
UNION ALL SELECT 2251633, '3f992c9b14489b7ef1e86e575e999a02717fad04b76c4730a32a8aae2ed103eb'
UNION ALL SELECT 2377337, '4794c2acd0b25c2f1db37ad0d15369b532180ec304206c203f594cd988b02cef'
UNION ALL SELECT 2425296, '5bf41511aaf741900e69e16bb4a2da34a8758b3d900acf0129bf7982f2185fa2'
UNION ALL SELECT 2474446, '9dc1d65e22b34fba7ae000b6ea62aee0ef85add48f9d5a8205b46865432b8859'
UNION ALL SELECT 2474448, '974f1942d650e5220fe6db9f9021b02decf2af735cad5261ebe4b43d2f541954'
UNION ALL SELECT 2474449, 'e1809357c61e8edc3de66c9c2f94b72ffaf34293f7dcdc81c6ec8e0df52e924a'
UNION ALL SELECT 2474452, '8a2cfa7c99d6cbcbd43129f8d7a70cdb48f6116754a45dcfcb896a74db48b582'
) ev ON ev.sale_observation_id=tq.sale_observation_id
WHERE tq.reason='title_collector_contradiction';

-- Byte-identical to the 062 definition except the NOT EXISTS table name.
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
    SELECT 1 FROM market_pc_sale_title_quarantine_effective tq
    WHERE tq.sale_observation_id=s.id
  )
  AND EXISTS (
    SELECT 1 FROM operator_strict_source_identity osi
    WHERE osi.variant_id=si.variant_id
      AND osi.source_code=si.source_code
      AND osi.external_entity_id=si.external_entity_id
  );

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('063');
