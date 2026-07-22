# Rebuild CARDZ Market Cap from the complete G10 dataset and daily increments

This ExecPlan is a living document. The sections `Progress`, `Surprises & Discoveries`, `Decision Log`, and `Outcomes & Retrospective` must be kept up to date as work proceeds.

This plan follows `C:/Users/jackson0202/Documents/Playground/.agent/PLANS.md`.

## Purpose / Big Picture

Build the independent `cardz-market-cap` repository into an art-market-first, data-credible product backed by the complete local Grade10 dataset and a repeatable daily incremental publisher. The public website must show a rank-ordered raw-card Top 100 heatmap, reliable PSA 10 market-cap rankings, 1d/7d/30d changes and tracked sales, four grader supply views, responsive card details, four languages, five currencies, and no upstream-provider leakage.

The legacy `cardz-platform` and `grade10-scraper` trees remain read-only inputs. JLP MySQL 5.7 is the sole production canonical authority; replay SQLite files are test artifacts only. Cloudflare serves sanitized versioned generations, and failed refreshes keep the last successful pointer.

## Workspace Target

- Target project: `C:/Users/jackson0202/Documents/Playground/cardz-market-cap`
- Read-only G10 source: `C:/Users/jackson0202/Documents/Playground/grade10-scraper`
- Read-only legacy source: `C:/Users/jackson0202/Documents/Playground/cardz-platform`
- Rules reviewed: workspace `AGENTS.md`, workspace `.agent/PLANS.md`, this project's `README.md`, and `package.json`
- Validation: `npm run lint`, `npm run typecheck`, `npm run test`, `npm run test:data`, `npm run images:verify`, `npm run build`, `npm run verify:public`
- Recovery: never mutate either source tree; publish a versioned snapshot before changing `latest.json`; preserve the old Worker and previous generation for rollback.

## Progress

- [x] (2026-07-22 15:44+09:00) Audited the technical document, the complete 600-card G10 dataset, current scraper/analytics defects, JLP schema direction, clean repo, and live UI drift.
- [x] (2026-07-22 15:44+09:00) Replaced the obsolete SNK-first, 30d-only, writable-canonical-SQLite plan with this G10-first plan.
- [x] (2026-07-22 16:03+09:00) Captured the live 1440 x 900 visual baseline and the supplied Figma DESIGN.md reference into `.agent/visual-audit/`, then recorded the specific comparison gates.
- [x] (2026-07-22 16:12+09:00) Added the isolated Cloudflare staging/production configuration, hardened headers, private-R2/public-leak verification, CI release gates, and security/runbook documentation; deployment remains intentionally pending the integrated v2 snapshot.
- [x] (2026-07-22 16:49+09:00) Moved the obsolete schema-1 SQLite copies out of `data/private` into the ignored, recoverable `data/runtime/private-quarantine/legacy-clean-room/` so the clean repository cannot mistake them for canonical authority.
- [x] (2026-07-22 18:27+09:00) Added fail-closed generation-canary and staging-only pointer-promotion hooks, with explicit S4U task injection for the HTTPS canary origin and absolute hook command arrays. Fake-command tests prove stale-receipt removal, baseline-change refusal, production refusal, and receipt-after-success only.
- [x] (2026-07-22 18:50+09:00) Froze and hashed the complete 600-card G10 generation, added immutable full/incremental landing replay, and kept all provider payloads outside public and Git-tracked paths.
- [x] (2026-07-22 18:50+09:00) Added the MySQL 5.7-compatible additive market schema, idempotent observation import, corrected sale semantics, grader population observations, and replay/order tests.
- [x] (2026-07-22 18:50+09:00) Produced sanitized demo generation `daily_20260722T094826496874Z`: 100 ranked cards, 260 watchlist cards, complete collector-number resolver including `023/MEP`, 360 hash-valid raw fronts, and explicit identity/image review blockers.
- [x] (2026-07-22 19:16+09:00) Implemented the shared 1d/7d/30d contract, grader population-change views, truthful daily price/tracked-sales charts, pointer-authorized media, and public privacy gates.
- [x] (2026-07-22 19:16+09:00) Rebuilt the home heatmap, ranking, details, market/grader routes, locales, currencies, SEO/GEO, and responsive interactions. Reviewed editorial copy is joined by exact identity guard; 45 stories are ready and 55 remain blocked for evidence review.
- [x] (2026-07-22 19:21+09:00) Passed lint, typecheck, 34 web tests, 27 data tests, 7 hook/canary tests, non-strict verification of 360 images, Next build, public/bundle leak gates, deployment isolation, and canary/staging Wrangler dry-runs.
- [x] (2026-07-22 19:54+09:00) Created and pushed the clean Private GitHub repository, deployed isolated canary and staging Workers, uploaded and read-back-verified all 360 R2 images, promoted generation `daily_20260722T094826496874Z` only after a generation-scoped live canary, and verified build `30e50fbba5c1` on every public route.
- [x] (2026-07-22 19:54+09:00) Removed all market media and local design artifacts from the Cloudflare static bundle. Live authorized media now resolves only through the active-pointer Worker route with the expected SHA-256 and cache policy; an unlisted hash and the local mockup route both return 404.
- [ ] Prove one unattended daily publish canary. The exact 06:30 S4U task action is generated and WhatIf-verified, but Windows denied task registration; the current 83-hour-old price observation also correctly blocks a daily run while preserving the R2 pointer byte-for-byte.
- [ ] Cut production only after the JLP migration runner/owner is confirmed and staging approval is recorded.

## Surprises & Discoveries

- Observation: the local G10 source is a daily full pull plus overwrite, not a database-style incremental pipeline. Only the existing sales cache attempts accumulation.
  Evidence: `grade10_scraper.py`, `grade10_analytics.py`, and `data/_state/last_run.json`.
- Observation: the configured `Grade10-Daily-Scraper` task has never completed an automatic run; the current data was created manually.
  Evidence: Task Scheduler last-run sentinel and next run at 06:30 JST.
- Observation: current derived K-lines are not genuine OHLC; open/close are sorted low/high values, 42,630 of 45,714 rows are carried, bundle totals corrupt some unit prices, and sale dates are frequently replaced by discovery day.
  Evidence: `grade10_kline.py`, `grade10_analytics.py`, and read-only aggregates over `data/analytics` and `data/sales_cache`.
- Observation: the current 600-card universe supports PSA market cap and four-grader population views, but not four grader market-cap rankings because SGC has no price stream.
  Evidence: PSA population 600 cards, BGS 524, CGC 554, SGC 380; SGC price coverage zero.
- Observation: the approved USD 2.624B / 89 Pokemon / 11 One Piece estimate is reproducible. Deduplicating all 600 current constituents by source identity, applying positive `priceUsd` plus PSA `topGrade >= 1000`, and sorting `priceUsd × PSA topGrade` yields 460 market-eligible cards and a Top 100 of USD 2,624,140,427 with the stated 89/11 mix. A narrower replay that accidentally omitted the alternate-source card directory produced USD 2.106B and was incorrect.
  Evidence: read-only 2026-07-22 replay across `grade10-scraper/data/index/{ptcg,ptcg100,opcg}/constituents.json` and both exact `data/cards/{snkrdunk,altxyz}/{id}/populations.json` paths.
- Observation: the initial exact identity plus raw-front public gate currently leaves 349 publishable candidates. Ranking that restricted preview pool gives USD 2,248,022,219 and a 91 Pokemon / 9 One Piece mix, so it is a different scope from the full market universe and must not silently replace the USD 2.624B benchmark.
  Evidence: `pipelines/g10_public_snapshot.py --self-test`; 111 current cards remain in the identity/image review queue.
- Observation: the G10 constituent price snapshot itself reports `updatedAt = 2026-07-19T00:00:00Z`. The 2026-07-22 scraper completion time is a fetch timestamp, not a newer price observation, so the current price generation exceeds the 48-hour production SLA.
  Evidence: `grade10-scraper/data/index/{ptcg,opcg}/summary.json` versus `data/_state/last_run.json`.
- Observation: all calculated Top 100 cards have a local image, but the pool mixes raw cards and slabs; image type cannot be inferred safely from filename or dimensions alone.
- Observation: the current live mobile page is not merely dense; at 390 px its desktop grid collapses into a narrow left strip and wraps brand, navigation, heading, body copy, and the summary card word-by-word.
  Evidence: `.agent/visual-audit/before-live-390x844.png` captured from the in-app browser.
- Observation: the workspace does not identify the CardzGame production backend migration runner or JLP database owner. Local replay and staging can proceed, but production cutover cannot.
- Observation: the first G10 public-gated replay exposed repeatable identity defects rather than isolated UI typos: Japanese promo namespaces were labelled English, provider-normalized Pokémon denominators lost their leading zero, and two different Snorlax variants shared one raw image. These are now resolver/validator concerns and must not be hand-corrected in React.
- Observation: metadata proves all 360 exported images are hash-valid raw fronts with exact number/language/TCG resolver evidence, but none has yet been promoted to `human_or_vision_confirmed`. The non-strict staging gate passes and the strict production gate deliberately fails all 360.
- Observation: the current compatible Next/OpenNext/Wrangler graph resolves `sharp 0.34.5` and Next's bundled `postcss 8.4.31`; the official-registry audit reports five high and one moderate advisory. Forcing patched 0.x packages violates upstream dependency ranges, so production remains blocked instead of shipping an invalid override.
- Observation: 45 Top 100 editorial stories have printing-specific four-language evidence and 55 remain `review_required` (51 thin evidence, two summary conflicts, two identity conflicts). Missing stories remain absent rather than being filled with generic market prose.
- Observation: the final sanitized demo generation contains 100 ranked cards and 260 watchlist cards. Its public-gated Top 100 is USD 2,445,570,776 with a 93 Pokemon / 7 One Piece split; the full ungated market-universe benchmark remains USD 2,624,140,427 with the approved 89/11 split.
- Observation: the first remote publisher attempt exposed a real Windows `spawnSync npx.cmd EINVAL` failure, and the first generation canary exposed a missing private-discovery flag. Both failed before `latest.json`, were repaired with direct Node CLI invocation and explicit private discovery, and are covered by hook tests.
- Observation: copying raw fronts into `apps/web/public` allowed the static asset binding to serve a known hash without consulting the active pointer. The Cloudflare build now uses a temporary allowlisted public directory and bundles neither market images nor local design artifacts; live tests confirm pointer-authorized media headers and 404 for an unknown hash.
- Observation: Windows denied registration of the separate `CARDZ-Market-Cap-Daily-Staging` S4U task. The installer no longer risks overwriting the legacy task and correctly resolves the first Python/Node application, but an authorized Windows task-registration context is still required.
- Observation: the first GitHub clean-clone CI run correctly revealed two portability assumptions: the private sibling G10 source and Windows `where.exe`. Source-dependent replay now runs when the read-only source exists and is explicitly skipped in clean CI; Task Scheduler execution remains Windows-only while its structural contract is checked everywhere.

## Decision Log

- Decision: G10 is the primary price, rank, tracked-sales, and grader-population feed. GemRate is an identity/population correction source; SNK remains a private standby adapter.
  Rationale: this is the user's supplied complete dataset and daily operating path.
  Date/Author: 2026-07-22, user and Codex.
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
- Decision: production canonical writes target JLP MySQL 5.7. Local SQLite is immutable replay only.
  Rationale: respects the existing single-database architecture and avoids a second production truth.
  Date/Author: 2026-07-22, user and Codex.
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

## Milestone 1 - G10 landing, schema, and deterministic replay

Create an immutable full-generation manifest, a private landing convention for full and daily runs, additive JLP-compatible SQL, an idempotent importer, corrected daily price/population observations, partial tracked-sales aggregates, and fixtures covering retries, bundles, dates, and duplicate rows.

### Validation

From the target directory, run `npm run test:data` and the documented Python replay command. Repeating the same full or incremental input must produce identical row counts and snapshot hash; a partial run must not advance the last-good generation.

## Milestone 2 - Canonical identity, images, and market snapshot

Resolve exact printing identities and complete collector numbers, quarantine conflicts, classify and verify raw-front images, calculate PSA Top 100/watchlist, expose four-grader supply metrics, and export only the sanitized public contract.

### Validation

Run `npm run images:verify`, `npm run test:data`, and `npm run verify:public`. Expect exactly 100 eligible cards, population at least 1000, complete collector numbers, raw-front image QC, no fake zero metrics, and no provider/source leakage.

## Milestone 3 - Art-first responsive product

Make the heatmap the first viewport, remove the market-cap summary card and Set column, implement a deterministic rank-ordered treemap, shared period controls, hover/focus/mobile details, responsive ranking list, grader pages, One Piece market page, and truthful price/tracked-sales charts.

### Validation

Run lint, typecheck, tests, and build. Capture and compare the implementation against the supplied references at 390, 768, 960, and 1440 pixels. Verify no horizontal overflow, no slab imagery, stable rank order, keyboard access, reduced motion, and one natural scroll into Top 100.

## Milestone 4 - Public boundary, SEO/GEO, staging, and daily automation

Add truthful locale metadata and structured data, security headers and leak gates, private R2 generation publishing, a canary endpoint, and an unattended Windows daily pipeline. Deploy a new Cloudflare staging Worker and prove one complete automatic data refresh without redeploying code.

### Validation

Run `npm run verify:all`, `npm audit`, `npx wrangler whoami`, the staging deploy command, public-bundle leak scans, and live canaries. Confirm the generation/effective dates, three periods, routes, raw images, and rollback pointer. Production routing remains blocked until the JLP migration authority is available.

## Interfaces and Dependencies

- `MarketWindow = "1d" | "7d" | "30d"`
- `MetricStatus = "ready" | "accumulating" | "stale" | "unavailable"`
- `CoverageStatus = "partial" | "stale" | "unavailable"`
- `Grader = "PSA" | "BGS" | "CGC" | "SGC"`
- Public cards use CARDZ opaque IDs, canonical printing identity, complete collector number, localized content, safe raw-front asset, period metrics, tracked-sales aggregates, grader populations, and timestamps.
- Private landing keys are `g10/full/<generation>/` and `g10/incremental/YYYY-MM-DD/<run-id>/`.
- R2 publishing writes `generations/<generation>/...`, verifies it, then atomically replaces `latest.json`.

## Idempotence and Recovery

Source payload hashes and canonical observation keys make imports safe to repeat. A run writes into a new generation, validates all gates, and advances the pointer only after the live canary succeeds. Failed or incomplete runs remain inspectable but never become public. The previous generation, previous Worker, and read-only source trees stay untouched.

## Outcomes & Retrospective

The clean repository, immutable replay/import contracts, additive JLP schema, sanitized Top 100/watchlist generation, art-first responsive application, private R2 publication chain, isolated Cloudflare canary/staging Workers, and Private GitHub history are implemented. Live route, generation, build, media authorization, leak, and fail-closed pointer canaries pass.

This is a truthful staging result, not a production cutover. Production remains blocked by the missing JLP production migration runner/owner, stale price input, 360 pending human/vision image confirmations, 55 editorial review items, missing independent grader supply coverage, missing production currency feed, unresolved compatible dependency advisories, and unverified live Cloudflare WAF/rate/bot controls. Windows also denied the S4U task registration, so one genuinely unattended daily canary has not yet been demonstrated.
