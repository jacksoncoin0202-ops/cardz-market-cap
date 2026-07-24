# CARDZ Market Data Routing

This file explains the executable contract in `config/data-routing.json`.
The JSON contract is authoritative; the daily backend validates it before
discovery, collection, migration, or database writes.

> **2026-07-24 營運決定：主力 SNK + GemRate，eBay 擱置。**
> SNKRDUNK（CloudFront）同 GemRate（Cloudflare）都可以**純 `requests`/`curl_cffi` 內部直打，唔使 browser**。
> eBay 用 PerimeterX（JS fingerprinting），`curl_cffi` 過唔到，一定要 Playwright——已決定**擱置**。
> 所以而家 production 數據管線 = GemRate（population authority）+ SNK（price authority），兩個都係內部直打。
> 快速收割：`pipelines/gemrate_brute_harvest.py --all-sets`（52 sets/16,309 卡，~1 分鐘，curl_cffi）、
> `pipelines/snkrdunk_discover.py --keywords ... --harvest`（2,186 卡，requests）。

## Authority and transport

An **authority** decides what a value means; a **transport** is only the route
used to obtain that authority's value. GemRate is the authority for canonical
identity and PSA 10 population. `gemrate_direct_api` is the preferred population
transport. Grade10's `price.getGradingPopulations` response is an allowed
GemRate mirror transport for **current** population only; it never makes G10 an
authority or a ranking fallback.

When direct and mirror population observations have the same observation date,
their values must agree or the run fails before promotion. When their dates
differ, CARDZ uses the newest observation and retains the transport provenance.
Population history remains direct-GemRate-only.

| Required data | Authority | Repository collector / transport | Ranking fallback | Failure behavior |
| --- | --- | --- | --- | --- |
| Candidate identity | GemRate exact identity evidence | preferred direct API identity receipt; alternate exact public-card receipt; Grade10 GemRate mirror is evidence only | structured/universal search and SNK are discovery only; G10 is bootstrap evidence only | keep unresolved; do not rank |
| PSA 10 population | GemRate | preferred direct API; alternate Grade10 `price.getGradingPopulations` GemRate mirror | none | exclude from ranking |
| PSA 10 population history | GemRate | direct API through `gemrate_source.py api-dump` | none | accumulating |
| PSA 10 reference price | SNK exact printing | `snk_market_data.py` | exact eBay PSA 10 sold data validates the reference; no ranking fallback | exclude when unavailable |
| Tracked sales | SNK recent trades + exact eBay PSA 10 sold records | SNK collector + repository-owned eBay sold adapter | G10 sale history is bootstrap evidence only | unavailable |
| 1d/7d/30d price change | CARDZ daily observations | `market_alerts.py` | none | accumulating |
| PSA 10 market cap | CARDZ derived | `market_alerts.py` | none | exclude when either dependency is absent |
| Display FX | Frankfurter daily USD base | `fx_rates.py` | last-good up to 72 hours | disable conversion |
| Market story | CARDZ editorial keyed by canonical printing | editorial import | G10 summary may seed research only | unavailable |

Candidate identity uses three GemRate transports in order: direct API identity
receipt, exact public-card receipt, then Grade10 GemRate mirror evidence.
Structured/universal search is discovery-only and can never bind a printing.
The public receipt's `card_number` is provider evidence and can be complete,
short or blank; only an already complete value can contribute to an exact
crosswalk. `gemrate_candidate_backfill.py` passes compatible evidence to
`source_crosswalk.py`, which confirms a printing only when language, edition,
finish and every retained candidate field are exact. It emits a review record
instead of guessing a short collector number, source language or base finish.
GemRate entity, universal, grader-member and spec IDs stay as private aliases
with receipt hash and source pointer; they enrich but never create or rebind a
CARDZ canonical printing.

## Non-negotiable population rule

`PSA 10 market cap = GemRate PSA 10 population × current PSA 10 reference price`.
G10 and SNK population values may be stored privately for comparison or source
diagnostics, but only a value recorded as GemRate authority can populate the
certified ranking dependency. The Grade10 mirror records GemRate provenance;
it is not a G10 population value.

## G10 boundary

G10 is not a canonical provider. It may supply bootstrap identities, old
summaries, mappings, sample eBay payloads, and last-good comparison evidence.
It cannot write the ranking population, current reference price, tracked-sales
aggregate, or derived change fields. This prevents upstream copied values from
silently overriding the GemRate, SNK, eBay, and CARDZ-owned observations.

## eBay boundary

The local `cardz-platform` Browse API provider queries active listings and
averages asking prices. That is not a sold-price feed and is therefore not
eligible for CARDZ reference price or tracked sales.

The G10 `price.getEBayDetails` route returns a small rolling sale-history
window, but its grade query is keyword-based and can mix raw cards into a
`PSA 10` result. It remains bootstrap evidence until a repository-owned adapter
validates exact printing identity, sold status, grade, currency, bundle
quantity, sale date, and transaction deduplication. Missing eBay coverage stays
unavailable; it never becomes `$0`.

## Top 350 tracked outputs

Canonical printings are stored once. The daily derivation writes three
memberships over the same observations:

```text
tcg Top 350 (rank 1-300 public, 301-350 private reserve)
pokemon Top 350 (rank 1-300 public, 301-350 private reserve)
one-piece Top 350 (rank 1-300 public, 301-350 private reserve)
```

The combined index is recalculated after Pokémon and One Piece updates, so a
new One Piece printing can change both the One Piece ranking and the combined
TCG ranking without duplicating the card or its history.

These are the only rank outputs. The reserve is excluded from the public
snapshot and remains available to operators for promotion alerts. Printing language is canonical identity
metadata, not a separate leaderboard, quota, or collection job. JLP is a
future integration seam and is not a collection, ranking, or publication gate.

GemRate population routes currently used by the repository:

```text
GET /cards/{gemrate_id}/population
GET /cards/{gemrate_id}/population/history?interval=week
```

The direct history endpoint uses GemRate's documented `week`／`two_week`
sampling interval. The current client requests `week` and stores only the
returned sampled history; it never expands those points into invented daily data.

## Operator commands

Inspect the complete route contract without starting Docker:

```powershell
python scripts/backend.py routes
```

Inspect one metric:

```powershell
python pipelines/data_routing.py --metric psa10_population
```

The same route validation runs automatically at the start of `backend.py
discovery` and `backend.py daily`. An invalid population authority or fallback
stops the job before database mutation.
