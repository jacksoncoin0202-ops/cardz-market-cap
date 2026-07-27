# Window Feasibility Report — 1D / 7D / 30D change figures

**Measured**: 2026-07-26 (local machine time)
**DB server clock at query time**: `CURDATE()` = `2026-07-25`, `UTC_TIMESTAMP()` = `2026-07-25 21:14:39` (DB is ~1 day behind local; **every date in this report is a DB `observed_date`, not a local date**)
**Method**: one-off read-only scripts `temp/window_feasibility.py` + `temp/window_feasibility_2.py`, following the `pipelines/db_runtime.py` connection convention. Session opened with `SET SESSION TRANSACTION READ ONLY`; **SELECT only**, no writes of any kind.
**Roster**: `data/runtime/private-source-map/tracked-gemrate-ids.txt` — 1468 lines, CRLF-terminated, read in Python with `line.strip()`. 1468 distinct IDs, all 40-char.

---

## 0. Executive answer

| Metric | 1D today | 7D today | 30D today |
|---|---|---|---|
| **Price** | 85 / 1468 roster (5.8%) | 63 / 1468 (4.3%) | 63 / 1468 (4.3%) |
| **Population (PSA 10)** | 1032 / 1468 (70.3%) | **0 / 1468 (0%)** | **0 / 1468 (0%)** |
| **Market cap** (price × PSA10 pop) | 85 / 1468 (5.8%) | **0 / 1468 (0%)** | **0 / 1468 (0%)** |
| **Index** (`market_index_snapshot`) | 3 index codes | **0** | **0** |
| **Tracked sales** (`market_tracked_sales_aggregate`) | **0 rows** | **0 rows** | **0 rows** |

Three independent blockers, each fatal on its own:

1. **Population has 5 calendar days of data total (2026-07-21 → 2026-07-25), of which only 3 are usable.** 7D and 30D population change — and therefore 7D/30D market-cap change — are literally uncomputable for every single card. This is not a coverage problem, it is an "the data does not exist yet" problem.
2. **Price only ever touches 273 of the 1468 roster cards (18.6%).** 1195 roster cards have never had a single price observation. Waiting does not fix this; only adding price-source coverage does.
3. **The latest price date is 2026-07-24 and it was a partial run** (112 variants vs a normal ~170–200). 2026-07-25 has **zero** price rows. So "today's" figures are measured against a broken day — see §2.2 for the steady-state numbers.

**Two structural findings the brief did not ask about but that change what the website can claim:**

- The website's `windows.{1d,7d,30d}.changePct` is **price change, not market-cap change**. `market_candidate_daily_snapshot.change_{1,7,30}d_pct` is computed as `percent_change(price, prior_price(N))` in [`pipelines/market_alerts.py`](../pipelines/market_alerts.py). There is **no market-cap change column anywhere in the schema**. Market cap is exposed only as a level (`marketCap`).
- Every window on the website also carries a `trackedSales` block, sourced from `market_tracked_sales_aggregate` ([`canonical_public_snapshot.py:432`](../pipelines/canonical_public_snapshot.py)). That table has **0 rows**, so `trackedSales` is `unavailable` for all cards in all three windows.

---

## 1. Where each metric actually lives

| Metric | Table | Notes |
|---|---|---|
| price | `market_price_observation` | `price_usd`, UNIQUE `(variant_id, source_code, observed_date)` |
| population | `market_grader_population_observation` | PSA 10 = `grader_code='PSA'` + `top_grade_population`. `top_grade_label` is the literal string `'top'` for all 7904 rows — there is no `'PSA 10'` label to filter on. UNIQUE `(variant_id, grader_code, source_code, observed_date)` |
| **market cap** | **derived — no table** | `cap = price_usd × top_grade_population(PSA)`, computed in memory in `market_alerts.py`. Persisted only as a *result* into `market_candidate_daily_snapshot.market_cap_usd` and `market_index_constituent.market_cap_usd`. **Measuring it = measuring its two inputs.** |
| index / aggregate | `market_index_snapshot`, `market_index_constituent` | 12 and 1908 rows |
| tracked sales | `market_tracked_sales_aggregate` | **0 rows** |
| daily sales | `market_daily_sales_aggregate` | 3592 rows, `snk_psa10` only |

**Roster → variant mapping is clean 1:1**: all 1468 roster IDs resolve via `catalog_source_identity(source_code='gemrate')` to **1468 distinct `variant_id`**, 0 unmatched. The current universe lock (`id=9`, effective 2026-07-24) has exactly 1468 members. So `N / 1468` is a fair denominator throughout.

---

## 2. Q1 — Distinct observation dates

| Metric | Table | Min date | Max date | Distinct dates |
|---|---|---|---|---|
| price | `market_price_observation` | 2023-06-19 | **2026-07-24** | **1132** |
| population PSA10 | `market_grader_population_observation` | 2026-07-21 | 2026-07-25 | **5** |
| market cap | derived | — | — | see §2.3 |
| index | `market_index_snapshot` | 2026-07-21 | 2026-07-24 | **4** |
| index constituents | `market_index_constituent` | 2026-07-21 | 2026-07-24 | 4 |
| daily sales | `market_daily_sales_aggregate` | 2023-07-20 | 2026-07-24 | 215 |
| tracked sales | `market_tracked_sales_aggregate` | — | — | **0 rows** |

### 2.1 Population — the exact dates, confirmed

The brief expected very few. Confirmed: **exactly 5**, and only 3 carry real volume.

| `observed_date` | PSA variants | Roster cards | % of roster | Verdict |
|---|---:|---:|---:|---|
| 2026-07-21 | 613 | 491 | 33.4% | partial |
| 2026-07-22 | **2** | 2 | 0.1% | **failed run** |
| 2026-07-23 | **1** | 1 | 0.1% | **failed run** |
| 2026-07-24 | 1216 | 1189 | **81.0%** | best day on record |
| 2026-07-25 | 1084 | 1084 | 73.8% | partial |

PSA population by source per day:

| Date | gemrate | snkrdunk | ebay |
|---|---:|---:|---:|
| 2026-07-21 | 270 | 336 | 59 |
| 2026-07-22 | 2 | — | — |
| 2026-07-23 | 1 | — | — |
| 2026-07-24 | 1204 | 71 | 33 |
| 2026-07-25 | 1084 | — | — |

**There is no 6th date. Population history is 5 days old, full stop.**

### 2.2 Price — 1132 dates, but that number is misleading

1132 distinct dates all come from **one source**. See §6. The 3-year depth belongs to 336 variants only.

Recent daily volume (distinct variants with a price row):

| Date | variants | Date | variants |
|---|---:|---|---:|
| 2026-07-16 | 191 | 2026-07-21 | 185 |
| 2026-07-17 | 177 | 2026-07-22 | 195 |
| 2026-07-18 | 173 | 2026-07-23 | 168 |
| 2026-07-19 | 394 (backfill spike) | **2026-07-24** | **112 ← partial run** |
| 2026-07-20 | 206 | **2026-07-25** | **0 ← no rows** |

### 2.3 Market cap — only 2 dates where both inputs land on the same day

Intersecting each variant's price dates with its PSA population dates yields exactly **2 distinct dates across the entire database: 2026-07-21 and 2026-07-24.** On 07-22 and 07-23 the population run failed, and on 07-25 there is no price. So same-day price×pop is currently a 2-observation history.

---

## 3. Q2 — Per-window feasibility

Two definitions are reported because they answer different questions:

- **STRICT** (as specified in the brief): the card has an observation **on the table's latest date**, AND an observation **≥ N days earlier**.
- **TOLERANT** (what the shipping pipeline actually accepts): current anchor anywhere in `[latest−2, latest]`, prior anchor anywhere in `[latest−N−3, latest−N]`. These are the real `max_gap_days` values from `pipelines/market_alerts.py` (`max_gap_days=2` for current, `3` for prior, `CURRENT_POPULATION_MAX_GAP_DAYS=2`). This is the number that determines whether the site renders a figure.

### 3.1 Price — anchor 2026-07-24

| Window | STRICT roster | STRICT table | TOLERANT roster | TOLERANT table |
|---|---|---|---|---|
| 1D | **85 / 1468 (5.8%)** | 112 / 395 (28.4%) | 220 / 1468 (15.0%) | 226 / 395 (57.2%) |
| 7D | **63 / 1468 (4.3%)** | 79 / 395 (20.0%) | 222 / 1468 (15.1%) | 229 / 395 (58.0%) |
| 30D | **63 / 1468 (4.3%)** | 79 / 395 (20.0%) | 234 / 1468 (15.9%) | 241 / 395 (61.0%) |

### 3.2 Price — steady state (the honest number)

Because 2026-07-24 was a partial run, the table above understates a normal day. Sweeping the anchor date backwards (STRICT, roster cards):

| Anchor date | 1D | 7D | 30D | (tolerant 1/7/30) |
|---|---:|---:|---:|---|
| 2026-07-24 | 85 | 63 | 63 | 220 / 222 / 234 |
| 2026-07-23 | 168 | 168 | 168 | 213 / 205 / 208 |
| 2026-07-22 | 194 | 194 | 194 | 217 / 207 / 209 |
| 2026-07-21 | 161 | 161 | 161 | 272 / 230 / 233 |
| 2026-07-20 | 177 | 177 | 177 | 272 / 228 / 226 |
| 2026-07-19 | 250 | 250 | 250 | 220 / 231 / 226 |
| 2026-07-18 | 153 | 153 | 153 | 203 / 201 / 203 |
| 2026-07-17 | 155 | 155 | 155 | 212 / 207 / 205 |

**Key structural insight: for price, 1D = 7D = 30D on every healthy day.** Window length costs nothing, because 251 of the 273 price-covered roster cards already have ≥30 days of history (and in fact ≥3 years). The binding constraint is purely **how many cards get a price row on any given day** (~155–195 roster cards, 11–13%).

**Steady-state price answer: ~168–194 / 1468 roster cards (11.4%–13.2%) for all three windows, identically.**

### 3.3 Population (PSA 10) — anchor 2026-07-25

| Window | STRICT roster | STRICT table | TOLERANT roster | TOLERANT table |
|---|---|---|---|---|
| 1D | **1032 / 1468 (70.3%)** | 1032 / 1590 (64.9%) | 1361 / 1468 (**92.7%**) | 1388 / 1590 (87.3%) |
| 7D | **0 / 1468 (0.0%)** | 0 / 1590 (0.0%) | **0 / 1468 (0.0%)** | 0 / 1590 (0.0%) |
| 30D | **0 / 1468 (0.0%)** | 0 / 1590 (0.0%) | **0 / 1468 (0.0%)** | 0 / 1590 (0.0%) |

Zero is exact, not rounded. The oldest population observation in the database is 2026-07-21, which is 4 days before the latest — shorter than a 7-day window.

**Independent confirmation from the pipeline's own output**: `market_candidate_daily_snapshot.population_change_7d_pct` and `population_change_30d_pct` are `NULL` for **all 1964 rows across all 7 evaluations**. The system already computes zero population-change figures today.

### 3.4 Market cap (price × PSA10 population) — anchor 2026-07-24

A market-cap change over N days needs four anchors: price now, pop now, price N days ago, pop N days ago.

| Window | STRICT roster | STRICT (both-input variants) | TOLERANT roster |
|---|---|---|---|
| 1D | **85 / 1468 (5.8%)** | 112 / 395 (28.4%) | 214 / 1468 (14.6%) |
| 7D | **0 / 1468 (0.0%)** | 0 / 395 (0.0%) | **0 / 1468 (0.0%)** |
| 30D | **0 / 1468 (0.0%)** | 0 / 395 (0.0%) | **0 / 1468 (0.0%)** |

Ceiling: only **273 / 1468 roster cards (18.6%)** have ever had both inputs, so market cap of any kind — level or change — can never exceed 18.6% of the roster with today's sources.

### 3.5 Index / aggregate

`market_index_snapshot` — 3 index codes × 4 dates (2026-07-21 … 2026-07-24):

| index_code | dates | constituents (07-21 → 07-24) | total_market_cap_usd on 07-24 |
|---|---|---|---|
| `tcg-combined` | 4 | 270 → 194 → 228 → 262 | 2,518,403,543.65 |
| `pokemon` | 4 | 233 → 181 → 210 → 230 | 2,186,774,224.15 |
| `one-piece` | 4 | 37 → 13 → 18 → 32 | 331,629,319.50 |

- **1D index change**: computable (07-23 and 07-24 are adjacent).
- **7D / 30D index change: 0** — the entire index history spans 3 days.
- Constituent count swings 194 → 270 day to day (±28%), so even the 1D index delta is dominated by membership churn, not price movement. Treat 1D index change as unreliable until the constituent set stabilises.

`market_index_constituent.change_30d_pct` is populated (456 of 524 rows on 07-24) — but that column is a **copy of the price 30D change**, not a market-cap change, and it inherits the price coverage limits above.

### 3.6 Daily sales aggregate — anchor 2026-07-24

| Window | STRICT roster | STRICT table | TOLERANT roster |
|---|---|---|---|
| 1D | 166 / 1468 (11.3%) | 166 / 336 (49.4%) | 211 / 1468 (14.4%) |
| 7D | 109 / 1468 (7.4%) | 109 / 336 (32.4%) | 147 / 1468 (10.0%) |
| 30D | 12 / 1468 (0.8%) | 12 / 336 (3.6%) | 20 / 1468 (1.4%) |

### 3.7 Empty tables (stated as required, no proxy substituted)

| Table | Rows |
|---|---|
| `market_tracked_sales_aggregate` | **0 rows** |
| `market_fx_rate_observation` | **0 rows** |
| `market_sale_observation` | **0 rows** |

---

## 4. Q3 — The honest earliest date each window becomes computable for ≥90% of the roster

"≥90% of roster" = ≥1322 of 1468 cards.

### Population (PSA 10)

Arithmetic floor, from first observation date 2026-07-21:

| Window | Earliest possible date *any* card can have it | Status |
|---|---|---|
| 1D | already here | 70.3% strict / 92.7% tolerant |
| 7D | **2026-07-28** | 0% today |
| 30D | **2026-08-20** | 0% today |

But the ≥90% bar has a second gate that is **not** satisfied: **daily roster coverage has never reached 90%.** Best day on record is 2026-07-24 at 1189/1468 = **81.0%**; 07-22 and 07-23 were 0.1%. A window needs ≥90% coverage at *both* ends, so:

> **Let S = the first date on which the gemrate population run reliably covers ≥90% of the roster (S has not happened yet — as of 2026-07-25 the run has never exceeded 81%).**
> - 7D POP ≥90% of roster: **S + 7 days**
> - 30D POP ≥90% of roster: **S + 30 days**

If the run is fixed and first clears 90% on 2026-07-27, that gives 7D on **2026-08-03** and 30D on **2026-08-26**. Those dates are conditional on a fix that has not landed; the unconditional floor remains 2026-07-28 / 2026-08-20 and applies only to the subset of cards with unbroken coverage.

**Blunt version: 30D POP is not computable until 2026-08-20 at the absolute earliest, and not for ≥90% of the roster until roughly 2026-08-26 — and only if the daily population run starts clearing 90% coverage first.**

### Price

**≥90% of the roster is not reachable by waiting — at any future date.** The ceiling is structural:

- 273 / 1468 roster cards (18.6%) have *ever* had a price observation.
- 251 / 1468 (17.1%) have real multi-day history (the `snk_psa10` set).
- 1195 / 1468 (81.4%) have **no price row at any date, ever**.

Time does not move this number. Only wiring more roster cards into a price source does. Today's *achievable* target is the ~11–13% that get a daily row, against an 18.6% ceiling.

### Market cap

Bounded by price. **≥90% is unreachable**; ceiling 18.6%. 7D/30D additionally blocked by population until the dates above.

### Index

7D at 2026-07-28, 30D at 2026-08-20 at the earliest — but the index is built on market cap, so it inherits the price ceiling and the constituent-churn problem in §3.5.

---

## 5. Q4 — Observation cadence

Gaps are measured between consecutive **distinct observation dates per variant** (multiple sources on one day count once).

### Price — genuinely daily for the cards it covers

| | All time | Last 30 days |
|---|---|---|
| variants with ≥2 obs | 369 | 365 |
| total gaps measured | 114,685 | 5,971 |
| **median gap** | **1 day** | **1 day** |
| mean gap | 2.52 d | 1.50 d |
| p25 / p50 / p75 | 1 / 1 / 2 | 1 / 1 / 1 |
| p90 / p99 | 5 / 21 | 3 / 8 |
| max gap | 706 d | 22 d |
| % gaps exactly 1 day | 64.7% | **78.8%** |
| % gaps ≤ 7 days | 94.8% | **98.8%** |

**Verdict: genuinely daily.** In the last 30 days 78.8% of gaps are exactly 1 day and 98.8% are ≤7 days. The 706-day all-time max is historical dormancy in the 2023–2024 archive, not current behaviour. Price cadence is **not** the reason the windows are thin — coverage breadth is.

### Population — cadence cannot be established yet

| | All time (= last 30 days; only 5 dates exist) |
|---|---|
| variants with ≥2 obs | 1326 |
| total gaps measured | 1326 |
| median gap | 1 day |
| p75 / p90 / p99 | 3 / 4 / 4 |
| max gap | 4 days |
| % gaps exactly 1 day | 64.9% |

**Do not read "median 1 day" as daily.** It is computed over a 5-day span in which 2 days were failed runs (2 and 1 variants). Each variant contributes on average exactly one gap. **There is not enough history to characterise population cadence — 3 usable observation days is not a cadence.**

### Daily sales aggregate — historically bursty, recently daily

| | All time | Last 30 days |
|---|---|---|
| median gap | 2 days | 1 day |
| p90 / p99 | 8 / 47 | 4 / 9 |
| max gap | 705 d | 21 d |
| % gaps ≤ 7 days | 89.2% | 97.7% |

All-time p99 of 47 days confirms the "3 years of data but long gaps" pattern for the historical archive; the last 30 days are clean.

---

## 6. Q5 — Per-source breakdown for price

`market_price_observation`, grouped by `source_code`:

| source_code | rows | distinct dates | distinct variants | roster variants | min date | max date | last write (`MAX(created_at)`) | NULL prices |
|---|---:|---:|---:|---:|---|---|---|---:|
| `snk_psa10` | **114,755** (99.6%) | **1132** | 336 | **251** | 2023-06-19 | 2026-07-24 | 2026-07-24 | 0 |
| `snkrdunk` | 406 | **2** | 336 | 251 | 2026-07-19 | 2026-07-24 | 2026-07-24 | 0 |
| `ebay` | 92 | **2** | 59 | 22 | 2026-07-19 | 2026-07-24 | 2026-07-24 | 0 |

**This is the single most important table in the report.**

- **`snk_psa10` is the only price source with history.** 1132 distinct dates over 3 years. Everything the website can say about 7D or 30D price movement rests on this one source and its 336 variants (251 on the roster).
- **`snkrdunk` and `ebay` have 2 distinct dates each** (2026-07-19 and 2026-07-24). Despite covering 336 and 59 variants, they contribute no usable window history — they are effectively two point-in-time snapshots, not time series.
- `snkrdunk` covers the same 336 variants as `snk_psa10`, so it adds **zero** new roster cards. `ebay` adds 22. Total roster price coverage: 251 + 22 = **273**.
- All three sources last wrote on 2026-07-24. None wrote on 2026-07-25.
- No NULL `price_usd` rows anywhere — coverage gaps are missing rows, not null values.

### Population by source (PSA only, for completeness)

| source_code | rows | distinct dates | distinct variants | min | max | last write |
|---|---:|---:|---:|---|---|---|
| `gemrate` | 2561 | 5 | **1477** | 2026-07-21 | 2026-07-25 | 2026-07-25 |
| `snkrdunk` | 407 | 2 | 336 | 2026-07-21 | 2026-07-24 | 2026-07-24 |
| `ebay` | 92 | 2 | 59 | 2026-07-21 | 2026-07-24 | 2026-07-24 |

`gemrate` is the only population source with roster-scale reach (1477 variants) and the only one that wrote on 2026-07-25.

---

## 7. What this means for the website

1. **Ship 1D price now, at ~11–13% of the roster** (~168–194 cards on a healthy day; 85 on the last recorded day because 2026-07-24 was a partial run and 2026-07-25 is empty). Under the pipeline's ±2/±3-day tolerance this rises to ~205–234 cards.
2. **7D and 30D price cost nothing extra over 1D** — same cards, same counts. If 1D ships, 7D and 30D ship with it.
3. **Do not ship any 7D or 30D population or market-cap figure.** They are 0 cards, not "few cards". The earliest honest 30D population date is **2026-08-20**, and ≥90% roster coverage needs the daily population run fixed first (best ever: 81%).
4. **Do not label the window changes "market cap change".** They are price changes. There is no market-cap change stored anywhere in the schema.
5. **`trackedSales` inside every window is `unavailable`** — `market_tracked_sales_aggregate` has 0 rows.
6. **The gating problem for price is breadth, not depth or cadence.** Cadence is genuinely daily (78.8% of recent gaps = 1 day) and depth is 3 years, but 1195 of 1468 roster cards have never had a price row. No amount of waiting changes that.

---

## Appendix — SQL executed

All statements ran after `SET SESSION TRANSACTION READ ONLY` on a session that issued only `SELECT`.

```sql
SET SESSION TRANSACTION READ ONLY;

-- server clock
SELECT CURDATE() AS d;
SELECT UTC_TIMESTAMP() AS t;

-- roster -> variant mapping (roster file read in Python with line.strip(); CRLF-safe)
SELECT external_entity_id, variant_id
FROM catalog_source_identity
WHERE source_code = 'gemrate';

-- confirm the PSA "top grade" label and estimated-flag distribution
SELECT grader_code, top_grade_label, estimated,
       COUNT(*) AS rows_n, COUNT(DISTINCT variant_id) AS variants_n
FROM market_grader_population_observation
GROUP BY grader_code, top_grade_label, estimated
ORDER BY rows_n DESC;

-- metric datasets: (variant, date) pairs, feasibility/cadence computed in Python
SELECT DISTINCT variant_id, observed_date
FROM market_price_observation
WHERE price_usd IS NOT NULL;

SELECT DISTINCT variant_id, observed_date
FROM market_grader_population_observation
WHERE grader_code = 'PSA'
  AND top_grade_population IS NOT NULL
  AND estimated = 0;

SELECT DISTINCT variant_id, observed_date
FROM market_daily_sales_aggregate;

-- Q5: per-source price breakdown
SELECT source_code,
       COUNT(*)                        AS rows_n,
       COUNT(DISTINCT observed_date)   AS distinct_dates,
       COUNT(DISTINCT variant_id)      AS distinct_variants,
       MIN(observed_date)              AS min_date,
       MAX(observed_date)              AS max_date,
       MAX(created_at)                 AS last_write_at,
       SUM(price_usd IS NULL)          AS null_price_rows
FROM market_price_observation
GROUP BY source_code
ORDER BY rows_n DESC;

SELECT DISTINCT source_code, variant_id
FROM market_price_observation
WHERE price_usd IS NOT NULL;

-- population per source / grader
SELECT source_code, grader_code,
       COUNT(*) AS rows_n,
       COUNT(DISTINCT observed_date) AS distinct_dates,
       COUNT(DISTINCT variant_id)    AS distinct_variants,
       MIN(observed_date) AS min_date, MAX(observed_date) AS max_date,
       MAX(created_at)    AS last_write_at
FROM market_grader_population_observation
GROUP BY source_code, grader_code
ORDER BY rows_n DESC;

-- daily volume
SELECT observed_date, COUNT(*) AS rows_n, COUNT(DISTINCT variant_id) AS variants_n
FROM market_price_observation
WHERE observed_date >= DATE_SUB('2026-07-25', INTERVAL 35 DAY)
GROUP BY observed_date
ORDER BY observed_date;

SELECT observed_date, source_code,
       COUNT(*) AS rows_n, COUNT(DISTINCT variant_id) AS variants_n
FROM market_grader_population_observation
WHERE grader_code = 'PSA'
GROUP BY observed_date, source_code
ORDER BY observed_date, source_code;

SELECT observed_date, grader_code, COUNT(DISTINCT variant_id) AS variants_n
FROM market_grader_population_observation
GROUP BY observed_date, grader_code
ORDER BY observed_date, grader_code;

-- index / aggregate
SELECT index_code, index_version, effective_date,
       constituent_count, total_market_cap_usd
FROM market_index_snapshot
ORDER BY index_code, effective_date;

SELECT s.effective_date, COUNT(*) AS rows_n,
       COUNT(DISTINCT c.variant_id) AS variants_n,
       SUM(c.change_30d_pct IS NOT NULL) AS with_change_30d
FROM market_index_constituent c
JOIN market_index_snapshot s ON s.id = c.index_snapshot_id
GROUP BY s.effective_date
ORDER BY s.effective_date;

-- what the pipeline itself already produces, per evaluation run
SELECT e.id AS evaluation_id, e.effective_date,
       COUNT(*) AS rows_n,
       COUNT(DISTINCT d.variant_id) AS variants_n,
       SUM(d.market_cap_usd IS NOT NULL)             AS with_market_cap,
       SUM(d.change_1d_pct IS NOT NULL)              AS with_price_1d,
       SUM(d.change_7d_pct IS NOT NULL)              AS with_price_7d,
       SUM(d.change_30d_pct IS NOT NULL)             AS with_price_30d,
       SUM(d.population_change_7d_pct IS NOT NULL)   AS with_pop_7d,
       SUM(d.population_change_30d_pct IS NOT NULL)  AS with_pop_30d
FROM market_candidate_daily_snapshot d
JOIN market_alert_evaluation e ON e.id = d.evaluation_id
GROUP BY e.id, e.effective_date
ORDER BY e.effective_date, e.id;

-- empty-table confirmation
SELECT COUNT(*) AS n FROM market_tracked_sales_aggregate;
SELECT COUNT(*) AS n FROM market_fx_rate_observation;
SELECT COUNT(*) AS n FROM market_sale_observation;

-- current universe size (denominator sanity check)
SELECT l.id AS lock_id, l.effective_at, COUNT(m.variant_id) AS members
FROM market_universe_lock l
LEFT JOIN market_universe_member m ON m.universe_lock_id = l.id
WHERE l.is_current = 1
GROUP BY l.id, l.effective_at;
```

**Feasibility / cadence definitions applied in Python** (against the `(variant_id, observed_date)` sets above):

```python
# STRICT (brief): observation on the table's latest date AND one >= N days earlier
strict = (latest in dates) and (min(dates) <= latest - timedelta(days=N))

# TOLERANT (pipeline, market_alerts.py max_gap_days=2 current / 3 prior)
current_ok = any(latest - timedelta(days=2) <= d <= latest for d in dates)
prior_hi   = latest - timedelta(days=N)
prior_ok   = any(prior_hi - timedelta(days=3) <= d <= prior_hi for d in dates)
tolerant   = current_ok and prior_ok

# market cap: BOTH price and PSA10 population must satisfy the test
market_cap_ok = price_ok(variant) and population_ok(variant)

# cadence: gaps between consecutive DISTINCT dates, per variant
gaps = [(d[i] - d[i-1]).days for i in range(1, len(sorted_distinct_dates))]
```

Scripts: `temp/window_feasibility.py`, `temp/window_feasibility_2.py`.
Raw results: `temp/window_feasibility_result.json`, `temp/window_feasibility_result2.json`.
