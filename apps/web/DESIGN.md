# CARDZ Market Cap — 前端設計系統（FE05）

> **呢份 doc 由 code 推導；同 code 唔一致時 code 贏（[AGENTS.md](../../AGENTS.md)）。**
> 見到差異 = 改呢份 doc，唔係改 code 去夾 doc。所有 line anchor 指向
> `apps/web/src/app/globals.css`（2,844 行）除非另有註明；行數會漂，`grep` 個 selector 快過信行數。

CARDZ Market Cap 唔係 landing page，係**資料密集嘅市場終端機**。
第一屏（`/`）係一個 100 格 treemap 加一行總市值 —— 呢個係產品，唔係 hero。
任何「加多啲視覺」嘅提案，第一個問題永遠係：**佢會唔會令第一屏慢咗、或者搶咗熱力圖嘅注意力？**
會 → 唔做，或者只落內頁。

---

## 1. Token

冇 Tailwind、冇 shadcn、冇 CSS-in-JS、冇 `next/font`。全部係 CSS custom property，
淺色定義喺 `:root`（:1–140），深色只覆寫**顏色類**嘅 token 喺 `[data-theme="dark"]`（:142–221）。

### 1.1 顏色（每個都有 dark 對版）

| Token | Light | Dark | 用途 |
|---|---|---|---|
| `--ink` | `#171717` | `#ececec` | 主文字 |
| `--muted` | `#6f6f6b` | `#9b9b96` | 次要文字 / 標籤 |
| `--paper` | `#f7f7f5` | `#101010` | 頁底 |
| `--surface` | `#fff` | `#1a1a1a` | 卡片 / panel |
| `--soft` | `#f0f0ed` | `#242424` | 內嵌區塊（provenance panel） |
| `--line` / `--line-strong` | `#e6e6e2` / `#cecec8` | `#2c2c2a` / `#444441` | 邊框兩級 |
| `--accent` | `#b85416` | `#e8823f` | 唯一品牌強調色 |
| `--positive` / `--negative` | `#23775b` / `#a63b52` | `#4cb893` / `#d4748c` | 升 / 跌（**會被 `data-updown` 對調**） |
| `--frame-up` / `--frame-down` | `#1B714E` / `#972646` | `#2FA37C` / `#B84A6E` | heatmap tile 專用升跌色 |
| `--ice` / `--lilac` | `#e8f0f1` / `#eeeaf4` | `#1c262a` / `#241f2e` | 卡圖底色漸變 |
| `--grader-psa/bgs/cgc/sgc/tag` | :124–128 | :206–210 | 評級機構身份色 |
| `--print-badge-*` | :131–139 | :212–220 | 印刷版本 badge（日文暖、英文冷） |
| `--holo-a1` / `--holo-a2` | `#fff` .42 / .10 | `#fff` .26 / .06 | 卡圖 sheen 高光／尾巴（FE05 WS2） |
| `--holo-rainbow` | conic 五段低 alpha | 同上，alpha 高少少 | 卡圖彩虹層（整條 gradient 做 token） |
| `--spot-a` | `#fff` .34 | `#fff` .20 | 卡圖 spotlight（`mix-blend-mode: soft-light`） |
| `--row-spot` | accent .10 | accent .16 | 相關卡格仔 hover spotlight |
| `--glow-accent` | accent ring + 光暈 | 同上，dark accent | `#1` chip、top mover chip 嘅 box-shadow |
| `--live-dot` / `--live-dot-halo` | `#1f9d55` | `#3ec98a` | live 徽章綠點 |
| `--live-beam` / `--live-beam-idle` | accent / `--line` | accent / `--line-strong` | live 徽章 border beam |

`--holo-idle`（`0.28`，:root 只定義一次）唔係顏色係透明度：手機／reduced-motion／未 hover
嗰陣個 sheen 就係呢個 alpha。`--live-dot` **故意唔用 `--positive`**：`--positive` 會俾
`data-updown` 對調（§2.2），揀咗「紅升」嘅用戶個「資料新鮮」點會變紅 = 講錯嘢。
狀態色同升跌色係兩件事。

**規矩：** 新 CSS 一個 hardcode 色都唔准。要新顏色 → 加一對 token（light + dark），唔係喺
component CSS 寫死 `#b85416`。呢條係 `fe-design-review` gate 嘅硬檢查。

### 1.2 高度 / 圓角 / z 階

| Token | 值 | 備註 |
|---|---|---|
| `--elev-1/2/3` | :24–26 | 三層階梯，同一光源（上方），y-offset 主導、blur 隨高度倍增 |
| `--shadow` | :22 | `--elev-3` 嘅舊別名，新 code 唔好再用 |
| `--section-radius` / `--control-radius` | `24px` / `12px` | 大區塊 / 控件 |
| `--card-img-radius` | `9%` | **已廢（:16）**，卡圖行原生 RGBA 圓角 |
| `--z-header` / `--z-overlay` / `--z-modal` | `80` / `100` / `120` | header < 浮層 < modal，唔准喺 component 度自己發明 z |
| `--header-height` | `68px` | `scroll-margin-top` 全部要跟佢 |

### 1.3 字體 / 字階（FE05 新增，:31–64）

| Token | 值 | 用途 |
|---|---|---|
| `--font-sans` | system stack（:34） | `body` 唯一字體。**冇 self-host、冇 `next/font`、冇 display face** |
| `--font-mono` | `"SF Mono", "Roboto Mono", ui-monospace, monospace` | 編號 / ID 類欄位 |

`--font-mono` 之前喺四個地方逐字複製；FE05 收埋做一個 token，四處（`.collector-cell` :1509、
`.mobile-card-number` :1657、`.identity-list dd` :2147、`.box-set-chip` :2724）全部 call 佢。
驗證：`grep -c "SF Mono" globals.css` → **1**（就係 token 定義嗰行；plan 寫 →0 係手民之誤，
token 定義本身就住喺 `globals.css`，→0 冇可能）。真正嘅驗收係 **零 call site 硬寫**：
全 `apps/web/src` grep `font-family` 剩返 3 × `var(--font-mono)` + 1 × `var(--font-sans)` + 1 × `inherit`（`.chart-label`）。

流體字階，六級。**step-2/3/4 逐字照抄 FE04 原本嗰條 `vw` clamp**，唔係另外畫一條線。

| Token | 值 | ramp 窗口 | 已落點（WS1） |
|---|---|---|---|
| `--step--1` | `clamp(12px, 1.3vw, 13px)` | 923–1000px | （未落用，留畀 WS2–5 新 CSS） |
| `--step-0` | `clamp(14px, 1.5vw, 15px)` | 933–1000px | （未落用，同上） |
| `--step-1` | `clamp(16px, 2.222vw, 17px)` | 720–765px | （未落用，同上） |
| `--step-2` | `clamp(20px, 3vw, 26px)` | 667–867px | `.content-body h2`（= FE04 原式） |
| `--step-3` | `clamp(26px, 3.4vw, 42px)` | 765–1236px | `.hub-hero h1`（= FE04 原式） |
| `--step-4` | `clamp(28px, 5vw, 42px)` | 560–840px | `.content-hero h1`（= FE04 原式） |

#### 收編規矩（WS1 review 之後收緊，硬標準）

**「零 reflow」= 喺 390 / 768 / 900 / 1024 / 1280 五個闊度（最少）逐個量 rect delta ≤ 2px。**
淨係量 390 同 1280 兩端**唔算數**：

> FE05 第一版將三條 `vw` clamp 改成 390→1280 直線，兩端一模一樣，但中段係另一條曲線 ——
> `.content-hero h1` @900 差 **−5.97px**、`.hub-hero h1` @768 差 **+6.68px**、
> `/methodology`@900 成頁位移 **57.74px**。**端點相同 ≠ 曲線相同。**

推論兩條，任何 WS 換 `clamp()` 都要跟：

1. **原式係 `vw` clamp → 照抄，唔准重寫。** 想換數就係設計改動，要 owner 拍板 + 補量度。
2. **原式係斷點階梯（`@media` 換 `font-size`）→ 唔准轉 fluid。** 連續函數夾唔到階梯，
   ramp 掃過嗰段一定有 ≤1px 偏差，段落一 rewrap 就係幾十 px。要轉 = 設計改動。

另外：**截圖／量度矩陣要按 selector 嘅真實 call site 揀頁，唔准按「呢幾頁順手」。**
`.hub-lead` 全站**只有 `/market-report`** 出（`seo-table.tsx:110`，`block.intro` 存在先渲染）；
`.provenance-panel` 喺 `/`、`/box`、`/pokemon`、`/card/[id]`；`.content-*` 喺 `/about`、`/methodology`、`/faq`、`/glossary`。

**已知未收編（欠單，要 owner 拍板先郁）：**

| 位置 | 現況 | 點解未收 |
|---|---|---|
| `.content-answer` | 17px / ≤720px 16px | **斷點階梯**。試過落 `--step-1`，ramp 掃過 720–765px，量到 `/methodology`@721 位移 **33.44px**、`/about`@721 **6.25px** |
| `.hub-lead` | 14px / ≥981px 15px | **斷點階梯**。試過落 `--step-0`，量到 `/market-report`@981 **13.58px**、@940 **4.69px** |
| `.provenance-panel h2` | 15px / ≤900px 14px | **斷點階梯**（步位喺 900，同 `.hub-lead` 嘅 981 唔同步，一個 token 服侍唔到兩個）。量到 `/` 同 `/card/[id]`@940 **1.24px** |
| `.hub-answer` | `clamp(14px, 1.4vw, 17px)` | 同 `.content-answer`（16→17px）係**同一個角色兩套字級** —— 真設計缺陷。統一落 `--step-1` 喺 390px 量到成頁跌 **49.7px** |
| `.hub-note` | 12px / ≥981px 12.5px | 斷點階梯；`--step--1` 上限 13px，1280px 量到累積 **22.9px** |
| `.provenance-body` | 13px 平頭 | `--step--1` 喺 390px 係 12px，量到 **5.1px** |

即係話 **WS1 淨係收編咗三個本來就係流體嘅落點**（`--step-2/3/4`），六個階梯／平頭落點原封不動。
`--step--1/0/1` 留住係俾 WS2–5 嘅**新** CSS 用（新 CSS 冇 baseline 要保）；
WS5 完咗仲係零 call site 就刪。**WS2 更新：`--step--1` 有咗第一個 call site（`.mover-chip`）；
`--step-0` / `--step-1` 仍然零。**`.content-answer` vs `.hub-answer` 嗰個「同一角色兩套字級」
值得 owner 一次過拍板。

字距：`--tracking-tight` `-0.02em`（大標題）、`--tracking-wide` `0.08em`（大寫細標籤）。
全局 `h1,h2,h3` 嘅 `-0.035em`（:265）**唔喺字階入面** —— 佢係首頁聲線嘅一部分，唔准順手改。

### 1.4 間距（FE05 新增，:67–74）

`--space-1…8` = 4 / 8 / 12 / 16 / 20 / 24 / 32 / 40。
新 CSS 用 token；現有硬數逐步收編，但**收編唔准改 px 值**（換 token = 零 delta，唔係「順手調靚啲」）。

### 1.5 動效曲線

`--ease-standard: cubic-bezier(0.22, 1, 0.36, 1)`（:27）、`--ease-spring: cubic-bezier(0.34, 1.56, 0.64, 1)`（:28）、
`--control-transition: 170ms`（:91）、`--interactive-transition: 180ms`（:92）。
新動效由呢四個揀，唔好再開新 timing。

---

## 2. 三條軸：任何 UI 都要三條同時啱

### 2.1 `data-theme`（light / dark）

`<html data-theme>` 由 `document-language.tsx:17` 嘅 inline script **喺 paint 之前**寫落去
（讀 `localStorage["cardz-theme"]`，冇就跟 OS）。Server **唔出** `data-theme`（避免 hydration mismatch），
`use-market-settings.ts:134` 之後接手。

- 落 dark 樣式一律用 **`[data-theme="dark"]` attribute selector**，唔准用 `@media (prefers-color-scheme: dark)`
  —— 用 media query 會忽略用戶喺 header 揀嘅設定。
- 驗證截圖要 `localStorage.setItem("cardz-theme","dark")` 喺 load 之前落（`add_init_script`）。
- 主題過渡只喺撳 toggle 嗰 260ms 開（`html.theme-transitions`，`use-market-settings.ts`）；
  唔准長期掛住，否則任何 hover / 換頁都拖住 240ms。

### 2.2 `data-updown`（green-up / red-up，:223–234）

owner 2026-08-16 決定：**全部 locale 默認綠升紅跌**；用戶自己揀「紅升」先落
`html[data-updown="red-up"]`，直接對調 `--positive` / `--negative`，所有 consumer
（sparkline / delta / badge / chart）一齊翻。

**`--frame-up` / `--frame-down` 故意唔喺呢度對調**（:226 註解）：heatmap tile 嘅色由
`heatmap.tsx` `tileColors` 按同一個 attribute 自己對調。改 CSS 側去「順手補埋」= 對調兩次 = 冇對調。

### 2.3 5 個語言 × 360px

`cardLanguages = ["en","ja","ko","zhCN","zhTW"]`（`lib/i18n.ts:8`）。
`body { min-width: 320px }`（:246），實際最窄要撐到 **360px**。
德文式長字冇問題，但**日文／中文卡名唔會斷行**，所以：

- 任何會載卡名 / set 名嘅 cell 要 `min-width: 0` + `overflow: hidden` + `text-overflow: ellipsis`；
- 唔准用固定 px 闊度扮「啱晒」——一個 locale 啱 = 四個 locale 爆；
- 數字一律 `font-variant-numeric: tabular-nums`（body :253 已全局開 `tnum`），排位先唔會跳。

---

## 3. Motion 清單同 reduced-motion 契約

### 3.1 硬契約（兩層）

1. **framer-motion**：`layout.tsx` 包住全站 `<MotionConfig reducedMotion="user">`
   （`components/motion-config.tsx`）。transform 類動畫即刻落地，只留 opacity。
2. **CSS**：`:2293` 有一個全局 block —— reduced-motion 之下所有
   `animation-duration` / `transition-duration` 壓到 `0.01ms`、`animation-iteration-count: 1`、
   `scroll-behavior: auto`。

**但 (2) 唔代表可以躺平。** 全局 block 只係「跑快到睇唔到」，佢**唔會**幫你拆
`will-change`、唔會幫你停 `iteration-count: infinite` 嘅視覺噪音、亦唔會令一個
`animation: … both` 嘅 element 停喺啱嘅最終狀態。

> **規矩：每個新 `@keyframes` / 新 transition，都要有自己一個
> `@media (prefers-reduced-motion: reduce)` sibling，明寫佢喺 reduced 之下係咩樣。**
> 冇 sibling 嘅 keyframe = `fe-design-review` 紅一粒。

### 3.2 現有 keyframes（10 個 + WS2 `live-beam`）

| Keyframe | 行 | 用途 | reduced-motion sibling |
|---|---|---|---|
| `menu-in` / `menu-out` | :434 / :440 | select 選單開關 | :2778 `.select-menu, .select-menu-exit { animation: none }` |
| `loading-sheen` | :620 | skeleton 掃光 | :1789 `.skeleton-block { animation: none }` |
| `copy-spin` | :847 | 複製中 spinner | :2778 `.copy-icon-busy svg { animation: none }` |
| `tile-in` | :950 | heatmap tile 入場淡入 | :2778 `.heatmap-tile { animation: none }` |
| `tile-pop` | :954 | 拖 slider 後新 tile 彈出 | 同上 + :2784 拆 `will-change` |
| `delta-rise` / `delta-fall` | :1477 / :1481 | 升跌箭嘴微動 | :1485 `.price-delta svg { animation: none }` |
| `copy-pop` | :1819 | 複製成功 | 全局 block |
| `fade-up` | :2761 | box / watchlist hero 浮現（桌面 only :2773–2776） | :2778 `.fade-up, .fade-up-desktop { animation: none }` |
| `live-beam` | `styles/glow-badges.css` | live 徽章 border beam，**`2.6s × 3` 之後停返 0deg**（冇 `fill-mode`），而且只喺 `.detail-page` 出 | 同檔 `@media (prefers-reduced-motion: reduce) { .detail-page .live-badge { animation: none } }` |

其他 transition 類 reduced-motion block：`:2186`（grader tabs）、`:2289`（cap-ticker）、
`:2669`（explore search / sort）、`:2778`（hover-lift / skip-link / detail-metrics / back-link）。

### 3.3 手機唔等於桌面

`fade-up` 只喺 `@media (min-width: 981px)` 播（:2773–2776）：手機第一屏要即刻見到內容，
唔可以等浮現。新動效預設跟呢個形狀 —— **手機靜態、桌面先加戲**，
tilt / spotlight 類仲要再加 `@media (hover: hover) and (pointer: fine)`。

---

## 4. 效能預算

呢啲數係硬預算，唔係目標：

1. **第一屏（`/`）= heatmap + 一行總市值。就係咁多。**
   唔加 marketing hero（owner 2026-08-16）、唔加新字體、唔加 WebGL/shader、
   唔喺 `/` 首屏加任何 IntersectionObserver reveal。`/` 嘅 LCP element 唔准變。
2. **Sparkline 永不動。** `rankings` 一頁有 100+ 個實例（`components/sparkline.tsx`），
   加動畫 = 100 條同時跑。呢條寫死喺檔入面，唔准「試下」。
3. **一個共用 IntersectionObserver。** 要 scroll reveal 就 module 級開**一個** observer 派畀所有
   subscriber，唔准每個 component 自己 `new IntersectionObserver`。每頁 reveal target ≤ 8 個、section 級。
4. **`will-change` 用完一定要拆。** 樣板 = `data-settled`（:949）：
   `.heatmap-tile[data-late]` 播 pop 期間先 `will-change: transform, opacity`（:947），
   `animationend` 由 `heatmap-tile.tsx:57` 落 `data-settled`，CSS 即刻 `will-change: auto`。
   reduced-motion 之下 `animationend` **唔會 fire**，所以 :2784 要自己拆一次 —— 呢個係最易漏嗰步。
5. **只 animate `transform` / `opacity`。** 唔准 animate `width` / `height` / `top` / `left` /
   `box-shadow` / `filter`（hover 一次性 transition 除外）。
6. **CLS ≤ 0.01**（`/`、`/card/[id]`、`/rankings/[slug]`，390 + 1280）。
   任何會改高度嘅效果要預留位，唔准 load 完先撐開。
7. **scroll 一頁 0 個 >200ms longtask**；pointer 連續掃 5 秒 0 個 >50ms longtask。
8. **零視覺 npm dep。** 要一粒 badge 發光就寫 15 行 CSS，唔好裝一個 library。

---

## 5. 卡圖契約

- **來源格式：429 × 600 透明畫布 WebP**（:1491–1492 註解，2026-07-25 標準）。
  DB 入面每張圖已經去底、已經係呢個比例。
- **原生 RGBA 圓角。** 前端**唔准**再 CSS 削角（`border-radius` 落卡圖）或者放大裁切；
  `--card-img-radius`（:16）已經標咗廢。`.detail-art img` 亦都明寫 `border: 0; filter: none`（:1490）。
- **overlay 只准用 mask。** 透明底代表卡形之外係空氣：任何 sheen / holo / spotlight 要
  `mask-image: url(<同一張 src>)`，唔係一個蓋住成個 box 嘅漸變 —— 否則透明邊會著色，穿崩。
- **`CardImage` 嘅 props 要保持 primitive。** `components/card-image.tsx` 係 100 格 heatmap 嘅
  hot path（:27 註解：每 render 建新 closure 會令 props 逐次唔同、`memo` 白做）。
  要加效果 → 開一個 **wrapper** component 包住佢，唔好改佢個 signature。
- 爛圖要放行：`handleCardImageError` 一定要標 `tile-img-ready`，否則格入面永遠透明。

### 5.1 `.card-art` 幾何契約（WS2 落實）

`.detail-art`（flex 置中）→ `.card-art`（100% 填滿，transform 落佢度）→ `<img>`（原封不動）。

- `.card-art` **一定要 100% 填滿 `.detail-art`**：`.detail-art img` 嗰句 `max-width/max-height: 100%`
  換咗個 parent，只有喺 wrapper 同 `.detail-art` 一樣大嗰陣先解出同一個數。實測 1280 同 390
  兩檔 `<img>` rect 逐個 sub-pixel 一模一樣（386.609 × 519.984 / 209.313 × 279.984）。
- Sheen / spotlight 兩個 pseudo 行 **`inset: var(--card-art-pad)` + `mask-size: contain`**。
  點解夾得返：`<img>` 係 `object-fit: contain` + `26px`（≤680px `16px`）padding，
  真正畫到卡嘅矩形 = wrapper 縮入一個 padding 之後再 contain-fit 卡嘅比例 —— 同 pseudo
  嗰個 box 完全同一條式，唔使 JS 度尺。實測偏差 1280 **0.01px**、390 **0.5px**。
  **`.detail-art img` 個 padding 改咗就要改 `--card-art-pad`**，否則 sheen 會偏。
- mask 來源由 `CardArt` inline 寫 `--card-art-src`（同一張 `_600`，SSR 就有，唔使等 hydrate）。
  冇來源就 fallback 落全透明 1×1 GIF：寧願冇效果，都好過一塊漸變蓋住透明底穿崩。
- **`transform` 唔准落 `.detail-art`**（佢係定位／背景層，桌面會 sticky）；`perspective` 先至落佢。

---

## 6. CSS 放邊度

| 位置 | 放咩 |
|---|---|
| `src/app/globals.css` | token（`:root` / `[data-theme="dark"]`）、全局 element 樣式、跨頁共用組件 |
| `src/app/styles/<feature>.css` | 單一功能面嘅樣式，由**擁有佢嗰個 component** import |
| component 檔 | 冇 inline style（除咗真係要由 JS 計嘅 CSS var） |

現有 pattern：

| 檔 | 由邊個 import |
|---|---|
| `styles/card-links.css` | `card-detail.tsx:23`、`breadcrumbs.tsx:3`、`related-cards.tsx:7`、`box-detail.tsx:20` |
| `styles/content-pages.css` | `content-page.tsx:24` |
| `styles/hubs.css` | `seo-table.tsx:6` |
| `styles/heatmap-tune.css` | `heatmap.tsx:26` |
| `styles/market-foot.css` | `market-page.tsx:15` |
| `styles/card-art.css` | `card-detail.tsx`（WS2；holo / tilt / spotlight + 相關卡 spotlight） |
| `styles/glow-badges.css` | `provenance.tsx`、`card-detail.tsx`、`market-page.tsx`（WS2；`#1` / top mover / live 徽章） |

**新功能 → 新 `styles/<feature>.css` + 喺擁有者度 import。**
`globals.css` 只收 token 加減，同埋 plan 明文點名嘅改動。呢個檔已經 2,838 行，
再塞就冇人搵得返嘢。

---

## 7. 品牌

- Wordmark / logo 檔喺 `public/brand/`。`header.tsx:67` 有一句 inline `display`
  係擋住舊嘅 `[data-theme="dark"] .brand-logo { display: none }` 規則 —— 改 header 之前睇清楚。
- **唯一強調色係 `--accent`**（暖橙）。升跌綠紅係**資料語意**，唔係品牌色，
  唔准攞去做裝飾（例如「呢個掣用綠色靚啲」）。
- 評級機構色（`--grader-*`）係**身份色**，同樣唔准攞去做裝飾。
- OG 圖冇 theme（scraper 唔會帶 `data-theme`），固定行 light 面（`api/og/card/[id]/route.tsx:13`）。

---

## 8. FE05 升級日誌

版本：**038 / FE05**（`lib/product-generation.ts`）。backend generation 冇郁（037 / 037db）——
FE05 純粹係 presentation 層。認 live：`/api/health` → `presentation: "FE05"`。

| WS | 內容 | 狀態 |
|---|---|---|
| **WS1** | token（`--font-sans/mono`、`--step--1…4`、`--tracking-*`、`--space-1…8`）、mono stack 四處收一、字階落三個流體點（`.content-hero h1` / `.content-body h2` / `.hub-hero h1`）、呢份 DESIGN.md、`PRESENTATION` 升 FE05 + fallback 037/FE04 | ✅ 已落 |
| **WS2** | 卡圖 holo / tilt / spotlight（`card-art.tsx` wrapper + `styles/card-art.css`）、`#1` chip、7d top mover chip、live 徽章 border beam（`styles/glow-badges.css`）、相關卡 hover spotlight | ✅ 已落 |
| WS3 | Motion 系統：共用 IntersectionObserver reveal、history chart 線條 draw-in、count-up 擴到卡頁 | TODO |
| WS4 | Loading / empty / status：共用 skeleton、empty-state、`aria-busy`；**唔准加 `app/card/loading.tsx`** | TODO |
| WS5 | OG 圖 v2（卡圖入圖，satori 讀唔到 WebP → 要解碼），fail-open 退返純文字版 | TODO |
| WS6 | HyperFrames 每日市場 recap 片（`apps/web` 以外，獨立 folder） | TODO（可選） |

**WS1 量到嘅實數**（review 之後重量，`temp/fe05/ws1-fix/midwidth.py`，runtime 注入 FE04
原宣告做 A/B，唔改檔）：`/`、`/about`、`/methodology`、`/market-report`、`/rankings/[slug]`、
`/card/[id]` **六頁 × 13 個闊度**（390 / 600 / 700 / 720 / 721 / 768 / 800 / 900 / 940 / 981 /
1024 / 1120 / 1280）——

- rect **max delta 0.00px**，78 個組合冇一個非零；`document.scrollHeight` delta 全部 **0**。
- 九個字階相關 selector × 78 個組合嘅 computed `font-size`，FE04 vs FE05 **0 個唔等**。
- **CLS**（`temp/fe05/ws1-fix/cls.json`、`cls_scroll.json`，每組 3 次）：`/`、`/rankings/[slug]`、
  `/market-report` 三頁 390 / 768 / 1280 全部 **0.0000**。`/card/[id]` 18 次入面 17 次 0.0000，
  一次 768px 度 **0.0757** —— 而嗰次係喺 **FE04 baseline 嗰邊**出，source 係一個冇 class 嘅
  `IMG`（卡圖 lazy load）。即係 **pre-existing、間歇性、同 WS1 無關**，唔准當「WS1 驗收過咗」。

**WS1 留低嘅欠單：**

1. 「已知未收編」六個 selector（見 §1.3）—— 三個階梯落點 + `.hub-answer` / `.hub-note` /
   `.provenance-body`。`.content-answer` vs `.hub-answer` 同一角色兩套字級，要 owner 一次過拍板。
2. `--step--1` / `--step-0` / `--step-1` 有定義零 call site（見 §1.3 尾）。
3. **畀 WS2 嘅前置欠單**：`/card/[id]` 個卡圖 `IMG` 冇預留位，CLS 間歇 0.0757。WS2 正正要喺
   同一個 `IMG` 加 tilt / holo —— **落之前先幫卡圖鎖 `aspect-ratio`**，否則 CLS 只會更差。
4. 驗證方法：`/` 同 `/card/[id]` **唔准用截圖 hash 做 regression gate**（熱力圖 tile 同卡圖
   lazy load 令同一份 code 連跑兩次都 DIFFERS），要用 rect / CLS 數字。dev server（`localhost:3901`，
   未 minify）量到嘅 longtask 唔算數，longtask 預算留返 production build 先判。
5. `temp/` 喺呢個 repo **未 gitignore**：WS 產出嘅 PNG / 腳本全部係 untracked，逐檔 `add` 時避開。
   `apps/web/next-env.d.ts` 係 Next dev 自己重生，唔好 stage。

（原本第 ② 條「`/api/health` 出 `036/FE04` 假配搭」已經修好：`FALLBACK_GENERATION` 由 `"036"`
改做 `"037"`，同 `docs/FE05_ROLLBACK.md` 個 `fe04-live` 錨點對齊。實測 `/api/health` →
`fallback: {product: "037", presentation: "FE04"}`。）

**WS2 量到嘅實數**（`temp/fe05/ws2/`，dev server :3901，卡 `cmc_fc229f7ae1b256b2119fa79b` = `#1`）：

- **`<img>` rect 零位移**：包 `<CardArt>` 前後，1280 `386.609 × 519.984`、390 `209.313 × 279.984`
  —— 逐個 sub-pixel 相同（`measure.py` baseline vs `verify.py`）。
- **CLS = 0.0000**，5 秒連續掃 pointer（301 次 `mouse.move`）期間 `layout-shift` entry **0 個**
  （`closeups.json` → `sweep.shifts: []`；observer 行 `buffered: true`，連載入期都收埋）。
- **longtask**：成個 session 得 2 個（`t=199ms/69ms`、`t=276ms/85ms`，全部係 dev hydration），
  掃 pointer 嗰 5 秒窗口（`3154–8168ms`）**0 個**。dev build 未 minify，呢個數只證「唔係我加嘅」。
- **beam 會停**：`live-badge.getAnimations()` 1 秒時 `running` / `iterations: 3`，10 秒之後
  **0 個 animation**、`--beam-angle` 返 `0deg`。`/` 同 `/box` 由頭到尾 **0 個**（beam 鎖死喺 `.detail-page`）。
- **390 冇溢出**：`document.documentElement.scrollWidth = 390`（light + dark），
  `.card-art` 個 `data-art-state` = `null` → **一個 listener 都冇 attach**。
- **reduced-motion**：`transform: none`、`will-change: auto`、CSS var 全部冇寫過、beam 0 個。
- `#1` 有 `--glow-accent`（`rgba(184,84,22,.34) 0 0 0 1px, …`），`#2` `boxShadow: none`。
- 三頁（`/card/[id]`、`/`、`/box`）console error / pageerror **0**。

**WS2 嘅決定同欠單：**

1. **WS1 第 ③ 條「落之前先鎖 `aspect-ratio`」冇做，係刻意嘅。** 卡圖 natural size **逐張唔同**
   （實測呢張係 `400 × 560`，唔係 doc 講嘅 `429 × 600`），payload 亦冇帶尺寸。喺 `.detail-art img`
   落一個寫死嘅 `aspect-ratio` 會令卡由 `386.6px` 闊變 `371.8px`（border-box 之下 ratio 套喺
   border box），即係為咗一個**間歇**嘅 CLS 去換一個**每次都有**嘅視覺改動。正解係 bake 側把
   `width/height` 寫入 payload → `<img>` 直接出 attribute。**欠單原封不動交返出去**，
   WS2 實測 CLS 0 唔代表嗰個間歇 shift 消失咗。
2. **live 徽章 beam 只喺 `/card/[id]` 行**（`.detail-page .live-badge`）。`provenance.tsx` 喺 `/`、
   `/box`、`/pokemon` 都出，`/` 嘅預算係「第一屏得熱力圖 + 一行總市值」，唔可以喺 load 嗰陣
   多一條 7.8 秒 paint 動畫喺頁尾轉緊。其餘頁面得靜態點 + 靜態邊。
3. **live dot 唔跳**（冇 pulse keyframe）。plan 原本抄 Kinetics 個 dot pulse，但 `infinite` pulse
   喺三頁常駐 = §4.5 明文唔准嘅視覺噪音；要跳就要同 beam 一樣有迭代上限，owner 拍板先加。
4. **ambient shimmer 冇落**（plan 話 ≤1 cycle/8s 可接受）。落咗就永遠有一條 paint 動畫喺卡頁跑，
   「idle CPU 回 0」呢條驗收會變成講唔清。sheen 純粹跟 pointer。
5. **top mover 落咗 chip，冇落 ranking row / heatmap tile。** `moverCard`（`market-page.tsx:74–80`）
   本來只係喺頁尾摺埋嘅 `<details>` 嗰句市況文字入面出過個名，冇自己嘅 render site；
   chip 擺返同一段（順手多一條內鏈落卡頁）。**ranking row 唔掂**（100 行 × box-shadow）、
   **heatmap tile 唔掂**（另一 session 揸住，而且要 `data-rank` hook）。
6. `--step--1` 而家有一個 call site（`.mover-chip`），§1.3 尾嗰句「零 call site」要跟住更新。
7. ESLint 喺呢棵 tree **行唔到**（`apps/web/package.json` 冇 lint script，`node_modules` 冇
   `eslint`，`npx` 會去拉一個唔同 major 嘅版本再 `ERR_MODULE_NOT_FOUND`）。WS2 只跑咗
   `npx tsc --noEmit -p apps/web`（**0 error**）。

### 明確非目標

唔遷 Tailwind / shadcn；唔上 WebGL / shader；`/` 唔加 marketing hero、唔加新字體；
唔改 `heatmap.tsx` 內部同 `exportHeatmap`；唔加 `app/card/loading.tsx`；
唔為視覺效果加 npm dep；無 scroll-jacking / parallax；sparkline 同 ranking row 唔動；
唔為咗動畫放鬆任何 gate / 契約 / crawler 可見文字。
