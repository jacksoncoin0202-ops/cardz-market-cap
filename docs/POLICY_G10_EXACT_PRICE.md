# Policy: G10 exact PSA10 price — DADDY approved

**asOf:** 2026-07-30（初版）→ **修訂 2026-07-30 晚**  
**Authority:** DADDY  
**Status:** **SUPERSEDED IN PART** — 以 [`POLICY_SOURCE_BINDING_DOCTRINE.md`](./POLICY_SOURCE_BINDING_DOCTRINE.md) 為準  

### 修訂摘要（必讀）
| 項 | 初版 | 終審 |
|----|------|------|
| G10 入面 **eBay / SNK / 成交量** | 可信 | ✅ **仍然可信、要用** |
| **`g10_kline` / G10 Index** | 曾當 exact | ❌ **永遠唔用**（自創 index，唔係正宗市價） |
| 有 index 元素 | — | ❌ 全部唔用 |
| 冇 index 元素 | — | ✅ 全部可以用 |

---

## Decision（修訂後）

| Item | Rule |
|------|------|
| G10 **真市** trust | eBay + SNK + volume 經 G10 工具落地 → **Trusted** |
| G10 **Index / kline** | **Banned** for mcap / FE 口價 |
| QC exact sources | `ebay`（exact identity）+ `snk_psa10` / `snk` / `snkrdunk` — **not** `g10_kline` |
| Still banned as exact | `tcgpricelookup`, Limitless $, invent, fuzzy, orphan ebay without identity |
| Sales | PC sold may land as `source_code=ebay` with `pc:{id}` + pricecharting exact |

## What “G10” means here (provenance)

| DB `source_code` | Pipeline | Physical source |
|------------------|----------|-----------------|
| `ebay` (price) | `pipelines/g10_ebay_ingest.py` | Local `grade10-scraper/data/cards/altxyz/{UUID}/ebay_PSA_10.json` → daily median |
| `g10_kline` (price) | `g10_analytics_ingest` → `g10_kline_price_bridge` | G10 analytics K-line ledger (`g10_kline_daily`) |
| `ebay` (sale) may also be | `c11_pc_sold_ingest.py` | PriceCharting HTML sold — **sales only**, id `pc:…` |

**Not** live eBay marketplace API scrape by CARDZ workers.

## Identity gate (fail-closed)

- `ebay` price requires `catalog_source_identity` **ebay** + `match_status=exact` (altxyz UUID).
- `g10_kline` requires exact identity on **ebay** and/or **snkrdunk** (bridge entity map).
- No invent; no re-labeling PC sale rows as SNK.

## Code

- `pipelines/canonical_db_qc.py` — `EXACT_PRICE_SOURCES` / `PRICE_IDENTITY_SOURCES`
- Freshness still applies (`PRICE_MAX_AGE` 48h unless separately relaxed).

## Follow-ups

1. Re-run QC after this policy.
2. If `g10_kline` rows are older than 48h → refresh G10 analytics / re-bridge (do not stamp fake “now”).
3. Prefer SNK when both SNK and G10 present (existing priority / source order).

## Explicit non-goals

- Does **not** auto-deploy or move public pointer.
- Does **not** make raw TCG / Limitless prices exact.
- Does **not** approve Huca as price authority.

## Related (same day)

- DB provenance footnotes: `POLICY_DB_PROVENANCE.md`
- FE Top100 liquidity: `POLICY_FE_TOP100_LIQUIDITY.md`
