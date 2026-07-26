# CARDS Market Cap — Session 交接 Report

> ## ⚠️ 已被取代 —— 呢份文係 2026-07-26 01:45 嘅**凍結快照**，唔係現況
>
> **現況睇 [PROJECT_STATE.md](../PROJECT_STATE.md)**（repo 根目錄，單一真相來源）。
>
> 呢份文已知過時嘅地方：寫住「publish 鏈未通」（同日已打通）、
> 「整體 65%」（之後推進過）、「ranking 停咗喺 07-24」（已修）。
> 當歷史紀錄讀，**唔好當現況引用**。

> 原文寫於 2026-07-26 01:45 JST。俾新 session 接手用：唔使翻舊 session。

---

## 0. TL;DR

- 目標：**本機 Windows 全自動營運 1–2 日成功，先上新 server**。一日一更，唔使實時。
- 用戶自評：前端搞掂晒；**淨係爭後端條鏈全自動通**。
- 整體上線進度約 **65%**。三個擋上線 gate：①ranking 停咗喺 07-24（root cause 已搵到已修，見 §3）②publish 鏈未通 ③排程之前盲跑冇 log（已修，見 §3 修復三）。
- 2026-07-26 01:48 已做三個修復：排程 trigger 06:30→**09:30**；ps1 加咗 log 捕捉；補 run 已改由 Task Scheduler detached 行緊（見 §4 驗收位）。

## 1. 項目定位

- 品牌 **CARDS Market Cap**（cardsmarketcap.com）；CF staging：`cardz-market-cap-staging.jacksoncoin0202.workers.dev`
- 唯一 live repo：`C:\Users\jackson0202\Documents\Playground\cardz-market-cap`（`cardz-deploy` 係 stale fork 唔准用；`cardz-platform` 已廢棄只讀）
- 市值公式：GemRate PSA 10 POP × validated PSA 10 USD 價
- 三榜 tcg-combined / pokemon / one-piece，各 350（300 公開 + 50 私人 reserve，reserve50 永不可 export/publish）
- 前端 Next.js 16 `apps/web`（dev: http://localhost:3793），五語+多貨幣，用戶已驗收
- Budget：$0 經常開支。GemRate 試用 key **約 07-29 到期**，之後全 keyless scraping（curl_cffi impersonate）。執行用平 agent，本機免費 soak，之後先 AWS。

## 2. 架構一條鏈（每日 flow）

```
Task Scheduler (09:30 JST)
 → deploy\windows\run-cardz-daily.ps1（load .env.private）
 → scripts\backend.py daily --mode staging          ← orchestrator（937 行）
 → pipelines\run_daily.py --mode staging --backend-only
     ① FX collect_or_reuse
     ② run_market_source_refresh：GemRate population + SNK PSA10 價 + TAG(輔助) + eBay(off)
        → market_source_sync.py 寫 landing batch
     ③ backend.py import  → canonical MySQL
     ④ pipelines\market_alerts.py  ← ★市值計算+排名+寫 market_index_snapshot 喺呢度
     ⑤ backend.py status
 → scripts\verify_daily_run.py                       ← outcome gate，daily 成敗都必跑

Task Scheduler (14:07 JST) — 獨立第二層
 → deploy\windows\run-cardz-watchdog.ps1
 → scripts\verify_daily_run.py --tag watchdog        ← 唯讀重驗，捉「根本冇行過」
```
- **backend-only 路徑冇 export/publish**。publish 係另一條路：`backend.py daily --publish`（拎走 --backend-only，加 --required-presentation-view top300）
- Derive 核心：[market_alerts.py](../pipelines/market_alerts.py) 個 `evaluate()` 嘅 `INSERT IGNORE INTO market_index_snapshot`，unique key `(index_code, index_version, effective_date)`；`effective_date` = `MAX(observed_date) FROM market_price_observation`（同檔 `latest_price_date()`）。**價唔推進 → snapshot 永遠唔會有新行，完全靜默。**

## 3. ★ 2026-07-26 Root Cause：timezone 撕裂（已修）

07-25 排程 exit 0 但冇新 snapshot，診斷結果：

1. [run_daily.py](../pipelines/run_daily.py) 個 `main()`：`market_run_id = datetime.now(timezone.utc).strftime("sources_%Y%m%d")` — **UTC 日期**
2. 本機時區 **+09:00 JST**。排程 06:30 JST = UTC 前一日 21:30 → run_id 永遠係「尋日」
3. 後果鏈（07-25 實證）：SNK collector 見同 run_id 已有輸出 → `"replayed": true`（report 實據：`sources_20260724_9e49eb726ee6\snk-psa10_report.json`）→ 冇新爬 → `market_price_observation` 最新停 07-24（113 條）→ `latest_price_date()`=07-24 → evaluation dedupe / INSERT IGNORE no-op → 冇 run 30
4. 語義撕裂實證：07-25 source obs **只有 gemrate 1136 條**（GemRate collector 用本地日期戳 07-25），而 07-24 有 ebay 66 / gemrate 3779 / snk_psa10 175 / snkrdunk 142 四路——兩套日期語義並存
5. 大量表面誤導：排程日日 exit 0、obs 有增長，令人以為正常

**修復（三項已做，01:48 JST 驗證）**：
1. Trigger 06:30 → **09:30 JST**（= UTC 00:30，UTC 日期同本地一致，run_id 撕裂自動消失，零 code 改動）。`Get-ScheduledTaskInfo` 已證 NextRun = 07/26 09:30
2. [run-cardz-daily.ps1](../deploy/windows/run-cardz-daily.ps1) 加咗 log 捕捉：每 run 寫 `data\runtime\logs\daily_<mode>_<timestamp>.log`。**注意 PS 5.1 footgun**：`$ErrorActionPreference='Stop'` + native command `2>&1` 會令 python 第一行 stderr 變 terminating error 秒殺成個 script（第一版真係咁死咗，exit 1 + 0-byte log）——所以必須用 `cmd.exe /c "... > log 2>&1"` byte-level redirect，已測證 stdout+stderr 齊全、中文冇亂碼、exit code 正確傳遞
3. 補 run 用 `Start-ScheduledTask` 起（§4）——detached 喺 Task Scheduler 名下，唔會跟 Claude session 死

## 4. 行緊嘅嘢（接手第一件事：驗收佢）

01:47:56 JST 用 `Start-ScheduledTask -TaskName 'CARDZ-Market-Cap-Daily'` 起咗補 run（**detached，session 重開唔影響**；01:48 已驗證 State=Running + log 實時寫入）：

- Log：`data/runtime/logs/daily_staging_20260726_014756.log`（tail 佢睇進度；全程約 1.5–2 小時：GemRate 1468 卡 public card pages + SNK 251 卡）
- 舊嘅 `daily_manual_20260726_0137.log` 係上一次未完成嘅嘗試（行到 GemRate 75/1468 被有序停咗，改用 detached 方式重起；SNK 未開始所以冇 replay 風險）
- 預期：而家 UTC=07-25 → run_id=`sources_20260725_<hash>`（新目錄，唔會 replay）→ SNK 新爬 ~251 卡 → price obs 推進到 07-25 → market_alerts 寫**新 index snapshot（effective_date 07-25）**
- 驗收 SQL（只讀，credentials 喺 `data\runtime\config\backend.env`，**永不印密碼**）：
  ```sql
  SELECT id, run_id, index_code, effective_date, constituent_count, total_market_cap_usd
  FROM market_index_snapshot ORDER BY id DESC LIMIT 6;
  -- 成功 = 見到 effective_date 2026-07-25 嘅新行（07-24 最新係 run 29: 262 張 / $2.518B combined）
  SELECT observed_date, COUNT(*) FROM market_price_observation
  GROUP BY observed_date ORDER BY observed_date DESC LIMIT 3;
  -- 成功 = 最新 observed_date 2026-07-25
  ```
- 如果 run fail：讀 log 尾段，錯誤多數喺 SNK collect 或 GemRate（key 07-29 到期後會死，見 §8 風險）
- 明朝 09:30 排程 run 會係第一個「日期一致」嘅自動 run（UTC=本地=07-26），佢應該出 07-26 snapshot——**呢個就係 soak Day 1 嘅檢查點**

## 5. Task Board

| # | 狀態 | 內容 |
|---|------|------|
| 1 | ✅ | 停用兩個廢棄 cardz-beta 排程 task |
| 2 | ✅ | Phase D 四評級 backfill 入 MySQL（1468 gids / 5376 obs / PSA 1416·CGC 1424·BGS 1233·SGC 1303） |
| 3 | 🔶 收尾 | Root cause 已搵到已修；等 manual run 完驗收新 snapshot（§4） |
| 4 | ⬜ | 打通 publish：DB→canonical snapshot→R2 staging→pointer。**先 `--local-only`**（唔使 R2/canary env）。remote 要三個 env：`CARDZ_STAGING_R2_BUCKET` + `CARDZ_GENERATION_CANARY_COMMAND_JSON` + `CARDZ_POINTER_PROMOTE_COMMAND_JSON` |
| 5 | ⬜ | 刷新 seed + 本地起 site 俾用戶驗收（**必須** `Start-Process '<url>'` 開佢瀏覽器 + 第一句講 URL） |
| 6 | 🔶 | 排程改造：時間（09:30）✅、log 捕捉 ✅（見 §3 修復二）、outcome verify gate ✅、獨立 watchdog 排程 ✅（14:07 JST，2026-07-26 加，見 §6 尾兩條）；剩返 `--publish`（等 #4 通咗先加） |
| 7 | 🔶 | 驗證新卡 auto-add 流程（等 run 完睇 ensure_std_card_images 段 log）；soak 監察指南 ✅ [docs/SOAK_GUIDE.md](SOAK_GUIDE.md)（2026-07-26） |

## 6. 預研結論（慳你行冤枉路）

- **262 eligible 唔會擋 export**：`top300` view 嘅 min coverage=100（[canonical_public_snapshot.py](../pipelines/canonical_public_snapshot.py) 個 `PRESENTATION_VIEW_MIN_COVERAGE`，由 `presentation_view_min_coverage()` 讀出），rows ≤ limit 合法，只要 rank 連續。publish 唔使等補到 300 張。
- Alert evaluation 全部 `coverage_status=blocked`（discovery radar blocked / eligible<100 兩種成因）——**blocked 唔擋 snapshot 寫入**（07-24 blocked 照寫 run 29），唔使即時處理。
- Coverage audit（`data/runtime/private-reports/data-coverage-audit.json`）同 canonical DB 脫節：`canonicalDb.state="not_queried"`，audit 要求 gemrate_direct live 驗證，唔認 DB 入面 backfill 好嘅數據。將來要修，唔擋上線。
- `backend.py daily` dispatch 邏輯喺 [scripts/backend.py](../scripts/backend.py) 個 `main()`；**`run_database_tool` 同上面嗰批 helper 未讀過**（實際調邊個腳本未睇，import 有問題先需要睇）。
- **Outcome verify gate（2026-07-26 加）**：daily 鏈有三類靜默失敗位——[market_alerts.py](../pipelines/market_alerts.py) `evaluate()` 嘅 `INSERT IGNORE` 落空只 `continue`、SNK collector replay 唔重爬、`db_runtime.py status` 只驗 integrity 唔驗 freshness——全部 exit 0 但零新數據（07-25 timezone 撕裂就係咁樣冇聲冇氣）。修法唔係逐個改，而係 [scripts/verify_daily_run.py](../scripts/verify_daily_run.py) 做 post-run outcome gate：驗 price freshness / 三 index snapshot freshness / per-source coverage（gemrate 必須 + snk_psa10 或 snkrdunk 任一，per-source 先捉到 gemrate 有 SNK 冇嘅 07-25 病態）+ constituent 跌 >20% warning。fail → exit 非零 + 寫 `data\runtime\alerts\daily_verify_<date>_<ts>.json`，`run-cardz-daily.ps1` 已掛鉤，Task Scheduler LastTaskResult 由此反映**結果**唔係過程。手動跑：`.venv-backend\Scripts\python.exe -X utf8 scripts\verify_daily_run.py --expected-date YYYY-MM-DD`。測試喺 [tests/test_verify_daily_run.py](../tests/test_verify_daily_run.py)（10 cases）。
- **獨立 watchdog 排程（2026-07-26 加）**：上面那個 gate 掛在 daily 之後，只有 daily 真正執行過才會觸發。機器關機、排程被 disable、run 卡死這三種情況下 gate 一次都不會執行，結果是完全靜音——與「一切正常」在表面上無法區分。所以另加一個獨立排程 `CARDZ-Market-Cap-Watchdog`，每日 **14:07 JST（= 05:07 UTC）** 執行 [deploy/windows/run-cardz-watchdog.ps1](../deploy/windows/run-cardz-watchdog.ps1)：先記錄 daily 排程本身的 `state` / `LastRunTime` / `LastTaskResult`（找不到就寫 `daily task NOT FOUND`），再以 `verify_daily_run.py --tag watchdog` 重驗當日數據。它是唯讀的，不重跑採集、不重試，失敗本身就是要保留的訊號。每日檢查用單一入口 [deploy/windows/cardz-status.ps1](../deploy/windows/cardz-status.ps1)，一次過看兩個排程 + 新鮮度 + alert 檔 + 兩份 log 的 `[verify]` 行；判讀表在 [docs/SOAK_GUIDE.md](SOAK_GUIDE.md)。Linux 對應單元是 `cardz-market-cap-watchdog.timer`（05:07 UTC），觸發時刻完全對應。

## 7. DB 快速參考

- MySQL 8.4 Docker `cardz-market-cap-db-1`，`127.0.0.1:3308`，DB `cardz_market_cap`，user `cardz`，密碼喺 `data\runtime\config\backend.env`（只讀查詢用，**永不印出**）
- `market_index_snapshot` 欄位：id, run_id, index_code(`tcg-combined`/`pokemon`/`one-piece`), index_version(`psa10-v3-complete`), effective_at, effective_date, constituent_count, total_market_cap_usd, snapshot_sha256, created_at（**冇** snapshot_date/scope_code）
- `market_grader_population_observation`.external_entity_id 格式 `gemrate:<gid>`（帶 prefix）
- 07-24 最新快照：run 29 = combined 262 / pokemon 230 / one-piece 32，$2.518B

## 8. 風險與地雷

1. **GemRate key ~07-29 到期——已降級為非事件（2026-07-26 02:20 JST 實證）**：07-25/26 backfill run log 見 `[daily] 1468 cards, direct=disabled, public-card-page=enabled, mirror=enabled`（[gemrate_source.py](../pipelines/gemrate_source.py) 個 `cmd_daily()` 打印），即 pipeline **已經以 keyless transport 做 primary** 行緊（public card page + Grade10 mirror，成功率 ~96%，07-25 入咗 1136 行 source observation）。`.env.private` 冇 set `GEMRATE_API_KEY`（grep 實證 0 hit），冇「過期 key 殘留令 direct 403」風險。key 到期唯一影響 = direct API transport 唔再可用，而佢本身已 disabled。keyless 路線細節：[pipelines/GEMRATE_SOURCE.md](../pipelines/GEMRATE_SOURCE.md)。
2. G10 stealth 規則：上傳/爬取**唔准照抄 G10 日程**。
3. TAG 係輔助，schema 壞唔擋主流程（run_daily 已 fail-soft + last_good fallback）。
4. eBay 採集預設 disabled（`CARDZ_EBAY_SOLD_ENABLED` 控制）。
5. Full-backfill 係另一條 resumable fail-closed 路徑，唔好同 daily 撈亂。
6. ~~`daily.lock` 留低會擋下次 run~~ **——查證後係假警報（2026-07-26 02:31 JST 實測）**：[run_daily.py:69-76](../pipelines/run_daily.py) 用 OS 級 advisory lock（Windows `msvcrt.locking` / POSIX `fcntl.flock`），唔係 PID 檔。實測用 `kill -9` 強殺持有者後，lock **檔仲喺磁碟**但第二個 process 即刻攞得到（BLOCKED → ACQUIRED）。即係 run 中途死機唔會卡死下一日，**唔好手動刪 lock 檔**（刪咗反而可能兩個 run 撞埋）。

## 9. 硬規則（必守，違反過被鬧）

- 回覆第一句稱「daddy」，廣東話；所有路徑/URL 用 markdown link
- 講「自動化行緊」之前必須即場驗證並寫明驗證時間；儲存≠發佈，唔誇大交付狀態
- 交付本地服務：`powershell -NoProfile -Command "Start-Process '<url>'"` 開用戶瀏覽器 + 第一句講 URL
- population 係存量指標，**永不顯示負 delta**；UI 唔准自加 disclaimer；產品 i18n 文案書面中文
- 卡圖原生 RGBA 圓角 429×600，唔准 CSS border-radius（詳見 repo `CLAUDE.md`）
- 改 .py 後跑 repo 測試先報完成；`python -X utf8` 必加；Windows 主環境唔用 WSL；Bash 每 call `cd "絕對路徑" &&` 開頭
- 唔准 commit secrets / print 密碼 / 重印 GemRate key；任何 agent 不得 push GitHub、部署 production、自行降低 POP/價格/身份 gate；GitHub push 要用戶確認，CF staging 可以先上
- 用戶授權模式：「KEEP GO KEEP GO」= 持續執行唔使逐步問准
