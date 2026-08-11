-- CARDZ 040: a rejected binding must say who rejected it.
--
-- On 2026-08-07 14:08 psa_identity_repair rejected every non-gemrate binding
-- on any variant whose gemrate identity would not resolve, and left
-- bind_evidence_json untouched. 146 rows ended up at match_status='rejected'
-- while their own evidence still read action='confirm'. Nothing had examined
-- those bindings, but nothing on the row said so, so for four days no lane
-- could tell a decided rejection from collateral -- 121 cards at PSA10
-- population >= 1000 looked permanently ruled out, including the Dodgers x
-- One Piece Night Luffy (v8) the owner kept finding missing from the board.
--
-- Every writer that produces this state has since been fixed to stamp its
-- reasoning (psa_identity_repair -> quarantine-unresolved-variant-identity,
-- resolve_active_psa_identity -> supersede-losing-gemrate-binding,
-- rebuild_036 stage_bind -> reject). This file does the two things code
-- cannot: relabel the rows the old writers left behind, and make the state
-- unrepresentable from here on.
--
-- Statements run in file order, so the backfill has to precede the constraint
-- -- ADD CONSTRAINT validates existing rows and would abort on all 81 of them.
-- Both halves are idempotent because the runner replays a half-applied file.

-- JSON_SET, not JSON_OBJECT: the losing row's own claim is still the record of
-- what it said, and 'legacy-unlabelled-rejection' is the honest label -- the
-- writer is no longer identifiable from the row, so inventing a specific
-- reason would be a worse lie than the one being repaired. The label is
-- deliberately NOT in REJECTION_VERDICT_ACTIONS: these rows were never
-- reasoned about, so a reverify lane must still be free to look at them.
UPDATE catalog_source_identity
   SET bind_evidence_json = JSON_SET(
         COALESCE(bind_evidence_json, JSON_OBJECT()),
         '$.previousAction',
           JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.action')),
         '$.action', 'legacy-unlabelled-rejection',
         -- No semicolons inside string literals in this file: the migration
         -- runner splits statements on ';' without parsing quotes, so one here
         -- truncates the statement mid-string and the file dies on a syntax
         -- error (measured 2026-08-11, first attempt at this migration).
         '$.reason', 'rejected before writers were required to record a reason, and the original decision (if any) was never written to this row',
         '$.relabelledBy', 'migration 040',
         '$.relabelledAt', UTC_TIMESTAMP())
 WHERE match_status LIKE 'rejected%'
   AND COALESCE(JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, '$.action')), '')
       IN ('confirm', 'replace', 'propose');

SET @have_check := (
    SELECT COUNT(*) FROM information_schema.CHECK_CONSTRAINTS
    WHERE CONSTRAINT_SCHEMA = DATABASE()
      AND CONSTRAINT_NAME = 'ck_source_identity_rejection_labelled'
);
SET @ddl := IF(
    @have_check > 0,
    'SELECT 1',
    'ALTER TABLE catalog_source_identity ADD CONSTRAINT ck_source_identity_rejection_labelled CHECK (match_status NOT LIKE ''rejected%'' OR COALESCE(JSON_UNQUOTE(JSON_EXTRACT(bind_evidence_json, ''$.action'')), '''') NOT IN (''confirm'', ''replace'', ''propose''))'
);
PREPARE add_check FROM @ddl;
EXECUTE add_check;
DEALLOCATE PREPARE add_check;

INSERT IGNORE INTO cardz_schema_version (version_code) VALUES ('040');
