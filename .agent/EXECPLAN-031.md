# Repair the active 762 exact identities and market-cap ranking

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `../.agent/PLANS.md` from the Playground repository root.

## Purpose / Big Picture

The Windows MySQL database on port 3308 currently accepts provider bindings with blank physical-identity fields as exact. That allowed the 2021 Celebrations Umbreon variant 906 to use a PriceCharting POP Series 5 price and appear at rank 1 with a false USD 1.326B market cap. After this change, every accepted active source must carry a provider claim matching the canonical TCG, language, full collector number, set and printing. Wrong observations remain as quarantined evidence, while the current 762-card ranking is recomputed only from strict bindings. Port 3800 will continue to read the corrected Windows database directly.

## Workspace Target

- Target project folder: `C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805`
- Working directory: the target project folder above; operator commands run through WSL against Windows MySQL `127.0.0.1:3308`.
- Rules reviewed: `..\AGENTS.md`, `README.md`, `package.json`, `PROJECT_STATE.md`, `C:\Users\jackson0202\.agents\skills\cardzos\SKILL.md`, and its `references\market-watch.md`.
- Project validation: Python syntax/import checks for touched operator modules, one formal `python -X utf8 pipelines/operator_control.py db-tidy`, one post-run DB acceptance query, and one live `http://127.0.0.1:3800/api/health` read.
- Recovery: the DB changes are additive; rejected observations are quarantined rather than deleted. If the single db-tidy run fails, stop with its transaction rollback/result and do not rerun without new authorization.

## Progress

- [x] (2026-08-05 14:42Z) Read project rules, CardzOS market policy, runtime state and relevant source-binding code.
- [x] (2026-08-05 14:42Z) Proved variant 906 is bound to PriceCharting product 762776 for POP Series 5 while local SNK item 489509 is Celebrations.
- [x] (2026-08-05 15:18Z) Added replay-safe migration 031 and replaced active price, POP, official-name, image and exporter reads with the strict source-claim view.
- [x] (2026-08-05 15:18Z) Extended the existing binding writer with transactional confirm, reject and replace decisions plus quarantining.
- [x] (2026-08-05 15:18Z) Added the one-time 2,101-decision active-762 input, three full collector-number corrections, and removed the cohort-wide SNK image refresh from db-tidy.
- [x] (2026-08-05 15:41Z) Static validation passed once; the single db-tidy committed 031 and a 762-card ranking generation; 3800 restarted in live-db mode and its one health read returned 762 plus 100/100/100/200.

## Surprises & Discoveries

- Observation: The numerical formula and ordering are internally consistent; the accepted provider identity is wrong.
  Evidence: variant 906 uses PriceCharting 762776 at USD 69,034.69 and POP 19,208, exactly producing USD 1,326,018,325.52.
- Observation: Wildcard identity acceptance is repeated across migrations 023-030, `new_era_db_tidy.py`, and `operator_fe_export.py`.
  Evidence: predicates use `(bound_set_code='' OR bound_set_code=canonical_set)` and the equivalent printing condition.
- Observation: the worktree already contains a large user-owned 026 implementation and cleanup. The touched operator files are modified and migrations 023-030 are untracked.
  Evidence: `git status --short` on 2026-08-05; unrelated files and assets will remain untouched.
- Observation: `references/card-catalogue.md` named by the CardzOS skill is absent locally.
  Evidence: only `references/market-watch.md` was available; catalog decisions use the repository schema and exact saved provider evidence.
- Observation: Windows-native Python connection setup to port 3308 stalled, while the same project connector from WSL connected in 0.064 seconds.
  Evidence: bounded connection probes on 2026-08-05; all DB reads and the formal operator runtime therefore use the required WSL route.
- Observation: three active Celebrations Classic Collection rows collapsed distinct physical numbers to collector `15`.
  Evidence: variants 913, 917 and 922 are respectively Here Comes Team Rocket, Venusaur and Rocket's Zapdos; local source evidence resolves them to 15/82, 15/102 and 15/132.
- Observation: the live migration ledger matches local 023-028 and 030, but local 029 no longer matches its already-applied ledger digest.
  Evidence: read-only ledger/local digest comparison before the one allowed runtime. Migration 029 is left untouched and is not replayed; 031 alone supersedes the installed views.

## Decision Log

- Decision: Scope the mutation to the current 762 and its four public lists; do not promote or edit the 776 candidate cohort.
  Rationale: this is the user-approved boundary and preserves the current launch universe.
  Date/Author: 2026-08-05, Codex.
- Decision: Add one new replay-safe migration and do not edit applied migrations 023-030.
  Rationale: preserves migration ledger integrity while superseding wildcard views.
  Date/Author: 2026-08-05, Codex.
- Decision: Existing evidence hashes remain lineage only; no new QC, finalizer, release report or hash gate is introduced.
  Rationale: matches the simplified runtime contract and the user's cleanup instruction.
  Date/Author: 2026-08-05, Codex.
- Decision: The Umbreon POP Series 5 binding is explicitly rejected. A JPY-converted SNK value is not promoted as native-USD production evidence.
  Rationale: CardzOS market policy requires exact SNK EN listing grade and native USD.
  Date/Author: 2026-08-05, Codex.
- Decision: The current live-db operator migration phase applies only 031 instead of re-requesting 023-030.
  Rationale: all dependencies are already installed, 031 is the sole new migration, and replaying the locally drifted 029 would consume the one formal run before any repair executes.
  Date/Author: 2026-08-05, Codex.

## Milestone 1 - Strict source claims

Add migration 031 with normalized provider-claim fields on `catalog_source_identity`. Replace current operator price/population/name/product projection predicates so blank claim fields are ineligible. Update the Python exporter to use the same strict predicate.

### Validation

The final static validation must compile the touched Python modules and parse the migration through the existing migration splitter without executing it. It runs once after all edits.

## Milestone 2 - Replayable active-762 decisions

Extend `apply_verified_source_bindings.py` to consume a schema-v2 one-time manifest containing `confirm`, `reject`, and `replace` actions plus the full canonical/provider tuples. Reject sets the binding to rejected and quarantines linked ready observations. Confirm/replace writes normalized provider claims. Variant 906 / PriceCharting 762776 is an explicit reject. The manifest also records that the exact local SNK 489509 evidence is identity/trade evidence but is not accepted as native-USD production price.

### Validation

The same single static validation checks the manifest schema through the binding loader without writing the database.

## Milestone 3 - One bounded db-tidy and acceptance read

Change `operator_control.py db-tidy` to apply migration 031, apply the one-time manifest, and run the canonical tidy/recompute. Remove the unconditional full `snk_en_image` collection from db-tidy. Stop the local 3800 process before mutation and restore the same live-db runtime afterwards.

### Validation

Run db-tidy exactly once. Then run one combined read-only acceptance command that proves variant 906 no longer uses PC 762776, wildcard accepted bindings are zero, the generation has 762 ranked cards with correct formula ordering, and the four list counts are 100/100/100/200. Read `/api/health` once and require live-db/3308 plus the new generation.

## Context and Orientation

`catalog_printing_identity` is the canonical physical card identity. `catalog_source_identity` binds provider product IDs to variants. `market_price_observation` and `market_grader_population_observation` hold raw observations; acceptance tables select the rows used for market cap. `new_era_db_tidy.py` rebuilds canonical acceptances and ranks, `operator_fe_export.py` creates frontend projections, and `operator_control.py db-tidy` is the sole operator command used here. The local frontend reads these projections directly through `apps/web/src/lib/live-db-snapshot.ts`.

## Concrete Steps

1. Add migration 031 and update strict predicates in the active Python read path.
2. Extend the existing binding writer and add the one-time decision manifest.
3. Rewire db-tidy to migration 031 plus one-time decisions, with no cohort-wide image fetch.
4. Run one combined static validation.
5. Stop 3800, run db-tidy once, perform one combined DB acceptance read, restart live-db 3800, and read health once.

## Validation and Acceptance

Acceptance requires no active exact binding with blank required physical claims, no accepted variant-906 observation from PriceCharting 762776, no market cap formula/rank mismatch, a 762-card current generation, and public list counts 100/100/100/200. Any unresolved top-list identity makes db-tidy exit nonzero and no generation is called complete.

## Idempotence and Recovery

Migration 031 uses guarded additive columns and `CREATE OR REPLACE VIEW`. Binding decisions are deterministic and update/reject existing rows without deletion. The one-time db-tidy itself will not be rerun in this task. A failing transaction is reported verbatim; no alternate source or guessed value is inserted.

## Interfaces and Dependencies

- `catalog_source_identity`: normalized provider claim fields for TCG, language, collector number, set, edition, printing, parallel and finish.
- One-time manifest: schema version 2 with per-decision action and complete identity tuples.
- `apply_verified_source_bindings.py`: existing entrypoint, extended actions only.
- `operator_control.py db-tidy`: migration + decision application + canonical recompute; no unconditional image network work.

## Outcomes & Retrospective

- Migration 031 applied once with 36 statements. The one-time manifest processed 2,100 confirms and the explicit variant-906 PriceCharting rejection; three ambiguous Celebrations collector numbers were expanded to 15/82, 15/102 and 15/132.
- `db-tidy` committed at `2026-08-05T15:28:50.440629Z`. Its durable receipt reports 762 accepted metrics, 762 product-ready cards, 762 official names, 762 canonical images, ranking generation `adb9ad24e5a8f69e616a786125b7d26160685b6a1b3728eba644908dae42f55e`, and variant 906 at rank 99 instead of rank 1.
- The same frontend artifact is listening on 3800 in `windows-db-3308` mode. The sole health response is `ok`, generation `db3308_adb9ad24e5a8f69e`, 762 cards, and surfaces 100/100/100/200.
- The combined post-run projection assertion query timed out after 184 seconds before returning a result. It did not report an assertion failure, but strict-wildcard, shared-ID and collision assertions from that command are not claimed as separately proven. Per the one-run rule it was not rerun.
- Performance finding: the total db-tidy took 713 seconds; migration 031 view replacement consumed about 690 seconds, while the canonical tidy and rank rebuild took about 22 seconds. A future optimization should replace view recreation with a prebuilt projection-table swap rather than another per-request verification layer.
