# Workstream：前端出 demo 模式 + 開返晒啲頁（可獨立開新對話執行）

> 呢個檔自足——唔使睇任何舊對話。來源：2026-08-01 對 apps/website 全量
> code 盤點（逐檔 grep flag/hidden/demo）。

## 最重要發現：**冇「隱藏頁面」呢回事**

Code 入面唔存在 feature flag 系統、冇 commented-out route、冇 hidden page。
全站唯一嘅閘係 **demo 模式**，由 snapshot 嘅 generation metadata 控制：

- `generation.mode = "demo"`
- `generation.productionEligible = false`
- `generation.blockers = ["presentation_assets_incomplete", "strict_qc_receipt_missing"]`
- `compose.yaml:26` — `MARKET_DATA_ALLOW_DEMO=true`（production 模式下要拎走）

即係「開返晒啲頁」嘅正確做法唔係改前端，而係**令 pipeline 出一個
production-eligible 嘅 snapshot**——兩個 blocker 清晒，mode 自然變
production，成個站就係「開晒」。

## 同 DB 嘅真實關係（更正返之前嘅講法）

排行榜、heatmap、heatmap-tiles-board **全部食 DB 衍生嘅 snapshot 欄位**，
唔係獨立於 DB：

- Heatmap 食 `snapshot.top100`：`marketCap.value` = 格仔面積，
  `windows[period].changePct` = 顏色。呢兩個數全部由 DB derive 出嚟。
- `HeatmapTilesBoard` 唯一 caller 係 /tune（`tune-lab.tsx:17`）——係 tuning
  playground 組件，唔係獨立數據源。
- 所以「舊數據得就開返」＝「snapshot 有齊呢啲欄位就自動有嘢睇」，
  唔使另外開掣。

## 2026-08-01 晚間盤點（DB snapshot 305 / eval 107 實數）——數據夠唔夠開頁

- **Watchlist 101–300：夠。** 200/200 identity=canonical、metric=ready、
  30d pure-PSA10 全部 ≥10 單（零 delist）。而家個站食 07-26 seed 得
  watchlist 129 張；出新 snapshot 自動變 200 張。
- **Graders（pop 觀測 7 日新鮮口徑，builder 168h fail-closed）**：
  PSA 300/300 ✅、CGC 298/300 ✅、SGC 287/300、BGS 169/300、**TAG 48/300 ❌**。
  Grader tab 係 data-driven（`gradersWithCards(market)`）——snapshot 有邊個
  grader 嘅卡就出邊個 tab，唔使 FE 開掣。
- 變化率年資：1d 571/576 ✅；7d 60/576、30d 8/576（daily snapshot 歷史淺，
  跑落去自然填滿，唔擋上線）。
- `market_ungraded_reference_price` 全庫 0 行 → seed 冇 `priceUngradedReference`
  key 係一致現象，唔係 bug。

## 真正要做嘅細項（照優先序）

1. **清 blocker ①**`strict_qc_receipt_missing` — daily run 行 strict QC 出
   receipt（launch line 本身就係做緊呢樣，trial 過咗自然清）。
2. **清 blocker ②**`presentation_assets_incomplete` — 補齊 429x600 canvas
   規格嘅卡圖 manifest（睇 `pipelines/canonical_public_snapshot.py` 嘅
   image manifest 部分邊啲卡缺圖）。
3. **拎走 `MARKET_DATA_ALLOW_DEMO=true`**（compose.yaml:26）——demo escape
   hatch，production 唔應該再容忍 demo snapshot。
4. ~~小修~~（2026-08-01 實查更正：**下面幾項全部唔使做／唔係隱藏開關**）：
   - ~~`market-page.tsx:65,67`~~ — 嗰兩行係 watchlist 頁 layout 差異
     （watchlist 頁唔出 Heatmap/GradingPulse，係設計唔係隱藏），`<Rankings>`
     一直有 render watchlist。
   - ~~`header.tsx:22`~~ — header 五條 link（all/pokemon/one-piece/graders/psa
     /watchlist）已經齊晒。TAG 唔應該開（pop 48/300）。
   - `sitemap.ts` 缺 `/graders/tag` — 維持現狀啱（TAG 未夠數據）。
   - seed 冇 `priceUngradedReference` — 上面已解釋，pipeline 側未有數據源。

## 驗收

- snapshot `generation.mode == "production"`、`blockers == []`
- compose 冇 `MARKET_DATA_ALLOW_DEMO`
- watchlist / graders/tag 顯示正常
- 用 `packages/market-data/src/validate.ts` build 過（website contract 綠）

## 2026-08-01 深夜：卡片語言掣（owner 拍板延後，擴池係前置）

- Owner 要求語言掣（All/EN/JP，睇齊 period selector 美術）出喺 market cap
  排行類頁（all/pokemon/one-piece/watchlist）；**graders 頁唔要**（graders
  係獨立 `grader-page.tsx`，本身冇掣，天然滿足）。
- 語意必須係「**每語言自己嘅 top 100**」（池 → 篩語言 → 重排 → 頭 100，
  同 pokemon/one-piece/graders 嘅 `scopeSnapshot`/`ranked()` 模式一致），
  唔係「mixed top100 再篩」（後者 owner 已否決）。
- **實數（最新 tcg-combined constituent set，573 張）**：EN 434 / JA 139；
  頭 300 內 EN 251 / **JA 得 49**；JA 第 100 張喺全池 rank **481**。
  → snapshot 而家只出 top100+watchlist(300)，JA 榜最多 49 張。
- **Owner 決定（2026-08-01）：等 pipeline 擴池（snapshot 出到全池 ~573 /
  至少 rank 481+）先出語言掣，一出就 EN/JA 各 100 齊。** 上線版冇語言掣。
- Code 底：曾實裝「裁完再篩」版（`f590e60`，含 CardLanguageSelector +
  `cardlang` URL param + 五語 label），已 revert（`06827ea`）。擴池後重做
  時直接攞嚟改池化重排，唔好 revert-revert 照用（篩選語意錯）。
- 擴池注意：`listCard()` 已 slim story/history，573 卡 client payload 要
  實測；`market_index_constituent` join `catalog_variant.card_language`
  就攞到語言（backfill 唔使做，identity 層已有）。

## 2026-08-02 晚：FE 版本鎖定（print-language 版上線）

**鎖定咗嘅版本**：local branch `wsl-cutover-20260731` commits `84b5c5f` +
`707dd20`（改動全部喺 `apps/web`）。Live：WSL docker `cardz-web:degated`
（port 3900，`MARKET_DATA_POINTER_PATH=/snap/latest.json` 經 bind mount 食
publish-staging pointer，新世代數據唔使 rebuild）。回滾 image：
`cardz-web:rollback-20260802`。

**Repo 推送**：remote `github.com/jacksoncoin0202-ops/cardz-market-cap` 嘅
`lock/fe-20260802` branch = remote `wsl-cutover-20260731` head（`4b277d4`）
+ 一個淨-FE commit，內容同 local apps/web 完全一致。淨推 FE（owner 指示）；
local 分支上 19 個未推嘅 backend/pipeline commits 留返喺 local，冇上 remote。

**Owner 三個決定（2026-08-02）**：

1. **恢復並鎖定「語言版本篩選」版。** Top 100 排行隔籬出 All/English/Japanese
   篩選（同 1d/7d/30d 並排）。注意語意：呢版係 **view-then-filter**（篩走行、
   唔重排，`viewRank`/`marketRank` 照舊出），同上面 08-01 記錄嘅
   「池→篩→重排、擴池後先出」方向**唔同**。Owner 2026-08-02 明確要呢版先上；
   池化重排版仍然係擴池之後嘅目標，到時呢個 filter 要重做語意。
2. **排行榜唔出印刷資料。** Top 100 同 Graders 五版每行嘅 print badge
   （Japanese print 等）同 chips（SV8a・SAR・foil）全部剷走（手機版考慮；
   複雜數據淨入內頁）。`PrintLanguageBadge`/`PrintAttributeChips` 而家冇
   caller，组件保留喺 `print-badge.tsx` 未刪。
3. **內頁先見印刷詳情。** 卡詳情頁 identity list 出 `Language`（語言版本）
   ＋ `Pack source`（卡包來源，即 `editionCode`）；heatmap 彈出卡
   （CardFacts）同步出呢兩欄。`editionCode` 舊紅線（36/433 已出街錯對）
   由 owner 改做「淨准內頁／彈卡出」；錯對修復屬數據線（另一條線進行中），
   前端照 DB 出街，唔做一致性遮掩。

**技術備註**：

- 新白名單 `DETAIL_PRINT_FIELDS`（`print-badge.tsx`）= editionCode + setCode +
  rarityCode + finishCode，淨供 card-detail / heatmap CardFacts；
  `PUBLIC_PRINT_FIELDS` 照舊（grader chips 已停用）。
- View model `printingIdentity.editionCode` 係 optional（舊 snapshot 冇呢條欄）。
- i18n 新 label `packSource`（en "Pack source" / zh-TW 卡包來源 / zh-CN 卡包来源 /
  ja 収録パック / ko 수록 팩）；卡包名出街前 title-case（`displayPackName`）。
- 已知：4 個 vitest 失敗屬數據線（2026-08-02 19:35 seed 刷新令
  「dev seed 冇 print identity」前提過時 + snapshot-pointer 工），同 FE 改動無關，
  留返俾數據線收尾。
