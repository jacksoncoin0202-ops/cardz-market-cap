# 2026-07-27 上線頂檔：窗口借數（fallback cascade）與已知數據問題

- **量度日期**：2026-07-27 15:30（本機時區）
- **量度對象**：production snapshot `canonical_20260726_e88c81289ac3`（258 published：top100 100 + watchlist 158），
  經 :3800 dev server（`MARKET_DATA_SNAPSHOT_PATH` → `data/runtime/local-serve/snapshot.json`）實測
- **前提**：呢份文記錄嘅係**用戶明確下令嘅產品取捨**，唔係 bug——
  用戶原話：「你 fallback 翻對上嗰個就得。千祈唔好完全冇……依家先用呢啲 fallback 數據頂住檔先。
  我哋嘅角度係要個畫面好睇咗先。如果你覺得啲數據有問題，到時寫低，遲啲再跟進都未遲。」
  呢份就係嗰句「到時寫低」。

---

## 1. 做咗咩：空窗口借數（donor 順序 1d → 7d → 30d）

> **2026-07-27 晚間更正**：初版做咗「30d→7d→1d、只有長窗口借短窗口」，用戶糾正——
> 正確邏輯係**任何空窗口都以 1d → 7d → 30d 順序借（最新鮮優先，跳過自己）**，
> 1d 空都可以問 7d/30d 借。已照改。本文下面嘅覆蓋率數字（借後有數幾多格）唔受影響
> ——donor 集合一樣，只係揀邊個 donor 唔同；「邊啲格借咗邊個窗口」嘅分佈就會變。

一個窗口指標唔可顯示（value null 或 status 唔係 ready/stale）時，
按 1d → 7d → 30d 順序（跳過自己）向第一個有數嘅窗口借**成個結果**，
標 `status: "stale"` + `fallbackWindow: <來源窗口>`。

- 落腳點：[apps/web/src/lib/snapshot.ts](../../../apps/web/src/lib/snapshot.ts) 嘅
  `borrowWindowMetric()` / `borrowTrackedSales()`（view 層，producer 一行冇改）
- 覆蓋指標：`changePct`、`marketCapChangePct`、`trackedSalesChangePct`、`trackedSales`、
  五 grader 嘅 `topGradePopulationChangePct`
- **fail-closed 底線冇郁**：`composeChangePct` 照舊任一輸入唔齊就 null；
  同窗口指標互相頂替（價變當市值變）照舊禁止；只准借**另一個窗口嘅同名指標整個結果**。
  三個窗口都冇數 → 照舊空白，唔作數。
- 測試：[market-cap-delta.test.ts](../../../apps/web/src/lib/market-cap-delta.test.ts)
  「borrows the freshest displayable window (1d→7d→30d)」鎖死借數次序（最新鮮優先）同 fail-closed 殘留行為。

### 借數收益（30d，258 published，2026-07-27 實測）

| 指標 | 借前有數 | 借後有數 | 剩餘空白原因 |
|---|---:|---:|---|
| `changePct` | 235/258 | **258/258** | — |
| `marketCapChangePct` | 224/258 | **244/258** | 14 張三個窗口都砌唔出複合值 |
| `trackedSales` | 246/258 | 246/258（**0 收益**） | 12 張任何窗口都零成交，冇嘢可借（→ 07-27 晚間 eBay 接線後剩 1 張，見下注） |
| TAG POP change（board 100 張） | 0/100 | **86/100** | 14 張連 1d/7d 都冇 |
| SGC POP change | 12 | 13 | 大部分 SGC 卡完全冇歷史 |

### Live 版面殘留空格（:3800，30d，2026-07-27 15:00 實測）

| 版面 | Accumulating | Unavailable | 「—」 |
|---|---:|---:|---:|
| 主板 Top100 | 0 | 0 | 11（全部係成交欄） |
| Watchlist | 0 | 0 | 1 |
| PSA | 0 | 0 | — |
| BGS | 11 | 0 | 0（市值欄修復後 100/100 有 $） |
| CGC | 13 | 0 | — |
| SGC | 6 | 0 | — |
| TAG | 14 | 0 | — |

重量方法：`curl -s "http://localhost:3800/graders/bgs?period=30d" | grep -o 'Accumulating' | wc -l`
（其餘版面同理；市值欄數 `market-cap-cell` 入面 `$` 值）。

---

## 2. 寫低嘅數據問題（遲啲跟進，唔擋出街）

1. **TAG 30d POP 變動 100% 係借數**。TAG 歷史得 1d/7d 深度（07-22 死過、07-26 先復活寫入，
   見 CLAUDE.md「TAG 每日死亡」+ PROJECT_STATE §0.5 更正），30d 原生全部 accumulating。
   TAG 版 30d 有 86 格靠借數頂住（方向更正後優先借 1d，1d 冇先輪 7d）。
   **真 30d 要等 TAG 日更連續累積夠 30 日（~08-25）。**
2. **SGC POP 變動大面積冇任何歷史**：借極都借唔到，board 剩 6 格 Accumulating。
   要 SGC 歷史源（GemRate per-card history 有 sgc key，但大部分卡冇 SGC 條目）。
3. ~~**12 張卡任何窗口都零成交**~~ → **2026-07-27 晚間已修**：eBay PSA10 成交
   （`market_sale_observation` 過濾 psa/10）接入 producer `latest_sales()` 後，
   零成交剩 **1 張**（rank 96 ST10-006——snk 死於 06-15 兼 eBay 無 PSA10 成交，兩源都冇嘢可接）。
   主板 Top100「—」11 → 1；30d 成交環比原生 54 → 106。
   詳見 [2026-07-27-ebay-sales-wiring/FINDING.md](../2026-07-27-ebay-sales-wiring/FINDING.md)。
4. **producer coverage 計數器 bug**：snapshot 內 `coverage.graderPopulationChangeReady`
   對 5 grader × 3 窗口全部報 0，但 per-card 實測 PSA 30d 有 98 ready——計數器計錯。
   冇任何 UI 讀呢個欄，唔影響訪客；修 producer 時順手執（獨立 finding，唔屬今次改動範圍）。
5. **EB02-010 錯圖**（用戶欽點 100% 錯：1000×730 橫向 banner 當卡面）。換圖候選已查實
   （TCGplayer 641620，625×873 乾淨卡面，見
   [2026-07-27-board-gaps/FINDING.md](../2026-07-27-board-gaps/FINDING.md)），**等用戶拍板先入庫**。

---

## 3. 還原路徑（真數據源接返嚟之後）

1. `grep -rn "fallbackWindow" apps/web/src/` 揾晒借數位。
2. [snapshot.ts](../../../apps/web/src/lib/snapshot.ts)：`cardView()` 個 return 同
   `graderPopulations` 度拆走 `borrowWindowMetric()` / `borrowTrackedSales()` 包裝，
   直接用原生 per-window 值；跟手可以刪埋兩個 borrow function 同 `windowFallbacks` 表。
3. [types.ts](../../../apps/web/src/lib/types.ts) 刪 `MarketMetric.fallbackWindow`。
4. [market-cap-delta.test.ts](../../../apps/web/src/lib/market-cap-delta.test.ts)
   刪 borrow test，同刪「never reports a bigger swing」入面嗰個 fallbackWindow guard。

一句結論：**上線畫面已冇原生空格殘留（成交 12 張除外），代價係 30d 有一批格仔實際顯示緊
7d/1d 嘅數（全部有 `fallbackWindow` 標記可追）；真數據源接返嚟之後照第 3 節四步還原 fail-closed。**
