# FE04 微互動統一（2026-08-16）

## 目標
統一晒成個站嘅 hover / active / transition 節奏，加返啲美術感，等用戶感覺到「成個站係一個整體」。

## 改動範圍

### 新增 CSS 變數（[globals.css](../apps/web/src/app/globals.css)）
```css
--control-transition: 170ms cubic-bezier(0.4, 0, 0.2, 1);
--interactive-transition: 180ms cubic-bezier(0.33, 1, 0.68, 1);
```

### P0 控制元件統一
- `.select-control`、`button.select-control`：border-color + color 用 `var(--control-transition)`
- `.period-selector button`、`.lang-filter button`：color 用 `var(--control-transition)`
- `.heatmap-tune-toggle`、`.heatmap-export`、`.share-button`：transition 用 `var(--control-transition)`
- `.primary-nav a`：color 用 `var(--control-transition)`

### P1 數據行 hover 統一
- `.desktop-ranking-table tbody tr`：background 用 `var(--interactive-transition)`
- `.mobile-rank-card`：background 用 `var(--interactive-transition)`，加 `content-visibility: auto` 優化長列表
- 新增 `.ranking-row-link` / `.mobile-rank-card` hover-lift：
  - `transform: translateY(-2px)` + `box-shadow: 0 4px 16px rgba(24,24,22,0.08)`
  - active 縮放 `0.99`

### P2 按鈕 active 統一
- `.primary-action:active`、`.heatmap-export:active`、`.share-button:active`、`.ranking-jump:active`、`.box-show-more:active`
  → `transform: scale(0.98)`

### P3 Share 成功態加強
- `.share-button[data-state="done"]`：加 background tint（`color-mix(in srgb, var(--positive) 8%, var(--control-bg))`）
- 新增 `@keyframes copy-pop`（300ms spring），套用到 `.copy-icon`

### P4 Heatmap transition 對齊
- `.heatmap-tile` base：加 `transform var(--interactive-transition)`，同時保留原有 box-shadow/filter/opacity
- 移除重複定義段落嘅 transition（避免覆蓋）

### Reduced motion
- 將 `.mobile-rank-card`、`.ranking-row-link`、`.hover-lift` 加入 `@media (prefers-reduced-motion: reduce)` 嘅 `animation: none; transition: none` 列表

## 依賴來源

### 受影響組件（已對齊）
- [rankings.tsx](../apps/web/src/components/rankings.tsx)：桌面 row 用 `className="ranking-row-link hover-lift"`，mobile card 用 `.mobile-rank-card`
- [copy-button.tsx](../apps/web/src/components/copy-button.tsx)：`.share-button[data-state]` 狀態 + `.copy-icon` 動畫
- [market-page.tsx](../apps/web/src/components/market-page.tsx)：`.heatmap-tile`、`.heatmap-export`、`.period-selector`、`.heatmap-tune-toggle`
- [header.tsx](../apps/web/src/components/header.tsx)：`.primary-nav a`、`.select-control`、`.lang-filter`

### 其他改動（非 FE04 微互動，但同 commit）
- **print-badge / print-chip**：日文版 vs 英文版 badge 色彩（[card-detail.tsx](../apps/web/src/components/card-detail.tsx)、[print-badge.tsx](../apps/web/src/components/print-badge.tsx)）
- **footer 重構**：`.site-footer` / `.footer-inner` / `.footer-brandline` / `.footer-methodology-title` / `.footer-methodology-body`（[market-page.tsx](../apps/web/src/components/market-page.tsx) 或 [layout.tsx](../apps/web/src/app/layout.tsx)）
- **heatmap-heading fix**：移除 `-webkit-line-clamp`，改 `overflow-wrap: break-word`，避免過渡寬度夾出「...」
- **box / sealed 新頁**：[apps/web/src/app/box/](../apps/web/src/app/box/)、[apps/web/src/app/sealed/](../apps/web/src/app/sealed/)、[sealed-market-page.tsx](../apps/web/src/components/sealed-market-page.tsx)、[sealed-rankings.tsx](../apps/web/src/components/sealed-rankings.tsx)
- **print-lang filter**：`PrintLangFilter` type + `cardLanguages` / `localizedCardLanguage`（[use-market-settings.ts](../apps/web/src/lib/use-market-settings.ts)、[i18n.ts](../apps/web/src/lib/i18n.ts)）

### 刪除嘅嘢（grader 相關）
- [grader-page.tsx](../apps/web/src/components/grader-page.tsx)、[grader-share-donut.tsx](../apps/web/src/components/grader-share-donut.tsx)、[grading-pulse.tsx](../apps/web/src/components/grading-pulse.tsx) 及相關 test
- [api/v1/graders/[grader]/route.ts](../apps/web/src/app/api/v1/graders/[grader]/route.ts)、[graders/[grader]/page.tsx](../apps/web/src/app/graders/[grader]/page.tsx)
- [grader-share.ts](../apps/web/src/lib/grader-share.ts) 及 test
- `PopulationDelta`（[rankings.tsx](../apps/web/src/components/rankings.tsx) 入面）

## 驗證
- `npm run build` 通過（Next.js 16.2.11）
- 無新增 TypeScript / ESLint error

## 部署
- 呢棵樹**唔係 live**（[PROJECT_STATE.md](../PROJECT_STATE.md) 講明）
- Live FE 出街車：`../cardz-market-cap-037-fe04-live`（`[deploy]` push `origin HEAD:main`）
- 要同步去 live 樹先會出街
