# 入庫前驗證工序（硬閘）

> **搵料易、入庫難。** Harvest 可以亂撈；**寫 MySQL／public 之前**一定過呢頁。  
> 詳方法論：[RECALL_VERIFY_OPS.md](RECALL_VERIFY_OPS.md) · 入口：[AGENTS.md](../AGENTS.md)

---

## 0. 一句

| 階段 | 要求 |
|---|---|
| 搵料 | **免 QC**（jsonl／temp／harvest 目錄） |
| **入庫前** | **必驗**——有腳本用腳本；腳本拒／殘渣先 AI 半自動；品味／法律先人 |
| 入庫後 | mark identity + registry；之後增量跟已記 ID |

**禁止：** 低門檻直接 `INSERT` · AI 口頭「應該係」就 commit · bypass `sample_image_qc` / `verify_pair`

---

## 1. 按數據類型：要過咩

### 1.1 Identity（張卡係咪啱）

| 項 | 要求 |
|---|---|
| **腳本** | `semi_auto_identity.py` · `full_volume_recall_verify.py` · clean 掃 |
| **硬條件（`verify_pair`）** | collector 必須中 · species 全字（mew≠mewtwo）· OP 名 hit · 純數字要 set hint · VMAX/VSTAR/GX stage · 拒 sleeve/playmat |
| **過先寫** | `catalog_source_identity` + ledger |
| **唔過** | 唔寫；可留 reject 報告；**錯綁要 clean 刪** |
| **AI 角色** | 編排多源 recall；**needsReview 殘渣** 半自動（高市值優先）——通過仍寫同一表，唔另開「AI 認證」通道 |
| **人** | 真歧義 printing／法律 |

**最低要求唔算為難：** 名+號對得上、唔 first-hit 亂綁。冇過 = 當冇 identity。

### 1.2 價（PSA10 觀測）

| 項 | 要求 |
|---|---|
| **腳本** | `qualified_pool_operator` map-tpl／harvest／ingest · `snk_market_data` · `g10_kline_price_bridge` |
| **硬條件** | 有 **variant_id 綁定**（TPL slug 過 name/collector 閘；SNK 有 verified identity）· **有 PSA10 數** 先寫 · **唔 invent** · 假 today stamp 當 bug |
| **過先寫** | `market_price_observation`（source_code + observed_date + price_usd） |
| **score 窗** | 用戶：**近 30 日有 bar 夠用**；更長史 welcome |
| **唔過** | harvest 可留檔；**唔寫價列**（例 TPL 有 slug 但 psa10 空） |

### 1.3 成交（流動性）

| 項 | 要求 |
|---|---|
| **腳本** | `ingest_snk_trades_sales` · `g10_sales_cache_ingest` · eBay/PC ingest |
| **硬條件** | 已 **verified identity** 先跟 id 拉成交 · 指紋去重 · **PSA10 濾**（唔混 grade）· 有就入晒（唔截 30d 採集） |
| **過先寫** | `market_sale_observation` |
| **30d** | 反推窗用嚟排 Top100／流動；**唔係**「只採 30 日」 |

### 1.4 圖（public 出街）

| 項 | 要求 |
|---|---|
| **腳本** | `sample_image_qc.py`（store 入口）· `ensure_image_abc` · `op_limitless_images` · FE：`verify_images.py` + `scan_sample_*` |
| **硬條件** | **SAMPLE / NOW DESIGNING 拒** · content-addressed 新 hash · 優先 Limitless OP `_EN` / G10 SNK · 有 TPL/TCG binding 先 TCGplayer |
| **範圍** | **所有 TCG 入庫前都要 QC**。SAMPLE **水印**實務上主要出喺 **OP／海賊王**（唔等於 PTCG 免 QC——身份／錯卡／缺圖照驗） |
| **過先寫** | `market_image_asset` + pointer + `market_image_qc`（`public_allowed`） |
| **腳本未覆蓋** | 語意「卡面=卡」多數仍 `meta_unreviewed` → **殘渣／上 FE 前** AI 或人抽樣睇；**唔過就唔 `public_allowed`** |
| **OP FE re-scan** | SAMPLE hits 必須 0 先報 OP 圖完成 |

---

## 2. 工序 checklist（每批入庫 copy）

```text
[ ] 1. harvest 已落 temp／private-source-map（可未 QC）
[ ] 2. identity：semi_auto / full_volume --write ？ accept/reject 數有記？
[ ] 3. 價：只 ingest 有 PSA10 + 已 map／已 bind 嘅卡？
[ ] 4. 成交：只跟 verified external id ingest？
[ ] 5. 圖：store 經 sample_image_qc？public_allowed？
[ ] 6. mark：identity + liquidity-source-registry.jsonl
[ ] 7. status before/after + 更新 PROJECT_STATE（可選 temp 報告）
```

**未勾 2–5 就唔好講「齊」。**  
「any_price 滿」只係 KPI A；**入庫驗證綠** 係另一欄。

---

## 3. 報進度時要分三欄

| 欄 | 例子 |
|---|---|
| **Harvest 有** | jsonl 810 檔、SNK pull N id |
| **DB 有（寫入）** | price 932 · sale 273 · image 855 |
| **驗證綠** | identity verify accept · SAMPLE 0 · public_allowed · clean 刪錯綁 |

---

## 4. 變更

| 日期 | 內容 |
|---|---|
| 2026-07-29 | 首版：用戶強調入庫前驗係正式工序；對齊現有腳本門檻 |
