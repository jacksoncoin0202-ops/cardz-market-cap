# CARDZ Market Cap

CARDZ Market Cap is an art-market-first intelligence product for trading cards. It combines gallery-grade presentation with a strict market-data contract for traders, collectors, and researchers.

The repository is a clean-room rebuild. The legacy application is a read-only import source and is never a runtime dependency. Public pages read only a sanitized, versioned snapshot generated from canonical records.

## Product principles

- Art first, then evidence. Card imagery and visual calm establish context before dense data appears.
- Data must be traceable. Identity, freshness, image safety, and ranking eligibility are explicit gates.
- Built for 80% traders and 20% collectors. Price, population, market cap, tracked sales, and 1-day, 7-day, and 30-day change support decisions, while each card keeps its cultural story.
- Missing data stays missing. The product never turns unavailable or accumulating metrics into a false zero.
- Private collection stays private. Provider identifiers, source URLs, secrets, and unmasked slab labels never enter public snapshots or builds.

See [CARDZ_POSITIONING.md](docs/CARDZ_POSITIONING.md), [DATA_CONTRACT.md](docs/DATA_CONTRACT.md), [FRONTEND_HANDSHAKE.md](docs/FRONTEND_HANDSHAKE.md), and [DESIGN_SYSTEM.md](docs/DESIGN_SYSTEM.md) for the product contract.

## Repository layout

```text
apps/web/                 Next.js web application and Cloudflare staging target
packages/market-data/     Public schema, resolver, ranking, and export logic
pipelines/                Private import, refresh, image QC, and publication jobs
data/private/             Immutable replay archives and fixtures, never a production database
data/public/              Sanitized deterministic seed snapshot
integrations/grade10/     Vendored private acquisition dependency and operator guides
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

## Backend control plane

`config/data-routing.json` is the single machine-readable control plane for
data authority, transport, importer, canonical storage, ranking, presentation
views, operator tools, and tests. It separates one canonical fact set from its
derived rankings and from the Top 100/300/350 projections used by a consumer.

```powershell
python scripts/backend.py registry --json
python scripts/backend.py explain psa10_population
python scripts/backend.py explain market_cap
python scripts/backend.py explain database.canonical
python scripts/backend.py work-items --status in_progress --priority P0
python scripts/backend.py graph --format html
python scripts/backend.py generate-docs --check
```

The generated files explain the route from a snapshot field through its metric,
database target, normalizer, collector, transport, authority, manual, and test.
They are generated from the registry; do not maintain a second set of routing
rules in a README, runbook, or frontend.

CodeGraph provides a local AST call/import index backed by SQLite/WAL. It is a
read-only engineering aid, not a data authority or a second architecture
registry:

```powershell
npm run graph:sync
npm run graph:status
.\node_modules\.bin\codegraph.cmd explore "GemRate candidate backfill canonical DB"
```

Before changing backend code, locate the affected registry node or work item
with `backend.py explain`, use CodeGraph to inspect the implementation impact,
edit only the owner files named by that work item, then regenerate and check
`docs/generated`. Generated documents and `.codegraph/codegraph.db` are never
hand-edited.

## Portable backend

The operational database uses one Python entrypoint on Windows and Linux. A local or EC2-hosted MySQL container needs Docker Compose:

```text
git lfs pull
python scripts/backend.py bootstrap --bootstrap-archive data/private/cardz-active-bootstrap.tar.gz
python scripts/backend.py status
python scripts/backend.py daily
```

For Amazon RDS or another managed MySQL service, inject the `CARDZ_DB_*` values through the server secret manager and skip local Docker:

```text
git lfs pull
python3 scripts/backend.py bootstrap --external-db --mode production --bootstrap-archive data/private/cardz-active-bootstrap.tar.gz
python3 scripts/backend.py daily --external-db --mode production
```

Amazon Linux 2023 keeps its unversioned `python3` on Python 3.9, below this repo's Python 3.10 minimum. Install a supported versioned interpreter. `backend.sh` automatically selects an installed `python3.10` or newer, or accepts an explicit override such as `CARDZ_PYTHON=python3.12 bash scripts/backend.sh bootstrap --external-db ...`; do not repoint the system Python symlink. See [AWS's AL2023 Python guidance](https://docs.aws.amazon.com/linux/al2023/ug/python.html).

The seed/archive contains canonical identities, dated normalized observations,
and manifests needed to restore a database. It does not define a rank cutoff:
Top 100, Top 300, Top 350, `top100_plus_200`, and `reserve50` are projections of
the same complete eligible rankings. It excludes raw provider payloads, images,
provider secrets, and undated derived price rows. Archive verification is
mandatory before restore. `--restore-overwrite` is deliberately explicit and
is only for replacing an already-restored runtime tree.

`backend.ps1` and `backend.sh` are thin convenience launchers only; migration, replay, integrity checks, daily collection, and platform detection live in `backend.py`. A second import is idempotent and must report zero new observations. External database settings must come from the current process environment; the runner never reuses the ignored local-Docker password file for RDS. Production external-database runs require `CARDZ_DB_SSL_CA` to point at the readable managed-database CA bundle.

`backend.py daily` is deliberately the portable backend-data job: it validates
routes, refreshes discovery/radar facts, collects current population and price,
appends canonical observations, recalculates complete eligible rankings, queues
historical backfill for new high-value targets, and validates MySQL. It does
**not** build or promote the public R2 generation yet. The DB-derived public
exporter is a separate remaining integration gate; running the backend job must
not be described as a live website publication.

The broad discovery/bootstrap collector is now self-contained under `integrations/grade10/`. Its exact upstream operating instructions are preserved in [OPERATOR_GUIDE.md](integrations/grade10/OPERATOR_GUIDE.md), while [the integration README](integrations/grade10/README.md) defines the portable CARDZ entrypoint. Run `python integrations/grade10/run_service.py self-check` after a clean clone. An explicit `--refresh-bootstrap-source` uses the vendored runner by default; no sibling `grade10-scraper` checkout or Windows batch file is required. The legacy analytics and synthetic K-line scripts are retained for replay compatibility only and are not canonical CARDZ ranking inputs.

Daily market alerts are persisted by `python scripts/backend.py alerts`. Normal
`backend.py daily` runs the same evaluator after canonical import. The radar
stores current evidence outside the historical target set and promotes a card to
historical backfill when it enters a configured presentation range.

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

`verify:public` checks both source and built output. It rejects private-data imports, provider identifiers, upstream URLs, secret-like values, unmasked slab assets, ineligible Top 300 records, incomplete localization, and invalid metric status semantics.

The strict gate intentionally fails for a `demo` generation. Release requires `generation.mode` set to `production`, `productionEligible` set to true, and every declared blocker resolved.

## Deployment boundary

Code deploy, backend data sync, and public-data publication are separate operations. The portable Python backend currently completes the first two. The public publisher must later export from the validated database into a versioned R2 generation, verify it, and only then advance `latest.json`; failed publication leaves the previous pointer unchanged. GitHub Actions never runs third-party collection jobs.

The private runner uses G10 only as discovery/bootstrap evidence, then records
idempotent daily price, population, rank, and tracked-sales observations in one
canonical store. It derives complete eligible rankings for combined TCG,
Pokémon, and One Piece. A canonical printing is stored once and may belong to
more than one ranking scope; language remains identity metadata only. Top 100,
Top 300, Top 350, and `top100_plus_200` are presentation views sliced from the
same generation. It derives close-to-close 1-day, 7-day, and 30-day changes and
recalculates every eligible card's market cap. There is no 1-hour metric. The
website reads the new snapshot automatically, so a daily refresh does not
redeploy application code and does not require an AI task to monitor it.

G10 is historical bootstrap/research evidence only: old summaries, mappings, sample payloads, and last-good comparison values. It is not a canonical provider and is not a completeness boundary. GemRate supplies canonical identity and grader population; its direct API is preferred, while Grade10 `price.getGradingPopulations` is a current-population GemRate mirror transport with retained provenance. SNK supplies exact PSA 10 reference prices and recent trades, eBay will supply additional tracked sold transactions after its repository-owned exact-grade adapter passes validation, and CARDZ derives the three Top 300 indexes plus 1d/7d/30d metrics. JLP is only a future integration seam and is not required to run or publish CARDZ Market Cap.

Supported card languages for both Pokémon and One Piece are Japanese, English, Korean, Traditional Chinese, and Simplified Chinese. They distinguish printings during exact identity resolution but do not produce independent rankings. A language with no exact source data remains unavailable; English or Japanese data never fills it silently. Thai printings are out of scope.

Data sources are first-class collectors coordinated by one 06:30 parent task:

- `integrations/grade10/grade10_scraper.py` (G10) — bootstrap/research evidence only; `price.getGradingPopulations` is permitted solely as a provenance-labelled GemRate current-population mirror transport.
- `pipelines/source_crosswalk.py` — builds the private exact discovery crosswalk; no name-search matching.
- `pipelines/active_universe.py` — applies canonical identity, population,
  market-cap, and momentum gates to current discovery facts, derives complete
  rankings, and emits target/history queues plus radar evidence.
- `pipelines/gemrate_source.py` — preferred direct GemRate population source for active PSA/BGS/CGC/SGC identities; full history is a backfill operation, not a daily download.
- `pipelines/tag_daily_capture.py` — auxiliary TAG population coverage. A live failure can reuse only a checksum-valid catalog captured within 72 hours while preserving its original observation time; otherwise TAG is omitted and cannot stop the primary SNK price run.
- `pipelines/snk_market_data.py` — daily PSA 10 reference-price history and partial recent-trade observations for active exact identities.
- `pipelines/market_source_sync.py` — normalizes immutable observations and derives combined, Pokémon, and One Piece private rankings.

The machine-readable source contract is `config/data-routing.json`; see `docs/DATA_ROUTING.md` and `docs/DATA_SOURCE_INVENTORY.md`. The current legacy eBay Browse client exposes active asking prices, not sold transactions, so it is not used as a price authority.

Do not install separate GemRate or SNK scheduler tasks. `pipelines/run_daily.py` owns the singleton run ID and publishes only after the complete generation passes validation.

The legacy worker and production route remain unchanged until staging passes its live canary and a separate production cutover is approved.
