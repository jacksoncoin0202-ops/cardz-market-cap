# 前端需求 Checklist（卡 = CARD · DB 填滿處方）

> **用途**：前端要邊啲卡、每張卡要邊啲欄、DB／pipeline 照單執藥。  
> **Agent 責任**：本檔 = 取數契約；改 snapshot／ingest 前先對呢份。  
> **量度基線**：2026-07-29 · 合資格池 940 · 有 PSA10 價可算市值 **916** · 無價 **24**。  
> **相關**：[`PAGE_DATA_REQUIREMENTS.md`](PAGE_DATA_REQUIREMENTS.md) · [`FRONTEND_HANDSHAKE.md`](FRONTEND_HANDSHAKE.md) · `apps/web` · `packages/market-data`

---

## 0. 一句定調

| 概念 | 定義 |
|---|---|
| **CARD** | 一個 printing = 一個 `variant_id` / opaque card id（唔係 CSS class） |
| **市值 marketCap** | `PSA10 參考價 × GemRate PSA10 POP`（USD） |
| **前端卡集** | 入 presentation cut 嘅卡；**只對呢批做「成套填滿」** |
| **池內其餘** | 940 維護池可半殘；**唔強制圖／故事／長 K** |

**Grading 系列上限：每家 grader 最多 show 頭 100 張。唔好為 grading 多備、多爬、多寫。**

---

## 1. 前端表面 × 最多幾多張卡

| # | 路由 | 產品名 | 卡點揀 | **Max 張** | 排序 |
|---|---|---|---|---:|---|
| A | `/` | TCG 總榜 + heatmap | combined 市值榜 cut → snapshot `top100` | **100** | marketCap ↓ |
| B | `/pokemon` | 寶可夢 | top100∪watchlist 入面 tcg=Pokémon，再 rank | **100** | marketCap ↓ |
| C | `/one-piece` | 海賊王 | 同上 tcg=One Piece | **100** | marketCap ↓ |
| D | `/watchlist` | 觀察名單 | snapshot `watchlist`（預設 rank 101–300 帶） | **≤200** | 跟 generation |
| E | `/card/[id]` | 卡內頁 | 必須已喺 top100∪watchlist | = 上板聯集 | — |
| F | `/graders/psa` | Grading · PSA | 上板聯集中 **有 PSA top-grade POP** 嘅卡 | **100** | 該 grader top POP ↓ |
| G | `/graders/bgs` | Grading · BGS | 有 BGS top POP | **100** | 同上 |
| H | `/graders/cgc` | Grading · CGC | 有 CGC top POP | **100** | 同上 |
| I | `/graders/sgc` | Grading · SGC | 有 SGC top POP | **100** | 同上 |
| J | `/graders/tag` | Grading · TAG | 有 TAG top POP | **100** | 同上 |

**實作依據**

- 市值榜／分 TCG：`scopeSnapshot` / producer presentation cut  
- Grading：`graderSnapshot()` — filter 有該 grader POP → sort topGradePopulation → **`.slice(0, 100)`**  
- Snapshot 形狀：`top100[]` + `watchlist[]`（`MarketViewSnapshot`）

**上板聯集（要成套填滿嘅 CARD 上限）**

```text
FE_CARD_SET = snapshot.top100 ∪ snapshot.watchlist
  ≈ combined 市值深度（常見 top100 + watchlist 至 ~300）
  ∩ 有 marketCap（有價 × 有 POP）

Grading 唔另開宇宙：只從 FE_CARD_SET 再切每家最多 100。
```

| 集合 | 規模（設計上限） | 成套填滿？ |
|---|---|---|
| **FE_CARD_SET** | 約 **≤300**（top100+watchlist） | ✅ **要** |
| 每家 Grading 顯示 | **≤100** | 用 FE 內已有 grader 欄；**唔額外擴卡** |
| 940 池其餘 | 其餘 | ❌ 半殘 OK |

---

## 2. 每張 FE CARD 要填滿嘅欄位（Checklist）

### 2.1 入圍硬條件（冇就唔上排名前端）

| # | 欄位 | Snapshot / UI | DB 來源 | 必要 |
|---|---|---|---|---|
| C1 | 身份 | `id`, collectorNumber, tcg | `catalog_variant` + opaque_id | ✅ |
| C2 | PSA10 價 | `pricePsa10` | `market_price_observation`（SNK > TPL > fallback） | ✅ |
| C3 | PSA10 POP | `populationPsa10` | GemRate → `market_grader_population_observation` / watchlist POP | ✅ |
| C4 | **市值** | `marketCap` = C2×C3 | producer 計算 | ✅ |
| C5 | 榜位 | `rank` | 當 generation 排序 cut | ✅ |

### 2.2 榜面／Heatmap／分 TCG（A–C）

| # | 欄位 | UI | DB / 組裝 | 必要 |
|---|---|---|---|---|
| L1 | 卡名（locale） | name | locale / editorial / catalog | ✅ en；他語可 null |
| L2 | set 名 | setName | 同上 | ✅ en |
| L3 | 圖 raw_front | image | `market_image_asset` + QC + webp | ✅ 上板 |
| L4 | 1d/7d/30d 價變 | windows.*.changePct | 日價史 | ✅ 有史先 ready |
| L5 | 市值變 | windows.*.marketCapChangePct | 價Δ × POPΔ | 有則填 |
| L6 | 窗成交 | windows.*.trackedSales | `market_sale_observation` → 日聚合（**PSA10 濾**） | partial OK |

### 2.3 卡內頁（E）

| # | 欄位 | UI | DB | 必要 |
|---|---|---|---|---|
| D1 | 小故事 4 語 | story | story pointer + locale | production top 嚴；可先隱藏空 |
| D2 | 日史 / K 線 | historyDaily[] | 日 close（非 OHLC） | ✅ ≥2 點先畫線 |
| D3 | 成交柱（圖上） | point trackedSales* | 日銷售聚合 | partial OK |
| D4 | 五 grader 供應格 | graderPopulations.* | 各 grader POP 現值 | PSA ✅；其他有就填 |
| D5 | grader POP Δ | topGradePopulationChangePct | grader 日/週史 | 有史先；**TAG 可永遠無 Δ** |

### 2.4 Grading 頁（F–J）— **每頁最多 100 卡**

| # | 欄位 | UI | 說明 | 必要 |
|---|---|---|---|---|
| G1 | 該 grader top grade 標籤 | topGrade | PSA 10 / BGS BL… | ✅ 入頁先決 |
| G2 | 該 grader top-grade POP | topGradePopulation | 排序鍵 | ✅ 入頁先決 |
| G3 | 該 grader total POP | total | 表欄 | 有則填 |
| G4 | POP 窗變動 | topGradePopulationChangePct | 1d/7d/30d | 有史先 |
| G5 | 市值（仍係 **PSA10 市值**） | marketCap | 唔另造非 PSA 市值 | ✅（跟 C4） |
| G6 | PSA10 價 | pricePsa10 | mobile 欄 | ✅ |
| G7 | 圖 + 卡號 + 名 | image, … | 同榜 | ✅ |
| G8 | sparkline | historyDaily 尾段 | 可銷售趨勢 | partial OK |
| G9 | 市佔圓環 | 全市場五 grader 總量 | 分母 = 未篩榜前全市場 | 全站一級 |

**Grading 唔需要**：為 BGS/CGC… 另算市值；為 grading 爬第 101+ 張；slab 圖。

### 2.5 全域

| # | 欄位 | 用途 |
|---|---|---|
| X1 | FX rates | 多幣顯示 |
| X2 | generation / effectiveAt | asOf、閘 |

---

## 3. 填滿優先序（DB-Link 執行序）

```text
1) 價（PSA10 only）→ 有 marketCap
2) 鎖定 FE_CARD_SET（市值排序 cut → top100 + watchlist）
3) 只對 FE_CARD_SET：
     a. 圖 raw_front（+ QC manifest）
     b. 日價史（夠 1d/7d/30d + 內頁線）
     c. PSA 以外 grader POP（BGS/CGC/SGC/TAG）— 夠每頁 head 100 即可
     d. 成交（partial OK）
     e. 故事四語（production 再嚴）
4) 940 其餘：身份 + GemRate POP 維護即可；唔強求圖/故事
5) Grading：唔擴卡；FE 內有 POP 就自然填滿每頁 ≤100
```

### 3.1 而家缺口（執行時重跑 status；下表係 2026-07-29 量度）

| 項 | 全池 940 | 含義 |
|---|---:|---|
| 有 PSA10 價 / 可市值 | **916** | 24 張暫時唔入排名 |
| 有圖 asset | ~713 | **只欠 FE 缺圖先補** |
| grader POP：PSA / CGC / SGC / BGS / TAG | 932 / 896 / 867 / 792 / **~103** | TAG 最瘦；Grading TAG 頁 ≤100 夠用就得 |

重算 FE 卡集同缺口：

```bash
export CARDZ_DB_HOST=127.0.0.1
python3 -X utf8 pipelines/qualified_pool_operator.py status
python3 -X utf8 temp/frontend_tier_vs_940.py   # 產 temp/frontend_tier_940_report.json
```

---

## 4. DB-Link 責任（邊個表 / 邊個腳本）

| 數據 | 寫入 | 讀出到前端 |
|---|---|---|
| 身份 / 卡 | `catalog_variant`, `catalog_source_identity` | snapshot `id` / names |
| PSA10 價 + 日史 | `market_price_observation` ← TPL / SNK / tcgfish… | pricePsa10, historyDaily, windows |
| POP | GemRate watchlist + `market_grader_population_observation` | populationPsa10, graderPopulations |
| 市值 / 榜 | producer 計算 + index snapshot | marketCap, rank |
| 成交 | `market_sale_observation`（PSA10 濾）→ 日聚合 | trackedSales* |
| 圖 | asset + `manifests/image-qc.json` + public webp | image |
| 故事 | story pointer + locale | story |
| 公開包 | `canonical_public_snapshot` → versioned JSON | **前端只讀呢層，唔直連 MySQL** |

**入口腳本（940 池）**

| 動作 | 命令 |
|---|---|
| 覆蓋現況 | `pipelines/qualified_pool_operator.py status` |
| 價 map/harvest/ingest | `… map-tpl` / `harvest-tpl` / `ingest-prices` |
| 補 PSA10 尾 | `temp/fill_no_price_psa10.py`（或後續收編 operator） |
| 圖（只缺圖 FE） | `… fill-images --write`（之後應加 FE-only filter） |
| 出街包 | `pipelines/canonical_public_snapshot.py` |

---

## 5. 明確唔做（防膨脹）

| 唔做 | 原因 |
|---|---|
| 為 Grading 備 500+ 卡 | 每頁最多 100 |
| 940 張張補圖／故事 | 未入 FE 唔使 |
| raw / 非 PSA10 價頂市值 | 市值口徑壞 |
| 非 PSA grader 另造 marketCap | 前端固定 PSA10 市值 |
| 假 OHLC | 契約係日 close |
| 前端直連 DB | handshake：只讀 snapshot |

---

## 6. 驗收（對 FE 卡，唔係對 940 口號）

- [ ] FE_CARD_SET 每張有 **C1–C5**（身份、價、POP、市值、rank）  
- [ ] FE 每張有 **L3 圖**（raw_front，唔係空 placeholder 海）  
- [ ] FE 每張 history 夠畫 1d/7d/30d 或明確 unavailable（唔假 0）  
- [ ] `/` `/pokemon` `/one-piece` 各 ≤100 有 rank 連續、市值降序  
- [ ] `/graders/{psa,bgs,cgc,sgc,tag}` 各 **≤100**；有 POP 先入；市值仍 PSA10  
- [ ] TAG 無 Δ 可 unavailable；有現值就夠上表  
- [ ] 池內無價 24 張唔出現喺排名前端  

---

## 7. 變更紀錄

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：前端表面 max 卡數、FE 成套欄位、Grading≤100、DB-Link 責任、唔膨脹 940 |
