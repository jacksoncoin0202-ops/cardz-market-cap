# CARDZ 生產最優數據組裝（**只定一路 · 無 fallback 表**）

**日期：** 2026-07-30  
**原則：** 每個需求只揀 **一條最優路徑**。唔寫「如果 A 唔得就 B」。唔得 = 修呢條路 / 開 contract gap，唔散彈。

---

## 0. 全局定律

| # | 定律 |
|---|------|
| 1 | **免費用 / 本地**：唔買 SaaS anti-bot |
| 2 | **No invent**：無成交唔印價 |
| 3 | **Identity ≥2 源** 先當 printing 準 |
| 4 | **Exact PSA10 price 源**：`snk_psa10` / `snk` / `snkrdunk` + **G10**（`g10_kline` / G10-path `ebay` median；DADDY 2026-07-30）；TPL purge。見 `POLICY_G10_EXACT_PRICE.md` |
| 4b | **DB 注腳**：每類事實可追溯 script/channel（`POLICY_DB_PROVENANCE.md` + `DB_PROVENANCE_MAP`） |
| 4c | **FE Top100**：無 30d PSA10 成交 → 剔除（`POLICY_FE_TOP100_LIQUIDITY.md`） |
| 5 | **多工** 跟 `HARD_RULES_MULTIWORKER.md` |

---

## 1. 每張合格卡要咩（唯一清單）

| 需求 | 定義 |
|------|------|
| **I** Identity | 名 / set / number / 語言·printing · ≥2 源一致 |
| **P** Exact PSA10 price | 契約可信源最新 PSA10 成交/市價 |
| **S** PSA10 sales 30d | 30 日內 PSA10 成交筆數 |
| **O** POP | PSA10 population |
| **G** Image | 已確認可公開圖 |

Optional 強化（有就更好，唔當 release gate 替代）：JP raw 市價、零售 ask、庫存。

---

## 2. 按 TCG · 最優源（每格只一個）

### 2.1 日本 Pokémon（主戰場）

| 需求 | **最優源** | **最優腳本 / 產物** | 點解係最優 |
|------|------------|---------------------|------------|
| **I** | GemRate exact bind + SNK exact bind | `gemrate_*` + `catalog_source_identity` SNK | 雙源、已生產 |
| **P** | SNK PSA10 market | `pipelines/snk_market_data.py` → `market_price_observation` | 契約 exact 源 |
| **S** | SNK recent trades | `ingest_snk_trades_sales.py` / SNK trades path | 真成交 |
| **O** | GemRate PSA10 pop | `gemrate_source` / history | 權威 pop |
| **G** | SNK product image（已確認閘） | `snk_image_*` | 已有 pipeline |
| **JP 市場證據 / K 線 / 店庫存** | **pokeca-chart API** | `reverse-skill/work/pokeca-chart/pokeca_scraper.py` | **已逆向、免 browser、691 卡 PSA10+raw+pop+shop；今日 live 驗證 691 items** |

**pokeca-chart 角色（寫死）：**  
- **最優 JP 全景價源**（raw + PSA10 日線 + pop + CardRush 庫存在 `nRushStock` / shop_stock）。  
- **唔替代** SNK 做 exact 契約價，直到 DATA_CONTRACT 明確升格；但 **wire 入 CARDZ 係第一優先 missed 源**。  
- Transport：**純 HTTP + AES decrypt**，唔經 Camofox、唔經 CDP。

### 2.2 英文 Pokémon

| 需求 | **最優源** | **最優腳本** | 點解 |
|------|------------|--------------|------|
| **I** | GemRate + PriceCharting product | PC CDP session + ledger attach | EN 印刷 PC 最強公開頁 |
| **P** | SNK 若有 exact EN/對應 apparel | `snk_market_data.py` | 仍係 exact 契約 |
| **S** | PriceCharting PSA10 completed sold → eBay rows | `pricecharting_cf_session` + `c11_pc_sold_ingest` | EN sold 最優公開抽取 |
| **O** | GemRate | gemrate | 同上 |
| **G** | 已確認 SNK/官方圖 pipeline | existing | — |

**PC transport 最優：** **真 Chrome CDP :9222** + `HARD_RULES` supervisor（**1 worker**）。  
CF 人手 click 係呢條路嘅操作步驟，**唔係另一條 fallback 源**。

### 2.3 One Piece

| 需求 | **最優源** | **最優腳本** |
|------|------------|--------------|
| I / P / S / O / G | SNK + GemRate | 現有 SNK/GemRate 生產路徑 |

pokeca-chart **唔做** OP 最優（站係ポケカ）。

---

## 3. 新源裁決（今日逆向）

### 3.1 pokeca-chart.com — **採用 · 最優 JP 補充**

| | |
|--|--|
| 狀態 | 已逆向 2026-07-22；**2026-07-30 live 691 items** |
| 最優用途 | JP 卡：日線、PSA10 價證據、pop、**店庫存（含 CardRush）** |
| 下一步 | **wire 入 CARDZ**（ingest → observation / evidence），唔再忽略 |

### 3.2 huca.tw — **不採用為主源**

| | |
|--|--|
| 本質 | Snkrdunk **代理層** + TW UI |
| 最優？ | **否**。CARDZ 已有 SNK 直連 = 更優 |
| 結論 | **唔接入生產**。避免雙重依賴同一 SNK 數據 |

### 3.3 cardrush.jp / cardrush-pokemon.jp — **不獨立接入**

| | |
|--|--|
| 本質 | 零售 ask + 網上庫存；**無 sold comps** |
| 最優拿法 | **經 pokeca `get_shop_stock_data` / `nRushStock`**（已有 API） |
| 獨立 HTML scrape | 差過 pokeca 聚合（CF、HTML 重） |
| 結論 | **唔開獨立 CardRush scraper**；庫存跟 pokeca 最優路 |

---

## 4. 執行地圖（DB 可見 · 一需求一腳本）

| `need`（可寫 control-plane / worklist） | 觸發條件 | **唯一腳本** |
|----------------------------------------|----------|--------------|
| `snk_price` | exact_psa10_price_missing ∧ SNK exact | `pipelines/snk_market_data.py` |
| `snk_sales` | psa10_sales_30d_missing ∧ SNK exact | SNK trades ingest |
| `gemrate_pop` | pop 缺 / 過舊 | `pipelines/gemrate_source.py`（及 history） |
| `pc_en_sold` | EN ∧ sales/identity 缺 PC | CDP session + `c11_pc_sold_ingest.py` **經 supervisor** |
| `pokeca_jp_daily` | JP ポケカ universe | `pokeca_scraper.py` → **待 wire CARDZ ingest** |
| `image_confirm` | image gate | 現有 image QC / SNK image path |

**禁止：** 同一 need 排兩個源「試完再試」。

---

## 5. 本機 runtime 最優組裝

| 組件 | 最優用法 |
|------|----------|
| **Chrome CDP 9222** | **唯一** PC HTML/sold 通道；`ensure_chrome_cdp.ps1` + `pc_full900_supervisor.ps1` |
| **pokeca HTTP API** | **唯一** JP 全景日線/店庫存通道（wire 後） |
| **SNK HTTP** | **唯一** exact PSA10 價 + 成交 |
| **GemRate** | **唯一** pop 權威 |
| Camofox | **唔進入最優價源圖**（PC 輸 CDP；SNK/Pokeca 唔需要） |
| Firecrawl | **唔進入價源圖**（文章 MD 用，唔做市價） |
| 多 shard 搶 CDP | **禁止**（R2） |

---

## 6. 最優入面再最優（下一深研順序）

1. **Wire pokeca → CARDZ DB**（最大 missed 收益）  
2. **PC supervisor 長駐** + full900 收尾  
3. **Exact 契約**是否升格 pokeca PSA10（要 DADDY 書面；升格前只做 evidence）  
4. Identity 2-source scorer 對齊  

---

## 7. 一句

**JP：SNK 價成交 + GemRate pop + pokeca 全景/庫存。**  
**EN：GemRate + PC（CDP）sold + SNK 若有 exact。**  
**Huca / 獨立 CardRush scrape / Camofox 價源 / 多 CDP worker：唔入最優圖。**
