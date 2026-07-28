# 各源 — 點搵一張卡

> 身份表：`data/runtime/private-source-map/qualified-940-identity.*`  
> 全圖：[PROJECT_MAP.md](PROJECT_MAP.md)

| 數據 | 權威 | 永久 key |
|---|---|---|
| PSA10 POP ≥1000 入池 | GemRate | `gemrateId` |
| JP PSA10 榜價 + 成交 | SNK | `snkItemId` |
| US PSA10 價 + ~1y 日史 | TPL SSR | `tplSlug` |
| 卡圖 | TCGplayer CDN | `tcgplayerId` |
| eBay 逐筆 sold | PriceCharting 頁 | PC product id/url |

---

## GemRate

```text
GET https://api.gemrate.com/v1/cards/{gemrate_id}/population?parsed_description=true   # needs-key
POST https://www.gemrate.com/universal-search-query  body {"query":"..."}              # no-key
腳本: pipelines/gemrate_source.py
DB: market_grader_population_observation · market_gemrate_psa10_watchlist
```

## TPL（US 價 · no-key）

```powershell
python -X utf8 pipelines\qualified_pool_operator.py map-tpl --workers 10
python -X utf8 pipelines\qualified_pool_operator.py harvest-tpl --mode full --workers 8
python -X utf8 pipelines\qualified_pool_operator.py ingest-prices
```

```text
搵 slug: GET https://tcgpricelookup.com/catalog?game=pokemon&q=Name+Number → /card/{slug}
拎價:   GET https://tcgpricelookup.com/card/{slug}  Header: RSC: 1  (curl_cffi chrome)
只寫 prices.graded.psa.10.ebay > 0
官方 api.tcgpricelookup.com + X-API-Key → 唔用
```

## SNK

見 [SNKRDUNK_API_MANUAL.md](SNKRDUNK_API_MANUAL.md)

## TCGplayer 圖

見 [TCGPLAYER_API_MANUAL.md](TCGPLAYER_API_MANUAL.md)

```text
CDN: https://tcgplayer-cdn.tcgplayer.com/product/{id}_in_1000x1000.jpg
search: POST mp-search-api.../v1/search/request?q={collector}
```

## 圖寶藏庫（OP／死角）

| # | URL |
|---|---|
| D1 | https://drive.google.com/drive/folders/12Y9o5_LzXtAry6tw042j7Fgk4eyyrmfj |
| D2 | https://drive.google.com/drive/u/0/folders/1w18aBFle3uMOSiD78B5O1sxTn6WI1hVq |
| D3 | https://drive.google.com/drive/folders/13HwWKRkiwZTarPbH4W0A2WhYqYkDWyxr |
| Limitless | https://onepiece.limitlesstcg.com/cards |
| OP.gg | https://onepiece.gg/cards/ |

流程：CDN 死 → Limitless/OP.gg → Drive 落 `data/private/op-image-inbox/` → `store_face_art_image` + A/B/C。

## PriceCharting / eBay sold

見 [PRICECHARTING_API_MANUAL.md](PRICECHARTING_API_MANUAL.md) · [EBAY_SOURCE.md](EBAY_SOURCE.md)

## TCGFish / Collectr（價尾巴）

```text
TCGFish: GET /search-results?q= → 卡 path → HTML 找 "PSA 10" 下一格 $
Collectr: GET https://api-v2.getcollectr.com/catalog/products/{id}
  Header Origin: https://app.getcollectr.com
腳本: pipelines/us_price_fallback.py
```
