-- CARDZ 057: retire the chart / K-line quote routes (PHASE 2).
--
-- DO NOT MERGE THIS FILE INTO THE LIVE TREE UNTIL PHASE 1 IS PROVEN.
-- pipelines/daily_chain_v2_contract.py:27 globs
-- "0[5-9][0-9]_daily_chain_v2_*.mysql.sql" and the chain's migrate task applies
-- every match, so landing this file in the live tree is the same as applying it
-- on the very next tick.  It sits unapplied on branch dev/20260823-p3-small
-- until the go/no-go query returns 0:
--
--   SELECT COUNT(*) FROM market_variant_source_state s
--   JOIN market_current_quote_revision q ON q.id=s.selected_quote_revision_id
--   LEFT JOIN market_source_observation so ON so.id=q.source_observation_id
--   WHERE s.capability='canonical_quote' AND s.is_selected=1
--     AND s.business_date=(SELECT MAX(business_date) FROM market_variant_source_state)
--     AND (JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.field'))
--            LIKE 'VGPC.chart_data.manualonly.%'
--          OR JSON_UNQUOTE(JSON_EXTRACT(so.payload_json,'$.source'))
--            ='snk_market_data_kline');
--
-- That query separates "the sale price actually won" from "the sale price was
-- merely configured to win".  Only the first justifies this file.
--
-- Phase 1 (056) out-priced the chart lanes at 90 against the sale lanes at
-- 10/20.  This file removes their eligibility outright, which is the layer that
-- actually deletes chart data from selection: the eligibility view, the
-- business-window barrier and the repair-route query all join
-- market_quote_route_policy and all require is_eligible=1.
--
-- WHY is_eligible=0 AND NOT is_active=0, AND NEVER DELETE:
-- pipelines/daily_chain_v2_db.py:366-397 rebuilds its "already has policy" set
-- as SELECT DISTINCT source_code FROM market_quote_route_policy WHERE
-- is_active=1, and route_policy_upserts emits a fresh
-- ('*', code, priority, is_eligible=1, is_active=1, NOW()) row for every
-- enabled quote-capable adapter missing from that set.  Deactivating (or
-- deleting) the v2 rows of pricecharting / snkrdunk would therefore resurrect
-- an ELIGIBLE chart route at the freshest activated_at on the next tick --
-- silently un-retiring the lane this file exists to retire.  Keeping
-- is_active=1 with is_eligible=0 keeps them in the "already has policy" set
-- forever.
--
-- snk / snk_psa10 are alias storage codes of snkrdunk and are not adapters, so
-- they are not at risk of re-seeding, but they are flipped for the same reason:
-- one flag, one meaning, no chart storage code left eligible.
--
-- Additive in spirit and idempotent: replaying it is a no-op.

UPDATE market_quote_route_policy
SET is_eligible=0
WHERE policy_version='cardz-route-v2'
  AND source_code IN ('pricecharting','snkrdunk','snk','snk_psa10');

-- cardz-route-v1 is retired wholesale rather than relying on
-- 'cardz-route-v2' > 'cardz-route-v1' winning the policy_version DESC
-- tiebreak: that ordering is a lexicographic accident which a future
-- 'cardz-route-v10' would reverse.  The v2 rows cover '*', 'en' and every zh
-- spelling; ja / ko resolve through '*' exactly as they do today.
UPDATE market_quote_route_policy
SET is_active=0
WHERE policy_version='cardz-route-v1';

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('057');
