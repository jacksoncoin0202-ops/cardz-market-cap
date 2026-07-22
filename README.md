# CARDZ Market Cap

CARDZ Market Cap is an art-market-first intelligence product for trading cards. It combines gallery-grade presentation with a strict market-data contract for traders, collectors, and researchers.

The repository is a clean-room rebuild. The legacy application is a read-only import source and is never a runtime dependency. Public pages read only a sanitized, versioned snapshot generated from canonical records.

## Product principles

- Art first, then evidence. Card imagery and visual calm establish context before dense data appears.
- Data must be traceable. Identity, freshness, image safety, and ranking eligibility are explicit gates.
- Built for 80% traders and 20% collectors. Price, population, market cap, tracked sales, and 1-day, 7-day, and 30-day change support decisions, while each card keeps its cultural story.
- Missing data stays missing. The product never turns unavailable or accumulating metrics into a false zero.
- Private collection stays private. Provider identifiers, source URLs, secrets, and unmasked slab labels never enter public snapshots or builds.

See [CARDZ_POSITIONING.md](docs/CARDZ_POSITIONING.md), [DATA_CONTRACT.md](docs/DATA_CONTRACT.md), and [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) for the product contract.

## Repository layout

```text
apps/web/                 Next.js web application and Cloudflare staging target
packages/market-data/     Public schema, resolver, ranking, and export logic
pipelines/                Private import, refresh, image QC, and publication jobs
data/private/             Immutable replay archives and fixtures, never a production database
data/public/              Sanitized deterministic seed snapshot
manifests/                Legacy import and image QC audit manifests
docs/                     Product, data, design, operations, and security contracts
scripts/                  Release and public-boundary verification
tests/                    Unit, data, and acceptance tests
```

## Local development

Prerequisites: Node.js 24, npm 11, Python 3.10 or newer, Git LFS, Wrangler, and 1Password CLI for private collection jobs.

```powershell
npm install
node scripts/verify-public.mjs --allow-demo
npm run dev
```

The web app must work from `data/public/seed-snapshot.json` without provider credentials. `--allow-demo` checks the public boundary and schema while a seed still declares honest production blockers. It does not make that generation releasable. Private refresh jobs are documented in [RUNBOOK.md](docs/RUNBOOK.md).

## Release gates

```powershell
npm run lint
npm run typecheck
npm run test
npm run test:data
npm run images:verify
npm run build
npm run verify:public
```

`verify:public` checks both source and built output. It rejects private-data imports, provider identifiers, upstream URLs, secret-like values, unmasked slab assets, ineligible Top 100 records, incomplete localization, and invalid metric status semantics.

The strict gate intentionally fails for a `demo` generation. Release requires `generation.mode` set to `production`, `productionEligible` set to true, and every declared blocker resolved.

## Deployment boundary

Code deploy and data publish are separate operations. A private Windows runner writes a versioned R2 generation, verifies it, then advances `latest.json`. Failed publication leaves the previous pointer unchanged. GitHub Actions never runs third-party collection jobs.

The private runner imports the complete local G10 generation once, then records idempotent daily price, population, rank, and tracked-sales observations. It derives close-to-close 1-day, 7-day, and 30-day changes and recalculates every eligible card's market cap. There is no 1-hour metric. The website reads the new snapshot automatically, so a daily refresh does not redeploy application code and does not require an AI task to monitor it.

G10 is the primary market-data feed. GemRate may correct canonical identity and population, while the SNK adapter remains private standby only. Production canonical writes belong to JLP MySQL 5.7; local SQLite archives exist solely for deterministic replay and tests.

The legacy worker and production route remain unchanged until staging passes its live canary and a separate production cutover is approved.
