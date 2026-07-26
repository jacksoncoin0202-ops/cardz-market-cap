# Build one canonical CARDZ market database with configurable ranking views

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `C:/Users/jackson0202/Documents/Playground/.agent/PLANS.md`.

## Purpose / Big Picture

Build the independent `cardz-market-cap` repository into an art-market-first, data-credible product backed by one canonical fact database and a repeatable daily incremental publisher. The broad local Grade10 catalogue is discovery/bootstrap evidence, not an instruction to collect every card forever. Combined TCG, Pokémon, and One Piece are ranking scopes over the same canonical printings and observations. Top 100, Top 300, Top 350, `Top 100 + 200`, and reserve 50 are presentation views over those complete rankings; they are not database storage boundaries and must never duplicate card facts. Multilingual crawling is explicitly deferred.

The legacy `cardz-platform` and `grade10-scraper` trees remain read-only inputs. The standalone product keeps immutable private run files as replay evidence, imports them idempotently into its own MySQL-compatible canonical database, and exports sanitized versioned snapshots from the same normalized contract. A repository clone must be able to restore the private bootstrap bundle, create the database, replay every accepted observation, and resume the daily incremental runner without JLP. JLP remains a possible future CardzGame integration target, not a current dependency or release gate. Replay SQLite files remain test artifacts only. Cloudflare serves sanitized versioned generations, and failed refreshes keep the last successful pointer.

## Workspace Target

- Target project: `C:/Users/jackson0202/Documents/Playground/cardz-market-cap`
- Read-only G10 source: `C:/Users/jackson0202/Documents/Playground/grade10-scraper`
- Read-only legacy source: `C:/Users/jackson0202/Documents/Playground/cardz-platform`
- Rules reviewed: workspace `AGENTS.md`, workspace `.agent/PLANS.md`, this project's `README.md`, and `package.json`
- Validation: `npm run lint`, `npm run typecheck`, `npm run test`, `npm run test:data`, `npm run images:verify`, `npm run build`, `npm run verify:public`
- Recovery: never mutate either source tree; publish a versioned snapshot before changing `latest.json`; preserve the old Worker and previous generation for rollback.

## Progress

- [x] (2026-07-24) Installed `@colbymchenry/codegraph` 1.5.0 as a local read-only SQLite/WAL code index, added reproducible npm commands, and verified the index against 172 repository files.
- [x] (2026-07-24) Added the architecture DAG and bounded task DAG to `config/data-routing.json`, exposed node/task reverse lookup through `backend.py explain` and `work-items`, generated the self-contained control-plane diagram/task board, and added drift/cycle/reference tests.
- [x] (2026-07-24) Upgraded `config/data-routing.json` into the single executable backend registry covering storage policy, ranking scopes, presentation views, metrics, tools, profiles, database destinations, and public consumers. Manual/test lineage metadata is the remaining registry polish.
- [x] (2026-07-24) Added `backend.py registry`, `explain`, `graph`, and generated-document checks; removed Top 100/300/350 as canonical storage gates while preserving view-level publication requirements.
- [x] (2026-07-24) Completed the GemRate direct/public-card-details/Grade10-mirror transport order, daily orchestration, pinned Linux/Windows Playwright dependency, normalized current receipts, canonical ingest, and regression fixtures.
- [x] (2026-07-24) Reordered daily execution so strict source/ranking coverage is evaluated after the current run collects and ingests repairable evidence; injected-failure regression preserves checkpoint and last-good.
- [x] (2026-07-24) Made canonical ranking export produce configurable Top 100/300/350, `Top 100 + 200`, and reserve-50 views from one complete ranking generation.
- [x] (2026-07-24) Superseded the transitional v3 rule that treated exactly 350 cards as canonical DB completeness. Rank 1–300 is public, 301–350 is a private presentation/reserve view, and neither range caps canonical storage.
- [x] (2026-07-24) Superseded the transitional all-three-exact-350 database gate. Coverage is now checked only for the requested presentation view; underfilled facts remain ingestible and auditable.
- [ ] (2026-07-24) Finish the current-facts collection control plane: backfill/classify the broad G10 + GemRate candidate base, collect exact SNK PSA 10 prices for resolved DB candidates, admit only PSA 10 POP >=1000 to rankings, and keep POP 971–999 in the pre-entry pool.
- [x] (2026-07-24) Repaired the `full-backfill` control flow: it now performs an isolated immutable G10 bootstrap, exact crosswalk rebuild, resumable GemRate candidate classification, strict SNK refill, actual exact SNK history/trade pull, and a private candidate-universe overlay before canonical daily normalize/import/derive/audit. The candidate-only SNK run is used only to validate and build the overlay; the final daily collection always refreshes the complete merged universe. Source-transport partials fail closed; classified unavailable/review rows stay as retry evidence and cannot promote, but never discard independently verified facts. Public-card collection remains explicit so a second live collector is never started by default.
- [x] (2026-07-24) Added executable data-cleaning rules: raw provider landing remains immutable/private, normalized observations carry typed fields plus a hash/pointer, canonical facts remain idempotent, and market snapshots are rebuildable. The registry validates the rule file and exposes it through `backend.py explain data_cleaning`.
- [x] (2026-07-24) Added the keyless GemRate public-card receipt crosswalk: new exact opaque-ID page evidence now produces either an exact canonical proposal or a reasoned review queue entry. It never selects a name-search result, completes a short collector number, or guesses language/edition/finish.
- [x] (2026-07-24) Defined the durable GemRate alias crosswalk: requested/entity/universal/grader-member/spec IDs are provider aliases anchored to one exact canonical printing, persisted privately with receipt provenance, and never become CARDZ IDs or public fields. Exact `snkItemId` is a separate source identity on the same canonical variant; unresolved or conflicting alias evidence remains in the review queue.
- [ ] Complete the real source expansion and MySQL run. Current local evidence remains underfilled, especially One Piece, so no v3 generation, seed, or public snapshot may replace last-good yet.

- [x] (2026-07-22 15:44+09:00) Audited the technical document, the complete 600-card G10 dataset, current scraper/analytics defects, JLP schema direction, clean repo, and live UI drift.
- [x] (2026-07-22 15:44+09:00) Replaced the obsolete SNK-first, 30d-only, writable-canonical-SQLite plan; the later 2026-07-23 source audit further supersedes G10-first authority.
- [x] (2026-07-22 16:03+09:00) Captured the live 1440 x 900 visual baseline and the supplied Figma DESIGN.md reference into `.agent/visual-audit/`, then recorded the specific comparison gates.
- [x] (2026-07-22 16:12+09:00) Added the isolated Cloudflare staging/production configuration, hardened headers, private-R2/public-leak verification, CI release gates, and security/runbook documentation; deployment remains intentionally pending the integrated v2 snapshot.
- [x] (2026-07-22 16:49+09:00) Moved the obsolete schema-1 SQLite copies out of `data/private` into the ignored, recoverable `data/runtime/private-quarantine/legacy-clean-room/` so the clean repository cannot mistake them for canonical authority.
- [x] (2026-07-22 18:27+09:00) Added fail-closed generation-canary and staging-only pointer-promotion hooks, with explicit S4U task injection for the HTTPS canary origin and absolute hook command arrays. Fake-command tests prove stale-receipt removal, baseline-change refusal, production refusal, and receipt-after-success only.
- [x] (2026-07-22 18:50+09:00) Froze and hashed the complete 600-card G10 generation, added immutable full/incremental landing replay, and kept all provider payloads outside public and Git-tracked paths.
- [x] (2026-07-22 18:50+09:00) Added the MySQL 5.7-compatible additive market schema, idempotent observation import, corrected sale semantics, grader population observations, and replay/order tests.
- [x] (2026-07-22 18:50+09:00) Produced sanitized demo generation `daily_20260722T094826496874Z`: 100 ranked cards, 260 watchlist cards, complete collector-number resolver including `023/MEP`, 360 hash-valid raw fronts, and explicit identity/image review blockers.
- [x] (2026-07-22 19:16+09:00) Implemented the shared 1d/7d/30d contract, grader population-change views, truthful daily price/tracked-sales charts, pointer-authorized media, and public privacy gates.
- [x] (2026-07-22 19:16+09:00) Rebuilt the home heatmap, ranking, details, market/grader routes, locales, currencies, SEO/GEO, and responsive interactions. Reviewed editorial copy is joined by exact identity guard; 45 stories are ready and 55 remain blocked for evidence review.
- [x] (2026-07-22 19:21+09:00) Passed lint, typecheck, 34 web tests, 28 data tests, 7 hook/canary tests, non-strict verification of 360 images, Next build, public/bundle leak gates, deployment isolation, and canary/staging Wrangler dry-runs.
- [x] (2026-07-22 19:54+09:00) Created and pushed the clean Private GitHub repository, deployed isolated canary and staging Workers, uploaded and read-back-verified all 360 R2 images, promoted generation `daily_20260722T094826496874Z` only after a generation-scoped live canary, and verified build `30e50fbba5c1` on every public route.
- [x] (2026-07-22 19:54+09:00) Removed all market media and local design artifacts from the Cloudflare static bundle. Live authorized media now resolves only through the active-pointer Worker route with the expected SHA-256 and cache policy; an unlisted hash and the local mockup route both return 404.
- [x] (2026-07-22 20:15+09:00) Passed the clean-clone GitHub CI on commit `d2d660a`, then completed 1440/960/768/390 live visual QA. The shared selector, ordered 100-card layout, hover/focus preview, mobile sheet, scroll target, object-contain images, and zero-horizontal-overflow checks pass. QA also exposed and prompted fixes for tile-link RSC prefetch amplification, the missing icon, and a mobile hero taller than the available viewport.
- [x] (2026-07-22 20:27+09:00) Deployed build `ce07aa09ae2a` through the isolated canary and staging Workers and reran live regression. At 390 x 844 the 121 px header plus 724 px hero matches the viewport within one pixel and keeps the ranking CTA visible; detail-route RSC prefetch fell from 100-card fan-out to zero. The 1440 layout, icon route, 100 ordered tiles, no-overflow check, console, and all eight public route canaries pass.
- [x] (2026-07-22 23:18+09:00) Added a private, daily USD FX observation adapter with JPY support, validated/atomic caching, 72-hour last-good recovery, a replaceable self-host endpoint, additive JLP FX migration, public freshness metrics, and the corresponding six-currency contract/tests. A live run collected all six rates dated 2026-07-22.
- [x] (2026-07-23 01:10+09:00) Re-scoped the current backend as standalone: JLP is deferred future integration and no longer a production blocker. Audited the new GemRate and SNK scripts, their private outputs, their absence from `run_daily.py`, and their exact overlap with the 600-card G10 universe.
- [x] (2026-07-23 01:10+09:00) Proved the raw SNK PSA 10 route live on one mapped card: 922 daily points from 2023-06-19 through 2026-07-22 plus 20 recent trades. The raw 30d close-to-close result differs materially from the copied G10 30d percentage, so CARDZ must derive its own windows.
- [x] (2026-07-23) Replaced all-600 daily collection with a deterministic bounded selector. After correcting asset language authority, the immutable lock contains 400 active printings: `one-piece:en=53`, `one-piece:ja=42`, `pokemon:en=25`, `pokemon:ja=279`, and `pokemon:zhCN=1`; the other supported segments remain explicit zero-coverage markets. Every segment is capped at 300 and only active exact IDs flow to daily GemRate/SNK collectors.
- [x] (2026-07-23) Added Korean Pokémon as an explicit card-language market, required Hangul card/set metadata, added KRW to the validated FX/public contract, and excluded Thai card markets. Current bootstrap coverage contains no Korean printing, so live Korean data acquisition remains a source-coverage milestone rather than fabricated fallback content.
- [x] (2026-07-23) Integrated TAG into the same bounded daily parent run as G10, GemRate and SNK. TAG capture is resumable and atomic, exact identity matching is fail-closed, ambiguous rows enter a private review queue, and TAG total/top-grade populations replay through the canonical observation contract.
- [x] (2026-07-23) Froze immutable lock `universe_20260722T181950Z_ad4c866a2786`, corrected the dated G10 bootstrap, and built a verified Git-LFS active-only archive containing three necessary canonical batches. Added standalone MySQL migrations/import/status plus one cross-platform `scripts/backend.py` entrypoint for Windows, Linux, local Docker, EC2, and RDS. The broad 600-card discovery tree and undated derived points do not enter the portable archive.
- [x] (2026-07-23) Rebuilt local MySQL from the active-only archive, proved a second import adds zero observations, and completed one real backend daily canary: frozen active lock 400, SNK 339/339, 9,096 daily-price observations, 2,608 sale observations, 12,101 new canonical observations, and final 400/400 catalog-to-universe integrity. Rebuilt and verified the bootstrap archive with the new daily batch (four canonical batches, eight files).
- [x] (2026-07-23) Hardened auxiliary TAG failure isolation after the live upstream catalog failed on incomplete 1997 set identity. TAG last-good now requires a persisted `capturedAt` within 72 hours, matching catalog SHA-256, a non-future observation date, and a valid object manifest; otherwise TAG is omitted while SNK and database sync continue. Windows and WSL fallback/launcher tests pass.
- [x] (2026-07-23) Vendored the exact seven-file Grade10 acquisition/operator dependency with a SHA-256 manifest, added the repository-owned cross-platform Python service wrapper, and verified it on Windows Python 3.10 and WSL/Linux Python 3.12 without invoking the sibling checkout. Runtime payloads remain ignored and no secret was copied.
- [x] (2026-07-23) Integrated fail-closed broad discovery before every portable daily run and persisted a deterministic alert evaluation bound to the discovery evidence. The current live radar accounts for all 600 index identities (`400` active, `200` outside), retains `15` unavailable-price identities as unavailable rather than rejecting them, and blocks a completeness claim while `58` high-potential outside identities remain unresolved.
- [ ] Commit and push the vendored integration, systemd units, tests, and Git-LFS bootstrap archive as one scoped portability change, then prove a fresh GitHub clone can bootstrap without either local sibling checkout. The current worktree passes Windows/Linux validation, but untracked files are not clone-reachable evidence.
- [ ] Extend the broad discovery lane beyond the current Grade10 index when an exact Korean/new-release feed is available, resolve the 58 high-potential identities, and only then change alert coverage from `blocked` to a stronger claim.
- [ ] Package only independently QC-passed raw-front assets and their manifests for the locked universe. Media remains outside the database bootstrap until semantic and rights gates pass; no slab or unlicensed bulk image source may be smuggled into the archive.
- [ ] Complete a production-equivalent local soak: restore from a clean checkout, replay the bootstrap without duplicates, run at least two scheduled daily increments, preserve last-good on an injected failure, and prove restart/recovery without manual data repair.
- [ ] Prove one unattended daily publish canary. Task registration has since succeeded: `CARDZ-Market-Cap-Daily` runs at **09:30 JST (00:30 UTC)** and `CARDZ-Market-Cap-Watchdog` at **14:07 JST (05:07 UTC)**. The trigger must not be moved back to 06:30 JST — `pipelines/run_daily.py` derives `market_run_id` from the UTC date, so a 06:30 JST trigger lands on the previous UTC day, collectors replay, and the chain exits zero with no new data (the 2026-07-25 silent failure). What remains unproven is the publish leg itself, which is still gated on Task Board #4.
- [ ] Integrate exact GemRate/SNK crosswalk generation, raw daily collection, standalone ranking derivation, and last-good publication into one unattended job; then cut production after staging approval. JLP is explicitly out of this milestone.
- [x] Add a payload-aware coverage audit that distinguishes provider mappings from locally verified GemRate/SNK observations, derives only evidence-backed market caps, writes exact refill/quarantine queues, and fails closed for an unverified global Top 100.
- [x] Replace the language-partitioned active lock with one deduplicated union of three ranked indexes: combined TCG Top 300, Pokémon Top 300, and One Piece Top 300. Preserve printing language only as identity metadata; do not create language quotas or language-specific collection jobs.
- [x] (2026-07-23 22:23+09:00) Completed a real tracked-universe daily canary. It replayed 336/336 exact SNK cards, normalized 8,977 daily price points and 2,580 recent tracked sales, imported 11,557 new observations, evaluated 395 candidates, and finished `backend-ready`. Same-day alert evaluation now stores immutable input-hash revisions while exact replays remain idempotent.
- [x] (2026-07-23) Added a machine-readable data-routing contract before expanding discovery. PSA 10 population and population history are GemRate-only authorities with no ranking fallback; price, tracked sales, derived windows, market cap, grader supply and FX each name one collector, freshness rule, database target and failure behavior.
- [x] (2026-07-23) Revised the routing contract after the source-origin audit: G10 is bootstrap/research evidence only; GemRate owns identity and grader population; SNK owns the exact reference price; validated eBay sold data is a secondary transaction/price-validation lane; CARDZ derives the three Top 300 memberships and all time windows. The legacy eBay Browse client was explicitly rejected as a sold-price source.
- [x] (2026-07-24) Completed Wave 1 data-routing documentation: separated GemRate population authority from its direct-API and Grade10 `price.getGradingPopulations` mirror transports; locked exactly three global Top 300 outputs; and removed obsolete per-language rank wording. No runtime collector, schema, or data mutation was made in this documentation milestone.
- [x] (2026-07-24) Completed Wave 1 evidence freeze and gap audit: immutable Grade10 landing proves 600 population payloads, 595 asset identities and five explicit missing-identity quarantines; the current verified intersection is only 42 market-ready cards, so all three Top 300 promotions remain blocked instead of reusing the stale public generation.
- [x] (2026-07-24) Completed Wave 2 intake and identity contracts: Grade10 detail intake now emits explicit identity, population, story/image pointers and exact-date PSA 10 sale evidence while quarantining short numbers, relative dates and ambiguous bundles; the crosswalk derives a deterministic seven-part printing key and review queue.
- [x] (2026-07-24) Completed Wave 2 GemRate transport resolution: direct API is preferred, the exact Grade10 GemRate mirror can supply current population without a key, same-day mismatches fail closed, different-day observations retain both provenance records, and partial runs never promote.
- [x] (2026-07-24) Added replay-safe canonical provenance tables and DB materialization for printing identity, population transport, story/image pointers, exact sales and tracked-sales windows. Python regression passed 64 tests and the G10 data-layer suite passed seven tests.
- [x] (2026-07-24) Completed Wave 3 market evidence: SNK exact PSA 10 reference closes and trades retain native JPY, USD conversion, FX and fetch provenance; `used_min_price` cannot become a sale or reference price; partial or identity-mismatched runs fail closed.
- [x] (2026-07-24) Replaced the legacy eBay/Browse path with a repository-owned completed-sale adapter. It requires exact printing and PSA 10 evidence, preserves bundle quantity/unit/transaction values and independent sale identity, and permits a fallback reference only after three exact non-bundle sales in 30 days.
- [x] (2026-07-24) Added CARDZ-owned pure 1d/7d/30d and tracked-sales derivation. It refuses cross-source/method comparisons, returns accumulating/unavailable instead of false zero, and emits daily line plus sales bars without OHLC. Full Python regression passed 76 tests.
- [x] (2026-07-24) Completed Wave 4 candidate backfill and measured the real offline gap: 2,097 candidates produce 449 resolved, 119 below threshold and 1,529 unavailable records. Search counts are never promoted to PSA 10 population; the incomplete manifest remains non-promotable.
- [x] (2026-07-24) Added strict resumable SNK BFS/refill worklists for POP >= 1,000 candidates. Missing or conflicting full identity enters review, partial acquisition cannot promote, and exact replay performs no network request.
- [x] (2026-07-24) Added strict Pokémon, One Piece and deduplicated combined Top 300 derivation, membership history and radar tiers. All three indexes require 300 eligible records; an underfilled generation preserves last-good. Python regression passed 89 tests and pipeline behavior passed 11 tests.
- [x] (2026-07-24) Completed Wave 5 backend automation: the repository-owned CLI now exposes doctor, routes, full-backfill, rebuild-db, daily, audit, export-snapshot, seed-build, seed-verify, seed-restore and machine-readable status. Daily execution is backend-only by default, requires an explicit publish flag, and preserves checkpoints and last-good on partial evidence.
- [x] (2026-07-24) Added content-addressed raw-payload retention and replay-safe pointer migration without deleting canonical price, population, tracked-sales, ranking or FX history. Added equivalent Windows Task Scheduler and Linux systemd contracts that invoke the same Python daily entrypoint at 06:30 JST with singleton, timeout and retry controls. Full Python regression passed 103 tests.
- [x] (2026-07-24) Completed Wave 6 portable seed and restore tooling: deterministic canonical `SQL.gz` plus detached manifest, secret scanning, migration SHA ledger, empty-database-only restore, zero-new-observation replay canary, explicit LFS/archive verification and AWS/Linux handoff documentation. The integrated backend suite passes 119 tests; a live MySQL seed/restore and two real increments remain runtime evidence gates.
- [ ] Add an exact Korean Pokémon discovery feed and canonical mappings. It must produce Hangul identity, complete collector number, population above 100, price history, and QC-passed raw fronts before `pokemon:ko` can become a live partition.

- [ ] (2026-07-23) Replace the 600-card Grade10-only completeness assumption with a GemRate-led broad candidate discovery and exact SNK price pass for Pokémon and One Piece. Rebuild the standalone canonical database from validated observations, report membership/rank drift against the current Top 100, fill deterministic gaps, and connect the database-derived snapshot to the existing web contract. GitHub push remains explicitly prohibited until Jackson approves it.

## Surprises & Discoveries

- Observation: the local G10 source is a daily full pull plus overwrite, not a database-style incremental pipeline. Only the existing sales cache attempts accumulation.
  Evidence: `grade10_scraper.py`, `grade10_analytics.py`, and `data/_state/last_run.json`.
- Observation: the configured `Grade10-Daily-Scraper` task has never completed an automatic run; the current data was created manually.
  Evidence: Task Scheduler last-run sentinel and next run at 06:30 JST.
- Observation: current derived K-lines are not genuine OHLC; open/close are sorted low/high values, 42,630 of 45,714 rows are carried, bundle totals corrupt some unit prices, and sale dates are frequently replaced by discovery day.
  Evidence: `grade10_kline.py`, `grade10_analytics.py`, and read-only aggregates over `data/analytics` and `data/sales_cache`.
- Observation: the current 600-card universe supports PSA market cap plus PSA/BGS/CGC/SGC/TAG population views, but not five grader market-cap rankings because non-PSA price streams are incomplete and TAG currently covers Pokémon only.
  Evidence: the G10 payload covers PSA/BGS/CGC/SGC while the exact TAG daily capture matched 208 active Pokémon printings; SGC and TAG do not have a complete reference-price stream.
- Observation: the approved USD 2.624B / 89 Pokemon / 11 One Piece estimate is reproducible. Deduplicating all 600 current constituents by source identity, applying positive `priceUsd` plus PSA `topGrade >= 1000`, and sorting `priceUsd × PSA topGrade` yields 460 market-eligible cards and a Top 100 of USD 2,624,140,427 with the stated 89/11 mix. A narrower replay that accidentally omitted the alternate-source card directory produced USD 2.106B and was incorrect.
  Evidence: read-only 2026-07-22 replay across `grade10-scraper/data/index/{ptcg,ptcg100,opcg}/constituents.json` and both exact `data/cards/{snkrdunk,altxyz}/{id}/populations.json` paths.
- Observation: the initial exact identity plus raw-front public gate currently leaves 349 publishable candidates. Ranking that restricted preview pool gives USD 2,248,022,219 and a 91 Pokemon / 9 One Piece mix, so it is a different scope from the full market universe and must not silently replace the USD 2.624B benchmark.
  Evidence: `pipelines/g10_public_snapshot.py --self-test`; 111 current cards remain in the identity/image review queue.
- Observation: the G10 constituent price snapshot itself reports `updatedAt = 2026-07-19T00:00:00Z`. The 2026-07-22 scraper completion time is a fetch timestamp, not a newer price observation, so the current price generation exceeds the 48-hour production SLA.
  Evidence: `grade10-scraper/data/index/{ptcg,opcg}/summary.json` versus `data/_state/last_run.json`.
- Observation: all calculated Top 100 cards have a local image, but the pool mixes raw cards and slabs; image type cannot be inferred safely from filename or dimensions alone.
- Observation: the current live mobile page is not merely dense; at 390 px its desktop grid collapses into a narrow left strip and wraps brand, navigation, heading, body copy, and the summary card word-by-word.
  Evidence: `.agent/visual-audit/before-live-390x844.png` captured from the in-app browser.
- Observation: JLP does not exist as an available integration target for this product today. Treating its runner or owner as a current production requirement was scope drift; the current product must publish standalone snapshots and leave a future adapter seam only.
- Observation: the first G10 public-gated replay exposed repeatable identity defects rather than isolated UI typos: Japanese promo namespaces were labelled English, provider-normalized Pokémon denominators lost their leading zero, and two different Snorlax variants shared one raw image. These are now resolver/validator concerns and must not be hand-corrected in React.
- Observation: metadata proves all 360 exported images are hash-valid raw fronts with exact number/language/TCG resolver evidence, but none has yet been promoted to `human_or_vision_confirmed`. The non-strict staging gate passes and the strict production gate deliberately fails all 360.
- Observation: the current compatible Next/OpenNext/Wrangler graph resolves `sharp 0.34.5` and Next's bundled `postcss 8.4.31`; the official-registry audit reports five high and one moderate advisory. Forcing patched 0.x packages violates upstream dependency ranges, so production remains blocked instead of shipping an invalid override.
- Observation: GemRate receipt aliases previously existed only in private JSON and universal-ID dedupe treated a second requested/member ID as a rebind even where both receipts proved the same canonical printing. Per-grader spec aliases also needed the grader namespace to avoid false collisions.
  Evidence: direct receipt audit over the current public-card landing and crosswalk regression fixtures.

- Observation: the previous route contract expressed provider authority and acquisition route through the same `primary` field, while Grade10's vendored collector exposes `price.getGradingPopulations` as a possible GemRate mirror.
  Evidence: `config/data-routing.json` and `integrations/grade10/grade10_scraper.py`.
- Observation: the 2026-07-23 payload-aware audit found 600 mapped cards but only 85 locally verified GemRate population payloads, 194 exact/fresh SNK PSA 10 observations, and 42 cards with both inputs needed for market cap. Those 42 cover only `pokemon:ja=41` and `pokemon:en=1`; global Top 100 verification is therefore false.
  Evidence: `data/runtime/private-reports/data-coverage-audit.json` generated by `pipelines/data_coverage_audit.py`.
- Observation: the three-index selector produced 300 combined members, 300 Pokémon members, and 95 currently qualified One Piece members. Deduplication reduced the provider collection universe to 395 canonical printings, with 395 GemRate mappings and 336 exact SNK mappings.
  Evidence: `data/runtime/private-source-map/tracked-universe.json`.
- Observation: the first tracked-universe full-history normalization materialized 114,422 SNK daily price observations, 3,227 tracked-sales aggregates, and 196 GemRate grader-population observations for 52 locally available cards. The current MySQL lock has 395 members and a repeated import replayed all eight batches with zero new observations.
  Evidence: `data/runtime/private-landing/sources/tracked_top300_bootstrap_20260723/manifest.json` and `scripts/backend.py status`.
- Observation: the runtime previously exposed a concrete integration break: the FX collector writes `data/runtime/private-fx/latest.json` while the new audit initially looked under `data/runtime/fx/latest.json`. The shared default now points to the collector's real last-good location and is covered by the live audit.
- Observation: the first tracked-universe daily canary successfully completed live SNK acquisition and database import, then stopped because the alert table allowed only one canonical input per date. Daily collection can legitimately improve within a day, so migration 006 now keys immutable evaluations by date plus input SHA-256. The repeated canary finished `backend-ready`; exact source replays inserted zero duplicate observations.
  Evidence: `pipelines/migrations/006_alert_evaluation_revisions.mysql.sql`, evaluation 3, and daily run `daily_20260723T132338065188Z`.
- Observation: all 600 current G10 cards expose an exact GemRate ID in their private population payload, but `pipelines/gemrate_ids.txt` contains 201 IDs and only 85 intersect the 600-card universe. The harvested data covers only 29 of the current combined Top 100 and zero of its 11 One Piece cards.
  Evidence: read-only join of `data/private/gemrate/cards/*` to `grade10-scraper/data/cards/*/populations.json` through the embedded GemRate ID.
- Observation: refreshing the 85 exact GemRate overlaps changed PSA 10 population for 51 cards and increased the calculated combined Top 100 cap from USD 2,624,140,427 to USD 2,624,579,836 without changing membership. This proves population refresh can change rank values even before price refresh.
  Evidence: read-only 600-card replay using current constituent prices and 2026-07-21 GemRate PSA 10 population.
- Observation: 442 of 600 G10 cards have direct numeric SNK identities; 70 of the current combined Top 100 are direct SNK identities while 30 use the alternate-source identity and still need an exact SNK crosswalk or last-good fallback.
  Evidence: source-path join across the 500-card Pokemon and 100-card One Piece constituent files.
- Observation: one live SNK PSA 10 pull returned 922 daily points and 20 trades. Recomputing its windows produced 1d -6.59%, 7d -10.86%, and 30d -6.07%, while the existing constituent carries 30d -13.07%.
  Evidence: `data/runtime/audit/snk-psa10-116069.jsonl` generated through `trading_card_single_psa10`.
- Observation: One Piece catalog discovery is group-scoped rather than global. One promo seed returned one ID, one ordinary set seed returned 13 IDs, and all 47 existing exact One Piece SNK seeds together discovered 265 IDs.
  Evidence: live read-only `bfs_discover` calls against the current SNK catalog.
- Observation: 45 Top 100 editorial stories have printing-specific four-language evidence and 55 remain `review_required` (51 thin evidence, two summary conflicts, two identity conflicts). Missing stories remain absent rather than being filled with generic market prose.
- Observation: the final sanitized demo generation contains 100 ranked cards and 260 watchlist cards. Its public-gated Top 100 is USD 2,445,570,776 with a 93 Pokemon / 7 One Piece split; the full ungated market-universe benchmark remains USD 2,624,140,427 with the approved 89/11 split.
- Observation: the first remote publisher attempt exposed a real Windows `spawnSync npx.cmd EINVAL` failure, and the first generation canary exposed a missing private-discovery flag. Both failed before `latest.json`, were repaired with direct Node CLI invocation and explicit private discovery, and are covered by hook tests.
- Observation: copying raw fronts into `apps/web/public` allowed the static asset binding to serve a known hash without consulting the active pointer. The Cloudflare build now uses a temporary allowlisted public directory and bundles neither market images nor local design artifacts; live tests confirm pointer-authorized media headers and 404 for an unknown hash.
- Observation: Windows denied registration of the separate `CARDZ-Market-Cap-Daily-Staging` S4U task. The installer no longer risks overwriting the legacy task and correctly resolves the first Python/Node application, but an authorized Windows task-registration context is still required.
- Observation: the first GitHub clean-clone CI run correctly revealed two portability assumptions: the private sibling G10 source and Windows `where.exe`. Source-dependent replay now runs when the read-only source exists and is explicitly skipped in clean CI; Task Scheduler execution remains Windows-only while its structural contract is checked everywhere.
- Observation: live browser QA showed that the 100 visible Next links caused roughly 105 unique RSC prefetch URLs and that the 390 px hero exceeded its available viewport by about 209 px. Heatmap tile prefetch is now disabled, the hero grid uses the exact available viewport height, and the compact mobile header leaves more space for the art surface.
- Observation: the current snapshot declares HKD/CNY/GBP/TWD but emits every non-USD rate as unavailable, so adding JPY only to the selector would still leave every converted price unusable.
  Evidence: `pipelines/g10_public_snapshot.py`, `data/public/seed-snapshot.json`, and the `currency_rate_feed_pending` production blocker.
- Observation: Frankfurter v2 provides keyless, cacheable daily rates for all six CARDZ currencies, including JPY and TWD, and can be self-hosted for future server portability.
  Evidence: official `https://frankfurter.dev/` documentation and a live USD query returning CNY, GBP, HKD, JPY, and TWD observations dated 2026-07-22.
- Observation: the current 600-card G10 discovery snapshot contains 375 Japanese, 123 English, and two Simplified-Chinese Pokémon records, plus 42 Japanese, 57 English, and one Simplified-Chinese One Piece record. It contains zero Korean and zero Thai records.
  Evidence: deterministic language counts from the read-only source crosswalk. Korean is therefore a supported empty market today, not live coverage.
- Observation: exact asset language must override the broader market row language. After applying that rule, the current immutable active universe is 400 cards: `one-piece:en=53`, `one-piece:ja=42`, `pokemon:en=25`, `pokemon:ja=279`, and `pokemon:zhCN=1`; Korean and Traditional-Chinese coverage are currently empty rather than silently filled from English/Japanese records.
  Evidence: `pipelines/active_universe.py --self-test` and immutable lock hash `ad4c866a278615e9e766eb31837c735f9a0a6286dddba874417fdf584d1e166f`.
- Observation: the old G10 full canonical batch contained 397 active prices without an `observedDate`; treating its fetch time as a market date would manufacture history. A corrective dated batch now supplies 397 active prices and PSA population for all 400 locked cards, while the bootstrap archive excludes the undated rows. Canonical replay currently has price coverage for 398/400 and PSA population coverage for 400/400; the two missing prices remain unavailable.
  Evidence: active-only archive build/verify, canonical replay counts, and corrective G10 incremental batch under `data/runtime/private-landing/g10/incremental/2026-07-21/`.
- Observation: a real daily run exposed incomplete TAG set metadata for year 1997. Treating TAG as a hard dependency would have blocked 339 complete SNK price pulls. The parent now records TAG as unavailable, preserves prior database observations, and continues the primary market path; a copied file cannot refresh fallback age because filesystem mtime is no longer authoritative.
  Evidence: successful `scripts/backend.py daily --mode staging` run `daily_20260722T191906523645Z`, `tagStatus=unavailable`, `snkLive=true`, and MySQL checkpoint `2026-07-22T19:37:23.555302`.
- Observation: the Grade10 acquisition scripts are Python and already store data relative to their own directory, but the only bundled orchestration entrypoint is a Windows-only batch file with workstation paths. The supplied operator-guide attachment is byte-equivalent to the source guide apart from its final newline.
  Evidence: read-only review of `grade10_scraper.py`, `grade10_analytics.py`, `grade10_kline.py`, `run_daily.bat`, both guide SHA-256 values, and a no-index diff.
- Observation: Grade10 analytics and K-line outputs cannot be canonical CARDZ market evidence: same-day same-price sale de-duplication can collapse real transactions, relative sale dates are bucketed to fetch day, grade fallback can substitute non-PSA observations, and OHLC open/close is synthetic price ordering rather than transaction-time ordering.
  Evidence: read-only review of `grade10_analytics.py`, `grade10_kline.py`, `TECHNICAL_DOC.md`, and `DATA_GUIDE.md`.
- Observation: the vendored acquisition service and private active archive are valid in the working tree, but Git currently tracks neither `integrations/grade10/`, `deploy/systemd/`, nor `data/private/cardz-active-bootstrap.tar.gz`. A fresh GitHub clone therefore cannot reproduce this state until the scoped files and LFS pointer are committed and pushed.
  Evidence: exact seven-file manifest self-check on Windows and WSL, archive verification, `git ls-files`, `git lfs ls-files`, and current `git status`.
- Observation: the broad roster contains 15 exact identities with a zero/missing source price. Dropping them would make the discovery denominator dishonest, so the radar retains them with `priceStatus=unavailable`; unavailable never means zero market value.
  Evidence: live vendored discovery refresh and `tests/test_market_discovery.py`.

## Decision Log

- Decision: local CodeGraph SQLite is a read-only implementation index, while `config/data-routing.json` remains the only handwritten architecture and task authority. Generated diagrams and task tables may be regenerated from the registry but may never become a second source of rules.
  Rationale: static call/import evidence helps agents locate implementation impact, but it cannot define provider authority, canonical data meaning, ranking policy, or execution ownership.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: canonical storage and ranking presentation are separate layers. The database stores each printing and observation once, each market scope stores an ordered membership over those canonical facts, and Top 100/300/350, `Top 100 + 200`, and reserve 50 are configurable export views rather than ingestion limits.
  Rationale: the same ranked evidence can serve every display cut without duplicate card rows, contradictory database gates, or a rebuild when the frontend changes its visible limit.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: `config/data-routing.json` is expanded, not shadowed by a second control-plane file. Generated route matrices, tool catalogues, lineage diagrams, and operator summaries must derive from that one registry.
  Rationale: a second hand-maintained registry would recreate the exact source/script/DB/document drift this milestone is intended to eliminate.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: target-first historical collection runs in two passes. The first pass obtains exact current GemRate PSA 10 population and SNK price broadly enough to rank the market; the second pass backfills full history only for the deduplicated high-value target set and queues newly promoted cards automatically.
  Rationale: ranking correctness requires a broad current comparison, while database size and collection cost stay bounded by avoiding full history for irrelevant cards.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: `POP` means the GemRate-authoritative PSA 10 graded population. A printing enters a formal ranking only at POP >= 1000. The daily pre-entry pool is narrowly bounded to POP 971–999; lower-POP discovery evidence may be retained, but does not enter the tracked collection pool.
  Rationale: this preserves the operator's hard market-recognition threshold, gives near-threshold cards enough lead time for exact SNK pricing, and prevents an unnecessarily large daily database and scrape workload.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: GemRate requested, entity, universal, grader-member and spec IDs are provider aliases, not canonical CARDZ identities. A universal alias may have many requested/member observations only when each has exact-crosswalked to the same canonical printing; grader-member/spec alias uniqueness includes the grader namespace. Conflicts or unanchored receipts fail closed into the identity review queue. An exact SNK item ID is persisted as a separate source identity on that same variant.
  Rationale: GemRate uses distinct identifier scopes. Treating universal aliases as one-to-one printings or omitting the grader namespace would silently rebind different provider entities; retaining the evidence relation permits deterministic incremental matching without exposing provider IDs publicly.
  Date/Author: 2026-07-24, Jackson and Codex.
- Decision: G10 remains the bootstrap universe and last-good compatibility feed. GemRate becomes the daily exact population feed and SNK becomes the daily exact PSA 10 price/history/tracked-sales feed wherever a confirmed crosswalk exists. CARDZ derives rank and 1d/7d/30d itself; copied provider rank or percentage is never canonical.
  Rationale: this preserves the complete 600-card baseline while allowing raw observations to correct rank values without exposing upstream providers from Cloudflare.
  Date/Author: 2026-07-23, Jackson and Codex.
- Superseded decision: daily tracking was previously partitioned by `TCG × card language`, capped at Top 100 plus 200 candidates, and admitted POP >100. It is retained only as historical context and must not drive current collection.
  Superseded by: the 2026-07-24 three-scope canonical ranking plus POP >=1000 formal / POP 971–999 pre-entry policy.
  Date/Author: 2026-07-23, Jackson and Codex.
- Superseded decision: the three rank memberships `tcg`, `pokemon`, and `one-piece` were previously capped at 300. The three scopes remain correct, but the storage cap is obsolete.
  Superseded by: complete eligible ranking storage with configurable Top 100/300/350 presentation views.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: GemRate is the sole canonical PSA population authority. G10 and SNK population values may be retained as private comparison evidence but cannot populate the ranking dependency or act as a fallback. A missing/stale GemRate population excludes the printing from the certified ranking.
  Rationale: population is the denominator that controls market cap eligibility and must not silently change provider semantics.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: Pokémon and One Piece both support Japanese, English, Korean, Traditional Chinese, and Simplified Chinese card-language markets. Korean identity requires Hangul card/set metadata and KRW is a supported display currency. Thai printings are out of scope.
  Rationale: these are explicit operator-selected markets; English fallback would destroy printing identity, while collecting Thai would add unsupported scope.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: the combined Top 100 remains `PSA 10 reference price × PSA 10 population`; PSA/BGS/CGC/SGC are not summed.
  Rationale: only PSA currently has defensible price coverage; summing incomplete grader markets would bias rank and market cap.
  Date/Author: 2026-07-22, Codex based on audited coverage and approved plan.
- Decision: preserve both scopes explicitly: USD 2.624140427B is the traceable current market-universe Top 100 before public identity/image gates; USD 2.445570776B is the final sanitized staging subset after deterministic identity and image remediation. Production remains closed until the actual market Top 100 passes canonical identity and human/vision raw-image QC and a price observation is within the freshness SLA.
  Rationale: public QC must not change the meaning of the market benchmark, the fetch completion time cannot substitute for the price effective time, and tracked-sale averages cannot silently replace the index reference price.
  Date/Author: 2026-07-22, Codex after deterministic source replay.
- Decision: one global `1d | 7d | 30d` selector controls heatmap color, table change, and tracked-sales window. Area and rank always use current market cap.
  Rationale: preserves a stable visual hierarchy and the requested simple interface.
  Date/Author: 2026-07-22, user and Codex.
- Decision: V1 renders daily reference-price and tracked-sales charts, not the existing synthetic candles.
  Rationale: true candlesticks require ordered timestamps and stable pagination that the current source does not provide.
  Date/Author: 2026-07-22, Codex.
- Decision: immutable content-addressed runs remain the disaster-recovery evidence, while a standalone MySQL-compatible database is the operational canonical authority. The database is rebuilt only through the same idempotent normalized batches; no second hand-edited catalogue is permitted. A future JLP adapter may consume the same schema contract without becoming a current release gate.
  Rationale: Jackson requires this workstation to behave like the eventual server and a clean GitHub clone to restore history, initialize its own database and continue daily increments. Immutable runs preserve reproducibility; MySQL-compatible operation supplies the production-equivalent runtime without depending on JLP.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: Windows, Linux, EC2, and RDS use the same Python 3.10+ backend entrypoint. PowerShell and shell files are convenience launchers only; the portable Python path owns venv creation, dependency installation, archive verification/restore, migrations, replay, integrity status, singleton daily collection, and fail-closed exit codes. RDS configuration must be explicitly process-injected, and production external-database mode requires verified TLS through `CARDZ_DB_SSL_CA`.
  Rationale: the local workstation must behave like the eventual server, and a clean GitHub clone must not depend on Windows Task Scheduler, drive-letter logic, or a local Docker password file.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: public editorial HTML is searchable while bulk data, raw history, provider identity, and upstream assets remain private and rate-limited.
  Rationale: balances SEO/GEO with data-protection requirements.
  Date/Author: 2026-07-22, Codex based on the approved plan.
- Decision: the bundled Wrangler pointer promoter is limited to staging and serialized by the daily singleton lock; production must use a separately owned atomic conditional writer.
  Rationale: Wrangler has no `If-Match` pointer put. Rechecking the baseline before a staging write is useful canary protection but is not an atomic production compare-and-swap.
  Date/Author: 2026-07-22, Codex.
- Decision: public image requests are authorized against the active pointer's explicit hash allowlist. Browser cache is five minutes and edge cache is one hour, so an image removed from the current generation is no longer an indefinitely retrievable append-only object through the Worker route.
  Rationale: content addressing alone proves bytes but does not revoke a formerly valid object that later fails QC.
  Date/Author: 2026-07-22, Codex after release security review.
- Decision: the first staging generation may use `metadata_exact_unreviewed` images and stale source prices only while its public generation declares blockers and the strict production gate remains closed.
  Rationale: this permits truthful UX and pipeline canaries without misrepresenting the dataset as production-ready.
  Date/Author: 2026-07-22, Codex.
- Decision: store all market values in USD and convert only from one freshness-checked daily USD FX snapshot containing USD, HKD, CNY, GBP, TWD, JPY, and KRW. Collect the snapshot privately through a replaceable Frankfurter adapter; never call the FX service from a browser request.
  Rationale: a formatting package cannot supply trustworthy rates. A private daily adapter keeps conversion deterministic, avoids client-side upstream leakage, supports last-good recovery, and remains portable to a self-hosted endpoint.
  Date/Author: 2026-07-22, user and Codex.
- Decision: vendor the Grade10 collector and its operational documentation under `integrations/grade10/`, but keep its analytics and synthetic K-line programs outside the canonical ranking path. The repository-owned Python runner replaces the Windows batch file and writes only ignored runtime data.
  Rationale: a clean GitHub clone must contain every code dependency needed to rebuild the private acquisition service on AWS/Linux, while known derived-data defects must not silently become market truth.
  Date/Author: 2026-07-23, Jackson and Codex.
- Decision: use a two-ring daily universe. A cheap broad discovery refresh detects new and accelerating constituents; exact identity, population, price, image and freshness gates control entry into the bounded `Top 100 + 200 candidates` active lock. Alerts can propose a lock refresh but cannot publish an unconfirmed printing automatically.
  Rationale: only collecting the frozen active lock is economical but cannot discover a new release outside it. Index-level discovery is small enough to run daily and provides an honest bounded-market completeness test.
  Date/Author: 2026-07-23, Jackson and Codex.

- Decision: broad market discovery is now GemRate-led rather than limited to the existing 600-card Grade10 index. GemRate supplies exact card identity and grader population; SNK supplies a price only after exact TCG, card language, complete collector number, edition/parallel and grade matching. Grade10 remains last-good evidence and a discovery cross-check, not proof of global completeness.
  Rationale: market cap requires both population and price. The current 600-card observed universe has unresolved high-potential outsiders and therefore cannot support a global Top 100 claim by itself.
  Date/Author: 2026-07-23, Jackson and Codex.

- Decision: Grade10/G10 is no longer a canonical fallback for any ranking dependency. It may seed story research, identity comparison, fixtures, and last-good diagnostics only. GemRate is authoritative for identity and per-grader population; SNK is authoritative for exact PSA 10 reference price; validated eBay sold transactions supplement tracked-sales coverage and validate price; CARDZ owns all derived changes, market caps, alerts, and ranks.
  Rationale: the audited G10 population and grader fields are downstream copies, while its eBay grade filter is keyword-based. Treating either as authority would duplicate upstream data and preserve known ambiguity.
  Date/Author: 2026-07-23, Jackson and Codex.

- Decision: record GemRate population authority separately from transport. Direct GemRate API is preferred; Grade10 `price.getGradingPopulations` is allowed only as a provenance-labelled GemRate mirror for current population. Same-date disagreement fails promotion, and different-date reads retain the newest value with its transport provenance.
  Rationale: this preserves one population authority while allowing resilient acquisition without silently treating a downstream Grade10 response as an independent source.
  Date/Author: 2026-07-24, Jackson and Codex.

- Decision: the runtime end state is one script-only Python orchestration path: discover, collect, validate, normalize, migrate/import, derive, gap-audit, exact-refill, export and atomic last-good promotion. AI is not a production dependency; unresolved identity, image and provider-schema conflicts enter a human review queue.
  Rationale: local Windows and later Linux/AWS operation must be repeatable without an interactive agent, while bad or partial source runs must never corrupt the canonical database or public pointer.
  Date/Author: 2026-07-23, Jackson and Codex.

## Milestone 1 - G10 landing, schema, and deterministic replay

Create an immutable discovery-generation manifest, freeze one versioned `Top 100 + up to 200 candidates` lock per `TCG × card language`, and let only identities in that lock enter historical backfill or daily canonical storage. Add a private landing convention for selected full-history and daily runs, standalone MySQL-compatible canonical SQL, an idempotent importer/replayer, corrected daily price/population observations, partial tracked-sales aggregates, and fixtures covering retries, bundles, dates, duplicate rows and database restoration.

### Validation

From the target directory, run `npm run test:data`, the documented Python replay command, and the database bootstrap smoke test. Repeating the same full or incremental input must produce identical row counts and snapshot hash; a partial run must not advance either the database checkpoint or last-good generation.

## Milestone 2 - Canonical identity, images, and market snapshot

Resolve exact printing identities and complete collector numbers, quarantine conflicts, classify and verify raw-front images, calculate PSA Top 100/watchlist, expose five-grader supply metrics, and export only the sanitized public contract.

### Validation

Run `npm run images:verify`, `npm run test:data`, and `npm run verify:public`. Expect exactly 100 eligible cards, population at least 1000, complete collector numbers, raw-front image QC, no fake zero metrics, and no provider/source leakage.

## Milestone 3 - Art-first responsive product

Make the heatmap the first viewport, remove the market-cap summary card and Set column, implement a deterministic rank-ordered treemap, shared period controls, hover/focus/mobile details, responsive ranking list, grader pages, One Piece market page, and truthful price/tracked-sales charts.

### Validation

Run lint, typecheck, tests, and build. Capture and compare the implementation against the supplied references at 390, 768, 960, and 1440 pixels. Verify no horizontal overflow, no slab imagery, stable rank order, keyboard access, reduced motion, and one natural scroll into Top 100.

## Milestone 4 - Public boundary, SEO/GEO, staging, and daily automation

Add truthful locale metadata and structured data, security headers and leak gates, private R2 generation publishing, a canary endpoint, and an unattended Windows daily pipeline. Deploy a new Cloudflare staging Worker and prove one complete automatic data refresh without redeploying code.

### Validation

Run `npm run verify:all`, `npm audit`, `npx wrangler whoami`, the staging deploy command, public-bundle leak scans, and live canaries. Confirm the generation/effective dates, three periods, routes, raw images, and rollback pointer. The standalone normalized batch and snapshot are the current publication inputs; no JLP migration authority is required.

## Milestone 5 - Portable acquisition dependency, discovery alerts, and operator coverage

Copy the reviewed Grade10 Python acquisition dependency and documentation into a private integration boundary, replace the workstation batch entrypoint with one Python runner, and wire explicit broad-index discovery into the standalone daily backend. Persist market-entry alerts and evidence for new releases, cutoff proximity, rank acceleration, market-cap velocity, and population acceleration. Produce a machine-readable coverage report that states the observed universe, cutoff completeness, missing canonical fields, and whether a Top 100 claim is verified for that universe.

### Validation

Run the vendored collector self-check without network mutation, the alert fixtures, database migration/replay twice, `scripts/backend.py status`, the Windows wrapper test, and a WSL/Linux invocation. A repeated discovery or alert evaluation must not duplicate rows; a failed collector must not advance checkpoints; the alert evaluator must never treat unavailable values as zero or auto-publish an unresolved identity.

## Milestone 6 - GemRate-led global candidate audit and database-derived live snapshot

Build a broad, replayable Pokémon and One Piece candidate manifest from G10 bootstrap identity plus GemRate exact identity and population data. Obtain current GemRate PSA 10 POP broadly enough to classify the market, resolve each exact DB candidate to SNK, and collect the current PSA 10 price needed to calculate complete combined, Pokémon, and One Piece rankings. Formal membership requires POP >=1000; the pre-entry pool is POP 971–999. Language remains printing metadata and never creates a separate ranking.

After current ranking is known, backfill full GemRate population history, SNK price history and tracked-sales evidence for the deduplicated union required by the Top 350 operator view. Normalize accepted observations into the standalone MySQL schema, quarantine conflicts with explicit reasons, generate a machine-readable completeness and gap report, run exact refill worklists, then export the web snapshot from the database rather than the legacy seed path. Consolidate the commands into the repository-owned Python backend entrypoint and update operator documentation so Windows and Linux/AWS use the same deterministic chain.

### Validation

From `C:/Users/jackson0202/Documents/Playground/cardz-market-cap`, run the project-owned Python self-tests, GemRate/SNK fixture tests, migration/replay twice, database integrity/status, candidate-completeness audit, snapshot schema/public-boundary verification, and the existing web build/tests. Expect no duplicate observations on replay, no first-search-result identity matches, no fake zero values, explicit unresolved queues, exact membership/rank drift evidence, and no last-good/public pointer change after an injected partial failure.

## Milestone 7 - Executable backend registry and ranking presentation views

Promote `config/data-routing.json` from a metric-only policy file into the one machine-readable backend registry. It must describe canonical storage, complete ranking scopes, configurable presentation views, source capabilities and transport order, repository tools, ordered execution profiles, manuals, database destinations, snapshot fields, API consumers, and tests. `scripts/backend.py` must expose registry inspection, reverse explanation, and generated lineage documentation so a new operator or agent can answer which source and script produce any database or frontend field without reading implementation code first.

Separate ranking eligibility from presentation limits. Canonical observations and ranked scope membership remain available independently of Top 100/300/350 display cuts. The exporter derives those cuts, `Top 100 + 200` as a Top 300 alias, and reserve 50 from one ordered generation. Complete the GemRate public-card-details current-population transport, make it portable through a pinned Playwright dependency, feed it into candidate backfill and daily collection, and put the strict audit after current-run collection rather than before a repairable refresh.

### Validation

From `C:/Users/jackson0202/Documents/Playground/cardz-market-cap`, run:

    python scripts/backend.py registry --json
    python scripts/backend.py explain psa10_population
    python scripts/backend.py explain market_index_constituent
    python scripts/backend.py graph --format html
    python scripts/backend.py generate-docs --check
    python -m unittest discover -s tests -p "test_*.py" -v
    npm run test:data
    npm run lint
    npm run typecheck
    npm run build

Expect the registry validator to resolve every metric, tool, database destination, manual and public consumer; generated files to be byte-stable; GemRate direct/public/mirror fixtures to preserve one authority with transport provenance; the same ranking generation to yield consistent Top 100/300/350 views; and injected source failure to preserve checkpoint and last-good state.

## Interfaces and Dependencies

- `MarketWindow = "1d" | "7d" | "30d"`
- `MetricStatus = "ready" | "accumulating" | "stale" | "unavailable"`
- `CoverageStatus = "partial" | "stale" | "unavailable"`
- `Grader = "PSA" | "BGS" | "CGC" | "SGC" | "TAG"`
- Public cards use CARDZ opaque IDs, canonical printing identity, complete collector number, localized content, safe raw-front asset, period metrics, tracked-sales aggregates, grader populations, and timestamps.
- Private landing keys are `g10/full/<generation>/` and `g10/incremental/YYYY-MM-DD/<run-id>/`.
- R2 publishing writes `generations/<generation>/...`, verifies it, then atomically replaces `latest.json`.
- Private acquisition code and guides live under `integrations/grade10/`; all generated payloads remain under ignored `data/runtime/` paths and never enter the public build.
- A market alert records its canonical variant, alert type, evaluation date, current/previous rank, rank-100 cutoff, evidence JSON, status, and first/last-seen timestamps. Alert rows are operational evidence, not public claims by themselves.

## Idempotence and Recovery

Source payload hashes and canonical observation keys make imports safe to repeat. A run writes into a new generation, validates all gates, and advances the pointer only after the live canary succeeds. Failed or incomplete runs remain inspectable but never become public. The previous generation, previous Worker, and read-only source trees stay untouched.

## Outcomes & Retrospective

The clean repository, immutable replay/import contracts, sanitized Top 100/watchlist generation, art-first responsive application, private R2 publication chain, isolated Cloudflare canary/staging Workers, and Private GitHub history are implemented. The backend now selects a bounded active universe before G10/GemRate/SNK/TAG collection, refreshes the 600-card broad radar before any daily database change, persists evidence-bound market-entry alerts, and validates USD/HKD/CNY/GBP/TWD/JPY/KRW rates without browser-side upstream calls. The exact Grade10 dependency and AWS/Linux units are now present and tested in the worktree; fresh-clone reproducibility still requires committing and pushing those currently untracked files and the LFS archive pointer.

This is a truthful backend staging result, not a production cutover. JLP is no longer a blocker. The standalone database/bootstrap path, deduplicated three-index tracked universe, full SNK history replay, idempotent MySQL import, and a real tracked-universe daily canary now pass on the workstation. The current selector has 300 combined, 300 Pokémon, and only 95 qualified One Piece members, producing a 395-printing union; the missing 205 One Piece candidates require broader exact discovery rather than fabricated rows. GemRate history is locally available for 52 of 395 mapped candidates and needs a privately injected API key to backfill the remaining worklist. Remaining backend proof is a second calendar-day unattended increment, managed-MySQL/RDS TLS canary when a server exists, and the database-derived public exporter/pointer publisher.

Wave 1 now makes the transport boundary explicit in the checked-in routing contract and operator documentation. It does not implement the direct/mirror collector selection, provenance storage, or same-day comparison gate; those are deliberately Wave 2 runtime work.

The P0/P1 operational review on 2026-07-24 closed three release-critical failures. Canonical seed archives no longer contain migration-owned metadata or transaction control, so restore owns one rollback boundary and can safely resume a repository-created empty migrated schema after a failed insert. The public exporter and `daily --publish` path now derive rank, price, population, changes, tracked sales, and history from canonical MySQL; the checked snapshot is presentation-only. Daily history selects the highest-priority observation per date and retains the latest 90 days rather than the oldest 90.

The full Python suite (128 tests), web suite (54 tests), market-data contract tests, direct Next production build, and structural snapshot contract pass. A live MySQL export/restore/daily canary is still unproved on this workstation because the database runtime is not running. Production release verification also remains closed until a fresh Cloudflare/OpenNext build replaces the stale provider-bearing Worker artifact; the current source-side external provider link was removed so a new build will not reproduce that leak.
