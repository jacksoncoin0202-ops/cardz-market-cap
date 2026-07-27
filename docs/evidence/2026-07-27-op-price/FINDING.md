# One Piece 價格覆蓋調查（POP≥1000 未有價 133 張）

**量測日期**：2026-07-27（DB session clock = UTC；本機 = JST +09:00）
**負責 agent**：`opus-op-price`
**寫入 DB 嘅資料**：**冇價格／身份資料**。全程唯讀 + 對外 HTTP GET。
唯一例外要講清楚：跑 `g10_identity_expand.py` dry-run 時，佢嘅 `_open_run()`
會喺 `market_ingest_run` 留一行 run 記錄（`id = 79`,
`run_key = a47c7079…`, `source_code = g10_identity`）。
呢行只係 run ledger 記帳，**冇**帶任何 `catalog_source_identity` /
`catalog_provider_identity_alias` / `market_identity_review_queue` 寫入
（收工實測 `catalog_source_identity` = 2,081 行、
`market_identity_review_queue` = 216 行，同 dry-run 報告嘅 `+0 / +0` 一致）。

---

## 一句結論

呢 133 張唔係「爬唔到價」，而係**對唔到身份**：`catalog_variant`
結構上冇 rarity／parallel／printing 欄位，但 SNKRDUNK 同 eBay 兩邊都係
**逐 parallel** 分開上架，所以每張目標卡都對住 2–20 個候選商品，
喺我哋一邊冇任何欄位揀得出邊個係邊個。任何自動對應都係猜，
而猜 = 降身份 gate，所以呢一輪**一張都冇寫**。

---

## 一、起點與終點（前後對帳）

| 量測項 | 開工前 | 收工後 |
|---|---|---|
| OP catalog 總數 | 311 | 311 |
| 有 PSA POP | 282 | 282 |
| POP≥1000 | 196 | 196 |
| 有過價（任何 POP） | 123 | 123 |
| **POP≥1000 且有價** | **63** | **63** |
| `market_price_observation` 總行數 | 120,974 | 120,974 |

目標 = 196 − 63 = **133 張**，實測正好 133 行，已出表：
[op_targets_133.csv](op_targets_133.csv)（variant_id / opaque_id /
canonical_name / collector_number / set_name / pop）。

我方寫入量 = 0，所以前後數字一致。期間 `ebay` 嘅 `last_day` 由 07-24 變
07-25，**唔係我寫**，係其他線／G10 每日 ingest 嘅正常推進。

`market_price_observation` 逐 source（收工時）：

| source_code | 行數 | 卡數 | 首日 | 尾日 |
|---|---|---|---|---|
| `snk_psa10` | 115,092 | 336 | 2023-06-19 | 2026-07-26 |
| `ebay` | 5,476 | 505 | 2026-04-25 | 2026-07-25 |
| `snkrdunk` | 406 | 336 | 2026-07-19 | 2026-07-24 |

---

## 二、對 133 張前提嘅修正（重要）

原本口徑當 133 張都係「獨立卡、等爬價」。實測唔係：

| 分類（官方重複口徑 `(tcg_code, card_language, set_name, collector_number)`） | 張數 |
|---|---|
| 單張成組（真獨立卡） | 111 |
| 2 張一組 | 17 |
| 3 張一組 | 5 |

再對 `catalog_printing_identity` 現有判定：

| `identity_status` | 張數 |
|---|---|
| `(冇 row)` | 111 |
| `canonical` | 7 |
| `duplicate` | **10** |
| `review` | 5 |

即係**133 張裡面有 10 張已經被 repo 自己嘅收斂邏輯判為 `duplicate`**，
唔應該當「未爬價嘅卡」計；另外 5 張係 `review`（同號但名唔一致，例如
`OP13-119` = `Portgas D. Ace` / `(Error)` / `(Super)` 三行）。

**扣走已判 duplicate 之後，真候選 = 123 張**，其中 111 張係乾淨單張。

另外 50 張（133 之中）有「同 collector_number 嘅兄弟 variant 已經有價」。
呢 50 張唔可以一句講成重複——OP 同一個號碼真係有 base／alt art／parallel
唔同印次，各自有獨立市價。呢個係 **catalog 粒度問題**，要 PM 決定拆定合，
我冇動 `canonical_name` / `collector_number`（硬規則）。

資料品質順手記低：有 1 張目標卡 `collector_number` 係字面 `UNKNOWN`；
`OP05-119` 有一行 `set_name` 寫 `One Piece Emperors in the New World`，
但 OP05 應該係 `Awakening of the New Era`（OP09 才係 Emperors）——疑似
set 標錯，冇改，只記錄。

---

## 三、身份覆蓋：真正嘅瓶頸

`catalog_source_identity` 對 133 張嘅覆蓋：

| source_code | 覆蓋 133 張之中幾多 |
|---|---|
| `gemrate` | 133 |
| `ebay` | **1** |
| `snkrdunk` | **1** |

即係全部 133 張只有 POP 身份，冇價源身份。而兩條寫價路徑都硬性要求價源身份：

- `g10_ebay_ingest.py` 明寫「唯一合法路徑係 `catalog_source_identity`，唔准靠卡名」
- `market_source_sync.py` 要 crosswalk 有 `snkItemId`，
  而 `snk_identity_is_exact()` 要 `matchStatus == "exact"` 加
  collectorNumber／language／parallel 完全一致

所以「爬蟲能力」由頭到尾都唔係 gate。

---

## 四、eBay／G10 線：已飽和，天花板 = 1 張

- G10 磁碟（`grade10-scraper/data/cards/{altxyz,snkrdunk}/`）共 **641 個目錄**，
  其中 One Piece 只有 **68 個**（altxyz 53 / snkrdunk 15），66 個有
  `ebay_PSA_10.json`。單靠磁碟就已經蓋唔到 133 張。
- 跑 `g10_identity_expand.py` dry-run（641 目錄全掃）：
  `accepted 585 / quarantined 52 / rejected 4`，
  證據階梯只出現 `direct+gemrate 360`、`direct 208`、`direct+gemrate_conflict 17`、
  `unresolved 56`——**冇任何 `gemrate`-only 或 `name_collector` 命中**，
  實際寫入 `identity +0 / alias +0 / review +0`。
  即係**身份擴展已經飽和，再跑一次一行都唔會多**。
- 用 collector_number 對 G10 `cardId`（已 normalize 掉連字號差異）：
  43 張命中，但拆開睇只有 **10 張** 係兩邊都唯一；29 張係我方同號多 variant，
  4 張係 G10 同 `cardId` 多目錄。而嗰 43 個 G10 目錄本身早已 `direct` 對到
  *其他* variant（通常係已經有價嘅嗰個兄弟），一個目錄只可以擁有一條 variant，
  所以呢條路對 133 張嘅淨增益 ≈ 0。

**要加 G10 roster 嘅話**（唔掂 GemRate key 部分）：要喺
`grade10-scraper` 上游加卡，令目錄帶 `populations.json` 嘅 40-hex `gemrate_id`，
之後 `g10_identity_expand.py` 嘅 T1 gemrate 階梯就會自動對到我哋已有嘅
`catalog_source_identity(gemrate, …)`，唔需要靠卡名。呢步係上游 roster 工作，
唔喺呢個 repo。

---

## 五、SNKRDUNK 線：有貨，但對唔到 parallel

好消息：SNKRDUNK **真係有** OP 單卡，而且用 collector number 搜得到，
標題帶方括號號碼 + 稀有度 + 日文 set 名，例如
`ギア2 R-P [OP11-080](ブースターパック「神速の拳」)`。
set 名亦對得返我哋英文 set（神速の拳 = A Fist of Divine Speed、
王族の血統 = Royal Blood、ロマンスドーン = Romance Dawn），可以做 set 層 disambiguation。

壞消息係 parallel 層。實測 113 個 distinct collector number：

| 判定 | collector number 數 |
|---|---|
| `unique_single`（只有 1 個候選） | **0** |
| `parallel_ambiguous`（2 個以上） | 112 |
| `not_found` | 1（就係 `UNKNOWN` 嗰個） |

每個號碼嘅候選 apparel id 數：最少 2、中位數約 6、最多 20。
例如 `OP11-080` 有 `ギア2 R-P`（parallel）同 `ギア2 R`（base）兩件，
而我哋 catalog 嗰行只叫 `Gear Two`，冇任何 rarity/parallel 標記——揀唔到。

產出：
- 產生器 [snk_candidate_discovery.py](snk_candidate_discovery.py)（唯讀，
  只做 search HTML GET，唔寫 DB 唔寫 crosswalk）
- 逐號候選 + 證據 [snk_candidates.json](snk_candidates.json)
- 人手審批用嘅摘要 [snk_ambiguity.csv](snk_ambiguity.csv)

### 中途改正嘅兩個自身 bug（記錄落嚟，因為佢直接影響數字）

1. 第一版把 sealed 判斷行落**整個** window，結果
   `ブースターパック「神速の拳」` 呢類 **set 名**入面嘅「パック」被當成未開封商品，
   誤殺真單卡，令 `OP11-080` 之類跌入 `not_found`。已改成只用方括號**之前**嘅標題判。
2. 第二版 dedup key 用 `(itemId, title)`，但同一頁同一個 apparel id 會用兩種寫法
   出現（`ロロノア・ゾロ L パラレル` 同 `ロロノア・ゾロ L-P`），令同一件商品當兩個候選、
   虛報 ambiguity。已改成用 `itemId` dedup。

修正前一度得出「21 個號碼唯一可對」，**嗰個數係錯嘅**，改正後係 **0**。
兩個 bug 都已經改喺 producer 腳本裡面。

### 為何唔准硬寫 crosswalk

`source_crosswalk.py` 嘅 `snkItemId` 係由上游 constituents index 推導：
`int(external_id) if source_code == "snkrdunk"`。即係一張卡要 canonical source
本身係 snkrdunk 才會有 `snkItemId`。我唔可以喺呢一層憑搜索結果塞一個 id 落去——
咁樣等於繞過 `snk_identity_is_exact()` 嘅 parallel 檢查，即降 gate。

---

## 六、卡住嘅原因（逐項）

| # | 卡住位 | 影響張數 | 原因 |
|---|---|---|---|
| 1 | catalog 冇 parallel／rarity 欄位 | 133（全部） | `catalog_variant` 7 條 identity 欄位到 `collector_number` 為止；兩邊價源都逐 parallel 上架，中位數 6 個候選揀 1 個，冇判別依據 |
| 2 | G10 磁碟 OP roster 太細 | 133 − 68 | 磁碟只有 68 個 OP 目錄，物理上蓋唔到 |
| 3 | `g10_identity_expand.py` 已飽和 | 68 | dry-run 淨增 identity +0 / alias +0 |
| 4 | crosswalk OP 名單封閉 | 133 − 1 | crosswalk 只有 100 張 OP、47 張有 `snkItemId`，全部已有價；名單由上游 index 決定 |
| 5 | 同號重複 variant | 22（10 已判 duplicate、5 review） | 官方重複口徑下屬同組，係 catalog 粒度問題唔係價格問題 |
| 6 | `collector_number = 'UNKNOWN'` | 1 | 冇號碼，任何以號碼為主嘅發現都對唔到 |

---

## 七、建議下一步（要 PM 決定，我冇做）

1. **加 printing discriminator**：喺 catalog 加一個 rarity／parallel 欄位
   （唔改現有 `canonical_name`／`collector_number`）。呢個係唯一一次性解開
   全部 133 張嘅做法，亦同時解 eBay 同 SNK 兩邊。
2. **人手批一批高 POP 卡**：用 [snk_ambiguity.csv](snk_ambiguity.csv) 由 POP 最高
   逐張揀 apparel id，寫成 exact mapping。頭 20 張大概覆蓋最大市值。
3. **上游 G10 roster 擴容**：令新目錄帶 `gemrate_id`，之後 T1 階梯自動對，
   零卡名猜測。屬 `grade10-scraper` 側工作。
4. **先處理 10 張 `duplicate` + 5 張 `review`**：呢 15 張根本唔應該計入
   「未有價」分母，清完之後分母同 KPI 都會準啲。

---

## 前提（premises）

- POP 用 `market_grader_population_observation.top_grade_population`
  取 `MAX()`，`grader_code='PSA'`。
- 「有價」= `market_price_observation` 有任何一行，唔設新鮮度條件
  （48 小時新鮮度係另一層 gate，本次冇動）。
- `tcg_code = 'one-piece'`（有連字號）。
- 行數一律 `COUNT(*)`，冇用 `information_schema.TABLE_ROWS`。
- 重複口徑跟 `converge_printing_identity.py` 同
  `docs/DB_INVENTORY_20260726.md` §4.2 嘅
  `(tcg_code, card_language, set_name, collector_number)`，冇重新發明。
- SNKRDUNK 候選係 2026-07-27 嘅 search HTML 快照；商品上落架會令數字浮動。
- 冇改任何 gate、冇改 catalog canonical 欄位、冇覆寫
  `data/public/seed-snapshot.json`、冇 commit／push／deploy、冇動 GemRate key。
