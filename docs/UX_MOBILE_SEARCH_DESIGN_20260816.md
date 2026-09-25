# 手機／電腦搜尋排名 UX 重設計（2026-08-16，設計稿，未實作）

完整稿（含互動原型、截圖、量度）：https://claude.ai/code/artifact/cdb81f2a-c0ab-4e9b-a87d-d697ee03025a
對象：`apps/web` working tree（release/037-fe04-box，未 commit 嘅 explore-bar / rankings 改動）。

## 現況量度（390×844，dev :3901，Playwright + DOM rect）
- 排行區第一張卡 top ≈ 421px；搜尋中 ≈ 486px。
- 排行區 control：語言 5 + 時段 6 + 範圍＋搜尋 + 排序 5（＋方向）= 4 行；搜尋中再多「計數 + 清除搜尋」一行。
- 同頁兩個一模一樣嘅時段選擇器（heatmap + ranking，已同步同一 URL period）。

## 設計（手機）
1. 預設：h2 + **一行**「🔍 搜尋卡牌 ｜ ⇅ 排序」+ 列表表頭；時段變表頭「6M ▾」popover。冇語言列、冇排序 chips、冇範圍 dropdown。
2. 搜尋中：熱力圖 + h2 收起，搜尋列 sticky；結果數入框內（「148 張」）；✕ 只清 q；結果先出 50 + 「再顯示」；rank 冇數字出「—」。
3. 搵唔到：空狀態畀「喺全站搜尋 →」（真 navigate 去 `/?q=…`），取代常駐範圍 dropdown。範圍 = route。
4. 排序 sheet：排序單選 → 方向（非 rank 先出）→ 印刷語言（榜上多過一種先出）；「套用」= **一個** `update()`；「重設」還原 sort/dir/lang。
5. 非預設篩選出一行可撳走 chips（`價格 ↓ ×`、`JP ×`）。
6. 桌面 table 同手機 list 只 render 一個（matchMedia）。

## 設計（電腦）
表頭排序／語言／時段／搜尋不變。拆走：範圍 dropdown、「清除搜尋」掣、計數 chip（入框內）。結果先出 80（`CATALOG_LIST_CAP`）。

## 邏輯規則
- URL 係唯一真相；範圍 = route；local state 只准：輸入框文字、sheet 草稿、popover 開關。
- 一個手勢一次寫入；`update()` 要 call 時讀 `window.location.search`，唔准 close over render 時嘅 params。
- 見到嘅掣一定影響下面張表。
- printLang 喺 pool 冇時要 `update({printLang:"all"})` 寫返 URL，唔好靜靜復活。
- catalog fetch 失敗保持 `null` + error flag，退化做當頁過濾，唔好 `[]`。
- 結果有上限；只 render 一個版面。

## 撞機／矛盾（Opus 5 ×3 實測，全 CONFIRMED）
| # | 級 | 問題 | 位置 | 修 |
|---|---|---|---|---|
| 1 | 會撞 | 搜尋結果無上限：一個字母 3,178 行／89,782 nodes，主線程 1.35s | rankings.tsx:150 | `limit: CATALOG_LIST_CAP` + 再顯示 |
| 2 | 狀態 | `update()` stale params：打字後 300ms 內撳 chip／時段互相覆蓋 | use-market-settings.ts:137 | 讀 live `window.location.search` |
| 3 | 狀態 | printLang 喺 pool 變時復活（294 → 1 張） | rankings.tsx:146 | demote 時寫返 URL |
| 4 | 狀態 | catalog 失敗 `setCatalog([])` → 全部「未合資格」 | rankings.tsx:126 | 保持 null + flag |
| 5 | 狀態 | 範圍 dropdown 冇 q 時死掣；清除後 liveScope 唔還原；kicker 講錯範圍 | rankings.tsx:155/246/193 | 拆走（範圍 = route） |
| 6 | 狀態 | 360px 方向掣被 clip；EN 摺 3 行 | globals.css:2408, explore-bar.tsx:263 | 手機拆 `.explore-dir` |
| 7 | 狀態 | 語言列闊度跟 h2 文字（241/144/210/194px） | globals.css:1805 | ≤680 `.ranking-heading > div{align-self:stretch}` |
| 8 | UX | 入搜尋列表跳 52px；✕ 撐高 6px | explore-bar.tsx:229 | 框固定 44px；計數入框 |
| 9 | UX | aria-live 每 debounce 播；分母 100→1599；scope trigger a11y | explore-bar.tsx:239/171/221 | live region 獨立 debounce ~1s |
| 10 | UX | rank 0 喺 26px 欄塞「Awaiting fresh price」 | rankings.tsx:328 | 「—」+ title |
| 11 | 細 | tie-break rank 0 排前 | list-explore.ts:128 | rank 0 → MAX |
| 12 | 細 | `collectorNumber` fold 冇 null guard | catalog-search.ts:155 | `?? ""` |
| 13 | 細 | 隱藏 table 照 render；haystack 冇 cache；SiteSearch 冇 debounce；PeriodSelector 死 props；heatmap.tsx:282 註解錯 | 多處 | 見稿 |

## 實作次序
A 邏輯先（唔改樣）→ B 手機瘦身（ExploreBar 拆 chips/dir/scope/reset；新 `sort-filter-sheet.tsx`；表頭時段 popover；toolbar sticky）→ C 電腦收尾（拆 scope dropdown、空狀態全站掣、box-rankings 跟 API）。

驗收：390×844 第一張卡 top ≤ 320；打「char」300ms 內撳排序 URL 同時有 `q=char&sort=price`；一個字母 DOM < 8,000、longtask < 200ms；斷網搜仍出當頁結果；四語言 360px toolbar 唔溢出；/pokemon 搜 luffy 空狀態一粒掣去 /?q=luffy。
