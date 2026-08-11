# HANDOFF — GEO 前端可引用性改造（2026-08-11）

> **內部文件，唔准出街。** 內文有內部識別碼同 gate 規則。
> 接手人：**Kimi**（前端）。後端／資料側另見 [HANDOFF_GEO_BACKEND_20260811.md](HANDOFF_GEO_BACKEND_20260811.md)。
> 呢份係交接，唔係計劃書——已經做咗嘅嘢喺第 3 節，仲要做嘅喺第 6 節。

---

## 0. TL;DR

用 GEOHub 診斷 cardzmarketcap.com 嘅「可被 AI 引用度」，首頁 55/100、卡片頁 85/100。改完之後兩頁都係 **100/100（六個維度全滿）**。改動集中喺 8 個檔 + 1 個新 component，**已經寫入 working tree，未 commit、未 deploy**。

同時執返一個真 bug：卡片頁**可見文字**同 client payload 曾經印住供應商代號（見第 4 節）。已清零。

**2026-08-11 更正**：呢份文件初稿寫嗰陣 tree 仲有別人未 commit 嘅 rank-0 fix 同 8,806 個 `.webp` 刪除纏住。佢哋已經喺 `f9b3ffd9`（19:05 +0900）commit 咗。下面第 3 節嘅檔案清單、第 5 節嘅 tree status、同埋個 patch 已經全部更新過——**用 `geo-fe-20260811-v2.patch`，舊嗰份已刪**。

**未完成：視覺 QA。** 我 render 唔到（第 7 節解釋點解），冇人親眼睇過個新 panel 排得靚唔靚。呢個係交俾你嘅第一件事。

---

## 1. 硬規矩

1. **唔准喺出街 HTML 出現供應商名。** 係 owner 明示嘅商業要求：**「方法可以講，來源唔准講」**。
2. 新加嘅文案只可以講**點計**（reference price 點嚟、population 點對齊），**唔可以**講數據由邊個平台嚟。
3. **⚠️ 冇任何自動 gate 幫你守呢條線。** `scripts/canary-public.mjs`（以前個 `FORBIDDEN` regex）**喺呢個 tree 已經冇咗**——2026-08-07 commit `78848067` 連同兩個 caller 一齊刪走。今日 `package.json` 得 `dev` / `build`，`.github/workflows/` 係空，AWS deploy 指令只有 health assert。即係**全靠人手守**。詳情同重建方案喺 [HANDOFF_GEO_BACKEND_20260811.md](HANDOFF_GEO_BACKEND_20260811.md) 第 3 節。
4. 呢個 tree 冇 unit test。**唯一 gate 係 `npx tsc --noEmit`**，改完一定要行。

---

## 2. 你要知嘅背景：GEOHub 點計分

GEOHub 係本機 CLI（`~/GEOHub`，**只行得喺 WSL**）。佢個 `diagnose` 掃一份 HTML 出六個維度分。**規則好硬、好蠢，但可預測**——今次個 100 分就係踩住呢啲規則攞返嚟嘅，**改文案嗰陣唔好整跌佢**：

| 維度 | 計法 |
|---|---|
| evidence | **只有 20 或 100**。可見文字有 `source` / `reference` / `citation` / `method` / `方法` / `来源`(簡體) 就 100 |
| authority | 同上二元。要有 `author` / `editor` / `expert` / `about` / `contact` / `作者` / `关于` |
| freshness | 同上二元。要有 `updated` / `更新` / 或 `20\d{2}[-/.年]\d{1,2}` |
| structure | 啱啱**一個** `<h1>` + 至少一個 `<h2>` 先有 100 |
| extractability | 底分（可見字 ≥300 → 50）+ 每個 `<main>`/`<article>`/`<ul\|ol\|dl>`/`<table>`/合法 JSON-LD +10，封頂 +50 |
| discoverability | title / meta description / canonical / 冇 noindex，四個 boolean 平均 |

**三個致命陷阱：**

- **只讀 rendered visible text。** 所有 attribute（`alt`、`title`、`datetime`）、`<meta>`、`<script>` 入面嘅嘢，**一律唔讀**。JSON-LD 內容完全唔計分，只當 extractability +10 信號。
- **文字係逐個 text node 用空格 join。** 即係 `<span>2026</span><span>-08-11</span>` 會變 `2026 -08-11`，freshness match 唔到。**日期同 label 必須喺同一個 text node，而且要 ISO。**
- **token list 得簡體。** 繁體「來源」「參考」match 唔到，「方法」同「更新」兩種字體通用。所以英文 kicker `METHOD & DATA` 同英文 byline 喺**五個語言都保留**——唔好「順手」翻譯走佢哋，一譯就跌分。

完整規則喺 skill：`~/.claude/skills/geohub/SKILL.md`。

---

## 3. 已經改咗咩

### 檔案清單

| 檔 | 改動 |
|---|---|
| **新增** [apps/web/src/components/provenance.tsx](../../apps/web/src/components/provenance.tsx) | 方法面板 component，首頁同卡片頁共用 |
| [apps/web/src/lib/i18n.ts](../../apps/web/src/lib/i18n.ts) | `interface Copy` 加 `provenance` block，五個語言全寫齊（+78 行） |
| [apps/web/src/components/market-page.tsx](../../apps/web/src/components/market-page.tsx) | 掛 `<Provenance updatedAt={snapshot.effectiveAt} />`；Dataset JSON-LD 加 `publisher` |
| [apps/web/src/components/card-detail.tsx](../../apps/web/src/components/card-detail.tsx) | 掛 `<Provenance updatedAt={card.pricePsa10.asOf \|\| snapshot.effectiveAt} />`；VisualArtwork JSON-LD 加 `dateModified` + `publisher`；**拆走可見嘅供應商名** |
| [apps/web/src/components/rankings.tsx](../../apps/web/src/components/rankings.tsx) | 2 個 `title=` tooltip 唔再印供應商名 |
| [apps/web/src/components/heatmap.tsx](../../apps/web/src/components/heatmap.tsx) | 1 個 tooltip 同上；legend `div` → `ul/li`（extractability 信號 + 讀屏語意） |
| [apps/web/src/components/header.tsx](../../apps/web/src/components/header.tsx) | Footer 加 byline（site-wide authority 信號，連「卡搵唔到」嗰版都覆蓋到） |
| [apps/web/src/lib/snapshot.ts](../../apps/web/src/lib/snapshot.ts) | `priceAnchorSource` 唔再入 client payload（projection 層硬 `null`） |
| [apps/web/src/app/globals.css](../../apps/web/src/app/globals.css) | `.provenance-*`、`.footer-byline` 樣式 + mobile media query；`.heatmap-legend` 跟住轉 list |

`apps/web/next-env.d.ts` 亦有 M，但係 Next dev 自己 regen 嘅，**唔關今次事**。

**已經入咗 HEAD、唔喺今次改動範圍**（`f9b3ffd9`「release: finalize CARDZ 036 FE03 daily public sync」，2026-08-11 19:05 +0900）：

- `apps/web/src/lib/live-db-snapshot.ts` — rank-0/unranked 狀態、collectorNumber 顯示值邏輯、awaitingFreshPrice
- `apps/web/src/lib/server-snapshot.ts` — rank-0 sort fix
- `packages/market-data/src/schema.ts` — rank-0 comment

呢三個檔係另一批人嘅業務邏輯，已經 commit 咗，同 GEO 無關。呢份文件初稿曾經將佢哋列做今次改動——**係錯嘅**，因為初稿寫嗰陣佢哋仲未 commit。`git checkout .` 唔會炸佢哋（佢哋已經喺 HEAD），但會炸埋上面 8 個未 commit 嘅 GEO 檔，所以一樣**絕對唔准**。

### 拎個 patch

working tree 而家得 14 個 entry（見第 5 節），我抽咗一份淨係今次 GEO 改動嘅 patch：

- [docs/handoff/geo-fe-20260811-v2.patch](geo-fe-20260811-v2.patch)（23 KB，`git apply --check --reverse` 已驗過，基於 HEAD `f9b3ffd9`）
- 新檔冇喺 patch 入面（未 tracked），另存一份：[provenance.tsx.new](provenance.tsx.new)
- **舊 patch `geo-fe-20260811.patch` 已刪**——佢基於錯誤基線（當時 rank-0 fix 仲未 commit），`git apply --check` 會 fail

想睇返改咗啲乜：

```bash
git -C 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805' diff -- apps/web/src
```

想淨係還原今次改動（**唔好用 `git checkout .`**）：

```bash
git -C 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805' apply --reverse docs/handoff/geo-fe-20260811-v2.patch
```

### i18n 契約

`interface Copy` 加咗：

```ts
provenance: {
  kicker: string;          // "METHOD & DATA" —— 五個語言都保持英文，evidence token 靠佢
  title: string;
  body: string;
  steps: { term: string; detail: string }[];   // 三條：Reference price / Population / Gaps
  updated: string;         // 只係 label，日期由 component 補
  byline: string;          // 含 "Editorial" —— authority token 靠佢，唔好譯走
  anchorSwitched: string;  // 取代原本印住供應商名嗰句
};
```

`copy: Record<Locale, Copy>` 覆蓋 `en` / `zh-TW` / `zh-CN` / `ja` / `ko`。**少一個 key 就 tsc 炸**——呢個係好事，唔好拆。

### 日期點解要自己切 ISO

見 [provenance.tsx](../../apps/web/src/components/provenance.tsx) 個 `isoDay()`：站內原本用 `formatObservationDate` 出「Aug 10, 2026」呢種 medium 格式，機器認唔到。所以 panel 嗰行自己切 `YYYY-MM-DD` 並且**同 label 放埋同一個 text node**。畀人睇嘅 medium 日期喺 `.data-time` 嗰行照舊，冇動。

---

## 4. 順手修咗嘅真 bug（唔好 revert）

改之前，卡片頁**可見文字**印住 `Historical anchor: <供應商代號>`，另加 3 個 `title=` tooltip，而且 `priceAnchorSource` 直接 serialize 落 client payload——`view-source` 就見到。合共 6 處。

原因：呢個 field 由後端一路 pass 到 UI，UI 只需要「換咗錨點」呢個 boolean，但就照原字串印出嚟。

現況（本機實測）：`/`、`/pokemon`、`/one-piece`、`/watchlist`、`/card/*` **供應商字眼 = 0**。

> **後端側仲有手尾**：呢個 field 由後端照發，前端而家係喺邊界 null 走佢——即係「前端幫後端執手尾」。正路係後端唔好發個字串出嚟。已經寫低喺 Codex 嗰份 handoff。

---

## 5. Working tree 現狀（**開工前必讀**）

呢個 tree 而家**相對乾淨**（`f9b3ffd9` 已經將 rank-0 fix 同 `.webp` prune commit 咗）：

```
14 個 git status entry
   8 M   apps/web/src 入面 8 個檔（全部係今次 GEO 改動）
   6 ??  包括 docs/handoff/、.claude/、provenance.tsx 新檔等等
```

**歷史背景**（呢份文件初稿寫嗰陣）：tree 曾經有 8,911 個 entry，其中 8,806 個係 `data/public/market-assets/\*.webp` 被 `scripts/prune_public_assets.py` 刪走。嗰批已經喺 `f9b3ffd9` commit 咗，唔再係問題。

**三條唔准做：**

1. **唔准 `git checkout .` / `git reset --hard`** —— 雖然 `.webp` 已經 commit 走，但 `apps/web/src` 仲有 8 個 GEO 改動檔未 commit，一 checkout 就全冇。
2. **唔准順手 commit 全部** —— 要 commit 就明確列檔名（見第 3 節嘅 9 個項目）。
3. **`npm run build` 會刪走 `apps/web/public/market-assets`**。做呢件事嘅係 [apps/web/scripts/prepare-standalone.mjs](../../apps/web/scripts/prepare-standalone.mjs)（build 最後一步，`CARDZ_BUILD_TARGET=node` 時行），係**故意**嘅——唔畀 build 期嘅 seed 圖蓋過 generation-aware route。卡圖真身住喺 `data/public/market-assets/`。
   （[COLLECTION_RUNBOOK.md:759](../COLLECTION_RUNBOOK.md) 仲寫住係 `sync-snapshot.mjs` 做——**嗰個檔喺呢個 tree 根本唔存在**，runbook 過時。）

---

## 6. 交俾你嘅工作（按緩急）

### P0 — 視覺 QA（我做唔到，見第 7 節）

dev server 之前行緊但而家 **timeout 咗**（19:09 測試冇回應）。要你自己起返：

```powershell
# 起 server（要 MySQL 3308 行緊）：
cd 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805\apps\web'
$env:CARDZ_DATA_MODE='live-db'; npm run dev -- --hostname 127.0.0.1 --port 3800
# 或者直接用 VS Code launch config「fe-live-db-3800」
Start-Process 'http://localhost:3800'
```

要睇嘅路由：

| 路由 | 望邊度 |
|---|---|
| <http://localhost:3800/> | 排行榜下面新 panel |
| <http://localhost:3800/pokemon> | 同上 |
| <http://localhost:3800/one-piece> | 同上 |
| <http://localhost:3800/watchlist> | 同上 |
| <http://localhost:3800/card/cmc_fc229f7ae1b256b2119fa79b> | 圖表下面新 panel + 改咗嘅 anchor 字 |

Checklist：

- [ ] `METHOD & DATA` kicker 同現有 `.section-kicker` 風格一致
- [ ] `dl` 三條（Reference price / Population / Gaps）排版唔崩，`dt`/`dd` 對齊
- [ ] 手機闊度（≤640px）唔會爆——CSS 有 media query 但未睇過
- [ ] Dark／light（如果站內有得切）兩邊都得
- [ ] 五個語言切一次：`en` / `zh-TW` / `zh-CN` / `ja` / `ko`，日文韓文長字唔好撞爆
- [ ] Footer byline 唔會同原有 footer 內容打交

### P0b — 我寫嘅文案有一句同 pipeline 對唔上（**已改，唔使郁**）

[i18n.ts](../../apps/web/src/lib/i18n.ts) 個 `provenance.steps[2]`（Gaps）原本寫：

> *A card with too few completed sales in the window is marked accumulating instead of being given a filled-in number.*

**呢個機制唔存在。** 後端 recon 查實：`accumulating` 呢個 status 由 window coverage / change-pct 決定（[pipelines/operator_fe_export.py:893](../../pipelines/operator_fe_export.py)、[pipelines/market_metrics.py:95](../../pipelines/market_metrics.py)），**同「成交筆數夠唔夠」冇關係**。冇任何 code 因為成交少過 N 筆而標 accumulating。

**已經改咗（五個語言）**：而家講「insufficient data coverage」／「資料覆蓋不足」／「数据覆盖不足」／「データカバレッジ」／「데이터 커버리지」，**唔再提成交筆數**。呢個係安全模糊版，等後端（Codex）定咗真實閘門（3 筆/5 筆/0 筆）先再精確化。

同一段落嘅舊 footer 文案（「no fewer than five verified PSA 10 sales inside every rolling 30-day window」，[i18n.ts:186](../../apps/web/src/lib/i18n.ts)）一樣對唔上：現行 pipeline 個門檻係 **3**（`MIN_EBAY_SALES = 3`），而且**每日跑嗰條線根本唔行 eBay 呢條路**，走嘅係 PriceCharting 指引價，**零成交筆數要求**。**呢句仲未改**——佢係舊有 `methodology.body`，唔係今次 GEO 改動範圍，改唔改係 owner 決定。

**點做：** 如果你覺得「資料覆蓋不足」呢個講法唔夠準確，等後端定咗閘門先再改。改嗰陣記住保留 `method` / `updated` 呢啲 token，唔好整跌 GEO 分（見第 2 節）。

### P1 — 收貨驗證

```bash
cd 'C:\Users\jackson0202\Documents\Playground\cardz-market-cap-fe-db-20260805\apps\web'; npx tsc --noEmit
```

要 0 error（我最後一次行係 exit 0）。

再驗一次冇泄漏（PowerShell）：

```bash
$h=(Invoke-WebRequest 'http://localhost:3800/card/cmc_fc229f7ae1b256b2119fa79b' -UseBasicParsing).Content; "leak=$([regex]::Matches($h,'(?i)snkrdunk|gemrate|\bebay\b').Count)"
```

要 `leak=0`。

### P2 — 想再驗 GEO 分（可選）

```bash
wsl.exe -e bash -lc 'cd ~/GEOHub && .venv/bin/geo-seo-hub diagnose --input /tmp/brief.json --output runs'
```

brief 要放**已 render 嘅 HTML**（用 `Invoke-WebRequest` 抓 SSR，唔好貼 source code）。基線：首頁 `run-6f7709068f43`、卡片頁 `run-97c40298e7e9`，兩個都係 100/100。

### P2b — 刪走我寫嘅一個錯 comment（**已改，唔使郁**）

[i18n.ts:100-101](../../apps/web/src/lib/i18n.ts) 我寫咗一句 comment，話 provider 名係「canary FORBIDDEN，任何 page HTML 撞到就 deploy 失敗」。**呢句係錯嘅**——嗰個 script 早喺 2026-08-07 已經刪咗（見第 1 節）。[provenance.tsx:9](../../apps/web/src/components/provenance.tsx) 同一句都要一齊改。

**已經改咗**：兩個檔都改成「供應商代號唔准曝光。⚠️ 呢條線目前冇自動 gate（canary-public.mjs 已刪），全靠人手守。」

### P3 — 已知未修（你決定做唔做）

- **AI crawler 政策同我之前講嘅唔同。** [apps/web/src/app/robots.ts:19](../../apps/web/src/app/robots.ts) **只擋兩個**：`GPTBot` 同 `CCBot`。**ClaudeBot / anthropic-ai / PerplexityBot / Google-Extended / Bytespider 全部放行**，`OAI-SearchBot` 亦係故意放行（search 同 training 分開，做法啱）。即係今次啲 GEO 改動**大部分 AI 引擎讀得到**。要唔要補齊 blocklist 係 owner 政策決定，**唔好自己改**。
- `/card/*` 嘅 leak 防護：見 Codex 嗰份第 3 節，正路唔係喺前端加 regex，係喺 snapshot bake 度 assert card id 格式。

---

## 7. 點解視覺 QA 交咗俾你

我試過三條路，全部唔通：

1. **in-app browser pane** —— 成頁卡死喺 `loading.tsx` 個 skeleton。真正嘅 `.page-shell`（入面有 `provenance-panel`）喺一個 `display:none` 嘅 div 度，React streaming reveal 完成唔到。server 側報 `TypeError: controller[kState].transformAlgorithm is not a function`，console 報 "Connection closed"。**呢個影響成版嘢，唔止我改嗰段**——係 Next 16 dev streaming 撞個 pane 嘅 transport。
2. **screenshot** —— 「Browser pane is not displayed」。
3. **真身 Chrome CDP `127.0.0.1:9222`** —— 冇開，attach 唔到。

**但 SSR HTML 本身係完整同正確嘅**：`Invoke-WebRequest` 攞返嚟 1,091,362 bytes，`provenance-panel` × 1，供應商字眼 × 0。即係「HTML 啱、冇人睇過個樣」。

用真身瀏覽器開就會 render 到——所以呢個係 P0 但唔係難題。

---

## 8. 呢個 tree 嘅身份（唔好搞錯）

Playground 有 5 個 `cardz-*` folder。**live 前端係呢個 tree**（`cardz-market-cap-fe-db-20260805`），唔係 `cardz-market-cap/`（舊 checkout）。

認法：live HTML 有 `Historical anchor:` 呢段 markup，只有呢個 tree 個 `card-detail.tsx` 有。dev server config 喺 [.claude/launch.json](../../.claude/launch.json) 個 `fe-live-db-3800`（要 MySQL 3308 行緊）。

`apps/web` 冇自己嘅 `node_modules`（workspace hoist 去 repo root）。`npx vitest run` 會話「No test files found」——係事實，呢個 tree 冇 unit test，唔好當環境壞咗。`npx eslint .` 會炸一個 Node ESM `moduleResolve` stack，**本身就爛**，同今次改動無關。
