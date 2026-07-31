# CARDZ backend source inventory

This inventory records what the current workstation can actually supply. It
does not turn an available script into a canonical authority.

| Source | Local implementation | Useful fields | Canonical use | Known limit |
| --- | --- | --- | --- | --- |
| GemRate | `pipelines/gemrate_source.py` | identity, complete card number, set, language, PSA/BGS/CGC/SGC population and history, grader volume | identity and population authority | API key must be injected privately; per-card TAG is not yet present in verified payloads |
| GemRate discovery | `pipelines/gemrate_candidate_discovery.py` | broad candidate IDs and search metadata | creates exact population worklists | search totals are discovery signals, never PSA 10 population |
| SNK | `pipelines/snk_market_data.py` | exact PSA 10 reference price, daily history, recent trades | price authority and one tracked-sales source | exact printing crosswalk is mandatory |
| eBay Browse API | legacy `cardz-platform/apps/api/app/providers/ebay.py` | active listing asking prices | none for ranking | not sold/completed transactions; current implementation searches names and averages asks |
| eBay item JSON-LD | `pipelines/ebay_brute_harvest.py item` | price, currency, PSA grade, **PSA cert number** (`hasCertification.certificationIdentification`), seller rating | validation / fallback evidence | per-item only; sold/completed search blocked by PerimeterX (see [EBAY_SOURCE.md](EBAY_SOURCE.md)) |
| G10 eBay details | `integrations/grade10/grade10_scraper.py` | small rolling `saleHistory` sample | bootstrap/test evidence | grade filter is keyword-based and has no stable pagination |
| Grade10/G10 | vendored integration and immutable old runs | summaries, mappings, historical samples, comparison values | research/bootstrap/last-good comparison only | copied upstream values are not canonical CARDZ observations |
| CARDZ | database derivation | 1d/7d/30d, market cap, rank, alerts | derived authority | requires fresh GemRate population and SNK price |
| TCGplayer | public mp-search / mpapi / infinite-api / CDN; PoC `docs/evidence/2026-07-27-image-fix/tcgplayer_lookup.py` | productId, set, collector number, clean face art URL, optional raw market/history | **image candidate source** only | multi-printing per number; QC required; not PSA10 authority. Manual: [TCGPLAYER_API_MANUAL.md](TCGPLAYER_API_MANUAL.md) |
| PriceCharting | official `/api/product`+CSV (token); product-page `VGPC` after CF (`pipelines/pricecharting_*`) | PSA10 current (`manual-only-price` cents), annual `sales-volume`, optional page history + eBay sale rows | secondary eBay-derived price / liquidity evidence | paid token not in repo; API has no historic sales; page depth needs headed CF session; bulk current = daily CSV. Manual: [PRICECHARTING_API_MANUAL.md](PRICECHARTING_API_MANUAL.md) |

**Agent entry for all provider API manuals:** [PROVIDER_API_INDEX.md](PROVIDER_API_INDEX.md)

## eBay routes（2026-07-24 更新）

而家有兩條 eBay route：

1. **Item JSON-LD（已驗證）** — `pipelines/ebay_brute_harvest.py item`：逐個 item ID 抽 Product JSON-LD，拎到價錢、PSA grade、**官方 PSA cert number**。PerimeterX 會自動過（item 頁）。詳見 [EBAY_SOURCE.md](EBAY_SOURCE.md)。
2. **Sold/completed search（未通）** — `/sch/i.html?LH_Complete=1&LH_Sold=1` 會彈 PerimeterX challenge，120 秒內唔自動過。要咪 headed 手動過一次，要咪行官方 Browse API（要 developer account）。

舊問題（official Browse API 係 active-listing 唔係 sold history）依然存在；`pipelines/ebay_sold_data.py` 係嚴格 normalizer，但要另外有 sold input 先用得。

每日 ranking 繼續用 fresh exact SNK price + GemRate PSA 10 population。eBay 缺席唔會 block ranking，但 tracked-sales coverage 會報 partial/unavailable。
