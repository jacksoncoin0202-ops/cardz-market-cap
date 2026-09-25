# PROJECT_STATE — CARDZ Market Cap（2026-08-25 handoff 執行中）

> ## 一條線：先收好 handoff 修復，再用自然排程證明全自動
>
> **08-25 17:00 後**先將隔離 staging 嘅 handoff 修復 fast-forward 落 authority，跑一次完整 Windows＋WSL 驗證並重裝四個 managed tasks → **08-26 03:30** 由自然 event 107 開第一條完整 E2E → live generation 對數 → 17:45 repo promo pack → Hermes 六渠道 receipts → 再攞第二個連續自然日，直到 `autonomous_proven = true`。
>
> **08-25 唔係綠日。** 舊 generation 仍正常 live，但 current journal run `/2` 已 `FAILED_FINAL`；唔准用 live 正常掩蓋當日 scheduled E2E 失敗。未有下一個自然日完整 receipts 前，唔准講「已全自動」。

> **呢份係現狀唯一入口。** 舊逐代記錄（025–037 開代史）已封存：[docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)。
> 讀嘢次序：[AGENTS.md](AGENTS.md)（硬規矩）→ 本檔（現狀）→ 各專題檔（§7 文件地圖）。
> **文件同 code 衝突時 code 贏**，發現即修文件。日程／cadence 真相永遠睇 `Get-ScheduledTask`，唔好信文件記憶。

### 2026-09-25 合併：FE／release 線（`origin/main`）併入同一條 code 線

> 目的：一條 code 線，修復唔再散落兩邊。合併喺隔離樹 `cardz-market-cap-restructure-20260925`（branch `consolidate/main-merge-20260925`）做；**落 authority 樹同出街之前唔當 live**——生效與否以 authority `git log` 同 live `/api/health` 為準。未落之前 live 仍然由 authority 樹 `rebuild/036-foundation`（資料）同 `origin/main`（FE／release）各自出。

- [KNOWN] 兩邊：`restructure/20260925`（資料／V2 鏈線，`37dce213`）＋ `origin/main`（FE／release 線，`3759d190`），merge base `5b0f8343`（2026-08-13）。
- 權威分工照舊：pipelines／V2 鏈／collectors／box（sealed）採集／identity／鏈測試跟資料線；`apps/web` 同 release／bake／validate／public-surface 工具跟 `origin/main`（即 WSL `~/cardz-market-cap-release-daily` 實際跑嗰份）。
- [KNOWN] FE 現況（`origin/main`）：presentation `FE05`、fallback `FE04`（`apps/web/src/lib/product-generation.ts`）。FE04 退路錨 tag `fe04-live` = `c622d741`，一句 `pwsh -NoProfile -File scripts\fe05_rollback.ps1`（[docs/FE05_ROLLBACK.md](docs/FE05_ROLLBACK.md)）。FE 設計 gate：`.claude/agents/fe-design-review.md` ＋ [apps/web/DESIGN.md](apps/web/DESIGN.md)。
- [KNOWN] Release 現況（`origin/main`）：daily release commit subject `release: daily CARDZ 037 FE04 $generation [deploy]`；最新 `3759d190`（2026-09-25 13:33 JST，`db3308_d3601e743fc3d8e9`）。`fabefa9b` 起 seed-snapshot minified、release 超過 95 MiB 即停；`4d4191d7` 起 K 線唔入公開 snapshot（日線／變幅只由真成交嚟）。
- Deploy 契約而家同資料線同一份 [AGENTS.md](AGENTS.md)：deploy literal 一入 message 就要喺 subject（規矩 17；`scripts/githooks/commit-msg`，`node scripts/install_githooks.mjs` 裝，`scripts/test-deploy-tag-contract.mjs` 守）；push 完必跑 `deploy_watch.ps1`（規矩 20）、一律 `pwsh`（規矩 21）。§4 P3「FE 樹文件分裂」由呢次合併收。
- 037／FE04 開代（2026-08-14）當日狀態：`origin/main` 版本檔嘅「037 / FE04 — 現狀」節已喺 [docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)；內頁／BOX 細節（`.detail-art`、askFloor `wide-metric`、`/box/[id]` Product + BreadcrumbList）喺 [docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)。

### 2026-08-26 宣傳鏈 cutover

- [KNOWN] **08-28 data→Live 已完成**：03:30 natural event 107 啟動 `cardz-v2:2026-08-28`，全程 scheduler provenance capture 正常；PriceCharting 首次 attempt 被 tick deadline interrupt，下一個自然 tick 自動續成。05:34 release gate 因 `test_pc_master_ball_rebind.py` stale synonym assertion fail-closed；只更新測試期望、targeted regression 通過後 guarded unpark release。06:08 `PUBLISHED`，generation `db3308_8325adf7c4ffe094` 與 Live confirmed、activeCount 1604；因 unpark，final origin `manual`、intervention `1`、`proven_autonomous=0`，唔作純自動綠日。
- [KNOWN] **08-28 全量身份母體已回到正確量級**：daily identity brief 計出 POP≥1000 母體 **1,655**，其中出街 1,373、有身份未出街 164、等人綁 99、自動研究 19；呢個新全量結果取代 08-27 不完整 extraction 嘅 1,153，亦解釋點解用戶判斷 957／1,153 一定錯。
- [KNOWN] **08-28 Hermes 六渠道 receipts 已收齊**：X EN／ZH、Instagram EN／ZH、Threads EN／ZH 全部 `ok=true`，同屬 generation `db3308_8325adf7c4ffe094`；四條 Meta live 圖均實測 `2160×2700`。14:18 JST 自然 no-agent tick 收口，14:30、14:45 後續 ticks 均 silent dedupe，cron 回復 `ok`、failure streak `0`。今日途中先修正被改成四渠道嘅 gate／runner，再固化 9222 單一 Threads tab、直達 Instagram bridge 嘅 deterministic account routing，所以 **08-28 社交亦唔算零介入 one-click 證明**；下一個自然 business date 仍要重證。
- [KNOWN] **08-28 repo promo pack 已由 17:45:01 自然 Task Scheduler run 完成**：outer task result `0`；scheduler receipt `data/runtime/promo/scheduler/2026-08-28.json` outcome `ok`、exitCode `0`、generation `db3308_8325adf7c4ffe094`，同 Live／Hermes 六渠道 receipts 一致。pack 有 `brief.json`、X／Instagram／Threads EN+ZH 六份 copy，同六份 `built`、`posted=false` action receipts；repo consumer 冇 browser、冇 public publish。
- [KNOWN] **08-27 data/live 自然 E2E 已完成並 live 對數**：03:30 Task Scheduler event 107 啟動 `cardz-v2:2026-08-27`；durable retry 自動處理 PriceCharting／checkpoint repair，38/38 tasks `COMPLETED`，05:24 JST `PUBLISHED`。run generation `db3308_9e1f8aeeac299789` 與 live `/api/health` 完全一致，live `status=ok`、`generatedAt=2026-08-26T20:20:55.736Z`。但 final observer 正確保留 journal 現況：10 個經 `wscript.exe` 入場嘅自然 repetition ticks 因 launcher 單次讀唔到已存在嘅 event 107，被記成 `origin.manual`，所以本日 `manual_intervention_count=10`、`proven_autonomous=0`；唔准用早段 snapshot 嘅 intervention 0 冒充全日證明。
- [KNOWN] **08-27 Hermes 六渠道 receipts 已收齊**：X EN／ZH、Instagram EN／ZH、Threads EN／ZH 全部 `ok=true`、`dry_run=false`、generation `db3308_9e1f8aeeac299789`、live media `2160×2700`。12:00–12:45 自然 ticks 先後暴露 missing top100、9222 短暫 probe、X active-account 判定；12:46 用戶回覆 Hermes「fix it」後，Hermes 暫停 cron 並手動修復／補出五路，13:30 恢復後自然 no-agent tick 補齊 Threads EN，execution `e5873c10ffc9463c820abf7a63d8afa1` completed。因中途有人手「fix it」，08-27 社交部分唔算純自然 one-click 證明。
- [KNOWN] **08-27 repo promo pack 已由 17:45:01 自然 Task Scheduler run 完成**：outer task result `0`；inner scheduler receipt contract `cardz-promo-pack-scheduled-v1`、outcome `ok`、exitCode `0`、generation `db3308_9e1f8aeeac299789`，同 live／六渠道 receipts 一致。pack 產生 `brief.json` + X／Instagram／Threads EN+ZH 六份 copy，同六份 dry-run `built` action receipts；repo consumer 全程只 build，冇 browser／冇 publish。08-27 data→live→promo pack→六渠道結果全部齊，但因社交中途有人手「fix it」，整日仍唔當純自然 one-click 綠日；要下一個自然 business date 零介入重證。
- [KNOWN] **08-27 provenance defect 已作最小修正**：Windows Operational log 證實被誤判 tick 其實各自有 exact `\CARDZ-Marketcap-Daily-V2` event 107。launcher 現只喺 production `wscript.exe` 父程序形狀下，最多 11 次、每次 500ms 重讀同一 event-107 證據；direct CLI 仍只讀一次，最後冇 exact event 仍 fail-closed 判 manual。PowerShell AST、`git diff --check`、WSL `scripts/test_daily_chain_v2.py` 已一次通過；冇回寫／洗走 08-27 journal，唯一接受證據係 08-28 自然 run。
- [KNOWN] 08-26 live bake 已完成；repo promo pack 已產生，但舊公開路徑造成混合 receipts：IG 英／中完全冇跑，Threads EN 有 3 次 `audience_mismatch` 點擊，Threads ZH 未成功。呢日唔當六渠道綠日。
- [KNOWN] 由下一個自然 business date **2026-08-27** 起，Hermes gate 固定以六個 primary receipts 收口：X EN／ZH、Instagram EN／ZH、Threads EN／ZH；WhatsApp／fork／site 唔再入 daily completion gate。
- [KNOWN] 公開 browser ownership 已收窄到 Hermes poster + 隔離持久化 **CDP 9222**。唔准用 Codex Chrome、日常 Chrome、9333，亦唔准再用 repo `promo_post.py --confirm`；repo task 只砌六渠道 pack。
- [KNOWN] IG daily block 已移除；IG 接受平台原生 4:5 圖、Original crop、IG→Threads switch off，08-27 live receipt 實測 2160×2700。Threads 用目標 handle 驗帳，唔再用 community/audience 字樣推斷。
- [KNOWN] 08-26 舊 Grok cron agent 無視 `IDLE`、嘗試公開操作並兩度自行 pause；現已改為 `cardz_marketcap_six_channel_daily.sh` deterministic **no-agent** job，恢復 active。`x-chrome-mcp-post.js` 已禁止 9222 不可用時另開 browser；只可 fail-closed。2026-08-27 起 gate 必須收齊 X／Instagram／Threads EN+ZH 六份 receipts。
- [KNOWN] 15:15 自然 no-agent run 暴露 WSL gate 未真正更新；9222 profile 隨後用主 Chrome `Default` 嘅一致 SQLite snapshot 重建並以原 port 重啟，主 Chrome／9333 全程未停。16:00 後再發現 posters 嘅 exit-7 仍會召喚 Hermes agent，導致 agent 覆蓋 gate、direct replay 及 Threads wrong-account loop；現已喺 shell 最前加入 2026-08-27 hard activation guard，並強制 `CARDZ_ACCOUNT_ESCALATION=0`。job 已恢復 active，今日後續 tick 只可 no-op。
- [KNOWN] 17:24–19:12 反覆覆蓋嘅實際 writer 已定位：另一個仍在運行嘅 Codex task `01a03c5b-bd7f-72a2-a46c-6d0d44e4f6ff`（標題「跟進 TokenMarketCap 自動鏈」；cwd `C:\Users\jackson0202\Documents\Codex\2026-08-26\tokenmarketcap-clark-block`）越出 TokenMarketCap／Clark scope，將 CARDZ Hermes job、skill、gate、runner 改成四渠道並引入 9224。19:56 已先 pause `bc4615fdd701`；20:00 該 task 明確確認停止所有 CARDZ／Hermes public action並轉 idle。CARDZ canonical skill、runtime gate／runner、Meta poster、Chrome watchdog 同 cron 已重建成 2026-08-27 起 X／Instagram／Threads EN+ZH 六渠道，只准隔離持久化 9222；今日 mixed receipts 不作綠日，亦冇刪任何已發公開貼文。

## 0. 一眼現狀

| 項 | 數值 | 來源 |
|---|---|---|
| Live | `https://app.cardzmarketcap.com` · status `ok` · generation `db3308_8325adf7c4ffe094` · generatedAt `2026-08-27T21:03:59.315Z` | V2 `live.confirmed` 2026-08-28 06:08 JST [KNOWN] |
| 版本 | product **037** · presentation **FE05**（fallback FE04）· BOX `/box` 307／275／307 | [KNOWN] |
| 日更排程 | `CARDZ-Marketcap-Daily-V2` **Ready**，03:30–17:00 每 10 分鐘；04:20 tick result **0**。Watchdog／Promo Ready；Observer 03:25 result **1** | Task Scheduler 2026-08-25 04:30 JST [KNOWN] |
| 今日 run | `cardz-v2:2026-08-28` **PUBLISHED**；Live generation `db3308_8325adf7c4ffe094`；final origin `manual`、intervention `1`、`proven_autonomous=0`（release test drift 修正後 guarded unpark） | observer `2026-08-28/report.md` + `live.confirmed` [KNOWN] |
| 自動化證明 | `autonomous_proven = false` | health.json [KNOWN] |
| ⚠ 今日失敗根因 | supersede 重建出同一 generation，`live-confirm` 燒 6 次後撞 `uq_publication_outbox_business_type_generation`；新 code 會第一次就用明確錯誤拒絕，唔再 raw 1062／重試 | journal terminal task + staged `daily_chain_v2_db.py` [KNOWN] |
| 宣傳鏈 | 2026-08-28 Hermes 六渠道 6/6 public receipts；17:45 repo promo pack outcome `ok`、六份 `built` receipts；因日內修鏈，仍待下一自然日零介入重證 | [KNOWN] |
| Code | authority `rebuild/036-foundation` HEAD `1cbbe85f` tracked clean；隔離 `codex/cardz-handoff-complete` implementation tip `b964893d`，11 implementation commits ahead（另加本狀態文件 commit），17:00 前未落 authority | `git status/log` 2026-08-25 04:30 JST [KNOWN] |

**Handoff 11 commits（staged，未 apply）[KNOWN]：** `53dc9531` 歷史 FX freeze＋060；`5f14ba53` reliability；`a2476ca2` shared registries；`4e1154af` executable guards；`99ca63cd` module split／recovery interlocks；`8d6e1fce` legacy retirement dependency；`5338ed4a` scheduled auto-align env；`9e23440a` promo inner receipt；`9c923721` docs；`faf9fb8d` FX unknowns；`b964893d` observer `/N` resolution。19 棵已確認 clean worktree 已移除，9 棵有效／dirty 樹保留；branch／commit 冇刪。

## 1. 前日記錄（2026-08-24）

**四個 commit 已落 `rebuild/036-foundation`（`bf1aa457..911397c5`；其後 08-24 已 push）：**

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
| **08-25 03:30 JST** | 排程有 event 107 啟動，但 current `/2` 之前已 terminal；之後 tick 全部 rc0/no-op | **唔合格**：current run `FAILED_FINAL`、event107 counter 仍 0；唔算自然 E2E [KNOWN] |
| ~~08-25 03:36 JST~~ | ~~bridge cron 補帳期 bridge~~ | **已證實唔存在**（08-24 晚掃齊 Hermes cron jobs.json、WSL crontab、Task Scheduler 三邊，零 03:36／bridge job）。帳期修正由下一行 Phase-1 人手 supersede 做，唔另起 cron |
| **08-25 Phase 1** | 人手 supersede 已執行，建立 `/2` | **失敗但 fail-closed**：rebuild 同舊 live bytes 相同，release 完成後 live-confirm duplicate generation terminal；live 冇錯數據出街 [KNOWN] |
| **08-25 17:00 後** | fast-forward 11 個 staged commits；一次完整 Windows／WSL／PowerShell tests；`-Apply` 重裝 daily/watchdog/promo/observer；確認 XML 後 unregister 三個 scoped legacy tasks | authority clean、全套測試綠、四 task definitions 指向 authority、新 observer 帶 `--notify` 並識得 resolve `/N` |
| **08-26 03:30 tick** | 第一個修復後自然 E2E；scheduled launcher 明文用 `env CARDZ_V2_AUTO_SUPERSEDE=1` 送入 WSL | event107、零 manual、060 apply、所有 core stage、release、live-confirm、live generation 全部 receipts 對齊 |
| **08-26 17:45 起** | repo promo pack + Hermes 六渠道真 browser chain | scheduler inner receipt exit 0、pack generation==live；X／IG／Threads EN/ZH 六份 success receipt，gate DONE |
| **之後第二個連續自然日** | 唔人手介入；只讀監察 | 連續兩個自然日 PUBLISHED、event107>0、manual=0、非 DEGRADED → `autonomous_proven=true` |

**平行一條：** 08-25 12:00–20:00 Hermes 會照舊 generation 嘗試；呢批只當 production 觀察，唔代替 08-26 新 generation E2E 證據。

## 4. 風險／欠單（按優先序）

| # | 欠單 | 現狀／根因 | 下一步 |
|---|---|---|---|
| ~~P1~~ ✅ | ~~059 未落 3308~~ **08-24 晚已人手落**（`db_runtime.py migrate --only 059`，16 statements；version `059`、`superseded` 欄、寬 unique key 全部實查有，event_key lock 冇郁） | tick infra stage 見到有就 skip（冪等）；08-26 自然 E2E 收 receipt |
| ~~P1~~ ✅ | ~~Census 過期~~ **08-24 20:56 JST 已人手 harvest**（52/52 set、16309 張、**957 張 ≥1000**——比舊檔多 1 張新卡過線）；7 日死線推到 **08-31** | `identity_census_stage.py` in-chain 自動 refresh 仍待自然 E2E receipt |
| **P1** | 宣傳鏈零介入一 take 未驗 | 08-28 六份 receipts 已齊，後續 tick silent dedupe；但今日途中修過六渠道 gate／runner 同 9222 Meta account routing，唔算自然零介入證明 | 下一個自然 business date 由 live generation 到六 receipts 全程只讀監察 |
| **P1** | Observer 排程 03:25 result 1，舊 observer 又只認 base ID | Task Scheduler action 由 03:25 行到 04:04 回 1；隔離 VBS smoke 證明 wrapper／quoting 可啟動，但 base request 對 current `/2` 會假報 `RUN_NOT_STARTED` | staged `b964893d` 動態 resolve 同日最高 `/N`，live/report 同時留 requested/resolved ID；17:00 後重裝，08-26 自然跑驗收 |
| ~~P1~~ ✅ | ~~`snk_grade` 會否同樣受歷史 FX 重算影響~~ | 唯一 writer 已封存於 `archive/pipelines/g10_snkrdunk_grades_ingest.py`，輸入 API 明文要求 USD；非 USD 行直接拒絕，從未做 JPY→USD 換算。全 repo 冇 active `snk_grade` writer/call site | 唔屬於 060 修復面，唔改 |
| ~~P1~~ ✅ | ~~08-03…08-16 FX 空窗成因未知~~ | 3308 `frankfurter` ingest 實查：08-02 後下一次 loader 係 08-17，中間零 run；08-02 run 嘅 `started_at` 亦冇被 last-good reuse 重播。舊 `CARDZ-Market-Cap-Daily` 最後只跑到 07-31，故空窗係 loader/scheduler 冇執行，唔係 72h reuse | `fx_asof` 對空窗用「不晚於市場日」最新已知點並寫 `fx_rate_as_of`；060 唔捏造歷史 backfill |
| P2 | `hold()` 唔寫 ledger | `rebuild_036.py:9324/9672` ledger 側**仍然未寫**（blocker／next_due 一格唔郁）。**措辭嗰半 08-24 已修**：held 行入自己個 `lane_held` 桶，日報講「lane hold 住、下次 reverify 淨係重評」，唔再算落「chain 自己再試」（rule 9 + test） | 補 ledger 寫入（brief 誠實度嗰半已完，唔使再等） |
| P2 | Cohort promotion 最後一里 | `qualified_identity`→`product_ready` 只有 rebuild validate 寫得，V2 冇 stage——收咗嘅新卡會停喺門口 | 藍圖 §C7；日報「有身份未出街」欄會照直報 |
| ~~P2→尾巴~~ ✅ | 殭屍 sweep **08-24 晚清賬 39/39**：最後 3 行（v1814/v2157/v2159）經 `operator_bind.py` 重用 disk sidecar 補登記 capture receipt（held_by_judge 屬預期），再 `operator-rule reject` 全 WROTE；re-survey unruled=0 | receipt 喺 `rulings/` + `bind-url/` | 完 |
| P3 | v2251 accept ruling 凍住 manual_review | ruling 令 reverify 無條件 hold（設計如此：chain 讓晒俾 operator），唔會自動升 exact | sibling-uniqueness rule 落地後 `--supersede` 呢條 ruling，俾機械路徑自己判 |
| ~~P2~~ **staged** | 調度器／分類器結構修 | structured test verdict、`execute_ready`／recovery interlocks、manual window／durability guards 已分批落 `4e1154af`、`99ca63cd` 等 staging commits | 17:00 後完整測試＋自然 E2E 未過前唔標 done |
| ~~P2~~ **已修 08-24** | Windows 側 durability test 紅 | `test_daily_chain_v2_durability.py` 標 WSL-only（`daily_chain_v2.py tick` 喺 `os.name=='nt'` 直接 SystemExit）；`test_pc_lane_durability.py` 只跳 `test_sigterm_writes_partial`（Windows `os.kill(SIGTERM)` = TerminateProcess，handler 唔會行） | 冇。兩個 suite 喺 Windows SKIP／綠、喺 WSL 照跑足 |
| ~~P3~~ ✅ | ~~本樹未 push~~ **08-24 晚已 push**（`7a5f188a..6dba8d8e` → origin/rebuild/036-foundation，ahead 0） | 之後新 commit 照常再 push | 完 |
| P3 | FE 樹文件分裂 | FE deploy 契約（`deploy_watch`／commit-msg hook／FE05_ROLLBACK）只喺 FE 樹；FE 樹 pointer 仲指 036 | 下次掂 FE 樹時同步；**2026-09-25**：`consolidate/main-merge-20260925` 將 FE 契約併入同一條線（落 authority 前未生效，見頂部「2026-09-25 合併」） |
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

**最近數字 [KNOWN]：** 2026-08-28 自然 daily identity census／brief 已計出 `PSA 10 POP >= 1000` 全量母體 **1,655**：出街 1,373、有身份未出街 164、等人綁 99、自動研究 19，四組精確加總 1,655。呢個結果取代 08-27 僅 1,153 rows 嘅不完整 extraction，亦唔准再沿用 08-24 嘅 957 當現況。08-24 intake 首張全自動收卡實證仍有效：Mega Manectric EX #158（Mega Evolution EN，pop 1003）verdict `auto`/`new_variant` → **variant 2259**；當時 discovery ledger **1622==1622**（active_exact 1604），只係歷史 snapshot，唔係今日母體數。

人手裁決方法論：[docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md)。認印刷（SNK `{SET} EN`、`:1ED`、CLC vs WOTC）：[docs/TCG_PRINTING_IDENTITY.md](docs/TCG_PRINTING_IDENTITY.md)。

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
| [docs/FE05_ROLLBACK.md](docs/FE05_ROLLBACK.md) | FE05→FE04 一句退路＋`deploy_watch` exit code 判讀（`origin/main` 線） |
| [apps/web/DESIGN.md](apps/web/DESIGN.md) | FE05 設計系統＋FE 出街 log（`origin/main` 線） |
| [docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md) | 人手身份裁決方法論（leftover-5 舊案） |
| [docs/TCG_PRINTING_IDENTITY.md](docs/TCG_PRINTING_IDENTITY.md) | TCG 印刷身份活頁（認卡／SNK 標題；唔係工程） |

**封存／歷史（唔好當現狀讀）：** [docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)、[docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md)、[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)、[docs/DAILY_CHAIN_AUTONOMY.md](docs/DAILY_CHAIN_AUTONOMY.md)（已被 V2 取代）、[docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md](docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md)、`docs/handoff/`、postmortem 三份。Sealed 現行操作改讀 [docs/SEALED_OPS.md](docs/SEALED_OPS.md)。
