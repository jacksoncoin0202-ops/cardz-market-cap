# CARDZ Private Runner, Data Publication, and Cloudflare Runbook

## Operating boundary

The private runner uses GemRate-led discovery and population, exact SNK PSA 10
prices/trades, and validated eBay sold transactions when available. GemRate
direct API is the preferred population transport; Grade10
`price.getGradingPopulations` may supply a current-population GemRate mirror
only, with provenance retained. G10 otherwise remains bootstrap/research
evidence only. The runner imports canonical facts into standalone MySQL,
derives complete rankings for combined TCG, Pokémon, and One Piece, and slices
those rankings into presentation views such as Top 100, Top 300, Top 350, and
`top100_plus_200`. The same Python control path runs on Windows and Linux.
Immutable private runs are replay evidence; JLP is only a possible future
integration. GitHub Actions never collects provider data. A daily data update
does not rebuild or redeploy the website.

Production stays on the existing site until the new data generation, scheduler canary, release gates, and production route are independently approved. No JLP owner or runner is required for the standalone release.

## Required tools and authority

- Node.js 24 and npm 11.
- Python 3.10 or newer.
- Docker Compose for bundled MySQL, or an externally managed compatible MySQL database.
- Git LFS.
- 1Password CLI signed into the approved account.
- Wrangler authenticated to the approved Cloudflare account for deployment or R2 mutation.

Verify metadata only:

```powershell
node --version
npm --version
python --version
git lfs version
op whoami
npm run cf:whoami
```

Do not print environment dumps, request headers, provider responses, signed URLs, or credentials. If approved secret injection is unavailable, skip that live collector and use only a still-valid bounded last-good observation.

## Standalone database bootstrap

Use the same Python entrypoint on a Windows workstation or Linux server:

```text
git lfs pull
python scripts/backend.py bootstrap --bootstrap-archive data/private/cardz-active-bootstrap.tar.gz
python scripts/backend.py registry --json
python scripts/backend.py import
python scripts/backend.py status
python scripts/backend.py audit
python scripts/backend.py daily
```

`audit` is the truthful pre-database coverage gate and does not require Docker or
MySQL. It resolves the newest private SNK PSA 10 run, validates local GemRate
payloads rather than counting mapped IDs, requires exact collector-number and
grade evidence, converts SNK JPY through the private daily FX snapshot, and
writes `data/runtime/private-reports/data-coverage-audit.json`.

Run the coverage audit before any public-market promotion:

```powershell
python pipelines/fx_rates.py
python scripts/backend.py audit
```

The report is coverage diagnosis for complete rankings and does not itself
select a public cutoff. A publication gate checks only the requested view
(`top100`, `top300`, `top350`, `top100_plus_200`, or `reserve50`) against the
complete ranking for its scope. The report includes exact GemRate/SNK refill
worklists and quarantine reasons; a mapping without a matching local payload
never counts as verified data.

For Amazon RDS, inject `CARDZ_DB_HOST`, `CARDZ_DB_PORT`, `CARDZ_DB_NAME`, `CARDZ_DB_USER`, and `CARDZ_DB_PASSWORD`, set `CARDZ_DB_MODE=external`, and set `CARDZ_DB_SSL_CA` to the readable managed-database CA bundle. Production external-database runs fail closed without TLS verification. Then run:

```text
git lfs pull
python3 scripts/backend.py bootstrap --external-db --mode production --bootstrap-archive data/private/cardz-active-bootstrap.tar.gz
python3 scripts/backend.py daily --external-db --mode production
```

Amazon Linux 2023 keeps `/usr/bin/python3` on Python 3.9, so install a supported versioned interpreter. `backend.sh` selects the first installed Python 3.10+ interpreter, or use `CARDZ_PYTHON=python3.12 bash scripts/backend.sh ...` to pin one explicitly. Do not change the operating system's `python3` symlink. The launcher passes the selected interpreter through to venv creation and every child pipeline. Reference: [AWS Python in AL2023](https://docs.aws.amazon.com/linux/al2023/ug/python.html).

`bootstrap` first verifies every archive path, checksum, canonical identity, and
dated observation before restoring it. It then migrates and replays the dated
canonical batches. Re-running `import` must replay completed batches with zero
inserted observations. Local secrets are created only under ignored
`data/runtime/config/backend.env`; managed-database credentials must be
explicitly injected into the current process and are never read from that local
file or written to the repository.

Build the private Git LFS bootstrap only after the active lock and canonical landing pass validation:

```text
python scripts/bootstrap_archive.py build --output data/private/cardz-active-bootstrap.tar.gz --overwrite
python scripts/bootstrap_archive.py verify --archive data/private/cardz-active-bootstrap.tar.gz
```

The archive excludes broad discovery payloads, undated derived points, images, `.env` files, provider credentials, and identities outside the current lock. Restoring over an existing runtime tree requires the explicit `--restore-overwrite` flag; normal clean-clone bootstrap does not use it.

## Secret injection

`.env.example` documents names only. Store live values in 1Password and inject them into the private process. A local `.env.private` may contain only `op://` references and is ignored.

```dotenv
GEMRATE_API_KEY=op://Private/CARDZ Market Data/GemRate API Key
CARDZ_PRIVATE_ACQUIRE_SCRIPT=integrations/grade10/run_service.py
CARDZ_GENERATION_CANARY_COMMAND_JSON=["node","pipelines/run-generation-canary.mjs"]
CARDZ_POINTER_PROMOTE_COMMAND_JSON=["node","pipelines/promote-staging-pointer.mjs"]
CARDZ_CANARY_ORIGIN=https://<approved-canary-worker>.workers.dev
# Optional: point at an approved self-hosted compatible endpoint.
CARDZ_FX_ENDPOINT=https://api.frankfurter.dev/v2/rates
```

Example:

```powershell
op run --env-file=.env.private -- powershell -NoProfile -File pipelines/run_daily.ps1
```

For the unattended staging task, persist the non-secret hook commands and canary origin in the Task Scheduler action instead of relying on an interactive shell environment:

```powershell
powershell -NoProfile -File pipelines/install_daily_task.ps1 `
  -Mode staging `
  -R2Bucket cardz-market-cap-staging-data `
  -CanaryOrigin https://<approved-canary-worker>.workers.dev
```

The installer resolves absolute Python, Node, canary-hook, and staging-promoter paths and embeds their JSON command arrays plus the HTTPS canary origin in the S4U task action. Use `-WhatIf` to inspect that action before registration. Production must pass an external atomic `-PointerPromoteCommandJson`; the installer refuses to default to the bundled staging promoter in production mode.

## First full backfill: current facts, then target history

1. Treat `cardz-platform` as read-only. The vendored Grade10 integration is a supported Windows/Linux bootstrap route, but it is not the global candidate boundary.
2. Run broad current candidate discovery, exact identity resolution, GemRate current PSA 10 population collection, and exact SNK current PSA 10 price collection.
3. Freeze every raw source result in its private landing namespace and record SHA-256, file count, effective date, schema version, and accepted/quarantined/rejected counts in the manifest.
4. Import current canonical facts without correction. Resolve printings through exact TCG, language, set, complete collector number, edition, parallel, and finish mappings.
5. Derive complete eligible rankings for `tcg-combined`, `pokemon`, and `one-piece`; select configured presentation ranges only after this calculation.
6. Deduplicate the selected historical targets across scopes, then backfill available GemRate population history, SNK price history, and tracked trades. Cards outside the target range retain current/radar evidence and are queued automatically when they qualify.
7. Quarantine missing numbers, language conflicts, ambiguous identities, unsafe images, and unsupported grader-price combinations. Repeating the same full import must not increase observation counts.

Do not treat the existing derived K-lines as exchange-quality OHLC. Do not use a fallback grade to construct PSA 10 market cap. Do not direct-read G10 or legacy tables from the web application.

## Daily incremental job

Windows Task Scheduler or a Linux systemd timer runs the same backend command once daily at 06:30 JST. The scheduler invokes `python scripts/backend.py daily` (or `python3 ... daily --external-db`); all collection and replay logic remains in Python. A cross-platform file lock and run ID prevent two runs from racing. This command currently completes steps 1–11 below and stops after database integrity validation:

1. Validate the routing registry, refresh broad candidate/radar current facts, and resolve exact identities. Grade10 can provide bootstrap evidence but is not the candidate boundary. A failure stops the parent run before canonical import or alert evaluation.
2. Fetch one private USD FX snapshot for HKD/CNY/GBP/TWD/JPY/KRW. Validate all seven rates, write it atomically, and reuse a last-good response for at most 72 hours when the endpoint temporarily fails. The browser never calls this endpoint.
3. Recalculate complete eligibility rankings from current canonical facts. A printing is stored once even when it belongs to multiple ranking scopes. `top100`, `top300`, `top350`, `top100_plus_200`, and `reserve50` are exporter views, not active-universe database locks. A requested view fails closed when it lacks enough eligible members; the database still retains all accepted current facts and historical evidence.
4. Refresh GemRate current population for target and radar IDs through the configured transport order: direct API, exact page-initiated JSON from the verified public `/card/{id}` page (with labelled DOM fallback), then the Grade10 `price.getGradingPopulations` current-population mirror. Direct `/card-details` fetches are not a transport. The mirror does not change authority. Same-date direct/mirror disagreement fails the run; page JSON records its live fetch time separately from its last-population-change metadata. Daily mode does not re-download full history except for newly promoted historical targets.
5. Refresh auxiliary TAG population coverage. A live schema/timeout failure may reuse only a checksum-valid catalog whose persisted `capturedAt` is no more than 72 hours old, while preserving its original `observedDate`. Future dates, malformed manifests, copied files without persisted capture time, and checksum mismatches are rejected. If no valid fallback exists, mark TAG unavailable and continue; TAG must never stop the primary price path.
6. Refresh every active exact SNK card with `trading_card_single_psa10`. A failed card prevents atomic promotion of that run.
7. Normalize observations with `market_source_sync.py`, retain recent daily price anchors, recalculate complete combined, Pokémon, and One Piece rankings, and queue new historical targets. Missing exact GemRate/SNK data stays unavailable; G10 cannot fill a canonical ranking dependency.
8. Import the immutable batch through `scripts/backend.py import`, then require
   `scripts/backend.py status` to pass the tracked-universe one-to-one integrity
   gate. A failed import does not promote the new universe.
9. Derive 1d, 7d, and 30d close-to-close changes from canonical daily observations. Missing anchors remain accumulating.
10. Rebuild partial tracked-sales aggregates for 1d, 7d, and 30d. A day with no observed sale is not automatically zero coverage.
11. Rank confirmed cards by unrounded `PSA 10 price × PSA 10 population`; require population at least 1,000, fresh price, complete number, and QC-passed raw front. A discovered printing without exact population is only a candidate. Korean printings additionally require Korean card/set text; KRW is display conversion only and never changes the USD ranking basis.

The following public-publication stage is deliberately separate and is not invoked by `backend.py daily` yet:

12. Export an immutable sanitized candidate from the validated database and run data, image, release, and leak gates.
13. Publish the candidate generation, verify it remotely, run staging canaries, then advance the pointer.

Until the DB-derived exporter is connected, a successful backend daily run
proves canonical collection/replay only; it must not be reported as a live
website update. The older public builder still depends on legacy discovery
evidence and is not part of the portable canonical AWS contract.

The job must return nonzero on any failed stage. One singleton parent task runs all collectors and publishes one generation, preventing mixed-date price/population state. Its bounded private log contains run ID, generation ID, step status, counts, hashes, and redacted error categories only.

The default FX adapter is `pipelines/fx_rates.py`. It uses the keyless Frankfurter v2 daily API through a configurable `CARDZ_FX_ENDPOINT`, so the same pipeline can point to a privately self-hosted compatible service after a server move. Run `python pipelines/fx_rates.py` for a private live collection check; its output reports timestamps and currency codes only, never the upstream payload or URL.

## Versioned R2 publication contract

The R2 bucket remains private. Keys follow:

```text
generations/<generation-id>/snapshot.json
market-assets/<sha256>.webp
latest.json
```

Publication is ordered and recoverable:

1. Build the complete generation locally; never build in `latest/`.
2. Validate snapshot schema, public boundary, eligibility, image hashes, and canonical `contentSha256`.
3. Idempotently upload and read back every referenced content-addressed image hash; a prior local pointer is never accepted as proof that another bucket still contains the bytes. Then upload the immutable snapshot object.
4. Read the snapshot back from R2 and verify generation ID, canonical hash, object size, and metadata.
5. Upload `candidate.json`, which the persistent canary Worker reads through `MARKET_DATA_POINTER_KEY=candidate.json`, then invoke the configured generation-scoped canary. The included `pipelines/run-generation-canary.mjs` tests that exact generation at `CARDZ_CANARY_ORIGIN` and writes the receipt path supplied in `CARDZ_CANARY_RECEIPT_PATH`.
6. Invoke a pointer promoter only after canary success. The included `pipelines/promote-staging-pointer.mjs` is deliberately **staging-only**: it re-downloads the baseline pointer, candidate pointer, and candidate generation; verifies their SHA-256/generation contract; repeats the baseline check immediately before the write; performs the Wrangler put; verifies readback; then writes `CARDZ_POINTER_PROMOTION_RECEIPT`. It refuses production mode and any bucket not equal to the explicit staging bucket allowlist. Wrangler has no atomic `If-Match`, so this hook is safe only behind the daily singleton lock for staging canaries. It is not an acceptable production promoter.
7. Production requires a separately owned promoter using R2 Workers API or S3-compatible conditional writes. It must atomically compare the expected prior-pointer SHA-256 and replace `latest.json`, then emit the same receipt contract. Do not configure the bundled staging hook in a production task.
8. Read `latest.json` back and require exact generation, key, and hash equality.
9. Record the prior and new pointer privately. Never overwrite or delete immutable generations during normal publishing.

The pointer carries the complete current media-hash allowlist. The Worker checks that allowlist before reading an image object. If an asset must be revoked, publish a clean generation without the hash, verify the new pointer, purge the affected Worker URL from Cloudflare cache, and retain the R2 object only in the private incident record until deletion is approved.

The Cloudflare build verifies every referenced local image but excludes `market-assets` from the static Worker asset bundle. This is required: otherwise a hashed file could bypass the active-pointer allowlist through the static asset binding.

If any step before pointer replacement fails, users remain on the last-good generation. Pointer readback must match the intended generation, key, and canonical hash.

## Local verification

From the repository root:

```powershell
npm ci
npm run lint
npm run typecheck
npm run test
npm run test:data
npm run images:verify
npm run build
npm run verify:public -- --allow-demo --require-build
npm run verify:deployment
```

`--allow-demo` is for honest local/staging structure only. A production candidate must pass:

```powershell
npm run verify:release
```

## Cloudflare staging

Staging uses Worker `cardz-market-cap-staging` and bucket `cardz-market-cap-staging-data`. Production uses different names and rejects demo generations. R2 public development URLs and bucket custom domains stay disabled.

Validate without mutation:

```powershell
npm run cf:dry-run:staging
```

Before an approved staging deployment, verify the active account and set a safe build ID such as the Git commit SHA:

```powershell
npm run cf:whoami
$env:CARDZ_PUBLIC_BUILD_ID = (git rev-parse --short=12 HEAD)
npm run cf:deploy:staging
```

This runbook does not authorize a production route change. Production intentionally has no route in source control until cutover approval identifies the exact zone and route.

## Live canary

The live server-rendered pages must expose `X-CARDZ-Build`. Data-backed pages expose their safe opaque generation through `X-CARDZ-Generation` when available, with the exact `data-cardz-generation` page-root attribute as the server-rendered fallback. Neither value may contain a provider-native value.

```powershell
$env:CARDZ_CANARY_ORIGIN = 'https://<approved-staging-host>'
$env:CARDZ_EXPECTED_BUILD_ID = '<expected-build-id>'
$env:CARDZ_EXPECTED_GENERATION = '<expected-generation-id>'
npm run canary:public
```

The persistent candidate Worker reads `candidate.json` and uses private discovery. Verify it separately before advancing the staging pointer:

```powershell
node scripts/canary-public.mjs --origin 'https://<approved-canary-host>' --expect-generation '<candidate-generation-id>' --discovery private
```

The canary checks homepage, TCG pages, watchlist, four grader pages, security headers, body leak patterns, and that `/api/snapshot`, `/latest.json`, `/generations/*`, and the seed snapshot are not public bulk-data surfaces. It does not print response bodies.

Manual visual checks cover 390, 768, 960, and 1440 px, 100 ordered Heatmap tiles, raw-front images, 1d/7d/30d state, no horizontal overflow, keyboard/tap interactions, and locale/currency retention.

## Cloudflare zone checklist

After staging deploy and before public traffic:

- Keep staging behind Cloudflare Access or emit `noindex` for the entire staging hostname.
- Enable managed WAF rules in log mode and review Security Events before enforcement.
- Add rate limits for unknown and expensive `/api/*` requests; begin with managed challenge.
- Block public pointer/generation/private/source-map paths.
- Permit verified search crawlers on production editorial HTML, permit OAI-SearchBot for search visibility, and block training-only crawlers according to `SECURITY.md`.
- Configure API Shield only for a future CARDZ-owned authenticated API/MCP/CLI; do not expose the website's internal data reader as that API.

Record the enabled product tier and actual rule IDs privately. Do not claim bot scoring or API Shield protection that the account plan does not provide.

## Rollback and recovery

Data rollback does not redeploy code:

1. Select the previous known-good immutable generation from the private publication log.
2. Read and verify its snapshot hash and image manifest.
3. Conditionally replace `latest.json` with a new pointer to that generation.
4. Read the pointer back and run the live canary with the restored generation ID.
5. Preserve the failed generation privately for investigation.

Code rollback switches the route to the prior verified Worker version or the retained legacy Worker. Do not combine a code rollback with deletion of data generations.

## Backend control-plane and full-backfill bootstrap

The current collection scope is language-neutral. First inspect the routing
registry, then collect broad current facts, derive complete rankings, and
backfill history only for the deduplicated target ranges:

```powershell
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py registry --json
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py explain market_cap
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py graph --format html
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py routes
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py full-backfill
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py status
```

One canonical printing may appear in multiple ranking scopes but is stored once.
The snapshot exporter later chooses the rank range it needs; it does not rebuild
the database to switch from Top 100 to Top 300 or Top 350.

`full-backfill` is intentionally a different profile from `daily`: it refreshes
the private G10 bootstrap landing, rebuilds the exact source crosswalk, resumes
the broad GemRate candidate classification, builds an exact SNK PSA 10 refill
worklist, pulls the resolved IDs' actual SNK PSA 10 history/trades into an
immutable run, and overlays only those exact candidate identities before the
existing normalize → canonical DB → derive → alert → audit path. The final
daily import then pulls SNK again for the complete merged universe; it never
uses the candidate-only run as a substitute for existing tracked cards. A source-run
transport failure stops before canonical observations or the last-good
generation can change. Classified `review` / `unavailable` candidates remain
private retry evidence; they do not discard unrelated verified facts, but they
cannot enter a formal rank or presentation view. It never publishes.

When candidate SNK prices must be converted from JPY, `full-backfill` refreshes
one private FX snapshot before building the candidate overlay and passes that
same snapshot into the final daily import. A missing or invalid FX snapshot is
therefore a fail-closed collection error, never a silently guessed USD price.

The public-card GemRate collector is deliberately opt-in because it is a long,
resumable acquisition job. Do not run it while another collector owns the same
worklist:

```powershell
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py full-backfill --collect-public-candidates
```

Without that flag, `full-backfill` reuses validated private candidate receipts
and reports a fail-closed incomplete manifest if more exact POP evidence is
required. The private artifacts are resumable under
`data/runtime/private-source-map/`; no provider data, key, or raw payload is
placed in a public snapshot.

GemRate backfill uses the generated exact worklist. Inject the API key through the process environment or an external secret manager; never place it in the repository:

```powershell
.\.venv-backend\Scripts\python.exe -X utf8 pipelines\gemrate_source.py api-dump `
  --ids-file data\runtime\private-source-map\tracked-gemrate-ids.txt --resume
```

Run the unattended incremental path with the same Python entrypoint on Windows or Linux:

```powershell
.\.venv-backend\Scripts\python.exe -X utf8 scripts\backend.py daily --mode staging
```

The daily job refreshes discovery, checkpoints provider acquisition, normalizes
immutable batches, imports idempotently, derives complete ranks/windows/alerts,
queues newly qualified historical targets, and prints one final machine-readable
status. Auxiliary TAG failure is reported without discarding valid primary-source
observations. A source or canonical failure returns non-zero and does not advance
a public pointer.

## Production stop gates

Do not cut over production until all are true:

- Full import replay is idempotent and daily incremental checkpoints are proven.
- A real unattended scheduled run has completed successfully.
- The requested presentation view has enough eligible cards in each declared
  scope, with rank, market cap, and source freshness verified from the same
  complete canonical generation.
- Official-registry production dependency audit passes without forcing packages outside their declared compatibility ranges.
- All public Top 300 assets are exact-match raw fronts.
- Staging live canary passes for the expected build and generation.
- Cloudflare zone controls and production route are explicitly approved.
- Previous Worker version and last-good data generation are recorded and tested for rollback.
