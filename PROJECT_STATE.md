# PROJECT_STATE — CARDZ Market Cap（2026-08-24 階段總結）

> **呢份係現狀唯一入口。** 舊逐代記錄（025–037 開代史）已封存：[docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)。
> 讀嘢次序：[AGENTS.md](AGENTS.md)（硬規矩）→ 本檔（現狀）→ 各專題檔（§7 文件地圖）。
> **文件同 code 衝突時 code 贏**，發現即修文件。日程／cadence 真相永遠睇 `scripts/install_cardz_daily_v2_task.ps1` + `Get-ScheduledTask`，唔好信文件記憶。

## 0. 一眼現狀（2026-08-24 實測讀數）

| 項 | 數值 | 來源 |
|---|---|---|
| Live | `https://app.cardzmarketcap.com` · **1604 張** · generation `db3308_0b7eb7f4d2c34174` | `/api/health` 2026-08-24T07:21Z [KNOWN] |
| 版本 | product **037** · presentation **FE05**（fallback FE04）· BOX `/box` 307／275／307 | 同上 [KNOWN] |
| 日更 | Task `CARDZ-Marketcap-Daily-V2`：03:30 JST 起**每 10 分鐘 tick 到 17:00**（PT55M、隱藏視窗）；今日 run `cardz-v2:2026-08-25` **PUBLISHED**（14:09 JST 收尾 exit 0） | `Get-ScheduledTask` + `data/runtime/daily-chain-v2/health.json` [KNOWN] |
| 自動化證明 | `autonomous_proven = false`——要連續兩個自然日（有 event 107、零人手介入、非 DEGRADED）先算 | health.json + [docs/DAILY_CHAIN_V2_CUTOVER.md](docs/DAILY_CHAIN_V2_CUTOVER.md) [KNOWN] |
| 價格模型 | 最新真實成交價優先；K 線只做長窗（≥90d）fallback 錨，短窗永不用 K 線（R6b hybrid） | GitHub main `163e53e9`，下一次 bake 上 live [KNOWN] |
| ⚠ 帳期 | **business date 行快一日**（早跑食咗 slot：08-24 當日跑咗 business date 08-25）。修法 = supersede rerun，見 §5-P0 | health.json [KNOWN] |

## 1. 邊棵樹做咩

| 位置 | 角色 | 規矩 |
|---|---|---|
| `cardz-market-cap-fe-db-20260805`（本樹，branch `rebuild/036-foundation`） | **資料／日更真身**：V2 鏈、pipelines、receipts、DB migrations | 唔准由呢度直接 bake／`[deploy]` |
| GitHub `origin/main` | **FE 出街源頭**：release bake 只 ff GitHub main | FE fix 要上 live＝push 上 GitHub main，等下一次 bake |
| `../cardz-market-cap-037-fe04-live` | FE 出街車 worktree（= origin/main） | 認佢靠 branch==origin/main + live HTML 獨有字串 |
| `../cardz-fe-price-20260823` | FE dev 樹（改 FE 喺度改，push main） | deploy 契約（deploy_watch／commit-msg hook）只喺 FE 樹有 |
| WSL `~/cardz-market-cap-release-daily` | bake／release checkout | 只睇 GitHub main；由鏈自動用 |
| `../cardz-market-cap`（舊實驗樹） | **read-only** | 唔准刪／搬——MySQL 3308 Docker compose 同 14GB volume 名由佢推導 |
| MySQL `127.0.0.1:3308`（Docker `cardz-market-cap-db-1`） | 唯一 DB，庫名 `cardz_market_cap` | 30-min SELECT cap 已 PERSIST；view 唔准 `SELECT *`／多 id IN |

## 2. 自動鏈（V2）點行

一日一條 run（`cardz-v2:<business date>`），由 Task Scheduler 每 10 分鐘 tick 推進；journal 係 WSL ext4 SQLite（唯一狀態真身），每個 stage 有 lease／heartbeat／retry ladder，全程 fail-closed：**三個實跑日零錯數據出街——要麼 PUBLISHED，要麼被閘攔住停低**。

**Stage 流程（`daily_chain_v2.plan()`）：**

```
source 採集（gemrate 4 shard ∥ pricecharting ∥ snkrdunk；fx 已退役）
  → core-contract-pre（fail-closed barrier）
  → identity phase：operator-apply（抽人手 inbox）→ intake（新卡入場，§3）
      → discover lanes（PC browser@CDP9333 ∥ SNK http）→ reverify（browser/http）→ pending
  → 10:15 JST IDENTITY_CUTOFF（identity phase 過時自動 degrade，唔擋出街）
  → activation → core-contract-post → daily-accept（寫 MySQL acceptance）
  → box（sealed sidecar）→ release（WSL bake：GitHub main + generation 對數 + [deploy] push）
  → live-confirm（讀 live /api/health 對 generation）→ brief（HERMES 身份日報，barrier phase）
  → publication_outbox（live.confirmed:<date>，promo／通知 consumer 由呢度攞事件）
```

**Publish 對數鏈：** daily-accept 產 `publicGenerationId = db3308_<sha[:16]>`（date+content 折入 hash）→ release script 要 bake 出**一模一樣**嘅 generation 先准 commit → live-confirm 讀返 live 對數 → journal `mark_publication` + outbox 落 `live.confirmed:<date>`（一 date 一 generation，寫死唯一）。

**失敗行為：** transient 行 retry ladder；attempts 用完＝TERMINAL（stage 自己退役唔 park 成條 run）；PARKED 有 `unpark`／`retire` 結案路徑；手動窗 floor 45 分鐘、唔准跨下一 tick、FAILED_FINAL 可 explicit 復活。連線層有 connect retry、read-path 120s query cap、tick preflight 長查詢通報（只報唔殺）。

**操作命令（WSL）：**
```bash
python3 -X utf8 pipelines/daily_chain_v2.py status --business-date YYYY-MM-DD   # 讀狀態
python3 -X utf8 pipelines/daily_chain_v2.py unpark --run-id ... --task-key ...  # 救 PARKED
python3 -X utf8 pipelines/daily_chain_v2.py retire --run-id ... --task-key ...  # 結案退役
```
Receipts／logs：`data/runtime/daily-chain-v2/<business-date>/{receipts,logs}/`；health：`data/runtime/daily-chain-v2/health.json`。

詳細：安裝／授權史 [docs/DAILY_CHAIN_V2_CUTOVER.md](docs/DAILY_CHAIN_V2_CUTOVER.md)；實測行為／缺陷分類 [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md)；加新數據源 [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md)。

## 3. 新卡點入場（intake 鏈——由 POP 過線到上 live）

一張卡由「GemRate PSA10 POP 過 1000」行到「live 排名有佢」嘅全自動路徑（2026-08-23 起 in-chain 行）：

1. **Census**：`data/private/gemrate_brute/psa10_1000_plus.jsonl`（GemRate brute harvest）。**7 日 stale fail-closed**——census 過期只准報 `censusStale`，唔准講「冇新卡」。⚠ harvest 而家仲係人手跑（§5-P1）。
2. **intake stage**（`pipelines/gemrate_identity_intake.py`，in-chain，mode apply）：逐個 census 卡分桶——`already_qualified`／`alias`／`ruled`（已裁決，原文照抄 reason）／`ambiguous`（要人手，零寫入）／`auto`（開 catalog variant + GemRate exact binding + member row + ledger rebuild，同一 transaction，有 ratchet 上限）。Receipt：`data/runtime/daily-chain-v2/identity-intake-<date>.json`。
3. **discover lanes**：PC（headed Chrome CDP **9333**，9222 唔准掂）＋ SNK（http），每 lane 每日 40 個預算，去搵 PC／SNK 身份候選。
4. **reverify stages**（browser／http）：候選經全套 gate 判 `exact`／held（print signature、product 對數、operator ruling 全部 honour）。**Gate 唔准鬆——證據唔夠永遠係修 input 或落 ruling。**
5. **pending → brief**：判唔完嘅入 pending；`identity_brief` stage（barrier phase，10:15 cutoff 殺唔到佢）將成個母體分四桶——**已上場／已裁決／chain 自己再試／等你決定**——經 HERMES 送日報，附可以直接 copy 嘅命令。
6. **人手前門**（你嘅唯一工作面）：
   ```bash
   python -X utf8 pipelines/operator_control.py bind-url --variant-id N --url "<PC/SNK URL>" --actor daddy --write   # YES：綁呢頁
   python -X utf8 pipelines/operator_control.py rule --source-code ... --variant-id N --external-id ID --actor daddy --action ... --reason "..." --write   # NO：裁決拒絕
   ```
   兩條命令 default dry-run、行真 gate、寫 receipt（`data/runtime/operator/{bind-url,rulings}/`）；批次可以掉入 `data/runtime/operator/bind/inbox/`，第二朝 `operator-apply` stage 自動抽乾。
7. **上場最後一里**：exact 之後 cohort `qualified_identity` → `product_ready` 要 rebuild validate 先寫得（**V2 未有呢個 stage——§5 欠單**）→ activation → 當日 daily-accept 上場。

**今日數字 [KNOWN]（intake receipt 2026-08-25）：** census 956 張 pop≥1000；新卡 backlog **0**（alias 7、already_qualified 949）；discovery ledger 1621 條帳齊（active_exact 1602）。

人手裁決方法論（POP 先、EN/JP 孖生、憑證據自己揀頁）：[docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md)。

## 4. 做咗（landed，證據齊）

- **V2 鏈 cutover + 連續實跑**：08-22／08-23／08-24 三個日全部 PUBLISHED，零錯數據出街 [KNOWN]。舊 036 四-slot 日程已 Disabled。
- **鏈耐久性包**（commit `2a9dcc57`，22 checks 綠）：connect retry、runaway query cap、長查詢 preflight、PARKED retire、手動窗 floor／FAILED_FINAL 復活。
- **R2–R7 修復批 + R5 journal contention**（`b09e4c9d` 一帶 land）：error 分類形狀、contention refund、cutoff clamp 等。
- **身份自動化 in-chain**（migrations 055/056/058；`gemrate_identity_intake`／`operator_bind`／`identity_brief`／operator-apply + reverify stages）：新卡由 census 到 brief 全自動，人手只做 bind-url／rule。
- **08-22/23 身份釋放波**：bracket-product 分支 + operator-rule supersede 落地；6 條 ruling receipt + 5 條 bind-url receipt；當日 20 張被扣卡全數處理。
- **R6b hybrid 價格模型**（GitHub main `163e53e9`）：最新真實成交價優先、K 線只做 ≥90d fallback 錨；連帶拆咗三個 test 檔嘅 `\b`→backspace 空轉斷言（fire-proofed）。
- **08-31 炸彈已拆** [KNOWN]：`MAX_CURRENT_PRICE_AGE_DAYS` 唔再寫死 30——由 `data/policy/daily-release-guardrails.json` 逐 source 聲明 + F-PRICE-AGE 驗證（漏聲明即炸）+ `rankedDropMaxRatio 0.02` 閘（排名卡跌超過 2% 即 abort，唔會靜靜出街）。
- **BOX／sealed 併線**：`sealed_daily.py` in-chain 產 `/box` sidecar，唔再人手搬。
- **fx source 退役**：`OPERATOR_RETIRED`（journal SKIPPED，唔阻 run）[KNOWN]。
- **宣傳鏈**：`promo_after_publish` 掛 outbox、47-check suite、零自動發文（發文只行授權 job）；Hermes 日報、WhatsApp／IG 執行者各有 receipt。

## 5. 未做（欠單，按優先序）

| # | 欠單 | 現狀／根因 | 下一步 |
|---|---|---|---|
| **P0** | **同日 supersede rerun**（帳期修正） | business date 行快一日；journal `business_date UNIQUE`＝一 date 一 run，PUBLISHED 後 tick 只 no-op | 藍圖已寫：[docs/PLAN_FULL_AUTO_MODE_20260824.md](docs/PLAN_FULL_AUTO_MODE_20260824.md)（Route B），**要 daddy 開聲先實施** |
| **P1** | Census 自動 refresh | brute harvest 人手跑（而家 3–4 日舊）；stale 7 日就 intake fail-closed | 排程 harvest task 或 in-chain stage（藍圖 §C2） |
| **P1** | Brief 誠實度覆核 | needsYou=0 係咪靠母體收窄達成——printSig／ambiguous 卡有冇靜咗 | ✅ audit 已判（見 §5.1） |
| P2 | 身份殘留收尾 | v1020/v1040（multiple_exact_bindings 記帳）、v2011/v2251（inactive 要裁決）、22 printSig、5 productMismatch、6 殭屍 pid | ✅ audit 已判（見 §5.1）；要 daddy 決定嘅列咗喺藍圖 |
| P2 | 調度器／分類器結構修 | audit 買單：classifier substring 判生死、execute_ready 批次屏障、clamp_manual_window 17:00 後失效 | [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) §5 buckets 逐項執 |
| P2 | Cohort promotion 最後一里 | `qualified_identity`→`product_ready` 只有 rebuild validate 寫得，V2 冇 stage——收咗嘅新卡會停喺門口 | 藍圖 §C7；日報「有身份未出街」欄會照直報 |
| P3 | autonomy proof | 連續兩個自然日零人手；早跑／bridge 每次都 reset counter | supersede 落地 + 兩日唔掂佢 |
| P3 | BOX 未有價對數 | live 307/275 vs compose 335/281 兩重 filter 未成文 | ✅ audit 已解（見 §5.1） |
| P3 | 本樹未 push | `rebuild/036-foundation` ahead of origin 84 commits [KNOWN] | 揀時機 push（唔阻日更） |
| P3 | FE 樹文件分裂 | FE deploy 契約（deploy_watch／commit-msg hook／FE05_ROLLBACK）只喺 FE 樹；FE 樹 pointer 仲指 036 | 下次掂 FE 樹時同步 |

### 5.1 Audit 收尾判詞（2026-08-24 四路 read-only 覆核）

四路 Opus agent（wf_6c938759-b48）read-only 覆核，判詞原檔喺 scratchpad `audit_r3.json`；行動細節同「要 daddy 決定」清單喺藍圖 §5。一句版：

| 路 | 判詞 |
|---|---|
| **DB 卡（db-cards）** | [KNOWN] **新卡 0**。v1020/v1040 `multiple_exact_bindings`＝**假陽性**——`snk_psa10`/`snkrdunk` 係同一個 external id 嘅兩個 alias，`discovery_ledger.py:66/75` 數行冇 canonicalize（其他 pipeline 有，`new_era_db_tidy.py:2970`）；修 ledger 計數，唔使掂 binding。v2011＝catalog 將 product 塞咗入 `parallel_code='cd promo'`，改 `holo` 就過（要 daddy 揀欄）。v2251＝catalog set/collector 自相矛盾，先修 identity 再談 binding。v1760 已 active_exact，冇嘢做。6 個「殭屍 pid」全部已降級 manual_review、6 卡各自有新 exact——現狀零影響，但全表有 **54** 個「1 exact + ≥1 non-exact PC row」同形狀，要清就一次過規則清 |
| **BOX 對數（box-unpriced）** | [KNOWN] **四個數全閉環**：335＝operator 面（include_candidates）、307＝公開面（source_frozen only，`sealed_operator.py:460-461/:689`）；281−6 個有價 candidate＝275。54 隻冇價＝**零可用 observation**（唔係 outlier 唔係 sold30d）：14 隻從未接 source（10 個 ptcg-en 老 set＋未發售），其餘等第一個 obs。今日 run 三個異象：collect 4 attempt（PC CDP 同自己 child 相撞）、fx 被 0.5 秒 freshness floor 殺死後 OPERATOR_RETIRED（core 源零完成但鏈綠）、reverify held 226 |
| **Brief 誠實度（brief-honesty）** | [KNOWN] **needsYou=0 係算式恆等式唔係觀察**——`identity_brief.py:264` `exact_n>0` 短路＋`:556` hold 只喺 needs_you 之後先睇，令 226 條 reverify hold（printSig 22、productMismatch 5、hard conflict 163、map 47…去重 226 distinct variant）**結構性入唔到「等你綁」**，brief 對佢哋一個字都冇講。仲有：`hold()` 唔寫 ledger（`rebuild_036.py:9324/9672`）所以「chain 會再試」對呢批係假；test fixture 用 production 永遠唔會出現嘅形狀（綠燈證緊冇人行嘅路）；stage 讀 journal 用錯 env var 名（`CARDZ_V2_STATE_DB` vs `CARDZ_DAILY_V2_STATE_DB`）。**Brief 送達本身有 receipt**（journal event delivered=1，05:07Z）——送到，但內容唔完整 |
| **printSig 判詞（printsig-adjudication）** | [KNOWN] **gate 冇壞，22 條係 4 個 sub-class**：18 條 Pokemon Master Ball 係**綁咗 base product**（正貨 [Master Ball] 頁存在，本地冇 capture 嗰兩個 console listing——批 fetch 重跑 discover 就零 code 改動 promote）；v2074/v1931 類＝anniversary set fallback 錯 console，正貨已喺本地 capture，實測零改 code promote 到；v2054 PRB01 現候選要 reject（docstring 點名屬 v267）；v2251 [SP Foil]＋v112/v36 [SP Silver/Gold] 要 daddy 拍板語義。**冇任何一條支持鬆 gate** |

修法全部係 input 側或計數側，冇一項要掂 acceptance/publish gate。要 daddy 決定嘅五件事列咗喺藍圖 §5 尾。

## 6. 計劃與諗法

**方向一句：** 人手歸零——鏈自動跑、帳期自動對齊、新卡自動入場，判唔到嘅先嚟朝早 brief 搵你；你嘅唯一日常工作係回覆 brief 嘅 bind-url／rule 命令。

- **完全體自動 MODE 藍圖**（supersede 設計、census 自動化、殘留收尾、驗收準則）：[docs/PLAN_FULL_AUTO_MODE_20260824.md](docs/PLAN_FULL_AUTO_MODE_20260824.md)。實施要 daddy 開聲。
- **價格哲學**：價＝最新真實成交；K 線只可以做長窗錨，永不冒充成交價。四源政策（ebay flag 由 env 管）係政策唔係 error。
- **Gate 哲學**：永不鬆 gate——證據唔夠＝修 input 或落 ruling；每個新 assert 要證明佢真係會 fire；「有檢查但零 call site」當冇檢查。
- **圖源權威**：G10 > SNK，其他血統 hard exclude；SAMPLE 水印唔擋發佈只做排名信號；人手 reject 係 hard authority。

## 7. 文件地圖（2026-08-24 重排）

**現行（讀呢啲）：**

| 檔 | 講咩 |
|---|---|
| [AGENTS.md](AGENTS.md) | 硬規矩（樹、DB、port、freeze） |
| 本檔 PROJECT_STATE.md | 現狀唯一入口＋做咗未做 |
| [docs/COLLECTION_RUNBOOK.md](docs/COLLECTION_RUNBOOK.md) | 五條採集 lane 機制／缺陷形狀（**日更調度唔關佢事**，嗰part 睇 §2） |
| [docs/DAILY_CHAIN_V2_CUTOVER.md](docs/DAILY_CHAIN_V2_CUTOVER.md) | V2 安裝／授權／autonomy 定義（cutover 已完成，任務行緊） |
| [docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md](docs/V2_CHAIN_STRUCTURAL_AUDIT_20260823.md) | 鏈實測行為＋結構欠單 |
| [docs/ADDING_A_SOURCE.md](docs/ADDING_A_SOURCE.md) | 加新數據源八步 |
| [docs/PROMO_CHAIN.md](docs/PROMO_CHAIN.md) | 宣傳鏈（零自動發文） |
| [docs/LEFTOVER5_IDENTITY_20260813.md](docs/LEFTOVER5_IDENTITY_20260813.md) | 人手身份裁決方法論 |
| [docs/PLAN_FULL_AUTO_MODE_20260824.md](docs/PLAN_FULL_AUTO_MODE_20260824.md) | 完全體自動 MODE 藍圖 |

**封存／歷史（唔好當現狀讀）：** [docs/archive/PROJECT_STATE_025-037_archived-20260824.md](docs/archive/PROJECT_STATE_025-037_archived-20260824.md)（025–037 開代史）、[docs/HANDOFF_036_20260812.md](docs/HANDOFF_036_20260812.md)（036 敘事＋當年欠單）、[docs/HANDOFF_037_FE04.md](docs/HANDOFF_037_FE04.md)（037 開代契約；日程／卡數已過時）、[docs/DAILY_CHAIN_AUTONOMY.md](docs/DAILY_CHAIN_AUTONOMY.md)（036 autonomy 定義，已被 V2 取代）、[docs/PLAN_036_CLOSEOUT.md](docs/PLAN_036_CLOSEOUT.md)（未執行計劃書）、[docs/SEALED_OPS.md](docs/SEALED_OPS.md)（⚠ 佢啲命令指住 read-only 舊樹，唔好照跟）、`docs/handoff/`（GEO 一次性）、postmortem 三份（案例記錄）。
