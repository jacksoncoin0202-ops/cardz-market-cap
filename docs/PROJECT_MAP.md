# 項目地圖 + 逆向／取數「點做」手冊

> **點用**：腳本多、分支多——呢份係**地圖**。每源只寫 **點做**，少講方法論。  
> **唯一營運必讀**仍係 [`PROJECT_STATE.md`](../PROJECT_STATE.md)。呢份係 **地圖／逆向附錄**。  
> 更新：**2026-07-29**

---

## 0. 全圖一覽（分支）

```text
                    PROJECT_STATE.md（唯一營運必讀）
                              │
         ┌────────────────────┼────────────────────┐
         ▼                    ▼                    ▼
    採集 pipelines        出街 snapshot         前端 apps/web
         │                    │                    │
    ┌────┴────┬─────┬─────┐   │              固定 cut
    ▼         ▼     ▼     ▼   ▼
  GemRate   TPL   SNK   圖/成交
  (POP)    (US價) (JP價+成交) (TCG/Limitless/Drive)
```

| 分支 | 幹嘛 | 主腳本 | 「逆向／取數」手冊 |
|---|---|---|---|
| **A 入池 POP** | PSA10≥1000 | `gemrate_*` | [§1 GemRate](#1-gemrate-pop入池) |
| **B US 價** | eBay 衍生 PSA10 | `qualified_pool_operator` + `tcgpricelookup_ssr` | [§2 TPL](#2-tcgpricelookup-us-psa10-價) |
| **C JP 價+成交** | 榜價 + 流動性 | `snk_market_data` + `ingest_snk_trades_sales` | [§3 SNK](#3-snkrdunk-jp-價--成交) |
| **D US 成交** | eBay sold 證據 | `pricecharting_*` + `ebay_sold_data` | [§4 PriceCharting--eBay](#4-pricecharting--ebay-成交) |
| **E 圖** | raw_front A/B/C | `tcgplayer_images` + `op_limitless_images` + Drive | [§5 圖](#5-卡圖-tcgplayer--limitless-op--drive) |
| **F 價尾巴** | 無 TPL 時 | `us_price_fallback` | [§6 TCGFish--Collectr](#6-tcgfish--collectr-價尾巴) |
| **G 出街** | generation | `canonical_public_snapshot` | [§7 出街](#7-出街-snapshot)（唔使逆向） |
| **H 前端** | 展示 | `apps/web` | [§8 前端](#8-前端)（唔使逆向） |

**70+ 個其他 pipelines** = 歷史／G10／診斷 → 唔入日更主線，見 STATE §G Legacy。

---

## 1. GemRate（POP／入池）

| | |
|---|---|
| **角色** | 入池門檻、PSA10 POP 權威 |
| **腳本** | `pipelines/gemrate_source.py`、watchlist 日掃 |
| **深手冊** | `pipelines/GEMRATE_SOURCE.md`（若有） |

### 點做

```bash
# 有 key：per-card population
# GET https://api.gemrate.com/v1/cards/{gemrate_id}/population?parsed_description=true

# 無 key 搜尋：
# POST https://www.gemrate.com/universal-search-query
# body: {"query":"Charizard 4 Base"}
```

1. 用 `gemrateId`（40 hex）——喺 identity／watchlist。  
2. 入 `market_gemrate_psa10_watchlist` + POP observation。  
3. **唔靠 TPL 當 POP。**

---

## 2. TCGPriceLookup（US PSA10 價）

| | |
|---|---|
| **角色** | 無 key 嘅 **主 US PSA10 價 + ~1y 日史** |
| **腳本** | `pipelines/tcgpricelookup_ssr.py` · operator `map-tpl` / `harvest-tpl` / `ingest-prices` |
| **深手冊** | `US_PRICE_SOURCE_RULES.md` |

### 點做（唔使 API key）

```bash
export CARDZ_DB_HOST=127.0.0.1

# 1) 綁 slug（catalog search + 名+卡號 score，fail-closed）
python3 -X utf8 pipelines/qualified_pool_operator.py map-tpl --workers 10

# 2) 爬卡頁 RSC（一次即有窗內日史）
python3 -X utf8 pipelines/qualified_pool_operator.py harvest-tpl --mode full --workers 8

# 3) 寫 DB
python3 -X utf8 pipelines/qualified_pool_operator.py ingest-prices
python3 -X utf8 pipelines/build_identity_registry.py
```

**手動單卡：**

```text
1. GET https://tcgpricelookup.com/catalog?game=pokemon&q=Charizard+4
   → 從 HTML 抽 /card/{slug}
2. GET https://tcgpricelookup.com/card/{slug}
   Header: RSC: 1
   → 用 curl_cffi impersonate chrome
3. 解析內嵌 prices.graded.psa.10.ebay + history 日點
4. 只寫 price_usd > 0 嘅 PSA10；raw NM 唔寫入市值
```

**永久 key**：`tplSlug`  
**官方 api.tcgpricelookup.com + X-API-Key** → **我哋唔用**。

---

## 3. SNKRDUNK（JP 價 + 成交）

| | |
|---|---|
| **角色** | JP PSA10 榜價 + **流動性主源**（trades） |
| **腳本** | `snkrdunk_discover` · `snk_market_data` · `bind_snk_watchlist` · **`ingest_snk_trades_sales`** |
| **深手冊** | `SNKRDUNK_API_MANUAL.md`（長；下面係精簡點做） |

### 點做

```bash
# A) 發現 id（全圖鑑，plain requests）
python3 -X utf8 pipelines/snkrdunk_discover.py \
  --keywords ポケモンカードゲーム ワンピースカードゲーム \
  --max-pages 60 --out data/private/snkrdunk_brute/discovered_ids.txt

# B) 940 池 exact bind（多格式 number + set + 名，fail-closed）
# 例：OP01-016 / OP01 016 / OP01016 / 016 / SET+NUM 複合
python3 -X utf8 pipelines/bind_snk_watchlist.py bind --write

# C) 已 bind id → 拉 master+K線+trades
python3 -X utf8 pipelines/snk_market_data.py \
  --ids-file data/runtime/private-source-map/qualified-940-snk-ids.txt \
  --condition trading_card_single_psa10 \
  --out data/runtime/private-source-map/snk-psa10-liquidity-full.jsonl --delay 0.3

# D) ★ 成交入 DB（以前斷喺呢步）
python3 -X utf8 pipelines/ingest_snk_trades_sales.py \
  --harvest data/runtime/private-source-map/snk-psa10-liquidity-full.jsonl
```

**核心 API（免登入）：**

```text
GET https://snkrdunk.com/v1/apparels/{itemId}
GET … trading history（condition_code=trading_card_single_psa10）
→ chart.points = 日價；trades[] = 成交（label 枚數、price 円、soldAt、title PSA10）
```

**永久 key**：`snkItemId`  
**時間**：~100 id live **5–15 分**；ingest 本地 **&lt;1 分**。

---

## 4. PriceCharting → eBay 成交

| | |
|---|---|
| **角色** | eBay PSA10 **逐筆 sold**（直爬 eBay sold **唔通**） |
| **腳本** | `pricecharting_cf_session` · `pricecharting_ebay_export` · `ebay_sold_data` |
| **深手冊** | `PRICECHARTING_API_MANUAL.md` · `EBAY_SOURCE.md` |

### 點做

```text
1. 首次：headed browser 過 CF → 存 cookies
   python3 -X utf8 pipelines/pricecharting_cf_session.py
2. 有 product map（card → PC url/id）先：
   python3 -X utf8 pipelines/pricecharting_ebay_export.py --map <map.jsonl>
3. 產出 → ebay_sold_data normalizer → market_sale_observation
```

**產品頁 parse（重點）：**

```text
- PSA10 guide / chart：頁內 VGPC.chart_data
- 成交表：div.completed-auctions-manual-only 內 table
  列：date · eBay 連結 · $price
```

**唔好做**：`LH_Sold=1` 直爬 eBay（PerimeterX）。  
**付費** `/api/product?t=`：repo 通常無 token；批量用 CSV／HTML 路徑。

---

## 5. 卡圖（TCGplayer · Limitless OP · Drive）

| | |
|---|---|
| **角色** | raw_front 圖 A/B/C |
| **腳本** | `tcgplayer_images` · `fill-images` · `ensure_image_abc` · `op_limitless_images` |
| **深手冊** | `TCGPLAYER_API_MANUAL.md` · STATE §F 寶藏庫 |

### 點做 — TCGplayer（主）

```text
1. 有 tcgplayerId（多數從 TPL harvest 嚟）：
   GET https://tcgplayer-cdn.tcgplayer.com/product/{id}_in_1000x1000.jpg
2. 無 id：
   POST https://mp-search-api.tcgplayer.com/v1/search/request?q={collector}
   Origin/Referer: tcgplayer.com（curl_cffi chrome）
3. store_face_art_image → market_image_asset + 檔 + market_image_qc
```

```bash
python3 -X utf8 pipelines/qualified_pool_operator.py fill-images --write
python3 -X utf8 pipelines/ensure_image_abc.py --write --refetch-missing
```

### 點做 — Limitless OP 圖（已逆向 CDN）

```text
URL =
https://limitlesstcg.nyc3.cdn.digitaloceanspaces.com/one-piece/{SET}/{SET}-{NUM}_EN.webp
例 OP13-118 → .../one-piece/OP13/OP13-118_EN.webp
```

```bash
python3 -X utf8 pipelines/op_limitless_images.py --write --only-missing
# mark：catalog_source_identity source_code=op_limitless
```

### 點做 — Drive／OP.gg

1. 開 STATE §F 三個 Drive／limitless／op.gg  
2. 按 collector 下載 → `data/private/op-image-inbox/`  
3. 再 `store_face_art_image` + QC  

**A/B/C**：asset 行 + `data/public` 與 `apps/web/public` webp + `market_image_qc`（`meta_unreviewed` 因 varchar(24)）。

---

## 6. TCGFish / Collectr（價尾巴）

| | |
|---|---|
| **角色** | TPL 無 PSA10 時嘅 **現價** fallback |
| **腳本** | `us_price_fallback.py` · `temp/fill_no_price_psa10.py` |

### 點做 — TCGFish

```text
1. GET https://www.tcgfish.net/search-results?q=Name+Number
2. 抽卡 path（唔好抽 /_next/static）
3. GET path → HTML 找 "PSA 10" 下一格 $…
4. 只寫 PSA10；ungraded 唔入市值
```

### 點做 — Collectr

```text
GET https://api-v2.getcollectr.com/catalog/products/{id}
Header: Origin https://app.getcollectr.com
→ 有 id 先有用；search 幾乎廢
```

---

## 7. 出街 snapshot

```bash
# 由 DB 砌 generation（詳見 pipeline 參數）
python3 -X utf8 pipelines/canonical_public_snapshot.py …
# 前端：pointer → versioned JSON · 唔讀 MySQL
```

Top100：**市值排序 + 30d PSA10 成交 &gt; 0**（接好 sale 先閘）。

---

## 8. 前端

| 路由 | max 卡 |
|---|---:|
| `/` `/pokemon` `/one-piece` | 100 |
| `/graders/*` | 100／家 |
| `/watchlist` | ≤200 |
| `/card/[id]` | ⊆ 上板 |

詳：`PROJECT_STATE` §J · `FRONTEND_REQUIREMENT_CHECKLIST.md`（可選）。

---

## 9. 手冊索引（全部已改純點做）

| 檔 | 內容 |
|---|---|
| [SNKRDUNK_API_MANUAL.md](SNKRDUNK_API_MANUAL.md) | SNK 命令 + endpoint |
| [PRICECHARTING_API_MANUAL.md](PRICECHARTING_API_MANUAL.md) | PC CF + sold |
| [TCGPLAYER_API_MANUAL.md](TCGPLAYER_API_MANUAL.md) | 圖 CDN + search |
| [US_PRICE_SOURCE_RULES.md](US_PRICE_SOURCE_RULES.md) | TPL / Fish / Collectr |
| [EBAY_SOURCE.md](EBAY_SOURCE.md) | eBay = 經 PC |
| [SOURCE_LOOKUP_METHODS.md](SOURCE_LOOKUP_METHODS.md) | 每源 key + URL |
| [OP_IMAGE_C_PLAN.md](OP_IMAGE_C_PLAN.md) | OP 圖瀑布命令 |
| [PROVIDER_API_INDEX.md](PROVIDER_API_INDEX.md) | 表格式入口 |

日常入口 = **本檔 §0–§8** + STATE。

---

## 10. 新源要加時

```text
1. DevTools Network 抄 200 URL
2. curl_cffi chrome 或 requests 重放
3. pipelines/xxx.py → JSONL/DB
4. mark catalog_source_identity
5. STATE §E + 本檔 §0 加一行
```

**禁**：發明 endpoint · raw 當 PSA10 · eBay sold 直爬 · first-hit bind

---

## 11. 召回 → QC → 入庫（必讀經驗）

詳：[RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md)

```text
任何源：低門檻 RECALL → 腳本 VERIFY → 過先 mark DB + registry
```

- 門檻可以極低（海量撈）  
- **QC 主體 = 腳本**；AI agent = 編排 + 殘渣  
- 入口：`pipelines/full_volume_recall_verify.py --write --recall-min 20`

---

## 12. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版地圖 |
| 2026-07-29 | 全部 provider 手冊改純「點做」；刪方法論長文 |
| 2026-07-29 | 加 RECALL_VERIFY_OPS：極低 recall + 腳本 QC 責任分工 |
