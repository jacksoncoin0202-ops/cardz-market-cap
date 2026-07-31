# Frontend → DB 數據需求對照（照單執藥）

> **目的**：前端 concept / 內頁寫一次 requirement，DB 同 pipeline 照單補數。  
> **產品目標**：訪客見到嘅位唔好空；冇數就唔顯示假 0，但主力榜面要有真實數據。  
> **量度範圍**：GemRate PSA10 POP≥1000 合資格池 **940** 張（`market_gemrate_psa10_watchlist`）。  
> **量度時間**：2026-07-29（本機），exact COUNT / 分組 depth。  
> **前端契約**：[`PAGE_DATA_REQUIREMENTS.md`](PAGE_DATA_REQUIREMENTS.md) · [`FRONTEND_HANDSHAKE.md`](FRONTEND_HANDSHAKE.md) · `packages/market-data/src/schema.ts`  
> **前端唔直讀 MySQL** —— 只讀 sanitized snapshot；本表係「DB 要備齊咩，producer 先砌到 snapshot」。

---

## 0. 全量同步狀態（先答：未齊，增量未開）

| 線 | 全量目標 | 現況 (940) | 狀態 |
|---|---|---:|---|
| 身份 / watchlist | 940 獨立 `variant_id` | **940** | ✅ 存量完成 |
| GemRate PSA POP 現值 | 940 | **932** PSA obs | 🟡 差 8 |
| GemRate POP 歷史 | 940 有週度史 | **372** | 🔴 差 ~568（檔案/入庫） |
| TPL slug 永久 map | 940 fail-closed | map 行 940 · 有效 slug **~682** | 🟡 差 ~258 未 bind |
| TPL 全量價 + 日史 | 有 slug 全 harvest 入庫 | harvest 檔 **662** · DB 價卡 **660** · 均深 **~55 日**（max 104） | 🟡 價覆蓋 70%；長史不足 |
| SNK exact ID | 能 exact 就永久 mark | identity **96** | 🔴 庫存 harvest 細 + 名對唔上 |
| SNK 價 / K 線 | 有 ID 就全 kline 入庫 | 價卡 **98** · 入庫 run155：**94 卡 / 24,471 點** · 均深 **~160 日** | 🟡 子集完成；未 bind 無長線 |
| eBay/PC 衍生價 | TPL 主；PC/eBay 輔 | ebay 價卡 **91** · ebay sale **91** | 🟡 窄覆蓋 |
| 圖（raw front + QC） | 940 QC-pass | asset **711** · pointer **715** · 兩邊都冇 **~222** | 🟡 差 ~225 |
| 成交 tracked sales | 有價卡有日聚合 | sale obs **95** · daily agg **92** | 🔴 榜表成交欄會空 |
| 四語名 + 小故事 | 上榜/內頁必有 | locale 任一語 **49** · 四語齊 **27** · story_ptr **49** | 🔴 最大「空位」來源 |
| **日更增量** | 全量穩後先開 | **未開** | ⏸ 等全量收口 |

**結論**：唔係「全部腳本已全量開晒」。已開過／已有產物嘅係 TPL full harvest（~662）、SNK 已 bind 子集 kline 檔、圖 fill、POP 現值。  
**未做完**：TPL 尾巴 map+harvest、SNK 擴 bind、無價 215、無圖 ~222、故事/i18n、成交擴面、POP 歷史。  
**增量 daily 按你規矩：全量收口先再開。**

---

## 1. 頁面 × 欄位 × DB 來源 × 缺口（主表）

### 1.1 Home Heatmap / 主板 tiles

| UI 欄位 | Snapshot 路徑 | DB / 組裝來源 | 排序／決策 | 940 覆蓋 | 空位影響 | 最佳補法 |
|---|---|---|---|---:|---|---|
| rank | `rank` | `market_index_*` 由 **市值** = price×POP 排序 | 先有 ready 價+POP 先入圍 | 有價 **722** | 無價唔上榜，榜淺 | 先補 TPL/SNK 價（見下） |
| 卡圖 | `image` | `market_image_asset` + QC + public webp | TCGplayer 主 → SNK/G10 fallback | **711–715** | placeholder 顯空 | `qualified_pool_operator fill-images` 重跑缺圖 222 |
| 名 | `names.*` | `catalog_variant` + `catalog_variant_locale` + editorial | en 必有；他語 null 唔抄 en | locale **49** | 非英 UI 變 unavailable | 編輯批次 + 翻譯隊列 |
| collector # | `collectorNumber` | catalog printing identity | complete 先過 gate | 隨 identity | 編號爛難信 | identity 已穩，少量修 |
| PSA10 現價 | `pricePsa10` | `market_price_observation` 有效源優先序 | **SNK > TPL > eBay/PC**（見 routing） | any **722** | 無價 = 無市值 = 唔上榜 | 全量：TPL 未 map 258 + SNK rebind |
| PSA10 POP | `populationPsa10` | `market_grader_population_observation` grader=PSA + GemRate hist | GemRate 權威 | **932** | 差 8 | watchlist 重掃 / exact map 尾 |
| 市值 | `marketCap` | price × POP | 兩邊 ready 先算 | ≤722 | 同價 | 同價 |
| 1d/7d/30d Δ價 | `windows.*.changePct` | 日價史借窗（1d→7d→30d） | 要 ≥ 窗長日點 | hist≥7 **677** · ≥30 **641** | 短史卡 Δ 空或借窗 | TPL 均深 55 日夠 30d；長尾靠 SNK |
| 窗成交 | `windows.*.trackedSales` | `market_daily_sales_aggregate`（PSA10 濾） | partial OK；唔好讀錯全 grade 混合表 | **~92** | 成交「—」 | SNK trades + eBay PSA10 sales 擴 bind 卡 |

### 1.2 Top 100 / Rankings 表

| UI 欄位 | 需求 | 940→上榜 現況 | 缺口 |
|---|---|---|---|
| 同上 + sparkline | `historyDaily` 尾 N 日 | 有 ≥7 日史 **677** | 無史卡線圖 empty |
| tracked sales 窗比 | 要兩個相鄰窗 | 成交卡少 | 成交覆蓋係結構瓶頸 |
| POP Δ（升箭） | PSA `topGradePopulationChangePct` | gemrate_hist 卡 **372** | 多數卡無 POP 週史 → 唔顯示升箭（可接受） |

### 1.3 Watchlist（預入圍 POP 971–999 等）

| UI 欄位 | 來源 | 狀態 |
|---|---|---|
| shadow rank / momentum | 同 metrics，scope 不同 | 前端有；**940 主池係 ≥1000**，pre-entry 另 roster |
| selection signals | producer 標記 | 非本輪 940 全量焦點 |

### 1.4 Card Detail 內頁（重點）

| UI 區塊 | 欄位 | DB 來源 | 決策 / 路徑 | 覆蓋 | 點解會空 | 補法 |
|---|---|---|---|---:|---|---|
| 大圖 | `image` | image asset + 200/600 variants | 必須 `raw_front` QC | ~711 | 222 無 asset | fill-images；TCGplayer product 圖 |
| 標題 / set | names, sets | locale + catalog | 四語獨立 | 49 / 27 四語 | 編輯未擴到 940 | editorial 批 + locale 表 |
| **小故事** | `stories` 四語 | `catalog_story_pointer` + locale body | 上榜 gate：四語各 ≥80 字、非模板 | story_ptr **49** · 四語 **27** | **最大內頁空洞** | 故事 producer 批（已有 long-form 32 檔可擴） |
| 市值 / 價 / POP | metrics | 同上 | — | 722 / 932 | 無價內頁 metrics 空 | 價全量 |
| 窗 Δ + 成交 | windows | 價史 + sales agg | — | 見上 | 成交薄 | sales 擴 |
| **Grader 供應格** | PSA/BGS/CGC/SGC/TAG | `market_grader_population_observation` | 現值；Δ 要歷史 | PSA 932 · CGC 896 · SGC 867 · BGS 792 · **TAG 103** | TAG 永遠無長史（源限制） | TAG 只顯示現值；Δ=`unavailable` |
| **K 線 / 日史圖** | `historyDaily[]` | 日 close 合併多源 + 當日 sales | **非 OHLC**；一日一 close | 任意源 ≥30 日 **641** · ≥90 日 **40** · ≥180 **25** | 多數只有 ~2 個月 TPL；長 K 線要 SNK | ① TPL full 入庫保 30d ② SNK kline 全量 ingest ③ 有 ID 先有長史 |
| 成交柱（圖上） | point.trackedSales* | daily sales agg by date | partial | 跟 sales 卡 | 多數日無柱 | eBay PSA10 + SNK 成交日匯 |

**K 線真實數字（重要）**

| 源 | 有價卡 | 均日深 | min–max 日 | ≥30d | ≥90d |
|---|---:|---:|---:|---:|---:|
| `tcgpricelookup` | 660 | **~55** | 1–104 | 616 | 11 |
| `snk_psa10` | 88 | **~160** | 1–866 | 26 | 26 |
| 任意源合併 | 722 | — | — | 641 | 40 |

→ **內頁「專業長 K」而家做唔到 940 全覆蓋**；最多係：TPL 卡 ~2 個月、SNK 子集更長。  
→ 全量策略：**TPL 保「有線」**；**SNK exact 永久 mark 後先拉長線**；唔好假造 OHLC。

### 1.5 TCG index（combined / pokemon / one-piece）

| 需求 | 來源 | 備註 |
|---|---|---|
| 完整 eligibility 排序後 cut top100/300 | 同一 generation | 唔另建庫 |
| 語系唔拆榜 | — | language ≠ leaderboard |

### 1.6 Grader page

| 欄位 | 來源 | 覆蓋 | 限制 |
|---|---|---|---|
| top-grade / total POP | grader obs | PSA 近齊；TAG 103 | TAG 無週史 → change 永遠 unavailable（源頭） |
| 市值欄 | 仍用 PSA10 價×PSA POP | 跟價覆蓋 | 非 PSA 唔另造市值 |

---

## 2. 永久 crosswalk（「mark 一次」）

| 源 | 表 / 檔 | 現 mark 數 | 查法（已定） |
|---|---|---:|---|
| GemRate | `catalog_source_identity` source=`gemrate` | **932** | POP watchlist 同源 |
| TCGPriceLookup | source=`tcgpricelookup` + `tpl-slug-map.jsonl` | **641** DB · **682** map | catalog-search + 嚴格 name/score gate |
| SNK | source=`snkrdunk` | **96** | card# + exact 名；fail-closed |
| eBay | source=`ebay` | **61** | PC/legacy；TPL 替主價 |
| TCGplayer 圖 | image pointer / product id | 隨圖 ~711 | CDN product image；唔靠付費 API |
| PriceCharting | 未批量 identity | **0** | 僅 HTML/export fallback；唔申請 API key |

產物目錄：`data/runtime/private-source-map/`  
- `qualified-940-identity.csv/jsonl`  
- `tpl-slug-map.jsonl` · `snk-psa10-940.jsonl` · `source-crosswalk.json`

---

## 3. 「做唔齊」清單 — 爭啲乜、點解、可唔可以硬上

| # | 缺口 | 差幾多（約） | 根因 | 可唔可以短期全綠 | 前端後果 |
|---|---|---|---|---|---|
| G1 | 無任何 PSA10 價 | **215** | 無 TPL slug 或 harvest 失敗 + 無 SNK | 可逼近，難 100% | 唔入排名 / 內頁空 metrics |
| G2 | TPL 未 map | **~258** | 嚴格 gate 防錯綁；名/set 對唔上 | 可再人工/規則放寬一檔 | 同 G1 |
| G3 | SNK 只 96 ID | **~844 無 SNK** | 本機 SNK harvest 庫存細 + 英日名對唔上，**唔係市場冇貨** | 要更大 SNK catalog 再 exact | 無 JP 權威價、無長 K |
| G4 | 長 K（≥90d） | 只有 **40** 卡 | TPL 窗 ~55 日；SNK 子集先有長史 | **做唔到 940 全長史**（源限制） | 內頁長圖只有短線 |
| G5 | 圖 | **~222** | 未 hit TCGplayer / 無 product | 多數可補 | placeholder |
| G6 | 四語故事 | **~891–913** 缺 | 編輯產能；上榜先寫 | 上榜子集先（Top 顯示），940 全寫成本高 | 內頁無 story panel |
| G7 | 成交 | **~845** 無 sale | 要有 marketplace ID + 成交 API/SSR | 隨 SNK/eBay bind 升 | 成交「—」、無柱 |
| G8 | POP 週史 | **~568** 無 gemrate_hist 行 | 檔未齊或未 join variant | 可再 import history_full | 無 POP 升箭 |
| G9 | TAG Δ | 結構性 | GemRate 無 TAG 週史 | **永遠唔得**（直到新源） | TAG 只顯示現值 |
| G10 | 全 940 專業「零空位」 | — | G1–G7 疊加 | **單次 session 做唔到** | 應用 presentation cut + 遮醜規則 |

### 能做到 vs 做唔到（對你問題直答）

| 目標 | 判斷 |
|---|---|
| 全量優先、完成先增量 | ✅ 規矩已 lock；增量未開 |
| 前端 requirement 表一次寫定，DB 照單 | ✅ 本文件 + PAGE_DATA |
| 上榜卡「看起來專業、少空位」 | 🟡 **可做**：先用 **有價∩有圖∩有 POP** 做 generation（而家 ~640–700 量級），故事 Top 子集四語 |
| 940 張張內頁都有長 K + 故事 + 成交 | ❌ **而家唔得**：SNK/故事/成交源覆蓋唔夠 |
| 爭啲乜先得 | 見 §4 優先隊列 |

---

## 4. 建議補數優先序（全量收口 → 先增量）

1. **價全量尾巴（G1/G2）**  
   - 重跑 `map-tpl` 對未 slug；`harvest-tpl --mode full`；`ingest-prices`  
   - 目標：any_price **722 → 850+**
2. **SNK kline 入庫完成（進行中）**  
   - `temp/ingest_snk_kline_940.py`（95 卡 / ~24k 點）  
   - 完成後 rebuild identity registry
3. **圖尾巴（G5）**  
   - `fill-images` 只打無 asset  
   - 目標：**711 → 900+**
4. **上榜故事四語（G6，presentation 子集）**  
   - 先 Top100/三榜成員，唔一次寫 940  
5. **成交（G7）** — 跟住已 bind 嘅 SNK/eBay 日更，唔阻擋 1–3  
6. **POP 史（G8）** — GemRate history import 補  
7. **全量 coverage gate 綠** → 先開 **incremental daily**（TPL incremental + SNK daily + POP scan）

**排序權威（上榜）**：`PSA10 市值 = 有效參考價 × GemRate PSA10 POP`，POP 門檻 ≥1000。  
**價源優先**：SNK PSA10（有 exact）> TPL eBay-derived > eBay/PC fallback。  
**圖優先**：TCGplayer CDN > SNK > G10。

---

## 5. Producer / 前端消費 checklist（防空位顯專業）

| 規則 | 說明 |
|---|---|
| 無價 → 唔進 published ranking | 好過「—」大片 |
| 無圖 → 可進 draft，正式 generation 優先有圖 | placeholder 限量 |
| 無故事 → 隱藏 story panel，唔放假英文 | 已有 UI `story &&` |
| 無成交 → sales 顯示 unavailable，唔顯示 $0 | format 層已處理 |
| 歷史 <2 點 → `noHistory`，唔畫假線 | HistoryChart 已處理 |
| 借窗 Δ 必須標 `fallbackWindow` | 可還原 fail-closed |
| TAG change 永不估 | 源限制寫死 |

---

## 6. 驗證命令

```bash
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap
export CARDZ_DB_HOST=127.0.0.1
python3 -X utf8 pipelines/qualified_pool_operator.py status
python3 -X utf8 temp/probe_frontend_coverage_940.py
```

數字同步寫入 [`PROJECT_STATE.md`](../PROJECT_STATE.md) §「940 全量覆蓋快照」。

---

## 7. 變更紀錄

| 日期 | 誰 | 內容 |
|---|---|---|
| 2026-07-29 | Grok | 首版：前端 concept/內頁 × DB 對照 + 940 實測缺口 + 全量/增量狀態；SNK kline run155 入庫後刷新 |
