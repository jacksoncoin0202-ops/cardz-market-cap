# Agent 管線索引（可選）

> **必讀**：[PROJECT_STATE.md](../PROJECT_STATE.md) · **點做地圖**：[PROJECT_MAP.md](PROJECT_MAP.md)  
> 本檔 = 腳本表。**更新**：2026-07-29

---

## 0. 你想做 → 打咩

| 做 | 命令 |
|---|---|
| 睇覆蓋 | `python -X utf8 pipelines/qualified_pool_operator.py status` |
| 價全量 | operator `map-tpl` → `harvest-tpl` → `ingest-prices` |
| 圖 | operator `fill-images` · `ensure_image_abc` · `op_limitless_images` |
| SNK 成交 | `snk_market_data` → **`ingest_snk_trades_sales`** |
| 身份表 | `build_identity_registry.py` · `qualified-940-identity.jsonl` |
| 出街 | `canonical_public_snapshot.py` → pointer → web |
| 點 call 源 | [PROJECT_MAP.md](PROJECT_MAP.md) · [SOURCE_LOOKUP_METHODS.md](SOURCE_LOOKUP_METHODS.md) |

---

## 1. 現役入口（CURRENT）— 只記呢啲

| 腳本 | 角色 | 狀態 |
|---|---|---|
| **`pipelines/qualified_pool_operator.py`** | **唯一** 合格池運維：status / map-tpl / harvest-tpl / ingest-prices / fill-images / maintain / gap-report | ✅ 主入口 |
| **`pipelines/build_identity_registry.py`** | 重生身份對照表（CSV/JSONL + 每源 lookup 提示） | ✅ 改 map 後要 rebuild |
| **`pipelines/tcgpricelookup_ssr.py`** | TPL SSR 底層（operator 會 call） | ✅ |
| **`pipelines/tcgplayer_images.py`** | TCGplayer 圖 | ✅ |
| **`pipelines/native_image_resolver.py`** | 圖入庫 + canvas | ✅ |
| **`pipelines/snk_market_data.py`** · **`bind_snk_watchlist.py`** | SNK id / 價 | ✅ 子集 |
| **`pipelines/us_price_fallback.py`** | TPL→TCGFish→Collectr→PC 鏈 | ✅ 單卡／尾數；**tcgfish 已用於補價** |
| **`pipelines/gemrate_*` / watchlist 日掃** | POP 權威 + 池擴大 | ✅ |
| **`pipelines/canonical_public_snapshot.py`** | DB → 公開 snapshot | ✅ 出街必經 |
| **`scripts/backend.py`** | 路由 / work-items / 部分 daily | ✅ L3 用 |
| `temp/fill_no_price_psa10.py` | 無價尾：重 map + TCGFish PSA10 入庫 | ⚠️ **有效但暫放 temp**；穩應收編 operator |
| `temp/frontend_tier_vs_940.py` | FE 卡集 vs 半殘分層報告 | ⚠️ temp；S1 診斷用 |

```bash
export CARDZ_DB_HOST=127.0.0.1
cd /mnt/c/Users/jackson0202/Documents/Playground/cardz-market-cap

python3 -X utf8 pipelines/qualified_pool_operator.py status
python3 -X utf8 pipelines/build_identity_registry.py
python3 -X utf8 pipelines/qualified_pool_operator.py maintain --harvest-mode incremental
```

---

## 2. 每源「搜法 / ID」— 永久 mark 一次

詳見 [`SOURCE_LOOKUP_METHODS.md`](SOURCE_LOOKUP_METHODS.md)。對照表已內嵌 `lookup` 物件。

| 源 | **永久 key** | 點搵（首次） | 之後只靠 key | DB 落點 |
|---|---|---|---|---|
| **CARDZ** | `variantId`（私）· `opaqueId`（公） | catalog + watchlist | 前端 id = opaqueId | `catalog_variant` |
| **GemRate** | `gemrateId`（40 hex） | API population / universal-search | POP 日更 | watchlist + `market_grader_population_observation` |
| **TPL** | `tplSlug` | catalog?game=&q= + **fail-closed score** | harvest card 頁 RSC | `market_price_observation` `tcgpricelookup` |
| **TCGplayer** | `productId` | TPL 頁帶出 **或** mp-search | CDN 圖 | image asset / pointer |
| **SNK** | `snkItemId` | harvest + card# exact bind | kline/API | `snk_psa10` / identity |
| **TCGFish** | path（例 `/...`） | site search HTML | 現價 PSA10 fallback | 可寫 `tcgfish` 價（fill 腳本） |
| **Collectr** | `product_id` | 難 discovery；有 id 先 | 長 history API | fallback |
| **PriceCharting** | product URL/id | CF session HTML | sold 行 | ebay sold 輔 |

**原則**：有 key 之後 **禁止**再靠英文名 first-hit 估綁（尤其 TPL/SNK）。

身份檔：

- `data/runtime/private-source-map/qualified-940-identity.jsonl`（腳本）
- `.../qualified-940-identity.csv`（人眼 / Excel）
- `.../tpl-slug-map.jsonl` · `snk-psa10-940.jsonl` · worklist

改咗 map / 入庫 → **一定** `build_identity_registry.py`。

---

## 3. DB：要唔要「整合 column」？

### 3.1 而家設計（正確，唔好亂 merge 成一張大表）

| 層 | 表（代表） | 作用 |
|---|---|---|
| 身份 | `catalog_variant` · `catalog_source_identity` | 一 printing 多源 external id |
| 價 | `market_price_observation` | 多源日點；`source_code` + `source_priority` |
| POP | `market_grader_population_observation` · gemrate history | 分 grader |
| 成交 | `market_sale_observation` | **PSA10 要濾 grade** |
| 圖 | `market_image_asset` · pointer · **另** `manifests/image-qc.json` | QC 主在 manifest；**搵圖寶藏** `PROJECT_STATE` §2.1 · `SOURCE_LOOKUP_METHODS` §3.1 |
| 出街 | index snapshot / public JSON | 前端只讀呢層 |

**唔需要**而家把所有源欄位 flatten 入 `catalog_variant` 一行——會強迫 schema 每次加源就 migration，同 append-only 觀測衝突。

### 3.2 真正欠嘅「整合」係邊種

| 要 | 唔要 |
|---|---|
| **身份 registry 檔** 齊 key + link + lookup（已有，要常 rebuild） | 取消 `catalog_source_identity` |
| **source_code 命名穩定**（`tcgpricelookup` / `snk_psa10` / `tcgfish`） | 同一源多個別名唔寫文檔 |
| **FE read model**（中後期）：物化榜／卡寬表 | 前端直 join 十張 observation 表 |
| **image QC**：DB `market_image_qc` ↔ manifest 對齊（S2） | 假装 asset 行 = 可出街 |

### 3.3 Agent 讀價／市值時

```text
市值 = 最新 PSA10 price_usd（priority 低者優先）× GemRate psa10 POP
價源優先（概念）：snk_psa10 < tcgpricelookup < tcgfish/ebay 輔
（實際 ORDER BY observed_date DESC, source_priority ASC — 以 producer 為準）
```

成交：**唔好**用 `market_daily_sales_aggregate` 嘅混合 grade ebay 行當 PSA10。

---

## 4. 腳本年齡／可信度（精簡）

| 類 | 例子 | Agent 點做 |
|---|---|---|
| **CURRENT 主線** | §1 表 | 預設用呢啲 |
| **LEGACY 但仍可能有用** | `g10_*` ingest、舊 ebay harvest | 只在 identity 已有、補歷史時用；**唔當 940 日更主線** |
| **DISPOSABLE** | `temp/*` 大多數 | 一次過診斷；**唔寫入 ROADMAP 當入口** |
| **文件腐爛風險** | `BETA_PLAN_20260726`、舊 HANDOFF | 現況以 **PROJECT_STATE + 本檔** 為準 |

**~74 個 pipelines 檔** = 歷史 + 現役混雜。新工作：**先 operator**，唔好「再寫第 75 個」。

---

## 5. 建議執整（按優先，唔一次大重構）

| # | 動作 | 點解 | 急？ |
|---|---|---|---|
| 1 | **每次 map/ingest 後 rebuild identity** | 而家已跑；避免 CSV 停喺舊 682 map | ✅ 流程化 |
| 2 | **把 `fill_no_price_psa10` 收編入 operator**（`fill-prices-tail`） | 唔留關鍵路徑喺 temp | S1 |
| 3 | **fill-images 可選 FE-only filter** | 唔強行補全池 200+ | S1 |
| 4 | **SOURCE_LOOKUP 加 tcgfish** | 已用於 68 卡補價 | ✅ 本輪文檔 |
| 5 | **pipelines/README 或本檔標 LEGACY 列表** | 減少 agent 誤用 g10 當主線 | S1 |
| 6 | **DB column 大整合** | **而家唔做**；S3 read model 先 | 長期 |
| 7 | **market_image_qc 同 manifest 同步** | 出街閘 | S2 |

---

## 6. 跟進 checklist（任何 session 開工）

1. [ ] 讀 `PROJECT_STATE.md` §0  
2. [ ] `qualified_pool_operator.py status`  
3. [ ] 要改卡身份／價 → 開 identity csv/jsonl 對 key  
4. [ ] 任務分 L0–L3（`AGENTS.md`）  
5. [ ] 收工：status 數字寫返 STATE；動過 map → `build_identity_registry.py`  
6. [ ] **唔**前端直連 DB；**唔**為 Grading 備 >100 卡  

---

## 7. 文檔地圖

| 檔 | 用途 |
|---|---|
| **`PROJECT_STATE.md`** | **唯一必讀** |
| 本檔 | 可選附錄 |

---

## 8. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版；後改為可選附錄，精華併入 PROJECT_STATE |
