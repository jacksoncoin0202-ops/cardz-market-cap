# CARDZ Data Contract

## Purpose

This contract defines the only route from private bootstrap evidence, GemRate
population, and SNK market observations to the public CARDZ Market Cap product.
G10 is bootstrap/research evidence only, not a market boundary or authority.
Public UI code consumes only a sanitized, versioned snapshot. Direct reads from
`grade10-scraper`, `cardz-platform`, private landing objects, replay SQLite, or
provider APIs are release-blocking errors.

## Authority and data layers

### Private landing

The complete source generation and every later run are immutable evidence:

```text
g10/full/<generation>/
g10/incremental/YYYY-MM-DD/<run-id>/
```

The initial full generation is imported once as discovery evidence. Daily
collectors refresh current facts for target and radar IDs, append only new
observations, and queue historical backfill after a card earns its target rank.
Every run records source hashes, effective and fetched timestamps, row counts,
accepted/quarantined/rejected counts, and completion state. Raw objects may
contain provider IDs and URLs and therefore never enter public paths or browser
bundles.

### Production canonical history

The current authority is one immutable private run namespace replayed
idempotently into the standalone MySQL-compatible operational database,
followed by a sanitized versioned public snapshot. The database is derived
state and must be rebuildable from the approved canonical seed/archive; it is
never a second hand-edited catalogue. Writable replay SQLite is not production
authority. A future JLP adapter may consume the same normalized observations,
but it is not a current release gate and cannot change ranking semantics
silently.

Local SQLite and compressed archives are immutable replay/test artifacts only. They cannot become a second production database.

### Public generation

Cloudflare stores immutable sanitized generations. The private publisher writes and verifies a candidate generation before advancing the last-good pointer. A failed or partial run cannot modify the active public generation.

## One-way flow

```text
Private discovery/bootstrap evidence   (integrations/grade10/)
          + broad current candidate discovery
          + GemRate identity/population (pipelines/gemrate_source.py → data/private/gemrate/)
          + private SNK price/history   (pipelines/snk_market_data.py)
                              ↓
                    private landing evidence
                              ↓
             exact identity, freshness, sales, image QC
                              ↓
              canonical facts and complete rankings
                              ↓
                configurable presentation views
                              ↓
                sanitized versioned public generation
                              ↓
                    Cloudflare Worker-rendered UI
```

There is no public-to-legacy fallback and the browser never calls an upstream provider.

## Target-first collection

The first full run has two separate phases. Phase one collects only the current
facts required to establish a defensible market ranking: broad candidate
discovery, exact canonical identity, GemRate current PSA 10 population, and
exact SNK current PSA 10 reference price. It then derives complete eligible
rankings for `tcg-combined`, `pokemon`, and `one-piece`; the existing Grade10
set is bootstrap evidence, never the global market boundary.

Phase two deduplicates the configured high-value presentation ranges and
backfills their available population history, price history, and tracked sales.
Cards outside a historical target retain current/radar evidence and enter the
backfill queue automatically when their rank qualifies. The database therefore
stores one canonical printing and its facts once; a ranking or display cutoff
does not create another card table or duplicate observations.

Pokémon and One Piece printings may be Japanese, English, Korean, Traditional
Chinese, or Simplified Chinese when exact source evidence exists. Language is
canonical identity metadata, not a separate ranking universe. Thai printings
are out of scope.

## Public metric model

```ts
type MarketWindow = "1d" | "7d" | "30d";
type MetricStatus = "ready" | "accumulating" | "stale" | "unavailable";
type CoverageStatus = "partial" | "stale" | "unavailable";
type Grader = "PSA" | "BGS" | "CGC" | "SGC" | "TAG";

interface MarketMetric {
  value: number | null;
  status: MetricStatus;
  asOf: string | null;
  anchorAt?: string | null;
}

interface WindowMetric {
  changePct: MarketMetric;
  trackedSales: {
    valueUsd: MarketMetric;
    count: MarketMetric;
    coverage: CoverageStatus;
    asOf: string | null;
  };
}
```

Rules:

- Missing, accumulating, or unavailable data is null, never fake `0`, `0.00%`, or a flat line.
- `1d` means adjacent daily batch close-to-close, not rolling 24 hours and not a 1-hour metric.
- 7d and 30d use the nearest eligible daily anchor without crossing canonical printing, language, grade, currency basis, or series version.
- Source price and tracked transaction price are distinct series. Transaction medians cannot silently replace the reference/index price.
- Price age `≤30h` is ready, `30–48h` is stale, and `>48h` is unavailable and ineligible.
- CARDZ derives 1d, 7d, and 30d from exact PSA 10 daily closes. A verified SNK history backfill may immediately supply these anchors; copied provider percentages are never canonical. Missing anchors remain accumulating.
- A historical anchor from a different authority family is never mixed into
  the current source's change calculation. It is a review warning and that
  change window stays accumulating; it does not invalidate an otherwise exact,
  fresh current PSA 10 price or delist the card.

## Tracked sales

Tracked sales are partial observations, not total market volume. For each window the private pipeline stores count, transaction value, unit price basis, quantity, grade, sale date, fetched time, and private provenance.

- Bundle `transaction_value` is never used as single-card `unit_price`.
- A source-supplied sale date is preserved; discovery date does not replace it.
- Repeated rolling windows are reconciled without collapsing distinct same-day same-price transactions.
- A source window with no provable coverage displays `—`, not `$0`.
- Exact-bound SNK recent trades, Grade10-cached eBay PSA 10 completed sales, and
  PriceCharting-page eBay PSA 10 completed sales are equal primary transaction
  evidence. All three pass the same printing, grade, timestamp, quantity,
  dedupe, and 30-day checks; an unbound cache remains bootstrap evidence only.
- The SNK identity family is closed over `snk_psa10`, `snk`, and `snkrdunk`.
  A numeric sale card ID may bind across those aliases only when the exact
  numeric external ID is identical; names never bridge the aliases.
- Current source pagination is incomplete, so public coverage remains `partial` until a stronger feed proves otherwise.

## Operational source and retry policy (hard)

- Cardz Market Cap is not CardzOS, CardzPSA10, CARDZ.Game/JLP, or Kado. Its
  only business database is MySQL `cardz_market_cap` and production execution
  uses the WSL repo plus `/home/jackson0202/cardz-market-cap/.venv-backend`.
- A provider pass collects every useful field available on the exact product
  page in one capture. Parsing may split those fields into price, sales,
  identity, and raw-reference rows afterwards; it must not refetch the same
  product once per field.
- An exact source binding is durable. Full stock repair runs once; later runs
  fetch only new observations plus retry-worklist failures. After targeted
  PriceCharting repairs, rebuild the current-exact map with
  `pc_full_serial_driver.py --consolidate-only`; never rerun all provider
  shards merely to refresh the local map.
- G10 may transport saved eBay or SNK evidence for research, but G10 index,
  kline, analytics, and any `g10*` price source code are forbidden in the
  canonical DB, market-cap calculation, and frontend snapshot. Accepted facts
  retain their real market family (`ebay` or `snk*`) and exact printing
  provenance.
- Do not discard a valid authority's observations merely because another
  authority covers the same product. Writers remain idempotent on their own
  durable item/fingerprint and preserve source-family provenance.
- Every completed canonical DB QC, including a blocked run, must be projected
  with `qc_failure_sync.py --write`. The price and sales lanes are exported as
  deterministic retry worklists. A blocked QC then stops publication; it does
  not lose the work queue.
- Before any image work, `run_daily.py` derives an immutable
  `price-sales-gate.json` from that same QC report. Only cards with a confirmed
  authority price and at least 10 exact single-card PSA 10 sales in 30 days
  enter the image batch; final full QC remains the release gate.

## Price history and chart integrity

V1 exposes daily reference-price points and tracked-sales aggregates. It may expose an observed sale range/median when clearly labelled partial.

The current derived candle files are quarantined because price sorting cannot establish open/close and carried rows are not sale days. Candlesticks remain disabled until ordered transaction timestamps, stable pagination, and a consistent grader/grade series exist.

## Canonical identity and CardzGame crosswalk

A publishable printing has an opaque CARDZ ID plus exact TCG, card language, set identity/code, complete collector number, edition, parallel, and finish evidence. Language is identity-bearing: otherwise identical JA and EN cards are different canonical printings and must never share an image or source binding. Ranking boards may still group languages, but that grouping cannot rewrite printing identity. Examples include `227/S-P`, `294/XY-P`, and `OP01-120`. Frontend code may not guess a missing language, suffix, prefix, denominator, zero padding, edition, parallel, or finish.

### Identity verification (hard)

**Every verified fact about a printing hangs off one `catalog_variant.id`.** Binds, external URLs, POP snapshots, sold evidence, and human/agent reviews are accumulated on that id (see `catalog_identity_evidence` / `pipelines/identity_evidence_ledger.py`). Agents must not leave verification only in chat.

**Identity trusted when any two independent sources agree it is the same printing** (collector + set/edition + parallel). Sources in the pool: **GemRate**, **SNK**, **PriceCharting**, **PSA official**. A third/fourth source thickens evidence; it is **not** required for the identity minimum.

- **English cards:** PriceCharting product pages are expected to exist; missing PC is usually "not fetched / CF session stale", not "no market".
- **PriceCharting access:** use `pipelines/pricecharting_cf_session.py` (or saved CF session). Direct HTTP/`web_fetch` hitting Cloudflare does **not** count as PC failure of the source.
- Conflict between two sources on printing ⇒ fail-closed; no invent, no averaging POP.

**tcgpricelookup is not an identity authority** for ranking (wrong-slug risk). Full criteria: [`IDENTITY_VERIFICATION_CRITERIA.md`](IDENTITY_VERIFICATION_CRITERIA.md).

Provider mappings remain private and separate from CardzGame mappings. CardzGame `card`, `card_chip`, and `card_pack_sub` records map through canonical printing/variant IDs. Fragment value is derived from the mapped variant price multiplied by the fragment ratio; fragments do not receive independent provider prices.

Ambiguous identity, language conflicts, incomplete collector numbers, fuzzy-only matches, and semantic image mismatches enter `identity_review_queue` and cannot enter a public ranking.

### Parallel and language identity (hard)

- The executable language policy, evidence order, closed language-code set, and
  twin-repair contract are maintained in [`CARD_LANGUAGE.md`](CARD_LANGUAGE.md).
- Parallel is part of printing identity: Base / Alternate Art / Manga / Wanted / Special / Reverse Holo are distinct printings and distinct canonical variants. Their POP, price, and sales are never merged into one variant.
- One GemRate member maps to exactly one canonical variant. A POP observation write that would merge multiple members of one variant is fail-closed skipped and counted as quarantined.
- Source bindings must match the variant's `card_language`. SNK and PriceCharting list Japanese and English printings as separate products with separate prices, sales, and charts.
- Twin-set hazard: Japanese 151 versus English 151 share names and numbers. Resolve through the correct language-specific set path only; a bare name+number search is not evidence.
- PriceCharting shard/workfile language is never authoritative. Before any browse or bind, the runner must replace it with the current canonical variant language and fail closed when that language is missing or unsupported.
- A current exact PriceCharting product binding is the card/printing identity gate for that product page. Its completed-sale titles are transport metadata, so they must not be required to repeat the collector number, card name, or language. The sale writer still rejects missing/invalid item IDs, non-positive prices, invalid dates, non-PSA-10 grades, raw cards, and bundles; a missing, stale, or ambiguous exact product binding rejects the whole map row.
- Because language changes the opaque public ID, QC retry projection reconciles
  rekeys by stable `variantId + lane`: the old opaque-ID item is resolved and
  only a still-current blocker is re-opened under the new public ID.

### Price authority and liquidity (hard)

- Exact PriceCharting PSA 10, exact SNK PSA 10, and exact eBay PSA 10 sold medians are accepted primary source families. PriceCharting raw, generic graded, asking-price, and unmatched product rows are not PSA 10 price authority.
- CARDZ owns the final QC decision: one exact-bound, ready authority source family observed within 48 hours is enough to confirm a current reference price. When two or more authority families are available and `max(price) / min(price) > 2.0`, the card fails closed into the PSA 10 price retry lane until another authoritative leg resolves the conflict.
- With one fresh authority family, its exact price is the reference price. With two or more fresh authority families inside the 2x guard, the reference price is their arithmetic mean; the individual source values remain in private evidence.
- When only sales exist, the shadow reference price is the median unit price of PSA 10 sales in the last 30 days; freshness follows the operator window (90 days).
- Unit price is always transaction value divided by quantity. Unresolved bundles are rejected, never averaged into single-card prices.
- Liquidity gate: a card needs at least 10 PSA 10 sales in the last 30 days across approved channels to stay frontend-listed. Nine or fewer is low-liquidity — a delisting status, not a deletion — and returns automatically when sales resume.

## Images

The public heatmap and ranking accept only `raw_front` images that pass all of these checks:

- TCG, language, collector number, and printing semantic match.
- Full card face is visible with safe `object-contain` presentation.
- No slab case, grader label, barcode, or certification number.
- Deterministic `cardz-front-geometry-v1` passes before semantic review: RGBA
  transparent 429×600 canvas, card fill at least 95% on both axes, card aspect
  ratio 0.68–0.75, and centre offset no more than 6 px. Matching canvas
  dimensions alone are not approval.
- `cardz-source-sample-v1` runs before OCR. Known SAMPLE-only source paths and
  proven One Piece TCGplayer watermark-template dimensions fail immediately;
  every remaining source family still receives SAMPLE OCR. Explicit source
  language markers must match the variant language before human review.
- Content hash, dimensions, source version, QC timestamp, and reviewer/method are recorded privately.

Slab, label crop, unmasked grader asset, and uncertain card crop may remain private evidence but cannot be copied into public assets or build traces. A changed upstream hash forces a new classification and QC run.
Local VLM output is an immutable review receipt only. It may create a
human/vision confirmation candidate, but it never promotes a DB row or public
asset by itself.

The active stock-image review path is script-first and does not use a local
LLM: source policy, geometry, then WSL Tesseract. Remaining candidates enter
the localhost review proxy. An operator decision code binds the dataset hash,
variant ID, asset ID, and content hash; the DB writer rechecks the latest asset
and all hashes before applying `human-review-v1`.

## Ranking, presentation views, and grader markets

All three ranking scopes use one comparable formula:

```text
PSA 10 market cap = current PSA 10 reference price × PSA 10 population
```

Eligibility requires:

```text
PSA 10 population >= 1000
AND population is not estimated
AND canonical identity is confirmed
AND identity confirmed by at least two independent sources
    (any two of GemRate / SNK / PriceCharting / PSA on same printing)
AND identity evidence hung on this variant_id (ledger)
AND collector number is complete
AND price age <= 48 hours
AND raw-front image QC passed
```

Human/agent reviews must always include **PSA10 POP** and external verification URLs (at least GemRate + PriceCharting/SNK when available). See `IDENTITY_VERIFICATION_CRITERIA.md`.

Eligible cards are sorted by unrounded market cap into complete rankings for
`tcg-combined`, `pokemon`, and `one-piece`. The canonical database stores
membership/rank only and never duplicates the printing record. `top100`,
`top300`, and `top350` are rank-range views; `top100_plus_200` is the `top300`
alias, and `reserve50` is ranks 301–350 for private operations. A requested
view fails closed only when that view lacks enough eligible cards. It never
changes what canonical facts the database may collect or preserve.

PSA, BGS, CGC, and SGC have independent supply/coverage views. A grader-specific market-cap ranking exists only when the same grader/grade has reliable price and population coverage. Missing grader price is unavailable, not estimated. Four grader populations are never summed into the PSA index.

The four-grader population and population-history series are collected by `pipelines/gemrate_source.py` into `data/private/gemrate/`. GemRate supplies population only; daily reference prices come from the exact private price-history collector, with G10 retained as bounded last-good bootstrap evidence. Public snapshots project from private observations through the same sanitation gates as every other provider — provider IDs, `gemrate_id` values, and upstream URLs never enter public output.

The V1 demo grader routes currently project grader population fields from the PSA-eligible public subset. They are not a complete canonical grader supply universe and must not be described as an all-market BGS, CGC, or SGC ranking. A grader-wide claim remains blocked until the standalone collector exports an independently gated supply universe for that grader; future JLP integration is not a current dependency.

## Public snapshot boundary

Public records may contain only CARDZ opaque IDs, canonical identity, complete collector number, four-locale content, safe raw image references, current PSA metrics, window metrics, grader populations, and timestamps.

They must not contain provider IDs or names, upstream URLs, raw payloads, private object keys, local paths, API credentials, cookies, unmasked slab assets, or source maps. R2 remains private; the Worker returns only the records required for the requested page, not a downloadable database or full private history.

## Localization and currency

Every public printing has independent English, Traditional Chinese, Simplified
Chinese, and Japanese editorial content when the selected public view requires
it. Production cannot silently fall back to English. Chinese copy uses written
language. Card-language identity is separate from interface locale: a Korean
printing must additionally preserve its Korean native card/set name even before
a full Korean interface locale is introduced.

USD is the stored base. HKD, CNY, GBP, TWD, JPY, and KRW use the generation's freshness-checked daily FX observation; conversion never mutates base metrics. The private runner caches a validated last-good FX response and never calls the currency service from a browser request. Locale, currency, and selected window persist in canonical public routes.

## Publication sequence

1. Acquire a private full or daily run and finalize its manifest.
2. Validate completeness and import it idempotently into the standalone immutable canonical replay; a future JLP adapter may consume the same normalized batch.
3. Resolve identity, price, population, windows, tracked sales, images, rank, localization, and FX.
4. Build a new sanitized generation and run data/privacy/image gates.
5. Upload versioned objects and verify their hashes.
6. Run live canaries against the candidate generation.
7. Advance the last-good pointer only after every check succeeds.

Any failure leaves the active generation unchanged.
