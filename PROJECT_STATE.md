# PROJECT_STATE — CARDZ Market Cap（2026-08-24 定案）

> ## 一條線：收返自動化嘅主權，四步走完就收工
>
> **08-25 03:30 tick** 自動上 migration 059 → **08-25 日間**人手 supersede 一次，親眼睇住帳期由「行快一日」拉返齊 → **08-26 03:30 tick** 開 `CARDZ_V2_AUTO_SUPERSEDE`，之後由鏈自己對齊 → **08-27／08-28 兩日唔好掂佢**，攞 `autonomous_proven = true`。
>
> **呢四步之間唔開新工程。** 其他欠單全部排喺 §4，唔准插隊；每一步嘅驗收條件寫喺 §3。

> **呢份係現狀唯一入口。** 舊逐代記錄（025–037 開代史）已封存：[docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)。
> 讀嘢次序：[AGENTS.md](AGENTS.md)（硬規矩）→ 本檔（現狀）→ 各專題檔（§7 文件地圖）。
> **文件同 code 衝突時 code 贏**，發現即修文件。日程／cadence 真相永遠睇 `Get-ScheduledTask`，唔好信文件記憶。

## 0. 一眼現狀

| 項 | 數值 | 來源 |
|---|---|---|
| Live | `https://app.cardzmarketcap.com` · **1604 張** · generation `db3308_0b7eb7f4d2c34174` | `/api/health` 07:21Z；宣傳 gate 19:00 JST 再對一次同一 generation [KNOWN] |
| 版本 | product **037** · presentation **FE05**（fallback FE04）· BOX `/box` 307／275／307 | [KNOWN] |
| 日更排程 | Task `CARDZ-Marketcap-Daily-V2` **Ready**，下一跑 **2026-08-25 03:30 JST**，PT10M repeat 到 17:00，上次 result **0** | `Get-ScheduledTaskInfo` 本檔實查 [KNOWN] |
| 今日 run | `cardz-v2:2026-08-25` **PUBLISHED**，14:09 JST 收尾 exit 0，零 PARKED | `data/runtime/daily-chain-v2/health.json` [KNOWN] |
| 自動化證明 | `autonomous_proven = false` | health.json [KNOWN] |
| ⚠ 帳期 | **business date 行快一日**（早跑食咗 slot：08-24 當日跑咗 business date 08-25）。修法＝supersede，見一條線 | health.json [KNOWN] |
| 宣傳鏈 | 2026-08-24 **六步全部出街**，gate DONE day=2026-08-24 generation=`db3308_0b7eb7f4d2c34174`（19:00 JST）。⚠ 今日係人手接力，唔係 cron 一 take | gate 輸出 [KNOWN] |
| 本樹 | `rebuild/036-foundation`，**08-24 晚已 push 上 origin（ahead 0）**，HEAD 見 `git log` | `git push` + `rev-list --count` 實查 [KNOWN] |

## 1. 今日（2026-08-24）落咗咩

**四個 commit 落 `rebuild/036-foundation`（`bf1aa457..911397c5`，未 push）：**

| commit | 做咗咩 |
|---|---|
| `e5542344` | **identity-brief 誠實度修**：喺 ready／exact 短路**之前**先讀未答嘅 reverify hold（08-24 audit 判 `needsYou=0` 係算式恆等式，唔係觀察）；lane objection 連 artifact 一齊出；bridge `CARDZ_V2_STATE_DB`，brief 而家讀返 run 自己嗰本 journal |
| `21dacd59` | **pc-identity 規則修**：`snk`／`snk_psa10` 數 exact binding 前先 fold 成同一個 provider（v1020／v1040 嘅 `multiple_exact_bindings` 係呢個假陽性）；`rh`／`spc` printing 而家賺得返自己嘅 `[Reverse]`／`[SP <metal>]` 頁；`_gemrate_printing_sha` 唔再含卡名 |
| `634c1d7f` | **daily-chain-v2 supersede**：一個 business date 可以用 `/N` run generation **原地再出街**；加 `identity-census` stage；watchdog 唔再將已 superseded 嘅日當「做完」。附 migration **059**——`publication_outbox` 唯一鎖由 `(business_date,event_type)` 放寬做 `(business_date,event_type,generation_id)` + `superseded` reader flag。**`event_key` 鎖冇郁**，所以普通重跑一樣照撞，唔會靜靜出兩次 |
| `911397c5` | **collect guard**：撞到 9333 PriceCharting child 仲喺度行嗰陣，喺有上限嘅 budget 入面等佢，唔再即刻拒 sweep（今日 PC 4 attempt 就係呢個形狀） |

**測試：** 84 個 suite 跑晒，**82 綠**。2 個紅係 pre-existing 嘅 Windows-only —— durability 測試要 WSL，**喺 WSL 行係綠**。唔係今日 regression。
**08-24 補：** 嗰 2 個已加 platform guard，Windows 而家 SKIP／綠（`test_daily_chain_v2_durability` 印 SKIP exit 0；`test_pc_lane_durability` 101/101，只跳 SIGTERM 嗰 5 個）。WSL 側照跑足：24 checks／107 checks。**未重跑全部 84 個 suite。**

**Migration 059 狀態 [KNOWN]：** ✅ **08-24 晚已人手 apply 落 3308**（用 chain 自己條路 `db_runtime.py migrate --only 059…`，16 statements）。實查：`cardz_schema_version` 有 `059`、`publication_outbox` 有 `superseded` 欄＋寬 unique key `uq_publication_outbox_business_type_generation`、event_key lock `uq_publication_outbox_event` 原封不動。聽朝 tick infra stage 冪等 skip。

**宣傳鏈 script 修（五個 bug，全部落 code）：**
- `x-chrome-mcp-post.ps1`：UNC temp materialization
- `x-chrome-mcp-post.js`：MCP tool-error 唔再食咗、launch-fallback 留低 breadcrumb
- `cardz_marketcap_meta_post.py`：`:visible` svg selector、`[role=menu]` composer 偵測、text 驗證改為 container-scoped

## 2. 今日 DB 清場結果（2026-08-24 執行完畢，全部有 receipt）

全部經 `operator_control.py` fail-closed 路徑寫入（capture receipt 缺一即拒），receipt 喺 `data/runtime/operator/`。

| # | 動作 | 命令 | Receipt | 前後數 |
|---|---|---|---|---|
| 1 | reject ×3（booster base page 唔係 promo reprint） | `operator-rule --action reject-wrong-printing-source --write`（v2074/9734901、v1931/9735163、v2054/8506784） | `rulings/ruling-20260824T102233198297Z-v2074-…`、`…753512Z-v1931-…`、`…102234303943Z-v2054-…` | 3 行 `applied:true`；v2074/v1931 receipt row 由 `bind-url/20260824T102145Z`+`…102146Z` 補登記 |
| 2 | v998/v112/v36 憑 `21dacd59` 新規則升 exact | `pc-identity-reverify --variant-id … --fetch-missing --write` | reverify artifact（promoted 含 v36 [SP Gold] 9277912、v112 [SP Silver] 8829097、v998 [Reverse] 5399714） | printSignatureMismatch −3、promoted +3 |
| 3 | v2011 Snorlax `parallel_code` 'cd promo'→'holo'（sha 重算＋provenance append） | `v2011_fix.py --write`（recomputedOldShaMatchesStored:true、dupeCheck:clean、rowcount==1） | `catalog-fix/catalog-fix-20260824T102630Z-v2011-parallel-code.json` | sha `776d6cc7…`→`906885944…` |
| 4 | v2251 Gecko Moria 單卡 accept ruling（pid 7419024 已驗 sp-foil） | `operator-rule --action accept --write` → `pc-identity-reverify --variant-id 2251 --write` | `rulings/ruling-20260824T103021622225Z-v2251-pricecharting.json` | **量度結果：ruling 令 row 變 `operator_ruled` hold（manual_review 凍住、受保護），reverify 唔會升 exact**——升級要等 sibling-uniqueness rule 落地＋supersede 呢條 ruling（見 §4 欠單） |
| 5 | rebuild_ledger | `python3 pipelines/discovery_ledger.py` | stdout `{"ok":true}` | ledger==catalog **1621**；active_exact 1604、identity_ambiguous 2、inactive_exact 15 |
| 6 | 殭屍 pid sweep（manual_review 坐喺已有 PC exact 嘅 variant 上） | survey → `operator-rule --action reject --write` ×30 | `rulings/ruling-20260824T10*` ×30（每張帶 before dump） | 候選 39 = 已 ruled 6 + **今次 ruled 30** + 冇 capture receipt 3（v1814/v2157/v2159，fail-closed 拒絕，做欠單）；re-survey 後 unruled 33→3 |

sibling-console inference：grep `pc_identity_discover.py` 證實**未落地**（`21dacd59` 冇包）——係 post-A3 排隊 code 工作，v2251 升級靠佢。

## 3. 時間表（08-25 → 08-28）

| 時間 | 做咩 | 驗收條件 |
|---|---|---|
| **08-25 03:30 JST** | 排程 tick 開波：infra stage 對 `schema-059` 冪等 skip（08-24 晚已人手 apply）；新 `identity-census` stage 首次埋位 | tick 唔炸；census receipt `identity-census-*.json` 出到（census 檔 08-24 20:56 啱啱 refresh 完，stage 應該回 fresh-skip——skip 都要有 receipt）；intake stage 對 v2259 冪等（08-24 20:19 已人手 --apply 收咗，聽朝應報 already_qualified 950／auto 0）[待驗] |
| ~~08-25 03:36 JST~~ | ~~bridge cron 補帳期 bridge~~ | **已證實唔存在**（08-24 晚掃齊 Hermes cron jobs.json、WSL crontab、Task Scheduler 三邊，零 03:36／bridge job）。帳期修正由下一行 Phase-1 人手 supersede 做，唔另起 cron |
| **08-25 日間** | **Phase 1：人手 supersede 一次**<br>`python3 -X utf8 pipelines/daily_chain_v2.py supersede --business-date YYYY-MM-DD --write`（default dry-run） | business date 拉返齊；舊 outbox row 標 `superseded`、新 row 帶新 generation；live `/api/health` generation 對得返新 run；**全程零 gate 鬆綁** |
| **08-26 03:30 tick** | **Phase 2：開自動對齊**——set `CARDZ_V2_AUTO_SUPERSEDE`（default OFF；未 set 之前 tick 行為同今日一模一樣） | tick 自己 supersede 一次就停（`origin='auto'` 一日一次、一世一次由 journal 自己 enforce）；archived run 嘅 manual／event-107 counter 要**帶得入**新 run——autonomy 證據唔准被 supersede 洗走 |
| **08-27 · 08-28** | **Autonomy proof：兩日唔好掂佢** | 連續兩個自然日：有 event 107、零人手介入、非 DEGRADED → `autonomous_proven` 轉 true。**期間任何一次人手介入＝counter 歸零重數** |

**平行一條（唔阻主線）：** 08-25 12:00 JST 宣傳 cron 第一個 slot——今日五個 script bug 修完之後，睇佢能唔能夠零介入一 take 出齊六步。

## 4. 風險／欠單（按優先序）

| # | 欠單 | 現狀／根因 | 下一步 |
|---|---|---|---|
| ~~P1~~ ✅ | ~~059 未落 3308~~ **08-24 晚已人手落**（`db_runtime.py migrate --only 059`，16 statements；version `059`、`superseded` 欄、寬 unique key 全部實查有，event_key lock 冇郁） | 聽朝 tick infra stage 見到有就 skip（冪等） | 淨返聽朝望一眼 tick 冇炸 |
| ~~P1~~ ✅ | ~~Census 過期~~ **08-24 20:56 JST 已人手 harvest**（52/52 set、16309 張、**957 張 ≥1000**——比舊檔多 1 張新卡過線）；7 日死線推到 **08-31** | `identity_census_stage.py` in-chain 自動 refresh 仍然一次未 fire | 08-25 tick 睇 `identity-census-*.json` receipt 證佢識自己行 |
| **P1** | 宣傳鏈 cron 一 take 未驗 | 五個 bug 已喺 code 修好，但今日六步係人手接力出街——**未證明過 cron 自己行得** | 08-25 12:00 JST 第一個 slot 睇 |
| P2 | `hold()` 唔寫 ledger | `rebuild_036.py:9324/9672` ledger 側**仍然未寫**（blocker／next_due 一格唔郁）。**措辭嗰半 08-24 已修**：held 行入自己個 `lane_held` 桶，日報講「lane hold 住、下次 reverify 淨係重評」，唔再算落「chain 自己再試」（rule 9 + test） | 補 ledger 寫入（brief 誠實度嗰半已完，唔使再等） |
| P2 | Cohort promotion 最後一里 | `qualified_identity`→`product_ready` 只有 rebuild validate 寫得，V2 冇 stage——收咗嘅新卡會停喺門口 | 藍圖 §C7；日報「有身份未出街」欄會照直報 |
| ~~P2→尾巴~~ ✅ | 殭屍 sweep **08-24 晚清賬 39/39**：最後 3 行（v1814/v2157/v2159）經 `operator_bind.py` 重用 disk sidecar 補登記 capture receipt（held_by_judge 屬預期），再 `operator-rule reject` 全 WROTE；re-survey unruled=0 | receipt 喺 `rulings/` + `bind-url/` | 完 |
| P3 | v2251 accept ruling 凍住 manual_review | ruling 令 reverify 無條件 hold（設計如此：chain 讓晒俾 operator），唔會自動升 exact | sibling-uniqueness rule 落地後 `--supersede` 呢條 ruling，俾機械路徑自己判 |
| P2 | 調度器／分類器結構修 | classifier substring 判生死、`execute_ready` 批次屏障、`clamp_manual_window` 17:00 後失效 | [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) §5 逐項執 |
| ~~P2~~ **已修 08-24** | Windows 側 durability test 紅 | `test_daily_chain_v2_durability.py` 標 WSL-only（`daily_chain_v2.py tick` 喺 `os.name=='nt'` 直接 SystemExit）；`test_pc_lane_durability.py` 只跳 `test_sigterm_writes_partial`（Windows `os.kill(SIGTERM)` = TerminateProcess，handler 唔會行） | 冇。兩個 suite 喺 Windows SKIP／綠、喺 WSL 照跑足 |
| ~~P3~~ ✅ | ~~本樹未 push~~ **08-24 晚已 push**（`7a5f188a..6dba8d8e` → origin/rebuild/036-foundation，ahead 0） | 之後新 commit 照常再 push | 完 |
| P3 | FE 樹文件分裂 | FE deploy 契約（`deploy_watch`／commit-msg hook／FE05_ROLLBACK）只喺 FE 樹；FE 樹 pointer 仲指 036 | 下次掂 FE 樹時同步 |
| ~~P3~~ ✅ | ~~phantom ps1~~ **08-24 晚根因＋修復**：index LF、working copy mixed CRLF/LF＋BOM，`autocrlf=true` 下永遠出 M；repo 內零 writer（純歷史手改）。working copy 統一 CRLF（BOM 保留），`git status` 已清、parser 0 error，**零 commit** | 如再現先考慮 `.gitattributes *.ps1 eol=crlf` pin（YAGNI，暫唔加） | 完 |

## 5. 機制參考

### 5.1 邊棵樹做咩

先記唯一三棵 Windows 樹：authority = `cardz-market-cap-fe-db-20260805`、FE 出街車 = `cardz-market-cap-037-fe04-live`、宿主／DB owner = `cardz-market-cap`。WSL release checkout 只係短命同步車，GitHub `origin/main` 係 remote ref，兩者都唔係另一棵資料 authority。

| 位置 | 角色 | 規矩 |
|---|---|---|
| `cardz-market-cap-fe-db-20260805`（本樹，`rebuild/036-foundation`） | **資料／日更真身**：V2 鏈、pipelines、receipts、DB migrations | 唔准由呢度直接 bake／`[deploy]` |
| GitHub `origin/main` | **FE 出街源頭**：release bake 只 ff GitHub main | FE fix 要上 live＝push 上 GitHub main，等下一次 bake |
| `../cardz-market-cap-037-fe04-live` | FE 出街車 worktree（= origin/main） | 認佢靠 branch==origin/main + live HTML 獨有字串 |
| `../cardz-fe-price-20260823` | FE dev 樹 | deploy 契約只喺 FE 樹有 |
| WSL `~/cardz-market-cap-release-daily` | bake／release checkout | 只睇 GitHub main；由鏈自動用 |
| `../cardz-market-cap`（舊實驗樹） | **read-only** | 唔准刪／搬——MySQL 3308 Docker compose 同 14GB volume 名由佢推導 |
| MySQL `127.0.0.1:3308`（Docker `cardz-market-cap-db-1`） | 唯一 DB，庫名 `cardz_market_cap` | 30-min SELECT cap 已 PERSIST；view 唔准 `SELECT *`／多 id IN |

### 5.2 自動鏈（V2）點行

一日一條 run（`cardz-v2:<business date>`，supersede 之後係 `<run_id>/N`），由 Task Scheduler 每 10 分鐘 tick 推進；journal 係 WSL ext4 SQLite（唯一狀態真身），每個 stage 有 lease／heartbeat／retry ladder，全程 fail-closed：**三個實跑日零錯數據出街——要麼 PUBLISHED，要麼被閘攔住停低**。

```
source 採集（gemrate 4 shard ∥ pricecharting ∥ snkrdunk；fx 已 OPERATOR_RETIRED）
  → core-contract-pre（fail-closed barrier）
  → identity phase：census（新）→ operator-apply → intake（§5.3）
      → discover lanes（PC browser@CDP9333 ∥ SNK http）→ reverify → pending
  → 10:15 JST IDENTITY_CUTOFF（identity phase 過時自動 degrade，唔擋出街）
  → activation → core-contract-post → daily-accept（寫 MySQL acceptance）
  → box（sealed sidecar）→ release（WSL bake：GitHub main + generation 對數 + [deploy] push）
  → live-confirm（讀 live /api/health 對 generation）→ brief（HERMES 身份日報，barrier phase）
  → publication_outbox（live.confirmed:<date>，promo／通知 consumer 由呢度攞事件）
```

**Publish 對數鏈：** daily-accept 產 `publicGenerationId = db3308_<sha[:16]>` → release script 要 bake 出**一模一樣**嘅 generation 先准 commit → live-confirm 讀返 live 對數 → journal `mark_publication` + outbox 落 `live.confirmed:<date>`。059 之後：一個 date 可以有多過一個 generation，但**同一 date 唔會同時有兩行都聲稱自己係 live**（supersede publish 喺同一 transaction 標舊行）。

**失敗行為：** transient 行 retry ladder；attempts 用完＝TERMINAL（stage 自己退役，唔 park 成條 run）；PARKED 有 `unpark`／`retire` 結案路徑；手動窗 floor 45 分鐘、唔准跨下一 tick、FAILED_FINAL 可 explicit 復活。

**兩套 status 字彙刻意唔統一：** rebuild checkpoint 用 `complete`；日更 chain task 用 `COMPLETED`。`market_alerts.py` 會揀最新 `status='complete'` 嘅 rebuild run 寫 ranking lineage；將舊 rebuild writer 改成 `completed`，會令較舊 mtime run 蓋過正確 ranking lineage。呢個係資料契約，唔係拼字欠單。

**操作命令（WSL）：**
```bash
python3 -X utf8 pipelines/daily_chain_v2.py status     --business-date YYYY-MM-DD
python3 -X utf8 pipelines/daily_chain_v2.py supersede  --business-date YYYY-MM-DD --write   # 原地重出街
python3 -X utf8 pipelines/daily_chain_v2.py unpark     --run-id ... --task-key ...
python3 -X utf8 pipelines/daily_chain_v2.py retire     --run-id ... --task-key ...
```
Receipts／logs：`data/runtime/daily-chain-v2/<business-date>/{receipts,logs}/`；health：`data/runtime/daily-chain-v2/health.json`。

### 5.3 新卡點入場（intake 鏈）

1. **Census**：`data/private/gemrate_brute/psa10_1000_plus.jsonl`。7 日 stale fail-closed——census 過期只准報 `censusStale`，**唔准講「冇新卡」**。`identity-census` stage（08-24 land）喺 3.5 日就自動 refresh，自己 gate mtime、budget 唔夠寧願唔跑（半截 harvest 比誠實過期更差）。
2. **intake stage**（`gemrate_identity_intake.py`，in-chain apply）：分桶 `already_qualified`／`alias`／`ruled`／`ambiguous`（要人手，零寫入）／`auto`（開 catalog variant + exact binding + member row + ledger rebuild，同一 transaction，有 ratchet 上限）。
3. **discover lanes**：PC（headed Chrome CDP **9333**，9222 唔准掂）＋ SNK（http），每 lane 每日 40 個預算。
4. **reverify**：候選經全套 gate 判 `exact`／held。**Gate 唔准鬆——證據唔夠永遠係修 input 或落 ruling。**
5. **pending → brief**：`identity_brief` stage 分四桶（已上場／已裁決／chain 自己再試／等你決定）經 HERMES 送日報，附可以 copy 嘅命令。「唔使你郁」入面再拆多一個 **`lane_held`**：lane hold 過嘅行只係下次 reverify pass 重評，唔算 chain 排咗隊再試（`hold()` 唔寫 ledger）。
6. **人手前門**（你嘅唯一日常工作面）：
   ```bash
   python -X utf8 pipelines/operator_control.py bind-url --variant-id N --url "<PC/SNK URL>" --actor daddy --write
   python -X utf8 pipelines/operator_control.py rule --source-code ... --variant-id N --external-id ID --actor daddy --action ... --reason "..." --write
   ```
   兩條 default dry-run、行真 gate、寫 receipt（`data/runtime/operator/{bind-url,rulings}/`）；批次掉入 `data/runtime/operator/bind/inbox/`，第二朝 `operator-apply` 自動抽乾。
7. **上場最後一里**：exact → cohort `qualified_identity` → `product_ready`（**V2 未有呢個 stage——§4 P2**）→ activation → daily-accept。

**最近數字 [KNOWN]（intake --apply 2026-08-24 20:19 JST，receipt `identity-intake-2026-08-24.json`）：** census **957** 張 pop≥1000（08-24 20:56 fresh harvest）；**首張全自動收卡實證**——Mega Manectric EX #158（Mega Evolution EN，pop 1003）verdict `auto`/`new_variant` → **variant 2259** 開咗（exact gemrate binding + capture receipt + member cohort `qualified_identity` + decision row，同一 transaction，DB 五路獨立驗證齊）；discovery ledger **1622==1622** 帳齊（active_exact 1604）。新卡 backlog 0。

人手裁決方法論：[docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md)。

## 6. 哲學（唔准商量嗰啲）

- **方向**：人手歸零——鏈自動跑、帳期自動對齊、新卡自動入場，判唔到嘅先嚟朝早 brief 搵你；你嘅唯一日常工作係回覆 brief 嘅 `bind-url`／`rule`。
- **Gate**：永不鬆 gate——證據唔夠＝修 input 或落 ruling；每個新 assert 要證明佢真係會 fire；**「有檢查但零 call site」當冇檢查**。
- **價格**：價＝最新真實成交；K 線只可以做長窗（≥90d）錨，永不冒充成交價。四源政策（ebay flag 由 env 管）係政策唔係 error。
- **圖源**：G10 > SNK，其他血統 hard exclude；SAMPLE 水印唔擋發佈只做排名信號；人手 reject 係 hard authority。
- **交付**：冇 receipt 唔准寫「做咗」；`autonomous_proven` 冇轉 true 之前唔准講「全自動」。

## 7. 文件地圖

**現行（讀呢啲）：**

| 檔 | 講咩 |
|---|---|
| [AGENTS.md](AGENTS.md) | 硬規矩（樹、DB、port、freeze） |
| 本檔 PROJECT_STATE.md | 現狀唯一入口＋方向＋欠單 |
| [docs/PLAN_FULL_AUTO_MODE_20260824.md](docs/PLAN_FULL_AUTO_MODE_20260824.md) | 完全體自動 MODE 藍圖（supersede 設計原文、census 自動化、驗收準則） |
| [docs/DAILY_CHAIN_V2_CUTOVER.md](docs/DAILY_CHAIN_V2_CUTOVER.md) | V2 安裝／授權／autonomy 定義 |
| [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) | 鏈實測行為＋結構欠單（§5 buckets 仲有貨） |
| [docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md) | 五條採集 lane 機制／缺陷形狀（**日更調度唔關佢事**，睇 §5.2） |
| [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md) | 加新數據源八步 |
| [docs/PROMO_CHAIN.md](docs/PROMO_CHAIN.md) | 宣傳鏈（零自動發文） |
| [docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md) | 人手身份裁決方法論 |

**封存／歷史（唔好當現狀讀）：** [docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)、[docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md)、[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)、[docs/DAILY_CHAIN_AUTONOMY.md](docs/DAILY_CHAIN_AUTONOMY.md)（已被 V2 取代）、[docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md](docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md)、`docs/handoff/`、postmortem 三份。Sealed 現行操作改讀 [docs/SEALED_OPS.md](docs/SEALED_OPS.md)。
