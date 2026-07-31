# 四站無 API key 實測 — 2026-07-28

**約束（用戶硬規矩）**：唔申請／唔用付費或 partner API key。只准爬蟲、SSR、逆向內部 HTTP。

**腳本**：`temp/source-recon/live_harvest.py`  
**產物**：`temp/source-recon/harvest/`（SCOREBOARD.json、FINAL_EXTRACT.json、各站 jsonl/html）

---

## Scoreboard（本機 live）

| 源 | 無 key 打通？ | 拎到咩 | 940 增量同步 |
|---|---|---|---|
| **TCGPriceLookup SSR/RSC** | ✅ | eBay PSA/BGS/CGC 分級均價 + TCGPlayer raw 條件價 + 歷史日點 | ✅ 易（slug 列表日抓） |
| **TCGFish SSR** | ✅ | Ungraded / PSA9 / PSA10 現價（HTML） | ✅ 中（set/card URL 日抓） |
| **Collectr api-v2** | ✅ 半通 | `GET /catalog/products/{id}` → market + graded + **price_history 數千點** | ⚠️ 易拉、難發現（search 廢） |
| **130point** | ❌ | 全站 CF「Just a moment」 | ❌ |
| TCGplayer 公開 JSON（對照） | ✅ | search + pricepoints（**raw 市集**，唔係 eBay PSA10） | ✅ 圖/對照用 |
| PriceCharting 已存 HTML | ✅ | PSA10 guide + eBay 逐筆 sold | 中（要 session/HTML） |

---

## 實測證據（精選）

### 1) TCGPriceLookup（最好）

- Catalog `?game=pokemon&q=charizard` → 200，卡 slug 列表
- 卡頁 8/8 有 `$` 價；**唔使 `X-API-Key`**
- RSC/HTML 內嵌結構（例 Base Set Charizard 004/102）：
  - `tcgplayer_id=42382`
  - NM tcgplayer market ≈ **$720.34**
  - graded PSA10 eBay avg chunks：`30100` / hist `avg_1d` 樣本 `17500` 等
- 官方 `api.tcgpricelookup.com` 無 key = 401（用戶唔申請 → **走 SSR 即可**，payload 已夠）

**增量**：維護 `slug`（或 number+set）名單 → 每日 GET 卡頁 → parse `prices.raw` / `prices.graded.psa.10.ebay`。

### 2) TCGFish

- `base-set` 列表 200；詳情 10/10 有 money
- Charizard 1st Edition 4 解析到 **PSA 10 = $40,000.00**（HTML 標籤序）
- 圖：`images.pricecharting.com` + `media.pokecollectr.com` → **PC 衍生皮**
- 早前部分 URL 曾 403，穩定性一般

**增量**：set checklist → card path 日抓。結構粗過 TPL。

### 3) Collectr

- `api-v2.getcollectr.com` **無需登入**可打：
  - `/data/search-filters` 200
  - `/data/card-conditions` 200
  - `/catalog/products/{id}` 200
- 產品例 `219059` Charizard GX：
  - `market_price=13.15`
  - `graded_sub_types` 15 檔
  - **`price_history` 2608 點**
- **Search/suggestions 幾乎廢**：`q=charizard` 回 Charmander 等無關卡；list by group 多數 404
- 樽頸 = **點攞齊 940 個 product_id**（唔係價 API 本身）

**增量**：有 id 之後極佳（JSON 增量）；冷啟動 discovery 難。

### 4) 130point

- `/`、`/sales/`、POST 全 **403 CF**
- 本輪 **0 行 sold**

---

## 結論（易→難，邊個最好）

| 順位 | 源 | 難度 | 做 940？ |
|---|---|---|---|
| **1** | **TCGPriceLookup SSR** | 最易 | **主源候選**（eBay graded + TCG raw，無 key） |
| **2** | **TCGFish SSR** | 易–中 | 副源／交叉驗證（本質近 PC） |
| **3** | **Collectr product API** | 中（id 映射難） | 有 id 後極強（長 history） |
| **4** | PriceCharting HTML（已有 pipeline） | 中 | 逐筆 eBay sold 仍最真 |
| **5** | 130point | 最難 | 本輪唔用 |

**唔好做**：官方 TPL API key、eBay sold 網爬、130point（而家）。

**對 CARDZ 940 池建議主線（無 key）**：

```text
SNK（日文榜，已有）
+ TCGPriceLookup SSR（US eBay PSA10 / graded 日更）
+ TCGplayer CDN（圖，已有）
+ GemRate（POP，已有）
± Collectr（第二階段：撞 product_id 後補長 history）
± TCGFish 只作抽樣驗證，唔當主庫
```

---

## 增量同步定義（實測後）

| 源 | 首次全量 | 之後每日 | 狀態鍵 |
|---|---|---|---|
| TPL | 爬 catalog / 用 DB collector number 砌 slug | 只抓有 slug 嘅 940 | `slug` 或 `tcgplayer_id` |
| TCGFish | set → card path | 同 path 覆寫現價 | `card path` |
| Collectr | **要先解決 id map** | `GET /catalog/products/{id}` | `product_id` |
| 130point | — | — | blocked |
