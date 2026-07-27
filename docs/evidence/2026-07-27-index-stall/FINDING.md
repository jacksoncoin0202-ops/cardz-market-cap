# 官方指數停更診斷

**量度時點：** 2026-07-27 12:48 JST（= 03:48 UTC）。本機時區 UTC+9，**DB timestamp 一律 UTC**，下文凡標 JST 均為換算值。
**Agent：** `bg-F-index-stall`（唯讀。零寫入 DB、零改動 pipeline、零外部 API 調用；只新增本目錄檔案）

---

## TLDR

**指數冇停更。**`market_index_snapshot` 嘅 `effective_date` 由 2026-07-21 到 2026-07-26 **連續無斷層**，`market_alert_evaluation` id=9（effective_date 07-26）亦已存在——兩者都喺 **2026-07-27 02:18:5x UTC（11:18 JST）**由 09:30 JST 排程 run 寫入。

報告嘅症狀（「最新停 07-25、冇 eval 9」）**係量度窗口偽陽性**：呢個指數係 T-1 序列，一日只由 09:30 JST 嗰個 task 產出一次，而 task 要行 **1 小時 48 分**先寫到 snapshot。所以**每日 00:00 JST 至 ~11:20 JST 之間查，`MAX(effective_date)` 必然讀到「今日 − 2」**。07-27 所有 evidence 目錄嘅建立時間喺 00:10–02:37 JST，全部落喺呢個窗口內。

**但**呢次診斷順帶量到三個真病，同停更假象無關但要 PM 知（見「真病」段）：publish 從來冇跑過、verify 每日 exit 1、07-26 世代薄弱兼市值翻倍。

---

## 證據鏈

### 步驟 1（先行）：兩張 observation 表嘅 `observed_date`

```bash
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/ro_sql.py "SELECT DATE(observed_date) AS d, COUNT(*) AS n FROM market_price_observation WHERE observed_date >= DATE_SUB(CURDATE(), INTERVAL 8 DAY) GROUP BY d ORDER BY d"
python -X utf8 scripts/ro_sql.py "SELECT DATE(observed_date) AS d, COUNT(*) AS n FROM market_grader_population_observation WHERE observed_date >= DATE_SUB(CURDATE(), INTERVAL 8 DAY) GROUP BY d ORDER BY d"
python -X utf8 scripts/ro_sql.py "SELECT 'price' AS tbl, MAX(observed_date) AS max_obs, COUNT(*) AS total FROM market_price_observation UNION ALL SELECT 'population', MAX(observed_date), COUNT(*) FROM market_grader_population_observation"
```

| `observed_date` | `market_price_observation` | `market_grader_population_observation` |
|---|---|---|
| 07-21 | 405 | 2477 |
| 07-22 | 977 | 521 |
| 07-23 | 365 | 1 |
| 07-24 | 1009 | 3883 |
| 07-25 | 755 | 2656 |
| 07-26 | **66** | 889 |
| 07-27 | — | 324 |

`MAX`：price = **2026-07-26**（總 124,987 行）；population = **2026-07-27**（總 10,751 行）。

**兩條線都冇斷供。**價格線 07-26 只得 66 行係量級塌陷，唔係零。分源拆解：

```bash
python -X utf8 scripts/ro_sql.py "SELECT DATE(observed_date) AS obs_d, source_code, COUNT(*) AS n FROM market_price_observation WHERE observed_date >= '2026-07-23' GROUP BY obs_d, source_code ORDER BY obs_d, source_code"
```

| `observed_date` | ebay | g10_kline | snk_psa10 | snkrdunk |
|---|---|---|---|---|
| 07-24 | 200 | 576 | 162 | 71 |
| 07-25 | 12 | 576 | 167 | — |
| 07-26 | **0** | **0** | 66 | — |

07-26 只剩 `snk_psa10` 一個源，`g10_kline`（前兩日各 576 行）同 `ebay` 歸零。

### 步驟 1b：原始抓取表證明今日有喺度跑

```bash
python -X utf8 scripts/ro_sql.py "SELECT DATE(created_at) AS d, observation_kind, COUNT(*) AS n FROM market_source_observation WHERE created_at >= DATE_SUB(CURDATE(), INTERVAL 6 DAY) GROUP BY d, observation_kind ORDER BY d, observation_kind"
```

07-27（UTC）寫入：`index_constituent` 7,684、`tracked_sales_daily` 1,844、五個 grader POP kind 合共 812。**上游今日有供。**

再拆 `created_at` × `observed_date`，證實 07-27 嗰次抓取帶返 07-13→07-26 嘅滾動歷史，而**最新一格 `index_constituent` 係 observed_date 07-26 = 66 行**——同 `market_price_observation` 07-26 嘅 66 行**完全對數**，印證「價格只由 `index_constituent` 衍生」。07-27 嗰格只有 `tracked_sales_daily` 21 行，冇 `index_constituent`，即上游 07-27 日線未埋單（JST 午夜先埋單，量度時 12:48 JST）。

### 步驟 2：evaluation / generation 鏈

```bash
python -X utf8 scripts/ro_sql.py "SELECT index_code, index_version, MAX(effective_date) AS max_eff, COUNT(*) AS n FROM market_index_snapshot GROUP BY index_code, index_version"
python -X utf8 scripts/ro_sql.py "SELECT * FROM market_alert_evaluation ORDER BY id"
```

`market_index_snapshot`（全表 18 行，唯一 `index_version = psa10-v3-complete`）：

| index_code | MAX(effective_date) | 行數 |
|---|---|---|
| one-piece | **2026-07-26** | 6 |
| pokemon | **2026-07-26** | 6 |
| tcg-combined | **2026-07-26** | 6 |

`effective_date` distinct = 07-21, 07-22, 07-23, 07-24, 07-25, 07-26 —— **6 日 × 3 個 index = 18 行，序列連續，零缺口。**

07-26 三行嘅 `run_id = 108`、`created_at = 2026-07-27 02:18:54~55 UTC`。

`market_alert_evaluation` 最新兩行：

| id | effective_date | eligible_count | top100_cutoff_usd | coverage_status | created_at (UTC) |
|---|---|---|---|---|---|
| 8 | 2026-07-25 | 255 | 7,090,475.90 | blocked | 2026-07-26 06:28:10 |
| **9** | **2026-07-26** | **336** | 10,495,019.22 | blocked | **2026-07-27 02:18:53** |

**eval 9 存在。**（`coverage_status=blocked` 係長期已知狀態，8 次 eval 全 blocked，唔擋 snapshot 寫入——見 `docs/HANDOFF.md:102`。）

### 步驟 3：daily run 嘅 log / state（旁證）

`data/runtime/logs/daily_staging_off_20260727_093001.log` 尾段（run 09:30:01 JST 開，log mtime 11:19 JST）：

```
{"candidates": 1468, "coverageStatus": "blocked", "cutoffUsd": 10495019.219999999,
 "effectiveDate": "2026-07-26", "eligible": 336, "evaluationId": 9, "status": "evaluated"}
...
"trackedIndexes": {"one-piece": {"count": 42, "effectiveDate": "2026-07-26"},
                   "pokemon": {"count": 294, "effectiveDate": "2026-07-26"},
                   "tcg-combined": {"count": 336, "effectiveDate": "2026-07-26", "publicCount": 300, "reserveCount": 36}}
...
{"runId": "daily_20260727T003011442904Z", "mode": "staging", "status": "backend-ready",
 "gemrateLive": false, "snkLive": true, "tagLive": true, "databaseSynced": true}
```

DB `created_at` 02:18:55 UTC ↔ log mtime 11:19 JST，差 1 分鐘，**時間軸對得上**。

排程狀態（唯讀）：

```powershell
Get-ScheduledTask -TaskName 'CARDZ-Market-Cap-Daily' | Get-ScheduledTaskInfo
```

```
LastRunTime    : 27/7/2026 9:30:01
LastTaskResult : 1
NextRunTime    : 28/7/2026 9:30:00
NumberOfMissedRuns : 0
StartBoundary  : 2026-07-26T09:30:00+09:00   Enabled : True
```

**Task 有跑、無漏跑，但 exit code = 1。**（成因見「真病 B」。）

### 步驟 4：verify gate 嘅 UTC/JST 基準

brief 指定嘅 `scripts/verify_handoff.py` **唔係日期閘本體**——佢只喺 line 39 引用 `scripts/verify_daily_run.py` 做「唯一 outcome gate」。真正基準邏輯喺 `scripts/verify_daily_run.py:81-97`：

```python
def default_expected_date(now: datetime | None = None) -> str:
    ...
    now = now or datetime.now(timezone.utc)
    return (now.date() - timedelta(days=1)).isoformat()
```

**(a) 舊病已修。**基準已由「UTC today」改為 **T-1**，docstring 明寫咗 JST 午夜埋單嘅推導。今日實測 `expectedDate: 2026-07-26`（07-27 UTC 跑），正確。

**(b) 同今次無因果。**今日 gate 輸出：

```
[verify] PASS price_freshness: max(observed_date)=2026-07-26 expected>=2026-07-26
[verify] PASS snapshot_freshness: all 3 indexes have effective_date>=2026-07-26
[verify] PASS source_coverage / PASS ingest_activity (10340 rows today UTC)
[verify] FAIL volume_floor: g10_analytics 1277->0 (0%); gemrate 3007->570 (19%); snk_psa10 913->528 (58%)
[verify] PASS constituent_sanity: tcg-combined constituents 255 -> 336
```

日期相關兩閘（`price_freshness`、`snapshot_freshness`）**兩個都 PASS**。gate 亦係喺 DB 寫入**之後**先跑（log 次序可證：`status: backend-ready` 喺 verify 行之前印），結構上唔可能「錯殺」snapshot。

> 註：`bg-I-verify-tz` 同期喺做 verify gate 時區專項診斷（FILE_CLAIMS 13:00 時見），呢段只作交叉覆核，深挖交返俾佢。

### 步驟 5：上游斷供 / 管道卡死 / gate 錯殺 三分

| 假設 | 判定 | 依據 |
|---|---|---|
| 上游斷供 | **部分成立（非停更主因）** | 07-26 價格得 66 行單源；`g10_kline`、`ebay` 歸零。但 POP 線 07-27 仍有 324 行，`market_source_observation` 07-27 有 10,340 行 |
| 管道卡死 | **07-26 成立、07-27 已解** | 見「真病 A」 |
| gate 錯殺 | **不成立** | 步驟 4；gate 為 post-hoc，且日期兩閘 PASS |
| 量度窗口偽陽性 | **成立，係報告症狀嘅解釋** | 序列 07-21→07-26 零缺口；07-26 世代 11:18 JST 先落地，症狀觀察落喺 00:10–02:37 JST |

---

## 真病（同停更假象分開，但要處理）

### A. 07-26 嗰次係真卡死，已自癒兼已修

`data/runtime/logs/daily_staging_off_20260726_093000.log` 尾：

```
subprocess.TimeoutExpired: Command '[... 'pipelines/gemrate_source.py', 'daily', ...]'
    timed out after 7200.0 seconds
backend command failed with exit code 1
```

09:30 JST 排程 run 喺 `run_daily.py` 舊 `run_checked()` 路徑 raise 上去，成條鏈（價格→指數→publish）一齊死。當日 07-25 世代要靠 14:12 JST 嗰次手動 run 喺 **15:28 JST（06:28 UTC）**先補到。

結構性修正**已經喺 repo 入面**：`pipelines/run_daily.py:352-366` 已用 `try/except (CalledProcessError, TimeoutExpired)` 包住 gemrate，degrade 成 `gemrateLive=false` 繼續行。今日 run 嘅 summary `"gemrateLive": false` 而照樣行完並寫低 snapshot，**證明修正生效**。呢條唔使再郁。

### B. publish 由頭到尾冇喺排程跑過

Task action：`run-cardz-daily.ps1 -Mode staging`，**冇 `-Publish`**。`deploy/windows/run-cardz-daily.ps1:18` 註明 `$Publish` 預設 `'off'` = 舊 backend-only 行為，要 publish 必須喺 task action 明寫 `-Publish local`。所以：

- run 終態係 `"status": "backend-ready"`，**唔係** `run_daily.py:991` 嗰個 `"status": "published"`
- `data/runtime/publish-staging/latest.json` mtime = **2026-07-26 04:15 JST**，即成兩代冇更新

**若果「官方指數」係指網站見到嘅嘢而唔係 DB 表**，噉先係真停更，而根因係呢個缺失嘅 flag，唔係數據鏈。呢點請 PM 先釐清口徑。

另外 task `LastTaskResult = 1`：wrapper 將 verify 嘅 `volume_floor` FAIL 帶成 process exit 1。即係話**排程日日報失敗，但 DB 其實寫咗嘢**——呢個 signal 反轉會令下次真死機睇唔出嚟。

### C. 07-26 世代存在但薄，市值翻倍係擴容唔係升市

```bash
python -X utf8 scripts/ro_sql.py "SELECT p.observed_date AS price_date, COUNT(*) AS constituents FROM market_index_snapshot s JOIN market_index_constituent c ON c.index_snapshot_id=s.id LEFT JOIN (SELECT variant_id, MAX(observed_date) AS observed_date FROM market_price_observation WHERE observed_date <= '2026-07-26' GROUP BY variant_id) p ON p.variant_id=c.variant_id WHERE s.index_code='tcg-combined' AND s.effective_date='2026-07-26' GROUP BY price_date"
```

tcg-combined 07-26 世代 336 個成分股入面：**只得 66 個有 07-26 價格，其餘 270 個用緊 07-25 價**，而且 336 個全部 `metric_status = 'ready'`（無一個標記為 stale）。

市值 2,439.9M USD（07-25）→ **5,246.9M USD**（07-26），+115%。拆解：

| 分組 | 數目 | 07-26 市值 | 07-25 市值 |
|---|---|---|---|
| 兩代都有 | 255 | 2,714.1M | 2,439.9M（+11.2%） |
| **07-26 新入圍** | **81** | **2,532.8M** | — |

**翻倍嘅 48% 由 81 隻新入圍成分股貢獻**（eval 9 `eligible_count` 336 vs eval 8 255，差 81，對得上），唔係價格暴漲。呢個數如果直接出街，讀者會理解成「一日升一倍」。

---

## 修復提案（交返俾主線 PM 執行，本 agent 冇改任何嘢）

按緊急度排：

1. **先釐清口徑（阻塞其他項）** — 「官方指數停更」係指 `market_index_snapshot` 表定係網站見到嗰個數？如果係後者，真正 action 係第 2 條；如果係前者，冇嘢要修，只需第 4 條。
2. **排程加 publish** — `CARDZ-Market-Cap-Daily` 嘅 action 加 `-Publish local`（`run-cardz-daily.ps1:18` 自己嘅註釋就係咁講）。注意 wrapper line 46-49：publish 鏈尾要 `npm run build` 兼要 npm 喺 PATH，改之前要確認排程 context 有 npm，否則會喺燒完成個 GemRate 抓取之後先死。
3. **拆開 verify 嘅退出語義** — 而家 `volume_floor` FAIL 令整個 task exit 1，日日紅燈會令真故障靜音。建議將 `volume_floor` 由 fail 降為 warn，或者令 wrapper 只喺 `price_freshness` / `snapshot_freshness` / `ingest_activity` 呢類硬閘失敗先回非零。
4. **量度窗口寫入文檔** — 「T-1 指數 + 09:30 JST run + ~1h50m 工時 ⇒ 每日 11:20 JST 前 `MAX(effective_date)` 讀到 today−2」呢句要寫入 HANDOFF/RUNBOOK，否則下一個 agent 喺朝早查 DB 會再報一次假停更（今次已經係一次）。
5. **修 07-26 價格單源塌陷** — `g10_kline` 由 576/日 跌到 0、`ebay` 由 200/日 跌到 0，要獨立查一次點解。呢個係 `volume_floor` FAIL 嘅實質內容，唔好淨係關咗個閘算數。
6. **`metric_status` 要反映價格新鮮度** — 270/336 用緊 T-1 價但全部標 `ready`，落 UI 會變成「今日數據」。建議加一個 carry-forward 標記，或者喺 snapshot 層記低 `freshPriceCount`。
7. **新入圍成分股要有 delta 說明** — 81 隻新股一次過帶入 2.53B，任何對外圖表都要分開「擴容」同「升跌」，唔好混入日變幅。

---

## 未能確定 / 仲差嘅證據

- **網站實際 serve 緊邊個世代**：本次只量到 `data/runtime/publish-staging/latest.json`（07-26 04:15 JST）同 DB。前端讀邊條路徑、有冇 R2/CDN 快取層頂住，未量。要 PM 或前端 agent 補。
- **`g10_kline` / `ebay` 07-26 歸零嘅成因**：只確認咗「歸零」呢個事實，未查 collector 側。屬第 5 條 action 範圍。
- **07-27 03:42 UTC 嗰兩個 `market_ingest_run`**（status `completed`，量度前 6 分鐘）：唔係排程 run，冇產出 index snapshot，估計係同期其他 agent 嘅動作，未追。
