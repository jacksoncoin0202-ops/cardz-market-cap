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

## Tracked sales

Tracked sales are partial observations, not total market volume. For each window the private pipeline stores count, transaction value, unit price basis, quantity, grade, sale date, fetched time, and private provenance.

- Bundle `transaction_value` is never used as single-card `unit_price`.
- A source-supplied sale date is preserved; discovery date does not replace it.
- Repeated rolling windows are reconciled without collapsing distinct same-day same-price transactions.
- A source window with no provable coverage displays `—`, not `$0`.
- Current source pagination is incomplete, so public coverage remains `partial` until a stronger feed proves otherwise.

## Price history and chart integrity

V1 exposes daily reference-price points and tracked-sales aggregates. It may expose an observed sale range/median when clearly labelled partial.

The current derived candle files are quarantined because price sorting cannot establish open/close and carried rows are not sale days. Candlesticks remain disabled until ordered transaction timestamps, stable pagination, and a consistent grader/grade series exist.

## Canonical identity and CardzGame crosswalk

A publishable printing has an opaque CARDZ ID, TCG, language, set identity/code, complete collector number, and edition/parallel/finish when those distinguish printings. Examples include `227/S-P`, `294/XY-P`, and `OP01-120`. Frontend code may not guess a missing suffix, prefix, denominator, or zero padding.

Provider mappings remain private and separate from CardzGame mappings. CardzGame `card`, `card_chip`, and `card_pack_sub` records map through canonical printing/variant IDs. Fragment value is derived from the mapped variant price multiplied by the fragment ratio; fragments do not receive independent provider prices.

Ambiguous identity, language conflicts, incomplete collector numbers, fuzzy-only matches, and semantic image mismatches enter `identity_review_queue` and cannot enter a public ranking.

## Images

The public heatmap and ranking accept only `raw_front` images that pass all of these checks:

- TCG, language, collector number, and printing semantic match.
- Full card face is visible with safe `object-contain` presentation.
- No slab case, grader label, barcode, or certification number.
- Content hash, dimensions, source version, QC timestamp, and reviewer/method are recorded privately.

Slab, label crop, unmasked grader asset, and uncertain card crop may remain private evidence but cannot be copied into public assets or build traces. A changed upstream hash forces a new classification and QC run.

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
AND collector number is complete
AND price age <= 48 hours
AND raw-front image QC passed
```

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
