# CARDZ Private Runner, Data Publication, and Cloudflare Runbook

## Operating boundary

The private Windows runner ingests the operator-provided G10 full history and daily incrementals, resolves exact canonical identities, writes JLP history, derives public metrics, and publishes a sanitized generation. GitHub Actions never collects provider data. A daily data update advances an R2 pointer; it does not rebuild or redeploy the website.

Production stays on the existing site until the new staging Worker, data generation, scheduler canary, JLP migration runner, and production route are independently approved.

## Required tools and authority

- Node.js 24 and npm 11.
- Python 3.10 or newer.
- Git LFS.
- 1Password CLI signed into the approved account.
- Wrangler authenticated to the approved Cloudflare account for deployment or R2 mutation.
- A confirmed JLP MySQL 5.7 migration runner and database owner for production writes.

Verify metadata only:

```powershell
node --version
npm --version
python --version
git lfs version
op whoami
npm run cf:whoami
```

Do not print environment dumps, request headers, provider responses, database URLs, signed URLs, or credentials. If 1Password or the JLP owner is unavailable, stop before collection or production migration.

## Secret injection

`.env.example` documents names only. Store live values in 1Password and inject them into the private process. A local `.env.private` may contain only `op://` references and is ignored.

```dotenv
GEMRATE_API_KEY=op://Private/CARDZ Market Data/GemRate API Key
CARDZ_JLP_MYSQL_DSN=op://Private/CARDZ Market Data/JLP MySQL DSN
CARDZ_JLP_PRODUCTION_RUNNER=C:\private\cardz-jlp-runner.py
CARDZ_PRIVATE_ACQUIRE_SCRIPT=C:\private\grade10-scraper\grade10_scraper.py
CARDZ_GENERATION_CANARY_COMMAND_JSON=["node","pipelines/run-generation-canary.mjs"]
CARDZ_POINTER_PROMOTE_COMMAND_JSON=["node","pipelines/promote-staging-pointer.mjs"]
CARDZ_CANARY_ORIGIN=https://<approved-canary-worker>.workers.dev
```

Example:

```powershell
op run --env-file=.env.private -- powershell -NoProfile -File pipelines/run_daily.ps1
```

For the unattended staging task, persist the non-secret hook commands and canary origin in the Task Scheduler action instead of relying on an interactive shell environment:

```powershell
powershell -NoProfile -File pipelines/install_daily_task.ps1 `
  -Mode staging `
  -PrivateAcquireScript C:\private\grade10-scraper\grade10_scraper.py `
  -R2Bucket cardz-market-cap-staging-data `
  -CanaryOrigin https://<approved-canary-worker>.workers.dev
```

The installer resolves absolute Python, Node, canary-hook, and staging-promoter paths and embeds their JSON command arrays plus the HTTPS canary origin in the S4U task action. Use `-WhatIf` to inspect that action before registration. Production must pass an external atomic `-PointerPromoteCommandJson`; the installer refuses to default to the bundled staging promoter in production mode.

## First G10 full import

1. Treat `cardz-platform` as read-only. The external `grade10-scraper.py` remains the separately owned mutable acquisition producer; after it exits successfully, CARDZ import code only reads the completed source tree and copies changed bytes into its own immutable landing namespace.
2. Freeze the selected G10 files under `data/runtime/private-landing/g10/full/<generation>/` outside Git tracking. Existing derived analytics and K-line output are evidence only and never become canonical observations.
3. Record SHA-256, file count, card count, effective date, schema version, and accepted/quarantined/rejected counts in the private import manifest.
4. Import raw observations without correction. Resolve canonical printings through exact TCG, language, set, complete collector number, edition, parallel, and finish mappings.
5. Quarantine missing numbers, language conflicts, ambiguous identities, unsafe images, and unsupported grader-price combinations.
6. Write accepted observations idempotently to JLP. Repeating the full import must not increase observation counts.
7. Derive the production candidate twice and require identical canonical hashes.

Do not treat the existing derived K-lines as exchange-quality OHLC. Do not use a fallback grade to construct PSA 10 market cap. Do not direct-read G10 or legacy tables from the web application.

## Daily incremental job

Task Scheduler runs one private process daily at 06:30 JST with overlap disabled. A lock and run ID prevent two publishers from racing. The required sequence is:

1. Run the private collector with image acquisition enabled, then read the last successful checkpoint and the current 600-card local generation.
2. Validate payload hashes and append only unseen raw observations.
3. Resolve exact identities and quarantine conflicts without weakening the production gate.
4. Refresh GemRate identity/population within its plan limits; use private G10 population fallback according to the data contract.
5. Record one daily PSA 10 reference close and top-grade PSA/BGS/CGC/SGC populations. Missing SGC price remains unavailable.
6. Derive 1d, 7d, and 30d close-to-close changes from canonical daily observations. Missing anchors remain accumulating.
7. Rebuild partial tracked-sales aggregates for 1d, 7d, and 30d. A day with no observed sale is not automatically zero coverage.
8. Rank confirmed cards by unrounded `PSA 10 price × PSA 10 population`; require population at least 1,000, fresh price, complete number, and QC-passed raw front.
9. Generate an immutable sanitized candidate and run data, image, release, and leak gates.
10. Publish the candidate generation, verify it remotely, run staging canaries, then advance the pointer.

The job must return nonzero on any failed stage. Its bounded private log contains run ID, generation ID, step status, counts, hashes, and redacted error categories only.

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

## Production stop gates

Do not cut over production until all are true:

- JLP migration runner and database owner are confirmed.
- Full import replay is idempotent and daily incremental checkpoints are proven.
- A real unattended scheduled run has completed successfully.
- Strict release gate passes with exactly 100 eligible cards and no demo blocker.
- Official-registry production dependency audit passes without forcing packages outside their declared compatibility ranges.
- All public Top 100 assets are exact-match raw fronts.
- Staging live canary passes for the expected build and generation.
- Cloudflare zone controls and production route are explicitly approved.
- Previous Worker version and last-good data generation are recorded and tested for rollback.
