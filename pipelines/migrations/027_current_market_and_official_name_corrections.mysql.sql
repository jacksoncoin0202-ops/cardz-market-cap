-- Replay-safe correction layered on the already-ledgered 026 migration.
--
-- 026 incorrectly treated provider fetch time as the primary current-price
-- ordering key. A backfilled old market day could therefore outrank the real
-- latest day and distort market cap / Top 300. This migration keeps the
-- provider-family route, then orders by market date and evidence time.
-- It also makes an accepted PSA/GemRate public name conditional on the exact
-- provider identity still being current.

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
INNER JOIN catalog_source_identity si
  ON si.variant_id=a.variant_id
 AND si.source_code=LOWER(a.source_code)
 AND si.external_entity_id=a.external_entity_id
 AND LOWER(si.match_status)='exact'
 AND si.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
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
            preferred.observed_date>ph.observed_date
            OR (
              preferred.observed_date=ph.observed_date
              AND (
                preferred.price_effective_at>ph.price_effective_at
                OR (
                  preferred.price_effective_at=ph.price_effective_at
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

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('027');
