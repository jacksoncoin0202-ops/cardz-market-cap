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
| grade10-bootstrap | discovery | `integrations/grade10/run_service.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook, data-contract | registry-lineage |
| candidate-gemrate-backfill | discovery | `pipelines/gemrate_candidate_backfill.py` | 14400s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, grader_supply_psa_bgs_cgc_sgc_tag | gemrate-source, backend-runbook | candidate-backfill, gemrate-transport |
| exact-crosswalk | discovery | `pipelines/source_crosswalk.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook, data-contract | registry-lineage |
| tracked-universe | discovery | `pipelines/tracked_universe.py` | 1800s | windows, linux, aws | candidate_identity | backend-runbook | daily-orchestration |
| gemrate-population | collect | `pipelines/gemrate_source.py` | 1800s | windows, linux, aws | psa10_population, psa10_population_history, grader_supply_psa_bgs_cgc_sgc_tag | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| snk-exact-refill | collect | `pipelines/snkrdunk_bulk.py` | 14400s | windows, linux, aws | psa10_reference_price, tracked_sales | snk-manual, backend-runbook | snk-exact-refill |
| snk-reference-price | collect | `pipelines/snk_market_data.py` | 1800s | windows, linux, aws | psa10_reference_price, tracked_sales | snk-manual, backend-runbook | snk-exact-refill, daily-orchestration |
| ebay-sold-normalizer | collect | `pipelines/ebay_sold_data.py` | 1800s | windows, linux, aws | tracked_sales, psa10_reference_price | data-contract, backend-runbook | ebay-normalizer |
| canonical-sync | normalize | `pipelines/market_source_sync.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, psa10_reference_price, tracked_sales, grader_supply_psa_bgs_cgc_sgc_tag, card_market_story | data-contract, backend-runbook | canonical-sync |
| canonical-db | ingest | `pipelines/db_runtime.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_population_history, psa10_reference_price, tracked_sales, grader_supply_psa_bgs_cgc_sgc_tag, card_market_story, fx_usd_display_rates | data-contract, backend-runbook, aws-handoff | canonical-sync |
| ranking-derivation | derive | `pipelines/ranking_derivation.py` | 1800s | windows, linux, aws | psa10_market_cap | data-contract | ranking-derivation |
| ranking-alerts | derive | `pipelines/market_alerts.py` | 1800s | windows, linux, aws | price_change_1d_7d_30d, psa10_market_cap | data-contract, backend-runbook | market-alerts |
| coverage-audit | audit | `pipelines/data_coverage_audit.py` | 1800s | windows, linux, aws | candidate_identity, psa10_population, psa10_reference_price, psa10_market_cap | data-contract, backend-runbook | coverage-audit |
| snapshot-export | export | `pipelines/canonical_public_snapshot.py` | 1800s | windows, linux, aws | psa10_population, psa10_reference_price, tracked_sales, price_change_1d_7d_30d, psa10_market_cap | frontend-handshake, data-contract | snapshot-export |
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
1. `audit` — coverage-audit.

### `weekly-candidate-refresh`

1. `preflight` — route-contract.
1. `discovery` — candidate-gemrate-backfill, exact-crosswalk.
1. `collect` — snk-exact-refill.
1. `normalize` — canonical-sync.
1. `ingest` — canonical-db.
1. `derive` — ranking-derivation, ranking-alerts.
1. `audit` — coverage-audit.

### `daily`

1. `preflight` — route-contract.
1. `discovery` — exact-crosswalk.
1. `collect` — gemrate-population, snk-reference-price, ebay-sold-normalizer.
1. `normalize` — canonical-sync.
1. `ingest` — canonical-db.
1. `derive` — ranking-derivation, ranking-alerts.
1. `audit` — coverage-audit.

### `export`

1. `audit` — coverage-audit.
1. `export` — snapshot-export.

### `snapshot-export`

1. `audit` — coverage-audit.
1. `export` — snapshot-export.

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
| normalizer.identity | normalizer | Exact canonical printing resolver | exact-crosswalk, canonical-sync | candidate_identity | — |
| crosswalk.provider-alias | normalizer | Provider alias to canonical printing | candidate-gemrate-backfill, canonical-db | candidate_identity | catalog_source_identity<br>catalog_provider_identity_alias |
| review.identity | review_queue | Identity conflict review queue | — | candidate_identity | market_identity_review_queue |
| collector.snk-exact | collector | Exact SNK PSA 10 price and trades | snk-exact-refill, snk-reference-price | psa10_reference_price, tracked_sales | — |
| database.canonical | database | Canonical MySQL-compatible facts | canonical-db | — | catalog_variant<br>catalog_source_identity<br>catalog_provider_identity_alias<br>market_grader_population_observation<br>market_price_observation<br>market_daily_sales_aggregate<br>market_candidate_daily_snapshot<br>market_index_snapshot<br>market_index_constituent |
| validator.eligibility | validator | POP, identity and price eligibility gate | coverage-audit | candidate_identity, psa10_population, psa10_reference_price, psa10_market_cap | — |
| derivation.market | derivation | Market cap, windows, sales and alerts | ranking-derivation, ranking-alerts | price_change_1d_7d_30d, psa10_market_cap, tracked_sales | — |
| ranking.scopes | derivation | Complete TCG, Pokemon and One Piece rankings | ranking-derivation | psa10_market_cap | — |
| export.views | export | Top 100, 300, 350 and reserve projections | — | — | — |
| export.sanitized-snapshot | export | Provider-safe frontend handshake | snapshot-export | — | — |
| orchestrator.daily | orchestrator | One Python daily control flow | — | — | — |
| state.last-good | checkpoint | Atomic checkpoint and last-good generation | — | — | — |
| index.codegraph | code_index | Local CodeGraph SQLite index | — | — | — |

## Work items

| ID | Priority | Status | Architecture nodes | Depends on | Owner files |
| --- | --- | --- | --- | --- | --- |
| P0-REGISTRY-SCHEMA | P0 | completed | orchestrator.daily<br>index.codegraph | — | `pipelines/registry_lineage.py`<br>`tests/test_registry_lineage.py` |
| P0-CONTROL-PLANE-NODES | P0 | completed | orchestrator.daily<br>database.canonical<br>export.sanitized-snapshot | — | `config/data-routing.json` |
| P0-TASK-BOARD-SOURCE | P0 | completed | orchestrator.daily | P0-CONTROL-PLANE-NODES | `config/data-routing.json` |
| P0-GENERATED-DOCS | P0 | completed | index.codegraph<br>orchestrator.daily | P0-REGISTRY-SCHEMA, P0-CONTROL-PLANE-NODES | `pipelines/registry_lineage.py`<br>`docs/generated` |
| P0-GRAPH-QUERY | P0 | completed | index.codegraph<br>orchestrator.daily | P0-REGISTRY-SCHEMA | `pipelines/registry_lineage.py`<br>`pipelines/data_routing.py`<br>`scripts/backend.py` |
| P0-CODEGRAPH-ADAPTER | P0 | completed | index.codegraph | — | `package.json`<br>`package-lock.json`<br>`.gitignore` |
| T2-GEMRATE-ALIAS-DURABILITY | P0 | completed | crosswalk.provider-alias<br>database.canonical<br>review.identity | P0-CONTROL-PLANE-NODES | `pipelines/db_runtime.py`<br>`pipelines/migrations/010_provider_identity_alias.mysql.sql`<br>`tests/test_db_runtime.py` |
| T3-GEMRATE-CANDIDATE-CLASSIFICATION | P0 | in_progress | discovery.candidates<br>transport.gemrate-public<br>normalizer.identity<br>review.identity | T2-GEMRATE-ALIAS-DURABILITY | `pipelines/gemrate_source.py`<br>`pipelines/gemrate_candidate_backfill.py`<br>`data/runtime/private-source-runs` |
| T4-SNK-EXACT-PRICE-HISTORY | P0 | planned | crosswalk.provider-alias<br>collector.snk-exact<br>database.canonical | T3-GEMRATE-CANDIDATE-CLASSIFICATION | `pipelines/snkrdunk_bulk.py`<br>`pipelines/snk_market_data.py`<br>`pipelines/source_crosswalk.py` |
| T5-RANKING-VIEWS | P0 | planned | validator.eligibility<br>derivation.market<br>ranking.scopes<br>export.views | T4-SNK-EXACT-PRICE-HISTORY | `pipelines/ranking_derivation.py`<br>`pipelines/canonical_public_snapshot.py` |
| T6-DAILY-RECOVERY | P0 | planned | orchestrator.daily<br>state.last-good | T5-RANKING-VIEWS | `scripts/backend.py`<br>`pipelines/run_daily.py`<br>`docs/RUNBOOK.md` |
## Route lineage

| Snapshot field | Metric | DB target | Importer | Collector | Authority / transport | Manuals | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| cards.<id>.identityStatus<br>cards.<id>.collectorNumber<br>cards.<id>.language<br>cards.<id>.names<br>cards.<id>.sets | candidate_identity | `catalog_source_identity + catalog_provider_identity_alias + market_identity_review_queue` | canonical-db | candidate-gemrate-backfill | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api_identity_receipt&#x27;, &#x27;alternates&#x27;: [&#x27;gemrate_public_exact_card_page_receipt&#x27;, &#x27;grade10_gemrate_mirror_identity_evidence&#x27;], &#x27;discoveryOnly&#x27;: [&#x27;gemrate_structured_search&#x27;, &#x27;gemrate_universal_search&#x27;], &#x27;directRoute&#x27;: &#x27;/cards/{gemrate_id}/population?parsed_description=true&#x27;, &#x27;publicCardPageRoute&#x27;: &#x27;/card/{gemrate_id}&#x27;, &#x27;mirrorRoute&#x27;: &#x27;price.getGradingPopulations&#x27;, &#x27;promotionRule&#x27;: &#x27;only an exact crosswalk may confirm a canonical printing; public card_number is provider evidence and may be short or blank; mirror and discovery evidence cannot bind or rebind a canonical printing&#x27;} | gemrate-source, backend-runbook | candidate-backfill, gemrate-transport |
| cards.<id>.populationPsa10<br>cards.<id>.graderPopulations.PSA | psa10_population | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api&#x27;, &#x27;alternates&#x27;: [&#x27;gemrate_public_card_page&#x27;, &#x27;grade10_getGradingPopulations_mirror&#x27;], &#x27;publicCardPageRoute&#x27;: &#x27;/card/{gemrate_id}&#x27;, &#x27;deprecatedPublicCardDetailsRoute&#x27;: &#x27;/card-details?gemrate_id={gemrate_id}&#x27;, &#x27;mirrorRoute&#x27;: &#x27;price.getGradingPopulations&#x27;, &#x27;mirrorCollector&#x27;: &#x27;integrations/grade10/grade10_scraper.py&#x27;, &#x27;mirrorScope&#x27;: &#x27;current population only&#x27;, &#x27;sameDayPolicy&#x27;: &#x27;require_equal_value_or_fail_run_for_direct_and_mirror&#x27;, &#x27;differentDayPolicy&#x27;: &#x27;direct_then_public_card_page_then_mirror_with_transport_provenance&#x27;} | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.graderPopulations.PSA.topGradePopulationChangePct | psa10_population_history | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / {&#x27;preferred&#x27;: &#x27;gemrate_direct_api&#x27;, &#x27;alternate&#x27;: None, &#x27;mirrorScope&#x27;: &#x27;not available for historical population&#x27;} | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.pricePsa10<br>cards.<id>.historyDaily | psa10_reference_price | `market_price_observation` | canonical-db | snk-exact-refill | snk_exact_psa10_history / snk | snk-manual, backend-runbook | snk-exact-refill |
| cards.<id>.windows.1d.trackedSales<br>cards.<id>.windows.7d.trackedSales<br>cards.<id>.windows.30d.trackedSales<br>cards.<id>.historyDaily | tracked_sales | `market_daily_sales_aggregate` | canonical-db | snk-reference-price | snk_exact_recent_trades / snk_recent_trades | snk-manual, backend-runbook | snk-exact-refill, daily-orchestration |
| cards.<id>.windows.1d.changePct<br>cards.<id>.windows.7d.changePct<br>cards.<id>.windows.30d.changePct | price_change_1d_7d_30d | `market_candidate_daily_snapshot` | canonical-db | ranking-alerts | cardz_daily_reference_price_derivation / cardz_derived | data-contract, backend-runbook | market-alerts |
| cards.<id>.marketCap<br>cards.<id>.rank | psa10_market_cap | `market_candidate_daily_snapshot` | canonical-db | ranking-derivation | cardz_psa10_market_cap_formula / cardz_derived | data-contract | ranking-derivation |
| cards.<id>.graderPopulations | grader_supply_psa_bgs_cgc_sgc_tag | `market_grader_population_observation` | canonical-db | gemrate-population | gemrate / gemrate | gemrate-source, backend-runbook | gemrate-transport, daily-orchestration |
| cards.<id>.stories | card_market_story | `card_market_story` | canonical-db | canonical-sync | cardz_editorial / cardz_editorial | data-contract, backend-runbook | canonical-sync |
| currencies | fx_usd_display_rates | `fx_rate_observation` | canonical-db | canonical-db | frankfurter / frankfurter | data-contract, backend-runbook, aws-handoff | canonical-sync |

## Manuals

| ID | Path | Purpose |
| --- | --- | --- |
| backend-runbook | `docs/RUNBOOK.md` | operator commands, checkpoint and rollback |
| data-contract | `docs/DATA_CONTRACT.md` | canonical data and snapshot contract |
| data-cleaning-rules | `docs/DATA_CLEANING_RULES.md` | raw retention, normalization, canonical fact and derivation rules |
| frontend-handshake | `docs/FRONTEND_HANDSHAKE.md` | sanitized frontend fields only |
| aws-handoff | `docs/AWS_HANDOFF.md` | Linux/AWS restore and scheduler handoff |
| gemrate-source | `pipelines/GEMRATE_SOURCE.md` | GemRate authority and transports |
| grade10-operator | `integrations/grade10/OPERATOR_GUIDE.md` | Grade10 bootstrap and mirror operation |
| snk-manual | `docs/SNKRDUNK_API_MANUAL.md` | exact SNK price/history collection |
## Presentation views

| ID | Ranks | Meaning |
| --- | --- | --- |
| top100 | 1-100 | Public headline projection of the canonical ranking. |
| top300 | 1-300 | Public Top 300 projection of the canonical ranking. |
| top350 | 1-350 | Operator view containing Top 300 plus reserve50. |
| top100_plus_200 | 1-300 | Compatibility alias for ranks 1-100 leaders plus ranks 101-300 watch candidates. |
| reserve50 | 301–350 | Private reserve projection; never emitted in a public snapshot. |

## Consumers

| ID | Type | Reads |
| --- | --- | --- |
| manual_operator | manual | profiles, tools, routes |
| automated_tests | test | routes, tools, profiles |
| canonical_mysql | database | routes, tools, storagePolicy |
| sanitized_snapshot | snapshot | rankingPolicy, presentationViews, routes |
