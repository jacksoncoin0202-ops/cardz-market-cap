# 交付鏈全圖 — 前端 → API → snapshot → DB → 數據源 → 腳本

2026-07-26 全量實測。呢份係「宏觀砌返埋」嘅主文檔：由已凍結嘅前端規格倒推到數據源，
列出每一個斷點，再排出邊個先做。

摘要版喺 [CLAUDE.md](../CLAUDE.md) 「前端 → DB 斷點圖」section。呢度係完整推導同證據。

---

## 一句話

前端已經係成品規格，DB 有三年價格歷史，但**中間層（producer）將四個已經有數嘅欄位接錯咗表或者寫死咗 null**，
再加上**兩張表由頭到尾未寫過一行**（FX、tracked sales aggregate）。
所以呢一刻嘅網站唔係「數據唔夠」——係「有數冇接上」同「有兩條線從未插電」。
真正等時間嘅只有 POP 窗口，真正做極都達唔到目標嘅只有價格覆蓋率。

---

## 第 0 層：前端就係規格（已凍結，唔准改）

前端已完成，所以佢係契約而唔係變數。倒推出嚟嘅硬要求：

### 窗口
三個窗口 `1d` / `7d` / `30d`，**純 client-side toggle** —— 即係一次過送三個窗口落去，
唔係每次切換叫 API。`period-selector.tsx` 只改 local state。

### 索引無保護（呢個係最狠嘅一條）
`card.windows[period]` 同 `card.graderPopulations[grader]` 喺全部 component **冇 optional chaining**。
後端每張卡必須齊 **3 個 window key × 5 個 grader key**，缺一個 = UI 直接 throw。
呢個唔係建議，係 crash 條件。

### 顯示欄位（每行）
rank · 卡圖 · 卡號 · 價 · 價 delta · POP · POP delta · 市值 · 市值 delta · 成交 · 成交 delta · 變動% · sparkline

### 排序
market cap desc，**server-side 定死**（`server-snapshot.ts` `ranked()` 取 top 100 再逐個 assign rank）。
UI 冇排序控制。grader 分頁會**重新 assign rank**（per-view rank，唔係全域 rank）。

### 圖表
- 詳情頁：`historyDaily` 畫價線 + 成交柱
- 列表頁：sparkline **只讀 `trackedSalesValueUsd`**，需要 ≥3 個非 null 點

### i18n
5 個 locale（`en` / `zh-TW` / `zh-CN` / `ja` / `ko`）× `name` / `setName` / `story` / `image.alt`

### 匯率（陷阱位）
7 個幣種。[format.ts](../apps/web/src/lib/format.ts) `formatMoney()` 開頭嗰兩句 `Number.isFinite` guard
—— **任何一個 rate 唔係 finite，嗰個幣種下所有錢銀欄位變空白**。
唔係顯示 fallback，係整版白。

---

## 第 1 層：API + 發佈路徑

**API 唔係第二條數據路。** 三個 v1 endpoint 同網頁讀同一份 R2 物件：

| 路徑 | 用途 |
|---|---|
| [apps/web/src/app/api/v1/market/route.ts](../apps/web/src/app/api/v1/market/route.ts) | 全市場列表 |
| [apps/web/src/app/api/v1/cards/[id]/route.ts](../apps/web/src/app/api/v1/cards/[id]/route.ts) | 單卡詳情 |
| [apps/web/src/app/api/v1/graders/[grader]/route.ts](../apps/web/src/app/api/v1/graders/[grader]/route.ts) | 分廠視圖 |
| [apps/web/src/app/market-assets/[asset]/route.ts](../apps/web/src/app/market-assets/[asset]/route.ts) | 卡圖 |

發佈鏈（[lib/server-snapshot.ts](../apps/web/src/lib/server-snapshot.ts)）：

```
canonical_public_snapshot.py
      ↓ JSON
   R2 bucket (binding MARKET_DATA)
      ↓ pointer latest.json → { snapshotKey }
   Next.js on Cloudflare Workers
      ↓ assertPublicSnapshot()  ← validate.ts 60+ 個閘喺呢度行
      ├─ SSR pages
      └─ /api/v1/*
```

三個設計要記住：
1. **pointer 做原子切換** —— 寫新 snapshot key，最後先改 `latest.json`。冇半新半舊狀態。
2. **`runtimeLastGood` in-memory fallback** —— 新 snapshot 過唔到 `assertPublicSnapshot`，
   worker 繼續派上一份好嘅。即係**發佈壞數據唔會即刻爆版，會靜靜地停留喺舊數據**。
   呢個係好事（唔會 down），亦係壞事（靜態失敗，要靠外部監測先知）。
3. **`listCard()` 會削數據** —— 列表視圖將 `story` 清空、`historyDaily` 只留
   `trackedSalesValueUsd !== null` 嘅點再取最後 14 個。
   所以 **stories 缺失只影響詳情頁**，但 **sparkline 完全靠 tracked sales**。

**結論：要做嘅唔係「起個 API」，係修好 producer。** API 已經齊。

---

## 第 2 層：snapshot 契約（validate.ts）

`packages/market-data/src/validate.ts` 60+ 個閘，全部 fatal。production 模式額外加新鮮度 SLA：
價格 ≤48h、POP ≤168h。

**實測：demo 模式 0 error，production 模式 586 error。**

| 錯誤 | 數 |
|---|---:|
| `top100 localization is incomplete` | 300 |
| `watchlist identity localization is incomplete` | 184 |
| `top100 stories are not independently localized` | 100 |
| 其他 | 2 |

584 / 586 係 i18n。**呢個係上線唯一硬 blocker。**

---

## 第 3 層：DB 實況（2026-07-26 實測）

34 張 base table，15 張 0 行。

| 表 | 行數 | 範圍 | 判決 |
|---|---:|---|---|
| `market_price_observation` | 115,253 | 2023-06-19 → 2026-07-24 | ✅ 真三年歷史 |
| `market_source_observation` | 277,127 | 2023-06-19 → | ✅ |
| `market_daily_sales_aggregate` | **3,592** | 2023-07-20 → 2026-07-24 · 336 variant | ✅ **有數但冇人用** |
| `market_grader_population_observation` | 7,904 | **只有 5 日** 07-21～07-25 | ⚠ 3 日可用 |
| `catalog_variant` | 1,590 | 全部 07-23～07-25 建 | ⚠ 3.3% 重複 |
| `market_fx_rate_observation` | **0** | — | ❌ 從未寫過 |
| `market_tracked_sales_aggregate` | **0** | — | ❌ 從未寫過 |

### POP 逐日（呢個係「5 日」嘅真面目）

| 日期 | variant | 備註 |
|---|---:|---|
| 07-21 | 613 | |
| 07-22 | 309 | 部分失敗 |
| 07-23 | **1** | **整日失敗** |
| 07-24 | 1216 | 歷來最高 = 81.0% roster |
| 07-25 | 1084 | |

5 個日曆日，**3 日可用**。每日 POP 覆蓋率**從未超過 81.0%**。

### 價格覆蓋率（結構性上限）

- 395 / 1590 variant（24.8%）有任何價格歷史
- **1195 / 1468 roster 卡（81.4%）從來冇過一行價** —— 硬上限 273（18.6%）
- 只有 `snk_psa10` 有真時序：114,755 行 / 1132 個唔同日期 / 336 variant
- `snkrdunk`（406 行）、`ebay`（92 行 / 59 variant）**各自得 2 個唔同日期**（07-19、07-24）
  → 係 point-in-time 快照，唔係時間序列

---

## 第 4 層：斷點總表（核心）

每一行 = 前端一個顯示元素 → 需要邊個數 → 而家點 → 斷喺邊。

| # | 前端元素 | 需要 | 實測現況 | 斷點性質 |
|---|---|---|---|---|
| 1 | **所有錢銀欄位** | 7 個 FX rate | `market_fx_rate_observation` **0 行** | ❌ 未插電。`fx_rates.py` 存在但從未接入 run_daily。切幣種 = 白版 |
| 2 | **成交額 / 成交 delta / sparkline** | `windows[w].trackedSales` | 讀 `market_tracked_sales_aggregate`（**0 行**）→ 192 卡 × 3 窗口全 unavailable | 🔌 **接錯表**。`market_daily_sales_aggregate` 有 3592 行 / 336 卡 / 3 年 |
| 3 | **POP delta / Grading Pulse +N** | `topGradePopulationChangePct` | ⚠️ **呢格已作廢**（量度時 `card_from_row()` 寫死 null）。2026-07-26 起改由 `population_series()` 真計，見 CLAUDE.md 嘅「POP 時間鎖作廢」 | 🔌+⏳ 已拆硬編碼；剩返 POP 真量日數不足 |
| 4 | **中日韓繁簡文案** | name/set/story × 4 locale | **0/192**（英文 192/192） | 🚧 584/586 production error。**上線唯一硬 blocker** |
| 5 | **市值 delta** | 市值窗口變動 | **schema 根本冇呢個欄**。UI 攞價格 changePct 頂替（[rankings.tsx](../apps/web/src/components/rankings.tsx) 個 `<MetricDelta>`） | ❗ 錯數。市值 = 價 × POP，POP 只升 → 顯示值**系統性低估** |
| 6 | **成交 delta** | 成交窗口變動 | 同上，攞價格 changePct 頂替（[rankings.tsx](../apps/web/src/components/rankings.tsx) 個 `<SalesDelta>`） | ❗ 錯數。#2 修完就有真數 |
| 7 | **topGrade 標籤** | `top_grade_label` | 大部分卡出字面 `"top"` 而唔係 `"10"`（[canonical_public_snapshot.py](../pipelines/canonical_public_snapshot.py) `card_from_row()` 個 `"topGrade"` 欄原封傳，validator 只查非空） | 🐛 低成本 |
| 8 | 7d / 30d 價格變動 | `change_7d_pct` / `30d_pct` | 1510 / 1557 於 1964 行有值 | ✅ 得 |
| 9 | historyDaily | 90 日 | DB 有 597 日 | ✅ 得（各卡日期範圍唔對齊） |

**圖例**：❌ 未插電 · 🔌 接錯 · ⏳ 等數據 · 🚧 上線 blocker · ❗ 顯示緊錯數 · 🐛 小 bug

---

## 第 5 層：優先次序

排序原則：**上線 blocker > 每蚊成本影響最大 > 顯示緊錯數 > 等時間 > 換策略**。

### P0 — 上線硬 blocker

**P0-1 · FX 0 行**（斷點 #1）
- 成本 **低**：`pipelines/fx_rates.py` 已存在，只係從未接落 `run_daily.py`
- 影響 **災難級**：用戶撳一下幣種切換就見白版
- **整份清單性價比最高嘅一件。先做呢個。**

**P0-2 · i18n 584 個 error**（斷點 #4）
- 成本 **中高**：192 卡 × 4 locale × 3 欄
- ⚠ 唔係機器翻譯就完事 —— CLAUDE.md 明文：卡名只准用官方譯名或社群共識俗名，
  **唔准逐詞直譯**，無共識保留英文。批量譯完要用戶過目先 ship。
- 分兩批：`name`/`setName` 影響全站（列表都要），`story` **只影響詳情頁**
  （`listCard()` 會清空 story）→ 可以先做前者解 300+184 個 error

### P1 — 數據喺屋企，只差接線（唔使爬新嘢，最抵做）

**P1-1 · tracked sales 改讀 `market_daily_sales_aggregate`**（斷點 #2）
- 實測近日每日 166–207 個 variant 有成交數，由 2023-07-20 起
- 改 producer 個 SELECT + 加窗口聚合
- 一改即刻救返「成交額」欄 + 全部列表 sparkline + 解鎖 P2-2

**P1-2 · 拆走 `topGradePopulationChangePct` 硬編碼**（斷點 #3）
- ⚠ **呢個唔係純接線** —— 上游 `population_change_7d_pct` / `30d_pct` 欄位存在，
  但實測 **0/1964 行有值**（對比：價格 change 欄 1580/1510/1557 行有值）。原因係 POP 得 5 日。
- 但硬編碼一日唔拆，07-28 數據到咗都**唔會顯示**。所以而家拆（低成本），到時自動着。

### P2 — 顯示緊錯數

**P2-1 · 市值 delta**（斷點 #5）—— 要喺 producer 加真市值窗口變動。受 POP 歷史限制，同 P1-2 一齊解鎖。
**P2-2 · 成交 delta**（斷點 #6）—— P1-1 做完就有真數可以接。
**P2-3 · topGrade 出 `"top"`**（斷點 #7）—— 成本極低，順手做。

### P3 — 時間鎖（做咗都要等）

| 目標 | 最早日期 | 條件 |
|---|---|---|
| POP 7D | **2026-07-28** | 首觀測 07-21 + 7 |
| POP 30D | **2026-08-20** | 首觀測 07-21 + 30 |
| POP 7D @ ≥90% roster | 2026-08-03 | 要先修好每日 run（歷來最高 81.0%），假設 07-27 first clear |
| POP 30D @ ≥90% roster | 2026-08-26 | 同上 |

**第二重閘：每日 POP 覆蓋率從未超過 81.0%。** 唔修好每日 run，等到 08-20 都唔夠 90%。

### P4 — 結構性上限（等唔嚟，要換策略）

**價格覆蓋率永遠上唔到 90%。** 1195/1468（81.4%）從來冇過一行價，硬上限 18.6%。

呢個直接關係到路線更新第 4 點「eBay = PSA10 成交唯一真源」：
- eBay 實測得 **92 行 / 59 variant / 2 個日期**
- 要坐實「唯一真源」呢個定位，eBay 採集要由 92 行做到每日穩定跑
- 呢個係**獨立工程**，唔係接線可以解決 —— 要單獨排

---

## 依賴圖

```
P0-1 FX ─────────────────────────► 錢銀欄位可用（獨立，即刻做得）

P0-2 i18n name/set ──┬──────────► production gate 過
       story ────────┘

P1-1 tracked sales ──────────────► 成交欄 + sparkline
        └────────────────────────► P2-2 成交 delta

P1-2 拆硬編碼 ──┐
                ├─ 07-28 ────────► POP 7D delta 自動着
POP 歷史累積 ───┘
                └─ 08-20 ────────► POP 30D delta

P2-1 市值 delta ◄── 需要 POP 窗口歷史（同上時間鎖）

P4 eBay 採集工程 ────────────────► 價格覆蓋率（獨立長線）
```

---

## 執行次序（結論）

1. **P0-1 FX** —— 低成本 × 災難級影響，冇依賴
2. **P1-1 tracked sales 接線** —— 低成本，即刻見效，解鎖 P2-2
3. **P1-2 拆 POP delta 硬編碼** —— 低成本，07-28 自動兌現
4. **P2-3 topGrade 標籤** —— 順手
5. **P0-2 i18n**（先 name/setName 後 story）—— 上線 blocker，但成本最高，要用戶參與
6. **P2-1 / P2-2 delta 正確性** —— 等 P1 完成
7. **修每日 POP run 覆蓋率**（81% → 90%+）—— 唔修的話 P3 個時間表冇意義
8. **P4 eBay 採集** —— 獨立長線工程

---

## 證據來源

- [docs/WINDOW_FEASIBILITY_20260726.md](WINDOW_FEASIBILITY_20260726.md) —— 1D/7D/30D 逐指標可行性，附全部 SQL
- [docs/DB_INVENTORY_20260726.md](DB_INVENTORY_20260726.md) —— 34 張表盤點
- `temp/verify_gaps.py` —— 斷點 #1 #2 #3 嘅直接驗證 SQL
- 前端契約：Agent A 全量讀 18 個 `apps/web/src/lib` + `components` 檔
- snapshot 契約：Agent B 全量讀 `packages/market-data/src` + `canonical_public_snapshot.py`
