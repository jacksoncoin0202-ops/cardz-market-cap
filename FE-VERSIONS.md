# FE Versions — CARDZ Market Cap 前端

前端品牌／界面版本登記。每次換標或大改外觀要喺度留底，因為 logo 一出就會同步到 OG 分享圖、favicon、heatmap 分享卡等好多 surface。

產品世代（036／FE03、037／FE04 BOX）唔寫呢度。037／FE04 契約喺 `cardz-market-cap-fe-db-20260805/docs/HANDOFF_037_FE04.md`。呢份 `fe04` 只係 2026-08-13 logo 換標。

## fe04 — 2026-08-13（logo current）

新 pixel 風格 logo 全量替換（light + dark 雙版本）。

來源：
- Light：`LogoSwitch (3).png`（黑字 +  橙 CAP，透明底）
- Dark：`CardzMarketCapDarkMode (2).png`（白字 + 橙 CAP，透明底）

替換 surface：
- `apps/web/public/brand/logo-horizontal-light-{256,512}.png` — header 日光
- `apps/web/public/brand/logo-horizontal-dark-{256,512}.png` — header 暗黑
- `apps/web/public/brand/og-light.png`（1200×630 白底，全站 `og:image` 單一出口，`route-metadata.ts`）
- `apps/web/public/brand/og-dark.png`（1200×630 #0f0f0f 底）
- `apps/web/public/brand/icon-transparent.png` — 方形 CAP icon（1024）
- `apps/web/public/brand/icon-white-seam.png` — 方形 CAP icon（1024）
- `apps/web/src/app/icon.png`（32）/ `apple-icon.png`（180）/ `favicon.ico`（16/32/48）

邏輯改動：
- `apps/web/src/components/heatmap.tsx` wide 分支：棄用舊「橫向 logo 前 74/512 當 icon」裁切，改為直接畫新橫向 pixel logo（寬按原圖比例，上限 320×scale）。方形 CAP icon 由 `icon-transparent.png` 出，唔再依賴橫向圖有獨立 icon 段。

設計約束（下次換標要守）：
- 方形 icon 一律由 light logo 嘅 CAP 字樣出（橙色像素範圍內），唔好用字標殘段。
- CAP 黑框頂邊係斜線（pixel 傾斜設計），唔係殘留，唔好當 bug 修。
- light/dark 用獨立 asset，唔好用 CSS filter 改色。

已知：WhatsApp／Telegram／iMessage 等會 cache `og:image`，舊預覽可能殘留一段時間，屬對方 platform cache，非本站問題。

## fe02 — （首次 pixel logo 換標）

首次將舊漸變 logo 換做 pixel 風格。細節以當時 commit 為準。

## fe01 — （基準）

前端基準版本，舊漸變 logo 時期。
