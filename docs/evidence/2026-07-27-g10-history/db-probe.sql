-- [1] market_grader_population_observation 全表覆蓋
SELECT COUNT(*) AS rows_total, COUNT(DISTINCT variant_id) AS variants,
       MIN(observed_date) AS min_d, MAX(observed_date) AS max_d,
       COUNT(DISTINCT observed_date) AS date_points
FROM market_grader_population_observation
;--END
-- [2] market_grader_population_observation 逐日
SELECT observed_date, COUNT(*) AS rows_n, COUNT(DISTINCT variant_id) AS variants,
       COUNT(DISTINCT grader_code) AS graders, COUNT(DISTINCT source_code) AS sources
FROM market_grader_population_observation GROUP BY observed_date ORDER BY observed_date
;--END
-- [3] market_population_transport_observation 全表覆蓋
SELECT COUNT(*) AS rows_total, COUNT(DISTINCT variant_id) AS variants,
       MIN(effective_date) AS min_d, MAX(effective_date) AS max_d,
       COUNT(DISTINCT effective_date) AS date_points
FROM market_population_transport_observation
;--END
-- [4] market_population_transport_observation 逐日
SELECT effective_date, COUNT(*) AS rows_n, COUNT(DISTINCT variant_id) AS variants,
       COUNT(DISTINCT transport_code) AS transports
FROM market_population_transport_observation GROUP BY effective_date ORDER BY effective_date
;--END
-- [5] 原始觀測表逐 kind（對照組：價格/K線史已入庫 1134/1102 日，POP 史得 5-6 日）
SELECT observation_kind, COUNT(*) AS n, MIN(observed_date) AS min_d,
       MAX(observed_date) AS max_d, COUNT(DISTINCT observed_date) AS dates
FROM market_source_observation GROUP BY observation_kind ORDER BY n DESC
;--END
-- [6] roster 同 gemrate identity 覆蓋
SELECT COUNT(DISTINCT p.variant_id) AS pop_variants,
       COUNT(DISTINCT csi.variant_id) AS pop_variants_with_gemrate_id
FROM market_grader_population_observation p
LEFT JOIN catalog_source_identity csi
  ON csi.variant_id=p.variant_id AND csi.source_code='gemrate'
