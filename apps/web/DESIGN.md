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

### 3.2 現有 keyframes（10 個 + WS2 `live-beam` + WS3 三個 chart keyframe）

| Keyframe | 行 | 用途 | reduced-motion sibling |
|---|---|---|---|
| `menu-in` / `menu-out` | :434 / :440 | select 選單開關 | :2778 `.select-menu, .select-menu-exit { animation: none }` |
| `loading-sheen` | :620 | skeleton 掃光 | :1789 `.skeleton-block { animation: none }` |
| `copy-spin` | :847 | 複製中 spinner | :2778 `.copy-icon-busy svg { animation: none }` |
| `tile-in` | :950 | heatmap tile 入場淡入 | :2778 `.heatmap-tile { animation: none }` |
| `tile-pop` | :954 | 拖 slider 後新 tile 彈出 | 同上 + :2784 拆 `will-change` |
| `delta-rise` / `delta-fall` | :1477 / :1481 | 升跌箭嘴微動 | :1485 `.price-delta svg { animation: none }` |
| `copy-pop` | :1819 | 複製成功 | 全局 block |
| `fade-up` | :2761 | box / watchlist hero 浮現（桌面 only :2773–2776）**＋ WS3 scroll reveal 重用同一條** | :2778 `.fade-up, .fade-up-desktop { animation: none }`；reveal 側喺 `styles/reveal.css` 自己再有一個 |
| `live-beam` | `styles/glow-badges.css` | live 徽章 border beam，**`2.6s × 3` 之後停返 0deg**（冇 `fill-mode`），而且只喺 `.detail-page` 出 | 同檔 `@media (prefers-reduced-motion: reduce) { .detail-page .live-badge { animation: none } }` |
| `chart-draw` | `styles/history-chart.css` | `.price-line` 由頭畫到尾（`pathLength="1"` + dashoffset 1→0，900ms） | 同檔 `@media (prefers-reduced-motion: reduce)`：`animation: none` + `stroke-dasharray: none` |
| `chart-bar-rise` | 同上 | `.sales-bar` 由 baseline `scaleY(0→1)`，stagger 30ms、序號封頂 20 | 同上 |
| `chart-dot-in` | 同上 | `.price-point` 喺線畫完（760ms）先淡入 | 同上 |

其他 transition 類 reduced-motion block：`:2186`（grader tabs）、`:2289`（cap-ticker）、
`:2669`（explore search / sort）、`:2778`（hover-lift / skip-link / detail-metrics / back-link /
**WS3 `.primary-action:active` + `.explore-dir-icon`**）。

WS3 三個 chart keyframe **全部收喺 `.history-panel[data-draw="in"]` 底下**：SSR 出嘅 HTML 冇
呢個 attribute，即係 view-source 見到嘅係畫好晒嘅圖（實測 `stroke-dashoffset` 出現 0 次）。

`data-draw` 有三個值：冇（SSR / 未入場）→ `"in"`（播緊）→ **`"done"`（播完，1200ms 後由
`history-chart.tsx` 落）**。三條 rule 只掛 `"in"`，所以入場係一次性 —— 之後撳 period
（bar / dot 嘅 React key 帶住 `point.at`，換窗即係全新 DOM 節點）唔會再播一次。
同 §4.4 `data-settled` 一樣係「動畫完咗要清場」嘅形狀。

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
   **WS3 落實：`components/reveal.tsx` 係唯一入口**（`<Reveal>` 同 `revealOnce()`），
   `rootMargin: "-10% 0px -10% 0px"`、once（一 intersect 就 `unobserve`）。
   **IO 一個人守唔住**：IO 淨係喺 threshold 跨界先派 entry，一下 fling 由「元素喺視窗
   下面（ratio 0）」跳到「喺上面（ratio 0）」ratio 冇變過 → 一個 callback 都冇 →
   個 section 永遠 opacity 0（審核 4/4 重現）。所以 `reveal.tsx` 另外有**一個共用**、
   rAF 節流嘅 `scroll` / `resize` sweep 兜底：`top < innerHeight * 0.9` 就播，
   `pendingTargets` 一空即刻 `removeEventListener`。呢個唔算多咗一個 observer，
   但「加 listener 要識自己拆」同 §4.4 `will-change` 同一條規矩。
   （試過只喺 IO callback 加「已經捲過咗頭」條件 —— **冇用**，實測一樣 opacity 0。）
   量法：`add_init_script` 包住 `window.IntersectionObserver` 數 construction ——
   `/card/[id]` 同 `/market-report` 各自量到 **2 個 construction，其中 `-10%` 嗰個 = 1**；
   另一個 `{rootMargin:"200px"}` 係 `next/link` 自己嘅 prefetch observer，唔屬於呢條預算。
   `<Reveal>` **唔准包 ranking row / heatmap tile / sparkline**，亦唔准喺 `/` 首屏出現。
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
| `styles/skeleton.css` | `skeletons.tsx:1`（WS4） |
| `styles/empty-state.css` | `empty-state.tsx:3`（WS4） |
| `styles/card-art.css` | `card-detail.tsx`（WS2；holo / tilt / spotlight + 相關卡 spotlight） |
| `styles/glow-badges.css` | `provenance.tsx`、`card-detail.tsx`、`market-page.tsx`（WS2；`#1` / top mover / live 徽章） |
| `styles/reveal.css` | `reveal.tsx:4`（WS3；`.reveal-pending` / `.reveal-in`，keyframe 借 globals 個 `fade-up`） |
| `styles/history-chart.css` | `history-chart.tsx`（WS3；線 draw-in、bar scaleY、點淡入） |

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
| **WS3** | Motion 系統：共用 IntersectionObserver reveal（`reveal.tsx` + `styles/reveal.css`）、history chart 線條 draw-in / bar scaleY / 點淡入（`styles/history-chart.css`）、count-up 擴到 PSA 10 價 + 鑑定數量 + hub stat、桌面 dialog spring overshoot、`.primary-action` press 陰影、排序方向掣 180° 翻轉 | ✅ 已落 |
| **WS4** | Loading / empty / status：共用 `skeletons.tsx`（`MarketHeroSkeleton` / `RankingRowsSkeleton`）+ `styles/skeleton.css`、`empty-state.tsx` + `styles/empty-state.css`（4 個 call site）、`aria-busy` 落 `#market-ranking` / `#box-ranking`。**`/card/[id]` 冇骨架**：`app/card/loading.tsx` 同 route 內 `<Suspense>` 兩條路都試過、兩條都要唔起（見下面 WS4 實數第 2 點） | ✅ 已落（1 條 plan gate 未過，見欠單 ⑤） |
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
7. ~~ESLint 喺呢棵 tree **行唔到**（`apps/web/package.json` 冇 lint script，`node_modules` 冇
   `eslint`，`npx` 會去拉一個唔同 major 嘅版本再 `ERR_MODULE_NOT_FOUND`）。~~ WS2 只跑咗
   `npx tsc --noEmit -p apps/web`（**0 error**）。
   **已修 729af269 + fix-tooling（2026-08-17）**：裝返 `eslint@9` + `eslint-config-next@16`，
   入口 `npm run lint`；基線 **10 error / 9 warning**（全部 pre-existing src，未修）。
   call site 係 `scripts/test-eslint-ratchet.mjs` —— `scripts/run_all_tests.py` 會自動 glob
   `scripts/test-*.mjs`，所以 `npm test` 一跑就跑到，多過基線／少過基線都紅。
   代價（明寫，唔係漏咗）：lockfile 由 78 個 `packages{}` 升到 **423** 個，全部 `dev:true`；
   `apps/web/Dockerfile:17` 係裸 `npm ci`（唔可以加 `--omit=dev`，`next build` 要 typescript
   同 `@types/*`），所以 Docker deps layer 大咗；runner stage 只 copy `.next/standalone`，
   **runtime image 唔受影響**。個 `@babel/*` subtree 係 `eslint-plugin-react-hooks@6` 拉入嚟，
   唔係 `eslint-config-next` 自己，所以「改用兩個 plugin」都省唔到（除非放棄 rules-of-hooks）。

**WS4 量到嘅實數**（`temp/fe05/ws4/`，dev server :3901）：

- **404 契約前後一樣**：`/card/does-not-exist` **404**、`/watchlist` **308**、
  `/card/cmc_fc229f7ae1b256b2119fa79b` **200**、`/`、`/box`、`/pokemon` **200** ——
  改 `page.tsx` 之前同之後逐個對，六個 code 一模一樣。
  關鍵係 `await requireCard(id)` 喺 `<Suspense>` **外面**（`card/[id]/page.tsx` 有註）：
  boundary 一 suspend 就沖 shell、status 鎖 200，次序調轉即刻變返 soft-404。
- **`/card/[id]` 冇骨架，兩條路都封死（review 2026-08-17 收返）**——原因唔同，要分開記：
  1. `app/card/loading.tsx`：segment loading 鎖 HTTP 200，`notFound()` 變 soft-404（上面已述）。
  2. route 入面自己包 `<Suspense>`：404 契約守得住（`requireCard` await 喺 boundary 外面），
     但 **crawler shell 炸咗**。`CardDetail` 個 client subtree 喺 SSR 期間會 suspend
     （`useMarketSettings` → `useSearchParams()`，use-market-settings.ts:121），一 suspend
     就係成個 boundary 內容跌入 `<div hidden id="S:1">`，要行 `$RC` script 先 reveal。
     **同一部機、同一張卡、同一 dev server 度前後對量**（`temp/fe05/ws4-fix/scan.json`）：

     | | `<h1>` byte | 第一個 `<div hidden id="S:*">` byte | h1 喺 shell？ |
     |---|---|---|---|
     | 有 `<Suspense>` | 16963 | 12486 | ❌ |
     | 拆走（現況） | 13112（GPTBot UA 量：10057） | 54164（51140） | ✅ |
     | 控制組 `/box/[id]`（一直冇 boundary） | 9059 | 15301 | ✅ |

     卡頁係 GEO 主力頁（`docs/CLOUDFLARE_AI_CRAWLER_UNBLOCK_20260816.md`），唔行 JS 嘅
     AI crawler 淨係讀 shell。換返嚟嗰個骨架又證實從來冇出現過（CDP 限速 40KB/s，
     每 400ms 抽 DOM，`.skeleton-detail-h1` 一次都冇出現）。零收益 + 實質代價 → 拆，
     連 `CardDetailSkeleton` 同 `.skeleton-detail-*` CSS 一齊刪（死 code 唔留）。
     順手修埋：`related` 用返 `requireCard` 已經 load 咗嗰份 snapshot，唔再 `await
     loadMarketSnapshot()` 第二次（live-db mode 本來一個 request 打兩次 DB）。
- **plan 嗰條「骨架同真實版面 390px 差 ≤4px」：`/card` 呢邊做唔到，缺口 234.95px。**
  唔係全條線都爆 —— h1 及以上（`.detail-actions` / `.detail-art` / `.detail-content` /
  `.detail-header` / h1）top 同闊度 delta **0.00px**（390 同 1280 都係，`rects-real.json`
  vs `rects-skeleton.json`），爆嘅係 `.detail-metrics` 同佢下面。根因係內容長度唔係 CSS：
  metrics 坐喺三個長度隨卡變嘅 block（h1 行數 158.4 / 197.97 / 237.56px 三檔、`.card-fact`、
  `.story-panel` @390 實測 226.08–486.23px）下面。8 張卡 @390 實測 metrics top vs 當時
  骨架嘅 1491.39px：`+125.83 / +64.80 / −67.64 / +20.27 / −22.61 / −234.95 / +106.74 / −85.30`
  → |max| **234.95px**、|中位數| **76.47px**（`h1-sample.json`）。
  **呢條 gate 未過（見欠單 ⑤）**；冇 owner 明文拍板之前，唔准將驗收面改寫成「淨計 h1 以上」。
  現況係整個卡頁骨架已經拆走，所以 SSR 路徑上冇 swap、冇 shift，但條 gate 一樣係未達成。
- **reduced-motion sibling 真係 fire**（`temp/fe05/ws4-fix/verify.json`，量緊 `/tune` 度生嘅
  `.skeleton-block`，即 `(market)/loading.tsx` 出嗰個）：`no-preference` →
  `animationName: "loading-sheen"` / `1.4s`；`reduce` → **`"none"`**。
  （早一版 probe 喺 `/` 度注個 `.skeleton-block` div 去量 —— `/` 根本冇 load `skeleton.css`，
  兩邊都出 `none`，**假 pass**。要量就要量真骨架。）
- **`aria-busy` 會翻**（`shots.json`）：`#market-ranking`
  `false → true（debounce 中）→ true → false（settle）`；`#box-ranking` `false → true → false`。
  debounce state 本來只活喺 `explore-bar.tsx`（local `text` vs URL `query`），加咗一個
  optional `onPendingChange` callback 抽返出嚟；`aria-busy` 掛喺原本嗰個 `<section>`（已經係
  `aria-labelledby` 嘅結果區），**冇加新 wrapper div** = 零 layout 改動。
  第一版仲有個 SSR bug：`catalog === null` 喺 server render 一定成立，深鏈 `/?q=…` 出嘅
  HTML 寫死 `aria-busy="true"`，冇 JS 就永遠 busy。加咗粒 mount 旗（`hydrated`）之後
  server HTML 一律 `false`（`curl /?q=zzzqqqnotacard | grep market-ranking` 實測）。
- **空狀態截圖** 390 / 1280 × light / dark 四張，`themeAttr` 對、icon 數 **1**、文字齊
  （`empty-390|1280-{light,dark}.png` + `empty-close-*.png` 近拍）。
- 五頁（`/`、`/box`、`/card/[id]`、`/?q=<冇結果>`、`/pokemon`）console error / warning / pageerror **0**。
- `npx tsc --noEmit -p apps/web` **0 error**（當時 ESLint 同 WS2 一樣行唔到；已修 729af269 + fix-tooling，見 WS2 欠單 ⑦）。

**WS4 嘅決定同欠單：**

1. **`.skeleton-*` 由 `globals.css` 搬去 `styles/skeleton.css`**，原位留咗三行指路註。
   `@keyframes loading-sheen`（`globals.css:620`）**冇搬** —— `.page-loading` 都用緊佢，
   搬咗就係 `/` 冇咗個 keyframe。
2. **冇加任何 i18n key**：四個 empty-state call site 全部重用現有 key，所以 5 個 locale 自動齊。
3. **骨架量度要落臨時 delay 先睇得到**（Suspense fallback 一 resolve 就冇）。用完即刪，
   `grep -rn "WS4-MEASURE-ONLY" apps/web/src` → **0**。卡頁骨架而家已經整個拆走，
   `grep -rn "CardDetailSkeleton\|skeleton-detail" apps/web/src` 淨返兩行指路註。
4. `box-rankings.tsx:133`（搜尋冇結果）同 `box-detail.tsx:31`（`.empty-detail`）兩個空狀態
   **未轉** `EmptyState` —— plan 只點名咗四個 call site，唔想順手擴大範圍。轉唔轉由 owner 講。
   即係話 `empty-state.tsx` 個 rationale 講「收埋做一個」而家仲有 2 處各寫各。
5. **卡頁骨架要重開，先過呢兩關**：(a) `.detail-metrics` 個 ±235px 要真正收窄 —— 唯一正路
   係 bake 側出「story / h1 長度分級」寫入 payload，骨架按級揀高度，喺 CSS 度再猜冇用；
   (b) 要搞掂「一 suspend 就冇咗 crawler shell」—— 要嘛等 PPR（shell 靜態、只有動態
   洞先 stream），要嘛將 `useSearchParams()` 由 `CardDetail` 主體推去葉節點再喺嗰度包
   細 boundary。**兩關未過之前唔准再加 `<Suspense>`／`loading.tsx`。**
6. **`EmptyState` 統一咗兩個位嘅外觀**（唔係 bug，係要 owner 知嘅視覺改動）：
   `.history-empty`（原本淨係 `margin-top`，冇框）同 `.empty-detail`（原本 `padding: 60px 0`，
   冇框）而家都食 `globals.css` `.empty-state` 個虛線盒；`.empty-detail` 另外居中咗兼
   `max-width: 460px`，DOM 由 `<section>` 變 `<div>`。要還原就喺 `styles/empty-state.css`
   加 `border: 0`。
7. `/card/[id]` 兩條約定（`notFound()` 唔准俾任何 Suspense 蓋住、`<h1>` 要留喺 shell）
   而家淨係靠 `page.tsx` 嗰段註解守——冇 test、冇 hook。最平嘅 ratchet 係喺 `cardz-verify`
   加兩步：打 `/card/does-not-exist` assert **404**，同埋抓 `/card/<id>` raw HTML assert
   `<h1>` 個 byte offset 細過第一個 `<div hidden id="S:`。**未做，欠單。**
8. `.empty-state-title` 由硬寫 13px 換咗 `var(--step--1)`（WS1 字階）：@390 出 **12px**、
   @1280 出 **13px**（`verify.json`）。即係手機嗰邊標題同提示同字級，靠 `--ink` + 600
   分主次。

**WS3 量到嘅實數**（`temp/fe05/ws3/`，dev server :3901，卡 `cmc_fc229f7ae1b256b2119fa79b`）：

- **crawler shell 零隱藏**（`crawler.json`，GPTBot UA、唔行 JS）：`/market-report`、`/card/[id]`、
  `/methodology`、`/rankings/most-valuable-pokemon-cards` 四頁 —— `reveal-pending` **0**、
  inline `opacity: 0` **0**、`data-draw` **0**、`stroke-dashoffset` **0**。
  hub 8 個 `<h2>` 逐句喺 shell（Concentration / 4 條 gainers-losers / populations / sets / More rankings）；
  卡頁 `<h1>` byte **10057** < 第一個 `<div hidden id="S:` **51556**（WS4 條 crawler-shell 約定冇被打爛）。
- **observer 數 = 1**（`verify.json`）：`/card/[id]` 同 `/market-report` 各量到 2 個
  `new IntersectionObserver`，`{"rootMargin":"200px"}`（`next/link` prefetch）＋
  `{"rootMargin":"-10% 0px -10% 0px"}`（WS3）—— **WS3 嗰個係 1 個**，唔係一個 component 一個。
- **成頁 scroll：CLS 0.0000、layout-shift entry 0 個、>200ms longtask 0 個**
  （390 最長 77ms、1280 最長 88ms，全部係 dev hydration；dev 未 minify，呢個數只證「唔係我加嘅」）。
- **chart draw-in 真係播**（`chart_draw`）：入視窗前 `data-draw: null` / `dasharray: none` /
  `dashoffset: 0px`（= SSR 嗰個最終狀態）；入視窗之後 150ms `0.504` → 300ms `0.172` →
  600ms `0.0056` → 1400ms `0`，bar `scaleY` 由 `0.805` 升到 `1`。
  截圖 `chart-draw-300ms.png`（線畫到約 83%、點未出）vs `chart-draw-end.png`（線齊、點齊）。
  播完 4 秒後 183 個 animation **全部 `finished`**、`will-change: auto`。
- **reduced-motion**：`/card/[id]` 同 `/market-report` 嘅 WS3 observer **0 個**、`.reveal-pending` **0 個**、
  `.history-panel` 冇 `data-draw`，probe 到嘅 section 逐個 `opacity: 1` 兼 `getAnimations()` 空
  （`reduced-card-1280.png` / `reduced-hub-1280.png`）。
- **390 完全靜態**（`shot_390_*`）：`reveal-pending` / `reveal-in` **0 個**、`metrics_opacity` `1`、
  `documentElement.scrollWidth` **375**（冇橫向溢出），light + dark 都係。
  1280 mid-scroll 影到動畫進行中（`pending: 2` / `revealed: 1` / `metrics_opacity` `0.9974`）——
  `card-{390,1280}-{light,dark}-mid.png` 四張。
- **桌面 dialog overshoot**（`verify3.json`）：`0.98712 → 峰值 1.00193 → 1`；
  reduced-motion 之下 `0.98 → 1`、峰值 1（同 FE04 一模一樣）。
- **排序方向掣**（`verify2.json`）：`data-dir="desc"` → icon `matrix(-1,0,0,-1,0,0)`（180°）、
  `asc` → `none`，`transition: transform 0.17s`，`aria-label` 照跟 `High to low` / `Low to high` 翻。
- **`.primary-action` press**：idle `boxShadow: none` / `transform: none`；撳住
  `scale(0.98)` + `--elev-1`，`transition-duration: 0.12s`（鬆手行返上面 0.22s spring）。
- 五個 context（卡頁 ×4、hub、`/`、404 頁）console error / pageerror **0**。
- `npx tsc --noEmit -p apps/web` **0 error**（當時 ESLint 同 WS2 一樣行唔到；已修 729af269 + fix-tooling，見 WS2 欠單 ⑦）。

**WS3 嘅決定同欠單：**

1. **Reveal 有三個閘，全部喺 mount 之後行：reduced-motion / `<981px` / 元素已經喺視窗入面。**
   三個任何一個中就**完全唔做嘢**（唔加 class、唔 observe）。第三個閘唔止係「首屏唔准動」——
   已經睇到嘅嘢事後先由 opacity 0 播返起，個效果係「跳返轉頭」，比冇動畫更差。
   結果：SSR HTML 一個隱藏 class 都冇，crawler / 無 JS 訪客同以前一模一樣。
2. **手機（<981px）完全靜態**，跟 `.fade-up-desktop` 同一個斷點（§3.3）。
   唔止 CSS 唔播 —— JS 側都唔 arm，所以 390px 一個 observer subscription 都冇。
3. **`box-detail.tsx` 冇加 `<Reveal>`**（plan 有點名）。佢個 `.detail-actions` / `.detail-grid`
   已經係 `fade-up`，成塊嘢入場已經浮現過一次；喺一個播緊 fade-up 嘅 parent 入面再套一層
   reveal = 同一舊嘢動兩次。`/box/[id]` 一樣食到 chart draw-in（`HistoryChart` 共用）。
4. **chart 唔另外包 `<Reveal>`**：draw-in 本身就係佢個入場，再加 fade-up 就係兩層。
   所以 `/card/[id]` 一共 3 個動效 target（story / metrics / related）＋ 1 個 chart draw。
   `/market-report` 係全站最多：**8 個 `.hub-section`**（7 個表 + 1 個 link group）＝ 啱啱到上限。
   再加 hub 表就會爆 §4.3 條「≤ 8」，要嘛分頁要嘛揀住 reveal。
5. **`sparkline.tsx` 明文唔動**，檔內第 9 行寫死原因（rankings 100+ 實例）。
6. **count-up 落 `card-detail` 三個數 + hub 四個 stat，ranking row 一律唔掂。**
   順手修咗一個舊 bug：市值嗰格本來只睇 `value === null` 就決定滾唔滾，但
   `formatMetric*` 喺 `accumulating` / `unavailable` 回嘅係一句**狀態字**（唔係數）——
   value 有數但 status 係 accumulating 嗰啲卡，舊 code 會由 0 滾去一個唔應該顯示嘅數字。
   而家三個 metric 行同一個 `tickerValue()`。
7. **hub stat 嘅 count-up 要過 server→client 邊界**：`HubShell` 係 server component，
   傳唔到 `format` function 落 `<CapTicker>`，所以 `HubStat` 加咗一個**可序列化**嘅
   `tick`（值 + `money|integer` + locale + rates），client 側 `hub-stat-ticker.tsx` 砌返
   同一條 formatter。`stat.value` 依然係權威文字：冇 `tick` 就照出佢，有 `tick` 都要同
   `value` 逐個字一樣（`money()` 一定係 USD compact），否則 hydrate 就會對唔到數。
   而家只有 `/market-report` 有 stats，其餘三條 hub 係 `stats: []`。
8. **桌面 dialog 只換 easing / 時間，`initial` 個 scale 唔郁。** 試過 `0.96` 起手（overshoot
   峰值 1.0039，靚啲），但 `reducedMotion="user"` 之下 framer 一樣會 render 一 frame `initial`：
   即係為咗桌面靚 2%，令 reduced-motion 用戶嗰下跳幅由 2% 變 4%。改返 `0.98` + `--ease-spring`，
   一樣量到 overshoot（1.00193）。手機 drag sheet 嘅 timing 一格都冇郁。
9. **`.primary-action:active` 個 `scale(0.98)` 本來就有**（globals.css :2238，2026-07-25 嗰批
   UI experiments）。WS3 加嘅只係陰影 + 120ms press 時間，唔係由零做一個 press 態 ——
   plan 寫「加 press state」係同現況有出入，記返落嚟。
10. **`.explore-dir` 只喺 681–980px 見得到**（`.explore-sort-chips` ≥981 由表頭排序取代、
    ≤680 轉 lean 模式唔 render chips）。即係話呢個 180° 翻轉喺手機同大螢幕都見唔到，
    量度要用 900px。要唔要喺 lean 模式都有個方向掣，係設計題，唔喺 WS3 範圍。
11. **dev server 量到嘅 longtask 唔算數**（WS1 欠單 ④ 同一條）：`/card/[id]` 同 `/` 嘅
    longtask 預算要 production build 先判。CLS / rect / observer 數呢啲同 build mode 無關嘅
    先當數。

**WS3 審核之後嘅修正**（證據 `temp/fe05/ws3-fix/`）：

12. **「一下大 wheel 令個 section 永遠 opacity 0」已修（major）。** 根因唔係 IO 冇睇到，
    係 **IO 淨係 threshold 跨界先派 entry**：ratio 由 0（喺下面）跳去 0（喺上面），
    冇跨界 = 零 callback。第一版照審核建議喺 callback 加 `bottom <= rootBounds.top`，
    重量之後**一樣 opacity 0**（`fixverify.json` 第一輪）—— 因為根本冇 callback 行到。
    正解係加共用 rAF scroll sweep（§4.3）。修完：`wheel1400` / `wheel2000` 之下
    `.detail-metrics` `opacity "1"`、`.reveal-pending` 0 個、`hidden: []`；
    `wheel600x1` 對照組照舊正常。
13. **chart 換 period 唔會再播一次（major）。** `data-draw` 加咗 `"done"` 終態。
    修前：撳一下 period，333 個價點 opacity 0 足 760ms、15 條 bar 塌返再升，條線仲喺度。
    修後（`draw.json` / `fixverify.json`）：`after_period_switch_120ms` →
    `barAnims: []`、`dotOpacity: "1"`、`lineAnims: []`、`draw: "done"`。
    入場本身照播（`draw.json`：dashoffset `0.9996 → 0.358 → 0.068 → 0.0002 → 0`、
    bar `scaleY 0.0008 → 0.915 → 1`、dot `0 → 0.597 → 1`，之後 state 升 `"done"`）。
14. **reveal 播完拆晒 class，唔留 transform。** `fade-up ... both` 本來會永遠留一個
    `matrix(1,0,0,1,0,0)`：非 `none` 嘅 transform = 成個 section 變咗
    `position: fixed` 後代嘅 containing block + stacking context，將來喺入面擺 popover
    會由零查起。而家 `animationend`（認 target + keyframe 名，因為佢會冒泡）之後
    `remove("reveal-pending", "reveal-in")`。實測 `/market-report` 8 個 `.hub-section`
    播完：`transform` 全部 `none`、`opacity 1`、`will-change auto`、殘留 class 0、
    `getAnimations()` 0。
15. **CLS @390 重量五次全部 `0.0000` / 0 個 shift**（`cls_390_x5`）—— 審核見到嗰次
    `0.0261` 五次重量都撞唔返；WS3 喺 390 由頭到尾冇 arm（observer 0、pending 0），
    所以就算間中有，都唔係 WS3 嚟。longtask 最大 66–99ms（dev build，同欠單 ⑪）。
16. **`box-detail.tsx` 嘅 `<Reveal>` 仍然冇加**（審核 minor，未修）。理由同上面第 3 點，
    要 owner 拍板先郁；`/box/[id]` 照食到 chart draw-in。
17. **reduced-motion sibling 逐條即場證明過會 fire**（AGENTS.md 規矩 9，`rule9.json`）：
    喺 `reduce` context 手動種返 `.reveal-pending` / `.reveal-in` / `data-draw="in"` →
    `opacity "1"`、`animation-name "none"`、`stroke-dasharray "none"`、bar / dot 都 `none`；
    同一段 forcing 喺 `no-preference` → `opacity 0` + `fade-up 0.7s` + `chart-draw` /
    `chart-bar-rise` / `chart-dot-in` 全部起。

**WS-state（URL 狀態 + 卡圖內在尺寸）實數**（證據 `temp/fe05/state/`）：

18. **WS1 欠單 ③ / WS2 決定 ① 已還。** 正解照 WS2 寫嘅做：bake 側本來就有
    `PublicImage.width/height`（`packages/market-data/src/schema.ts:59`），只係 view 層掉咗。
    而家 `snapshot.ts` / `catalog-search.ts` / `box-view.ts` 三個投影共用一個收窄點
    `lib/types.ts intrinsicSize()`（AGENTS.md 規矩 13，唔開第四份），`card-image.tsx` 出
    `<img width height>`。`snapshot.ts` 一改就蓋埋 live DB 路（`live-db-snapshot.ts` 尾巴
    `normaliseSnapshot()`），所以 `live-db-snapshot.ts` 零改動。
19. **CLS 由 0.0585 → 0.0000（`mech.json`）。** 40KB/s throttle 撞唔返欠單 ③ 個 shift
    （同 WS1 講嘅「18 次中 1 次」一致），所以改成 **route 延遲 3 秒**先逼佢出：
    冇 attr 嗰邊 `@768` 三次全部 **0.0585**（1 個 shift entry）、`@390` 三次入面兩次 **0.0262**；
    有 attr 嗰邊 `@768` / `@390` 各三次 **全部 0.0000、0 個 entry**。
    「冇 attr」嗰組係 route 改寫 HTML 剝走 `width`/`height` 造出嚟，每 run 都覆檢
    hydrate 之後 attr 仍然係 `null`（React 19 冇補返）。
20. **⚠️ `.detail-art img` 個 **元素盒** 真係變大咗，唔係 0 delta（`geom.json`）。**
    `<img width height>` 係 presentational hint（`width:429px; height:600px`），**兩軸都寫死之後
    冇咗保比例約束**，`max-width`/`max-height` 各自 clamp，所以個盒由「內容大細」變成「撐滿容器」：
    390 度 +148.687px 闊、1280 度 +10.844px 闊。**但用戶睇到嗰張圖冇郁**：`object-fit: contain`
    算出嚟嘅 paint rect delta **0.016px**，`.detail-art` 元素截圖兩邊 **byte-identical**
    （正常卡 + 怪比例卡 × 390/1280 四組全中）。正路修法係 CSS 加一條 `img { height: auto }`，
    但 WS-state 冇 CSS 檔權限（`globals.css` / `styles/*.css` 由第二個 agent 揸）——
    **留返欠單**。ranking thumb / heatmap tile 個盒係 0 delta，只有 `.detail-art` 有呢個形狀。
21. **7 張卡 bake 落嘅 base 尺寸同真正出街嗰個 variant 唔同（bake 側資料欠單）。**
    掃晒 `data/public/market-assets` 5709 個 WebP：1604 張卡嘅 `_600` **全部 429×600**、
    `_200` 全部 200×280，但有 7 個 base 檔係 719×1000 ×3 / 600×838 / 1000×730 / 500×698 /
    431×600。因為 `srcSet()` 一定出 variants 而且有 `sizes`，瀏覽器**永遠唔會**畫 base 檔，
    即係嗰 7 張卡宣告嘅比例描述緊一個唔會 render 嘅檔。實測無害（宣告值兩軸都大過容器，
    clamp 完個盒同正常卡一模一樣，截圖 byte-identical），但正解係 bake 側寫 variant 嘅尺寸。
22. **`box-image.tsx` 未消費 `image.width/height`。** `box-view.ts` 已經帶住（data-only），
    component 側未出 attribute —— `/box/[id]` 個圖仲係冇預留位。
23. **公開 API 契約 additive**：`/api/v1/market`、`/api/v1/cards/[id]`、`/api/v1/catalog` 三條
    route 都係直接 `Response.json` 個 view，所以 `image.width` / `image.height` 自動出咗街
    （實測 429/600、1000/730）。**只加 key，冇改冇刪**。全個 `docs/` 冇任何檔寫過呢個 payload
    嘅 schema，所以除咗呢度冇第二處要同步。
24. **「顯示更多」由 React state 搬去 URL（`show=<int>`）。** 只有大過 `CATALOG_LIST_CAP`(80)
    先出現喺 URL；`normaliseShow()` 嚴格淨數字（`240abc` 唔准當 240）、向上湊到 80 嘅倍數、
    上限 = 命中數湊足一版。`update()` 用 `router.replace` 所以唔加 history entry
    （實測撳兩次 `history.length` 一直係 **2**）。q 一變就 delete 個 param，`SortFilterSheet`
    嘅「還原」一樣 delete。
25. **back-nav 實測（`show_url.json`）**：`/?q=a` 撳兩次 → `?q=a&show=240` / 240 行，
    撳第 150 行（`rect_top 365.5`、`scrollY 18747`）入卡頁再 back → URL 保住 `show=240`、
    240 行、嗰行仲喺度、`rect_top` **365.5**（**delta 0.0**），而 `scrollY` 係 18987 ——
    即係 `scroll-restoration.tsx` 行嘅係**錨點相對**還原，唔係絕對 Y，數字對唔上唔代表壞咗。
    新開一版 `?q=a&show=240` 喺 390（mobile list）同 1280（desktop table）都出足 240 行。
26. **App Router 嘅 push 係 same-document**，Playwright `wait_for_url()` 由頭到尾唔會 fire。
    量呢類跳轉要 poll `location.pathname`，唔係 `wait_for_url` 壞咗。
27. **`naturalWidth` 喺 `srcset` w-descriptor + `sizes` 之下係密度校正過**：同一個 429×600 檔
    喺 1280 報 400×560、喺 390 報 250×351。WS2 寫嘅「實測呢張係 400 × 560」就係呢個。
    攞真實檔案尺寸唔可以信 `naturalWidth`。

### 明確非目標

唔遷 Tailwind / shadcn；唔上 WebGL / shader；`/` 唔加 marketing hero、唔加新字體；
唔改 `heatmap.tsx` 內部同 `exportHeatmap`；唔加 `app/card/loading.tsx`；
唔為視覺效果加 npm dep；無 scroll-jacking / parallax；sparkline 同 ranking row 唔動；
唔為咗動畫放鬆任何 gate / 契約 / crawler 可見文字。
