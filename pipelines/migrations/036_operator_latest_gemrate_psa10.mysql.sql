-- CARDZ 036: the ONLY POP authority. Exactly one row per gemrate_id;
-- id DESC breaks effective_at ties, so the 035-era MAX() self-join multi-row
-- problem and Python fetch-order dependence cannot recur.
CREATE OR REPLACE VIEW operator_latest_gemrate_psa10 AS
SELECT gemrate_id, variant_id, psa10_population, effective_at,
       observed_date, raw_payload_sha256, generation_id
FROM (
  SELECT o.*,
         ROW_NUMBER() OVER (PARTITION BY o.gemrate_id
                            ORDER BY o.effective_at DESC, o.id DESC) rn
  FROM market_gemrate_psa10_observation_v2 o
) r
WHERE r.rn = 1;
