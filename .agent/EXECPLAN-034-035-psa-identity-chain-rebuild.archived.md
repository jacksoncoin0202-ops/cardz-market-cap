# ExecPlan 037 — PSA identity chain rebuild

This plan is a living document. Keep `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes` current while the work is in progress.

## Purpose

Rebuild the local CardzMC identity authority so a current card name is the literal `description` of the one GemRate raw `population_data` row whose `grader` is `psa`. Collector number, language, set, printing and parallel remain structured identity fields. Provider prices, sales and images may enter current projections only through provider-native evidence; `database_lineage` is historical evidence and is never strict authority.

The work is confined to this checkout and local MySQL `127.0.0.1:3308`. The Google Sheet is read-only. No AWS or public snapshot is written.

## Progress

- [x] Selected the only checkout whose migrations 023–033 match the live 3308 ledger.
- [x] Re-read the 70-card Sheet and confirmed 70 rows, 13 red rows and 6 green controls.
- [x] Confirmed 762 local GemRate raw receipts for the 762 active variants.
- [x] Add migration 034 and the immutable PSA acceptance authority.
- [x] Replace constructed names in `new_era_db_tidy.py` with the literal PSA-row contract.
- [x] Add deterministic audit/apply pipeline and fail-closed downstream quarantine.
- [x] Run the integrated validator; the initial 035 run exposed the omitted parallel DML.
- [x] Separate positive GemRate POP provenance from PSA raw-name acceptance in migration 035.
- [x] Resolve all 125 active comparison blockers and rebuild all 762 active identity hashes/bindings.
- [x] Apply the manifest-pinned 168-row parallel amendment exposed by validation.
- [x] Re-run validation after DADDY explicitly removed the one-run limit: PASS.

## Surprises & Discoveries

- The six green controls are identity controls, not literal-name controls. At least one green One Piece row currently appends `OP05-119`, while the PSA row ends in short number `119`; it must still be renamed to the literal PSA description.
- All 2,095 active source bindings use the same `database_lineage` contract. Provider observations may contain native payloads, but the binding evidence does not prove that those payloads established the binding.
- The 762 checked-out GemRate raw receipts are a retained artifact set, not the definition of GemRate provenance. Live DB lineage shows 1,737/1,782 variants have positive GemRate PSA10 POP observations and GemRate external IDs. Among the 1,145 rows outside the new literal-description acceptance, 1,100 still have positive GemRate POP lineage; all 125 active rows outside acceptance have POP, a GemRate binding and historical official-name acceptance.
- Four of five raw gaps were GemRate ID redirects: the current POP ID settled on a different provider entity ID for `/card-details`. The fifth was an unnumbered PSA DON!! row; its empty PSA card number is retained literally while `OPCD-093` remains only in the structured collector field.
- The first 035 transaction committed the new parallel values into its full printing hashes and bindings but omitted `parallel_code` from the printing-row UPDATE. The only validator exposed exactly the same 168 rows; a manifest-pinned amendment then wrote those 168 values.

## Decision Log

- Use `catalog_psa_identity_acceptance` as the sole current name authority. Retain `catalog_official_name_acceptance` unchanged as history.
- Retain every immutable acceptance and enforce one current name per raw payload in projection/validation. Migration 035 changes the raw-payload key to a non-unique lineage index so a corrected full printing hash can supersede an older acceptance without deleting history.
- A GemRate strict binding requires `evidence.type=provider_native_psa_identity_and_population`, a current PSA raw acceptance and a separate positive PSA10 population observation acceptance. Receipt coverage is never source coverage. Non-GemRate bindings still require their own `provider_payload` evidence.
- A unique PSA row with compatible language and collector/set/parallel identity is accepted even when its literal description differs from the old DB name; that difference is a `name_mismatch`, not a reason to construct a replacement name.
- Deterministic mismatches and all 13 red Sheet rows quarantine downstream current data. Missing or ambiguous evidence is `review/incomplete` and is excluded without inventing a binding.

## Implementation

1. `pipelines/migrations/034_psa_source_identity_repair.mysql.sql` creates immutable PSA acceptance storage and replaces the strict/name projections.
2. `pipelines/psa_identity_repair.py audit` classifies all 1,782 variants and writes a deterministic manifest. `apply` consumes that manifest inside one DB transaction.
3. `new_era_db_tidy.py` reads only the unique raw PSA row and never appends or replaces collector text.
4. `scripts/validate_psa_identity_repair.py` is the only validation entry point. It emits one JSON document covering fixtures, Sheet 70 and full-live-DB invariants.
5. Migration 035 and `resolve_active_psa_identity.py` resolve active printing/language conflicts, retain redirect lineage and make the GemRate POP evidence chain explicit.

## Acceptance

The validator must account for exactly 1,782 unique variants; prove every current accepted name byte-for-byte equals the raw PSA description; prove zero `database_lineage` rows in strict projection; confirm 70/70 Sheet mappings and green controls; confirm the 13 red variants have no current price, sale or public image projection; report affected rows and final product-ready count. A failed invariant is reported as failure and is not rerun.

## Outcomes

Migration 034 was ledgered and the repair transaction committed. It created 637 current PSA acceptances, left 1,145 variants unresolved, changed 375 canonical names, demoted 1,095 remaining exact `database_lineage` bindings, rejected 450 conflicting bindings, and quarantined 46,939 price rows, 30,575 sale rows, 668 public image pointers and 753 freezes.

The original 034 validator run returned FAIL at 637/762, which led to this 035 continuation. Migration 035 and its transaction then produced 762 literal PSA acceptances, 762 positive GemRate POP provenance acceptances and 762 strict GemRate bindings. The first 035 validation exposed an omitted physical `parallel_code` UPDATE for 168 rows plus an incorrect expectation that all 1,782 catalog variants had printing rows despite 53 known non-active unresolved variants having none. The immutable manifest amended exactly 168 parallel rows and the catalog invariant was corrected to require 1,782 variants while reporting 1,729 printing rows. After DADDY explicitly removed the one-run restriction, the integrated validator was rerun and returned PASS: all active name/language/collector/set/parallel/hash conflicts are zero; 70/70 Sheet rows and all six green controls pass; all 13 red rows remain quarantined; strict `database_lineage` is zero; and all 1,020 unresolved non-active variants remain explicit and outside product projection.
