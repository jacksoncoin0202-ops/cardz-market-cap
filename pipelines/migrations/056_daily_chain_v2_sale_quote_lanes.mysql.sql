-- CARDZ 056: the PSA10 canonical price becomes the latest real sale.
--
-- Owner decision 2026-08-23: the displayed / accepted PSA10 price must be a
-- completed sale, not a chart series point.  EN reads PriceCharting sold comps
-- first, every other language reads SNKRDUNK trades first.
--
-- Shape: two migration-owned ALIAS storage codes, not two new adapters.
-- pipelines/daily_chain_v2_db.py:sync_source_registry rewrites
-- canonical_source_code / identity_source_code to the adapter's own code on
-- every tick (its params tuple is (source_code, source_code, source_code,
-- source_code, ...)), so an adapter-registered alias would be flattened nightly.
-- It only iterates the adapter specs it is handed, which is exactly why the
-- snk / snk_psa10 alias rows seeded by 051 still carry canonical='snkrdunk'
-- today.  The same rule keeps the two rows below stable.
--
-- HARD CONSTRAINT: canonical_source_code MUST equal identity_source_code for
-- each alias.  V2 acceptance writes sr.identity_source_code into
-- market_metric_history_acceptance.source_code (pipelines/rebuild_036.py:7396)
-- while operator_eligible_current_quote_revision requires
-- h.source_code = sr.canonical_source_code.  Any divergence silently drops
-- every alias row out of the selector.
--
-- 'quote' must appear in capabilities_json: it is load-bearing in three
-- places -- the eligibility view join, the V2 acceptance join, and the
-- business-window barrier join (pipelines/daily_chain_v2_db.py:169-171).
-- required_class stays 'quote' and never 'core': a core row would open its own
-- barrier section, which nothing can ever measure for an alias
-- (daily_chain_v2_contract.py:core_contract_keys / daily_chain_v2_db.py:234).
--
-- Phase 1 only.  The chart lanes stay registered and eligible here and are
-- merely out-priced (10/20 beats 90 because rebuild_036.py winner_key negates
-- the priority).  Retiring them is a separate, later file, gated on the
-- post-cutover query that proves chart selection already reached zero.
--
-- Additive and idempotent: every statement is an upsert, no row is deleted,
-- no existing priority is rewritten outside the new cardz-route-v2 version.
--
-- Note on market_source_policy: that table does not exist in this database
-- (information_schema probe, 2026-08-23), and no code references it.  The
-- declarative retirement record the plan sketched is therefore omitted rather
-- than written against a missing table, which would abort the migrate task
-- mid-file.  Route policy plus the mint guard already carry the retirement.

INSERT INTO market_source_registry
  (source_code,canonical_source_code,identity_source_code,display_name,
   capabilities_json,transport,concurrency_group,max_concurrency,cadence,
   freshness_sla_minutes,required_class,identity_lane,route_priority,
   enabled,config_json)
VALUES
  ('pricecharting_sales','pricecharting','pricecharting',
   'PriceCharting latest sale',JSON_ARRAY('quote','price'),
   'cdp:9333','cdp:9333',1,'daily',405,'quote','browser',20,1,
   JSON_OBJECT('alias',TRUE,'lane','sale','parentSourceCode','pricecharting')),
  ('snkrdunk_sales','snkrdunk','snkrdunk',
   'SNKRDUNK latest sale',JSON_ARRAY('quote','price'),
   'http','host:snkrdunk',1,'daily',405,'quote','http',10,1,
   JSON_OBJECT('alias',TRUE,'lane','sale','parentSourceCode','snkrdunk'))
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
  identity_lane=VALUES(identity_lane),
  route_priority=VALUES(route_priority),
  enabled=VALUES(enabled),
  config_json=VALUES(config_json);

-- cardz-route-v2.  The eligibility view picks one policy row per source with
-- ORDER BY exact-language, activated_at DESC, policy_version DESC, priority ASC
-- (pipelines/current_quote_revision.py:413), so a v2 row newer than the
-- 2026-08-20 v1 rows wins the first tiebreak without deactivating anything.
-- Every v1 language scope is mirrored here: a scope left unmirrored would keep
-- serving the v1 chart priority of 10.
--
-- zh* leads with SNKRDUNK per the owner brief, which also settles the standing
-- contradiction between the v1 rows (PriceCharting first) and
-- current_quote_revision.NON_LEAD_ROUTE_LANGUAGES (SNK-only).  ja / ko inherit
-- the '*' scope, which is SNKRDUNK-first for the same reason.
--
-- The chart codes are listed at 90 on purpose rather than omitted: they must
-- keep an is_active=1 row so daily_chain_v2_db.sync_source_registry still sees
-- them in its "already has policy" set and never re-seeds them at priority
-- DEFAULT_ROUTE_PRIORITY with is_eligible=1.
INSERT INTO market_quote_route_policy
  (policy_version,language_code,source_code,priority,is_eligible,is_active,activated_at)
VALUES
  ('cardz-route-v2','en','pricecharting_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','en','snkrdunk_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','en','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','en','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','en','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','en','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','*','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhtw','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-tw','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zhcn','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','snkrdunk_sales',10,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','pricecharting_sales',20,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','pricecharting',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','snkrdunk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','snk',90,1,1,'2026-08-22 12:00:00.000000'),
  ('cardz-route-v2','zh-cn','snk_psa10',90,1,1,'2026-08-22 12:00:00.000000')
ON DUPLICATE KEY UPDATE
  priority=VALUES(priority),is_eligible=VALUES(is_eligible),
  is_active=VALUES(is_active),activated_at=VALUES(activated_at);

-- 049 mapped storage code to identity code with a literal
-- CASE WHEN q.source_code IN ('snk','snk_psa10') THEN 'snkrdunk' END, in both
-- branches.  V2 acceptance stores sr.identity_source_code in ph.source_code, so
-- for any alias the literal is wrong and the join silently drops the row --
-- and this view is INNER JOINed by the FE bake, which then throws on
-- "ranking rows != universe members".  The mapping becomes registry-driven
-- here.  A LEFT JOIN plus COALESCE, not an INNER JOIN: a quote whose source is
-- absent from the registry must keep resolving under its own code exactly as
-- it does today, never disappear.  Every other predicate is byte-identical
-- to 049.
CREATE OR REPLACE VIEW operator_resolved_canonical_metric_quote AS
SELECT m.id AS metric_acceptance_id,
       q.id AS quote_revision_id,
       q.variant_id,
       q.source_code,
       q.source_external_entity_id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.source_observation_id,
       q.market_price_observation_id,
       q.payload_sha256,
       q.quote_lineage_sha256,
       q.reconstruction_kind,
       'direct' AS resolution_kind,
       q.quote_lineage_sha256 AS resolver_evidence_sha256
FROM market_canonical_metric_acceptance m
INNER JOIN market_metric_history_acceptance ph
  ON ph.id=m.price_history_acceptance_id
 AND ph.metric_kind='psa10_price'
 AND ph.source_record_type='market_current_quote_revision'
INNER JOIN market_current_quote_revision q
  ON q.id=ph.source_record_id
 AND q.variant_id=m.variant_id
 AND q.source_period_at=ph.observed_date
 AND q.checked_at=ph.source_effective_at
 AND q.source_external_entity_id=ph.external_entity_id
 AND q.payload_sha256=ph.source_payload_sha256
 AND q.quote_lineage_sha256 REGEXP '^[0-9a-f]{64}$'
LEFT JOIN market_source_registry sr
  ON sr.source_code=q.source_code
INNER JOIN market_metric_history_acceptance poh
  ON poh.id=m.population_history_acceptance_id
 AND poh.metric_kind='psa10_population'
INNER JOIN market_grader_population_observation pop
  ON pop.id=poh.source_record_id
 AND pop.variant_id=m.variant_id
WHERE COALESCE(sr.identity_source_code,q.source_code)=ph.source_code
  AND pop.top_grade_population>0
  AND q.price_usd=ROUND(m.market_cap_usd/pop.top_grade_population,6)
UNION ALL
SELECT m.id AS metric_acceptance_id,
       q.id AS quote_revision_id,
       q.variant_id,
       q.source_code,
       q.source_external_entity_id,
       q.price_usd,
       q.source_period_at,
       q.checked_at,
       q.source_observation_id,
       q.market_price_observation_id,
       q.payload_sha256,
       q.quote_lineage_sha256,
       q.reconstruction_kind,
       'legacy' AS resolution_kind,
       r.resolver_evidence_sha256
FROM market_canonical_metric_acceptance m
INNER JOIN market_metric_history_acceptance ph
  ON ph.id=m.price_history_acceptance_id
 AND ph.metric_kind='psa10_price'
 AND ph.source_record_type='market_price_observation'
INNER JOIN market_legacy_quote_resolution r
  ON r.metric_acceptance_id=m.id
 AND r.resolution_status='resolved'
 AND r.resolver_evidence_sha256 REGEXP '^[0-9a-f]{64}$'
INNER JOIN market_current_quote_revision q
  ON q.id=r.quote_revision_id
 AND q.variant_id=m.variant_id
 AND q.reconstruction_kind='legacy_generation_reconstructed'
 AND q.reconstructed_from_acceptance_id=m.id
 AND q.price_usd=r.price_usd
 AND q.source_period_at=r.source_period_at
 AND q.checked_at=r.checked_at
 AND q.quote_lineage_sha256=r.quote_lineage_sha256
 AND q.source_external_entity_id=ph.external_entity_id
 AND q.source_period_at=ph.observed_date
 AND q.checked_at=m.accepted_at
 AND q.payload_sha256=ph.source_payload_sha256
LEFT JOIN market_source_registry sr
  ON sr.source_code=q.source_code
INNER JOIN market_metric_history_acceptance poh
  ON poh.id=m.population_history_acceptance_id
 AND poh.metric_kind='psa10_population'
INNER JOIN market_grader_population_observation pop
  ON pop.id=poh.source_record_id
 AND pop.variant_id=m.variant_id
WHERE COALESCE(sr.identity_source_code,q.source_code)=ph.source_code
  AND pop.top_grade_population>0
  AND m.market_cap_usd>0
  AND q.price_usd=ROUND(m.market_cap_usd/pop.top_grade_population,6)
  AND r.resolver_evidence_sha256=SHA2(CONCAT_WS('|',
        'market_legacy_quote_resolution_v2',
        m.id,
        m.price_history_acceptance_id,
        m.population_history_acceptance_id,
        m.metric_lineage_sha256,
        ph.lineage_sha256,
        poh.lineage_sha256,
        CAST(q.price_usd AS CHAR),
        pop.top_grade_population,
        q.quote_lineage_sha256
      ),256);

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('056');
