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
| 本樹 | `rebuild/036-foundation`，HEAD `911397c5`，**ahead of origin 89 commits（未 push）** | `git rev-list --count` [KNOWN] |

## 1. 今日（2026-08-24）落咗咩

**四個 commit 落 `rebuild/036-foundation`（`bf1aa457..911397c5`，未 push）：**

| commit | 做咗咩 |
|---|---|
| `e5542344` | **identity-brief 誠實度修**：喺 ready／exact 短路**之前**先讀未答嘅 reverify hold（08-24 audit 判 `needsYou=0` 係算式恆等式，唔係觀察）；lane objection 連 artifact 一齊出；bridge `CARDZ_V2_STATE_DB`，brief 而家讀返 run 自己嗰本 journal |
| `21dacd59` | **pc-identity 規則修**：`snk`／`snk_psa10` 數 exact binding 前先 fold 成同一個 provider（v1020／v1040 嘅 `multiple_exact_bindings` 係呢個假陽性）；`rh`／`spc` printing 而家賺得返自己嘅 `[Reverse]`／`[SP <metal>]` 頁；`_gemrate_printing_sha` 唔再含卡名 |
| `634c1d7f` | **daily-chain-v2 supersede**：一個 business date 可以用 `/N` run generation **原地再出街**；加 `identity-census` stage；watchdog 唔再將已 superseded 嘅日當「做完」。附 migration **059**——`publication_outbox` 唯一鎖由 `(business_date,event_type)` 放寬做 `(business_date,event_type,generation_id)` + `superseded` reader flag。**`event_key` 鎖冇郁**，所以普通重跑一樣照撞，唔會靜靜出兩次 |
| `911397c5` | **collect guard**：撞到 9333 PriceCharting child 仲喺度行嗰陣，喺有上限嘅 budget 入面等佢，唔再即刻拒 sweep（今日 PC 4 attempt 就係呢個形狀） |

**測試：** 84 個 suite 跑晒，**82 綠**。2 個紅係 pre-existing 嘅 Windows-only —— durability 測試要 WSL，**喺 WSL 行係綠（23 checks）**。唔係今日 regression。

**Migration 059 狀態 [KNOWN]：** 檔喺 repo（`pipelines/migrations/059_daily_chain_v2_publication_supersede.mysql.sql`），**未 apply 落 MySQL 3308**。唔使人手落——`daily_chain_v2_contract.py:v2_schema_capabilities` 由檔名推導出 `schema-059` capability，infra stage 下個 tick（08-25 03:30 JST）自動 apply，orchestrator 零改動。SQL 本身 additive + idempotent，半途死可以重播。

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
| **08-25 03:30 JST** | 排程 tick 開波：infra stage 自動 apply `schema-059`；新 `identity-census` stage 首次埋位 | `cardz_schema_version` 有 `059`；`publication_outbox` 見到 `superseded` 欄同寬 unique key；census receipt 出到（census 檔停喺 08-20 23:17，已過 3.5 日 refresh 線）[待驗] |
| 08-25 03:36 JST | bridge cron（主線今日安排）補一手帳期 bridge | ⚠ 本檔掃 Task Scheduler + Hermes cron register 搵唔到呢個 job（唯一 03:3x 係 V2 task 03:30 + PT10M）。**起飛前要核實佢真係註冊咗**，唔好靠記憶 |
| **08-25 日間** | **Phase 1：人手 supersede 一次**<br>`python3 -X utf8 pipelines/daily_chain_v2.py supersede --business-date YYYY-MM-DD --write`（default dry-run） | business date 拉返齊；舊 outbox row 標 `superseded`、新 row 帶新 generation；live `/api/health` generation 對得返新 run；**全程零 gate 鬆綁** |
| **08-26 03:30 tick** | **Phase 2：開自動對齊**——set `CARDZ_V2_AUTO_SUPERSEDE`（default OFF；未 set 之前 tick 行為同今日一模一樣） | tick 自己 supersede 一次就停（`origin='auto'` 一日一次、一世一次由 journal 自己 enforce）；archived run 嘅 manual／event-107 counter 要**帶得入**新 run——autonomy 證據唔准被 supersede 洗走 |
| **08-27 · 08-28** | **Autonomy proof：兩日唔好掂佢** | 連續兩個自然日：有 event 107、零人手介入、非 DEGRADED → `autonomous_proven` 轉 true。**期間任何一次人手介入＝counter 歸零重數** |

**平行一條（唔阻主線）：** 08-25 12:00 JST 宣傳 cron 第一個 slot——今日五個 script bug 修完之後，睇佢能唔能夠零介入一 take 出齊六步。

## 4. 風險／欠單（按優先序）

| # | 欠單 | 現狀／根因 | 下一步 |
|---|---|---|---|
| **P1** | 059 未落 3308 | 檔喺 repo，DB 未有 `superseded` 欄／寬 unique | 08-25 03:30 tick 自動 apply。**apply 唔到＝成條 supersede 路行唔通**，要即刻人手落 |
| **P1** | Census 自動 refresh 未實跑過 | `identity_census_stage.py` 今日先 in-chain，一次都未 fire；census 檔停喺 **08-20 23:17**，**08-27 23:17** 撞 7 日 intake fail-closed 線 | 08-25 tick 睇 receipt；唔 fire 就人手 harvest 續命，唔好等到撞線 |
| **P1** | 宣傳鏈 cron 一 take 未驗 | 五個 bug 已喺 code 修好，但今日六步係人手接力出街——**未證明過 cron 自己行得** | 08-25 12:00 JST 第一個 slot 睇 |
| P2 | `hold()` 唔寫 ledger | `rebuild_036.py:9324/9672`；brief 對 hold 卡講「chain 會再試」對呢批仍然係**假**。`e5542344` 只修咗 brief 讀 hold 嘅次序，冇修 ledger | 補 ledger 寫入，或者改 brief 措辭講返真相 |
| P2 | Cohort promotion 最後一里 | `qualified_identity`→`product_ready` 只有 rebuild validate 寫得，V2 冇 stage——收咗嘅新卡會停喺門口 | 藍圖 §C7；日報「有身份未出街」欄會照直報 |
| P2→尾巴 | 殭屍 pid sweep 已做（見 §2 #6）：30 行 ruled，剩 **3 行冇 capture receipt**（v1814/6235246、v2157/8508439、v2159/8508412）operator-rule fail-closed 拒絕 | 呢 3 行係 08-22 discover 留低、冇 capture；rule 佢要先 CDP 9333 補 capture（為死 proposal 燒 fetch，唔急） | 下次開 9333 順手補；或者等佢哋跟 sibling-rule 批次一齊清 |
| P3 | v2251 accept ruling 凍住 manual_review | ruling 令 reverify 無條件 hold（設計如此：chain 讓晒俾 operator），唔會自動升 exact | sibling-uniqueness rule 落地後 `--supersede` 呢條 ruling，俾機械路徑自己判 |
| P2 | 調度器／分類器結構修 | classifier substring 判生死、`execute_ready` 批次屏障、`clamp_manual_window` 17:00 後失效 | [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) §5 逐項執 |
| P2 | Windows 側 durability test 紅 | 2 個 suite 要 WSL（WSL 綠，23 checks）；Windows 紅係 pre-existing | 標 WSL-only 或補 platform skip，唔好日日靠人記住「呢兩條唔算」 |
| P3 | 本樹未 push | ahead of origin **89 commits** | 揀時機 push（唔阻日更） |
| P3 | FE 樹文件分裂 | FE deploy 契約（`deploy_watch`／commit-msg hook／FE05_ROLLBACK）只喺 FE 樹；FE 樹 pointer 仲指 036 | 下次掂 FE 樹時同步 |
| P3 | `scripts/daily_public_release.ps1` phantom modification | working tree 長期有 `M`，**唔准 commit** | 查根因（邊個 writer 改咗佢），未查到之前保持 uncommitted |

## 5. 機制參考

### 5.1 邊棵樹做咩

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
5. **pending → brief**：`identity_brief` stage 分四桶（已上場／已裁決／chain 自己再試／等你決定）經 HERMES 送日報，附可以 copy 嘅命令。
6. **人手前門**（你嘅唯一日常工作面）：
   ```bash
   python -X utf8 pipelines/operator_control.py bind-url --variant-id N --url "<PC/SNK URL>" --actor daddy --write
   python -X utf8 pipelines/operator_control.py rule --source-code ... --variant-id N --external-id ID --actor daddy --action ... --reason "..." --write
   ```
   兩條 default dry-run、行真 gate、寫 receipt（`data/runtime/operator/{bind-url,rulings}/`）；批次掉入 `data/runtime/operator/bind/inbox/`，第二朝 `operator-apply` 自動抽乾。
7. **上場最後一里**：exact → cohort `qualified_identity` → `product_ready`（**V2 未有呢個 stage——§4 P2**）→ activation → daily-accept。

**最近數字 [KNOWN]（intake receipt 2026-08-25）：** census 956 張 pop≥1000；新卡 backlog **0**（alias 7、already_qualified 949）；discovery ledger 1621 條帳齊（active_exact 1602）。

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

**封存／歷史（唔好當現狀讀）：** [docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)、[docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md)、[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)、[docs/DAILY_CHAIN_AUTONOMY.md](docs/DAILY_CHAIN_AUTONOMY.md)（已被 V2 取代）、[docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md](docs/archive/PLAN_036_CLOSEOUT_archived-20260824.md)、[docs/SEALED_OPS.md](docs/SEALED_OPS.md)（⚠ 命令指住 read-only 舊樹，唔好照跟）、`docs/handoff/`、postmortem 三份。
