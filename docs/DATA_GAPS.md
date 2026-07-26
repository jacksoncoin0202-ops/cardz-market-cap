# DATA GAPS

已知數據缺口登記處。**呢個檔係累積式：新增請 append 一節，唔好覆蓋人哋寫低嘅嘢。**
每節要寫：缺乜、實測證據、影響邊度、係咪源頭限制、暫定處理。

---

## 🔴 延時炸彈 — `2026-07-29T00:00:00Z`

**全份文件唯一有硬日期嘅缺口。接手嘅人第一眼要見到呢個。**

| | |
|---|---|
| **咩事** | DB 入面 **220 張** ranked 卡嘅 TAG POP 觀測全部係 `2026-07-22T00:00:00Z`，同一秒。 |
| **幾時爆** | **`2026-07-29T00:00:00Z`**（07-22 + 168h）。跟 pipeline run 嘅 `generation.effectiveAt` 行，唔係 wall-clock。 |
| **點爆** | **0 → 220 條 validation error 一步跳**。冇 partial degradation、冇漸進警告、冇黃燈。`publish` 直接擋死，日更當日死。 |
| **⚠️ 閘係啱嘅，唔好去拆佢** | `assertGraderPopulationFreshness()`（`packages/market-data/src/validate.ts`）係**今日先加入**嘅 —— `git show HEAD` 驗到 HEAD 冇呢個 function，working tree 有 3 處引用。**即係話呢條死線尋日並唔存在**，係新閘揭穿咗一個一路都喺度嘅腐爛數據。真正嘅 bug 喺 producer：`latest_populations()` **冇日期下限**，一個死咗五日嘅源照樣 stamp `status="ready"`。閘只係第一個講真話嘅人。 |
| **阻唔阻上線** | 🟡 **唔阻聽日（07-27）交付**。**阻 AWS 上線後第 3 日。** `data/public/seed-snapshot.json` 唔受影響 —— demo seed 嘅 TAG 全部 `unavailable`，被閘 skip。 |
| **詳情 / 修法** | 見下面 §「TAG — DB 現存觀測凍喺 2026-07-22」（TAG agent 寫，內有修完實測數字同 producer 改動建議） |
| **來源** | 量度日期 2026-07-26 / 來源：POP delta agent 實測 |

---

## 優先次序表（兩維：影響訪客 × 補救路徑）

**用途：睇邊幾樣可以夾埋同一批做，唔使逐條開工。**

| | **有現成補救路徑**（跑返／改返現有嘢） | **要新開發**（要寫新嘢或者搵新源） |
|---|---|---|
| **🔴 訪客見到** | **A** TAG 凍結 · **D** POP delta 覆蓋薄 · **E** 12 張出唔到街 · **K** 19 張缺四語故事 · **L** 卡名／故事錯配 · **O** GemRate 歷史檔缺 · **P** FX 單日單點 · **T** 四源同日縮水（根因未查） | **B** TAG 永遠冇 POP 歷史 · **F** 卡圖 QC 孤兒 331 · **G** DB 卡圖 QC 閘從未通電 · **I** catalog 身分重複 |
| **⚪ 只影響內部** | **H** 203 條裁決積壓 · **J** 隔離／拒收積壓 · **N** roster 檔案分裂 · **R** 文檔數字腐爛 | **C** POP 變動上游三層斷 · **M** 價格覆蓋率 23.2% · **Q** 六張表 0 行 |

**S**（snapshot 自報 6 個 blocker + `mode:"demo"`）唔入呢個格 —— 佢係上面各項嘅**總和讀數**，見下面 §S。

### 可以同一批做嘅分組

| 批 | 包含 | 一次過做嘅理由 |
|---|---|---|
| **① GemRate 抓數批** | **D** · **O** | 兩樣都係「要 GemRate 補數」。key 有每日 1000 配額，開一次窗口一次過掃，唔好分兩次燒配額 |
| **② 卡圖 QC 批** | **F** · **G** · **E** 嘅 3 張 | 同一條 QC 鏈。改 keying（opaque_id → variant_id）同重跑 QC 要一齊做，分開做會再漂一次 |
| **③ 人手裁決批** | **H** · **I** · **E** 嘅 9 張 · **L** | 全部係「同一張實體卡分裂／對唔到身分」，要人眼睇。開一次 review session 過晒 |
| **④ 編輯批** | **K** | 19 張缺英文原文 → 補完英文先入翻譯隊列（`editorial_translate_queue.py`） |
| **⑤ 接線批** | **C** · **Q** · **G** | 全部係「表／欄存在但冇人讀寫」。改 producer / 加 migration，同一輪 code review |
| **⑥ 要新源批** | **B** · **M** | 呢兩樣抓幾多次現有源都解決唔到，要揾新供應商／新採集器。**唔好混入①** |
| **⑦ 治理批** | **J** · **N** · **R** | 全部係「決定 + 記錄」，唔使寫新 code |

> **T 未夠資格入批。** 「四個源同日集體縮水」（見下面 §「四個源同日集體縮水」，sched-ops agent 寫）
> 根因未查 —— **真斷更** 定 **`source_code` 改名** 兩個可能性後果完全相反，
> 一個要修數據、一個要修閘。**先花 30 分鐘查 writer 側 diff，查完先分批。**

### 上線閘對照（snapshot 自報 blocker → 對應缺口）

| `generation.blockers` | 對應本文缺口 |
|---|---|
| `canonical_identity_review_pending` | **H** · **I** |
| `image_semantic_qc_review_pending` | **F** · **G** |
| `grader_supply_universe_pending` | **B** · **D** · **A** |
| `four_locale_editorial_review_pending` | **K** |
| `production_database_cutover_pending` | **G** · **Q** |
| `price_reference_over_48h` | **M** · **A** |

**即係話：現有 6 個上線閘，全部喺呢份文入面有對應實體缺口，冇一個係憑空。**

---

## TAG — 冇 per-card POP 歷史（源頭限制，抓幾多次都解決唔到）

- **登記日**：2026-07-26
- **狀態**：源頭限制，未解決

### 缺乜
TAG **唔喺 GemRate per-card population API 入面**（`pipelines/canonical_public_snapshot.py:47` 早有註明）。
`data/private/gemrate/cards/<sha1>/history_full.json` 有 157 個週度點（2023-07-29 → 2026-07-25），
入面**只有 PSA / BGS / CGC / SGC，冇 TAG**。

### 實測證據
- TAG 有現值嘅卡：**0 張有歷史**。
- 因此 `graderPopulations.TAG.topGradePopulationChangePct` 嘅 `7d` / `30d` **永遠計唔出**。
- 對比：PSA/BGS/CGC/SGC 有 3 年週度歷史，delta 正常出到。

### 影響
- TAG 出街只可以有「現值」，冇「升跌」。
- 唔可以攞 0 或者任何推算值頂替 —— 冇數就係 `unavailable`，value 必須 `null`
  （`packages/market-data/src/validate.ts` 嘅 `validateMetric` 已經釘死呢點）。

### 暫定處理
唔開支線去補。TAG 嘅 change window 保持 `unavailable`，直到有真正嘅 TAG 歷史來源。

---

## TAG — DB 現存觀測凍喺 2026-07-22（pipeline 已修，但未跑入 DB）

- **登記日**：2026-07-26
- **狀態**：pipeline 已修並實測通過；**production landing / DB 未回填**

### 死因（已修）
`pipelines/tag_pop_data.py` 舊版喺 `dump_fresh` 嘅 `for year → for set` 迴圈入面
`raise RuntimeError("TAG set identity is incomplete for {year}")`，
1997 年 3 條爛行就炸死成個 multi-year catalog dump。
`run_daily.py` 接住 error 後改用 `latest_tag_catalog(max_age_hours=72)` 回退，
07-23 成功回退一次（重用 07-22 數據），之後 cache 過咗 72h 就永遠 `None`。
→ 每日照跑、每日照死，係「排程住失敗」唔係「冇排程」。

### 修完實測（2026-07-26）
| 指標 | 修之前 | 修之後（實測） |
|---|---|---|
| `tagCards` | 0 | **324** |
| `tagMissing` | 1241 | **917** |
| `tagObservations` | 0 | **324** |
| catalog rows | 0（爆） | **26,230** |
| `unusableSets` | — | **0 / 2637** |
| `observedDate` | — | **2026-07-26** |

同一個 1355 卡 active universe 對比。驗證輸出寫喺 `temp/`，**冇掂 production landing**。

### 未做
新觀測**未入 DB**。DB 現存 TAG 觀測全部係 `2026-07-22T00:00:00Z`（220 張 ranked 卡，同一秒）。
要有人跑真嘅日更 pipeline（唔係 `--output temp/`）先會回填。

### 硬期限
`packages/market-data/src/validate.ts` 新增嘅 per-grader 168h 新鮮度閘，
會喺 **`2026-07-29T00:00:00Z`**（07-22 + 168h）一次過爆 **220 條** validation error，
publish 直接擋死。冇 partial degradation，係 0 → 220 一步跳。
（trip 點跟 pipeline run 嘅 `generation.effectiveAt` 行，唔係 wall-clock。）

`data/public/seed-snapshot.json` **唔受影響** —— demo seed 360 張卡 TAG 全部係
`unavailable`，被閘 skip。

### 要人做嘅 producer 改動（我冇改，`canonical_public_snapshot.py` 有第二個 agent 郁緊）

**根因**：`latest_populations()` **冇日期下限** —— 只要 DB 有任何一行就照出 `status="ready"`，
所以一個死咗五日嘅源會被標成「現值」。閘唔係問題，閘只係揭穿咗呢件事。

**改法**（喺 `pipelines/canonical_public_snapshot.py` 砌 `graderPopulations` 嗰度）：
1. 攞每個 grader 嘅觀測 `effective_at`，同 `generation.effective_at` 計差。
2. 超過 **168h**（同 `validate.ts` 嘅 `POPULATION_FRESHNESS_HOURS` 對齊，唔好各有各數）：
   - `status` → `"unavailable"`
   - `value` → `null`（`validateMetric` 硬性要求 unavailable 必須 value=null）
   - `asOf` 照留，方便查係幾時斷嘅
3. `topGradePopulationChangePct` 同步標 `unavailable`。
4. **唔准**降級做 `"stale"` —— `stale` 一樣會被新鮮度閘捉，而且對前端嚟講
   `stale` 仍然係「有數」，會照出個舊數字。

改完之後：源死 → 前端顯示「暫無」→ 日更繼續出街，唔會因為一個評級行冧而全盤停。
源返生 → 自動變返 `ready`，唔使人手解鎖。

---

## 四個源同日集體縮水 — 07-24 → 07-25（新閘首日實測揭發）

*sched-ops agent 追加，2026-07-26。呢節寫嘅時候上面嘅優先次序矩陣（A–S）已經定咗稿，
所以呢一項未入矩陣 —— 下次維護矩陣嘅人請補返入去。*

| | |
|---|---|
| **咩事** | `market_source_observation` 四個源喺同一日（07-24 → 07-25）齊齊跌穿 90%，其中兩個直接歸零 |
| **點揭發** | 今日新加嘅 `volume_floor` 閘首次跑就 fail。**其餘五個 check 全綠** —— 呢個正正係「靜靜地少咗嘢」嘅教科書例子 |
| **量度指令** | `python -X utf8 scripts/verify_daily_run.py --no-alert`（2026-07-26 實測） |
| **阻唔阻上線** | 🟡 未定 —— 未查根因，唔知係真斷更定係 source_code 改名 |

實測數字（07-24 基準 → 07-25 實際）：

| source_code | 07-24 | 07-25 | 剩返 |
|---|---:|---:|---:|
| `gemrate` | 3779 | 3007 | 80% |
| `snk_psa10` | 502 | 281 | 56% |
| `snkrdunk` | 142 | **0** | 0% |
| `ebay` | 66 | **0** | 0% |
| `g10_analytics` | 0 | 1277 | 新出現 |

**未查證嘅嘢（唔准當結論）：**

1. `snkrdunk` 同 `ebay` 歸零，同時 `g10_analytics` 由零變 1277 —— 睇落似 source_code 改名／來源重組，但**冇證據**，未查過 writer 側改咗乜。
2. 如果係真斷更，`ebay` 完全冇被現有 `source_coverage` 覆蓋（佢唔喺 `REQUIRED_SOURCES` 亦唔喺 `ANY_OF_SOURCES`），所以佢可以永遠歸零而冇人知。
3. `gemrate` 3779 → 3007 可能同 GemRate key 每日 1000 配額有關（見上面 §GemRate 相關章節），亦可能係 roster 縮咗。未對過。

**對新閘嘅影響（要知）：** `volume_floor` 而家喺真實 DB 上係 **fail** 狀態。
呢個 fail 未經人手裁決過係 true positive 定係要調參數：

- 如果係真斷更 → 閘做啱嘢，要修數據唔係修閘。
- 如果係 source_code 改名 → 要喺 `scripts/verify_daily_run.py` 加一個「已退役 source」名單，
  否則舊名嘅 baseline 會令個閘永遠見紅，跟住就冇人再信佢（alert fatigue，比冇閘更差）。

**閾值本身亦未經歷史數據驗證。** `VOLUME_FLOOR_RATIO = 0.9` 係按指示直接寫死，
冇攞過歷史逐日行數分佈算過正常波幅。`data/runtime/verify/source_volume.json`
（30 日滾動流水賬）由今日開始累積，一個月之後先夠數據調返個閾值。
在此之前 0.9 係一個**未量過嘅假設**，唔係實測結論。

---

# 缺口登記（C–S）— 數據審計 agent

## ID 索引

| ID | 缺口 | 喺邊 |
|---|---|---|
| **A** | TAG DB 觀測凍喺 2026-07-22（延時炸彈） | 上面 §「TAG — DB 現存觀測凍喺 2026-07-22」（TAG agent 寫） |
| **B** | TAG 永遠冇 per-card POP 歷史（源頭限制） | 上面 §「TAG — 冇 per-card POP 歷史」（TAG agent 寫） |
| **C** | POP 變動上游三層斷 | 下面 |
| **D** | POP delta 覆蓋率薄 | 下面 |
| **E** | 12 張排名卡出唔到街 | 下面 |
| **F** | 卡圖 QC 孤兒 331 / catalog 1,617 張冇圖 | 下面 |
| **G** | DB 卡圖 QC 閘從未通電（0 張 `public_allowed`） | 下面 |
| **H** | 身分裁決佇列 203 條全 pending | 下面 |
| **I** | catalog 身分重複（20 組 identity / 288 組 printing_key） | 下面 |
| **J** | 隔離／拒收積壓 23,938 / 16,530 | 下面 |
| **K** | 19 張排名卡缺四語故事（源頭缺英文原文） | 下面 |
| **L** | 卡名／故事錯配污染 | 下面 |
| **M** | 價格覆蓋率 catalog 23.2% | 下面 |
| **N** | roster 檔案分裂（600 檔 226 個唔喺 DB） | 下面 |
| **O** | GemRate 歷史檔只有 701 / 2,978 | 下面 |
| **P** | FX 只有 10 行單日單點 | 下面 |
| **Q** | 六張表 0 行 | 下面 |
| **R** | 文檔數字腐爛 | 下面 |
| **S** | snapshot 自報 6 個 blocker + `mode:"demo"` | 下面 |
| **T** | 四個源同日集體縮水（07-24 → 07-25） | 上面 §「四個源同日集體縮水」（sched-ops agent 寫，根因未查） |

## 量度基準（本節 C–S 全部適用）

- **量度時間**：2026-07-26，DB `NOW()` **08:46 → 09:05 UTC**（本機 17:46 → 18:05 JST）。
  ⚠ **DB 時鐘比本機慢 9 個鐘**，睇 timestamp 唔好撈亂。
- **量法**：全部經 `scripts/ro_sql.py`（唯讀閘）行 exact `COUNT(*)`，
  **冇用過 `information_schema.TABLE_ROWS`**（估算值，喺呢個庫會俾出相反結論）。
  涉及 producer 邏輯嗰幾條，用 read-only replay（載入 `canonical_public_snapshot` 嘅真 function，
  **冇寫過任何 `data/public/` 檔案**）。
- **每條缺口下面嘅 SQL 可以直接重跑**，唔使揾返 `temp/` 嘅一次性腳本。

```bash
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py "<下面嘅 SQL>"
```

- **C、D 兩條唔係我量嘅** —— 量度日期 2026-07-26 / 來源：**POP delta agent 實測**。

---

## C — POP 變動上游三層斷（`population_change_*` 永遠計唔出）

- **登記日**：2026-07-26 ｜ **來源：POP delta agent 實測**（我冇重量）
- **狀態**：三層都要修，拆任何一層都唔夠

### 缺乜
`market_candidate_daily_snapshot` 嘅 POP 變動欄由頭到尾冇一個值。

### 實測數量

| 量度 | 數 |
|---|---|
| `market_candidate_daily_snapshot` 總行 | **3,432** |
| `population_change_7d_pct` 有值 | **0**（3,432 / 3,432 全 NULL） |
| `population_change_30d_pct` 有值 | **0**（3,432 / 3,432 全 NULL） |
| `population_change_1d_pct` | **欄根本唔存在** |

> ⚠ 精確講法係 **100% NULL**，唔係「全 0」。NULL 同 0 喺 SQL 同前端行為唔同，唔好混。

### 量度時間 + 方法
量度日期 2026-07-26 / 來源：POP delta agent 實測。本 agent 獨立覆核咗 NULL 比例：

```sql
SELECT COUNT(*) total,
       SUM(population_change_7d_pct IS NULL) null_7d,
       SUM(population_change_30d_pct IS NULL) null_30d
FROM market_candidate_daily_snapshot;
```
（`population_change_1d_pct` 唔存在 → `DESC market_candidate_daily_snapshot` 驗）

### 影響
**只影響內部。** 訪客見唔到 alert。但係呢個係**危險嘅假安全網**：
`market_alert_evaluation` 睇落有喺度跑，實際上 POP 異動警報**由開機到今日一次都冇可能響過**。
以為有監測，其實冇。

### 根因（三層，逐層都足以獨力殺死佢）
1. `pipelines/market_alerts.py` 嘅 `prior_population(7)` 用 `max_gap_days=3`：
   由 07-25 搵 07-18 錨點，實際搜 07-15..07-18，而 DB **最早 POP 觀測係 07-21** → **永遠 return None**。
2. `population_change_1d_pct` 呢條欄**從未 model 過** —— 1D POP delta 由設計上就唔存在。
3. `market_rows()` 個 SELECT **由來冇 select 過**嗰兩條欄，就算計到都 join 唔到 producer。

### 補救路徑
**要新開發**（第 2 層要 schema migration）。
但 producer 側 2026-07-26 已改成**直接由 `population_series()` 計**，唔再經
`market_candidate_daily_snapshot` —— 即係話呢三層而家係**孤兒鏈**，
修佢係為咗 alert 層，唔係為咗前端。**修之前先決定 alert 層仲要唔要。**

### 阻唔阻上線
⚪ **唔阻。** 純內部。但唔好當佢「有監測」。

---

## D — POP delta 覆蓋率薄（243 張出版卡）

- **登記日**：2026-07-26 ｜ **來源：POP delta agent 實測**（我冇重量）
- **狀態**：1d 係採集節奏問題（修得好）；7d/30d 係歷史檔覆蓋問題（見 **O**）；TAG 係源頭死症（見 **B**）

### 實測數量（分母 = 243 張已出版卡）

| 窗口 | PSA | BGS | CGC | SGC | TAG |
|---|---:|---:|---:|---:|---:|
| **1d** | **66** | 0 | 0 | 0 | **0** |
| **7d** | **67** | 59 | 36 | 3 | **0** |
| **30d** | **119** | 102 | 62 | 6 | **0** |

- **負 delta 數量：0**（存量指標規則守住咗 —— POP 只升唔跌，`anchor > current` 一律出 `unavailable`）

### 量度時間 + 方法
量度日期 2026-07-26 / 來源：POP delta agent 實測。

本 agent 獨立覆核咗 **1d = 66 嘅成因**（呢個係我自己量嘅，同上）：

```sql
SELECT o.observed_date, COUNT(DISTINCT o.variant_id) cards
FROM market_grader_population_observation o
JOIN market_index_constituent c ON c.variant_id = o.variant_id
WHERE o.grader_code = 'PSA' AND c.generation_id = 19
GROUP BY o.observed_date ORDER BY o.observed_date;
```

結果：**07-21 = 255 · 07-24 = 254 · 07-25 = 71**。
**冇 07-22、冇 07-23。** 即係相鄰兩日成對嘅只有 07-24/07-25 嗰 71 張 → 1d 上限就係嗰度嚟。

### 影響
🔴 **訪客見到。** 排行榜大部分卡嘅 POP 升跌欄空白。
（唔准攞價格 changePct 頂替 —— 呢個係 CLAUDE.md「數據語義」硬規矩。）

### 根因
- **1d**：唔係時間鎖，係**採集節奏斷咗兩日**（07-22 / 07-23 冇跑）。每日 run 補齊 → 1d 即刻 255/255。
- **7d / 30d**：受 `history_full.json` 覆蓋率封頂 → 見 **O**。
- **TAG 全 0**：源頭冇歷史 → 見 **B**，修唔好。

### 補救路徑
**有現成**：`pipelines/gemrate_source.py daily` 已存在，跑返就得。歸 **① GemRate 抓數批**。

### 阻唔阻上線
🟡 **唔阻硬上線**，但係 `grader_supply_universe_pending` blocker 嘅一部分（見 **S**）。

---

## E — 12 張排名卡出唔到街

- **登記日**：2026-07-26 ｜ **狀態**：全部 12 張已分類，冇「未知」剩低

### 缺乜
今日快照 255 張排名卡，只有 **243 張**出到街，**12 張被 producer skip**。

### 實測數量

| | top300 view | top100 view |
|---|---:|---:|
| ranked | **255** | 100 |
| published | **243** | 94 |
| skipped | **12** | 6 |

出版分層：`pack_id` **195** + `new_from_catalog` **48** = 243。
skip 原因：`image_unavailable` **3** + `ambiguous_printing_key` **9**。

**逐張分類（12/12，冇剩）：**

| skip 原因 | variant_id | tcg/lang | 卡 | collector | `catalog_key_n` | 有本機圖 | 過咗 QC | 有英文原文 |
|---|---:|---|---|---|---:|:---:|:---:|:---:|
| `ambiguous_printing_key` | 19 | one-piece/en | Monkey D. Luffy | ST01-012 | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 24 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 60 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 78 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 128 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 132 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 134 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 188 | one-piece/en | — | — | 2–9 | ✅ | ❌ | ❌ |
| `ambiguous_printing_key` | 192 | one-piece/en | Sanji | OP06-119 | 2–9 | ✅ | ❌ | ❌ |
| `image_unavailable` | 7 | pokemon/ja | Poncho 系列 | 207/XY-P | 1 | ✅ | ❌ | ✅ |
| `image_unavailable` | 35 | pokemon/ja | Pikachu | 153/SV-P | 1 | ✅ | ❌ | ✅ |
| `image_unavailable` | 163 | one-piece/en | Monkey D. Luffy | ST21-014 | 1 | ✅ | ❌ | ❌ |

> **12 張全部都有本機圖（`market_image_asset` 各 1 行），12 張全部冇入過 QC manifest。**
> 即係話「冇圖」呢個講法係錯嘅 —— 係**圖喺度但未過 QC**。

### 量度時間 + 方法
2026-07-26 08:52 UTC。Read-only replay，載入 producer 真 function，**冇寫過 `data/public/`**：

```python
gen = cps.latest_generation(conn, cps.presentation_view_min_coverage("top300"))
rows = cps.market_rows(conn, gen, required_count=cps.presentation_view_limit("top300"))
resolved, skipped, tiers = cps.resolve_presentation_entries(
    rows, cps.load_presentation(ROOT/"data/public/presentation-pack.json")[1],
    cps.load_public_images(), cps.catalog_printing_key_counts(conn))
```
（generation id **19** / `effective_at` **2026-07-25**）

### 影響
🔴 **訪客見到** —— 排行榜少咗 12 張，而且係靜靜咁少（冇任何 UI 提示）。

### 根因（兩類，唔好當一件事）
- **3 張 `image_unavailable`**：`printing_key` 唯一（`catalog_key_n=1`），純粹係**圖未過 QC** → 屬 **F/G** 同一條鏈。
- **9 張 `ambiguous_printing_key`**：`printing_key = (tcg, language, collector_normalized)` 撞到 2–9 個 catalog variant，
  producer **fail-closed 唔敢亂揀** → 屬 **I**（身分重複）。**呢 9 張補幾多圖都出唔到街。**

### 補救路徑
- 3 張 → **有現成**（跑 QC 鏈）→ **② 卡圖 QC 批**
- 9 張 → **要人手裁決**（拆 printing_key 撞）→ **③ 人手裁決批**

### 阻唔阻上線
🟡 **唔阻**（243 張照出街），但係 `image_semantic_qc_review_pending` + `canonical_identity_review_pending` 兩個 blocker 嘅可見證據。

---

## F — 卡圖 QC 孤兒 331 張 / catalog 1,617 張冇圖

- **登記日**：2026-07-26 ｜ **狀態**：`set_name` 漂移造成，會再漂

### 實測數量

| 量度 | 數 |
|---|---:|
| QC manifest 過咗閘嘅 `raw_front` 記錄（檔案真係喺 disk） | **419** |
| 准用 sha 總數 | **623** |
| `catalog_variant` 總數 | **1,705** |
| QC public id **對得返** 現行 catalog `opaque_id` | **88** |
| QC public id **孤兒**（對唔返任何現行 variant） | **331**（419 中嘅 79%） |
| catalog variant **冇 QC 圖** | **1,617**（1,705 中嘅 94.8%） |

### 量度時間 + 方法
2026-07-26 08:58 UTC。用 producer 真 loader `cps.load_public_images()`（會驗 `publicAllowed` +
`imageKind=='raw_front'` + sha 64 字 + 有寬高 + `data/public/market-assets/<sha>.webp` 真係存在），
再同 `SELECT opaque_id FROM catalog_variant` 做 set 交集。

### 影響
🔴 **訪客見到。** 出街嗰 243 張入面 4 張冇圖；擴充到全 catalog 就係 1,617 張冇圖。

### 根因
`opaque_id = sha256(tcg, language, set_name, collector, name)` —— **`set_name` 一改，id 就漂**。
QC manifest 用漂得郁嘅 `opaque_id` 做 key，catalog 改咗套名之後 331 張 QC 過咗嘅圖就對唔返身。
**圖冇壞、QC 冇白做，係 key 揀錯。**
（詳見 `docs/evidence/2026-07-26-image-coverage/FINDING.md` —— 207 對漂移全部 `pack_set != db_set` 而 `repro_ok:true`。）

### 補救路徑
**要新開發**：QC manifest 要改用**唔漂嘅 `variant_id`** 做 key（同 `catalog_variant_locale` 一樣嘅修法），
讀嗰刻先 join 出當日 `opaque_id`。**唔改 keying 就重跑幾多次 QC 都會再漂一次。**
歸 **② 卡圖 QC 批**，同 **G** 一齊做。

### 阻唔阻上線
🟡 **唔阻今日**（243 張有 239 張有圖），但係 `image_semantic_qc_review_pending` 嘅本體。

---

## G — DB 卡圖 QC 閘從未通電（0 張 `public_allowed`）

- **登記日**：2026-07-26 ｜ **狀態**：表有、行有、閘從來冇開過

### 實測數量

| 量度 | 數 |
|---|---:|
| `market_image_asset` 行 | **456** |
| `market_image_qc` 行 | **456** |
| `market_image_source_pointer` 行 | **468** |
| 有 asset 嘅 distinct variant | **453** |
| 有 QC 行嘅 distinct variant | **453** |
| **`public_allowed = 1` 嘅 variant** | **0** |

### 量度時間 + 方法
2026-07-26 08:50 UTC。

```sql
SELECT COUNT(*) qc_rows,
       COUNT(DISTINCT variant_id) variants,
       SUM(public_allowed = 1) allowed
FROM market_image_qc;
```

### 影響
🔴 **訪客見到（但係要等 DB cutover 之後先爆）。**
網站而家啲圖**完全靠** `manifests/image-qc.json` + `data/public/market-assets/` 呢條 **檔案路徑**，
**由頭到尾冇經過 DB 嘅 QC 閘**。即係話 `production_database_cutover_pending` 一旦解除、
producer 轉去讀 DB QC，**所有卡即刻冇圖**。

### 根因
**從未接線。** DB 側 QC 表寫咗 456 行判定結果，但冇任何一個 producer 讀 `public_allowed`，
亦冇任何一段 code 將 manifest 嘅判定寫返 DB。兩套 QC 平行存在，只有檔案嗰套通電。
**典型「資料喺曬度但係冇接線」。**

### 補救路徑
**要新開發**：揀一套做正源（建議 DB，因為 cutover 遲早要做），另一套降做 dump。
歸 **② 卡圖 QC 批** + **⑤ 接線批**。

### 阻唔阻上線
🔴 **阻 DB cutover。** 唔阻今日出街（今日行檔案路徑）。
**呢個係最容易被誤判做「已解決」嘅一條** —— 睇網站有圖就以為 QC 通咗。

---

## H — 身分裁決佇列 203 條全部 pending

- **登記日**：2026-07-26 ｜ **狀態**：入咗隊，冇人裁過一條

### 實測數量

`market_identity_review_queue` = **203 行，`status` 全部 `pending`（203/203）**。

| 原因 | snkrdunk | ebay | 小計 |
|---|---:|---:|---:|
| `g10_collector_number_mismatch` | 65 | 27 | **92** |
| `g10_name_not_in_catalog` | 63 | 11 | **74** |
| `g10_variant_duplicate_conflict` | 2 | 15 | **17** |
| `g10_collector_unparsed` | — | — | **11** |
| `g10_no_asset_info` | — | — | **4** |
| `g10_language_unmapped` | — | — | **3** |
| `g10_ambiguous_candidates` | — | — | **2** |
| **總計** | | | **203** |

### 量度時間 + 方法
2026-07-26 08:49 UTC。

```sql
SELECT status, reason_code, source_code, COUNT(*) n
FROM market_identity_review_queue
GROUP BY status, reason_code, source_code ORDER BY n DESC;
```

> ⚠ **`docs/DB_INVENTORY_20260726.md` 話呢張表 0 行 —— 係假嘅。** 見 **R**。

### 影響
⚪ **只影響內部**（訪客見唔到 queue），但**間接影響訪客**：呢 203 條就係「同一張卡對唔到身」嘅積壓，
唔裁就一路有卡對唔到價／POP／圖。

### 根因
**判定層通咗電，但冇人跟進。** 唔係「冇人判」，係「判咗冇人睇」。
全部 203 條屬 G10 家族，即係 G10 baseline 對數嗰陣撞出嚟嘅身分爭議。

### 補救路徑
**有現成**（queue 已存在，要嘅係人眼）。歸 **③ 人手裁決批** —— 同 **I**、**E** 嘅 9 張、**L** 一次過睇。

### 阻唔阻上線
🔴 **阻。** 直接對應 `canonical_identity_review_pending` blocker。

---

## I — catalog 身分重複

- **登記日**：2026-07-26 ｜ **狀態**：收斂表設計好咗但 0 行

### 實測數量

| 重複維度 | 組 | 涉及行 |
|---|---:|---:|
| 完整身分 `(tcg, language, canonical_name, set_name, collector_number)` | **20** | **41** |
| **`printing_key` `(tcg, language, collector_number)`** | **288** | **967** |

967 / 1,705 = **56.7%** 嘅 catalog variant 個 printing_key 唔唯一。

### 量度時間 + 方法
2026-07-26 08:55 UTC。

```sql
SELECT COUNT(*) grp, SUM(n) rows_involved FROM (
  SELECT COUNT(*) n FROM catalog_variant
  GROUP BY tcg_code, card_language, collector_number HAVING COUNT(*) > 1
) t;
```

### 影響
🔴 **訪客見到** —— 呢個就係 **E** 嗰 9 張消失卡嘅直接根因。
producer 見到 `printing_key` 撞就 fail-closed 唔敢揀，張卡靜靜咁唔見咗。

### 根因
`catalog_printing_identity` **設計上就係做收斂呢件事，但係 0 行**（見 **Q**）。
上游 5 個源各有各 `normalize_collector`（**3 份實作散落 11 個檔**），
normalizer 唔一致 → 同一張卡分裂成多個 variant_id。

### 補救路徑
**要新開發**：
1. 共用 `pipelines/normalize.py`（消除 3 份 `normalize_collector`）
2. `catalog_printing_identity` 通電做收斂

⚠ **唔准直接改 DB 嘅 `canonical_name` / `collector_number` 嚟「整靚」** ——
會換 `opaque_id`、孤立價格同 POP 史、撞 `db_runtime.py:640` 嘅 fail-closed assert。
歸 **③ 人手裁決批**（先裁）+ **⑤ 接線批**（後收斂）。

### 阻唔阻上線
🔴 **阻。** `canonical_identity_review_pending`。

---

## J — 隔離／拒收積壓（⚠ 官方基線數字係**發大咗**嘅）

- **登記日**：2026-07-26 ｜ **狀態**：冇人開過嚟睇

### 實測數量 —— **兩個數，唔好引錯個**

| | quarantined | rejected |
|---|---:|---:|
| **原始 `SUM()` over 94 行 `market_ingest_run`** | 70,474 | 32,738 |
| **去重後（真實）** | **23,938** | **16,530** |

**點解會差咁遠**：94 行 run 記錄，按 `(source_code, effective_at, payload_sha256, manifest_sha256)`
去重之後**只剩 33 個唯一簽名**。同一次 ingest 嘅結果被重複寫入多次：
- `g10_analytics` 23,131 條隔離**寫咗 3 次** → 直接 `SUM()` 出 69,393
- `snkrdunk` 9,595 條拒收寫咗 2 次
- `snk_grade` 6,613 條拒收寫咗 2 次

**去重後逐源：**

| source | quarantined | rejected |
|---|---:|---:|
| `g10_analytics` | 23,131 | — |
| `g10_identity` | 504 | 12 |
| `g10_research` | 288 | — |
| `g10_variant_seed` | 14 | — |
| `g10_asset` | 1 | 310 |
| `snkrdunk` | — | 9,595 |
| `snk_grade` | — | 6,613 |
| **合計** | **23,938** | **16,530** |

### 量度時間 + 方法
2026-07-26 09:00 UTC。

```sql
SELECT SUM(quarantined_count) q, SUM(rejected_count) r FROM (
  SELECT DISTINCT source_code, effective_at, payload_sha256, manifest_sha256,
         quarantined_count, rejected_count
  FROM market_ingest_run
) t;
```

> ⚠ **CLAUDE.md 引用緊嘅 70,474 / 32,738 係未去重嘅數。** 唔係錯得好緊要（量級啱），
> 但**唔好攞嚟做覆蓋率分母**，會低估咗接近 3 倍。

### 影響
⚪ **只影響內部。** 但係「靜靜地少咗嘢」—— 2.4 萬條數據被隔離，冇人知入面有冇本來應該收嘅。

### 根因
**判定層通咗電（好事），但冇 review 出口。** 冇任何 UI／腳本可以開 quarantine 嚟睇。
另外 `market_ingest_run` 有重複寫入問題（同一簽名寫 2–3 次），呢個本身都係一個細 bug。

### 補救路徑
**有現成**（payload 全部喺 `market_source_observation.payload_json`，用 `ro_sql.py` 就 sample 到）。
歸 **⑦ 治理批**。第一步係抽樣 100 條睇下係咪真係應該隔離。

### 阻唔阻上線
⚪ **唔阻。**

---

## K — 19 張排名卡缺四語故事（源頭缺英文原文）

- **登記日**：2026-07-26 ｜ **狀態**：唔係翻譯問題，係英文原文根本冇

### 實測數量

| locale | 255 張排名卡有故事嘅 |
|---|---:|
| `en` | **236** |
| `ja` | **236** |
| `zhTW` | **236** |
| `zhCN` | **236** |

**四語數字一模一樣 → 缺口係同一批 19 張。** 冇英文原文 ⇒ 冇嘢可以譯。

已捕捉完整 19 張清單（rank / variant_id / opaque_id / 卡名 / 套名 / collector），例如：
rank 18 vid 19 Monkey D. Luffy ST01-012 · rank 36 vid 34 Mew/Mewtwo Gx SM191 ·
rank 39 vid 39 Giratina Vstar GG69 · rank 151 vid 192 Sanji OP06-119。

### 量度時間 + 方法
2026-07-26 08:57 UTC。

```sql
SELECT l.locale_code, COUNT(DISTINCT l.variant_id) n
FROM catalog_variant_locale l
JOIN market_index_constituent c ON c.variant_id = l.variant_id AND c.generation_id = 19
WHERE l.market_story IS NOT NULL AND CHAR_LENGTH(TRIM(l.market_story)) > 0
GROUP BY l.locale_code;
```

### 影響
🔴 **訪客見到**，但**只喺詳情頁**（`listCard()` 會削走 `story`，列表視圖唔受影響）。
另外 `validate.ts:283-287` 嘅故事閘**只查 top100** → 呢 19 張入面唔喺 top100 嗰啲唔會觸發 error。

### 根因
`pipelines/g10_research_ingest.py` 覆蓋率唔齊 —— G10 側本身冇呢 19 張嘅研究文。
**唔係翻譯 pipeline 問題**（`editorial_translate_queue.py` 只收「DB 已經有英文原文」嘅卡，設計正確）。

### 補救路徑
**有現成**（跑返 `g10_research_ingest.py` 擴覆蓋，再入翻譯隊列）。歸 **④ 編輯批**。
⚠ 譯名唔准機器直譯 —— 跟 CLAUDE.md「卡名中文化」規矩，批量完要用戶過目。

### 阻唔阻上線
🟡 對應 `four_locale_editorial_review_pending` blocker，但**唔係死線嘢**（top100 已經 100/100 四語齊）。

---

## L — 卡名／故事錯配污染

- **登記日**：2026-07-26 ｜ **狀態**：至少 2 張已確認，未做全量掃

### 實測（逐張核過）

| variant_id | `canonical_name` | `set_name` | `collector_number` | 問題 |
|---:|---|---|---|---|
| **390** | `Mewtwo` | `Pokemon TCG Classic: Blastoise` | `014/032` | **四語故事全部講緊 Blastoise 14/32**（en 2,371 字 · ja 316 · zhCN 221 · zhTW 229）。卡名同故事**講兩張唔同嘅卡**。 |
| **102** | `Shanks SEC-SP Booster Pack ROMANCE DAWN` | `Comic Parallel` | `OP01-120` | 卡名欄**溝咗 booster pack 宣傳文字**入去。 |

### 量度時間 + 方法
2026-07-26 08:59 UTC。逐條讀 `catalog_variant` + `catalog_variant_locale` 對比：

```sql
SELECT v.id, v.canonical_name, v.set_name, v.collector_number,
       l.locale_code, CHAR_LENGTH(l.market_story) story_len, LEFT(l.market_story, 120) head
FROM catalog_variant v LEFT JOIN catalog_variant_locale l ON l.variant_id = v.id
WHERE v.id IN (390, 102);
```

### 影響
🔴 **訪客見到** —— 詳情頁卡名寫 Mewtwo、故事全篇講 Blastoise。呢個係**完整性 bug 唔係美觀問題**，
係最傷信任嗰種。

### 根因
上游 `set_name` / `canonical_name` 收數收錯，加上冇 name↔story 一致性檢查。
**未做全量掃** —— 呢兩張係抽查撞到嘅，實際數量未知。

### 補救路徑
**有現成，但一定要行 display 層**：改 `apps/web/src/lib/card-names.ts`。
⚠ **唔准改 DB 嘅 `canonical_name`** —— 會換 `opaque_id`、孤立歷史（見 **I** 同一條硬規矩）。
歸 **③ 人手裁決批**。**第一步應該係寫個 name↔story 一致性掃描，量返實際有幾多張。**

### 阻唔阻上線
🟡 **唔阻**，但係出街之後最容易俾人截圖嘅一種錯。

---

## M — 價格覆蓋率：catalog 23.2%

- **登記日**：2026-07-26 ｜ **狀態**：兩個分母講法唔同，唔好混

### 實測數量

| 分母 | 有價 | 覆蓋率 |
|---|---:|---:|
| **今日排名卡 255 張** | **255** | **100%** ✅ |
| roster 1,468 張 | 395 | 26.9% |
| **catalog 1,705 張** | **395** | **23.2%** |

**逐源（`market_price_observation` 共 119,266 行）：**

| source | 行 | 卡 | 日期範圍 / 日數 |
|---|---:|---:|---|
| `snk_psa10` | **115,036** | 336 | 2023-06-19 → 2026-07-25（真 3 年） |
| `ebay` | **3,824** | 343 | **92 個唔同日期** |
| `snkrdunk` | 406 | 336 | 2026-07-19 起 |

**成交（`market_sale_observation` 共 93,063 行）—— 呢張表同上面嗰張唔同，唔好混：**

| source | 行 | 卡 | 日數 |
|---|---:|---:|---:|
| `snkrdunk` | 65,502 | 350 | 1,025 |
| `snk_grade` | 14,546 | 347 | 1,025 |
| **`ebay`** | **13,015** | **344** | **93**（2026-04-24 → 07-25） |

排名卡有真 eBay 成交：**222 / 255（87.1%）**。255 張**全部**至少有一個源嘅成交。

### 量度時間 + 方法
2026-07-26 08:47 UTC。

```sql
SELECT source_code, COUNT(*) rows_n, COUNT(DISTINCT variant_id) cards,
       COUNT(DISTINCT observed_date) days, MIN(observed_date) d0, MAX(observed_date) d1
FROM market_price_observation GROUP BY source_code;
```

### 影響
⚪ **只影響內部 / 擴充上限。** 訪客今日只見到 255 張榜卡，**全部有價**。
1,310 張冇價嘅卡根本入唔到榜，所以訪客見唔到窿。

### 根因
07-23 之後新入 catalog 嗰批（1,705 − 395）**未有過任何一行價**。純採集量問題。

### 補救路徑
**要新開發／新源**（擴採集覆蓋）。歸 **⑥ 要新源批**。
⚠ 「硬上限 18.6%」呢個講法**已作廢** —— 舊算法當 eBay 得 92 行（92 係**日數**唔係行數）。
上限唔係結構性嘅，加得幾多源就升幾多。

### 阻唔阻上線
⚪ **唔阻。** 但係 `price_reference_over_48h` blocker 嘅背景（見 **S**）。

---

## N — roster 檔案分裂

- **登記日**：2026-07-26 ｜ **狀態**：唔係窿，係「三代候選池未收編」

### 實測數量

| 檔 | 行 | 喺 DB | 唔喺 DB |
|---|---:|---:|---:|
| **`tracked-gemrate-ids.txt`**（現行 roster） | **1,468** | **1,468** | **0** ✅ |
| `gemrate-ids.txt` | 600 | 374 | **226** |
| `active-gemrate-ids.txt` | 400 | — | — |

DB 有 **1,496** 個 gemrate 身分（對應 1,495 個 variant），其中 **28 個唔喺 tracked roster**。

### 量度時間 + 方法
2026-07-26 08:58 UTC。**用 Python `line.strip()` 做比對，唔用 `comm`／`grep -f`** ——
`tracked-gemrate-ids.txt` 係 **CRLF**，另外兩個係 **LF**，用 shell 工具會報「零重疊」呢種假答案。

```python
{ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()}
```
DB 側：`SELECT external_entity_id, variant_id FROM catalog_source_identity WHERE source_code='gemrate'`

### 影響
⚪ **只影響內部。** 現行 roster（1,468）**DB 100% 有對應，一個窿都冇**。

### 根因
`gemrate-ids.txt` 係 **2026-07-23 02:11 嘅舊產物**，冇任何 pipeline 讀。
226 個「唔喺 DB」係**唔同世代嘅候選池未收編**，唔係 daily roster 有窿。

### 補救路徑
**有現成**（一個決定：收編定刪）。歸 **⑦ 治理批**。

### 阻唔阻上線
⚪ **唔阻。** 登記落嚟係為咗防止下一個 agent 見到「226 個唔喺 DB」就以為 roster 爛咗。

---

## O — GemRate 歷史檔只有 701 / 2,978

- **登記日**：2026-07-26 ｜ **狀態**：呢個先係 7d/30d POP delta 嘅真正天花板

### 實測數量

| 量度 | 數 |
|---|---:|
| `data/private/gemrate/cards/` 目錄數 | **2,978** |
| 入面有 `history_full.json` 嘅 | **701**（23.5%） |
| tracked roster 1,468 入面有歷史檔嘅 | **598**（40.7%） |
| DB gemrate 身分 1,496 入面有歷史檔嘅 | **598** |

歷史檔內容：**157 個週線點，2023-07-29 → 2026-07-25**（~3 年），
逐點有 `grades.{psa_10, beckett_10_pristine, sgc_10_pristine, cgc_10_perfect}`。
**冇 TAG**（見 **B**）。

### 量度時間 + 方法
2026-07-26 08:58 UTC。

```python
{p.parent.name for p in (ROOT/"data/private/gemrate/cards").glob("*/history_full.json")}
```

### 影響
🔴 **訪客見到** —— 直接封住 **D** 嘅 7d/30d 覆蓋率上限。

### ⚠ 順手作廢一個錯誤時間鎖
**CLAUDE.md 嘅「POP 7D 最早 2026-07-28 / 30D 最早 2026-08-20」已作廢，唔好再引用。**
嗰個推算假設 POP 史由 `market_grader_population_observation` 首行（07-21）起計。
**實測唔係** —— 本機一早有 3 年週線史，7D／30D **今日已經計得到**，
瓶頸係**有幾多張卡有嗰個檔**，唔係時間。

### 根因
`gemrate_source.py:964` `_save(cdir / "history_full.json", hist)` 只喺行過 API dump 嘅卡先寫。
2,277 個目錄係其他 subcommand（搜尋／公開卡頁）造出嚟，冇 full history。
呢個目錄 gitignore（`.gitignore:29`），缺檔唔係錯 —— 冇歷史就退返 DB 每日觀測，窗口報 `accumulating`。

### 補救路徑
**有現成**：`gemrate_source.py api-dump`（凍結掃）。
⚠ **key 有每日 1000 配額**，1,468 張roster **一日結構上掃唔完**，要排窗口。
歸 **① GemRate 抓數批** —— 同 **D** 一次過安排，唔好分兩次燒配額。

### 阻唔阻上線
🟡 **唔阻**，但係 `grader_supply_universe_pending` 嘅本體。

---

## P — FX 只有 10 行單日單點

- **登記日**：2026-07-26 ｜ **狀態**：已接線，但薄到冇容錯

### 實測數量
`market_fx_rate_observation` = **10 行**，**6 個幣種**（CNY / GBP / JPY / KRW / HKD / TWD），
`effective_at` 只有 **2026-07-24 / 07-25 / 07-26** 三日。

### 量度時間 + 方法
2026-07-26 08:46 UTC。

```sql
SELECT COUNT(*) n, COUNT(DISTINCT currency_code) ccy,
       MIN(effective_at) d0, MAX(effective_at) d1
FROM market_fx_rate_observation;
```

> ⚠ `docs/DB_INVENTORY_20260726.md` 話呢張表 0 行 —— **假嘅**，見 **R**。

### 影響
🔴 **訪客見到，而且係最脆嘅一條線。**
`apps/web/src/lib/format.ts:31-33`：**任何一個 FX rate 唔係 finite → 嗰個幣種下所有錢銀欄位變空白**。
唔係 fallback，係整版白。10 行 / 3 日 = **冇任何歷史容錯**，斷一日就有幣種爆。

### 根因
`pipelines/fx_rates.py` 2026-07-26 先接線，之前完全冇跑過。冇 backfill。

### 補救路徑
**有現成**（`fx_rates.py` 已喺 `run_daily.py` 入面）。
建議加返歷史 backfill + 「上一個已知好值」fallback，唔好靠當日單點。

### 阻唔阻上線
🟡 **唔阻**（今日 6 個幣種齊），但**單點失效風險高**，值得喺 AWS 上線前補 fallback。

---

## Q — 六張表 0 行

- **登記日**：2026-07-26 ｜ **狀態**：兩類，唔好一刀切

### 實測數量（34 張 base table 入面，exact `COUNT(*)` 確認 0）

| 表 | 類型 |
|---|---|
| `market_tracked_sales_aggregate` | ✅ **已正式廢棄**（前端已改讀 `market_daily_sales_aggregate`，12,078 行） |
| **`catalog_printing_identity`** | 🔴 **設計上就係做身分收斂，但從未通電** → **I** 嘅解藥 |
| `market_raw_payload_object` | 🟡 原始 payload 獨立歸檔，從未寫過 |
| `market_source_observation_payload_pointer` | 🟡 同上 |
| `market_source_effective_observation` | 🟡 從未寫過 |
| `market_retention_archive_manifest` | 🟡 從未寫過 |

### 量度時間 + 方法
2026-07-26 08:46 UTC。34 條 `SELECT COUNT(*) FROM <table>` 做 `UNION ALL`，
**冇用 `information_schema.TABLE_ROWS`**。

### 影響
⚪ **只影響內部。**
- `catalog_printing_identity` 0 行 = **I** 冇解藥 = **E** 嗰 9 張永遠出唔到街。
- 兩張 payload 表 0 行 = **原始 payload 冇獨立歸檔**，追溯淨係靠 `market_source_observation.payload_json` 本身。

### 根因
**從未接線**（唔係「表設計錯」）。

### 補救路徑
**要新開發。** 歸 **⑤ 接線批**。優先做 `catalog_printing_identity`（佢解 **I** + **E**）。

### 阻唔阻上線
🔴 `catalog_printing_identity` 阻（`production_database_cutover_pending`）；其餘四張唔阻。

---

## R — 文檔數字腐爛（呢個係元缺口）

- **登記日**：2026-07-26 ｜ **狀態**：`docs/DB_INVENTORY_20260726.md` 至少 6 條數字係反嘅

### 實測（文檔講 0 行，實測全部有數）

| 表 | 文檔講 | **實測 exact `COUNT(*)`** |
|---|---:|---:|
| `market_identity_review_queue` | 0 | **203** |
| `market_sale_observation` | 0 | **93,063** |
| `catalog_variant_locale` | 0 | **1,200** |
| `market_fx_rate_observation` | 0 | **10** |
| `market_image_asset` / `_qc` / `_source_pointer` | 0 | **456 / 456 / 468** |
| `catalog_story_pointer` | 0 | **466** |
| `catalog_provider_identity_alias` | 0 | **360** |

其他實測值（供對數）：`catalog_variant` **1,705** · `market_source_observation` **337,052** ·
`market_price_observation` **119,266** · `market_candidate_daily_snapshot` **3,432**。

### 量度時間 + 方法
2026-07-26 08:46 UTC，34 張表一次過 `UNION ALL` 嘅 exact `COUNT(*)`。

### 影響
⚪ **只影響內部 —— 但係破壞力最大嗰種。**
呢個 project 嘅 agent 失效模式**唔係揾錯檔**，係**成功讀到啱嗰份文檔而文檔啲數字爛咗**。
「表 0 行」呢種講法會令下一個 agent 直接跳過整條線唔查。

### 根因
文檔寫嗰陣可能用咗 `information_schema.TABLE_ROWS`（估算值）。
實測佢報 `catalog_variant` 1,590 而 exact `COUNT(*)` 係 **1,705**。

### 補救路徑
**有現成**：CLAUDE.md 已有 `@verified` 驗證戳制度 + `scripts/verify_claims.py`。
**要做嘅係將 `DB_INVENTORY_20260726.md` 嗰批數字補戳**，唔補戳就會再爛一次。
歸 **⑦ 治理批**。

### 阻唔阻上線
⚪ **唔阻**，但**唔修就會令上面 A–Q 全部有機會被下一個 agent 誤判**。

---

## S — snapshot 自報 6 個 blocker + `mode:"demo"`

- **登記日**：2026-07-26 ｜ **狀態**：呢個唔係獨立缺口，係上面各項嘅**總和讀數**

### 實測
`data/public/seed-snapshot.json` 嘅 `generation` 自己寫住：

```
mode: "demo"
productionEligible: false
blockers: [
  canonical_identity_review_pending,
  image_semantic_qc_review_pending,
  grader_supply_universe_pending,
  four_locale_editorial_review_pending,
  production_database_cutover_pending,
  price_reference_over_48h
]
```

### 量度時間 + 方法
2026-07-26 08:44 本機。直接讀 `data/public/seed-snapshot.json` 嘅 `generation` 物件（唯讀）。

### 影響
🔴 **系統自己已經講明「未夠格上生產」。** 呢個係最誠實嘅一個訊號，唔好無視。

### 對應關係
見文件頂部嘅「上線閘對照」表 —— **6 個 blocker 全部喺本文有對應實體缺口，冇一個係憑空。**

### 補救路徑
清晒對應缺口 → blocker 自己會消。**唔准手動改 `mode` / `productionEligible` 去「解鎖」。**

### 阻唔阻上線
🔴 **呢個就係上線閘本身。**

---

## 基線修正（三個之前引用緊嘅數字，實測對唔上）

登記喺度係為咗**防止下一個 agent 繼續引用錯嘅基線**。

| 之前嘅講法 | 實測（2026-07-26） | 點解會錯 |
|---|---|---|
| 「70 張 `image_unavailable`；catalog 由 192 升到 262」 | **255 ranked / 243 published / 12 skipped**（3 `image_unavailable` + 9 `ambiguous_printing_key`） | **262 係 2026-07-24 嗰份 `tcg-combined` 快照（id 13）嘅成分數**，唔係今日。今日係 generation **19**（07-25）= 255。id 13 → 19 之間：10 張跌咗出榜、3 張新入榜 |
| 「隔離 70,474 / 拒收 32,738」 | **23,938 / 16,530**（去重後） | 94 行 `market_ingest_run` 去重後只剩 33 個唯一簽名；`g10_analytics` 同一批寫咗 3 次 |
| 「eBay 3,824 行 / 343 卡 / 92 日」 | 呢個係 **`market_price_observation`** 嘅數，**啱嘅**。但 **`market_sale_observation`** 嘅 eBay 係 **13,015 行 / 344 卡 / 93 日**（2026-04-24 → 07-25） | 兩張唔同嘅表，講「eBay 成交」要指明邊張 |

---

## 給接手嘅人：三句話

1. **唯一有硬日期嘅係文件頂部嗰個 `2026-07-29T00:00:00Z`**（缺口 **A**）。其餘冇死線。
2. **12 張出唔到街嘅卡全部已分類，冇「未知」剩低**（缺口 **E**）—— 3 張補 QC 就得，9 張要人手拆身分撞。
3. **唔好逐條缺口開工。** 睇文件頂部「可以同一批做嘅分組」，7 個批次入面
   **③ 人手裁決批**（**H** + **I** + **E** 嘅 9 張 + **L**）一次過做，可以同時解掉 3 個上線 blocker。

---

## 前端訪客視角實測到嘅資料缺口（2026-07-26 18:15，唯讀 QA 掃描）

來源：`temp/qa_sweep_report.md`。以下每條都係**喺 http://localhost:3800 實際渲染出嚟見到**嘅缺口，唔係由資料庫推論。量度方式：curl 取 SSR HTML 逐 cell 解析 + `/api/v1/*` JSON 統計。

### QA-1. 短窗（1d / 7d）變動率全空 —— 三個時間窗得一個有數

`/api/v1/market`（n=100，top100）：

| 欄位 | 1d | 7d | 30d |
|---|---|---|---|
| `changePct` 空值 | **100/100** | **100/100** | 0/100 |
| `marketCapChangePct` 空值 | **100/100** | **100/100** | 0/100 |
| `trackedSalesChangePct` 空值 | **100/100** | **100/100** | **100/100** |
| `trackedSales.valueUsd` 空值 | 37/100 | 3/100 | 0/100 |

**訪客可見後果**：撳 1d 或 7d，主表格「Chg」欄 100 行全部變 `Accumulating`，同時 Price 同 Mkt Cap 嘅升跌數字一併消失。實測第一行：30d = `$2,993−$168.17 … $148.14M−$2.6M … -5.32%`；1d = `$2,993 … $148.14M … Accumulating`。

**阻唔阻上線**：🔴

---

### QA-2. rank 101–300（watchlist）完全冇 population 升跌

`/api/v1/market?scope=watchlist`（n=200）對比 top100（n=100）：

| 資料集 | PSA `topGradePopulationChangePct` 空值（1d / 7d / 30d） |
|---|---|
| top100（rank 1–100） | 0/100 / 0/100 / 0/100 |
| **watchlist（rank 101–300）** | **200/200 / 200/200 / 200/200** |

連鎖後果：`marketCapChangePct` 要 price delta × pop delta 兩個輸入先砌到，所以 watchlist 30d `marketCapChangePct` 亦係 **200/200 空**（top100 = 0/100）。

**訪客可見後果**：`/watchlist` 200 行全部只有價格有升跌箭嘴，Pop 同 Mkt Cap 淨係一舊靜態數字，同首頁排版唔一致。實測 rank 101：`$2,094 −$183.08 | 4,524 | $9.47M`。

**阻唔阻上線**：🔴

---

### QA-3. BGS / CGC / SGC 冇市值，只有 PSA 有

`/graders/*` 頁面逐 cell 解析（每頁 100 行資料）：

| 頁 | `Mkt Cap` 欄 `—` | `30d change` 欄 `Accumulating` |
|---|---|---|
| `/graders/psa` | 0/100 | **67/100 (67%)** |
| `/graders/bgs` | **100/100 (100%)** | **100/100 (100%)** |
| `/graders/cgc` | **100/100 (100%)** | **100/100 (100%)** |
| `/graders/sgc` | **100/100 (100%)** | **100/100 (100%)** |

頁頂大字：BGS / CGC / SGC 三頁都係 `Market cap unavailable`，只有 PSA 頁係 `PSA 10 market cap`。

**阻唔阻上線**：🔴

---

### QA-4. TAG 完全冇資料，而且 market share widget 喺 TAG 頁壞埋

`/graders/tag` HTTP 200，但 body 只得 34,019 bytes（其他 grader 頁約 970,000 bytes），資料列 **0 行**。

全頁見街原文節錄：

```
Market share — Total  PSA 0.0 %  BGS 0.0 %  CGC 0.0 %  SGC 0.0 %  TAG 0.0 %
Top-grade population  Market cap unavailable
No eligible cards are available in this view.      ← 出現兩次
```

**注意呢個唔淨係「TAG 冇數」**：同一個 market share widget 喺 `/graders/psa` 係正常嘅（`Total 4,321,540 / PSA 87.5% / BGS 2.8% / CGC 9.5% / SGC 0.2% / TAG 0.0%`），但喺 TAG 頁**五個評級全部變 0.0%、Total 變 `—`**。即係 widget 喺「主評級零資料」情況下會整版塌，係一個獨立嘅呈現 bug。

而 grader nav 五個掣（`PSA BGS CGC SGC TAG`）照樣 render，訪客撳得入呢版死頁。

snapshot 層對應：`coverage.graderPopulationReady` = PSA 100 / CGC 100 / BGS 93 / SGC 81 / **TAG 0**。

**阻唔阻上線**：🔴

---

### QA-5. 四語系編輯內容缺口：韓文全缺、set 名全缺

**(a) 韓文故事／set 名／alt 永遠出英文。** 兩層原因：

1. 資料層：`data/editorial/top100-stories.json`（150 條）嘅 locale key 係 `['en','zhTW','zhCN','ja']` —— **冇 `ko`**。`data/public/seed-snapshot.json` 嘅 `names`/`sets`/`stories` 亦係 `en, ja, zhCN, zhTW`，冇 `ko`。
2. 代碼層：`apps/web/src/lib/snapshot.ts:46` 硬寫 `ko: value.en || fallback`。

實測 `/card/cmc_4104320a31c4d989742c067d?lang=ko`：`<h1>` = `반 고흐 피카츄`（韓文，啱），但 `<meta name="description">` = `This promo recasts Pikachu through Van Gogh's Self-Portrait with Grey Felt ...`（英文）。

**(b) set 名五個 locale 全部英文。** `data/editorial/set-names.json` 存在（46,372 bytes，07-26 08:23 改過）但 `apps/web/src/lib/snapshot.ts` **從來冇 import 佢**（只 import 咗 `top100-stories.json`）。即係資料備好咗但冇接落顯示層。

**(c) 故事覆蓋率**：top100 **62/100**、watchlist **29/260**。

**阻唔阻上線**：🟡（(b) 係接線問題，唔使等新資料）

---

### QA-6. 資料時間戳見街，落後 4 日

每版卡片詳情頁可見文字：**`Data time : Jul 22, 2026, 6:48 PM`**（掃描日 2026-07-26）。

snapshot：`generation.mode = "demo"`、`productionEligible = false`、id `daily_20260722T094826496874Z`，6 個 blocker 未清。

**額外部署風險**：`apps/web/src/lib/server-snapshot.ts` 嘅 `loadNodeSnapshot()` 喺 `MARKET_DATA_SNAPSHOT_PATH` 未設時會**靜靜地** fallback 落 `getSeedSnapshot()`（即呢份 demo seed），唔會報錯。上 AWS 前要硬驗證呢個環境變數。

**阻唔阻上線**：🔴（如果 demo 資料出街）

---

### 呢六條同已有缺口嘅關係

QA-1 / QA-2 / QA-3 / QA-4 全部指向**同一個上游根源：population 觀測嘅時間深度同評級覆蓋率不足**——冇連續兩日嘅 POP 觀測就砌唔到 1d/7d delta，冇非 PSA 評級嘅 POP 就砌唔到嗰啲評級嘅市值。QA-5 係編輯內容缺口，同上面獨立。

**建議**：呢四條唔好當四件事做，補上游 population 時間序列一次過解決。

---

## U — GemRate 凍結抓取：184 張卡仲未抓到，**已抓 = 零卡**

- **登記日**：2026-07-26 ｜ **寫者**：gemrate-freeze agent
- **狀態**：🔴 **未抓到任何一張**。抓取排咗一次性任務等 quota 窗口，未執行。
- **關係**：呢節係 **O**（歷史檔只有 701/2,978）嘅**執行面**。O 講「差幾多」，呢節講「點解今日補唔到、幾時再試」。

### 缺乜

| 批 | 張數 | 呼叫數 | 點解要抓 |
|---|---:|---:|---|
| Pass A `temp/freeze_pass_a_missing.txt` | **132** | 264 | 完全冇 `history_full.json` |
| Pass B `temp/freeze_pass_b_biweekly.txt` | **52** | 104 | 舊 client 冇傳 `interval=week`，攞到 14 日間距嘅點，要重抓做週線 |
| **合計** | **184** | **368** | 每張卡 2 個 endpoint（`/population` + `/population/history`） |

兩個檔全部 40-hex，零重疊（已驗）。

### quota 實測模型（**呢個係新事實，之前份簡報寫嘅係推論**）

GemRate 行 **AWS API Gateway usage-plan quota**，唔係普通 rate limit：

```
HTTP 429
x-amzn-ErrorType: LimitExceededException
body: {"message":"Limit Exceeded"}
```

**冇 `Retry-After`、冇任何 rate-limit header** —— API 由頭到尾唔會話你聽幾時開。
所以「幾時 reset」**推唔到**，只可以 poll。好在 usage-plan 打回頭嘅 429 **唔計入 quota**，poll 唔燒額度。

實測證據（2026-07-26）：

| 量度 | 結果 |
|---|---|
| 07-25 20:00Z 寫入成功卡數 | history 438 / population 434 |
| 07-25 21:00Z | 62 / 62 |
| 07-26 02:00Z | population 4，**history 0** |
| 合計 | **500 張 × 2 endpoint = 1000 call**，之後硬 429 |
| 單次探測 `2026-07-26T09:01:34Z` | **HTTP 429**（quota 仍然乾） |

`daily_20260726T023638Z` run manifest `resolvedCount=4` 已推廣，**34 秒後**嘅
`daily_20260726T023712Z` 就 `resolvedCount=0`。即係 **quota 係逐少 aged-out 咁滲返出嚟，
唔係定時一次過 reset**；固定 00:00 UTC 每日重置嘅模型同實測對唔上（UTC 日過咗 9 個鐘只得 4 個 call 過到）。

⇒ 由 07-25 20:32Z 開始燒計，rolling-24h 推算大批額度 **07-26 20:32Z** 之後先返嚟。
**呢個係強證據推論，唔係直接觀測到嘅 reset** —— 所以排程係「到時開始 poll」而唔係「到時跑一次」。

### 已抓幾多

**零卡。** 07-26 全日 quota 都係乾嘅，一張都補唔到。
（上一手 agent 嗰次 Pass B 嘗試 `data/runtime/logs/gemrate_freeze_slow.log` 52 個 429 ladder，
同樣係 **0 成功** —— Pass B 完全未動過。）

### 補救路徑（已裝，未跑）

| | |
|---|---|
| **檔喺邊** | [deploy/windows/gemrate-freeze-oneshot.ps1](../deploy/windows/gemrate-freeze-oneshot.ps1) |
| **邊個寫** | gemrate-freeze agent（2026-07-26） |
| **覆蓋率尺** | [scripts/measure_pop_coverage.py](../scripts/measure_pop_coverage.py) —— 由 `temp/` 歸位（排程唔准依賴 `temp/`，`tests/test_verify_doc_refs.py` 守住）。before/after 兩次都用呢把尺，寫 `temp/pop_coverage_<label>.json`。⚠️ `temp/` 嗰份舊副本冇刪（其他 agent 行緊），新嘢一律用 `scripts/` 嗰份 |
| **邊個讀** | Windows Task Scheduler：`CARDZ-GemRate-Freeze-Oneshot-A`（07-27 05:47 JST）· `CARDZ-GemRate-Freeze-Oneshot-B`（07-28 05:47 JST，backup） |
| **而家有冇人用** | ✅ 兩個任務 `State=Ready`，**驗證時間 2026-07-26 18:16:40 JST**（`Get-ScheduledTask`）。**未執行過，唔好講到似已經抓完。** |

行為：到時間開始 poll（每 10 分鐘一個探測，最多 14 鐘）→ 一 200 就即刻
Pass A（`--resume`）→ Pass B（**故意冇 `--resume`**，否則 52 張全部因為檔已存在被 skip）→
覆蓋率覆核 → 寫 `temp/gemrate_freeze_result.txt` → 自刪任務。
成功會落 marker `data/runtime/gemrate_freeze_complete.json`，B 見到 marker 就即刻收工，唔會嘥 104 個 call。

⚠️ 呢個一次性任務**只做 `api-dump`**（淨係寫 `history_full.json`），
**唔產生 ranking、唔寫 snapshot、唔郁 pointer、唔 publish** ——
所以唔違反 `deploy/windows/install_gemrate_task.ps1` 嗰條「禁止獨立 GemRate 排程」規矩。
**下一個 agent 唔准喺嗰個 script 加 `run_daily.py` / `canonical_public_snapshot.py` / publish / pointer 任何一 call。**

⚠️ 兩個任務都係 `LogonType=Interactive`（無 admin 權限註冊唔到 S4U，實測「存取被拒」）。
即係話 **用戶登出咗就唔會準時跑**，會等到下次登入先補跑（有 `-StartWhenAvailable`）。
想準時跑就要用 admin 重註冊做 S4U。

### 硬期限

**API key `2026-07-29` 死。** 死咗之後 `cmd_api_dump` 硬 require key、`return 2` 即死，
即係 **184 張再冇機會補**。剩返嘅窗口實際上得 07-27 同 07-28 兩次。

### TAG 唔喺呢個範圍

TAG 唔喺 GemRate per-card population API 入面，抓幾多次都唔會有 TAG 歷史 ——
見上面 §「TAG — 冇 per-card POP 歷史（源頭限制）」。
**唔准為 TAG 燒 quota**，224 張 TAG 卡嘅 7d/30d POP delta 係結構性做唔到。

### 順手記低一個死排程

`CARDZ-Freeze-Sweep-Guard` 指住 **`temp\freeze-sweep-guard.ps1`**，07-26 09:20 JST 跑過一次、
`LastTaskResult=1`（撞 quota 死），`NextRunTime` 空 —— 已經係死 one-shot，唔會再跑。
**生產排程唔應該指住 `temp/`**（`temp/` 隨時被清）。呢個任務留喺度冇害，但唔好照抄做 template。

---

## 部署鏈缺口（linux-deploy agent，2026-07-26 實測）

以下每條都係喺 **Docker container / WSL Ubuntu 24.04** 真跑出嚟，唔係靜態讀 code。
每個數字後面標咗量度日期，因為呢個 repo 已經出現過「量度過咗保質期」——
`docs/AWS_DEPLOY.md` §8.3 對住一份之後被還原嘅檔量咗一輪，結論錯咗兩日。

### 1. 冇一份 production-eligible snapshot 存在，所以「真數據出街」全鏈驗唔到

`data/public/seed-snapshot.json`（2026-07-26 量）：

| 欄位 | 值 |
|---|---|
| `generation.id` | `daily_20260722T094826496874Z` |
| `generation.mode` | `demo` |
| `productionEligible` | `False` |
| `blockers` | 6 條（`canonical_identity_review_pending`、`image_semantic_qc_review_pending`、`grader_supply_universe_pending`、`four_locale_editorial_review_pending`、`production_database_cutover_pending`、`price_reference_over_48h`） |

**實測方法**：抄一份出 `temp/`，將 `mode` 撳做 `production`、`productionEligible=true`、清空
`blockers`，再用 `MARKET_DATA_SNAPSHOT_PATH` 掛入 container。結果 **503**，validator 逐張卡報：

```
generation content hash is inconsistent          ← 自己改 JSON 整爛 contentSha256，唔算數
top100[N] identity is not confirmed
top100[N] localization is incomplete
top100[N] stories are not independently localized
top100[N] price exceeds 48h freshness SLA
```

**結論**：載入機制（讀檔 → validate → normalise → serve）**冇問題**，
擋住嘅係**數據本身未夠 production 資格**。呢個係上游數據缺口，唔係部署 bug。

**未能驗證**：一份真正 production-eligible snapshot runtime load 得唔得。
要驗就要先有一份，而家冇。**唔准為咗驗呢個而跑
`pipelines/canonical_public_snapshot.py` 唔加 `--output temp/xxx.json`** ——
`--output` 預設值係 `data/public/seed-snapshot.json`（[pipelines/canonical_public_snapshot.py:1087](../pipelines/canonical_public_snapshot.py)），
跑赤條條會直接覆寫 demo seed，就係之前污染嗰次嘅成因。

### 2. `pipelines/requirements.txt` 冇 pytest — 照住文檔裝完個 venv 跑唔到測試

WSL Ubuntu 24.04 實測（2026-07-26），完全照 [docs/SERVER_MIGRATION.md](SERVER_MIGRATION.md) §3 做：

```
pip install -r pipelines/requirements.txt   → exit 0
.venv-backend/bin/python -m pytest tests/   → No module named pytest
```

`pipelines/requirements.txt` 得 8 個 package（Pillow / PyMySQL / cryptography / curl_cffi /
pycryptodome / requests / playwright / zstandard），**冇 pytest、冇 pytest-cov**。
即係接手嗰個人裝完根本冇得自我驗證。

⚠️ 而且 repo 根目錄**冇 `pytest.ini` / `pyproject.toml` / `setup.cfg` / `conftest.py`**
（2026-07-26 `ls` 實測），所以 pytest 嘅 `rootdir` 靠 cwd 猜，`--no-cov` 亦都淨係喺
pytest-cov 裝咗嘅機先識。Windows 上面跑得通純粹因為嗰部機啱啱好裝咗。

**暫定處理**：文檔補咗一句叫人額外 `pip install pytest pytest-cov`。
**冇改 `pipelines/requirements.txt`**（唔喺本 agent 範圍，而且驚同其他 agent 撞）。
正路係將 test 依賴寫入一個 `requirements-dev.txt` 或者加返 pytest config。

### 3. `tests/` 依賴 `integrations/grade10/`，抽走會 collection error

`tests/test_grade10_integration.py` 直接 load `integrations/grade10/run_service.py`。
用一個唔含 `integrations/` 嘅精簡 payload 跑 pytest，會喺 **collection 階段**就死：

```
FileNotFoundError: [Errno 2] No such file or directory: '.../integrations/grade10/run_service.py'
ERROR tests/test_grade10_integration.py
!!!!! Interrupted: 1 error during collection !!!!!
```

一個 error 就會 **interrupt 成個 test session**，其餘測試一條都唔跑。
即係話任何人想精簡部署 payload（例如淨係抄 `pipelines/` + `tests/`）都會中招。
**唔係 repo bug**，係打包時要知嘅耦合。

### 4. 11 個 `.py` 喺 working tree 係 CRLF

2026-07-26 喺 WSL `file(1)` 實測，working tree（唔係 clean clone）：
`pipelines/` 7 個（`run_daily.py`、`canonical_public_snapshot.py`、`market_source_sync.py`、
`ranking_derivation.py`、`active_universe.py`、`g10_public_snapshot.py`、`db_runtime.py`）、
`tests/` 4 個，其中 3 個係 **CRLF 同 LF 混住**。

**影響有限**：Python 兩種都食，而且 [.gitattributes](../.gitattributes) 有 `*.py text eol=lf`，
clean clone checkout 出嚟會係 LF。**致命嗰類（`.sh` / `.service` / `.timer`）實測 CRLF = 0**，
所以唔會出現 `bad interpreter: /usr/bin/env bash^M`。記低係因為佢代表 working tree
同 clean clone 唔一致，量嘢時要講清楚量緊邊份。

### 5. `pipelines/tag_pop_data.py:2` SyntaxWarning

`SyntaxWarning: invalid escape sequence '\p'`（docstring 入面）。每次 pytest 都出。
唔影響行為，但係每次跑測試都有噪音。**冇改**（`pipelines/tag_pop_data.py` 唔喺本 agent 範圍）。

---

## TAG — 07-29 炸彈已拆到 220 → 45，**未清零**（2026-07-26 回填實跑）

- **登記日**：2026-07-26（承上面 §「TAG — DB 現存觀測凍喺 2026-07-22」，嗰節嘅「未做 / 新觀測未入 DB」**已過時**）
- **狀態**：production landing + DB **已回填**；炸彈由 **220 條減到 45 條**，**仲有 45 條未拆**

### 實際做咗（全部係 production 路徑，唔係 `temp/`）

真跑 `tag_daily_capture.py` 入 `data/runtime/private-source-runs/sources_20260726_a75de43f547d/`，
再用**新** backfill run-id 過 `market_source_sync.py` 寫 landing，最後 `scripts/backend.py import` 入 DB。

| 指標 | 回填前 | 回填後（實測 `COUNT(*)`） |
|---|---|---|
| TAG 總行數 | 513 | **837**（+324） |
| TAG variants | 307 | **451** |
| TAG `MAX(observed_date)` | 2026-07-22 | **2026-07-26** |
| `source_code='tag'` 行數 | 206 | **530** |
| TAG `observed_date` 分佈 | 07-22 = 513 | 07-22 = 513（原封不動）+ **07-26 = 324（新增）** |

`unusableSets` 實測 **0 / 2637**，`catalogSha256` 同 temp 驗證跑一模一樣（`61ab7473…`），即係抓取可重現。

### 側損檢查：冇整污糟第二個源

回填特登用 `--gemrate-root` 指去一個**唔存在**嘅路徑，令 batch 得 TAG 一種 observation
（`gemrateObservations: 0`、`snk 0`、`ebay 0`）。原因：`market_source_sync.observation()`
**唔會**寫 `effectiveAt`，`db_runtime` 會 fallback 去 batch 嘅 `effectiveAt`（= now），
所以順手重發 gemrate 會令**陳舊 POP 扮新鮮**。實測前後 `MAX(effective_at)` 逐個源對比，
gemrate / snkrdunk / ebay **一個都冇郁**（gemrate 四個 grader 全部停喺 `2026-07-26 06:27:38.292238`）。

`scripts/backend.py import` 回報 `batches: 16, replayedBatches: 15, observations: 324`
—— 15 個舊 batch 全部被 `market_ingest_run` 嘅 `run_key` 擋咗，只有新 batch 入數。

### 仲未拆嘅 45 條（真炸彈餘額）

用 **真嘅** `packages/market-data/dist/validate.js`（唔係我自己重寫邏輯）跑
`validatePublicSnapshot(snapshot, { production: true })`，`generation.effectiveAt` 撳去 `2026-07-29T06:00:00Z`：

| 情境 | TAG POP 新鮮度 error |
|---|---|
| 回填前（counterfactual：把 175 條新鮮嘅撳返 07-22） | **220** |
| 回填後（實際） | **45** |

counterfactual 啱啱好落返 220，同上面舊節寫嘅數對得上，證明呢個量法冇造馬。

**點解仲有 45**：呢 45 張 ranked 卡今次 TAG 抓取**認唔到身分**
（`ambiguous 23 / unmatched 894`），所以佢哋 DB 入面最新一行仍然係 07-22，
`status` 仍然係 `ready` → 逐張各爆一條。快照層面 TAG `asOf` 分佈：
**07-26 = 175 張、07-22 = 45 張、`unavailable` = 23 張**（`unavailable` 會被閘 skip，唔算）。

45 張集中喺日文 promo / special box 變體，例如：
`Pikachu with Grey Felt Hat`（2023 SV Black Star Promo × Van Gogh）、
`Poncho`（2016 XY Special Box Promo Japanese，4 張）、
`Team Skull Pikachu`（2016 SM Promo Japanese Special Box）、
`Pikachu EX`（2016 Ex Expansion 20th Anniversary Japanese 1st Edition）。

**再抓多幾次都唔會自動好返** —— 佢哋唔係「未抓到」，係「抓到但配唔到身分」。

### 剩低嘅 45 條要點修

**唔係再跑一次抓取**。兩條路，同上面舊節嘅結論一致：

1. **（首選，validate.ts 自己註明揀咗呢條）** producer 加日期下限：
   `pipelines/canonical_public_snapshot.py` 嘅 `latest_populations()` **冇日期下限**，
   超過 168h 就應該標 `status="unavailable"` / `value=null`，自然被閘 skip。
   → 45 條即刻歸零，而且源再死都唔會炸。
   **本 agent 冇改**：`canonical_public_snapshot.py` 有第二個 agent 郁緊（git `MM`）。
2. 補 TAG 身分對照（`ambiguous 23`），令呢 45 張下次抓取配得返。屬於長線覆蓋率工作。

### 冇掂嘅嘢

`data/public/seed-snapshot.json` sha256 回填前後**完全一樣**
（`29513845600f40df88e27579fe90e4c03da4130b145a41293ea02aae03f10f20`），`git status` 乾淨。
快照產出一律 `--output temp/`。



---

## eBay / SNKRDUNK「每日採集歸零」—— 係 (b)：兩者從來唔係每日源

**查證日期**：2026-07-26　**Agent**：ebay-snk-triage　**量度時間**：2026-07-26 約 10:00 UTC
**結論**：`volume_floor` 報嘅 `ebay 66 → 0`、`snkrdunk 142 → 0` **唔係採集器死咗**，
係個閘攞「一次性 backfill / bootstrap 批次」當咗「每日源」比較。冇任何嘢喺 07-25 壞咗。

### 決定性證據：`created_at` 分佈（唔係 `observed_date`）

`market_price_observation` 逐 source 逐「寫入日」：

| source_code | 寫入日 | 行數 | 覆蓋 observed_date |
|---|---|---:|---|
| ebay | 2026-07-25 | **3,732** | 2026-04-25..2026-07-25（**92 日**）|
| ebay | 2026-07-24 | 33 | 07-24 只此一日 |
| ebay | 2026-07-23 | 59 | 07-19 只此一日 |
| snkrdunk | 2026-07-24 | 71 | 07-24 只此一日 |
| snkrdunk | 2026-07-23 | 335 | 07-19 只此一日 |
| snk_psa10 | **2026-07-26** | 281 | 07-24..07-25 ← 活生生 |

**一次寫入橫跨 92 個 observed_date = 一次性 backfill，唔係逐日採集。**
`snkrdunk` 有史以來得 406 行、只喺 2 日寫入、每次淨係填 1 個 observed_date。

`run_id` 追溯（join `market_ingest_run`）：
- ebay 3,768 行 → `run_id=71`，`source_code='ebay'`，**`ingest_mode='backfill'`**，13,015 accepted，2026-07-25 23:56 一次過入。
- 其餘細批 ebay/snkrdunk → `run_src='cardz_normalized'`，來自 bootstrap 批次。

### 兩個 source_code 根本係「幽靈源」

`db_runtime.py:865` — `provider = str(row.get("providerCode") or row.get("sourceCode") or "private")`。
`market_source_observation.source_code` 存嘅係 **providerCode**，冇 providerCode 先 fallback 落卡嘅
`canonicalSourceCode`。而 `temp/expand_tracked_universe.py:100` 呢個一次性擴universe腳本
**冇出 providerCode**，payload 係 `{"priceUsd": ..., "grade": "PSA 10", "bootstrap": "active-universe"}`，
於是 fallback 成張卡嘅 canonical namespace → 憑空多咗 `ebay` / `snkrdunk` 兩個「源」。
（crosswalk 600 張卡：`snkrdunk` 442、`ebay` 158 —— 呢兩個字本來係**卡身分命名空間**，唔係採集器。）

佐證：`source_code='ebay'` 底下竟然有 `grader_population_bgs/cgc/sgc/tag` —— eBay 唔會出評分人口。

### SNKRDUNK 根本冇死

SNKRDUNK 真正嘅每日價格線係 **`snk_psa10`**，今日照跑：
`market_source_observation` observed_date=2026-07-26 有 117 行，07-25 有 281 行。
`scripts/verify_daily_run.py:39` 自己都寫明 `ANY_OF_SOURCES = ("snk_psa10", "snkrdunk")` —— 同一條 feed 嘅兩個名。
所謂「成交 65,502 行入到、價格 0 行」都係同一個誤讀：嗰 65,502 行係
`market_ingest_run` id=78/82 `ingest_mode='backfill'` 一次過入嘅，唔係每日增量。

### eBay：唔係開關問題，係**根本冇採集器**（要修 = 大工程）

1. `pipelines/run_daily.py:462` 由 `CARDZ_EBAY_SOLD_ENABLED` gate 住，預設 unset。
   `docs/SERVER_MIGRATION.md:190` 同 `docs/HANDOFF.md:120` 都寫明「預設 disabled，保持 unset」。
2. **就算 flip 咗都攞唔到嘢。** `pipelines/ebay_sold_data.py` 只係 *normalizer*，
   docstring 明寫 "This module has no Browse API path"，main() 第 313 行：
   `if args.input is None or not args.input.is_file(): raise RuntimeError("eBay sold adapter unavailable...")`
   → 冇 `CARDZ_EBAY_SOLD_INPUT` 就 exit 1 → run_daily 接住 → `ebay_status="unavailable"` → 0 行。
3. **現有 harvester 補唔到窿。** `pipelines/ebay_brute_harvest.py` 自己 docstring 講明：
   「R2 search —— completed/sold 搜尋。⚠ PerimeterX 擋 ... 呢條路而家未通，淨返 item route 可用。」
   只能拎**已知 item ID** 嘅頁，做唔到 completed-sold 發現。而且 `ebay_sold_data.py` 冇 import 佢，
   `data/private/ebay_brute/` 亦都唔存在。

**即係：要 eBay 每日有真成交，必須 (i) 攻破 eBay completed-sold search 嘅 PerimeterX，
或 (ii) 買 eBay API / 第三方成交 feed。兩條都係新工程 → 本 agent 依指示唔開工。**

現有 eBay 成交數據仍然可用但係**靜態**：`market_sale_observation` 13,015 行 / 344 卡 /
93 個 sold 日（2026-04-24..2026-07-25），全部 2026-07-25 23:56 一次 backfill 入，之後冇再增。

### 未修嘅缺陷（留返俾 gate owner 決定）

`volume_floor` 分唔開「每日 feed」同「一次性 backfill/bootstrap 批次」，
所以**每次有 backfill 冇重跑就會誤報一次紅**。今次就係。
可行方向（本 agent **冇改**，因為 `scripts/verify_daily_run.py` 係另一個 agent 今日啱啱出嘅閘）：
- 只對「連續 N 日都有行」嘅 source_code 施加 floor；或
- 明確 allowlist 每日源（`gemrate` / `snk_psa10` / `g10_analytics` / `tag`），
  其餘 source_code 唔入 floor 計算；或
- 由源頭修：bootstrap/backfill 批次一律要帶 `providerCode`，唔好 fallback 落 canonical namespace。

### 本 agent 冇掂過任何檔案

純唯讀診斷。冇改 code、冇跑採集、冇註冊排程、冇 git add/commit。


---

## U-2 — 凍結抓取加咗 Pass C（剩額填充）＋ 排程改成多觸發點

**2026-07-26 18:45 JST 更新，接住 §U。**

### 點解要加 Pass C

優先 pass（A 132 + B 52 = 368 call）**食唔晒一個窗口**。一個窗口 1000 call，
兩個窗口（07-27 / 07-28）合共 2000 call，優先只佔 368 →
**剩 1,632 call ≈ 816 張卡**。呢批額度 key 一死（07-29）就永久蒸發，
**唔屬於「記低事後做」嗰類缺口 —— 事後冇得做，係單向門。**

| | 數 |
|---|---|
| roster（`tracked-gemrate-ids.txt`） | **1,468** |
| roster 入面已有 `history_full.json` | **598** |
| **roster 缺歷史** | **870** |
| 其中 Pass A 已覆蓋 | 132 |
| **Pass A 之後仲缺** | **738** |
| 兩個窗口剩額可抓 | **816 張** |

816 > 738 →**理論上兩個窗口食盡就填得晒 roster**，仲剩 78 張卡嘅額度。
（前提係兩個窗口都真係開到、而且用戶嗰陣喺線。）

⚠️ `data/private/gemrate/cards/` 目錄數 **2,978** 係「歷來掂過嘅卡目錄數」，
**唔係 roster**。§O 個標題「701 / 2,978」用嘅係目錄數做分母，
講 roster 覆蓋率要用 598 / 1,468。呢兩個分母唔好撈埋。

### Pass C 規格

| | |
|---|---|
| **檔喺邊** | 清單產生器 `scripts/build_freeze_pass_c.py`；輸出 `data/runtime/private-source-map/freeze-pass-c-spare-ids.txt` |
| **邊個寫** | `deploy/windows/gemrate-freeze-oneshot.ps1` 喺 Pass A/B 之後即場叫 |
| **邊個讀** | 同一個 .ps1 嘅 `Invoke-ApiDump ... passC`（`--resume`） |
| **而家有冇人用** | ✅ 兩個一次性排程都會行。未跑過（2026-07-26 18:45 JST 點） |

- **差集喺 runtime 先計**，所以 Pass A/B 頭先抓到嘅自動剔走，唔會重抓。
- **優先次序**：榜內排名靠前（`tracked-universe.json` 嘅 `rankMemberships` 最細 rank）→
  市值 desc → roster 原順序。純檔案運算，**唔使 DB**，DB 死都唔會擋住 harvest。
  實測 870 張缺歷史入面有 **146 張係榜內卡**，呢 146 張排最前。
- **429 = 停止訊號**，唔預留、唔估剩幾多額。每張成功即刻 `_save()` 落盤，中途死都保得住。
- ⚠️ **Pass C 抓唔晒係正常。** 成功條件 = 優先 184 張抓齊。
  `.ps1` 唔會因為 Pass C 未完就報 fail。

### 排程改動：單一觸發點 → 每日五個

原本得 05:47 JST 一個觸發點。因為攞唔到 admin，`LogonType` 只能係 `Interactive`
（S4U 註冊實測「存取被拒」），即係**用戶嗰刻登出就成個窗口報銷**。
改用時間換：每日 **05:47 / 09:47 / 13:47 / 17:47 / 21:47 JST** 五個觸發點。

三個理由：quota 打回頭嘅 429 唔計配額（poll 免費）· 成功會落 marker，
後面觸發唔會重複做優先 pass · rolling window 越夜額度越多，遲跑反而攞得仲多。
→「用戶必須 05:47 喺線」變成「用戶當日任何時候喺線過就得」。

`MaxPollHours` 由 14 降到 **3.5**，短過觸發間隔（4 鐘），避免上一輪未收工就撞下一個觸發
（Windows `MultipleInstances=IgnoreNew` 會靜靜跳過嗰次）。

**marker 語義改咗**：`data/runtime/gemrate_freeze_complete.json` 而家只代表
「**優先 184 張抓齊**」，**唔代表冇嘢做**。後面嘅觸發見到 marker 會跳過 A/B，
但**照跑 Pass C** —— 唔係就會白白蒸發成個第二窗口。
自刪只喺 `PRIORITY_DONE_SPARE_DONE`（真係冇卡再缺）或者過咗 07-29 先做。
兩個任務各有一個 **07-30 清理觸發**（A 03:00 / B 03:15 JST）兜底自刪。

### `temp/gemrate_freeze_result.txt` 五種 status

`NOT_RUN_YET`（script 永遠唔會寫呢個，見到即係未跑過）·
`QUOTA_NEVER_OPENED`（跑過，零卡）· `PRIORITY_PARTIAL`（優先未齊）·
`PRIORITY_DONE_SPARE_PARTIAL`（**成功**，剩額食到 429）· `PRIORITY_DONE_SPARE_DONE`（全清）·
`KEY_EXPIRED_CLEANUP`。**實際收穫睇 `cardsGained`**（history 檔數前後之差），唔好淨信 status。

### 驗證（2026-07-26 18:44 JST）

兩個任務 `State=Ready`，各 6 個觸發，`LastTaskResult=267011`（SCHED_S_TASK_HAS_NOT_RUN）
= **一次都未跑過**。零 API call smoke test 行過兩條早退路徑（過期清理 / quota 未開），
兩次都冇覆寫 result 檔（no-work guard 生效）。全測試 732 passed。
**已抓卡數依然係零。**

## V — 韓文（ko）冇任何內容來源（frontend-golive，2026-07-26 實測）

`apps/web/src/lib/snapshot.ts` 原本硬寫 `ko: value.en`，即係無論如何都攞英文
當韓文出。查過三個來源，韓文係**真係一條都冇**：

| 來源 | 條數 | locale keys |
|---|---|---|
| `data/public/seed-snapshot.json` 嘅 `names` / `sets` / `image.alt` | 360 張卡 | `en` / `zhTW` / `zhCN` / `ja`（**冇 ko**） |
| `data/editorial/top100-stories.json` | 150 | `en` / `zhTW` / `zhCN` / `ja`（**冇 ko**） |
| `data/editorial/set-names.json` | 172 | `zhTW` / `zhCN` / `ja`（**冇 ko**，連 en 都冇，key 本身就係 en） |

順帶實測到：snapshot 入面 `names` / `sets` / `image.alt` 嘅 `zhTW` / `zhCN` / `ja`
**三條 key 存在但值全部係空字串**（360/360）。即係 producer 從來冇填過翻譯，
所有非英文語系一路靠 fallback 頂住。

**已做**：`ko` 改出 `null`，唔再扮有翻譯。顯示層本身已經有 fallback
（`|| t.status.unavailable` / `|| t.labels.imageAlt`），story panel 更加係
`{story && ...}`，冇嘢就成塊唔出——唔會出現「韓文標題下面一段英文」。
卡名唔受影響：`apps/web/src/lib/card-names.ts` 有獨立嘅 `KO_LEXICON` / `KO_EXACT`，
韓文卡名照出。

**未補**：set 名同 story 嘅韓文。韓文版而家 set 名位置出「데이터 없음」、
story 整段唔出。要補就要一份 ko 編輯來源（`set-names.json` 加 `ko` key、
`top100-stories.json` 加 `ko` story），唔係前端接線做得到。

**注意呢個唔對稱**：set 名冇翻譯時，`zh-TW` / `zh-CN` / `ja` 會跌返英文
（set 名係產品標題，例如「2014 XY Flashfire」，出英文本身正確），但 `ko` 出
「데이터 없음」。呢個係跟指示做（韓文冇來源就唔准扮），如果之後決定 set 名
韓文都跌返英文，改 `snapshot.ts` 嘅 `localisedSetName()` 一行就得。

## W — `set-names.json` 覆蓋率 209/360 張卡（frontend-golive，2026-07-26 實測）

`data/editorial/set-names.json`（172 條，2026-07-25 生成）之前**從來冇人 import**，
所以五個語系嘅 set 名全部出英文。已經接線落 `snapshot.ts`。

接線後實測覆蓋：

- snapshot 有 **258** 個唔同 set 名
- `set-names.json` 蓋到 **139** 個（**119 個未蓋**）
- 換算卡數：**209 / 360** 張卡有中日文 set 名，其餘 151 張跌返英文

未蓋嗰批以日版舊 set 為主（例：`2014 XY Flashfire`、`2014 XY Promo Japanese`、
`1999 Gym 2 Challenge From the Darkness Japanese Holo`）。要補就係喺
`set-names.json` 加 entry，key 用 snapshot 嘅 `sets.en` 原文（要逐字一樣）。

## X — OG 圖出唔到卡圖：`next/og` 解唔到 WebP（frontend-golive，2026-07-26 實測）

`/api/og/card/[id]` 出嘅分享圖係純排版（卡名 / set / 市值 / PSA 10 價 / POP），
**冇卡圖**。原因唔係揾唔到圖，係 `next/og` 底層嘅 resvg 解唔到 WebP。

實測方法：同一段 markup、洋紅底、只換圖片 URL，數非洋紅像素：

- 餵 PNG → **8450 px**（畫得出）
- 餵 WebP → **0 px**（乜都冇，唔報錯，靜靜留白）

而 `apps/web/public/market-assets/` 1080 個檔**100% 係 WebP**（content-addressed
`<sha256>.webp`），所以一張卡圖都出唔到。

**冇加 sharp 落去**：`sharp@0.34.5` 而家只係一個未宣告嘅 hoisted transitive dep，
而且係 native binary，喺 Cloudflare Workers（`@opennextjs/cloudflare`）行唔到，
另外 `apps/web/Dockerfile` 係 linux-deploy agent 嘅檔。要真係放卡圖，
需要一條 WebP → PNG 嘅路（例如 producer 順手出多一個 `_og.png` 衍生檔），
唔應該喺 render 時先轉。

## Y — 日文卡名有逐字直譯（frontend-golive，2026-07-26）

`apps/web/src/lib/card-names.ts` 部分日文卡名係照英文卡名逐字譯，唔係日本市場
真係叫嘅名。已修一個有來源嘅：

| 卡 | 修前 `ja` | 修後 `ja` |
|---|---|---|
| Pikachu with Grey Felt Hat (085/SVP) | グレーのフェルト帽をかぶったピカチュウ | ゴッホピカチュウ |

呢張卡**只出過英文版**（梵高美術館 ＋ 海外 Pokémon Center 限定），冇日文印刷版，
所以根本冇「官方日文卡名」。日本市場一律叫「ゴッホピカチュウ」（magi、
スニーカーダンク、ARTnews JAPAN、Hypebeast JP 都用呢個名）。原本嗰個係照英文
逐字譯，順手借咗幅畫嘅日文標題《グレーのフェルト帽をかぶった自画像》。

**未做**：其餘卡名未逐張核。核一個名要揾日文一手來源（官方卡表 / 日本 TCG 媒體 /
二手市場通稱），唔可以自己譯，所以冇批量做。優先核「英文卡名本身係描述性短語」
嗰批（`Pikachu playing in the sea`、`Gyarados Pretend Pikachu` 呢類），
因為呢啲最容易被逐字譯。`apps/web/src/lib/audit-card-names.test.ts` 跑一次會印
全部 360 張嘅四語對照，可以攞嚟做人手核對嘅工作清單。

---

## `git clone` 攞唔到圖 derivative，所以 clone 出嚟砌唔起前端（2026-07-26 量，handoff-pkg）

`data/public/market-assets/` 喺 git HEAD 入面**只有 360 個 base `.webp`，零個
`_200` / `_600`**。而 `apps/web/scripts/sync-snapshot.mjs` 撞到任何一個缺失
derivative 係**直接 throw**，唔會降級；`apps/web` 個 `prebuild` / `predev` 又
一定會叫佢。即係話：**一個乾淨 `git clone` 係 build 唔到個 web app 嘅**，
喺 `npm run prebuild` 嗰步就死。

- zip 交付路徑**唔受影響** —— 打包器由 working tree 抄，1,080 個檔（360 base +
  720 derivative）全部入包，實測 `prebuild` 綠燈。
- 只有 clone 路徑中招。
- **未做，冇追**：邊個係啱嘅修法（commit 埋 derivative？定係 build 期生成？）
  係 image pipeline 嗰邊嘅決定，唔喺交付包範圍。呢度淨係記低。

## `npm ci` 報 18 條 high severity（2026-07-26 量，handoff-pkg）

喺解壓出嚟嘅乾淨包度跑 `npm ci`，exit 0，但尾段報 `18 high severity
vulnerabilities`。冇睇係邊幾條，冇跑 `npm audit fix` —— 郁 lockfile 會改變交付
內容，而且唔喺「個包跑唔跑得起」嘅範圍。**記低，未做。**


---

## 2026-07-26 每日鏈斷更三條：GemRate timeout · eBay · snkrdunk

量度時間 **2026-07-26 19:0x JST**。DB 數字全部 exact `COUNT(*)`（唔准用
`information_schema.TABLE_ROWS`），log 數字出自
`data/runtime/logs/daily_staging_off_20260726_093000.log`。
**現象同根因分開寫；未確認嘅一律標「未確認」。**

三條都係**數據量問題唔係代碼問題 → 唔阻上線**，但入面夾住**兩個代碼 defect**，
喺各條尾標咗 🐛。

---

### 一、GemRate `daily` 撞 7200s timeout，拖冧成條鏈（已修，但仲燒 2 粒鐘）

**現象。** 09:30 排程嗰次 run 由 `09:30:06` 開跑，log 喺 `11:30:10`（檔案 mtime）終止，
全份得 **112 行**，最後一句係 `backend command failed with exit code 1`。
traceback 鏈：`run_daily.py` 個 `main()` → `run_market_source_refresh()` → `run_checked()` →
`subprocess.TimeoutExpired: ... timed out after 7200.0 seconds`，
命令係 `gemrate_source.py daily --ids-file .../tracked-gemrate-ids.txt`。

**因為死喺 `run_market_source_refresh()` 入面**，排喺 GemRate 後面嘅 TAG / SNK / eBay
**一個都冇行過** —— 呢個係「07-26 DB 冇新 ingest row」嘅直接原因之一。
即係話 07-26 呢一日嘅缺口係 **(b) 冇被叫到**，唔係 (a) 跑咗零收成。

**根因（已確認）。** 冇 API key 時 `gemrate_source.py` 個 `cmd_daily()` 會將全部 1468 張卡
行 Playwright 公開卡頁，必然撞 7200s 硬 timeout。同一機制 CLAUDE.md
「GemRate key 點樣入到每日 run」嗰節已經記錄過（`gemrate.env` 存在但冇 code load 過）。

**現況：已修。** `run_daily.py` 個 `run_market_source_refresh()` 而家用
`try/except (subprocess.CalledProcessError, subprocess.TimeoutExpired)` 包住
`gemrate_daily_command(...)` 呢個 call，degrade 成 `gemrate_live=False` 繼續行落去，
並且 print `[daily] population refresh degraded (...)`。
09:30 嗰次 traceback 指嗰個**裸 `run_checked` 直呼位置，喺現行檔已經唔存在**。
（呢句係對住 2026-07-26 19:0x 嘅 working tree 講；同期有平行 agent 改緊
`run_daily.py`，引用之前重讀過。）

**仲未解 —— 呢個先係上線關注點。** try/except 只係令佢唔再殺鏈，
**7200s wall-clock 照燒足**。實測 `09:30:06 → 11:30:10` = **2 小時 0 分 4 秒**，
而且呢兩個鐘係燒喺一個**必然 timeout** 嘅步驟。搬上 AWS/Linux
（`cardz-market-cap-daily.timer`，00:30 UTC + 1800s jitter）之後：

- daily job 每日固定佔 2 個鐘 compute，係純浪費；
- 最遲完成要早過 `cardz-market-cap-watchdog.timer`（05:07 UTC）。
  00:30 UTC + 最多 1800s jitter = 最遲 01:00 開跑，燒 2 個鐘到 03:00，
  之後仲要行埋 TAG / SNK / 指標 / snapshot / publish —— **headroom 得返約 2 個鐘**。

**補救路徑（未做）。**
1. 確認 `gemrate.env` 個 key 真係灌到入子進程 —— 睇 log 有冇 `direct=enabled`。有 key 就唔會行全量 Playwright。
2. 冇 key 時唔好行 1468 張公開卡頁，應該早退去 mirror fallback（`DEFAULT_G10_MIRROR_ROOT`），唔好等 timeout。
3. 俾 GemRate step 一個專屬短 timeout，唔好共用全域 `timeout`（而家個 7200 係全域值）。

**阻唔阻上線：唔阻**（鏈已經 degrade-continue，snapshot 照出）。
但 compute 成本 + watchdog headroom 要喺上線前決定接受定修。

---

### 二、snkrdunk：sale 有數 / price 冇數 —— **「同源兩個出口」呢個定位唔成立**

**現象（exact `COUNT(*)`）。**

| 表 | 篩選 | 行數 | 寫入日 `DATE(created_at)` |
|---|---|---:|---|
| `market_price_observation` | `source_code='snkrdunk'` | **406** | 只有 07-23（335 行）同 07-24（71 行）|
| `market_sale_observation` | snkrdunk | **65,502** | 07-26 一日 |

量法：`SELECT source_code, DATE(created_at), COUNT(*) FROM market_price_observation
WHERE source_code='snkrdunk' GROUP BY 1,2`；sale 側 join `market_ingest_run` 攞 `ingest_mode`。

<!-- @verified 2026-07-26 id=snkrdunk-price-rows-total expect>=406 ttl=90 sql=SELECT COUNT(*) FROM market_price_observation WHERE source_code='snkrdunk' -->

**根因（已確認，而且同直覺相反）。兩條都唔係每日增量，所以佢哋根本唔係同一條線嘅兩個出口。**

- **sale 嗰 65,502 行**：join `market_ingest_run` → run id 78 / 82，
  **`ingest_mode='backfill'`**，07-26 一次過入。唔係每日增量。
- **price 嗰 406 行**：07-23 嗰 335 行嘅 `observed_date` **全部係 07-19**，
  07-24 嗰 71 行 `observed_date` 係 07-24 —— 兩批都係一次性寫入，唔係連續日線。
  來源係一個一次性 universe 擴張 operator 腳本（`temp/expand_tracked_universe.py`，
  `trackingOrigin == "psa10_over1000_exhaustive_20260725"`）。

**SNKRDUNK 本身完全冇死。** 佢真正嘅每日價格線係 **`snk_psa10`**：
`observed_date='2026-07-25'` 有 **281** 行，`'2026-07-26'` 有 **117** 行，照跑。
`verify_daily_run.py` 個 `ANY_OF_SOURCES` 常數本身就寫住 `("snk_psa10", "snkrdunk")`
當同一個源嘅兩個名 —— 即係代碼一早知佢哋係同一件事。

<!-- @verified 2026-07-26 id=snk-psa10-0725-rows expect>=281 ttl=90 sql=SELECT COUNT(*) FROM market_price_observation WHERE source_code='snk_psa10' AND observed_date='2026-07-25' -->

**🐛 代碼 defect：`db_runtime.py` 會靜靜咁鑄造唔存在嘅 source。**
入 `market_source_observation` 嗰陣，`source_code` 攞嘅係
`row.get("providerCode") or row.get("sourceCode") or "private"`。
任何 writer 冇出 `providerCode`，就會 fallback 落卡嘅 **`canonicalSourceCode`** ——
而 `snkrdunk` / `ebay` 呢啲字係**卡身分命名空間**，唔係採集器名。
結果：一個一次性腳本可以憑空造出一個叫 `snkrdunk` 嘅「源」。

證據（呢個係最硬嗰粒）：`source_code='ebay'` 底下**有 `grader_population_bgs` /
`_cgc` / `_sgc` / `_tag` 呢啲 observation kind**，payload 係 `{"bootstrap": "active-universe"}`。
**eBay 唔會出評分人口。** 即係呢個 `source_code` 唔係講緊採集來源。

後果：所有 `GROUP BY source_code` 嘅監控（包括 `verify_daily_run.py` 個 `volume_floor` 閘）
都會被呢啲幽靈源污染，然後喺幽靈源冇第二次寫入嗰日報紅。
**07-25 嗰次 volume_floor 紅燈就係咁嚟。**

**補救路徑（未做）。** `db_runtime.py` 入 `market_source_observation` 嗰陣，
`providerCode` 缺失應該 fail-closed 或者寫死 `"private"`，
**唔准 fallback 落 `canonicalSourceCode`**。
另外呢個 operator 腳本仲喺 `temp/`，跟 CLAUDE.md「診斷結論要歸位」應該歸位或者刪走。

**阻唔阻上線：唔阻**（snk_psa10 生勾勾，前端讀嘅價格冇少）。

---

### 三、eBay：price + sale 同日一齊零 —— **失敗形狀同 snkrdunk 完全唔同，唔准夾同一個根因**

**現象。** `market_price_observation` `source_code='ebay'` = **3,824 行 / 343 卡**；
`market_sale_observation` eBay = **13,015 行 / 344 卡 / 93 個 unique 日**。
兩者 07-26 都零新增。

**根因（已確認）：唔係「跑咗然後零收成」，係由頭到尾冇每日採集器。**

加返 `DATE(created_at)` 就反轉晒個結論：

| `source_code` | 寫入日 | 行數 | 覆蓋幾多個 `observed_date` |
|---|---|---:|---|
| `ebay` | **2026-07-25** | **3,732** | 2026-04-25 → 07-25，**92 日** |
| `ebay` | 2026-07-24 | 33 | 1 日 |
| `ebay` | 2026-07-23 | 59 | 1 日 |

**一次寫入橫跨 92 個 `observed_date` = 一次性 backfill**，唔係 92 日嘅每日採集。
join `market_ingest_run` 實錘：嗰批全部 `run_id=71`、`source_code='ebay'`、
**`ingest_mode='backfill'`**、accepted **13,015**、started **2026-07-25 23:56**。

量法：`SELECT DATE(created_at), COUNT(*), MIN(observed_date), MAX(observed_date),
COUNT(DISTINCT observed_date) FROM market_price_observation WHERE source_code='ebay'
GROUP BY 1`，再 `JOIN market_ingest_run ON run_id` 攞 `ingest_mode`。

<!-- @verified 2026-07-26 id=ebay-price-single-writeday expect>=3700 ttl=90 sql=SELECT COUNT(*) FROM market_price_observation WHERE source_code='ebay' AND DATE(created_at)='2026-07-25' -->

**三重確認冇每日 transport：**

1. `run_daily.py` 個 eBay 段由 `CARDZ_EBAY_SOLD_ENABLED` env var 閘住，
   `docs/SERVER_MIGRATION.md` 同 `docs/HANDOFF.md` 都寫明預設 unset —— **係設計決定，唔係漏 config**。
2. **就算 flip 咗都攞唔到嘢。** `ebay_sold_data.py` 個 docstring 明寫
   *"This module has no Browse API path"*，`main()` 見到冇 `--input` 檔就
   `raise RuntimeError("eBay sold adapter unavailable: ...")` → `run_daily.py` catch →
   `ebay_status="unavailable"` → 零行。**佢係 normalizer，唔係 collector。**
3. **現有 harvester 補唔到窿。** `ebay_brute_harvest.py` 自己 docstring 寫住 completed/sold
   搜尋畀 **PerimeterX** 擋住、「呢條路而家未通」，只剩「已知 item ID」路線 ——
   做唔到成交發現。而且 `ebay_sold_data.py` 冇 import 佢，`data/private/ebay_brute/` 唔存在。

**`ebay_psa10`（真採集器會用嘅 providerCode，見 `market_source_sync.py` 個
`build_source_observations()`）喺 `market_source_observation` 由頭到尾 0 行。**

<!-- @verified 2026-07-26 id=ebay-psa10-provider-zero expect=0 ttl=3 sql=SELECT COUNT(*) FROM market_source_observation WHERE source_code='ebay_psa10' -->

（呢粒戳特登用 `ttl=3` + `expect=0`：一日真係起咗 collector，佢就應該 DRIFT 報紅提我哋更新呢節。）

**⚠ 文檔腐爛更正 —— 呢粒要改 CLAUDE.md，但唔喺我今次範圍。**
CLAUDE.md「per-source 生死」寫住 eBay「**已經每日穩定跑緊 3 個月**」、
路線第 8 點「~~P4 eBay 採集~~ —— 唔係工程項，源已經每日跑緊」。
嗰個結論係**淨睇 `observed_date` 嘅 distinct count** 得出嘅（92 個唔同日期）。
加返 `DATE(created_at)` 就見到嗰 92 個日期係**一次 backfill 嘅內容**。
→ **路線第 8 點嗰條刪除線要重開**，eBay 每日成交仍然係未起嘅工程。
（呢節本身就係 CLAUDE.md「多 agent 協作衛生」講嗰種失效模式：
agent 讀啱咗權威文檔，俾入面嘅數字呃咗。）

**同 snkrdunk 嘅分別（唔准夾埋講）：**

| | snkrdunk | eBay |
|---|---|---|
| `source_code` 本質 | **幽靈** —— providerCode fallback 鑄出嚟 | **真源名**，但冇 transport |
| 真源生死 | ✅ 生勾勾（`snk_psa10` 每日跑） | ❌ 從來冇每日跑過 |
| 修法 | 修 `db_runtime.py` fallback（一行級） | 起一個新採集器（新工程） |

唯一共通點：兩者都唔係每日增量。**淨係憑呢點唔可以推出同一個根因。**

**補救路徑（三條都係新工程，冇一條係 flag 級修復）。**
1. 攻破 completed/sold search 嘅 PerimeterX（`ebay_brute_harvest.py` 已經試過，未通）。
2. 買 eBay API 或者第三方成交 feed。
3. 接 G10 本機嗰份 —— `docs/G10_BASELINE.md` 講嘅 **559 卡 / 9,489 條**，
   前提係先寫日期解析（ISO 同相對 `N days ago` 兩種格式撈埋）同擴 identity 覆蓋率。
   **成本最低嗰條，數據一早喺本機硬碟。**

**阻唔阻上線：唔阻**（現有 13,015 行成交數據靜態但可用）。
⚠ 但「eBay = PSA10 成交唯一真源」呢個產品定位，而家係靠**一份 2026-07-25 嘅凍結快照**撐住，
**每過一日舊一日**，而且冇任何閘會喺佢過期時出聲（同 TAG 個 07-22 凍結數同一個病）。

---

### 附：`volume_floor` 閘本身有缺陷（唔喺呢三條入面，但同源）

`verify_daily_run.py` 個 `volume_floor` 用 rolling 30 日 ledger 比今日行數，
**分唔開「每日 feed」同「一次性 backfill / bootstrap 批次」**。
所以每次有 backfill 而第二日冇重跑，佢就誤報一次紅 —— 07-25 嗰次就係。

**我冇改呢個閘。** 佢係另一個 agent 同日出嘅嘢，
喺 rollout 中途改鬆一個閘去令佢唔叫，正正就係「靜靜地少咗嘢」嘅反向版。
三個方向（ledger 分 `ingest_mode` / 幽靈源列白名單 / 只對 `REQUIRED_SOURCES` 行 floor）
留返俾閘 owner 揀。

---

## §Z — `snk_psa10` 五日內流失 94.5% 觀測量（量度於 2026-07-26 19:5x）

**🔴 影響上線。** 唔係缺口，係一條生存中嘅源正在死。

量法（`market_source_observation`，即係 `verify_daily_run.py` 嘅 `counts_on()` 讀嘅同一張表、同一條 query）：

```sql
SELECT source_code, observed_date, COUNT(*) AS n
FROM market_source_observation
WHERE observed_date >= DATE_SUB('2026-07-26', INTERVAL 9 DAY)
GROUP BY source_code, observed_date;
```

| observed_date | 07-17 | 07-18 | 07-19 | 07-20 | 07-21 | 07-22 | 07-23 | 07-24 | 07-25 | 07-26 |
|---|---|---|---|---|---|---|---|---|---|---|
| `snk_psa10` | 1674 | 1603 | 1711 | 1993 | **2149** | 1692 | 853 | 502 | 281 | **117** |

由 07-21 高位起計連續五日下跌，每日約腰斬（50% / 59% / 56% / 42%）。roster 約 1,468 張，07-26 嘅 117 行 ≈ **8% 覆蓋**。

**呢個形狀值得注意**：穩定平台期之後突然開始等比衰減，係爬蟲被漸進式 throttle 或者 IP 逐步封鎖嘅典型曲線，唔似係上游真係冇成交。**未證實**，需要睇 collector 嘅 HTTP 狀態碼分佈先落定論——本節只報量度到嘅嘢。

### 點解冇人發現

`volume_floor` 閘每日都有 fire，alert 檔一直有寫入 `data/runtime/alerts/`。但 `CARDZ_ALERT_WEBHOOK` 未設定，`notify_alert.py` 走 `no CARDZ_ALERT_WEBHOOK configured; alert recorded only` 然後 **return 0**，所以 alert unit 自己成功、`systemctl --failed` 乾淨，五日嘅警報全部躺喺 disk 冇人睇。

呢條係「檢查器 exit 0」病嘅第四個實例（前三個見 `docs/STAGE_REVIEW_20260726.md` §3.1），亦係第一個**已經造成實際損失**嘅實例。

### 同場確認（唔使另開條目）

- `g10_analytics` 636（07-24）→ **1277**（07-25），翻倍。實錘咗 §3.4 預測嘅 backfill 污染：07-26 嘅正常量對住 1277 做基準，必然出一日假紅。
- `tag` 07-22 有 206 行，之後停到 07-26 先返嚟（324 行）。
- `gemrate` 3779→3007（80%）觸發咗閘，但佢 07-22 得 8 行、07-23 得 1 行，本身就係斷續源，20% 落差喺佢自己噪音內，**唔當異常**。
- `ebay` / `snkrdunk` 兩個名係 provider fallback 捏造（見 §ghost source），07-25 歸零屬預期，聽日 baseline < 10 會自動唔再計。

### 修之前要答嘅問題（唔喺呢輪做）

1. collector 近五日嘅 HTTP 狀態碼分佈——429 / 403 佔比升唔升？
2. 係每張卡攞少咗，定係攞到嘅卡數本身少咗？（`COUNT(DISTINCT card_id)` vs `COUNT(*)`）
3. 有冇改過 request 節奏或者 User-Agent。

**唔准喺呢輪動手修**：呢條要睇 collector 執行記錄，唔係改 gate 或者改閾值可以解決。調高閾值去令警報收聲，係喺報壞消息嘅嘢上面貼膠布。
