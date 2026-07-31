# DB 填庫捷徑／經驗（PM 累積 · 2026-07-29）

> 目標：**逆向網站 → 腳本 → 填 DB → snapshot → 前端有色有圖**。  
> 唔 invent 價；試盡真源。下次 agent **先讀呢份** 再動手。

---

## 1. 心智（PM）

| 層 | 意思 |
|---|---|
| FE cut 先綠 | top100∪watchlist 三窗 `changePct` 非 null、圖無 SAMPLE |
| 池 940 後綠 | any_price / sale_any / snk_id 繼續爬 |
| 「打爆 DB」 | 可測 KPI 連續升，唔係 magically 100% 全世界卡 |

**無止境執行** = 循環：`measure gap → harvest 候補 → **QC／verify** → mark 綠路徑／記 reject → 用 pattern 再 harvest → rebuild → 寫 lesson → 再 measure`。  
**QC 完先再揾**（用戶）：SNK 有 feedback 都要先過閘——可能 20 張零過或 1 張即過。Pattern：[`SOURCE_QC_PATTERNS.md`](SOURCE_QC_PATTERNS.md)。  
停條件：FE 綠 **或** 連續 N pass 零新行（源真乾）。

---

## 2. 已逆向源 → 填邊個表（捷徑）

| 源 | 逆向文件 | 腳本 | 入邊 |
|---|---|---|---|
| GemRate | PROJECT_MAP §1 | `gemrate_*` | POP / watchlist |
| TPL SSR | US_PRICE_SOURCE_RULES | operator map/harvest/ingest | `market_price_observation` 日史 |
| SNK | SNKRDUNK_API_MANUAL | `snk_market_data` · `ingest_snk_trades_sales` | 價 + 成交 |
| G10 K 線 | evidence/2026-07-27-kline-bridge | **`g10_kline_price_bridge.py --write`** | 價洞（prio 300） |
| G10 sales_cache | G10_BASELINE | `g10_sales_cache_ingest` | 成交史 |
| PC→eBay | PRICECHARTING_API_MANUAL | `pricecharting_*` | 成交（慢） |
| Limitless OP 圖 | CARD_SOURCING_HANDBOOK | **`op_limitless_images.py --write`** | 乾淨 OP 圖（禁 sample） |
| TCGplayer 圖 | TCGPLAYER_API_MANUAL | fill-images | 圖（要 QC 拒 SAMPLE） |

**硬禁**：Limitless `$` / raw TCG **唔入** PSA10 市值。

---

## 3. 今次踩過嘅坑（必記）

### 3.1 30d 全係 0% —— 唔係市場無升跌
- **根因**：TPL `ebayAvg1d` 被 stamp 成 **today**，同 last hist 同價 → 頭尾 0%。
- **修**：`qualified_pool_operator` 唔好將「等於 last hist」嘅 current 寫成今日；清假 today 行。
- **捷徑**：見到大批 0% 先查 **今日是否假觀測**，再查斷層。

### 3.2 有 7d 冇 30d —— 顯示問題 vs 真缺史
- 組裝用 **頭尾**（而家 ÷ ~N 日前）；±容差、翻前幾日即可。
- **唔好** overlay「假 0 救援」揀有意義 %——用戶要簡單頭尾。
- 真缺 bar → 補 harvest，唔 invent。

### 3.3 G10 K 線已在 ledger 但前端無史
- **根因**：`market_source_observation` 有 `g10_kline_daily`，snapshot 只讀 `market_price_observation`。
- **捷徑**：一有 sparse → **先跑** `g10_kline_price_bridge.py --write`（今次一次 +183 行）。

### 3.4 SAMPLE 圖
- TCGplayer SAMPLE **QC reject**；OP 用 **Limitless / G10 SNK**（用戶驗收）。
- 換圖：`content-addressed` **新 hash**，**唔覆寫**舊 sha；舊檔可留（共用）。
- 黃色像素 ≠ SAMPLE（梵高皮卡丘假陽）→ 要 OCR `SAMPLE` 或 contact sheet。

### 3.5 Sparse OP（Boa / Luffy OP11…）
- TPL PSA10 史常斷喺 6 月底；要 **SNK kline + G10 + eBay sold** 疊。
- identity 未 bind → kline bridge **quarantine**（對唔到 snkrdunk/ebay id）。
- **Comic / SEC-SP 並行印**：一般 OP 卡號 bind 可能錯 id；要 **comic exact SNK id**（例 OP11-118→519929、OP07-051→198723、OP14-119→728159）先有長 K。
- `g10_sales_cache_ingest` 只寫 **成交**，唔寫價；價要 `g10_kline_price_bridge` 或 SNK kline。
- `snk_market_data` 對舊 jsonl resume 會撞 **active universe lock** → 用**新 out 檔名**。

### 3.6 Sparse 耗盡結果（2026-07-29）
| 卡 | 結果 |
|---|---|
| Luffy OP11 / Boa / Mihawk | ✅ comic SNK + sales_cache 補洞 |
| ST10-006 / Greninja Star | ⚠️ 錨點窗真無 PSA10 bar——唔 invent |

### 3.7 SAMPLE 圖
- FE 19+1 張 OP → Limitless 換新 hash；P-110 Limitless 404 → G10 SNK CDN。
- re-scan hits **0**；DB `market_image_asset` 要另跑 `op_limitless_images --write` 先 durable。

---

## 4. 一 pass 標準流水（copy-paste）

```powershell
cd C:\Users\jackson0202\Documents\Playground\cardz-market-cap
# load backend.env → CARDZ_DB_*
$env:CARDZ_DB_HOST="127.0.0.1"
python -X utf8 pipelines\g10_kline_price_bridge.py --write --host 127.0.0.1 --port 3308 ...
python -X utf8 pipelines\bind_snk_watchlist.py bind --write
python -X utf8 pipelines\qualified_pool_operator.py harvest-tpl --mode full --workers 6
python -X utf8 pipelines\qualified_pool_operator.py ingest-prices
# SNK bound ids: snk_market_data → ingest_snk_trades_sales
python -X utf8 pipelines\op_limitless_images.py --write --delay 0.12
python -X utf8 pipelines\canonical_public_snapshot.py --view top300_boards ...
# copy → local-serve + seed + latest.json
python -X utf8 scripts\db_fill_until_green.py --pass-once
```

---

## 5. KPI 點叫「綠」

| KPI | FE 綠 | 池繼續 |
|---|---|---|
| changePct null 1d/7d/30d | 0 | — |
| sparse ±5d @30d | 越少越好 | 繼續 harvest |
| SAMPLE hits（OCR/sheet） | 0 | — |
| any_price / sale_any / snk_id | — | 日更升 |

---

## 6. 下次 agent 開工 30 秒

1. 讀 `PROJECT_STATE.md` + **本檔**  
2. `qualified_pool_operator status`  
3. 量 snapshot null/sparse  
4. **先 G10 kline bridge**，再 TPL/SNK  
5. OP 圖問題 → Limitless 先，TCGplayer SAMPLE 後  
6. 每 pass 寫一行 `temp/db_fill_loop_log.jsonl`  

---

## 7. 成功必須 mark 路徑（防「下次又死」）

| 成功做咗咩 | 永久 mark 邊 |
|---|---|
| SNK bind exact | `catalog_source_identity` (`snkrdunk`, external_entity_id) |
| SNK trades 入庫 | `liquidity-source-registry.jsonl` → `preferredLiquiditySource=snkrdunk` + `script=pipelines/ingest_snk_trades_sales.py` |
| TPL 價 | identity `tcgpricelookup` + registry 可記 `script=qualified_pool_operator harvest-tpl` |
| G10 kline 補洞 | 寫入 `market_price_observation` source=`g10_kline`（本身有 provenance） |
| OP 乾淨圖 | identity `op_limitless` 或 `g10_snkrdunk`；manifest image-qc source 欄 |
| semi_auto 過 QC | ledger `semi-auto-identity-ledger.jsonl` + identity 表 |

**洪水式 agent**：分 track 並行（no-price / SNK / 圖+identity），但 **mark 必須寫落 DB 或 registry**——唔好淨係改 snapshot。  
日後自動化 = 只讀 `preferredLiquiditySource` / identity 再跑對應 script。

---

*Last update: 2026-07-29 flood — mark path table · TPL/SNK/G10/Limitless*
