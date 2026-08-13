# CARDZ Backend Control Plane

This file is generated from `config/data-routing.json`. Do not edit it by hand.

## Architecture entrypoint

- Registry: `config/data-routing.json`.
- Code index: `@colbymchenry/codegraph` at `.codegraph/codegraph.db`.
- The code index is read-only evidence. It never overrides authority, routing, storage, ranking, or work-item policy.

## Storage and ranking policy

- Canonical ranking storage limit: `None` (unbounded).
- Raw payload policy: `private_pointer_only`.
- Data-cleaning rules: `config/data-cleaning-rules.json` (raw preserved privately; MySQL retains pointers plus typed facts).
- Ranking language partitioning: `False`.
- Formal ranking: PSA 10 POP ≥ `1000`.
- Pre-entry radar: PSA 10 POP `971–999`; POP `≤970` remains discovery-only.

## Tools

| ID | Phase | Entrypoint | Timeout | OS | Route metrics | Manuals | Tests |
| --- | --- | --- | --- | --- | --- | --- |
| route-contract | preflight | `pipelines/data_routing.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, psa10_reference_price, tracked_sales, price_change_1d_7d_30d, psa10_market_cap, grader_supply_psa_bgs_cgc_sgc_tag, card_market_story, fx_usd_display_rates | backend-runbook, data-contract | registry-lineage |
| failure-ledger | status | `pipelines/failure_ledger.py` | 1800s | windows, linux, aws | — | backend-runbook | failure-ledger |
| pricecharting-full-shard | collect | `pipelines/pc_full_shard_runner.py` | 14400s | windows, linux, aws | tracked_sales, psa10_reference_price | data-contract, backend-runbook | failure-ledger |
| pricecharting-full-serial | collect | `pipelines/pc_full_serial_driver.py` | 86400s | windows, linux, aws | tracked_sales, psa10_reference_price | data-contract, backend-runbook | failure-ledger |
| grade10-bootstrap | discovery | `integrations/grade10/run_service.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook, data-contract | registry-lineage |
| candidate-gemrate-backfill | discovery | `pipelines/gemrate_candidate_backfill.py` | 14400s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, grader_supply_psa_bgs_cgc_sgc_tag | gemrate-source, backend-runbook | candidate-backfill, gemrate-transport |
| exact-crosswalk | discovery | `pipelines/source_crosswalk.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook, data-contract | registry-lineage |
| tracked-universe | discovery | `pipelines/tracked_universe.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook | daily-orchestration |
| gemrate-population | collect | `pipelines/gemrate_source.py` | 1800s | windows, linux, aws | psa10_population, psa10_population_history, grader_supply_psa_bgs_cgc_sgc_tag | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| snk-exact-refill | collect | `pipelines/snkrdunk_bulk.py` | 14400s | windows, linux, aws | psa10_reference_price, tracked_sales | snk-manual, backend-runbook | snk-exact-refill |
| snk-reference-price | collect | `pipelines/snk_market_data.py` | 1800s | windows, linux, aws | psa10_reference_price, tracked_sales | snk-manual, backend-runbook | snk-exact-refill, daily-orchestration |
| ebay-sold-normalizer | collect | `pipelines/ebay_sold_data.py` | 1800s | windows, linux, aws | tracked_sales, psa10_reference_price | data-contract, backend-runbook | ebay-normalizer |
| tcgpricelookup-ssr | collect | `pipelines/tcgpricelookup_ssr.py` | 14400s | windows, linux, aws | psa10_reference_price, price_change_1d_7d_30d | data-contract | tcgpricelookup-ssr |
| us-price-fallback | collect | `pipelines/us_price_fallback.py` | 1800s | windows, linux, aws | psa10_reference_price | data-contract | registry-lineage |
| canonical-sync | normalize | `pipelines/market_source_sync.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, psa10_reference_price, tracked_sales, grader_supply_psa_bgs_cgc_sgc_tag, card_market_story | data-contract, backend-runbook | canonical-sync |
| canonical-db | ingest | `pipelines/db_runtime.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, psa10_reference_price, tracked_sales, grader_supply_psa_bgs_cgc_sgc_tag, card_market_story, fx_usd_display_rates | data-contract, backend-runbook | canonical-sync |
| canonical-db-qc | audit | `pipelines/canonical_db_qc.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_reference_price, tracked_sales, psa10_market_cap | data-contract, backend-runbook | canonical-db-qc |
| universe-authority | materialize | `pipelines/universe_authority.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population | data-contract, backend-runbook | universe-authority |
| printing-plan | materialize | `pipelines/db_runtime.py` | 1800s | windows, linux, aws | candidate_identity | canonical-printing, data-contract, backend-runbook | canonical-printing, backend-cli |
| printing-materialize | materialize | `pipelines/db_runtime.py` | 1800s | windows, linux, aws | candidate_identity | canonical-printing, data-contract, backend-runbook | canonical-printing, backend-cli |
| machine-status | status | `scripts/backend.py` | 5s | windows, linux, aws | candidate_identity, psa10_population, psa10_reference_price, tracked_sales, psa10_market_cap | backend-runbook | universe-authority |
| project-state-render | status | `scripts/render_project_state.py` | 1800s | windows, linux, aws | — | backend-runbook | project-state-render |
| ranking-derivation | derive | `pipelines/ranking_derivation.py` | 1800s | windows, linux, aws | psa10_market_cap | data-contract | ranking-derivation |
| ranking-alerts | derive | `pipelines/market_alerts.py` | 1800s | windows, linux, aws | price_change_1d_7d_30d, psa10_market_cap | data-contract, backend-runbook | market-alerts |
| coverage-audit | audit | `pipelines/data_coverage_audit.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_reference_price, psa10_market_cap | data-contract, backend-runbook | coverage-audit |
| snapshot-export | export | `pipelines/canonical_public_snapshot.py` | 1800s | windows, linux, aws | psa10_population, psa10_reference_price, tracked_sales, price_change_1d_7d_30d, psa10_market_cap | data-contract | snapshot-export |
| public-snapshot-qc | qc | `pipelines/public_snapshot_qc.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_reference_price, tracked_sales, psa10_market_cap | data-contract | public-snapshot-qc |
| official-publisher | publish | `pipelines/publish-snapshot.mjs` | 1800s | windows, linux, aws | — | data-contract, backend-runbook | official-publisher |
| canonical-seed | seed | `scripts/canonical_seed.py` | 1800s | windows, linux, aws | — | backend-runbook, data-contract | registry-lineage |
| seed-restore | restore | `scripts/seed_restore.py` | 1800s | windows, linux, aws | — | backend-runbook, data-contract | registry-lineage |
| handoff-verify | verify | `scripts/verify_handoff.py` | 1800s | windows, linux, aws | — | backend-runbook, data-contract | registry-lineage |

## Profiles

### `full-backfill`

1. `preflight` — route-contract.
1. `discovery` — grade10-bootstrap, exact-crosswalk, tracked-universe, candidate-gemrate-backfill.
1. `collect` — snk-exact-refill, gemrate-population, snk-reference-price, ebay-sold-normalizer.
1. `normalize` — canonical-sync.
1. `ingest` — canonical-db.
1. `derive` — ranking-derivation, ranking-alerts.
1. `audit` — canonical-db-qc, coverage-audit.

### `weekly-candidate-refresh`

1. `preflight` — route-contract.
1. `discovery` — candidate-gemrate-backfill, exact-crosswalk.
1. `collect` — snk-exact-refill.
1. `normalize` — canonical-sync.
1. `ingest` — canonical-db.
1. `derive` — ranking-derivation, ranking-alerts.
1. `audit` — canonical-db-qc, coverage-audit.

### `daily`

1. `preflight` — route-contract, machine-status.
1. `discovery` — exact-crosswalk.
1. `collect` — gemrate-population, snk-reference-price, ebay-sold-normalizer.
1. `normalize` — canonical-sync.
1. `ingest` — canonical-db.
1. `materialize` — universe-authority.
1. `derive` — ranking-derivation, ranking-alerts.
1. `audit` — canonical-db-qc, coverage-audit.
1. `export` — snapshot-export.
1. `qc` — public-snapshot-qc.
1. `publish` — official-publisher.

### `export`

1. `audit` — canonical-db-qc, coverage-audit.
1. `export` — snapshot-export.
1. `qc` — public-snapshot-qc.
1. `publish` — official-publisher.

### `snapshot-export`

1. `audit` — canonical-db-qc, coverage-audit.
1. `export` — snapshot-export.
1. `qc` — public-snapshot-qc.
1. `publish` — official-publisher.

### `seed-build`

1. `audit` — coverage-audit.
1. `seed` — canonical-seed, handoff-verify.

### `restore`

1. `verify` — handoff-verify.
1. `restore` — seed-restore.
1. `canary` — route-contract, coverage-audit.

### `seed-restore`

1. `verify` — handoff-verify.
1. `restore` — seed-restore.
1. `canary` — route-contract, coverage-audit.

## Architecture nodes

| ID | Kind | Label | Tools | Metrics | DB targets |
| --- | --- | --- | --- | --- | --- |
| discovery.candidates | collector | Candidate discovery and radar | grade10-bootstrap, candidate-gemrate-backfill | candidate_identity | — |
| authority.gemrate | authority | GemRate identity and population authority | — | candidate_identity, psa10_population, psa10_population_history, grader_supply_psa_bgs_cgc_sgc_tag | — |
| transport.gemrate-direct | transport | GemRate direct API | gemrate-population | psa10_population, psa10_population_history | — |
| transport.gemrate-public | transport | GemRate exact public card receipt | candidate-gemrate-backfill | candidate_identity, psa10_population | — |
| transport.gemrate-mirror | transport | Grade10 GemRate current-POP mirror | grade10-bootstrap, gemrate-population | psa10_population | — |
| landing.immutable-receipts | landing | Immutable private receipts and manifests | — | — | — |
| normalizer.identity | normalizer | Exact canonical printing resolver | exact-crosswalk, canonical-sync, printing-plan, printing-materialize | candidate_identity | — |
| crosswalk.provider-alias | normalizer | Provider alias to canonical printing | candidate-gemrate-backfill, canonical-db | candidate_identity | catalog_source_identity<br>catalog_provider_identity_alias |
| review.identity | review_queue | Identity conflict review queue | — | candidate_identity | market_identity_review_queue |
| collector.snk-exact | collector | Exact SNK PSA 10 price and trades | snk-exact-refill, snk-reference-price | psa10_reference_price, tracked_sales | — |
| database.canonical | database | Canonical MySQL-compatible facts | canonical-db, printing-materialize | — | catalog_variant<br>catalog_printing_identity<br>catalog_source_identity<br>catalog_provider_identity_alias<br>market_grader_population_observation<br>market_price_observation<br>market_daily_sales_aggregate<br>market_candidate_daily_snapshot<br>market_index_snapshot<br>market_index_constituent |
| validator.canonical-db-qc | validator | Read-only full canonical DB release QC | canonical-db-qc | candidate_identity, psa10_population, psa10_reference_price, tracked_sales, psa10_market_cap | — |
| authority.universe | authority | Deterministic DB-derived qualified and monitoring universe | universe-authority | — | market_universe_lock<br>market_universe_member |
| validator.eligibility | validator | POP, identity and price eligibility gate | coverage-audit | candidate_identity, psa10_population, psa10_reference_price, psa10_market_cap | — |
| review.media | review_queue | Identity, image and outlier QC review | public-snapshot-qc | — | — |
| validator.public-qc | validator | Generation-bound strict public QC receipt | public-snapshot-qc | — | — |
| derivation.market | derivation | Market cap, windows, sales and alerts | ranking-derivation, ranking-alerts | price_change_1d_7d_30d, psa10_market_cap, tracked_sales | — |
| ranking.scopes | derivation | Complete TCG, Pokemon and One Piece rankings | ranking-derivation | psa10_market_cap | — |
| export.views | export | Top 100, 300, 350 and reserve projections | — | — | — |
| export.sanitized-snapshot | export | Provider-safe frontend handshake | snapshot-export | — | — |
| publisher.official | export | Only generation and atomic pointer publisher | official-publisher | — | — |
| orchestrator.daily | orchestrator | One Python daily control flow | — | — | — |
| status.machine | validator | Read-only machine release status | machine-status, project-state-render | — | — |
| state.last-good | checkpoint | Atomic checkpoint and last-good generation | — | — | — |
| index.codegraph | code_index | Local CodeGraph SQLite index | — | — | — |

## Work items

| ID | Priority | Architecture nodes | Depends on | Owner files |
| --- | --- | --- | --- | --- |
| P0-REGISTRY-SCHEMA | P0 | orchestrator.daily<br>index.codegraph | — | `pipelines/registry_lineage.py`<br>`tests/test_registry_lineage.py` |
| P0-CONTROL-PLANE-NODES | P0 | orchestrator.daily<br>database.canonical<br>export.sanitized-snapshot | — | `config/data-routing.json` |
| P0-TASK-BOARD-SOURCE | P0 | orchestrator.daily | P0-CONTROL-PLANE-NODES | `config/data-routing.json` |
| P0-GENERATED-DOCS | P0 | index.codegraph<br>orchestrator.daily | P0-REGISTRY-SCHEMA, P0-CONTROL-PLANE-NODES | `pipelines/registry_lineage.py`<br>`docs/generated` |
| P0-GRAPH-QUERY | P0 | index.codegraph<br>orchestrator.daily | P0-REGISTRY-SCHEMA | `pipelines/registry_lineage.py`<br>`pipelines/data_routing.py`<br>`scripts/backend.py` |
| P0-CODEGRAPH-ADAPTER | P0 | index.codegraph | — | `package.json`<br>`package-lock.json`<br>`.gitignore` |
| P0-DOCUMENT-AUTHORITY | P0 | index.codegraph<br>orchestrator.daily | P0-REGISTRY-SCHEMA | `config/data-routing.json`<br>`pipelines/registry_lineage.py`<br>`scripts/verify_doc_refs.py`<br>`tests/test_registry_lineage.py`<br>`tests/test_verify_doc_refs.py`<br>`docs/generated/DOCUMENT_AUTHORITY.md` |
| P0-AGENT-EXECUTION-FUNNEL | P0 | index.codegraph<br>orchestrator.daily | P0-DOCUMENT-AUTHORITY | `config/data-routing.json`<br>`pipelines/registry_lineage.py`<br>`tests/test_registry_lineage.py`<br>`docs/generated/AGENT_EXECUTION_FUNNEL.md`<br>`docs/generated/AGENT_EXECUTION_ARCHITECTURE.html`<br>`docs/generated/roles` |
| QC-A11-BASELINE-BOOTSTRAP | P0 | status.machine<br>database.canonical<br>index.codegraph | P0-AGENT-EXECUTION-FUNNEL | `scripts/backend.py`<br>`tests/test_backend_cli_contract.py` |
| QC-A01-FRONTEND-CONSUMERS | P0 | export.views<br>export.sanitized-snapshot | QC-A11-BASELINE-BOOTSTRAP | `apps/web/src`<br>`tests/frontend-consumer-census` |
| QC-A02-PUBLIC-FIELD-LINEAGE | P0 | export.sanitized-snapshot<br>validator.public-qc<br>database.canonical | QC-A11-BASELINE-BOOTSTRAP | `packages/market-data/src`<br>`pipelines/canonical_public_snapshot.py`<br>`tests/test_canonical_public_snapshot.py` |
| QC-A03-CANONICAL-SCHEMA-FINGERPRINT | P0 | database.canonical<br>normalizer.identity<br>authority.universe | QC-A11-BASELINE-BOOTSTRAP | `pipelines/migrations`<br>`pipelines/db_runtime.py`<br>`pipelines/canonical_db_qc.py`<br>`tests/test_printing_materialization.py` |
| QC-A04-SCRIPT-LIFECYCLE | P0 | index.codegraph<br>orchestrator.daily<br>publisher.official | QC-A11-BASELINE-BOOTSTRAP | `pipelines/script_inventory.py`<br>`tests/test_script_inventory.py` |
| QC-A11-CONTROL-CLI | P0 | orchestrator.daily<br>index.codegraph<br>status.machine | QC-A01-FRONTEND-CONSUMERS, QC-A02-PUBLIC-FIELD-LINEAGE, QC-A03-CANONICAL-SCHEMA-FINGERPRINT, QC-A04-SCRIPT-LIFECYCLE | `scripts/backend.py`<br>`tests/test_backend_cli_contract.py` |
| QC-A05-POP-GRADER | P0 | authority.gemrate<br>transport.gemrate-direct<br>database.canonical | QC-A11-CONTROL-CLI, QC-A03-CANONICAL-SCHEMA-FINGERPRINT | `pipelines/gemrate_source.py`<br>`pipelines/gemrate_candidate_backfill.py`<br>`tests/test_gemrate_transport.py` |
| QC-A06-PRICE-HISTORY | P0 | collector.snk-exact<br>database.canonical | QC-A11-CONTROL-CLI, QC-A03-CANONICAL-SCHEMA-FINGERPRINT | `pipelines/snk_market_data.py`<br>`pipelines/snkrdunk_bulk.py`<br>`tests/test_snkrdunk_bulk_refill.py` |
| QC-A07-SALES-LIQUIDITY | P0 | collector.snk-exact<br>derivation.market<br>database.canonical | QC-A11-CONTROL-CLI, QC-A03-CANONICAL-SCHEMA-FINGERPRINT | `pipelines/ebay_sold_data.py`<br>`tests/test_ebay_sold_data.py` |
| QC-A08-IMAGE-MEDIA | P0 | review.media<br>validator.public-qc | QC-A11-CONTROL-CLI, QC-A02-PUBLIC-FIELD-LINEAGE, QC-A03-CANONICAL-SCHEMA-FINGERPRINT | `pipelines/verify_images.py`<br>`tests/test_public_snapshot_qc.py` |
| QC-A09-EDITORIAL-LOCALES | P1 | export.sanitized-snapshot<br>review.media | QC-A11-CONTROL-CLI, QC-A01-FRONTEND-CONSUMERS, QC-A02-PUBLIC-FIELD-LINEAGE | `pipelines/editorial_import.py`<br>`tests/test_editorial_import.py` |
| QC-A10-DERIVED-RANKING | P0 | derivation.market<br>ranking.scopes<br>export.views | QC-A01-FRONTEND-CONSUMERS, QC-A02-PUBLIC-FIELD-LINEAGE, QC-A05-POP-GRADER, QC-A06-PRICE-HISTORY, QC-A07-SALES-LIQUIDITY | `pipelines/ranking_derivation.py`<br>`pipelines/market_alerts.py`<br>`tests/test_ranking_derivation.py` |
| QC-A11-INCREMENTAL-RUNTIME | P0 | orchestrator.daily<br>state.last-good<br>publisher.official | QC-A04-SCRIPT-LIFECYCLE, QC-A05-POP-GRADER, QC-A06-PRICE-HISTORY, QC-A07-SALES-LIQUIDITY, QC-A08-IMAGE-MEDIA, QC-A09-EDITORIAL-LOCALES, QC-A10-DERIVED-RANKING | `scripts/backend.py`<br>`pipelines/run_daily.py`<br>`pipelines/run_receipts.py`<br>`tests/test_run_daily_orchestration.py` |
| QC-A12-INDEPENDENT-REDTEAM | P0 | validator.canonical-db-qc<br>validator.public-qc<br>status.machine | QC-A01-FRONTEND-CONSUMERS, QC-A02-PUBLIC-FIELD-LINEAGE, QC-A03-CANONICAL-SCHEMA-FINGERPRINT, QC-A04-SCRIPT-LIFECYCLE, QC-A05-POP-GRADER, QC-A06-PRICE-HISTORY, QC-A07-SALES-LIQUIDITY, QC-A08-IMAGE-MEDIA, QC-A09-EDITORIAL-LOCALES, QC-A10-DERIVED-RANKING, QC-A11-INCREMENTAL-RUNTIME | `tests/acceptance`<br>`data/runtime/private-reports/red-team` |
| T2-GEMRATE-ALIAS-DURABILITY | P0 | crosswalk.provider-alias<br>database.canonical<br>review.identity | P0-CONTROL-PLANE-NODES | `pipelines/db_runtime.py`<br>`pipelines/migrations/010_provider_identity_alias.mysql.sql`<br>`tests/test_db_runtime.py` |
| T3-GEMRATE-CANDIDATE-CLASSIFICATION | P0 | discovery.candidates<br>transport.gemrate-public<br>normalizer.identity<br>review.identity | T2-GEMRATE-ALIAS-DURABILITY | `pipelines/gemrate_source.py`<br>`pipelines/gemrate_candidate_backfill.py`<br>`data/runtime/private-source-runs` |
| T4-SNK-EXACT-PRICE-HISTORY | P0 | crosswalk.provider-alias<br>collector.snk-exact<br>database.canonical | T3-GEMRATE-CANDIDATE-CLASSIFICATION | `pipelines/snkrdunk_bulk.py`<br>`pipelines/snk_market_data.py`<br>`pipelines/source_crosswalk.py`<br>`pipelines/tracked_universe.py`<br>`scripts/backend.py` |
| T5-RANKING-VIEWS | P0 | validator.eligibility<br>derivation.market<br>ranking.scopes<br>export.views | T4-SNK-EXACT-PRICE-HISTORY | `pipelines/ranking_derivation.py`<br>`pipelines/canonical_public_snapshot.py` |
| T6-DAILY-RECOVERY | P0 | orchestrator.daily<br>state.last-good<br>publisher.official | T5-RANKING-VIEWS | `scripts/backend.py`<br>`pipelines/run_daily.py`<br>`pipelines/run_receipts.py`<br>`docs/RUNBOOK.md` |
| P0-UNIVERSE-AUTHORITY | P0 | database.canonical<br>authority.universe<br>status.machine | T2-GEMRATE-ALIAS-DURABILITY | `pipelines/universe_authority.py`<br>`pipelines/db_runtime.py`<br>`scripts/backend.py`<br>`tests/test_universe_authority.py` |
| P0-CANONICAL-DB-QC | P0 | database.canonical<br>validator.canonical-db-qc<br>review.media | P0-UNIVERSE-AUTHORITY | `pipelines/canonical_db_qc.py`<br>`pipelines/run_daily.py`<br>`tests/test_canonical_db_qc.py` |
| P0-CANONICAL-PRINTING-MATERIALIZATION | P0 | normalizer.identity<br>review.identity<br>database.canonical | T2-GEMRATE-ALIAS-DURABILITY, P0-CANONICAL-DB-QC | `pipelines/db_runtime.py`<br>`scripts/backend.py`<br>`config/data-routing.json`<br>`docs/CANONICAL_PRINTING_MATERIALIZATION.md`<br>`tests/test_printing_materialization.py`<br>`tests/test_backend_cli_contract.py` |
| P0-GENERATION-QC | P0 | validator.public-qc<br>review.media<br>export.sanitized-snapshot | P0-UNIVERSE-AUTHORITY | `pipelines/public_snapshot_qc.py`<br>`pipelines/verify_images.py`<br>`tests/test_public_snapshot_qc.py` |
| P0-OFFICIAL-PUBLISHER | P0 | validator.public-qc<br>publisher.official<br>state.last-good | P0-GENERATION-QC | `pipelines/publish-snapshot.mjs`<br>`pipelines/ensure_std_card_images.py`<br>`pipelines/native_image_refetch.py`<br>`pipelines/canvas_normalize_backfill.py`<br>`scripts/bake_publish_pack.py`<br>`scripts/db_fill_until_green.py`<br>`tests/test_legacy_image_backfill_containment.py` |
| P1-TRUTHFUL-FRONTEND | P1 | export.sanitized-snapshot | P0-GENERATION-QC | `packages/market-data/src`<br>`apps/web/src` |
| P1-MACHINE-PROJECT-STATE | P1 | status.machine | P0-REGISTRY-SCHEMA | `scripts/render_project_state.py`<br>`PROJECT_STATE.md`<br>`docs/archive/PROJECT_STATE_PRE_QC_20260729.md`<br>`tests/test_project_state_renderer.py` |
| W1-OBSERVATION-KIND-EXPANSION | P2 | collector.snk-exact<br>database.canonical | — | `pipelines/g10_snkrdunk_grades_ingest.py`<br>`pipelines/c11_pc_sold_ingest.py`<br>`pipelines/ebay_sold_data.py` |
| W2-IDENTITY-STRENGTHENING-FIELDS | P2 | normalizer.identity<br>database.canonical | W1-OBSERVATION-KIND-EXPANSION | `pipelines/migrations`<br>`pipelines/db_runtime.py`<br>`config/data-routing.json` |
| W3-CARD-CERT-REGISTRY | P2 | normalizer.identity<br>database.canonical | W2-IDENTITY-STRENGTHENING-FIELDS | `pipelines/migrations`<br>`docs/DATA_CONTRACT.md`<br>`config/data-routing.json` |
## Route lineage

| Snapshot field | Metric | DB target | Importer | Collector | Authority / transport | Manuals | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cards.<id>.identityStatus<br>cards.<id>.collectorNumber<br>cards.<id>.names<br>cards.<id>.sets | candidate_identity | `catalog_printing_identity + catalog_source_identity + catalog_provider_identity_alias + market_identity_review_queue` | canonical-db | candidate-gemrate-backfill | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api_identity_receipt&#x27;, &#x27;alternates&#x27;: [&#x27;gemrate_public_exact_card_page_receipt&#x27;, &#x27;grade10_gemrate_mirror_identity_evidence&#x27;], &#x27;discoveryOnly&#x27;: [&#x27;gemrate_structured_search&#x27;, &#x27;gemrate_universal_search&#x27;], &#x27;directRoute&#x27;: &#x27;/cards/{gemrate_id}/population?parsed_description=true&#x27;, &#x27;publicCardPageRoute&#x27;: &#x27;/card/{gemrate_id}&#x27;, &#x27;mirrorRoute&#x27;: &#x27;price.getGradingPopulations&#x27;, &#x27;promotionRule&#x27;: &#x27;exact GemRate and exact SNK bindings are necessary but not sufficient; all seven printing fields (tcgCode, cardLanguage, setName, collectorNumber, editionCode, parallelCode, finishCode), the QC card evidence hash and content-addressed field-level source receipts must match before canonicalization; cardLanguage is identity-bearing and separates otherwise identical JA/EN printings; ranking boards may remain language-combined&#x27;} | gemrate-source, backend-runbook | candidate-backfill, gemrate-transport |
| cards.<id>.populationPsa10<br>cards.<id>.graderPopulations.PSA | psa10_population | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api&#x27;, &#x27;alternates&#x27;: [&#x27;gemrate_public_card_page&#x27;, &#x27;grade10_getGradingPopulations_mirror&#x27;], &#x27;publicCardPageRoute&#x27;: &#x27;/card/{gemrate_id}&#x27;, &#x27;deprecatedPublicCardDetailsRoute&#x27;: &#x27;/card-details?gemrate_id={gemrate_id}&#x27;, &#x27;mirrorRoute&#x27;: &#x27;price.getGradingPopulations&#x27;, &#x27;mirrorCollector&#x27;: &#x27;integrations/grade10/grade10_scraper.py&#x27;, &#x27;mirrorScope&#x27;: &#x27;current population only&#x27;, &#x27;sameDayPolicy&#x27;: &#x27;require_equal_value_or_fail_run_for_direct_and_mirror&#x27;, &#x27;differentDayPolicy&#x27;: &#x27;direct_then_public_card_page_then_mirror_with_transport_provenance&#x27;} | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.graderPopulations.PSA.topGradePopulationChangePct | psa10_population_history | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api&#x27;, &#x27;alternate&#x27;: None, &#x27;mirrorScope&#x27;: &#x27;not available for historical population&#x27;} | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.pricePsa10<br>cards.<id>.historyDaily | psa10_reference_price | `market_price_observation` | canonical-db | canonical-db-qc | cardz_exact_psa10_cross_source_qc / cardz_cross_source_qc | data-contract, backend-runbook | canonical-db-qc |
| cards.<id>.windows.1d.trackedSales<br>cards.<id>.windows.7d.trackedSales<br>cards.<id>.windows.30d.trackedSales<br>cards.<id>.historyDaily | tracked_sales | `market_daily_sales_aggregate` | canonical-db | snk-reference-price | cardz_exact_psa10_sales_qc / snk_grade10_pricecharting_exact_psa10_sales | snk-manual, backend-runbook | snk-exact-refill, daily-orchestration |
| cards.<id>.windows.1d.changePct<br>cards.<id>.windows.7d.changePct<br>cards.<id>.windows.30d.changePct | price_change_1d_7d_30d | `market_candidate_daily_snapshot` | canonical-db | ranking-alerts | cardz_daily_reference_price_derivation / cardz_derived | data-contract, backend-runbook | market-alerts |
| cards.<id>.marketCap<br>cards.<id>.rank | psa10_market_cap | `market_candidate_daily_snapshot` | canonical-db | ranking-derivation | cardz_psa10_market_cap_formula / cardz_derived | data-contract | ranking-derivation |
| cards.<id>.graderPopulations | grader_supply_psa_bgs_cgc_sgc_tag | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / gemrate | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.stories | card_market_story | `card_market_story` | canonical-db | canonical-sync | cardz_editorial / cardz_editorial | data-contract, backend-runbook | canonical-sync |
| currencies | fx_usd_display_rates | `fx_rate_observation` | canonical-db | canonical-db | frankfurter / frankfurter | data-contract, backend-runbook | canonical-sync |

## Manuals

| ID | Path | Document status | Execution from prose | Purpose |
| --- | --- | --- | --- | --- |
| backend-runbook | `docs/RUNBOOK.md` | `active` | denied | operator commands, checkpoint and rollback |
| data-contract | `docs/DATA_CONTRACT.md` | `active` | allowed | canonical data and snapshot contract |
| canonical-printing | `docs/CANONICAL_PRINTING_MATERIALIZATION.md` | `active` | allowed | content-addressed printing approval, candidate, transaction and rollback contract |
| data-cleaning-rules | `docs/DATA_CLEANING_RULES.md` | `active` | allowed | raw retention, normalization, canonical fact and derivation rules |
| gemrate-source | `pipelines/GEMRATE_SOURCE.md` | `active` | denied | GemRate authority and transports |
| grade10-operator | `integrations/grade10/OPERATOR_GUIDE.md` | `reference_only` | denied | Grade10 bootstrap and mirror operation |
| snk-manual | `docs/SNKRDUNK_API_MANUAL.md` | `active` | denied | exact SNK price/history collection |
| provider-api-index | `docs/PROVIDER_API_INDEX.md` | `reference_only` | denied | agent reference index for SNK / PriceCharting / TCGplayer manuals |
| pricecharting-manual | `docs/PRICECHARTING_API_MANUAL.md` | `reference_only` | denied | PriceCharting current price CSV/API + product-page VGPC history/sales |
## Presentation views

| ID | Ranks | Meaning |
| --- | --- | --- |
| top100 | 1-100 | Compatibility array for a QC-filtered Verified Top N; it claims Verified Top 100 only when all 100 pass. |
| top300 | 1-300 | Public Top 300 projection of the canonical ranking. |
| top300_boards | combined 1-300 union pokemon 1-100 union one-piece 1-100 | Public multi-board union, deduplicated by canonical variant and capped at 500 cards. |
| top350 | 1-350 | Operator view containing Top 300 plus reserve50. |
| top100_plus_200 | 1-300 | Compatibility alias for ranks 1-100 leaders plus ranks 101-300 watch candidates. |
| reserve50 | 301–350 | Private reserve projection; never emitted in a public snapshot. |

## Consumers

| ID | Type | Reads |
| --- | --- | --- |
| manual_operator | manual | profiles, tools, routes |
| automated_tests | test | routes, tools, profiles |
| canonical_mysql | database | routes, tools, storagePolicy, universePolicy |
| sanitized_snapshot | snapshot | rankingPolicy, presentationViews, publicReleasePolicy, routes |
