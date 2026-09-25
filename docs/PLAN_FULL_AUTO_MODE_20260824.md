# 完全體自動 MODE — 藍圖（2026-08-24）

> 狀態：**計劃書，未實施。** 所有 [KNOWN] 由 code 偵察（檔:行）實證；設計判斷標 [INFERRED]。
> 實施分級喺 §6：第 0 級唔使問；改 journal／MySQL schema／orchestrator 一律**要 daddy 開聲**。

## 0. 目標一句話

人手歸零：鏈自動跑、**帳期自動對齊**、新卡自動入場、判唔到嘅先嚟朝早 brief 搵你。你嘅唯一日常工作＝回覆 brief 嘅 `bind-url`／`rule` 命令。

## 1. 而家差咩（gap 總表）

| 級 | Gap | 一句根因 |
|---|---|---|
| P0 | 同日 supersede rerun | 一 date 一 run 係 journal 寫死；PUBLISHED 後 tick 只 no-op → 早跑永遠食咗聽日 slot，帳期錯一日冇得自愈 |
| P1 | Census 自動 refresh | GemRate brute harvest 人手跑；7 日 stale intake fail-closed（設計正確，但冇人跑就永遠 stale） |
| P1 | Brief 誠實度 | needsYou=0 要證明唔係靠母體收窄達成（audit 判詞見 §5） |
| P2 | 身份殘留收尾 | 4 張 ledger ambiguous、22 printSig、5 productMismatch、6 殭屍 pid（audit 判詞見 §5） |
| P2 | 調度器／分類器結構修 | classifier substring 判生死；execute_ready 批次屏障；clamp_manual_window 17:00 後失效（audit §5 buckets） |
| P2 | Cohort promotion 最後一里 | `qualified_identity`→`product_ready` 只有 rebuild validate 寫得；V2 冇 stage，新卡入到場都停喺門口 |
| P3 | autonomy proof | 每次人手／bridge 都 reset counter；supersede 落地 + 兩個自然日唔掂就自動達成 |

## 2. P0：同日 supersede rerun（設計，偵察已完成）

### 2.1 現狀機制 [KNOWN，全部檔:行實證]

- Run 創建唯一路徑 `Journal.ensure_run`（`pipelines/daily_chain_v2_journal.py:301-336`）：`run_id = cardz-v2:<date>`，`INSERT … ON CONFLICT(business_date) DO NOTHING`，row 屬於第二個 run_id 即刻 raise。`chain_run.business_date TEXT NOT NULL UNIQUE`（journal:145）。
- Tick「已出街即 no-op」：`daily_chain_v2.py:3231-3236`（status ∈ {PUBLISHED, PUBLISHED_DEGRADED, FAILED_FINAL, ABORTED} → 只回 summary）。呢個 set 係 literal，**唔係** journal:42 嘅 `RUN_SUCCESS_STATES`——兩處都要掂。
- Tick 揀 date：`daily_chain_v2.py:3515` `day = args.business_date or datetime.now(JST).date()`。
- `--run-label` 只係 rehearsal 岔道：另開 journal 檔（`rehearsal_state_path`，:280-292）＋另一 runtime dir `<date>-<label>/`（:853-857），**三重硬閘禁止 publish**（:3553-3562、:1922-1924）。
- **`chain_task.task_key` 係 date-scoped 唔係 run-scoped**（`daily_chain_v2_contract.py:173-188`：`{date}:{source}:{capability}:{shard}:{sha}`，PK + `ON CONFLICT DO NOTHING`）——淨拆 chain_run UNIQUE 嘅話，第二條 run 會**靜靜繼承**第一條 run 嘅已完成 task。呢個先係最大隱藏 touchpoint。
- MySQL 側：`market_variant_source_state UNIQUE(business_date,variant_id,source,capability)` 係 upsert（re-stamp run_id，蓋得過）；**真正出街鎖係 `publication_outbox UNIQUE(business_date,event_type)`**——`insert_live_event`（`daily_chain_v2_db.py:866-909`）同 date 唔同 generation 即 raise。
- Bake 揀 generation **唔睇 date**：live 讀 `MAX(accepted_at)` 最新 generation（`apps/web/src/lib/live-db-snapshot.ts:301-313`）。⇒ **supersede 新內容一定會上 live，就算 outbox 拒絕記帳**——所以 outbox 改動同 release 改動必須同一批落，唔准分開。
- 同內容重跑出唔到街（`daily_public_release.sh:351-353` refuse「no immutable generation change」）——by design，唔使改：supersede 本身就係為咗攞**新鮮採集**嘅新內容。
- 人手 run 會 `manual_intervention_count+=1` 並 reset `proven_autonomous`（journal:518-526）——誠實，by design。

### 2.2 揀咗嘅路線：**Route B — supersede-in-place（封存舊 row，date 重用）** [INFERRED：三路線中改動最細而誠實]

保留 `business_date UNIQUE` 唔郁。新增：

1. **Journal**（`daily_chain_v2_journal.py`）：
   - 新 table `chain_run_archive`（照 chain_run 全欄 + `superseded_at`、`superseded_by_run_id`）；additive migration 跟 initialise() 現有 `PRAGMA table_info` 慣例（journal:244-269 冇 table-rebuild 先例，Route B 特登唔使 rebuild）。
   - 新 `supersede_run(business_date, reason)`：同一 transaction 內——①成條 chain_run + 該 date 全部 chain_task／chain_attempt row **先 dump 落 receipt JSON**（永遠唔准靜靜 delete）②搬去 archive／刪除 ③`ensure_run` 開新 row，run_id `cardz-v2:<date>/2`。
   - `chain_event.event_key` 係 run_id 前綴（journal:1450），自然唔撞。
   - autonomy 讀數（contract.py:565-590 個 `{business_date: row}` dict、journal:530-547、:1428-1440）：archive 咗就仍然一 date 一 row，assumption 不破——呢個就係揀 archive 而唔係 soft-delete 嘅原因。
2. **Orchestrator**（`daily_chain_v2.py`）：新 `supersede` subcommand（照 `retire` :3436 形狀；parser :3499-3512、dispatch :3538-3544）；tick guard（:3231-3236）唔使改——新 row 係 RUNNING。runtime dir 重用現有 label 岔道攞 `<date>-S2/`（:853-855），receipts 唔會冚舊 run。
3. **MySQL migration（要 daddy 開聲）**：`publication_outbox` 加 `superseded TINYINT` + 放寬唯一鍵為 `(business_date, event_type, generation_id)`；`insert_live_event`（db.py:866-909）改成「有舊 row → 標 superseded 再插新」；`recover_live_event`（:788-863，bare event_key 查）要識揀 current row。**同 release/live-confirm 改動同一批落**（§2.1 嘅「一定上 live」hazard）。
4. **Watchdog**：`watchdog_live_release.ps1:146-149` 對「今日 PUBLISHED＝收聲」——supersede 開波 run_state 離開 PUBLISHED 會re-arm，係想要嘅行為（重跑期間本來就應該睇住）。
5. **副作用（接受並記錄）**：`market_variant_source_state` re-stamp run_id 後，舊 run 嘅 `recover_daily_accept` 路徑失效——舊 run 已封存，接受。

### 2.3 觸發政策（兩期）

- **Phase 1（先落）：人手命令** `daily_chain_v2.py supersede --business-date D --reason "..."`。每 date 最多一次；只准 supersede PUBLISHED／PUBLISHED_DEGRADED；跑之前 refuse 如果 date 唔係「今日或未來」（歷史 date 冇意義）。
- **Phase 2（觀察一週後）：自動對齊**——03:30 JST 自然 tick 見今日 date 已 PUBLISHED **而且**嗰條 run 嘅 generated_at 早過今日自然窗開波 → 自動 supersede 一次。有 event 107 嘅自動 supersede 唔應該計 manual（要喺 register_provenance 明寫呢個分支，唔准靜靜改 counter 語義）。
- **對齊 playbook**：落地日 D：自然 tick supersede 重跑 date D（新鮮數據出街）→ D+1 起自然節奏，bridge cron 全部退役。

## 3. P1：Census 自動 refresh

- 現況：`gemrate_brute_harvest.py --all-sets` 人手跑；intake 讀 `psa10_1000_plus.jsonl`，>7 日 fail-closed 報 `censusStale`。
- 設計：每個自然 business date 跑 `identity-census`（concurrency group `host:gemrate`）全量刷新 GemRate set census；再於 identity intake 前跑 `identity-completeness`，動態重算全部 `PSA10 >= 1000` 卡、當日入／出名單、逐卡 DB 缺口及 GROK queue，並把合併 census 交現有 guarded intake／discovery。任何數量（包括 2026-08-25 的 1,660）都不寫死；失敗唔阻出街（optional phase），但唔完整證據必須明示，唔准講成「冇新卡」。[KNOWN]
- 唔改 intake 判斷邏輯——stale gate 係保護，唔係 bug。

## 4. P2：結構修 + cohort promotion

- 結構修跟 [V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) §5 buckets 逐項執（classifier 白名單化、scheduler pump、17:00 後 manual window cap）；每項落之前重種 bug 證明 test 會 fire。
- Cohort promotion：開一個窄嘅 V2 stage（只將 `qualified_identity` 而且 price-route + image-bind artifact 齊嘅 variant 走 rebuild validate 提升做 `product_ready`）；唔准繞過 validate 直接寫 cohort。[INFERRED：設計判斷，工程量另議]

## 5. 身份殘留收尾（audit 判詞）

> 2026-08-24 四路 Opus agent read-only 覆核（workflow `wf_6c938759-b48`）。全部判詞有 file:line／DB 實測支撐；判詞原檔喺 session scratchpad `audit_r3.json`。**冇任何一項要鬆 gate**——全部係 input 修、計數修、或者 capture 補齊。

### 5.1 可以直接做（第 0 級，唔使問）

| 項 | 根因（file:line） | 修法 |
|---|---|---|
| v1020/v1040 `multiple_exact_bindings` 假陽性 | `pipelines/discovery_ledger.py:66`（`SUM(match_status='exact')` 數 row 唔數 distinct external id）＋ `:75`（`snk`/`snk_psa10`/`snkrdunk` 三個 source code 當三個源） | 照 `new_era_db_tidy.py:2970` / `operator_fe_export.py:196` 個 `CASE WHEN … THEN 'snkrdunk'` canonicalize 之後先 dedupe 計數；test 重種 bug（兩行 alias 同 id → 必須數 1） |
| Brief 誠實度三個窿 | ① `pipelines/identity_brief.py:264` `exact_n>0` 短路食咗兩條 needs_you 入口；② `:556-557` hold 只裝飾唔分桶（226 條 reverify hold 零上榜）；③ `rebuild_036.py:9324-9328/9672-9676` `hold()` 唔寫 ledger → 「chain 會再試」係假 | ① classify 加「有未裁決 hold」分支（喺 `:264` 之前查 hold map）；② hold map 升做分桶輸入；③ hold 同步寫 `blocker_code`/`next_due_at`（唔改 gate，只令 ledger 講真話）。test fixture 要用 production 真形狀（`exact_n>=1` + hold），唔准再用 `exact_n=0` 假形狀（`scripts/test_identity_brief_message.py:51`） |
| stage 讀 journal 用錯 env var | `daily_chain_v2_stage.py:526` 讀 `CARDZ_V2_STATE_DB`，但 `identity_brief.py:967` → `daily_chain_v2_journal.py:79` 讀 `CARDZ_DAILY_V2_STATE_DB` | 統一一個名（跟 journal 側），另一個名保留做 fallback 一個 release 期，之後刪 |
| v2074/v1931 類 anniversary 錯綁 | discover console 配對：set_name「3rd Anniversary Set」對唔到 console，fallback 去號碼 set 拎咗 booster base 頁 | reject 現候選 → discover 補 `one-piece-japanese-promo` console fallback；v2074 正貨已喺本地 capture，實測 `product_agrees`＋`_pc_print_signature_ok('3rd Anniversary')` 雙過，零 code 改動 promote 到 |
| collect PC CDP 自撞（今日 4 attempt） | PC child 已行緊，parent 再開 → `PC_CHILD_ALREADY_RUNNING` ×2 | attempt 前查 child pid file／lease，撞就等唔係開新 |

### 5.2 要 daddy 決定（第 1 級，逐項開聲）

1. **18 條 Pokemon Master Ball rebind**——正貨 `[Master Ball]` 頁存在（同 set 已有 4 條 mb exact 證路徑通），本地冇 `scarlet-&-violet-151` / `terastal-festival` 兩個 console capture。**批一次 fetch**（CDP 9333 行 discover），之後 reverify 零 code 改動 promote。防線：rebind 條件讀 `canonical_name`（PSA label），唔准讀 `parallel_code`（實測唔可靠）。
2. **v2011 Snorlax CD Promo**——頁面啱，catalog 將 product 塞咗入 `parallel_code='cd promo'`，`canonical_name` 自己寫住 Snorlax-Holo，console 14 件貨冇 sibling。實測改 `parallel_code='holo'` 就過。要你確認改 `parallel_code` 定 `printing_code`（掂 `catalog_printing_identity`）。
3. **v2251 Gecko Moria [SP Foil]**——舊決定明文排除 SP Foil/SP Gold（`rebuild_036.py:8917-8921`，catalog 只有粗 `sp`）；呢個號碼 PC 6 件貨 SP 類只有一件、其實唔含糊。建議**單張人手 ruling**，唔好郁 synonym table。另外佢 catalog set/collector 自相矛盾（set_name OP08 vs collector ST03-004），identity 要先修。
4. **v112/v36 [SP Silver]/[SP Gold]**——要你拍板一句語義：catalog「3rd Anniversary-Silver/Gold」係咪＝PC「[SP Silver]/[SP Gold]」同一實體卡。點頭就落窄規則（只 key `spc`、metal 字要 variant 自己 parallel 有、孿生互拒），blast radius 3 行＋雙向 test。
5. **v2054 PRB01 Luffy alt-art**——現候選 8506784 **即刻 reject**（docstring 點名屬 v267）；但正貨 `[Alternate Art PRB01]` 係 product+treatment 複合 bracket，現規則讀唔到（`product_agrees` 差 ['best','premium']）。要唔要開單處理複合 bracket，你決定；唔急。
6. **54 個「1 exact + ≥1 non-exact PC row」殭屍形狀**——6 個點名殭屍 pid 已降級 manual_review、現狀零影響；但任何 adjudicator 升返其中一行做 exact 就即刻爆 `multiple_exact_bindings`。要清就一次過規則清（retire 同 variant 已有另一 exact 嘅 manual_review row），唔好逐個手清。

### 5.3 BOX / run 異象（已解，記錄在案）

- BOX 四個數閉環：335 operator 面／307 公開面（`sealed_operator.py:460-461` `source_frozen` filter、`:689` include_candidates）；281−6 priced candidates＝275；54 冇價全部＝零可用 observation（`sealed_price_compose.py:132-189` fallback 到底都冇料），其中 10 個 ptcg-en 老 set box 從未接 source——要有價要行 sealed bind SOP，唔係修 composer。
- fx 源被 0.5 秒 freshness floor 差殺死後 OPERATOR_RETIRED——core-class 源零完成而鏈綠，係 retire 語義嘅已知代價；下次見到唔使查。
- `pc_sale_title_quarantine` 57/47,897 title_collector_contradiction、SNK reverify hardConflicts 117（多數 ja/en 語言鏡像）——都係 gate 做緊嘢嘅樣，唔係故障。

## 6. 執行分級

**第 0 級（唔使問）：** journal archive table + supersede_run + subcommand 落 code + test（重種 bug 證 fire）；census stage code + test；全部 dry-run 驗。
**第 1 級（要 daddy 開聲）：** MySQL outbox migration apply；第一次真 supersede（Phase 1 人手觸發嗰次）；census stage 插入 plan()（改 orchestrator＝改出街路徑）；殘留卡 ruling `--write`。
**第 2 級（要 daddy 開聲＋揀時機）：** Phase 2 自動 supersede 開關；結構修 buckets；cohort promotion stage。
**永遠唔准：** 鬆任何 gate；改 `discovery_coverage_baseline.json`；掂 `backend.env`；卡名入 `_gemrate_printing_sha()`；view 多 id IN query；喺 tick 窗內改鏈 code。

## 7. 驗收（watched e2e，唔係 unit test 過就算）

1. Supersede 日：親眼睇住「supersede → 重採集 → 新 generation → live 對數 → outbox 兩 row（舊標 superseded）→ promo gate 認新 generation」一條龍。
2. 對齊後第 2 個自然日：零人手、event 107、`autonomous_proven` 有進度。
3. 47-check promo suite 全綠；QC report as-of 規則唔受影響；journal archive receipt 可以完整重演舊 run。
