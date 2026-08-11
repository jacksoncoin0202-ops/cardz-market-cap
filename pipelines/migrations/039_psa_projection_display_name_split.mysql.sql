-- Take the string claim out of the projection view and give it to the validator.
--
-- 035 defined operator_psa_identity_projection with
--   AND BINARY v.canonical_name=BINARY a.psa_description
-- inside the join. That single condition is why the truncated card names kept
-- coming back after every repair. catalog_variant.canonical_name is what the
-- site prints; catalog_psa_identity_acceptance.psa_description is the PSA label
-- verbatim; and the PSA label ends at the bare numerator ("... Special Art Rare
-- 110") while the number we hold is "110/80". Forcing the two to be byte-equal
-- meant the display could never carry the full number: any run that completed it
-- dropped the row out of this view, which made new_era_db_tidy's
-- `accepted != active` check raise and validator034's activeAccepted invariant go
-- red -- so the completion was always reverted as "the repair".
--
-- Measured 2026-08-11: 436 of 1605 bound variants, and 71 of the published top
-- 100, were sitting on a name truncated by exactly this loop.
--
-- The claim is not dropped, it moves. scripts/validate_psa_identity_repair.py now
-- asserts BOTH halves per row, byte-exact, re-derived from the raw payload on
-- disk:
--   literal                                    == a.psa_description
--   complete_collector_tail(literal, collector) == v.canonical_name
-- That is strictly more than this view could ever check, because the completion
-- needs leading-zero folding (`079` has to recognise `79/73`) and SQL cannot
-- express it. An invented name, a GemRate rollup name, and a hand-edit all still
-- fail there. What stays in the view is what SQL can honestly enforce: the row
-- points at a confirmed variant and a confirmed printing whose hash matches.
--
-- Everything else about the view -- the printing join, the language equality, the
-- hex-shape guards, the supersede filter -- is reproduced unchanged from 035.
-- CREATE OR REPLACE VIEW is idempotent, so this file replays cleanly.

CREATE OR REPLACE VIEW operator_psa_identity_projection AS
SELECT a.*
FROM catalog_psa_identity_acceptance a
INNER JOIN catalog_variant v
  ON v.id=a.variant_id
 AND v.card_language=a.psa_language
 AND v.identity_status='confirmed'
INNER JOIN catalog_printing_identity p
  ON p.variant_id=a.variant_id
 AND p.canonical_printing_sha256=a.canonical_printing_sha256
 AND p.card_language=a.psa_language
 AND p.identity_status='confirmed'
WHERE a.psa_description<>''
  AND a.raw_payload_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.psa_row_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.evidence_sha256 REGEXP '^[0-9a-f]{64}$'
  AND a.lineage_sha256 REGEXP '^[0-9a-f]{64}$'
  AND NOT EXISTS (
    SELECT 1 FROM catalog_psa_identity_acceptance newer
    WHERE newer.supersedes_acceptance_id=a.id
  );
