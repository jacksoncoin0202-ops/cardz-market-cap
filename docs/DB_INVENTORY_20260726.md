# DB Inventory Report — 2026-07-26

**查詢方式**：一次性唯讀腳本 `temp/db_inventory.py`（沿用 `pipelines/db_runtime.py` 嘅 `add_connection_args`/`connection_from_args` connection 慣例 + `scripts/verify_daily_run.py` 嘅 `backend.env` 讀取慣例），session 顯式 `SET SESSION TRANSACTION READ ONLY`，全程只有 `SELECT`。
**查詢執行時間**：2026-07-26 05:50 本機時間。
**DB server 時鐘**：`CURDATE()` 喺查詢當刻回傳 `2026-07-25`（DB server 同本機時鐘相差約 1 日，懷疑係 UTC vs 本機時區，下面所有「最近 30 日」「gap day」都以 DB server 嘅 `2026-07-25` 做基準日，唔係用本機日期）。
**目標**：驗證假設「我哋 DB 應該好多數據起手，只係去重同增量同步」是否成立。

---

## 1. 一句總結

**假設部分成立，部分唔成立** —— raw 觀測數據（`market_price_observation` / `market_source_observation` / `market_daily_sales_aggregate`）真係有 2023-06 至今嘅 3 年歷史，唔需要重爬；但 **catalog identity 層（`catalog_variant`）成個係 2026-07-23～07-25 呢 41 小時內先建立**（唔係舊数据），**POP population 觀測（`market_grader_population_observation`）只有 5 日歷史（07-21～07-25）**，**`market_fx_rate_observation` 同 `market_tracked_sales_aggregate` 兩張表完全零行**。去重方面，觀測 fact table（price / population）**實測 0% 重複**（UNIQUE KEY 有效擋住），真正嘅重複集中喺 **`catalog_variant` 本身（2.6%–3.3% 涉及重複組）**——即係話要做嘅唔係「fact table 去重」，而係「catalog identity 去重」。增量同步方面，`snk_psa10` / `gemrate` / `snkrdunk` / `ebay` 都仲生存緊，但 `tag` source 得 07-22 一日數據、之後再冇寫入，狀態存疑。Roster 對齊方面，`tracked-gemrate-ids.txt`（1468 個）**100% 喺 DB 有對應**，但 `gemrate-ids.txt`（600 個）得 62.3%（374 個）有對應，**226 個（37.7%）完全唔喺 DB 度**——呢個係最大缺口，要重爬嘅唔係全庫，係呢 226 個 GemRate ID。

---

## 2. 每表清單（Part A）

先用 `information_schema.tables` 攞真實表清單，一共 **34 張 base table**（唔止 brief 提到嗰 9 張）。以下按資料量排序，`created_at`/`observed_date`/`effective_date`/`effective_at` 揀優先順序自動偵測時間欄。

| 表 | 行數 | 時間欄 | 最早 | 最新 | distinct variant_id |
|---|---:|---|---|---|---:|
| `market_source_observation` | 277,127 | observed_date | 2023-06-19 | 2026-07-25 | — (無 variant_id 欄) |
| `market_price_observation` | 115,253 | observed_date | 2023-06-19 | 2026-07-24 | 395 |
| `market_grader_population_observation` | 7,904 | observed_date | 2026-07-21 | 2026-07-25 | 1,590 |
| `market_population_transport_observation` | 7,706 | effective_date | 2026-07-21 | 2026-07-25 | 1,590 |
| `market_universe_member` | 4,102 | created_at | 2026-07-23 | 2026-07-25 | 1,590 |
| `market_daily_sales_aggregate` | 3,592 | observed_date | 2023-07-20 | 2026-07-24 | 336 |
| `catalog_source_identity` | 1,891 | created_at | 2026-07-23 | 2026-07-25 | 1,590 |
| `market_index_constituent` | 1,908 | created_at | 2026-07-24 | 2026-07-24 | 293 |
| `catalog_variant` | 1,590 | created_at | 2026-07-23 | 2026-07-25 | — |
| `market_candidate_daily_snapshot` | 1,964 | created_at | 2026-07-24 | 2026-07-24 | 395 |
| `market_ingest_run` | 68 | effective_at | 2026-07-19 | 2026-07-25 | — |
| `market_alert` | 66 | created_at | 2026-07-24 | 2026-07-24 | 66 |
| `market_alert_event` | 65 | created_at | 2026-07-24 | 2026-07-24 | — |
| `market_universe_lock` | 6 | effective_at | 2026-07-23 | 2026-07-24 | — |
| `market_alert_evaluation` | 7 | effective_date | 2026-07-21 | 2026-07-24 | — |
| `market_index_snapshot` | 12 | effective_date | 2026-07-21 | 2026-07-24 | — |
| `market_ingest_checkpoint` | 1 | — | — | — | — |
| `cardz_migration_ledger` | 10 | — | — | — | — |
| `cardz_schema_version` | 9 | — | — | — | — |
| `market_fx_rate_observation` | **0** | effective_date | — | — | — |
| `market_tracked_sales_aggregate` | **0** | — | — | — | — |
| `catalog_printing_identity` | **0** | — | — | — | — |
| `catalog_provider_identity_alias` | **0** | — | — | — | — |
| `catalog_story_pointer` | **0** | — | — | — | — |
| `catalog_variant_locale` | **0** | — | — | — | — |
| `market_identity_review_queue` | **0** | — | — | — | — |
| `market_image_asset` / `market_image_qc` / `market_image_source_pointer` | **0** | — | — | — | — |
| `market_raw_payload_object` | **0** | — | — | — | — |
| `market_retention_archive_manifest` | **0** | — | — | — | — |
| `market_sale_observation` | **0** | — | — | — | — |
| `market_source_effective_observation` | **0** | — | — | — | — |
| `market_source_observation_payload_pointer` | **0** | — | — | — | — |

**重點觀察**：
- **`catalog_variant`（1590 行）全部 `created_at` 落喺 2026-07-23 17:42 至 2026-07-25 10:42 呢 41 小時內** —— catalog identity 層係最近先重建，唔係「舊數據」。呢個同 `catalog_source_identity`（同樣 07-23 起）吻合，證實兩者係同一次 identity rebuild 出嚟。
- `market_price_observation` / `market_source_observation` / `market_daily_sales_aggregate` 三張 raw 觀測表**先至係真係有 2023 年歷史嘅表**，證明「舊 scrape 數據冚曬保留咗」呢個講法係啱嘅——但佢哋只覆蓋 **395 / 1590 個 variant（24.8%）**，即係得返舊 catalog 有覆蓋嗰批卡先有價格歷史，新加入 catalog 嘅卡（07-23 之後新增）大部分未有任何價格歷史。
- `market_fx_rate_observation`（brief 明確問到嘅表）**完全 0 行，由頭到尾未寫過** —— FX 匯率轉換呢個功能實際上未跑過。
- `market_tracked_sales_aggregate`（brief 明確問到嘅表）**同樣 0 行**。
- `market_index_constituent` 只有 07-24 一日嘅快照（1908 行 = 3 indexes × 該日 constituent 數），`market_index_snapshot` 得 12 行（4 日 × 3 index），index 快照歷史都好短。

---

## 3. 時間覆蓋 + Gap Day（Part B）

以 DB server `CURDATE()=2026-07-25` 為基準，回溯 30 日（2026-06-26 至 2026-07-25）。

### `market_price_observation`（每日行數）

> ⚠️ 下表係 2026-07-26 嘅量度（本檔係凍結快照，數字唔會逐格更新）。**2026-07-27 複測：07-25 已由 0 → 755 行、07-24 由 113 → 1009 行**（07-26 補 run 填返）。下面「永久性缺口，唔使再查」結論已作廢，唔好再引用；現行數字即場用 `scripts/ro_sql.py` 重量。

| 日期 | 行數 | 日期 | 行數 | 日期 | 行數 |
|---|---:|---|---:|---|---:|
| 06-26 | 203 | 07-06 | 204 | 07-16 | 191 |
| 06-27 | 195 | 07-07 | 209 | 07-17 | 177 |
| 06-28 | 196 | 07-08 | 220 | 07-18 | 173 |
| 06-29 | 215 | 07-09 | 218 | 07-19 | 566 ← 單日暴增 |
| 06-30 | 222 | 07-10 | 209 | 07-20 | 206 |
| 07-01 | 208 | 07-11 | 197 | 07-21 | 185 |
| 07-02 | 203 | 07-12 | 204 | 07-22 | 195 |
| 07-03 | 211 | 07-13 | 196 | 07-23 | 168 |
| 07-04 | 201 | 07-14 | 205 | 07-24 | 113 ← 偏低 |
| 07-05 | 230 | 07-15 | 202 | **07-25** | **0 ← GAP** |

- **量度當日 2026-07-25 = 0 行**，與已知 root cause（UTC vs JST 時區令舊 06:30 JST schedule 嘅 `run_id` 誤判為 replay，跳過重爬；已於 2026-07-26 修好）吻合。**（2026-07-27 更新：呢個缺口已被 07-26 補 run 填返 755 行，唔係永久缺口——見上面警告行。）**
- 07-19 單日 566 行係唯一異常高峰（其餘日子穩定喺 168–230 行區間），07-24 得 113 行明顯偏低——可能係修復當日只執行咗部分 batch，需要留意但唔影響本報告結論。

### `market_grader_population_observation`（每日行數）

| 日期範圍 | 行數 |
|---|---:|
| 06-26 ～ 07-20（合共 25 日） | **全部 0** |
| 07-21 | 2,363 |
| 07-22 | 521 |
| 07-23 | 1 ← 近乎失敗 |
| 07-24 | 3,883 |
| 07-25 | 1,136 |

- POP 觀測**實質只有 5 日歷史（07-21～07-25）**，之前 25 日全部零行——同 Part A 發現一致：呢條 pipeline 係最近先接通，唔係「歷史豐富」嘅表。07-23 得 1 行，睇落似一次接近失敗嘅執行，建議另外覆查該日 ingest log，但唔影響本次結論方向。

---

## 4. 去重分析（Part C）

### 4.1 Fact table（price / population）—— 實測結果：0% 重複

| 表 | Grouping（brief 字面要求） | 重複組 | 涉及行數 | 總行數 | 佔比 |
|---|---|---:|---:|---:|---:|
| `market_price_observation` | `(variant_id, observed_date, source_priority)` | 0 | 0 | 115,253 | 0% |
| `market_price_observation` | 真實 UNIQUE KEY `(variant_id, source_code, observed_date)` | 0 | 0 | 115,253 | 0% |
| `market_grader_population_observation` | `(variant_id, grader_code, effective_at)` | 0 | 0 | 7,904 | 0% |
| `market_grader_population_observation` | 真實 UNIQUE KEY `(variant_id, grader_code, source_code, observed_date)` | 0 | 0 | 7,904 | 0% |

**重要澄清**：呢兩張表嘅真實 UNIQUE KEY 其實同 brief 字面寫嘅 tuple 唔一樣——`market_price_observation` 嘅 key 用 `source_code` 唔係 `source_priority`；`market_grader_population_observation` 嘅 key 用 `source_code + observed_date` 唔係 `effective_at`。兩種 grouping 分開驗證過，**結果都係零**，即係話呢兩張表本身冚一齊都冇重複行，`source_priority` 相同都唔會撞——因為唔同 `source_code`（`snk_psa10`/`snkrdunk`/`ebay`/`gemrate`/`tag`）本身已經令 tuple 唔會撞。**呢兩張 fact table 完全唔需要做去重工作。**

### 4.2 `catalog_variant`（真正有重複問題嘅表）

| Grouping | 重複組 | 涉及行數 | 總行數 | 佔比 |
|---|---:|---:|---:|---:|
| `(tcg_code, card_language, set_name, collector_number)` | 24 | 52 | 1,590 | **3.3%** |
| `(canonical_name, set_name, collector_number, card_language)` | 20 | 41 | 1,590 | **2.6%** |

樣本（`(tcg_code, card_language, set_name, collector_number)` 撞到嘅頭 3 組）：

| tcg | 語言 | set | collector_number | 重複次數 | variant_id 們 |
|---|---|---|---|---:|---|
| one-piece | en | One Piece Carrying On His Will | OP13-118 | 3 | 1465, 1466, 1468 |
| one-piece | en | One Piece Carrying On His Will | OP13-119 | 3 | 1464, 1467, 1565 |
| one-piece | en | One Piece Carrying On His Will | OP13-120 | 3 | 956, 1558, 1563 |

**結論**：Brief 原本問嘅係 price / population 表有冇重複，但實測顯示嗰兩張表非常乾淨（UNIQUE KEY 生效）。**真正需要去重嘅係 `catalog_variant` 本身**——同一張實體卡（同 set、同 collector number、同語言）出現 2–3 個唔同 `variant_id`，涉及 52 行（3.3%）。呢個唔算好大範圍，但直接影響 Part A 提到嘅「395/1590 個 variant 先有價格歷史」計算會唔會被重複 variant_id 拉低——值得做，但唔係阻礙增量同步嘅級別問題。

（附註：`catalog_printing_identity` 表理論上係用嚟做 canonical printing key 嘅收斂表，但目前 0 行，未有實際使用——如果未來想徹底解決 catalog_variant 重複問題，呢張表係設計上嘅正確落腳點。）

---

## 5. 各 Source 存活狀態（Part D）

`market_ingest_checkpoint` 表得 1 行（`source_code=cardz_normalized, stream_key=canonical_batches, last_effective_at=2026-07-25T15:51:47`），唔係逐 source 嘅 checkpoint 帳——實際逐 source 嘅「最後寫入日」改以 `GROUP BY source_code` 直接喺 fact table 度量度。

### `market_price_observation` by source_code

| source_code | 行數 | 最早 | 最新 | distinct variant |
|---|---:|---|---|---:|
| `snk_psa10` | 114,755 (99.6%) | 2023-06-19 | 2026-07-24 | 336 |
| `snkrdunk` | 406 | 2026-07-19 | 2026-07-24 | 336 |
| `ebay` | 92 | 2026-07-19 | 2026-07-24 | 59 |

### `market_grader_population_observation` by source_code

| source_code | 行數 | 最早 | 最新 | distinct variant |
|---|---:|---|---|---:|
| `gemrate` | 5,950 | 2026-07-21 | 2026-07-25 | 1,477 |
| `snkrdunk` | 1,506 | 2026-07-21 | 2026-07-24 | 336 |
| `ebay` | 242 | 2026-07-21 | 2026-07-24 | 59 |
| `tag` | 206 | 2026-07-22 | **2026-07-22（只此一日）** | 206 |

### `market_source_observation` by source_code（raw ingestion ledger）

| source_code | 行數 | 最早 | 最新 |
|---|---:|---|---|
| `snk_psa10` | 268,135 | 2023-06-19 | 2026-07-24 |
| `gemrate` | 6,146 | 2026-07-21 | 2026-07-25 |
| `snkrdunk` | 2,247 | 2026-07-19 | 2026-07-24 |
| `ebay` | 393 | 2026-07-19 | 2026-07-24 |
| `tag` | 206 | 2026-07-22 | 2026-07-22（只此一日） |

### `market_daily_sales_aggregate` by source_code

| source_code | 行數 | 最早 | 最新 |
|---|---:|---|---|
| `snk_psa10` | 3,592 | 2023-07-20 | 2026-07-24 | （唯一 source，其他 source 完全未餵過呢張表）

**評估**：
- **ALIVE（持續寫緊）**：`snk_psa10`（主力，3 年歷史）、`gemrate`（POP 權威來源，5 日歷史但每日持續）、`snkrdunk`、`ebay`——四個 source 最後寫入日都係 07-24 或 07-25，同已知 gap 一致，屬正常運作。
- **狀態存疑**：`tag` source **只喺 2026-07-22 出現過一次**（206 行，206 個 variant），之後 3 日完全冇再寫入——唔清楚係一次性 backfill 定係已經死咗嘅 pipeline，**未能確認**，需要人手覆查 `tag` source 嘅 ingest 排程定義先可以落結論。
- **增量同步建議 resume 日期**：`snk_psa10` / `snkrdunk` / `ebay`（price）由 **2026-07-25** 開始補；`gemrate`（population）由 **2026-07-26** 開始（07-25 已有齊 1136 行，係最新嘅）；`tag` 需要先確認係咪仲有效先決定點跟進。

---

## 6. Roster 對齊（Part E）

`catalog_source_identity WHERE source_code='gemrate'` 總共 **1,496** 個 identity 對應（去重後 external_entity_id）。

| Roster 檔案 | 檔案內 ID 數 | 喺 DB 有對應 | 冇對應（缺口） | 對應率 |
|---|---:|---:|---:|---:|
| `tracked-gemrate-ids.txt` | 1,468 | **1,468** | **0** | **100%** |
| `gemrate-ids.txt` | 600 | 374 | **226** | 62.3% |
| 兩檔聯集（去重） | 1,721 | 1,495 | 226 | 86.9% |

冇對應樣本（`gemrate-ids.txt` 缺口，頭 10 個）：
```
02301986c03376d30e30aa180ade48dba93cc44e
0286b637e1e184f5341dc9e59c3cb6fb121312e6
034db065646d0d27b7848af5e24fd63994ac8e74
04d718ff34a8299705a1eb37cc4716a42ac1655c
04df698563efe3e76e76b75cebfdc95243216369
056a29073ae574f609d88b58a6aed6d158518e04
0679c1f82316b6353e0bc9fbea3af6f315e27d20
06fc2c69b95a880ab4d8d55d65728c43193411e2
0b31619e8ad6b1924b84c0b91dc7d4b15e0ee68b
0b827d9089696d3ef59fe72dd3b3eb24f1d5462b
```
（完整 226 個清單喺 `temp/db_inventory_result.json` → `roster_alignment.gemrate_ids_600`，呢度只擷取樣本）

另外，DB 入面有 **1 個** gemrate identity 完全唔屬於任何一個 roster 檔案（`db_gemrate_ids_not_in_any_roster_file: 1`）——即係 roster 檔案幾乎完全覆蓋咗 DB 現有嘅 gemrate 對應，冇「DB 多咗 roster 唔知嘅」呢種情況。

**結論**：`tracked-gemrate-ids.txt`（主力追蹤名單）**100% 已經入庫**，呢部分完全唔需要重爬。真正嘅缺口喺 `gemrate-ids.txt` 嗰 226 個（37.7%）—— 呢批 ID 從未成功解析成 `catalog_variant`，屬於「未爬過」而唔係「爬完要去重」。

---

## 7. 建議下一步

1. **唔使重爬**：`snk_psa10` / `snkrdunk` / `ebay` 嘅 raw 價格同 sales aggregate 歷史（2023-06 至 2026-07-24）完整保留，`tracked-gemrate-ids.txt` 1468 個 roster 100% 已入庫——呢部分直接做**增量同步**即可，resume 日期見 §5。
2. **要重爬（局部）**：`gemrate-ids.txt` 嗰 226 個未入庫 GemRate ID（清單喺 `temp/db_inventory_result.json`）——呢批係真正嘅資料缺口，唔係去重問題。
3. **要去重（局部，唔急）**：`catalog_variant` 有 3.3%（52/1590 行，24 組）疑似同一張實體卡對應多個 `variant_id`，建議透過 `catalog_printing_identity`（目前 0 行，設計上就係做呢件事嘅表）補回 canonical printing key 收斂邏輯。Price / population 兩張 fact table**完全唔需要去重**（實測 0%）。
4. **需要覆查（未能確認，唔好假設）**：
   - `tag` source 只喺 07-22 出現一次，之後靜默——係一次性 backfill 定係已死 pipeline，未能確認，要睇返 ingest 排程設定先知。
   - `market_fx_rate_observation`（0 行）同 `market_tracked_sales_aggregate`（0 行）呢兩張表由頭到尾未寫過——如果呢兩個功能係現行需求嘅一部分，需要另外評估係咪要由零開始接通，唔係「增量同步」可以解決，**未能確認**呢兩個功能而家係咪仲喺 roadmap 上。
   - `catalog_variant` 只有 41 小時嘅 `created_at` 歷史（07-23 17:42～07-25 10:42），代表 identity 層最近整套重建過——重建原因、以及重建有冇完整保留舊 variant_id 對外部（例如前端 URL slug）嘅參照，**未能確認**，需要另外查 migration/deploy 紀錄，唔喺本次 DB 查詢範圍內。

---

## 附錄：查詢腳本與原始輸出

- 查詢腳本：`temp/db_inventory.py`（唯讀，session 級 `SET SESSION TRANSACTION READ ONLY`，冇任何寫入語句）
- 完整結構化輸出：`temp/db_inventory_result.json`（本報告所有數字嘅原始來源，含 226 個缺口 ID 全list、24 組 catalog_variant 重複組全部樣本等未有喺報告內完整貼出嘅細節）
