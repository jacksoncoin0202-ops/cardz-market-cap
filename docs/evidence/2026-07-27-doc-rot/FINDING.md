# 文檔腐爛審計 — 2026-07-27

**審計員**：G（唯讀審計，冇改過任何被審文檔）
**量度日期**：2026-07-27 12:45–13:05 JST
**審計範圍**：[docs/HANDOFF.md](../../HANDOFF.md)、[README.md](../../../README.md)、`docs/*.md` 全部 33 份（不含 `docs/evidence/`）。合共 **34 份**。
**`apps/web/README.md` 唔存在**，該範圍項作廢。

**量度時前提**：MySQL `cardz-market-cap-db-1` 在線；DB env 由 `data/runtime/config/backend.env` 載入（內容從未印出）；零外部 API 調用；所有 DB 數字用 `scripts/ro_sql.py` 加 `COUNT(*)` 量度，冇用 `information_schema.TABLE_ROWS`。

---

## TL;DR

| 級別 | 條數 | 一句概括 |
|---|---:|---|
| **BROKEN** | **4** | 照做會出錯或者靜默攞錯數 |
| **STALE** | **10** | 數字／狀態過時，唔致命但會誤導判斷 |
| **OK** | **14** | 抽查過，實測相符 |

**最需要即刻處理嗰條**：[HANDOFF.md §8.1](../../HANDOFF.md) 寫住 GemRate key 到期「已降級為非事件」，理由係 `direct=disabled`。**今朝 09:30 嘅 run log 實測係 `direct=enabled`** —— 風險評估完全反轉咗。key 約 07-29 到期，而家距離兩日，照住份文檔做等於乜都唔做。

**呢個 repo 嘅腐爛形態已經量化咗**：路徑同命令幾乎零腐爛（34 份文、637 個引用、0 條死 link、0 個唔存在嘅 script），腐爛 100% 集中喺**數字同狀態描述**。用「檢查死連結」呢類工具係捉唔到呢個 repo 嘅病嘅。

**根因**：repo 自己已經有反腐爛機制 —— [scripts/verify_claims.py](../../../scripts/verify_claims.py) 嘅 `@verified` 戳（過期會叫）。但 34 份 in-scope 文檔入面**得 1 份**（`DATA_GAPS.md`，5 個戳）有用。機制存在，覆蓋率 3%。詳見 STALE-9。

---

## BROKEN — 照做會出錯

### BROKEN-1 · HANDOFF §8.1：GemRate key 風險評估已經反轉

**文檔原文**（[docs/HANDOFF.md:117](../../HANDOFF.md)）：

> 1. **GemRate key ~07-29 到期——已降級為非事件（2026-07-26 02:20 JST 實證）**：07-25/26 backfill run log 見 `[daily] 1468 cards, direct=disabled, public-card-page=enabled, mirror=enabled`（[gemrate_source.py](../../../pipelines/gemrate_source.py) 個 `cmd_daily()` 打印），即 pipeline **已經以 keyless transport 做 primary** 行緊 […] key 到期唯一影響 = direct API transport 唔再可用，而佢本身已 disabled。

**實測證據**（2026-07-27 13:00 量度）：

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap" && \
for f in $(ls -t data/runtime/logs/daily_*.log | head -2); do echo "[$f]"; \
  grep -o "direct=[a-z]*, public-card-page=[a-z]*, mirror=[a-z]*" "$f" | tail -2; done
```

```
[data/runtime/logs/daily_staging_off_20260727_093001.log]
direct=enabled, public-card-page=standby, mirror=enabled
[data/runtime/logs/daily_staging_off_20260726_141239.log]
direct=enabled, public-card-page=standby, mirror=enabled
```

**一句結論**：`direct` 由 disabled 變咗 **enabled**，`public-card-page` 由 enabled 變咗 **standby**。即係話 key 而家係 **primary transport**，keyless 只係後備。文檔講嘅完全相反。

**點解危險**：份文檔明確叫接手嘅人唔使理呢件事（「已降級為非事件」）。實情係 07-29 key 一到期，primary transport 就冧，要靠一條而家只係 standby 嘅路頂上。呢個係整份文入面唯一一條會令人**主動選擇唔行動**嘅錯。

**建議改法**：將 §8.1 整段第一句由

> **GemRate key ~07-29 到期——已降級為非事件（2026-07-26 02:20 JST 實證）**

改成

> **GemRate key ~07-29 到期——係真風險（2026-07-27 13:00 JST 實測）**：`daily_staging_off_20260727_093001.log` 見 `direct=enabled, public-card-page=standby, mirror=enabled`，即 direct API 而家係 **primary**，keyless public card page 只係 standby。key 到期＝primary transport 失效，必須喺 07-29 前實測 standby 路徑食得住 1468 卡全量。

同時將段尾「`.env.private` 冇 set `GEMRATE_API_KEY`（grep 實證 0 hit）」呢句刪走或者改成指向現行注入路徑（key 而家經 `data/runtime/config/gemrate.env` → `backend.py` `SECRETS_PATH` 入去，`.env.private` grep 唔到唔代表冇 key）。

---

### BROKEN-2 · HANDOFF §2：publish 命令用咗唔存在嘅 flag 名

**文檔原文**（[docs/HANDOFF.md:50](../../HANDOFF.md)）：

> - **backend-only 路徑冇 export/publish**。publish 係另一條路：`backend.py daily --publish`（拎走 --backend-only，加 --required-presentation-view top300）

**實測證據**：`scripts/backend.py` 全部 CLI flag（`argparse` 抽取）：

```
['--allow-empty-db', '--bootstrap-archive', '--check', '--collect-public-candidates',
 '--confirm-rebuild-db', '--external-db', '--format', '--full-backfill-candidate-out',
 '--full-backfill-snk-refill-out', '--json', '--local-only', '--mode', '--output',
 '--presentation-view', '--priority', '--publish', '--refresh-active-universe',
 '--require-gemrate-refresh', '--require-global-top350', '--restore-overwrite',
 '--seed-archive', '--seed-target', '--snapshot-output', '--snk-run', '--status', '--wsl-distro']
```

`backend.py` 用嘅係 **`--presentation-view`**（[scripts/backend.py:783](../../../scripts/backend.py)），而且 [scripts/backend.py:815](../../../scripts/backend.py) 係嚴格 `parser.parse_args()`，未知 flag 直接 argparse error exit 2。

`--required-presentation-view` 係 **`pipelines/run_daily.py`** 嘅 flag（[pipelines/run_daily.py:646](../../../pipelines/run_daily.py)），唔係 `backend.py` 嘅。

呢個 repo 一共有三個名極似嘅 flag，好易撈亂：

| Flag | 屬邊個 script |
|---|---|
| `--presentation-view` | `scripts/backend.py`（用戶入口） |
| `--required-presentation-view` | `pipelines/run_daily.py`（用戶入口） |
| `--require-presentation-view` | `pipelines/canonical_public_snapshot.py` 內部調用（`backend.py:328/409`、`run_daily.py:155` 傳落去） |

**一句結論**：照抄文檔嗰句砌 `backend.py daily --publish --required-presentation-view top300`，會即刻 argparse error，行都行唔到。

**建議改法**：將

> `backend.py daily --publish`（拎走 --backend-only，加 --required-presentation-view top300）

改成

> `backend.py daily --publish --presentation-view top300`（`backend.py` 收 `--presentation-view`；佢會自動轉成 `--required-presentation-view` 傳落 `run_daily.py`，見 [backend.py:935](../../../scripts/backend.py)。直接跑 `run_daily.py` 嗰陣先用 `--required-presentation-view`）

---

### BROKEN-3 · HANDOFF §7：`external_entity_id` 格式描述唔完整，照住寫 query 會靜默漏數

**文檔原文**（[docs/HANDOFF.md:112](../../HANDOFF.md)）：

> - `market_grader_population_observation`.external_entity_id 格式 `gemrate:<gid>`（帶 prefix）

**實測證據**（2026-07-27 12:55）：

```sql
SELECT source_code, SUBSTRING_INDEX(external_entity_id,':',1) AS prefix, COUNT(*) AS n
FROM market_grader_population_observation GROUP BY source_code, prefix ORDER BY n DESC;
```

| source_code | prefix | n |
|---|---|---:|
| gemrate | gemrate | 6899 |
| snkrdunk | snkrdunk | 1506 |
| **gemrate** | **snkrdunk** | **1147** |
| tag | snkrdunk | 547 |
| tag | gemrate | 288 |
| ebay | ebay | 242 |
| **gemrate** | **ebay** | **103** |
| tag | ebay | 19 |

**一句結論**：prefix 有三種（`gemrate:` / `snkrdunk:` / `ebay:`），而且 **prefix 同 `source_code` 唔對齊** —— `source_code='gemrate'` 嘅行入面有 1147 行帶 `snkrdunk:` prefix、103 行帶 `ebay:` prefix。

**點解危險**：呢條唔會報錯，會靜默出錯。任何人照文檔寫 `WHERE external_entity_id = CONCAT('gemrate:', gid)` 去撈 GemRate 數據，會靜靜哋漏走 **1250 行**（佔 gemrate source 15%），然後得出一個睇落合理但係錯嘅覆蓋率。

**建議改法**：將嗰行改成

> - `market_grader_population_observation`.external_entity_id 係 `<namespace>:<id>`，namespace 有三個：`gemrate:` / `snkrdunk:` / `ebay:`。**namespace 唔等同 `source_code`** —— `source_code='gemrate'` 嘅行入面 1147 行係 `snkrdunk:` prefix、103 行係 `ebay:`（2026-07-27 實測）。撈某個 source 一律用 `source_code` 過濾，唔好靠 prefix。

---

### BROKEN-4 · DB_INVENTORY_20260726.md：叫人唔好再查一個已經冇咗嘅缺口

**文檔原文**（[docs/DB_INVENTORY_20260726.md:83](../../DB_INVENTORY_20260726.md)）：

> - **確認 2026-07-25 = 0 行**，與已知 root cause（UTC vs JST 時區令舊 06:30 JST schedule 嘅 `run_id` 誤判為 replay，跳過重爬；已於 2026-07-26 修好）完全吻合，呢個係預期之內嘅永久性缺口，**唔使再查**。

同段表格亦寫住 `07-24 | 113 ← 偏低`、`07-25 | 0 ← GAP`。

**實測證據**（2026-07-27 13:00）：

```sql
SELECT observed_date, source_code, COUNT(*) AS n FROM market_price_observation
WHERE observed_date BETWEEN '2026-07-24' AND '2026-07-27'
GROUP BY observed_date, source_code ORDER BY observed_date DESC, n DESC;
```

| observed_date | 實測總數 | 明細 | 文檔講 |
|---|---:|---|---:|
| 2026-07-26 | 66 | snk_psa10 66 | —（文檔冇） |
| **2026-07-25** | **755** | g10_kline 576 · snk_psa10 167 · ebay 12 | **0（GAP）** |
| **2026-07-24** | **1009** | g10_kline 576 · ebay 200 · snk_psa10 162 · snkrdunk 71 | **113** |

**一句結論**：07-25 嗰個所謂「永久性缺口」已經被 07-26 補 run 填返 755 行；07-24 亦由 113 升到 1009。份文檔嘅整段日行數表已經全面失效，但佢用祈使句叫人**唔好再查**。

**點解算 BROKEN 唔算 STALE**：其餘過時數字只係令人估錯，呢條係一條**指令**。agent 讀完會直接跳過呢段數據，帶住「07-25 冇數」嘅假設落後面所有推論。

**建議改法**：份文件名自帶日期（`20260726`），係一份有意嘅凍結快照，所以唔應該逐格更新。最低成本改法係喺該表上面加一行警告：

> ⚠️ 下表係 2026-07-26 嘅量度。**2026-07-27 複測：07-25 已由 0 → 755 行、07-24 由 113 → 1009 行**（07-26 補 run 填返）。下面「永久性缺口，唔使再查」嗰句已作廢，唔好再引用。現行數字請即場用 `scripts/ro_sql.py` 重量。

同時將「呢個係預期之內嘅永久性缺口，唔使再查」刪走。

---

## STALE — 數字／描述過時，唔致命

### STALE-1 · HANDOFF §2：`backend.py` 行數

**原文**（[HANDOFF.md:36](../../HANDOFF.md)）：`scripts\backend.py daily --mode staging          ← orchestrator（937 行）`
**實測**：`wc -l scripts/backend.py` → **955**
**改法**：`（937 行）` → `（955 行，2026-07-27 量）`。或者索性刪走行數 —— 行數係必然腐爛嘅數字，冇任何操作價值。**建議刪走**。

### STALE-2 · HANDOFF §6：測試 case 數

**原文**（[HANDOFF.md:105](../../HANDOFF.md)）：`測試喺 [tests/test_verify_daily_run.py](../tests/test_verify_daily_run.py)（10 cases）`
**實測**：`grep -c "^def test_" tests/test_verify_daily_run.py` → **24**
**改法**：`（10 cases）` → `（24 cases，2026-07-27 量）`。同上，建議索性刪走數字改成「（見檔內 `test_` 函數）」。

### STALE-3 · HANDOFF §7：「最新快照」已經唔係最新

**原文**（[HANDOFF.md:113](../../HANDOFF.md)）：`07-24 最新快照：run 29 = combined 262 / pokemon 230 / one-piece 32，$2.518B`
**實測**（`SELECT ... FROM market_index_snapshot ORDER BY id DESC LIMIT 9`）：

| effective_date | run_id | tcg-combined | pokemon | one-piece | combined 市值 |
|---|---:|---:|---:|---:|---|
| **2026-07-26** | **108** | **336** | **294** | **42** | **$5.247B** |
| 2026-07-25 | 95 | 255 | 224 | 31 | $2.440B |
| 2026-07-24 | 29 | 262 | 230 | 32 | $2.518B |

**注意**：run 29 嗰組數字**本身完全正確**，錯嘅只係「最新」兩個字。市值兩日內由 $2.518B → $5.247B（翻超過一倍），任何人拎住舊數做 sanity check 都會判斷錯。
**改法**：`07-24 最新快照：run 29 = ...` → `快照進度（2026-07-27 量）：run 108 / 07-26 = combined 336 / pokemon 294 / one-piece 42，$5.247B。（歷史：run 29 / 07-24 = 262 / 230 / 32，$2.518B）`

### STALE-4 · HANDOFF §1：dev port 3793 冇講點先去到

**原文**（[HANDOFF.md:28](../../HANDOFF.md)）：`前端 Next.js 16 `apps/web`（dev: http://localhost:3793）`
**實測**：`apps/web/package.json` 嘅 `"dev": "next dev"` —— **冇 `-p`**，直接 `npm run dev` 出 Next 預設 **3000**。3793 只喺 [.claude/launch.json](../../../.claude/launch.json) 嘅 `cardz-market-cap-web` config 出現，而且係 `"autoPort": true`（撞 port 會自己飄）。
另外 `docs/DATA_GAPS.md:1041` 同 `docs/STAGE_REVIEW_20260726.md:27` 兩份文都用緊 **3800**（`launch.json` 嘅 `cardz-web` config，`autoPort: false`）。即係全 repo 有兩個互相矛盾嘅「dev port」講法。
**改法**：`（dev: http://localhost:3793）` → `（dev：用 launch.json 嘅 `cardz-web` config 出 http://localhost:3800 固定 port；`npm run dev` 裸跑係 Next 預設 3000）`

### STALE-5 · HANDOFF §4：用現在式描述一個 35 鐘頭前已完嘅 run

**原文**（[HANDOFF.md:68-70](../../HANDOFF.md)）：`## 4. 行緊嘅嘢（接手第一件事：驗收佢）` / `01:47:56 JST […] 起咗補 run […] 01:48 已驗證 State=Running`
**實測**：該 run 嘅 log `data/runtime/logs/daily_staging_20260726_014756.log` 存在（✓ 路徑啱），但係 2026-07-26 嘅嘢；之後已經行過 run 95（07-25）同 run 108（07-26）。頂部 banner 有講份文係凍結快照，但 §4 個標題係祈使句「接手第一件事：驗收佢」，同 banner 打交。
**改法**：將標題 `## 4. 行緊嘅嘢（接手第一件事：驗收佢）` 改成 `## 4. （歷史）2026-07-26 01:47 嗰個補 run —— 已完成，結果見 run 95/108`。

### STALE-6 · HANDOFF Task Board #2：backfill 數字

**原文**（[HANDOFF.md:92](../../HANDOFF.md)）：`Phase D 四評級 backfill 入 MySQL（1468 gids / 5376 obs / PSA 1416·CGC 1424·BGS 1233·SGC 1303）`
**實測**：gemrate source 1490 distinct gids / **8149** obs；各評級 distinct gid：PSA **1591** · CGC **1527** · BGS **1413** · SGC **1346**。
呢條係「✅ 已完成」嘅歷史紀錄，數字增長屬正常，嚴重性最低。
**改法**：句尾加 `（完成當日數字；2026-07-27 已增長到 PSA 1591·CGC 1527·BGS 1413·SGC 1346）`。

### STALE-7 · README：「唔准裝獨立 GemRate/SNK 排程」同實機唔一致

**原文**（[README.md:177](../../../README.md)）：

> Do not install separate GemRate or SNK scheduler tasks. `pipelines/run_daily.py` owns the singleton run ID and publishes only after the complete generation passes validation.

**實測**（`Get-ScheduledTask` + `Get-ScheduledTaskInfo`，2026-07-27 12:50）：實機除咗 daily / watchdog 之外仲有

| Task | State | 觸發 / 最近結果 |
|---|---|---|
| `CARDZ-GemRate-Freeze-Oneshot-A` | Ready | NextRun 27/7 13:47，LastResult 0 |
| `CARDZ-GemRate-Freeze-Oneshot-B` | Ready | NextRun 28/7 05:47，LastResult 267011（未跑過） |
| `CARDZ-TAG-Daily-Capture` | Ready | 06:45，LastRun 27/7，LastResult 0 |
| `CARDZ-Freeze-Sweep-Guard` | Ready | LastResult 1 |

**一句結論**：呢句規則同實機狀態直接矛盾。要麼規則過時（freeze/週掃係後來刻意加嘅獨立節奏），要麼實機違規。**呢條要主線 PM 裁決，唔係單純改字**。
**改法（假設規則過時）**：改成

> Do not install separate GemRate or SNK **daily price/population** scheduler tasks — `pipelines/run_daily.py` owns the singleton daily run ID. The GemRate **freeze/roster sweep** tasks (`CARDZ-GemRate-Freeze-Oneshot-*`) and `CARDZ-TAG-Daily-Capture` run on a deliberately separate cadence and are exempt.

### STALE-8 · `CARDZ-Freeze-Sweep-Guard` 指住 `temp\`，但 repo 出咗 `deploy/windows/`

**證據**：repo 自己嘅 [scripts/verify_doc_refs.py](../../../scripts/verify_doc_refs.py) 掃出唯一一個 `TEMPDEP`：

```json
{"task": "CARDZ-Freeze-Sweep-Guard",
 "path": "temp\\freeze-sweep-guard.ps1",
 "action": "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"...\\cardz-market-cap\\temp\\freeze-sweep-guard.ps1\""}
```

而 repo 實際有 `deploy/windows/freeze-sweep-guard.ps1`。`temp/` 唔入版本控制，清 temp 就會靜靜咁整死個排程。
**改法**：呢條唔係改文檔，係改排程 action 指去 `deploy\windows\freeze-sweep-guard.ps1`。列喺呢度係因為佢係 repo 自己個掃描器報出嚟而一直未處理。

### STALE-9 · 反腐爛機制存在但覆蓋率 3%

**證據**：[scripts/verify_claims.py](../../../scripts/verify_claims.py) 係專門為咗解決呢個問題而寫嘅（doc string 原話：「an agent reads the authoritative document, gets the path right, trusts a number that was measured days ago」）。實跑 exit 0，全部戳都 hold。

但 `grep -rl "@verified" docs/*.md README.md` 只有 **1 份**：`docs/DATA_GAPS.md`（5 個戳）。其餘 33 份 in-scope 文檔 —— 包括本次搵到 BROKEN 嘅 `HANDOFF.md` 同 `DB_INVENTORY_20260726.md` —— **一個戳都冇**。`verify_claims.py` 掃到嘅其餘戳全部喺 `docs/evidence/**`（歷史包，本身唔會再變）。

**一句結論**：機制建咗，但淨係用喺唔會腐爛嘅文（evidence 凍結包），冇用喺會腐爛嘅文（HANDOFF、README、DB_INVENTORY）。呢個係本次 4 條 BROKEN 全部可以被自動捉到但冇捉到嘅結構性原因。

**改法（最高槓桿嘅一條）**：喺 HANDOFF §7 嗰批 DB 數字加戳，例如：

```markdown
- 快照進度：run 108 / 07-26 = combined 336
  <!--@verified 2026-07-27 id=handoff.idx.combined expect>=336
      sql=SELECT constituent_count FROM market_index_snapshot WHERE index_code='tcg-combined' ORDER BY id DESC LIMIT 1-->
```

之後 `verify_claims.py` 過 3 日 TTL 就會自動叫。

### STALE-10 · CLAUDE.md 仲叫人「接手第一份讀 HANDOFF」

**證據**：`CLAUDE.md` 嘅文檔地圖列住 `docs/HANDOFF.md（接手第一份）`，但 `HANDOFF.md` 自己開頭第 3 行寫住「⚠️ 已被取代 …… 現況睇 PROJECT_STATE.md」。
**一句結論**：新 agent 照 CLAUDE.md 指引，第一份讀到嘅就係一份自認已被取代、而且入面有 3 條 BROKEN 嘅文。
**改法**：`CLAUDE.md` 該行改成 `docs/HANDOFF.md（**歷史凍結快照，非現況** — 現況一律睇 PROJECT_STATE.md）`。
（`CLAUDE.md` 係 FILE_CLAIMS 標明嘅最高碰撞風險檔，改之前記得 claim。）

---

## OK — 抽查過，實測相符

路徑同命令層面幾乎零腐爛，以下逐項實測過：

1. **死連結 0 條**。34 份文抽出 637 個引用（markdown link + 反引號路徑），過濾到真路徑後 **0 條斷**。producer：[pathcheck.py](pathcheck.py)。
2. **repo 自己個掃描器 0 failure**。`scripts/verify_doc_refs.py` → `docs_scanned: 52, references: 118, OK 113 / DRIFT 3 / BARE 2 / TEMPDEP 1, failures: 0`。
3. **命令引用 0 條指去唔存在嘅 script**。34 份文入面全部 `python *.py` / `node *.mjs` / `npm run *` / `*.ps1` 逐個對過。producer：[cmdcheck.py](cmdcheck.py)。（`docs/AWS_DEPLOY.md` 三處 `apps/web/server.js` 係假陽性 —— 嗰個係 `.next/standalone` build artifact，文檔前面已經有 `cd apps/web/.next/standalone`。）
4. **HANDOFF §7 `market_index_snapshot` 欄位表完全正確**，包括「**冇** snapshot_date/scope_code」呢個否定聲明。實測欄位：`id, run_id, index_code, index_version, effective_at, effective_date, constituent_count, total_market_cap_usd, snapshot_sha256, created_at`，一個不多一個不少。
5. **`index_version` = `psa10-v3-complete`** ✓ 實測三個 index 全部係。
6. **排程時間兩個都啱**：`CARDZ-Market-Cap-Daily` trigger `2026-07-26T09:30:00+09:00` ✓ 對 HANDOFF「09:30 JST」；`CARDZ-Market-Cap-Watchdog` trigger `2026-07-26T14:07:00+09:00` ✓ 對「14:07 JST」。
7. **HANDOFF Task Board #1 屬實** —— `cardz-beta-daily-refresh` 同 `cardz-beta-hourly-refresh` 兩個都係 Disabled。
8. **HANDOFF §6 `PRESENTATION_VIEW_MIN_COVERAGE` 講法正確** —— [canonical_public_snapshot.py:78-84](../../../pipelines/canonical_public_snapshot.py) 個 dict `top300: 100` ✓，`presentation_view_min_coverage()` 存在 ✓。
9. **`run_daily.py` flag 全對** —— `--backend-only` ✓、`--required-presentation-view` ✓、`--mode staging` ✓、`--local-only` ✓。
10. **`verify_daily_run.py` flag 全對** —— 得三個：`--expected-date` ✓、`--tag` ✓、`--no-alert`。HANDOFF 引嘅 `--expected-date YYYY-MM-DD` 同 `--tag watchdog` 都存在。
11. **`backend.py` action 全對** —— README 用到嘅 `registry` / `explain` / `work-items` / `graph` / `generate-docs` / `bootstrap` / `status` / `daily` / `alerts` 九個全部喺 choices tuple 入面；`--publish` 亦存在。
12. **README 引嘅 flag 全對** —— `--bootstrap-archive` / `--restore-overwrite` / `--external-db` / `--json` / `--check` / `--format` / `--status` / `--priority` 八個逐個 grep 確認。
13. **`AWS_HANDOFF.md` 同 `PACKAGING_CHECKLIST.md` 嘅 `--view top300 --production` 正確** —— 實跑 `canonical_public_snapshot.py --help` 確認 `--view {top100,top300,top350,top100_plus_200,top300_boards}` ✓ `--production` ✓。（同 BROKEN-2 對照：呢兩份文冇撈亂 flag，淨係 HANDOFF 撈亂咗。）
14. **README repository layout 十個目錄全部存在** ✓；**HANDOFF 引用嘅九個檔案全部存在** ✓（包括兩個 log、三個 ps1、`SOAK_GUIDE.md`、`pipelines/GEMRATE_SOURCE.md`、`PROJECT_STATE.md`、`cardz-market-cap-watchdog.timer`）。

---

## 附帶觀察（唔屬文檔腐爛，但主線應該知）

- **今日 09:30 嘅 daily run 失敗**：`CARDZ-Market-Cap-Daily` LastTaskResult = **1**（LastRun 2026-07-27 09:30:01）；`CARDZ-Market-Cap-Watchdog` LastTaskResult = **1**（LastRun 2026-07-26 14:07）。`market_price_observation` 2026-07-27 **零行**，07-26 得 66 行（對比 07-25 755、07-24 1009）。呢個係 live 故障，唔係文檔問題，應該由診斷指數停更嗰條線跟。
- **one-piece 只有 42 隻成分股**（目標 350）。HANDOFF §1「三榜各 350」讀落似現況，實際係目標。冇當 STALE 計係因為上下文明顯係講設計，但如果要嚴謹可以加「（目標；2026-07-27 實際 336/294/42）」。

---

## 可重跑

```bash
cd "C:\Users\jackson0202\Documents\Playground\cardz-market-cap"

# 死連結
python -X utf8 docs/evidence/2026-07-27-doc-rot/pathcheck.py

# 命令存在性
python -X utf8 docs/evidence/2026-07-27-doc-rot/cmdcheck.py

# repo 自己個引用掃描器
python -X utf8 scripts/verify_doc_refs.py --json temp/docrefs.json

# 驗證戳
set -a && . data/runtime/config/backend.env && set +a
python -X utf8 scripts/verify_claims.py
```
