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
| `--font-inter` | `next/font/local` 出（`src/fonts/index.ts`，落 `<html className>`） | **只准由 `--font-sans` consume**；call site 唔准直用、唔准硬寫 `"Inter"`（family 係 hash 名） |
| `--f-latin` / `--f-jp` / `--f-tc` / `--f-sc` / `--f-kr` / `--f-tail` | `var(--font-inter)` / 各 script 嘅 OS 字 stack / `-apple-system, BlinkMacSystemFont, Arial, sans-serif` | **只准由 `--font-sans` 砌用**（§1.3.3）；call site 唔准直用 `--f-*` |
| `--font-sans` | `var(--f-latin), var(--f-jp), var(--f-tc), var(--f-sc), var(--f-kr), var(--f-tail)`（:root 默認 JP-first）+ 四條 `[lang]:lang()` 覆蓋按語言重排 | `body` 唯一字體。拉丁 self-host Inter（§1.3.1），CJK 行 OS 字、按 `<html lang>` 排先後（§1.3.3）。**冇 display face、冇 CJK web font** |
| `--font-mono` | `"SF Mono", "Cascadia Mono", Consolas, "Roboto Mono", ui-monospace, monospace` | 編號 / ID 類欄位（Windows 之前跌 Courier New，webfont commit 補 Cascadia / Consolas） |

`--font-mono` 之前喺四個地方逐字複製；FE05 收埋做一個 token，四處（`.collector-cell` :1509、
`.mobile-card-number` :1657、`.identity-list dd` :2147、`.box-set-chip` :2724）全部 call 佢。
驗證：`grep -c "SF Mono" globals.css` → **1**（就係 token 定義嗰行；plan 寫 →0 係手民之誤，
token 定義本身就住喺 `globals.css`，→0 冇可能）。真正嘅驗收係 **零 call site 硬寫**：
全 `apps/web/src` grep `font-family` 剩返 3 × `var(--font-mono)` + 1 × `var(--font-sans)` + 1 × `inherit`（`.chart-label`）。

#### 1.3.1 Web font 交付（FE05 webfont commit，2026-08-17；推翻 §8 決定 5）

**點解要**：舊 `--font-sans` 係 Apple system stack → `"Yu Gothic"` → Arial。Windows 冇 SF Pro / Helvetica Neue /
PingFang / Hiragino，第一隻存在嘅係 **Yu Gothic（Windows 內置日文字）**，Windows Chrome / Edge 全站拉丁 + 數字
都由 Yu Gothic 嘅拉丁字形出（投訴「用緊日文字體」「冇 load 好」，B 2026-08-17）。而且 Yu Gothic / JhengHei /
YaHei / Malgun 全部只得 300/400/700 static weight，CSS 匹配 500→400、>500→700，`h1,h2,h3{font-weight:500}`
出 Regular、`.heatmap-total-cap` 600 / `.cap-ticker` 650 出 Bold —— **標題 vs 標籤喺 CJK 系統字上必然倒轉**。
呢個係 bug，唔係品味。

| 項 | 值 | 點解 |
|---|---|---|
| 檔 | `src/fonts/InterVariable-latin.woff2` 48,256 bytes，sha256 `3100e775…bc62`（寫喺 `src/fonts/index.ts`，contract test 對數） | `@fontsource-variable/inter@5.3.0` `files/inter-latin-wght-normal.woff2`，**手抄 binary 唔 `npm i`**（`scripts/test-lockfile-prod-pins.mjs`：任何 npm i 都可能 re-hoist browserslist） |
| subset | latin only（U+0000-00FF + 常用標點 / ₤€™↑↓−∕）；**唔收 latin-ext**（+85 kB）| CJK / 諺文一個 glyph 都冇，靠 `--font-sans` 後面 OS 字逐字 fallback；₩ ₹ ₱ ₫ ₪ ₺ ฿ 一樣行 OS fallback |
| 載入 | `next/font/local`：`weight "100 900"`、`display: "swap"`、`preload: true`、`adjustFontFallback: "Arial"` | `swap` 唔係 `optional`：optional 100ms 內攞唔到就今次永不 swap，第一次訪問大概率照睇 Yu Gothic = 修唔到投訴。Arial metric fallback（size-adjust / ascent / descent override）壓 swap CLS |
| 位置 | `inter.variable` **一定落 `<html>`**（`layout.tsx`） | `--font-sans` 住喺 `:root`；`--font-inter` 只喺 body 定義嘅話 `:root` 度 `var()` 解唔到 → 整條 `--font-sans` invalid → 全站跌 serif、CI 照綠 |
| CSP | `font-src 'self'` 唔使改 | next/font 出 `/_next/static/media/*.woff2` 同源；`prepare-standalone.mjs` 已 copy `.next/static` |
| 點解唔 `next/font/google` | webhook `docker compose up --build` build stage 要出外網攞字體，一次 DNS / egress 失敗 = deploy fail；google 版最終都係 self-host，CSP 零分別 |
| 授權 | SIL OFL 1.1，`src/fonts/OFL.txt` 同行（binary 派發義務） |
| 契約 | `scripts/test-fe-font-contract.mjs`（`npm test` 自動 glob）：next/font 只准 `src/fonts/index.ts` 一處、零手寫 `@font-face` / googleapis / gstatic、`--f-latin = var(--font-inter)` + 每條 `--font-sans` 宣告打頭 `var(--f-latin)`（cjk commit 起，見 §1.3.3）、variable 落 `<html>`、OFL + sha256 + ≤60 kB。加完種過 3 個 bug 見紅（body 位置 / 外部 host / --font-sans 換頭）再還原 |

**連帶改動（同一 commit，唔准拆開出街）：**

- body：`font-feature-settings: "kern" 1, "tnum" 1` → `font-variant-numeric: tabular-nums`（低階屬性 all-or-nothing，後代一寫就抹走 tnum；tnum 喺 Yu Gothic 上係死嘅，Inter 上「復活」→ 全站數字 advance 一齊變）；加 `font-synthesis: none`（CJK 只得 Regular 嘅家族唔准合成粗體，10–13px 漢字會糊）；拆 `text-rendering: optimizeLegibility`。
- `lib/tile-style.ts` `tileLabelEm`：由 canvas `measureText` 改做**逐字元查表**。Inter 4 default 數字係 proportional（「1」0.415em），CSS 靠 tnum 先等闊，canvas 睇唔到 tnum 亦睇唔到 letter-spacing，比例按內容 0.95–1.18 飄，一個 fudge 修唔到；亦解決咗「第一次量到 fallback 就永久 cache」同 SSR ≠ CSR。表值：數字 / + / − 0.6455、`.` 0.2686、`%` 1.0293（Inter 800 tabular，DOM 實測），+ `.tile-move` letter-spacing 0.01em/字。swap 前 Arial-metric 數字 0.556 窄過表值 → 只會細少少，唔會爆邊。
- `scripts/test-fe-heatmap-title-width.mjs` 估算表換 Inter 500 逐字元 advance + tabular 數字 0.6455；divisor 9.5 **唔郁**（最闊「ワンピース TOP 100」估 9.08 / DOM 9.057em = 95.4%）；negative self-test 照 fire（舊 EN 11.34、舊 JA 14.84 > 9.5）。

**§1.3「零 reflow ≤ 2px」對呢粒 commit 豁免**：換字體 = 明知全站 Latin 幾何會郁（Inter vs Yu Gothic 拉丁 +3.3%：canvas 100px「Top 100 market heatmap」1180.66 vs 1143.12 vs Arial 1106.2）。驗收標準改為：**冇跌出容器 / 冇撞埋 / 冇新 ellipsis、CLS ≤ 0.01、`/` LCP element 仍係 `.heatmap-heading h1`**（量到嘅實數見 §8 log 該行）。

**量度腳本**：`temp/fe05/review-webfont/measure-inter.mjs`（headless Chromium 對 dev :3901，`document.fonts.ready` 之後）出：逐字元 advance（500 / 600）、五語言標題 DOM 闊度、tile label 800 tabular 逐字元、三個 context `1111` vs `9999` 差 = 0、`fontSynthesis: none`。換字體 / 改 tile weight / 改字距 = 重跑再更新兩張表。

**注意**：`document.fonts.check("500 15px Inter")` 喺呢個 build **會回 true** —— next/font/local 出嘅 family 叫 `inter`（跟 export 名），family 比對 case-insensitive；所以「硬寫 Inter 一定 false」呢句唔成立，驗證要睇 `getComputedStyle(document.body).fontFamily` 第一項 = `inter` + `[...document.fonts]` 有 `inter 100 900` loaded。

#### 1.3.2 字重 / 微字級 / 行高 / 字距 token（FE05 type-scale commit，2026-08-17）

**點解要**：§1.3.1 淨係換咗「邊隻字」；標題 vs 標籤倒轉嘅第二半係字重本身。CJK 系統字（Yu Gothic /
JhengHei / YaHei / Malgun）只得 Regular / Bold 兩級，CSS 匹配界線 = **500**（500→400、>500→700）。
舊 scale `h1,h2,h3` 500、細標籤 550 / 650 —— 26 條 550 / 650 宣告喺 CJK 上完全冇分別，而標題（500→Regular）
仲輕過標籤（650→Bold）。所以字重角色**一定要跨過 500 界**，而且 weight 唔准做階層唯一載體（size + weight + colour 三樣一齊）。

| Token | 值 | 角色 / call site 例 |
|---|---|---|
| `--w-body` | 400 | 內文（body 預設） |
| `--w-quiet` | 500 | 靜態次要：`.heatmap-total-cap`（**由 600 降落嚟**）、`.footer-brandline`、`.ranking-jump`、`.prestige-tagline` |
| `--w-heading` | 600 | `h1,h2,h3`（**由 500 升上嚟**）、`.related-group h2` |
| `--w-name` | 600 | 卡名：`.ranking-name strong`、`.grader-table .grader-card strong`、`.catalog-hit-copy strong` |
| `--w-label` | 600 | 大寫 kicker / 表頭 / chip：`.section-kicker`、`.rank-kicker`、`.desktop-ranking-table th`、`.footer-*-title`、`.sort-sheet-group h4`、`.detail-rank`、`.print-badge`、`.empty-state-action`、`.sort-sheet-actions button`、tune 掣 |
| `--w-data` | 600 | 數字：`.cap-ticker`、`.price-now`、`.market-cap-cell`、`.detail-metrics strong`、`.grader-supply-grid strong`、`.grader-share-center strong`、`.hub-stat dd`、`.related-figures strong`、`.mover-chip em`、`.tune-field output` |
| `--w-strong` | 700 | 硬 700 保留位（`.mobile-card-price` / `.currency-symbol` / `.rank-cell[data-rank="3"]` 仍然直寫 700） |
| `--w-tile` | 800 | `.tile-move`（直寫 800，同 `tileLabelEm` 表值綁死，唔准淨改 token） |
| `--fs-nano` / `--fs-micro` / `--fs-th` | 9px / 10px / 9.5px | 微字級 floor：CJK 承載文字 ≥10px（`.preview-facts dt` 9→10、`.preview-time` 9→10、`.footer-*-title`、`.select-group-label`、`.rank-kicker`、`.sort-sheet-group h4`、`.heatmap-legend`、`.print-chip`、`.grader-supply-grid span`、`.grader-share-center span`）；`th` 9.5。**10.5px 嘅（`.related-meta` / `.related-figures em` / `.hub-stat dt` / `.seo-table thead th` / `.seo-table-sub` / ≥1440 `th`）寫 `max(10.5px, var(--fs-micro))`** —— 拉丁唔郁，C3 CJK token 升到 11 先跟。純數字微標（`.price-delta` 9.5、`.mobile-lang-badge` / `.mobile-card-number` / `.collector-cell` 9–10）唔郁。`.mobile-list-header` 9→**10px 固定唔用 token**：390px 五欄一行，CJK 11px 欄名會互撞 |
| `--lh-display` / `--lh-hero` | 1.04 / 1.06 | `h1,h2,h3` / `.heatmap-heading h1,h2`（`/` H1 高度 = lh × fs，C3 CJK 唔郁 `--lh-hero`） |
| `--lh-clamp2` / `--lh-clamp2m` | 1.15 / 1.25 | `.preview-copy h3` / `.mobile-card-name` 兩行 clamp；`max-height` 一律寫 `calc(2 * var(--lh-*) * 1em)` |
| `--lh-copy` | 1.55 | `.hero-copy` |
| `--clip-pad` | 0.16em | `.heatmap-heading h1,h2` `padding-block` + 負 margin 抵消：clamp 盒唔裁 descender / CJK 上下 |
| `--track-display` / `--track-name` / `--track-copy` | -0.035em / -0.02em / -0.012em | `h1,h2,h3` / `.detail-header h1` / `.hero-copy` |
| `--track-kicker` / `--track-th` / `--track-sentence` | 0.14em / 0.055em / 0.1em | `.section-kicker`、`.content-toc h2`、`.content-more h2` / `.desktop-ranking-table th` / `.prestige-tagline` |

**首頁階層（`.heatmap-heading`）**：H1 `var(--w-heading)` 600（字級公式一個字唔郁）、`.heatmap-total-cap` 14px 保留 weight
`var(--w-quiet)` 500、`.cap-ticker` `var(--w-data)` 600。階層由 size（68:14）+ weight（600 vs 500，跨 500 界）+ colour
（ink : muted）三樣承載，accent 只剩個數字。

**`case` feature**：只落 uppercase label class（globals.css 一條 grouped rule 8 個 selector + `content-pages.css` 3 個 +
`hubs.css` 2 個 + `glow-badges.css` 1 個），**唔准全局開** —— `·` 係全站分隔符，`case` 會將佢升高。

**`overflow-wrap: anywhere` → `break-word`**（`.preview-copy .muted-copy`、`.preview-facts dd`）：grid 欄已 `minmax(0,1fr)` +
`min-width:0`，`anywhere` 喺有位嘅情況下都會揀「Brilliant Star|s」咁斷。

**硬規**：全站 computed `font-weight` ⊆ {400, 500, 600, 700, 800}；`grep -rnE "font-weight:\s*(550|650)" apps/web/src` → 0
（review agent check #9）。新規則要字重就 call token，唔准再發明 550 / 650。

流體字階，六級。**step-2/3/4 逐字照抄 FE04 原本嗰條 `vw` clamp**，唔係另外畫一條線。

| Token | 值 | ramp 窗口 | 已落點 |
|---|---|---|---|
| `--step--1` | `clamp(12px, 1.3vw, 13px)` | 923–1000px | `.mover-chip`（WS2）、`.empty-state-title`（WS4） |
| `--step-0` | `clamp(14px, 1.5vw, 15px)` | 933–1000px | `.hub-lead`、`.provenance-panel h2`（**fix-visual**） |
| `--step-1` | `clamp(16px, 2.222vw, 17px)` | 720–765px | `.content-answer`、`.hub-answer`（**fix-visual**） |
| `--step-2` | `clamp(20px, 3vw, 26px)` | 667–867px | `.content-body h2`（= FE04 原式） |
| `--step-3` | `clamp(26px, 3.4vw, 42px)` | 765–1236px | `.hub-hero h1`（= FE04 原式） |
| `--step-4` | `clamp(28px, 5vw, 42px)` | 560–840px | `.content-hero h1`（= FE04 原式） |

#### 1.3.3 CJK：per-`:lang()` 字體 stack + CJK token 覆蓋 + 卡名 `lang`（FE05 cjk commit，2026-08-17）

**點解要**：§1.3.1 之後拉丁係 Inter，但 Inter 冇一個 CJK glyph，漢字 / 假名 / 諺文全部靠 `--font-sans` 後面嘅 OS 字逐字
fallback。舊 stack 一條到底（JP 字排先），即係 **zh-TW / zh-CN 頁嘅漢字喺 Windows 都係 Yu Gothic 出**（Han unification：
「直 / 骨 / 画」字形跟字體國別，台灣用戶見到日式字形），ko 頁嘅漢字同樣。而且 §1.3.2 嘅字距 / 行高 / 微字級係為拉丁調嘅
（-0.035em display tracking、9–10px 微標、1.04 行高）—— 對 CJK 一律唔啱：CJK 唔靠 tracking 出聲線、方塊字 10px 以下糊、
1.04 行高會裁上下。所以 C3 做三件事，**全部係 token 覆蓋，唔重寫版面規則**。

**1. 字體 stack 拆做 per-script token，按 `<html lang>` 重排**（globals.css `:root` + 緊跟 `:root {}` 後面嘅 4 條 `[lang]:lang()`）：

| Token | 值 |
|---|---|
| `--f-latin` | `var(--font-inter)` |
| `--f-jp` | `"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Yu Gothic", YuGothic, Meiryo, "Noto Sans JP", "Noto Sans CJK JP"` |
| `--f-tc` | `"PingFang TC", "PingFang HK", "Microsoft JhengHei", "Noto Sans TC", "Noto Sans CJK TC"` |
| `--f-sc` | `"PingFang SC", "Microsoft YaHei", "Noto Sans SC", "Noto Sans CJK SC"` |
| `--f-kr` | `"Apple SD Gothic Neo", "Malgun Gothic", "Noto Sans KR", "Noto Sans CJK KR"` |
| `--f-tail` | `-apple-system, BlinkMacSystemFont, Arial, sans-serif` |
| `--font-sans`（`:root` 默認） | `latin, jp, tc, sc, kr, tail` —— 未標 lang 嘅漢字 JP-first（owner 默認：卡本身係日版為主） |
| `[lang]:lang(ja)` | `latin, jp, tc, sc, kr, tail` |
| `[lang]:lang(zh-Hant)` | `latin, tc, sc, jp, kr, tail` |
| `[lang]:lang(zh-Hans)` | `latin, sc, tc, jp, kr, tail` |
| `[lang]:lang(ko)` | `latin, kr, jp, tc, sc, tail` |

**兩個陷阱（contract test 守住）**：
- **每條 `[lang]:lang()` 一定要同時再寫 `font-family: var(--font-sans)`** —— `body { font-family: var(--font-sans) }` 只 compute 一次，
  後代 `<span lang="en">` 繼承嘅係 *computed* family，淨改 token 唔會生效。selector 用 `[lang]:lang(x)`（specificity 0,2,0）
  係為咗壓過 `body`（0,0,1）同 `.xxx`（0,1,0）—— 唔准喺 call site 再寫 `font-family`。
- **`:lang(zh-Hant)` 唔 match `lang="zh-TW"`**（RFC 4647 extended filtering 只由 prefix 對）。所以 `<html lang>` 同任何 `lang` 屬性
  **只准出五個值 `en | ja | ko | zh-Hant | zh-Hans`**（`lib/card-name.ts` `htmlLang()`；`document-language.tsx` LangScript + `DocumentLanguage`
  兩處都行呢個 map），唔准喺 JSX 硬寫 `lang="zh-TW"`。
- 唔用 `Yu Gothic UI` / `Microsoft JhengHei UI` / `YaHei UI`（Windows UI 變體字面較窄、行高較細）：§1.3.1 / §1.3.2 嘅量度（title-width 表、
  tile label、H1 高度）全部係用非 UI 版量嘅，換 UI 版就要全部重量。

**2. CJK token 覆蓋**（一個 `:is(:lang(ja), :lang(ko), :lang(zh-Hant), :lang(zh-Hans)) { … }` block，緊跟 stack 規則）：

| Token | 拉丁（§1.3.2） | CJK | 點解 |
|---|---|---|---|
| `--fs-nano` / `--fs-micro` / `--fs-th` | 9 / 10 / 9.5px | **10 / 11 / 11px** | 方塊字 floor 10、標籤 11（§1.3.2 嗰堆 `max(10.5px, var(--fs-micro))` 就係為呢一步留嘅） |
| `--lh-display` / `--lh-clamp2` / `--lh-clamp2m` / `--lh-copy` | 1.04 / 1.15 / 1.25 / 1.55 | **1.25 / 1.35 / 1.4 / 1.75** | 方塊字上下滿格，1.04 會裁 |
| `--lh-hero` | 1.06 | **1.06 唔郁** | `/` H1 高度 = lh × fs，郁咗就偷 heatmap 高度；改用 `--clip-pad` 頂。⚠️ 驗收係「**390 度五語言相等**」（H1 撞住字級下限 20px）——**1280 由來未相等過**：H1 fs = `min(4.6vw, calc(100cqi / 9.5))`，1280 度綁住 `100cqi/9.5`，而 cqi（`.heatmap-title` 容器）隨 `.heatmap-controls` 字長逐語言變 426.25–440.25px → fs 差 1.474px → 高度差 1.54px。實測 live（C2，冇 CJK block）同 dev（C3）五個數逐位相同，即係版面結構本身，唔係 CJK token 造成（`temp/fe05/review-webfont/heading-baseline-live.mjs`） |
| `--clip-pad` | 0.16em | **0.24em** | H1 padding-block（負 margin 抵消），CJK 上下唔裁 |
| `--track-display` / `--track-name` / `--track-copy` / `--track-sentence` / `--tracking-tight` | -0.035 / -0.02 / -0.012 / 0.1em / 舊值 | **0** | CJK 唔靠 tracking；`.prestige-tagline` 0.1em 對整句日文 / 韓文係最刺眼嗰個 |
| `--track-kicker` / `--track-th` / `--tracking-wide` | 0.14 / 0.055 / 舊值 | **0.05 / 0.02 / 0.03em** | 大寫 kicker 對漢字 no-op，但字距唔可以係 0.14em |
| **`--track-hero`**（新，`:root` 一次） | -0.035em | **-0.035em 唔郁**（唔喺 CJK block 出現） | `.heatmap-heading h1,h2` 由 `--track-display` 改行 `--track-hero`：ja「ワンピース TOP 100」清零就係 9.52em > 9.5 × 0.97 = 出「…」。`test-fe-heatmap-title-width.mjs` 直接讀呢個 token（唔硬寫 -0.035）並 assert 全 globals.css 只宣告一次 |

再加四條**唔係 token** 嘅 CJK 規則：`:lang(ko) { word-break: keep-all; overflow-wrap: break-word }`（韓文按詞斷）；
`:is(:lang(ja), :lang(zh-Hant), :lang(zh-Hans)) :is(h1,h2,h3,.mobile-card-name,.preview-copy h3,.muted-copy,.preview-facts dd,.hero-copy,.story-panel p) { line-break: strict }`
（禁則：「。」「、」唔准企行首）；**`:is(:lang(ja),:lang(ko),:lang(zh-Hant),:lang(zh-Hans)) [lang="en"] { word-break: normal; overflow-wrap: break-word; line-break: auto }`**
（見下面 3.）；`.rank-kicker / .select-group-label / .footer-methodology-title / .footer-nav-title / .sort-sheet-group h4 / .seo-table thead th`
六條硬寫 letter-spacing 嘅標籤喺 CJK 下改行 `var(--track-kicker)`。`text-transform: uppercase` 保留（對漢字 no-op）。
`test-fe-font-contract.mjs` 掃全部 CSS：凡硬寫 `letter-spacing` 絕對值 > 0.04em 而唔喺收編名單 = 紅（原本漏咗
`.sort-sheet-group h4` 0.06em，ja「並べ替え」真係食住 20% 超標字距）。

**3. 卡名 `lang` 屬性**（`lib/card-name.ts` `displayCardNameLang()` / `cardNameLangAttr()`）：`displayCardName` 跌落英文 `officialName`
嗰啲卡（zh-CN 譯名覆蓋最少）喺 CJK 頁出 `lang="en"`，譯名存在就唔出屬性（同頁面語言一樣）。
**唔用 `card.cardLanguage`**（實體卡印刷語言，唔係顯示緊嘅名嘅語言）。
⚠️ **`lang="en"` 自己一個係改唔到 CSS 嘅**：`:lang(ko)` 嘅 keep-all 由祖先**繼承**落嚟、`line-break: strict` 嘅 subject 係
`:is(h1, .mobile-card-name, …)` 按 tag/class 直接命中元素本身 —— 兩條都唔會因為元素標咗 `lang="en"` 而唔 match。
真正嘅 opt-out 係上面第四條明寫規則（specificity 同 strict 條打和 0,2,0，靠排喺佢後面贏）。
`test-fe-lang-attr.mjs` assert 呢條規則存在、解除 `word-break` + `line-break`、而且排喺 `line-break: strict` 之後。
落點：`rankings.tsx`（桌面 + 手機卡名）、`card-detail.tsx` H1、`related-cards.tsx`。**`heatmap.tsx` 嘅 tile aria / preview 名冇加**
（review gate off-limits 檔，見 §8 該行「未做」）。

**4. 文案（唔用 `text-autospace`）**：**四個** live ja 文案檔 —— `lib/i18n.ts`、`lib/site-copy.ts`、`lib/hub-copy.ts`（63 處）、
`lib/related-cards.ts`（12 處）—— ja block 和欧混植補半形空格：`PSA10`→`PSA 10`、`BOX市場`→`BOX 市場`、
`未開封BOX`→`未開封 BOX`、`トップ100`→`トップ 100`、`のFAQ`→`の FAQ`；`ja.hero.title` 拆走假名／漢字之間嘅空格
（「ポケモンカード・トレカ時価総額 — PSA 10 指数」）。
⚠️ 呢條規則本來只寫喺呢度、冇 call site，結果 `hub-copy` / `related-cards` 漏咗成個月都冇人發現。
而家由 `scripts/test-fe-lang-attr.mjs` ⑥ 守：掃呢四個檔（**去咗註釋先掃**），命中即紅。
**唔好裸 grep 成個 `apps/web/src`** —— 全 repo 剩低嘅命中係廣東話／英文註釋（`lib/types.ts:115`、`lib/live-db-snapshot.ts:115`、
`globals.css:2225`…），當紅會逼人改註釋。亦**唔做**「漢字貼住拉丁就紅」嘅通用偵測：`101位以降` 係故意保留嘅正常寫法。

**5. `CapTicker` 單位鎖**（`lib/ticker-start.ts` `tickerStart()` / `tickerEase()`）：compact 單位由 Intl 按值揀（$999M → $1.0B、万 → 億），
由 0 滾上去途中會換字、字串長度跳。起點改為「同一單位範圍最低嘅 10 冪」（$1B → $2.7B、1億 → 12.31億），跨單位 retarget
由該範圍邊緣接落去（$2.7B → $850M 變 $999.9M → $850M）。format 係黑盒，只靠字串「單位簽名」（拆走數字 / 分隔符 / 空格）比對。
兩個真係爆過嘅邊界：

- **`tickerEase` 一定要夾 `[0,1]`**：`start` 喺 layout effect 攞，rAF callback 收到嘅 `now` 係嗰 frame 開始嗰刻，
  同 frame 內就會 `elapsed < 0`；ease-out cubic `1-(1-p)³` 喺 p<0 回負值 → 顯示值跌到起點以下。
  dev 實測起點 $1B 第一 frame 出咗 **$993.75M**（單位跳返 M）。
- **target 貼住單位範圍頂（頭 0.01%，例如 $999.99M）搵唔到更高嘅同單位起點 → 回 `target`（唔滾）**，
  唔准回範圍底：舊寫法回 `$1M`，即係 `$12B → $999.99M` 呢個**跌價**畫面由低三個數量級嘅位向上滾。

契約 `scripts/test-fe-ticker-unit.mjs`：5 locale × 6 貨幣 × 10 目標 = 900 條 path 各採 101 點 + 298 條真 rAF path（由 −2 frame 掃起）
+ **4800 條單位邊緣 path**（每個 10 冪 ×0.999999…×1.01）。方向斷言係**無條件**嘅（`from > target → start ≥ target`）——
舊版用 `roomAbove` 守住，而 `$999.99M` 啱啱就係 `roomAbove === false`，個 bug 就係咁綠住出街。

**html lang 唔加 cookie fallback**：`middleware.ts` 對非 en 嘅 document navigation 一定 302 補 `?lang=`，URL 就係 locale 真相；
LangScript 讀 cookie 出 ja 而 render 出嚟係 en 內容 = 脫節，寧願唔加。

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
`.provenance-panel` 嘅 call site 由 component import 反查（review fix 2026-08-17，`grep components/provenance`）：
`market-page.tsx`（`RankingSurface` → `/`、`/pokemon`、`/one-piece`）、`box-market-page.tsx`（`/box`，
`/sealed` 只係 `redirect("/box")` 嘅 alias）、`box-detail.tsx`（`/box/[id]`）、`card-detail.tsx`（`/card/[id]`）；
`.content-*` 喺 `ContentPage` 嘅五個 route：`/about`、`/methodology`、`/faq`、`/glossary`、**`/data`**。
**呢兩行就係量度矩陣嘅完整清單，加頁之前先 grep import，唔好靠記。**

**已收編（fix-visual，2026-08-17，owner 明文授權接受 reflow）：**

四個階梯落點收咗，**呢個係設計改動唔係零 reflow 收編** —— 下面「fix-visual 量到嘅實數」
就係新 baseline，之後量度一律同佢比，唔好再攞 WS1 嗰組數當基準。

| 位置 | 原本 | 而家 | 效果 |
|---|---|---|---|
| `.content-answer` | 17px / ≤720px 16px | `var(--step-1)` | ≤720 同 ≥765 一模一樣；ramp 只喺 721–764 之間 |
| `.hub-answer` | `clamp(14px, 1.4vw, 17px)` | `var(--step-1)` | **同 `.content-answer` 統一咗**（一個角色一套字級）；手機由 14 → 16px |
| `.hub-lead` | 14px / ≥981px 15px | `var(--step-0)` | 步位由 981 變成 933–1000 嘅 ramp |
| `.provenance-panel h2` | 15px / ≤900px 14px | `var(--step-0)` | 步位由 900 變成 933–1000 嘅 ramp（901–932 由 15px 變 14px） |

**仲未收編（欠單，要 owner 另外拍板）：**

| 位置 | 現況 | 點解未收 |
|---|---|---|
| `.hub-note` | 12px / ≥981px 12.5px | 斷點階梯；`--step--1` 上限 13px，1280px 量到累積 **22.9px** |
| `.provenance-body` | 13px 平頭 | `--step--1` 喺 390px 係 12px，量到 **5.1px** |

**WS1 欠單②「`--step-0` / `--step-1` 有定義零 call site → WS5 完仲係零就刪」已經結案：
兩個 token 而家各有兩個 call site，行「收編」唔行「刪」。** 六級字階全部有落點。

字距：`--tracking-tight` `-0.02em`（大標題）、`--tracking-wide` `0.08em`（大寫細標籤）。
全局 `h1,h2,h3` 嘅 `-0.035em`（:265）**唔喺字階入面** —— 佢係首頁聲線嘅一部分，唔准順手改。

#### 1.3.4 圖片出口嘅字體（FE05 og-share commit，2026-08-17）

網站有**三個**出口會渲染文字，各行各嘅 renderer，字體唔會自動跟：

| 出口 | Renderer | 點攞 Inter | 一錯會點 |
|---|---|---|---|
| 網頁 | 瀏覽器 | `next/font/local` → `--font-inter`（§1.3.1） | 全站跌 fallback |
| `/api/og/card/[id]` | **satori**（`next/og`） | `public/fonts/og/*.ttf` 經 `ImageResponse({ fonts })` | 靜靜用 satori 內置 Geist |
| 分享 PNG | **canvas**（`share-image.ts`） | `getComputedStyle(document.body).fontFamily` | 靜靜用系統字（Windows = Segoe UI） |

三條硬規矩：

1. **satori 唔食 woff2、唔食 variable font。** 所以 OG 要獨立一套 **static TTF**，唔可以
   重用 §1.3.1 個 `InterVariable-latin.woff2`。只 register **400 / 600 / 700**，
   而且 **satori 唔會合成字重** —— layout 寫 500 就靜靜跌去最近嗰個 face，冇 error。
   `test-fe-font-contract.mjs` ⑥ 掃住成個 route 嘅 `fontWeight:`，跳出呢三個數即紅。
2. **canvas 嗰邊唔准硬寫 `"Inter"`。** next/font 出嘅 family 名 dev 係 `inter`、
   production build 係 `__Inter_xxxx` —— 硬寫喺 prod 一定 miss，而 `ctx.font = "700 26px Inter"`
   **唔會 throw**，只會靜靜跌返上一個有效值。一定要讀 `getComputedStyle(document.body).fontFamily`，
   而且要喺 `await document.fonts.ready` **之後**先 build 啲 font string。
3. **OG 字體 load 唔到要 fail-open。** 讀盤失敗就 `console.warn` + 唔傳 `fonts`（退返 bundled font），
   唔好為咗字體令個 OG endpoint 500 —— 社交平台抓唔到圖比字體唔啱睇嚴重好多。

檔案要傍 `OFL.txt`（OFL 1.1 派發義務），sha256 抄喺 `route.tsx` 檔頭，由 ⑥ 對數。

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
| `live-dot-pulse` | `styles/glow-badges.css` | live 徽章綠點嘅擴散環（`.live-dot::after`），**`1.2s × 5` = 6s 之後停**，**冇 fill-mode**（100% 格 = base style，播完 `getAnimations()` 歸 0，同 `card-sheen-sweep` 一致） | 同檔 `@media (prefers-reduced-motion: reduce)`：`.live-dot::after { animation: none; display: none }` |
| `card-sheen-sweep` | `styles/card-art.css` | 手機／無 hover 嘅一次性 holo 掃光（`.card-sheen::before`），`1.6s linear × 1`、**冇 fill-mode**，播完 `getAnimations()` 回 0 | 同檔 reduce block：`.card-art[data-art-sheen="ready"] .card-sheen{display:none}` + `::before{animation:none}`（**selector 形狀要同 `@media (hover: none)` 嗰兩條一樣**，見 §3.4） |

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

**例外（fix-visual）：`card-sheen-sweep` 係「手機專屬」。** 唔係「桌面有、手機減」，
係反過嚟 —— 桌面靠 pointer 掃 sheen，冇 hover 嘅機根本冇 pointer 事件，卡圖由頭到尾
死實。所以喺 `@media (hover: none)` 補**一次**掃光（唔係常駐 ambient），掃完返去同
以前一模一樣嘅靜態低透明 holo。原則冇變：**手機唔准有常駐動畫**。

### 3.4 reduced-motion sibling 嘅 selector 形狀（fix-visual 學到）

**sibling 嘅 selector 特異度要 ≥ 佢要壓嗰條，唔係寫個 class 就算。**
`card-sheen-sweep` 第一版喺 reduce block 寫 `.card-sheen { display: none }`（0,1,0），
但開啟嗰條係 `.card-art[data-art-sheen="ready"] .card-sheen`（0,3,0）——
**特異度輸咗，source order 幫唔到手**。實測（`temp/fe05/visual/rule9.json` 第一輪）
喺 `reduce` context 手動種返個 attribute，computed 仍然係 `display: block` +
`animationName: card-sheen-sweep`：即係呢個 sibling **寫咗等於冇寫**
（AGENTS.md 規矩 9「有檢查但零 call site 當冇檢查」嘅 CSS 版）。
修法係喺 reduce block 用同一個 selector 形狀再寫一次。

**驗法：喺 `reduce` context 手動 force 返個 DOM 狀態（種 class / 種 attribute）再讀
computed style**，唔好淨係睇「JS 冇 arm 所以睇唔到動畫」——嗰個係 JS 閘 pass，
唔係 CSS sibling pass。

---

## 4. 效能預算

呢啲數係硬預算，唔係目標：

1. **第一屏（`/`）= heatmap + 一行總市值。就係咁多。**
   唔加 marketing hero（owner 2026-08-16）、唔加 WebGL/shader、
   唔喺 `/` 首屏加任何 IntersectionObserver reveal。`/` 嘅 LCP element 唔准變。
   字體：拉丁收窄為**一隻** self-host variable font ≤ 60 kB（Inter latin，§1.3.1，owner 2026-08-17）；
   **唔准第二隻 / display face / CJK web font**（Noto CJK 幾百 kB 起，第二期另議 `font-display: optional`）。
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
- **要郁嘅 overlay 一定要兩層：外層揸 mask（唔郁）、內層做 transform。**
  mask 落邊個 element，transform 就連 mask 一齊搬，即係「sheen 留喺卡形之內」直接爆。
  fix-visual 個手機掃光就係咁：`.card-sheen`（`inset: var(--card-art-pad)` + mask +
  `overflow: hidden`）包住 `.card-sheen::before`（條光帶，`translate3d` 掃過）。
  `.card-art::before` / `::after` 兩個 pseudo 唔郁位（只換 `background-position`），
  所以佢哋單層就夠。

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
| **fix-visual** | header 兩個 select menu 轉實色底、`--step-0`/`--step-1` 收編（answer 角色統一）、live 綠點有上限脈衝、手機一次性 holo 掃光 | ✅ 已落（2026-08-17） |
| **fix-heatmap-title** | heatmap H1 永遠一行（`white-space: nowrap` + 字級 = `min(4.6vw, 100cqi/9.5)`，`.heatmap-title` 係 inline-size container）；heading 拆走描述句、footer 拆走 methodology-note；五語言標題縮到 ≤ 9em（`test-fe-heatmap-title-width.mjs` 守住） | ✅ 已落 + live（2026-08-17 13:50，22422add） |
| **fix-owner-round-0817** | owner 2026-08-17 三輪口頭 review：桌面填滿（shell cap 1440→2400、gutter `clamp(32px,3vw,72px)`、表格 88px 行／卡名欄 26%）、字級手機↔桌面統一（nav 14 / metric label 11 / 數值 22↔20 / story 15）、內頁順序「圖 → 走勢 → 市值/數量 → 簡介」、header search 常駐 + 升跌反轉掣搬落 nav 行（≤980）、卡榜 EN/JP/SC/TC chip（桌面 + 手機）、原盒 group 只分 TCG + 語言入排序 sheet／`.lang-filter`、品牌橙細節（heatmap 總市值數字、nav 現位底線 2px、升跌掣兩支箭嘴跟 `--positive/--negative`）；順手修 `.detail-metrics span` 食咗 `.cap-ticker` 令三格數值 9.5px 灰字 | ✅ 已落 + live（2026-08-17 06:31Z，49e27f95） |
| **fix-tile-label-fit** | heatmap 升跌 label 唔准食字：`fitTileLabel()` 用 canvas 量真字體闊度（同 `.tile-move` 同 family／800），先試完整 `+295.2%`，唔入就縮字（下限 8px）→ 去小數 `+295%` → 都唔入就唔顯示；離 tile 邊 4px、padding 1px 3px；share 圖同一套。實測 390/360/768/1440/1920 × 3 hub × 1D/1Y：0 格爆邊、最細邊距 4px（`temp/fe05/desktop-fill/tilelabel.py`） | ✅ 已落 + live（2026-08-17 07:22Z，03fb0ed7） |
| **feat-currencies** | 貨幣由 7 隻加到 **31 隻**（正典次序，USD 永遠 index 0）；trigger 同選項都出貨幣自己個符號做「logo」；選項出 code + ICU 本地化名（`Intl.DisplayNames`，五語系）；選單 **USD 釘最頂**（owner「usd默認最頂」），之後按 **亞太 → 美洲 → 歐洲 → 中東非洲** 分四組、`max-height: min(420px, 100svh − header − 24px)` 可捲；`availableCurrencies()`（`lib/server-snapshot.ts`）只出 snapshot 真係有匯率嗰批（讀唔到就 fail-open 出全部）；`<SiteHeader>` server wrapper 餵 prop 落 client `<Header>`；`geo-defaults.ts` 由 7 個國家加到 45 個 | ✅ 已落 + live（2026-08-17 08:17Z FE 3bc093a0；08:21Z bake 7a0a7214 出 31 隻匯率，live `?currency=SGD` → `SGD 34.58億`、EUR/MYR 有價） |
| **fix-share-image** | heatmap「分享圖片」PNG 重做（owner 2026-08-17 晚：「睇上去好山寨」→ 圖片主導、咩都唔使加、全英文、高清、色塊小圓角）：畫圖抽出 `lib/share-image.ts`（純 canvas `renderHeatmapShare()`）、`heatmap.tsx` `exportHeatmap` 只剩砌 opts → toBlob → share/download；`scale = clamp(2400/frameWidth, 2, 3.5)` + 5.2M px 面積封頂（390 直度出 1421×2304、1440 橫度 2774×1455）；底色 + radial vignette + 128² grain + accent hairline 框；tile 圓角 4×scale（同 `.heatmap-tile`）、卡圖 drop shadow、label 同 `.tile-move` 同一組幾何 + plate + text-shadow；header 量內容排一行／兩行；下底只剩一行 legend（swatch = `colors.up/down/neutral`，已跟 red-up／`/tune`）+「Deeper shade = bigger move」（唔夠位就唔出）；footer／QR／methodology／stat／tagline 全部拆走。順手修：tile↔card 對錯（`visibleCards[index]` vs treemap 重排 → 改 `tile.item.card`）、label 字級乘咗兩次 scale、`textBaseline` 漏出、toast 由「已複製連結」改 `t.share.done/error`；`taglines.ts` 死咗嘅 `pickRandomTagline` 刪走。QC：`temp/fe05/desktop-fill/share_image.py` 攔 `navigator.share`，390/1440 × zh-TW/en × light/dark × 綠升/紅升 16 張全 OK | ✅ 已落 + live（2026-08-17 11:04Z push d9af2fa8，11:05Z live chunk 已有新字串；live 實出 390→1421×2304、1440→2774×1455） |
| **fe05(webfont)** | 拉丁 self-host **Inter Variable latin**（§1.3.1；owner 2026-08-17 拍板，推翻 §8 決定 5）：`src/fonts/{InterVariable-latin.woff2,OFL.txt,index.ts}`（next/font/local，wght 100–900、swap、preload、Arial metric fallback）、`layout.tsx` `<html className={inter.variable}>`、`--font-sans` 打頭 `var(--font-inter)`、`--font-mono` 補 Cascadia / Consolas、body `font-feature-settings tnum` → `font-variant-numeric: tabular-nums` + `font-synthesis: none`、`tile-style.ts` canvas 量度 → Inter 800 tabular 逐字元查表、`test-fe-heatmap-title-width.mjs` 估算表換 Inter 逐字元 + tabular 數字、新 `test-fe-font-contract.mjs`。**只換字，字重／字級／字距一條冇郁**（C2 另 commit）。驗收（dev :3901 headless Chromium，`temp/fe05/review-webfont/{measure-inter,verify-c1,tilelabel}.mjs`）：body family 第一項 `inter`、`inter 100 900 loaded`；woff2 **1 個請求 48,256 B** `immutable` + `<link rel=preload as=font>`、0 CSP violation；`/` **CLS 0 / 0 / 0**（warm ×3）、cold-swap（woff2 +500ms）**0.0001**；LCP element 四次都係 `.heatmap-heading h1`；390 × 5 locale × `/`、`/pokemon`、`/card/[id]`：scrollWidth 全部 390、0 個元素 right > 390、H1 nowrap `scrollWidth == clientWidth`（零 ellipsis）、`.heatmap-heading` 高度五語言 **150px 全等**；tile label 20 組（390/360/768/1440/1920 × 3 hub + 1Y）**0 格爆邊、minGap 4px、overflowText 0**；`tsc` 0；`npm test` FE 全綠（2 個紅係 `pipelines/rebuild_036.py` 讀 machine-private `backend.env`，同 FE 無關）。**點樣反轉**：`git revert` 呢粒（woff2 / OFL 留 repo 無害）| ✅ 已落 + live（2026-08-17 12:17Z，push 2ee7d5a2；live 用內容認：`Link:` header 有 `rel=preload; as=font` woff2、woff2 200 / 48,256 B / `immutable` / `font/woff2`、`<html class="inter_…__variable">`、CSS chunk 有 `--font-inter` + `font-family: inter` + `tabular-nums`。⚠️ live `X-CARDZ-Build` 永遠係 `local`（`next.config.ts` `CARDZ_PUBLIC_BUILD_ID` 出街 build 冇設），**唔可以靠 header 對 SHA**，要用內容 marker） |
| **fe05(type-scale)** | 字重反轉修正 + 字級／行高／字距 token 化（§1.3.2；投訴 2026-08-17「H1 普通、副標粗、Windows 成個站日文字體」嘅第二粒）：`:root` 加 `--w-*` / `--fs-*` / `--lh-*` / `--track-*` / `--clip-pad` token；`h1,h2,h3` **500 → 600**（CJK OS 字 500 落 Regular，600 落 Bold —— 標題 vs 標籤必須跨 500 界）；`.heatmap-total-cap` **600 → 500**、`.cap-ticker` 650 → 600；26 條 550/650 全部收做 500/600 token（label / name / data / chip 角色）；`.catalog-hit-copy strong` UA 700 → 明寫 600；微字級 9/10px → `var(--fs-micro)`（10px，C3 CJK 升 11）、`.desktop-ranking-table th` → `var(--fs-th)`、`.mobile-list-header` 固定 10px（390 五欄，唔用 token）；`overflow-wrap: anywhere` → `break-word`（`.preview-copy .muted-copy` / `.preview-facts dd`）；`case` feature 只落 15 條 uppercase label class（`·` 係全站分隔符，唔准全局）。`test-fe-heatmap-title-width.mjs` 估算表換 Inter **600** 逐字元（widest 9.06em「ワンピース TOP 100」，DOM 對照誤差 ≤ 0.7%）；review agent check #9 加 `grep 550|650 → 0` + heading ≥ label pair + computed weight ⊆ {400,500,600,700,800}。驗收（dev :3901，`temp/fe05/review-webfont/verify-c2{,-preview}.mjs`）：5 locale × `/`、`/pokemon`、`/card/[id]`、`/market-report`、`/box` 每對 heading ≥ label 全 OK（H1 600/45.79px vs total-cap **500**/14px vs cap-ticker 600/14px；ranking h2 600 vs th 600/10.5px；detail h1 600/46px；related h2 600 vs meta 400；hub h1/h2 600 vs hub-stat dt 400）；全站 computed weight ⊆ {400,500,600,700,800}、`fontSynthesis: none`；390 × 5 locale × 3 route scrollWidth 390 / 0 溢出 / `.heatmap-heading` **150px 全等** / `.mobile-list-header` 26px 全等 / H1 零 ellipsis；CJK 最細 10px；hover preview h3 clamp 兩行完整（ch 51 ≥ 2×25.3）、muted / dd `break-word`；`tsc` 0、font-contract PASS、title-width PASS。**點樣反轉**：`git revert` 呢粒（純 CSS + docs + test 表，冇 binary） | ✅ 已落 + live（2026-08-17 push 345f8555 12:41Z；live CSS chunk 12:42Z 已有 `--w-heading`、`.heatmap-total-cap{…font-weight:var(--w-quiet)}` + `--w-quiet:500`、`h1,h2,h3{font-weight:var(--w-heading)}`；12:56Z 再對一次同一組 marker。注意 minified CSS 保留 `var(--w-*)`，唔會解成 500，probe 要對 token 名） |
| **fix-heatmap-align** | heatmap 色塊「挨左挨右、間距唔勻」修正（owner 2026-08-17 晚：「save 低嗰張熱力圖好過喺 website 見到嗰張」）。**根因唔喺 treemap，喺 render**：`heatmapTreemapLayout` 出浮點 x/y/w/h，之前每格各自 `+ gap/2`（1.5px），每條邊都落喺半粒 device px 上，Chrome 逐個 absolutely-positioned box 獨立 snap → 名義 3px 嘅 gap 實際 render 成 2 / 3 / 4px（1440 有 18% gap 唔啱、1920 32%；frame 四邊 L/T 1.5px vs R 1.14 / B 1.22）。**修法**：新 `lib/pixel-snap.ts` 做 device-px 格嘅唯一契約 —— `snapTileBox()` 釘**邊界線**（唔係逐格）落 device px 整數格，`gap` 拆做 `p = floor(g/2)`（左／上）+ `q = g − p`（右／下），相鄰兩格共用同一條線 → 任何 dpr 之下 gap 一律 exactly `round(gap × dpr)` 粒 device px；treemap「面積 ∝ 市值」算法一個字冇郁。另有 `snapCardBox()`（卡圖置中偏移整數化，唔會左右差半粒）同 `snapFrameSize()`（floor，唔准 round 出界）。`heatmap.tsx` 同 `heatmap-tiles-board.tsx`（`/tune`）同一套。**順手修第二粒真 bug**：`.tile-move` 淡底板本來係 CSS `color-mix(in srgb, var(--frame-up/--frame-down) 34%)`，但紅升模式係 JS 對調 `colors.up/down`、CSS token **唔會**對調 → 紅色 tile 頂住綠色底板（`/tune` 自訂色一樣中）。改為 `tileStyle().plate`（同 tile 同一隻 hex 34%）inline 落 label，CSS 兩條規則刪走；`share-image.ts` 改食 `st.plate` 唔再自己計；死 code `TILE_CARD_STYLE` 刪走（幾何一早喺 `.tile-card`）。**驗收**（DOM 幾何，`temp/fe05/desktop-fill/board_measure.py` + `board_plate_check.py`）：1440 dpr1 由 **396/400 條邊唔喺 device 格、42 個 gap 唔係 3px** → **0/400、gap 全部 exactly 3.0000（std 0）**；1920 由 399/400 + 74 個 off → 0/400 + 0 off；dpr2 → 0/400、gap 全部 6.0000；dpr1.25 剩 ≤ 0.023 device px 殘差（LayoutUnit 1/64 量化，= 1.6% 粒 pixel，肉眼冇）；手機 390 dpr3 gap 9–9.047；plate 色桌面紅升 99/99 + `/tune` 100/100 同 tile 同色；`tsc` 0、eslint 0 error。**第二版（絕對格，同日 push `99ee19fa` 之後）**：出街後量 live 先發現 tile 之間齊晒，但 frame 最外圈仲差一粒 —— render 出嚟 1440 係 `左2 上2 右3 下3`、1920 `左2 上2 右2 下3`、1440@2x `左3 上3 右4 下3`。因為 Chrome snap 嘅係**絕對**座標，而 frame 自己個 origin 係浮點（1440 實測 `left 43.1875 / width 1338.625`），淨係喺 frame 本地座標 round，右／下邊 round 落絕對格就多一粒。`snapFrameSize` → `snapFrameGrid(w, h, dpr, absLeft, absTop)`，回埋 `originX/originY`（= 由 frame 邊移到最近嗰條 device 線），`snapTileBox` 改收成個 grid；量度改用 `getBoundingClientRect()`（`.heatmap-frame` `border:0` 冇 padding，同 contentRect 一樣）。修完四邊全等：1440 `2/2/2/2`、1920 `2/2/2/2`、1440@2x `3/3/3/3`、390@3x `5/5/5/5`，gap 依然 231 / 220 條全部 3px、`cardOffMax` 0。**`fracStyleCount` 由 0 變 100 係預期**：inline style 而家帶住 sub-pixel origin 偏移（例如 1.8125），**要睇嘅係 render 出嚟嘅絕對格**，唔係 CSS 值靚唔靚。⚠️ `absTop` 係 viewport 座標會跟 scroll 變，ResizeObserver 唔會因為 scroll 而 fire → scroll 完 `originY` 可能過時；**gap 唔受影響**（兩個整數之差，加同一個偏移 round 完不變），最多最外圈上／下飄 ±1 device px，唔值得為咗佢喺 scroll handler setState 重排 100 格。share PNG 1440 出 2774×1457（floor 改 round，之前 2772×1455）。**⚠️ 像素掃描器會呃你**：只數「純底色」行會每個 gap 少算 1–2 行（Chrome 邊緣 AA 溝色，實測綠 `(27,113,78)` 同底 `(247,247,245)` 中間有 `(116,167,145)`）—— 信 DOM `getBoundingClientRect × dpr` 嘅整數性，唔好信數 pixel。新 gate `scripts/test-fe-pixel-snap.mjs`（dpr 1/1.25/1.5/2/3 × gap 0–5，種返 `x1 = R - q - 1` 證實會 fail）。share PNG 唔行呢套（canvas 自己 scale 2–3.5），只係 1440 出圖由 2774 → 2772 闊（frame 改 floor）。**點樣反轉**：`git revert` 呢粒 | ✅ 已出街（2026-08-17：`99ee19fa` tile gap + `b56ab40e` frame 絕對格；live 量到 1440 / 1920 四邊 2/2/2/2、gap 全部 3px）|
| **fe05(cjk)** | per-`:lang()` CJK 字體 stack + CJK 排版覆蓋 + 卡名 `lang` 屬性（§1.3.3；2026-08-17 投訴嘅第三粒，收尾）：`--font-sans` 拆做 `--f-latin` / `--f-jp` / `--f-tc` / `--f-sc` / `--f-kr`，四條 `[lang]:lang(ja|zh-Hant|zh-Hans|ko)` 各自重排 —— **每條都要再寫一次 `font-family`**（`body{font-family:var(--font-sans)}` 只 compute 一次，後代繼承嘅係結果唔係個 var）。CJK token block：`--fs-nano/micro/th` 10/11/11px、`--lh-display/clamp2/clamp2m/copy` 1.25/1.35/1.4/1.75、`--clip-pad` 0.24em、`--track-*` 清零、`--track-kicker` 0.05em；`--lh-hero` 同 `--track-hero` **唔郁**（郁咗偷 heatmap 高度／H1 出「…」）。另加 `:lang(ko) word-break: keep-all`、ja/zh `line-break: strict`、六條大寫 label 收 `--track-kicker`，同**明寫嘅 `[lang="en"]` 斷行解除規則** —— 元素自己標 `lang="en"` **唔會**自動解除（keep-all 由祖先繼承落嚟、`line-break: strict` 係按 tag/class 直接命中 `<h1>`／`.mobile-card-name`），specificity 打和靠排喺後面贏。卡名加 `displayCardNameLang()` / `cardNameLangAttr()`（Han unification：日文卡名喺中文頁本來會攞錯 `直`／`骨`／`画` 字形）。ja 文案和欧混植補到晒**四個** live 文案檔（`i18n.ts`、`site-copy.ts`、`hub-copy.ts` 63 處、`related-cards.ts` 12 處 —— 之前只做咗頭兩個，後兩個漏咗成個月冇人發現，因為當時得一句文冇 call site）。`cap-ticker` 單位由目標值一次過決定（新 `lib/ticker-start.ts`：`tickerStart()` + `tickerEase()` 夾 `[0,1]`），修返兩個真 glitch：第一 frame 負進度跌穿起點（實測 `/` en/USD 出過 `$993.75M`）、target 貼住單位頂（`$999.99M`）時跌價竟然由 `$1M` 向上滾。**新閘**：`test-fe-lang-attr.mjs`（`htmlLang()`×5、LangScript 真 `eval`×10、header `localeLang`、122 個 tsx `lang=` 屬性、卡名 5×5 分支、`[lang="en"]` 解除規則、4 個 ja 文案檔和欧混植）、`test-fe-ticker-unit.mjs`（900 paths × 101 樣本 + 298 rAF paths + **4800 單位邊緣 paths**，方向係**無條件**斷言 —— 舊寫法俾 `roomAbove` 守住，正好 skip 咗出事嗰個 case）、`test-fe-font-contract.mjs` 加「`:root` 有默認 `--font-sans`」+「`body` 真係 call 佢」+ 全 CSS `letter-spacing` 掃描（>0.04em 一定要喺 CJK 收編名單入面）。六條新檢查逐條種返 bug 證實會紅（`FAILED TO FIRE: none`）。**實測**（dev :3901，5 locale × 390/1280 × `/` `/one-piece` `/card/…`，`temp/fe05/review-webfont/verify-c3.json` 0 fail）：CDP `CSS.getPlatformFontsForNode` ja → Yu Gothic、zh-Hant → PingFang TC、zh-Hans → Microsoft YaHei、ko → Malgun，**zh 頁 H1 零 Yu Gothic**；`.heatmap-heading` 390 五語言 spread **0.02px**（clip-pad token 對抵啱）；1280 spread 1.54px 係版面結構本身（live C2 同 dev C3 五個數逐位相同，見 §1.3.3 `--lh-hero` 行）；cap-ticker 五個 locale 全程單位唔變（`$B` / `￥億` / `₩조` / `$億` / `¥亿`，35–50 個取樣值）；`npx tsc --noEmit -p apps/web` 0；FE test 全綠（`npm test` 52/54，紅嗰 2 條係 python data-lane 搵唔到 `data/runtime/config/backend.env`，呢個 FE worktree 本來就冇，同 C3 無關）。**點樣反轉**：`git revert` 呢粒 —— CJK 會退返 C2 嘅單一 stack（Windows 上 zh 頁重新見日文字形）、卡名 `lang` 屬性同 ja 半形空格一齊退，Latin Inter（C1）唔受影響 | ✅ 已落 + live（2026-08-17 `14:59:35Z`）。**⚠️ 出過一次 deploy 事故，值得記住**：`895f9f76` `14:28:28Z` push 咗上 `origin/main`、subject 有 `[deploy]`，但 GitHub hook `658470027` **完全冇派** —— poll 足 15 分鐘零變化，而 hook 本身 `active: true` / `last_response 200`、`cf-cache-status: DYNAMIC`（唔關 Cloudflare 事）、前 5 粒 push 逐粒 2 秒內對得返 1:1。即係 **GitHub 側漏派**，唔係 `next build` 炸，本機點驗都修唔到。C4（`215a5e00`，`14:58:05Z` push）一 push delivery 就 1.8 秒後到、**90 秒**上街，順手帶埋 C3 —— 證實 webhook 路徑健康，嗰次係一次性。分辨「冇派」vs「build 炸」嘅指令已寫入 [FE05_ROLLBACK.md](../../docs/FE05_ROLLBACK.md) 第 8 步。**live 實測**：CSS chunk `--f-jp`/`--f-tc`/`--f-sc`/`--f-kr`/`--track-hero`/`lang(ja)`/`keep-all` 全中（83,731 → 85,603 B）；`/?lang=ja` SSR 真係日文（有「時価総額」）而且和欧混植乾淨 —— **case-sensitive** `PSA10` 0 / `PSA 10` 48、`未開封BOX` 0、`BOX市場` 0、`トップ100` 0 / `トップ 100` 3 |
| **fe05(og-share)** | OG 圖同分享 PNG 對齊網頁字體（§1.3.4；2026-08-17 收尾第四粒）。**兩個出口本來都唔行 Inter**：① `/api/og/card/[id]` 個 `ImageResponse` 冇 `fonts`，satori 靜靜用 `next/dist/compiled/@vercel/og/Geist-Regular.ttf`；② `share-image.ts` 硬寫一條系統 stack（`-apple-system, … "Segoe UI", …`），canvas 揀咩字唔會報錯 —— 即係網頁 Inter、OG Geist、分享圖 Segoe UI，**三個出口三隻字**。修法：新 `public/fonts/og/{Inter-Regular,Inter-SemiBold,Inter-Bold}.ttf` + `OFL.txt`（Google Fonts CSS API v2 + legacy UA 攞 static TTF，Inter v20 2026-08-17；**satori 唔食 woff2、唔食 variable font**，所以唔可以重用 C1 個 woff2），module 級 `loadOgFonts()` memo + 兩路 `existsSync`（`public/fonts/og` / `apps/web/public/fonts/og`，沿用 route 本身嘅 standalone pattern）+ **fail-open**（load 唔到就 `console.warn` 退返 bundled font，唔好為咗字體令 OG 圖 500）。satori 唔會合成字重，所以只 register 400/600/700，layout 全部收埋落呢三個數：metric label `600`/ls 1.8、metric value **700 → 600**、TextOnly kicker `600`/ls **4 → 3.4**、rank pill **700 → 600**、set name 明寫 `400`、卡名留 `700`。`share-image.ts` 加 `shareFont()` 讀 `getComputedStyle(document.body).fontFamily`（**唔可以硬寫 `"Inter"`** —— dev 個 family 叫 `inter`、production build 變 `__Inter_xxxx`，硬寫喺 prod 一定 miss 然後靜靜跌返系統字），SSR 冇 `document` 就退返系統 stack；四條 font string 同 tile label 全部搬到 `await document.fonts.ready` **之後**先 build。**新閘**：`test-fe-font-contract.mjs` ⑥ —— 由 `route.tsx` 有冇 reference `public/fonts/og` **反推**檔案必須齊（唔准寫 `if (existsSync(dir))`，否則人哋 `rm -rf` 個 folder 成段 check 靜靜消失、CI 照綠），三個 TTF 逐個對 sha256（抄喺 route.tsx 檔頭）+ sfnt magic `00010000`，再掃全 route 嘅 `fontWeight:` 一定 ⊆ {400,600,700}。五條種返 bug 逐條證實會紅、加一個負控制（route 唔再 reference → 應該仍然綠）：`FAILED TO FIRE: none`。**實測**（dev :3901）：`verify-c4-share.mjs` **PASS** —— body computed family 唔係系統字、`document.fonts.check` 認得，同一句字 computed **400.96px** vs 舊 fallback stack **384.17px**（差 16.79px = 改動有實效），真撳「分享圖片」掣攔到 download，出圖 2496×1502 人肉睇過（tile 標籤全部入 tile、legend 冇撞）；OG `x-og-art: 1`、200、430,260 B、warm 141–188 ms（`loadOgFonts` memo，只有第一次讀盤）；最長卡名 113 字 clamp 4 行有 `…`、最長 set 名 67 字 clamp 一行，冇撞底邊；`npx tsc --noEmit -p apps/web` 0；`npm test` 52/54（紅嗰 2 條係 python data-lane 搵唔到 `data/runtime/config/backend.env`，呢個 FE worktree 本來就冇呢個目錄，同 C4 無關）。**點樣反轉**：`git revert` 呢粒 —— OG 退返 Geist、分享圖退返 Segoe UI，TTF 留 repo 無害；網頁本身（C1）唔受影響 | ✅ 已落 + live（2026-08-17 push `215a5e00` `14:58:05Z`，`14:59:35Z` 上街）。live 認法用內容 marker（`X-CARDZ-Build` 永遠 `local`）：`GET /fonts/og/Inter-Regular.ttf` → **200 / 324,820 B / sha256 `1b08e7fc…`**（對得返 route.tsx 檔頭）；`GET /api/og/card/<id>` → 200 / `image/png` / `x-og-art: 1` / 430,260 B / 306–696 ms；張 live PNG 自己 Read 過，同本機出嗰張逐 byte 一樣（430,260 B），字係 Inter |
| **fe05(assets)** | 圖片面收尾（2026-08-17 review 第五粒，唔關字體事但同一輪執）。**① 刪 7 個孤兒 PNG**（`apps/web/public/brand/`，合計 **206,227 B**）：`icon-white-seam.png`、`logo-fe02-cardzmarketcap.png`、`logo-horizontal-{dark,light}-{256,512}.png`、`og-dark.png`。⚠️ **plan 嗰張表寫錯咗邊個係孤兒** —— 佢寫 `icon-transparent.png` 冇人用，實測佢有 1 個 reference（`app/manifest.ts:7` 一句 provenance comment），真正零 reference 嗰個係 byte-identical 嘅 `icon-white-seam.png`（兩個都係 29,871 B）。刪咗零引用嗰個，兩個都刪就會斷 manifest 個註釋鏈。`og-dark.png` 同樣零引用（OG route 淨係讀 `og-light.png`）。**② header logo 出 2× 細版**：`.brand-logo` 最大只顯示 50px 高（≥1440；default 46、手機 34），但之前派緊 879×380 原圖 = **8.8× pixel**。新 `logo-cardz-marketcap{,-dark}-h100.png`（sharp `resize({height:100, fit:'inside', kernel:'lanczos3'}) + palette png`）231×100 **18,429 → 9,338 B**、247×100 **9,775 → 7,592 B**，`width`/`height` 明寫落 `<img>`。**原圖唔准刪** —— OG route（sharp）同 `share-image.ts`（2496px canvas）要全解析度。**③ `box-image.tsx` 補 intrinsic dims**（之前完全冇 → 圖一 decode 就由 0×0 撐開，`.detail-art` 雖然定高、盒外唔郁，但盒**內**張圖照計 layout-shift）：`data/public/box-subset.json` **307/307** 個 product 都有 width/height，缺就唔填（type 本來就係 optional，**唔知就唔准填**假數）。`site-search.tsx` 縮圖唔補 dims（`.ranking-thumb` CSS 釘死 42×60 / 手機 52×72，補咗都係多餘），改補 `loading="lazy"` + `decoding="async"` —— 搜尋 sheet 一開就 40 幾張圖，呢個先係實效。**④ alt 本地化**：`card-detail.tsx` 由 EN `officialName` → `title`（= `displayCardName(card, locale, …)`，同 H1 同一個字串）；`heatmap-tiles-board.tsx` alt / aria-label 加 `displayCardName(card, locale ?? "en")`，`tune-lab.tsx` 餵 `locale` 落去（呢個 board 淨係 `/tune` 用，用戶面嗰個 `heatmap.tsx` 一早已經本地化）。**⑤ `.explore-kbd` `font:` shorthand 拆返逐個屬性** —— shorthand 會**重設**冇寫嘅 `font-*` 子屬性，而且入面硬寫一條 mono stack，繞過 `--font-mono`（Windows 嘅 Cascadia / Consolas 係 C1 先加入去嗰條）。順手改返 `.gitattributes` 字體嗰段註釋：原本寫「LFS pointer 喺 Docker build 就係 130 bytes 文字檔」係**講錯咗** —— 呢個 repo 個 deploy 其實有 smudge（`public/brand/*.png` 全部係 LFS pointer，而 live OG 圖出到正常 logo 就係證據），字體唔行 LFS 係「唔值得為咗 380 kB 冒個無聲 fallback 風險」，唔係「一定唔得」。**驗收**（dev :3901，`temp/fe05/review-webfont/verify-c5.mjs`，`fails: []`）：header `currentSrc` 係 `-h100`、`naturalHeight` 100、`width/height` 屬性同真實尺寸一致、natural ≥ 2× rendered（1280 度 rendered 50px）；`.explore-kbd` computed family 第一項 == `--font-mono` 第一項（`"SF Mono"`）、11px / 600；box 詳情圖有 `width=1600 height=1600`；`/card/…?lang=ja` 圖 alt == H1（「ゴッホのピカチュウ グレーフェルト帽 085/SVP」）；成個 session **零個**請求打去嗰 7 個刪咗嘅 PNG。`npx tsc --noEmit -p apps/web` 0；`npm test` 52/54（紅嗰 2 條係 python data-lane 搵唔到 `data/runtime/config/backend.env`，呢個 FE worktree 本來就冇呢個目錄）。**點樣反轉**：`git revert` 呢粒 —— 7 個孤兒 PNG 會返嚟（無害，本來就冇人用）、header 退返派原圖、box 圖冇 dims、alt 退返英文 | ✅ 已落 |
| **fe05(ranking-feed)** | 榜單分頁改做**接落去**（owner 2026-08-18：「每次撳『展示更多』或者換排列數量，你都彈我返去首頁上面，真係好離譜⋯⋯我想繼續碌落去、繼續掃」＋「掃到最盡會有阻尼彈一彈，夾硬再拉又 load 咗個全新版面出嚟」）。**兩粒根因**：① App Router 個 `<Link>` **預設 `scroll={true}`**，soft nav 完 `window.scrollTo(0,0)` —— 榜喺成版最底，所以每撳一下就由榜尾彈返 hero 頂（`use-market-settings.ts:308` 個 `router.replace` 一早已經 `scroll:false`，所以換 sort／幣值從來唔跳，**得 pager 呢幾條 link 中招**）；② `?page=` 係**換頁**唔係接落去，碌到底冇嘢再嚟就見到瀏覽器 rubber-band，撳完成個 list 由 #501 重畫 = 「全新版面」。**修法**：`ranking-pager.tsx` 轉 `"use client"`，全部 link `scroll={false}` + 自己 `scrollIntoView('#market-ranking')`（**唔係唔郁 scroll** —— 換頁換數量本來就要返榜頂，只係唔應該返成個網站嘅頂）；「展示更多」`preventDefault` 改行 `onLoadMore`，`market-page.tsx` 揸一個 `{sig, rows, nextPage, failed}` feed state，新 `lib/ranking-feed.ts` 打 `/api/v1/market?scope&page&pageSize`（**唔可以用 `/api/v1/catalog`**，嗰個冇 price/marketcap/sparkline，接出嚟會係半死行）concat 落 SSR 嗰批下面；pager 上面加個 1px sentinel，`IntersectionObserver` 提早攞下一批。**server → client 只准過純資料**：`hrefFor()` 喺 `ranking-surface.tsx` 行晒，落嚟已經係 `RankingPagerData`（一堆現成 href + label），function 過唔到界。**四個陷阱，逐個踩過**：⓵ side effect 唔准擺喺 `setFeed` updater 入面（StrictMode 行兩次 = 一 render 兩個 request），改用 `inFlight` ref；⓶ ref **唔准喺 render 期間寫**（`react-hooks` 係 error 級，`npm run lint` 由 0 error 變 2），要搬入 `useEffect`；⓷ sentinel rootMargin 一開始淨係寫下邊 800px，**用戶一嘢 fling 落底就跳過咗佢**（實測 sentinel 停喺 viewport 上面 167px，observer 由頭到尾冇 fire 過、rows 卡死 100），要補上邊 1200px；⓸ IntersectionObserver **只喺狀態轉變先 call callback** —— 企咗喺頁尾唔郁，接完一批之後 sentinel 仍然「交叉緊」＝冇轉變＝下一批永遠唔嚟（卡死 200），所以每次 load 完 `unobserve` + `observe` 攞返個 initial notification。另外 `nextHref` 係 SSR 嗰刻算，接到尾都仲係非 null，唔另外數 `hasMore` 就會留低一個撳極都冇反應嘅掣；`1/3` 亦改成 `1–3/17` 講返實情。⓹ 返榜頂要**等 nav commit 咗**先做（`page|pageSize` 一換先 scroll），唔可以喺 `onClick` 即刻行 —— 站方係 `scroll-behavior: smooth`，由榜尾行返榜頂成 1.4 秒動畫，喺 list 由 800 行縮返 200 行之前起步就係跑緊個舊版面；個旗仲要擺 **module level**（soft nav 會令 component 重建，擺 ref 果版三個 viewport 全部唔 scroll，實測）。上限 `AUTO_APPEND_ROW_CAP = 800` 行（= size 100 自動接足 7 次），再落去要用戶自己撳。**冇 JS 一樣行**：全部仍然係真 `<a href>`，append 只喺有 handler 嗰陣接管；`heatmapCards` 同 JSON-LD `listedCards` **照用 SSR 嗰 100 張**（接落去係用戶行為，寫落 structured data 就係同 crawler 講大話，熱力圖亦唔應該變 600 格）。**驗收**（`temp/fe05/review-webfont/verify-pager.mjs`，dev :3901，desktop 1280 / mobile 390 / ja 1280 三組全 PASS）：撳「展示更多」→ `scrollY` 9731 → 18631（**唔係 0**）、rows 200 → 300、URL 一個字冇變；碌到 pager 附近未撳已經自動接咗（100 → 200）；碌到底自動接到 600–800 行；換 `size=200` → URL 真係帶 `size=200` 而 `#market-ranking` 落喺 viewport 頂（top 68 / 92px，唔係 `scrollY 0`）。`tsc` 0、`npm run lint` 0 error（8 warning = 基線）、`npm test` 52/54（紅嗰 2 條係 python data-lane 搵唔到 `data/runtime/config/backend.env`，呢個 FE worktree 本來就冇）。**點樣反轉**：`git revert` 呢粒 —— pager 退返純 server component 換頁版，`lib/ranking-feed.ts` 變孤兒（無害，可以一齊刪） | ✅ 已落 |
| **fe05(tile-corner)** | 熱力圖四隻角嘅格跟返 frame 圓角（owner 2026-08-18：「熱力圖四個角頭，指咗入去，入邊嗰條白色／銀色線食咗個角頭 —— 應該跟返個弧形圓形嘅邊收返好條線」）。**根因**：`.heatmap-frame` 係 `overflow:hidden` + `--section-radius`（24px，≤ 手機斷點 18px），但 tile 一律 `border-radius: 4px` 方角，四隻角嗰格就俾 frame 斜斜切走一隻角 —— hover 嗰條 `inset 0 0 0 2px` 白線行到角位就斷開（放大圖睇得好清楚）。**修法**：`heatmap.tsx` 個 layout effect 按 treemap 座標（`x/y` 貼 0 或者 `frameW/frameH`，0.5px 容差）落 `data-corner="tl|tr|bl|br"`（一格可以食兩隻角，所以係 token list 用 `~=` 揀），CSS 俾嗰隻角 `calc(var(--heatmap-frame-radius) - 2px)`（減 2 = tile 由 frame 邊縮入咗半個 gap，實測 1.6px，扣返先同 frame 個弧同心）。**`data-corner` 行 DOM 直寫唔加 props** —— `HeatmapTile` 係 `memo`，同 `role`/`tabindex` 一樣嘅既有 pattern（heat-perf 合約）。順手將 frame 圓角換成 `--heatmap-frame-radius` var：手機斷點本來直接改 `border-radius: 18px`，tile 果邊讀 `--section-radius` 就會對唔上（**呢個真係被 gate 捉到**：`verify-heatmap-corner.mjs` 390 那組報 `radius 22 ≠ frame 18 − 2` ×4，改咗 token 先綠）。**驗收**（`temp/fe05/review-webfont/verify-heatmap-corner.mjs`，1280 dark + 390 light 兩組 PASS）：四隻角都真係搵到貼邊嗰格、`data-corner` 標啱、對應嗰隻角 radius = frame − 2（22 / 16），其餘三隻角仍然 4px，中間唔貼邊嗰格四隻角全部 4px 兼冇 `data-corner`（防 selector 掃咗全場）；八張 hover 住嘅放大圖自己 Read 過，白線由角位斷開變成順住個弧收埋。**點樣反轉**：`git revert` 呢粒 —— 四隻角退返方角俾 frame 切走，其餘完全唔受影響 | ✅ 已落 |
| **fe05(logo-svg)** | wordmark 由烘死點陣換 SVG（C5 只 flag 咗、owner 2026-08-18 拍板做）。新 `public/brand/logo-cardz-marketcap{,-dark}.svg`（light 59,294 B / dark 12,501 B），落 **OG route 兩路**（TextOnly `<img width=280 height=121>` / Art `200×86`）同 `share-image.ts` 兩個 skin。**satori 一定要 `data:image/svg+xml;base64,`** —— `;charset=utf-8,` + `encodeURIComponent` 會喺 `btoa` 度掟 `InvalidCharacterError`。**實測數（dev :3901 + 真 @vercel/og）**：OG PNG bytes 28,214 → 27,644（**−570 B**）、OG response 584,347 → 583,794（−553 B）；ink bbox SVG 198×84 vs PNG 196×83（bottom y=584 兩邊一樣）；render 時間用**交替臂 N=25** harness（唔係先跑完一組再跑另一組 —— 第二臂已經熱身，會偏）量得 SVG **22.7ms** vs PNG **26.2ms**（−3.5ms）；share canvas 四組只有 dark/375 差 1px（275 vs 276），零 canvas taint（8 個 `getImageData` probe）。**header logo 仍然留 PNG**：brotli q11 之下 light 版 `h100.png` 9,098 B vs `.svg` 17,016 B（SVG **大 7,918 B**），得 dark 版 SVG 細（4,994 vs 7,304）—— 換一半冇意思。新閘 `scripts/test-fe-brand-logo-svg.mjs`。**兩段舊註釋係錯嘅，已改正**（重現唔到，數字寫喺註釋入面）：① 「satori 會 letterbox」——`width="900"` 而 viewBox 唔郁、甚至剝走 width/height，ink 都係 198×84 @(957,501)、inkPx 6435 **逐個數一樣**（`<img>` 已經俾咗 explicit box，resvg 唔理 SVG 自己嗰兩個屬性）；② 「Chrome 冇 width/height 就當 300×150 → ratio 爛 → oneRow 跳」——實際 300×130（light）/ 300×122（dark），**ratio 保住**，drawn `logoW` 只差 1px。真正會出事嘅係反方向：`width` 改咗而 viewBox 唔郁（900×419）→ `logoW` 259 → **241**，細咗 ~7%，而呢個**只影響 share-image 條 Chrome canvas 路**，唔影響 OG。**覆核捉到 5 條假閘**：4 條種 fault 照綠（OG 改指 `-dark` skin＝白字畫落淺底隱形、share-image 兩個 skin 掉轉、`<img>` 200×86 縮做 100×43、`</svg>` 前另開第二個 `<defs>` 藏死 path），已補真 assert 並逐條種返證紅（`temp/fe05/review-webfont/fault-drill-c6b.mjs`：X3/X4/X5/X6 exit=1、restore sha 對得返、finalExit=0）。**未補**：`share-image.ts:246` `logoW = round(logoH × w/h)` 呢條算式本身仲冇 assert。**點樣反轉**：`git revert` 呢粒 —— OG／分享圖退返 PNG wordmark，兩個 SVG 留 repo 無害 | ✅ 已落 |
| **fe05(deploy-watch)** | push 完唔准走人（AGENTS 規矩 16）+ **改正 2026-08-17 個錯判斷**。新 `scripts/deploy_watch.ps1`：push 完跑佢，exit code 就係診斷（`0` 上街驗到／`2` delivery 2xx 但 live 冇轉＝AWS 側／`3` GitHub 未派／`4` 接收端非 2xx／`5` 未量到就已經錯）。**根因改正**：`GET /hooks/{id}/deliveries` **只列已經派咗嘅**，未派出去嘅 event 喺個列表係完全隱形 —— 所以「查唔到 delivery」同「GitHub 冇收過」外觀一模一樣，2026-08-17 就係咁判錯。真假分辨靠 delivery 個 **`guid`**（UUIDv1，頭 60 bit 係 event 產生時間），`delivered_at − guid時間` 先係真 lag。**實測**：`895f9f76`（`fe05(cjk)`）guid 話 GitHub `14:28:28.722Z` 就已經知道，`14:59:24.105Z` 先派 —— 遲 **1855.4 秒（30 分 55 秒）**、`OK 200`、`redelivery=false`、`throttled_at=null`。本機 reflog 56 粒 push **56/56 全部對得返 delivery，一件真漏都冇**；72 小時 82 件 median **1.2s**、p90 **1.4s**。即係嗰次唔係漏派，係一件遲咗 500 倍。**順手記低一個坑**：`gh api --jq` 一 `tostring`／字串內插就整爛 19 位 delivery id（`…839744` → `…840000`，gh 2.86.0 實測），要 redeliver 就唔可以經字串。**點樣反轉**：刪 `scripts/deploy_watch.ps1` + revert AGENTS 規矩 16（純工具，冇 runtime 影響） | ✅ 已落 |
| **fe05(kiosk)** | 熱力圖 kiosk 全屏（owner 2026-08-18：「鋪頭店主想推廣我哋嘅 index⋯⋯一個掣一撳落去就自動適應屏幕，橫嘅打橫佔據晒、直嘅打直佔據晒⋯⋯要 show 翻個公司 logo」）。**唔使寫新演算法**：`heatmapTreemapLayout()` 收 frame 實際闊高 + ResizeObserver，任何長寬比佢一早自己重排 —— 剩返「換個容器」。新 `app/styles/heatmap-kiosk.css`（heatmap.tsx 自己 import，同 heatmap-tune.css 同一 pattern）+ `heatmap.tsx` kiosk state / toggle / logo overlay + i18n `heatmap.fullscreen` / `exitFullscreen` 五語言。**兩條路共用同一個 `[data-kiosk="true"]`**：native `requestFullscreen()`，同 CSS 假全屏（`html.heatmap-kiosk-fallback`）—— **iPhone Safari 冇 `Element.requestFullscreen`**，唔做呢條路個功能喺一大堆真店主部機直情係死嘅。**三個坑**：① `html { scrollbar-gutter: stable }`（globals.css :1476）令 `position: fixed` 嘅 containing block 少 15px，實測 frame 1265 vs innerWidth 1280 —— `overflow:hidden` **同** `scrollbar-gutter:auto` 兩句缺一不可（淨落 overflow 量返出嚟一樣係 1265）；② 退出嗰下 `focus()` 預設會 scroll-into-view，撞上 `html { scroll-behavior: smooth }` 就變一段動畫，**倒轉頭剷走**上一行啱啱還原好嘅 scroll 位（實測 260 → 247 → 72 → 26 → **0**），一定要 `focus({ preventScroll: true })`；③ 假全屏用 `visibility:hidden` 唔用 `display:none`（800 行榜 display:none 會 reflow 兩次兼塌 scroll 高度，還原就唔準）。**收 chrome（owner 同日再落）**：「都全圖 mode 就唔好要 top100 同升跌 仲有日子，好影響睇」→ `.heatmap-title` / `.heatmap-footer` / `.period-selector` 三舊 `display:none`。⚠️ 收嘅係 **`.heatmap-title`，唔係 `.heatmap-heading`** —— `{controls}`（入面就係退出掣）係 `.heatmap-heading` 第二個 child，落錯個 class 實測個掣即刻 `w:0 h:0`，入咗 kiosk 就永遠出唔返、店主要 reload 成版。收埋之後 heading 剩一個 child，`justify-content: space-between` 會令掣跌返最左 → `.heatmap-controls { margin-left: auto }`（**唔可以用 `justify-content: flex-end`**：窄機 heading 轉 column，flex-end 喺 column 係「推去底」，實測 720×1280 個掣仲企喺 x=16）。**驗收**（`temp/fe05/review-webfont/verify-kiosk.mjs`，1280×720 / 720×1280 × light/dark + 兩條 fallback，6 case PASS）：frame == viewport ±2；tile 覆蓋率 93.75%（**門檻寫 93% 唔係 95%** —— gap 3px 之下天花板本身就係 ~93.9%，寫 95% 就係一條由頭到尾一定紅嘅假閘，「真係鋪滿」改由 fillScan ≥99.9% + union bbox ≤8px 守）；四角 radius **exactly 0**；三舊 chrome computed `display` 全部 `none`（**唔准 `absent`** —— absent = selector 打錯字，睇落同「收得好」一模一樣）；退出掣 44×44 @ x=1220/1280、660/720（兩邊各剩 16px）；section accessible name 實測仍然係 `Top 100 heatmap`（`aria-labelledby` 引用 `display:none` 嘅 h1 照計文字 —— spec 咁講，但實測過先收貨，用 `locator.ariaSnapshot()`，`page.accessibility.snapshot` 喺 Playwright 1.62 已經拆咗）；退出 scroll 260 → 260（容差 2）。**reduced-motion 之下退出會飄 25px**（260 → 285），容差放寬到 30 並寫明未收殮：hook 咗 `scrollTo`/`scroll`/`scrollBy`/`scrollIntoView`/`HTMLElement.focus` 之後兩個 motion mode **JS call 逐個一樣**（都係我哋自己嗰兩句），scroll-snap 亦排除（`scrollSnapType: none`）—— 即係引擎層，**根因未查到**。**新閘逐條種 fault 證紅**：`fault-drill-c8-chrome.mjs` C（revert 收 title）/ D（selector 改成 `.heatmap-heading`）/ E（刪 `margin-left:auto`）三條 exit=1 兼命中預期嗰句、restore sha 對得返、finalExit=0；另有 FAULT-A（拆 `preventScroll`）→ 260→0、FAULT-B（刪 `focus()`）→ activeElement 空。⚠️ **`heatmap.tsx` 喺 eslint `globalIgnores` 入面**（§8 WS2 欠單 7），呢粒改動 **零 lint 覆蓋**。**點樣反轉**：`git revert` 呢粒 —— 掣同全屏一齊消失，非 kiosk 版面一個字冇郁 | ✅ 已落 |
| WS5 | OG 圖 v2（卡圖入圖，satori 讀唔到 WebP → 要解碼），fail-open 退返純文字版 | TODO |
| **kiosk 特效層** | kiosk 呼吸感／掃光／自動聚光巡遊／邊框脈動／入場 burst／底噪（owner 2026-08-18：「超誇張少少嘅呼吸感」＋「你到去邊張要飛去中間比大家睇到先得」＋「#3 太細，呢個係排行嚟」）。原型同實測喺 `temp/fe05/kiosk-fx/`（`kiosk-fx.css` / `kiosk-fx.js` / `kiosk-fx-notes.md` / `perf.json` / `throttle-ladder.json` / `reduce-proof.json`），**未入 `apps/web/`**。已知數：60Hz 之下 44 個 run median 全部 59.9fps、CLS 全部 0，所以未節流數字冇資訊量；**要睇 CPU 節流梯** —— 守得住 55fps 嘅最大倍數 baseline 5× / sweep 4× / breathe **2×**，`recalcStyleDuration` 10 秒 delta breathe **255ms**（tour 27 / edge 33 / sweep 48 / noise 0）。「細格唔派呼吸」試過：動畫由 99 條減到 74 條，3× 節流一樣 30.1fps ⇒ 樽頸係逐幀掃成塊板嗰個 style pass，唔係條數，**已 revert**。reduce sibling 17/17 PASS。 | ✅ 已落（見下一行） |
| **fe05(kiosk-fx)** | 上面嗰層原型正式入 `apps/web/`：新 `components/heatmap-kiosk-fx.tsx`（FLIP 巡遊 state machine + 資訊板 + 七件 overlay，`{kiosk && …}` mount，unmount 就係唯一清場時機）、`app/styles/heatmap-kiosk.css` 184 → 851 行、`heatmap.tsx` +101/−2（`--fx-i`/`--fx-r` 直寫落 tile DOM，唔加 props —— `HeatmapTile` 係 `memo`，同 `data-corner` 同一 pattern）。kill switch 走 URL query（`?kioskfx=breathe,tour`；空值 = 五個全熄），因為部機掛喺牆上冇 devtools。**owner 收貨後即刻報返兩件嘢，兩件都唔係我原本諗嗰個根因：** ① 「啱啱入去有一版⋯⋯啲卡全部走晒位」。我第一輪用 `getBoundingClientRect` 量 tile union，見到 0–100ms 左邊留 53.1px 右邊留 14.3px，判咗係「frame 已經撐到 1920 但 treemap 未重排」，跟住寫咗個 `data-kiosk-settling` 遮罩去遮嗰 200ms —— **判錯**。真相係 gBCR **分唔開「動畫播緊」同「版面排錯」**（transform 一齊計入去）；改用 `offsetLeft/offsetWidth`（transform 之前嘅 layout box）再量，**由 t=0 起四邊都係 2px，版面從來冇排錯過**。遮罩同埋為此加嘅 `ResizeObserver` + `flushSync` 全部拆咗，`heatmap.tsx` 嗰段量度 code 一個字冇郁。owner 見到嗰版係**入場動畫本身**：原本 `scale(0.62)` + `1.035` overshoot、delay 散喺 0…520ms、播 620ms ⇒ 成 **1.14 秒**之內 100 格細細粒散喺度，而頭 ~100ms 因為 `backwards` fill 令全部格 opacity 0，直情係**一塊白板**（0ms 截圖一片白）。改成 `scale(0.94) → none`、delay 0…**220ms**、dur **380ms**，**同埋 keyframe 唔再 fade opacity**（要淡就淡 frame 底色，唔好淡 tile —— 掛牆大電視閃一下白比郁一郁難睇好多）。② owner 覺得成塊板有浸白霧：`.heatmap-dim` 條射燈紗綁咗 `[data-kiosk-tour]`，但嗰個 attribute 係**模式**（`fly`|`hold`，mount 就派、拆 component 先拆），唔係「而家有格飛緊」⇒ 由入場第一秒起成塊板長期蓋住紗，連 5 秒 warmup 同每輪之間嗰 400ms gap 都照蓋。實測：t=16…3000ms `.heatmap-dim` opacity **1.00** 而 `.kiosk-stage` 係 **0×0 / opacity 0**（射燈着咗但冇嘢照）。改綁真身 `data-kiosk-star`（JS 揀中先派、落返地即拆）：`…[data-kiosk-tour]:has(.heatmap-tile[data-kiosk-star]) .heatmap-dim` **(0,7,0)**，檔尾 reduced-motion sibling 跟住由 (0,6,0) 升 **(0,8,0)**（唔升就輸畀啟用 rule = 寫咗等於冇寫，§3.4）。`:has()` 唔支援嘅瀏覽器會丟走成條 rule ⇒ 紗永遠唔着 = 冇射燈但塊板乾淨，degrade 方向啱。**驗收**（`temp/fe05/kiosk-fx-land/repro-enter-jump.mjs`，1920×1080 有頭 Chromium，撳真掣，17 個採樣點 0…11500ms）：判詞一 = 任何「frame 見得到但 layout box 左右／上下留白差 >4px」全綠；判詞二 = 任何「冇星但紗 >0.15」全綠 —— 實測紗 0–4800ms 熄、5400–9600ms 着、10400ms（gap）熄、11500ms（下一輪）再着，同飛卡完全同步。**兩條判詞逐條種 fault 證紅**：`FAULT=layout`（注入 `margin-left:-40px`）→ 左−38/右42、exit=1；`FAULT=haze`（把紗綁返 `[data-kiosk-tour]`，即係修之前嗰個寫法）→ 50…4800ms 紗=1.00、exit=1；乾淨跑 exit=0。另外飛行期間拆走 `.tile-card` 條 1px 白 hairline（`s` 實測 3.150 / 4.667 ⇒ 放大後 3.15px / 4.67px，配上 `object-fit:contain` 嘅左右留白望落係個白 slab 框住張卡），拆嗰陣**唔准用 `revert`/`unset`**（revert 退去 UA origin 三段一齊冇、unset = none），要逐段抄返 globals 頭兩段。**閘**：`tsc` 0；`npm run lint --workspace @cardz/web` 0 error / 8 warning（基線）；`npm test` 53/55（紅嗰 2 條係 python data-lane 搵唔到 `data/runtime/config/backend.env`，呢個 FE worktree 本來就冇）；`verify-kiosk.mjs` 六個 case PASS。**未收殮嘅債，唔當已修**：`heatmap.tsx` 同 `heatmap-kiosk-fx.tsx` 都喺 eslint `globalIgnores`，呢層嘢**零 lint 覆蓋**，全靠 tsc + 呢幾支腳本；breathe 喺 CPU 節流梯只守得住 **2×**（其餘特效 4–5×），店主部機弱過呢個就要用 `?kioskfx=` 熄；原型嗰份 `perf.json` 係喺一個 HMR 髒咗嘅 dev server 度量，數只可以當數量級。臨時預覽用嘅 `apps/web/public/__fx/` 已刪。**點樣反轉**：`git revert` 呢粒 —— `heatmap-kiosk-fx.tsx` 變孤兒、CSS 退返 C8 個 184 行版，kiosk 全屏本身照行 | ✅ 已落 |
| **fe05(rank-size)** | 榜單每頁數量掣 100 / 200 / 500 + 畫面行數封頂（owner 2026-08-18：「每頁展示嘅項目數量，俾人去揀 100、200 或者 500 個，唔好有一千個喇⋯⋯有陣時我碌下碌下落到去，原來 show 到成 800 個項目，部機就會 lag 機，我要 F5 refresh 一次先可以更新返」）。**「最多揀到嘅每頁數量」同「畫面最多幾多行」由今日起係同一個數**：`RANKING_ROW_CAP = Math.max(...RANKING_PAGE_SIZES)` —— **唔准寫死**，寫死嘅話下次有人加個 800 落選項，cap 就會靜靜變咗「揀到但顯示唔到」。`RANKING_PAGE_SIZES` 由 `[100,200,300,500,1000]` 收做 `[100,200,500]`。**300 唔准直接踢 404**：榜尾嗰排數量掣一直係真 `<a href="?size=300">`，出過街即係可能已經俾人 bookmark／爬蟲收咗 —— 開 `LEGACY_PAGE_SIZES = [300]`，parser 照收、**任何 UI 都唔再出**（呢個 list 只會縮唔會長）。**順手修一粒會俾 cap 揭出嚟嘅舊 bug**：`nextHref` 係 server 算 `page + 1`，但接咗 4 版落去之後真正「下一版」係 `page + 5` —— 撞到 cap 之後 `›` 就係唯一逃生口，指返已經睇過嗰版就等於死路。function 過唔到 server→client 界，所以改成派 `pageHrefs: string[]`（**成個榜每版一條**，size 100 × 1604 張 = 17 條短字串，payload 忽略得），client `nextHref = pageHrefs[lastLoadedPage]`。**舊 cap 兩個洞**：`AUTO_APPEND_ROW_CAP = 800` 只夾自動接、撳「展示更多」照過；而且判嘅係**接之前**（`loadedRows < cap`）—— size 500 之下 500 < 800 成立，接完就 1000。而家一律 `loadedRows + pageSize <= RANKING_ROW_CAP`，自動同手動同一條。撞頂之後個掣會消失，所以喺原本嗰條 `aria-live` status 出一句本地化解釋（`labels.rowCapReached`，五語言，`{count}` 喺 server 填死，唔想連 `fillTemplate` 都拖埋落 client）。**擺位**：桌面榜頂新開一行 `.ranking-filter-row`（`flex-wrap`，語言列 + 數量列共用 `.lang-filter` 個樣；margin 掛喺 row 唔掛喺 `.lang-filter`，否則 wrap 之後多咗一段空隙）；**手機 ≤680 同語言列一模一樣搬入 `SortFilterSheet`**（新「每頁」段）—— 唔另開新 pattern。`/box` 唔行 `?size=` 分頁，所以 `pageSizePicker={false}`、`pageSize` 只填預設頂住 type；搜尋模式 `pager` 係 `null`（行 `?show=`）亦唔出掣，出個揀唔郁嘅掣就係呃人。換數量**一定要一齊 `page: 1`**（喺第 5 版揀 500 唔重設就變 `?page=5&size=500` = #2001 起，1604 張直接 404）；`use-market-settings.ts` 讀 **live URL** 唔讀 props，換完即刻著返個掣，唔使等 RSC 行完。**驗收**（`temp/fe05/rank-cap/{verify-rank-cap,shots}.mjs`，dev :3901 有頭 Chromium 1440×900）：起錶 100 行、榜頂同 pager 兩處都係 `[100,200,500]`；碌 12 次 → 400 → **500 之後永遠 500**、掣收起、`next=/?page=6`（唔係 `page=2`）、status 出咗解釋；1440 語言@43+225／數量@276+221 **同一行 y162**，1024 同一行 y150，768 自動疊兩行，三個闊度 `scrollWidth` 都冇爆；撳 `›` → `/?page=6`、第一行 **#501**、榜頂落喺 viewport 頂（−18…6px，唔係 `scrollY 0`）；390 榜頂 `.ranking-filter-row` **唔存在**、排序 sheet 三段 `[Sort by / Language / Per page]`（截圖自己 Read 過）。**兩條 fault 逐條種返證紅**：A `RANKING_PAGE_SIZES` 加返 `1000` → 數量掣❌ + 見過 **1000 行**❌（順帶證實 cap 真係跟 `Math.max` 走，唔係另一個寫死嘅數）；B `RANKING_ROW_CAP` 改死 `2000` → 見過 **1100 行**❌ 而數量掣仍然✅（即係三條判詞真係各自獨立）；兩個 fault exit=1，還原後重跑 exit=0。⚠️ **試過用 `page.addInitScript` 注一粒假 `1000` 掣入 `.page-size-filter`：唔得，React 一 re-render 就掃走個 node，個閘照樣綠** —— 「注唔入去所以睇落冇事」正正就係假閘，fault 一定要落 source 種。**驗證腳本嘅 `EXPECTED_SIZES` / `ROW_CAP` 係故意寫死唔 import 實作**（import 咗 = 兩邊一齊錯都驗唔到）。**誠實講一句**：size **200 之下自動接停喺 400 行唔係 500**（再接一整版就 600，爆 cap）—— 設計如此，唔係壞咗；要睇 401–600 撳 `›`。**未收殮**：cap 只擋 append，冇擋「一開就 `?size=500`」嗰 500 行本身（owner 講 800 先 lag，500 係佢自己揀嘅上限）。**點樣反轉**：`git revert` 呢粒 —— 數量掣退返 100/200/300/500/1000、cap 退返 800（連同兩個舊洞）、`›` 退返指 `page+1`、手機 sheet 少一段 | ✅ 已落 |
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
   入口 `npm run lint`；首次基線 **10 error / 9 warning**（全部 pre-existing src）。
   **fix-lint（2026-08-17）之後：0 error / 8 warning，`npm run lint` exit 0。**
   一格 rule severity 都冇郁 —— 7 個 error 用 `eslint-disable-next-line` + 一行理由
   （`rankings.tsx` / `site-search.tsx` 嘅 hydration flag 同打字重設 highlight、
   `live-db-snapshot.ts` 5 句同步 `require`，轉 `await import` 會逼三個 function 變 async），
   3 個喺 `heatmap.tsx`（`react-hooks/refs`，即 :444–446 明文寫住嘅「render 淨係讀 ref」設計）
   行 `globalIgnores` —— **嗰個檔由另一 session 揸住，呢個 ignore 係欠單**：交返之後要剷走
   呢行再決定修定 disable，剷走之前 `heatmap.tsx` 零 lint 覆蓋。`heatmap-tile.tsx` 冇 ignore（0 problem）。
   證明 rule 仲係著：`npx eslint . --no-inline-config` **7 個 error 原地彈返出嚟**（唔係關咗 rule）。
   剩低 8 個 warning：7 個係 `_` 前綴嘅「特登唔用」變數（`server-snapshot.ts` ×4、
   `live-db-snapshot.ts` ×2、`use-updown.ts` ×1 —— next 個 config 淨係 `'warn'`，冇
   `varsIgnorePattern: "^_"`），1 個係 `ui/sheet.tsx:111` 嘅 ref-cleanup（**特登**喺 cleanup
   先讀 `returnFocusRef.current`，抄去 effect 開頭 = 改 focus 返嚟嘅目標）。
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
    **（fix-state 覆核，`temp/fe05/fix-state/m_attrs.py`）** 仲係咁：同一版 code 之下
    有 attr 嗰張卡 `.detail-art img` 個盒 1280 **397.453×520** / 390 **358×280**，
    冇 attr 嗰張（下面第 21 點個 fail-closed 閘剛好剝走咗佢）1280 **386.609×519.984** /
    390 **209.313×279.984** —— 即係 +10.844px / +148.687px，同 review 量到嘅一模一樣。
    正解仍然係 CSS `.detail-art img { height: auto }`，**唔喺 WS-state 權限範圍**（欠單未還）。
    **（2026-08-17 結案：`height: auto` 唔係正解，已試過、已量到、已收返。）**
    `temp/fe05/final/mech_auto.json`：加 `width:auto; height:auto` 之後未 load 個盒又變返冇比例，
    route 延遲 3s 之下 CLS 由 **0** 彈返 **0.026@390 / 0.058@768 / 0.032@1280**（shift 來源 IMG 本身）——
    即係同冇 attr 之前一模一樣。所以個 element box 撐滿容器係**接受咗嘅代價**（paint 同 mask 幾何
    唔變、截圖 byte-identical），globals.css `.detail-art img` 嗰行上面已寫明「故意唔加 height:auto」。
    欠單 20 由「未還」改做「唔還——決定咁樣」。
21. **7 張卡 bake 落嘅 base 尺寸同真正出街嗰個 variant 唔同（bake 側資料欠單）。**
    **（fix-state 補）** FE 側加咗 fail-closed 閘：`intrinsicSize(w, h, kind)` 見到
    `kind === "raw_front"` 而比例同 variant 畫布（`429/600`，`pipelines/build_asset_derivatives.py:13-15`）
    差過 1% 就成對唔出。實測 `/api/v1/catalog`：1603 張卡照出（`429×600` ×1597、
    `719×1000` ×3、`600×838`、`500×698`、`431×600` —— 比例全部差 <0.6%），
    **淨係 1 張**（`cmc_51dbab1d0cede988c65e5f81`，宣告 `1000×730` 橫向、出街 `_600` 係
    429×600 直度）而家 omit，`/api/v1/cards/[id]` 個 `image` 冇咗 `width`/`height`。
    307 條 BOX 一條都冇變（BOX 唔行卡畫布：實測有 `1000×730` / `750×750` / `1600×1600`）。
    根因仲喺 bake 側，呢個閘只係唔准 FE 講一個唔會 render 嘅比例。
    掃晒 `data/public/market-assets` 5709 個 WebP：1604 張卡嘅 `_600` **全部 429×600**、
    `_200` 全部 200×280，但有 7 個 base 檔係 719×1000 ×3 / 600×838 / 1000×730 / 500×698 /
    431×600。因為 `srcSet()` 一定出 variants 而且有 `sizes`，瀏覽器**永遠唔會**畫 base 檔，
    即係嗰 7 張卡宣告嘅比例描述緊一個唔會 render 嘅檔。實測無害（宣告值兩軸都大過容器，
    clamp 完個盒同正常卡一模一樣，截圖 byte-identical），但正解係 bake 側寫 variant 嘅尺寸。
22. **`box-image.tsx` 未消費 `image.width/height`。** `box-view.ts` 已經帶住（data-only），
    component 側未出 attribute —— `/box/[id]` 個圖仲係冇預留位。
23. **公開 API 契約 additive**：`/api/v1/market`、`/api/v1/cards/[id]`、`/api/v1/catalog` 三條
    route 都係直接 `Response.json` 個 view，所以 `image.width` / `image.height` 自動出咗街
    （實測 429/600；`1000/730` 嗰張由 fix-state 個 fail-closed 閘剝走咗，見第 21 點）。
    **只加 key，冇改冇刪**。全個 `docs/` 冇任何檔寫過呢個 payload
    嘅 schema，所以除咗呢度冇第二處要同步。
24. **「顯示更多」由 React state 搬去 URL（`show=<int>`）。** 只有大過 `CATALOG_LIST_CAP`(80)
    先出現喺 URL；`normaliseShow()` 嚴格淨數字（`240abc` 唔准當 240）、向上湊到 80 嘅倍數、
    上限 = 命中數湊足一版。`update()` 用 `router.replace` 所以唔加 history entry
    （實測撳兩次 `history.length` 一直係 **2**）。q 一變就 delete 個 param，`SortFilterSheet`
    嘅「還原」一樣 delete。
    **（fix-state 改咗兩件事）**
    - **硬頂由 8000 改成 480（`URL_SHOW_CAP = CATALOG_LIST_CAP * 6`），而且真係夾得住。**
      原本寫 `CATALOG_LIST_CAP * 100`，但 catalog 得 1911 條、`rankings.tsx` 已經按命中數
      clamp 到 ≤1920，所以 8000 由頭到尾冇夾過嘢＝死 code，個註釋講嘅保護根本冇 fire。
      實測（`temp/fe05/fix-state/m_load.py`，`/?q=a&show=8000`）改之前一個 commit render
      1594 行：1280 **50,615 node / 最長 long task 787ms**、390 **40,164 node / 412ms**；
      改之後 480 行：1280 **15,699 node / 628ms**、390 **12,402 node / 305ms**
      （URL 已經係 canonical `show=480` 嗰次：1280 **15,707 / 496ms**、390 **12,372 / 248ms**，
      差嗰 ~130ms 係自我修正嗰次 `router.replace` 嘅第二次 render）。
    - **撳過 480 行嗰段唔上 URL**，改為 `rankings.tsx` 嘅 `sessionShow`（綁 `query`，
      同 `update()` 「q 一變 delete show」同一條規矩，「還原」一齊清）。實測撳 8 下：
      行數 160/240/320/400/480/**560/640/720**，`show` 停喺 **480**，`replaceState` 停喺
      **5 次**（第 6–8 下零 navigation），`history.length` 全程 **2**，零 console error。
      代價講明白：撳到 720 行入卡頁再 back，還原到 **480** 行唔係 720。
    - **URL 會自我修正（新）**：以前 `?show=abc` 出 80 行但 URL 一路留住 `abc`，
      之後任何一次 `update()` 都照抄住個垃圾值。而家 `showDirty` + `rankings.tsx`
      一個 effect 寫返去（同 `langDemoted` 同一個 pattern）。實測 14 個值
      （`temp/fe05/fix-state/m_sanitise.py`，全部 `?q=a&show=<raw>`）：
      `abc` / `-5` / `240abc` / `1e3` / `160.0` / `0` / `80` → 80 行 + URL **冇咗個 param**；
      `123` → 160 行 + `show=160`；`00000240` / `+240`（`+` 解碼成空格，`trim` 完係 240）
      → 240 行 + `show=240`；`99999999` / `8000` → 480 行 + `show=480`；
      `160` / `480` 原樣。**render 幾多行 URL 就寫幾多**，零 console error。
25. **back-nav 實測（`show_url.json`）**：`/?q=a` 撳兩次 → `?q=a&show=240` / 240 行，
    撳第 150 行（`rect_top 365.5`、`scrollY 18747`）入卡頁再 back → URL 保住 `show=240`、
    240 行、嗰行仲喺度、`rect_top` **365.5**（**delta 0.0**），而 `scrollY` 係 18987 ——
    即係 `scroll-restoration.tsx` 行嘅係**錨點相對**還原，唔係絕對 Y，數字對唔上唔代表壞咗。
    新開一版 `?q=a&show=240` 喺 390（mobile list）同 1280（desktop table）都出足 240 行。
    **（fix-state 覆核）** 同一個場景（390，`?q=a&show=240`、第 150 行、
    `temp/fe05/fix-state/m_back240.py`）改完之後一樣：back 返嚟 `?q=a&show=240`、240 行、
    `rect_top` **delta 0.0**。而 `?q=a&show=8000` 呢條路 back 返嚟由 1594 行變 480 行，
    back 嗰刻嘅 long task 由**最長 769ms / 合共 1538ms**（review 量）跌到
    **最長 411ms / 合共 822ms**（`m_back.py`，390），URL 保住 `?q=a&show=480`。
26. **App Router 嘅 push 係 same-document**，Playwright `wait_for_url()` 由頭到尾唔會 fire。
    量呢類跳轉要 poll `location.pathname`，唔係 `wait_for_url` 壞咗。
27. **`naturalWidth` 喺 `srcset` w-descriptor + `sizes` 之下係密度校正過**：同一個 429×600 檔
    喺 1280 報 400×560、喺 390 報 250×351。WS2 寫嘅「實測呢張係 400 × 560」就係呢個。
    攞真實檔案尺寸唔可以信 `naturalWidth`。

**fix-visual 量到嘅實數**（`temp/fe05/visual/`，dev server :3901，卡 `cmc_fc229f7ae1b256b2119fa79b`）：

- **兩個 header menu 都係實色**（`verify.json` `A_menus`，8 個組合 = 語言／貨幣 × light／dark × 390／1280）：
  `backgroundColor` light `rgb(255,255,255)`（= `--surface`）、dark `rgb(26,26,26)`；
  `backdropFilter` **全部 `none`**；選項文字 light `rgb(23,23,23)` / dark `rgb(236,236,236)`
  （= `--ink`，選中同未選中都係）；`data-theme` 逐個對得上。截圖 8 張
  `menu-{language,currency}-{390,1280}-{light,dark}.png`。
- **字階收編（A/B，同一個 page load 注返 FE04 宣告做對照，唔改檔）**：6 頁 × **13 個闊度**
  （360 / 390 / 720 / 721 / 765 / 768 / 900 / 901 / 940 / 981 / 1000 / 1024 / 1280，`ab.json`）——
  - **最大 |Δtop| = 49.67px**（`/rankings/[slug]` @360 同 @390，`.hub-answer` 14→16px 令段落多咗行）；
    `docHeight` 最大 **+49**（同一格）、最大 **−34**（`/methodology`@721，`.content-answer` 17→16.02px）。
    兩個數都喺 owner 畀嘅 ~60px 上限之內。
  - `.hub-answer` 自己個 box 最大 **Δwidth 129.59px**（@900–1000）—— 呢個係 `max-width: 72ch`
    跟住字級變闊，唔係頁面位移，唔好同 Δtop 溝埋講。
  - `/`、`/card/[id]` **13 個闊度全部 0.00px / docHeight 0**（佢哋只食 `.provenance-panel h2`，
    而 `--step-0` 喺 ≤932 同 ≥1000 解出同以前一樣嘅 14 / 15px）。差異只喺 901–981：
    量到 |Δtop| **1.36px**（@901）、**1.24px**（@940）、**0.39px**（@981）。
  - `/about`、`/methodology` 除咗 721 嗰格之外全部 0.00px。
  - **`scrollWidth` 13 × 6 = 78 個組合逐個相同**（新舊完全一樣）→ 零新增橫向溢出。
- **補量：另外 8 個 call-site route**（review 2026-08-17 揸返同一支 A/B 腳本；頭 6 個
  `temp/fe05/review-visual/r10_gap.py`，`/data` 同 `/box/[id]` 係 `temp/fe05/fix-visual-r2/r11_data_boxid.py`）。
  原本 6 頁矩陣漏咗呢 8 個，**唔係漏 bug，係漏記錄** —— 逐格 `scrollWidth` 新舊相同、
  零 CLIP、零 OVERFLOW。呢 8 行連上面 6 行先係完整 baseline：

  | route | 食邊個 selector | 最大 \|Δtop\| | 同格 ΔdocHeight |
  |---|---|---|---|
  | `/glossary` | `.content-answer` | **31.88px** @721 | −32 |
  | `/faq` | `.content-answer` | 6.25px @721 | −6 |
  | `/data` | `.content-answer` | 6.25px @721 | −6 |
  | `/box` | `.provenance-panel h2` | 1.36px @901 | −2 |
  | `/pokemon` | `.provenance-panel h2` | 1.36px @901 | −2 |
  | `/one-piece` | `.provenance-panel h2` | 1.36px @901 | −2 |
  | `/sealed`（→ `/box` alias） | `.provenance-panel h2` | 1.36px @901 | −2 |
  | `/box/[id]` | `.provenance-panel h2` | 1.36px @901 | −1 |

  `.content-answer` 三頁全部只喺 **721 一格**有分別（722–764 未量，但 ramp 同一條）；
  `.provenance-panel h2` 五頁全部係 901 / 940 / 981 三格 = 1.36 / 1.23–1.24 / 0.39px，
  同 `/`、`/card/[id]` 一模一樣。最壞嗰格 `/glossary`@721 = 31.88px，仲喺 owner 嘅 ~60px 之內，
  但佢係全站第二大（僅次於 `/rankings/[slug]` 49.67px）—— 之後改 `--step-1` 要連佢一齊量。
- **360px × zh-TW / ja / ko**（`verify.json` `D_i18n360`，18 個「語言 × 頁」組合）：
  `documentElement.scrollWidth` / `body.scrollWidth` **全部 = 360 = innerWidth**；
  四個收編 selector 逐個 `scrollHeight - clientHeight ≤ 1` 兼 `overflow: visible`
  → **0 個裁字**。三張截圖 `i18n-360-{zhTW,ja,ko}.png`。
- **live 綠點脈衝有上限，而且播完唔留低嘢**（`B_livedot`；review fix 之後重量，
  `temp/fe05/fix-visual-r2/r3_rerun.json`）：`iterations 5`、`duration 1200ms`、
  **`fill none`**；t0 `running`（1 個），**t=7.5s 同 t=10.5s 兩次採樣
  `document.querySelector('.live-dot').getAnimations({subtree:true}).length` 都係 `0`**，
  `/` 同 `/card/[id]` 兩邊一樣（beam 仍然鎖死喺 `.detail-page`，粒點兩邊都跳）。
  **第一輪呢度紅過**：原本個 shorthand 有 `forwards`，個 effect 永遠 "in effect" →
  10.5 秒之後 `getAnimations()` 仍然係 1（`finished`、`currentTime` 封頂 `6000`）。
  上限本身冇壞（冇第 6 圈、冇重播），但係 100% 嗰格已經等於 base style，`forwards` 買唔到
  任何嘢，淨係令「播完唔准留低嘢」嗰類 audit 讀成紅 —— 而且同隔籬 `card-sheen-sweep`
  （冇 fill-mode，實測 1 → 0）唔一致。**拆咗 `forwards`，兩個新 keyframe 而家同一形狀。**
  `reduce` context **0 個 animation**。`scrollWidth` 冇變（1265）。
  **唔會 re-render 再播**：撳 header 換貨幣（成個 provenance subtree re-render）之後
  仍然係 **0 個 animation**（冇新 animation 生出嚟）。
- **手機一次性掃光**（`C_sheen`，390 × `is_mobile` + `has_touch`）：
  `data-art-sheen="ready"`、`animation-iteration-count 1`、`fill-mode none`、`1.6s`；
  早期取樣 `getAnimations()` **1 個 `running`** → 2.5 秒之後 **0 個**（light + dark 都係）。
  **CLS 0.0000、`layout-shift` entry 0 個**（observer `buffered: true`），
  `scrollWidth` **390**（冇橫向溢出）。桌面 1280：`.card-sheen` `display: none`、
  0 個 animation、`data-art-state` 仍然 `idle`（tilt 冇郁）。
  截圖：`sheen-mid-390-{dark,light}.png`（`animation.pause()` + `currentTime = 800`，
  即 linear 之下嘅正中）vs `sheen-rest-390-{dark,light}.png`。
- **兩個新 reduced-motion sibling 逐個即場證明會 fire**（`rule9.json`，AGENTS.md 規矩 9）：
  喺 `reduce` context 手動種 `data-art-sheen="ready"` →
  `.card-sheen` `display "none"`、`::before` `animationName "none"`、`getAnimations()` 0；
  `.live-dot::after` `display "none"` / `animationName "none"` / `getAnimations()` 0。
  同一段 forcing 喺 `no-preference` → `display "block"`、`card-sheen-sweep 1.6s` /
  `live-dot-pulse 1.2s × 5` 全部起。
  **第一輪呢個 probe 紅咗**（reduce 之下仍然 `display: block` + `card-sheen-sweep`），
  根因同修法見 §3.4。
- 6 頁 × 390／1280 兩檔 console error / warning / pageerror **全部 0**（`console.json`）。
- `npx tsc --noEmit -p apps/web` **0 error**（ESLint 喺呢棵 tree 行唔到，見 WS2 欠單 ⑦）。

**fix-visual 嘅決定（owner 授權我代拍板，每條寫埋點樣反轉）：**

1. **決定：`.select-menu` 轉實色 `--surface`，唔係調高 alpha。**
   根因同 `.period-menu-list` 一模一樣：祖先 `.site-header` 自己有 `backdrop-filter`
   （globals.css :351）→ 佢係 **backdrop root**，入面個 popover 個 `backdrop-filter`
   sample 唔到 header 以外嘅頁面內容，所以只係一層 .82 alpha 蓋住**未 blur** 嘅底 =
   睇穿。`backdrop-filter` 亦一齊收（喺 backdrop root 入面冇效果，淨係逼多次 repaint）。
   **點樣反轉**：`globals.css` `.select-menu` 改返 `background: var(--surface-translucent-strong)`
   + `backdrop-filter: blur(14px)`（但咁樣就係還原個 bug）。真正嘅另一條路係將 header
   個 `backdrop-filter` 拆走，令 popover 唔再喺 backdrop root 入面 —— 嗰個係另一單嘢。

2. **決定：`.content-answer` 同 `.hub-answer` 統一落 `--step-1`，手機面由 14px 升到 16px。**
   兩個係同一個「可引用摘要」角色，兩套字級係真設計缺陷；owner 已經授權接受 reflow，
   所以揀「統一」而唔係「兩邊各自留硬數」。代價量咗：`/rankings/[slug]` @360/390 成頁跌 49.67px。
   **點樣反轉**：`styles/hubs.css` `.hub-answer` 改返 `font-size: clamp(14px, 1.4vw, 17px)`；
   `styles/content-pages.css` `.content-answer` 改返 `17px` + `@media (max-width: 720px)` 加返 `16px`。

3. **決定：`.hub-lead` / `.provenance-panel h2` 落 `--step-0`（WS1 原計劃）。**
   兩端數值同以前一樣，改嘅只係「步位喺邊」——由硬斷點（981 / 900）變成 933–1000 嘅 ramp。
   最大代價喺 `/market-report`（`.hub-lead` 唯一 call site），`/` 同 `/card/[id]` 最多 1.36px。
   **點樣反轉**：`hubs.css` `.hub-lead` 改返 `14px` + `@media (min-width: 981px)` 加返 `15px`；
   `globals.css` `.provenance-panel h2` 改返 `15px` + `@media (max-width: 900px)` 加返 `14px`。

4. **決定：live 綠點脈衝喺**所有**出 `.live-badge` 嘅頁行，唔學 beam 鎖死喺 `.detail-page`。**
   beam 鎖 `.detail-page` 嘅理由係「`/` 唔可以喺 load 嗰陣多一條 7.8 秒 paint 動畫」——
   beam 行 `@property` 角度 + conic-gradient 邊框，**每 frame 都要重 paint**。
   粒點個環淨係 transform / opacity（compositor 做，零 paint）、只有 7px、6 秒收工，
   成本唔同一個數量級，而「資料新鮮」本來就係全站訊號。
   **點樣反轉**：`glow-badges.css` 個 `.live-dot::after` 選擇器加返 `.detail-page` 前綴。

5. **決定：`/` 唔加 display face（維持默認）。** ⚠️ **部分推翻（owner 2026-08-17，webfont commit）**：
   原文「`--font-sans` 一個 system stack 到底」已廢 —— system stack 喺 Windows 跌落 Yu Gothic，
   標題／標籤字重倒轉 + 拉丁字形錯，係 bug 唔係「靚啲」（根因同數字見 §1.3.1）。而家拉丁行
   self-host Inter Variable（一隻、latin subset、48 kB、preload + swap + Arial metric fallback）。
   **仍然成立嘅部分**：唔加 display face、唔加第二隻字體、唔加 CJK web font；`/` LCP element 唔准變。
   **點樣反轉**：`git revert` webfont commit（`fe05(webfont)`）—— `--font-sans` 會退返 system stack、
   `tile-style.ts` 退返 canvas 量度、contract test 一齊走；woff2 / OFL 留喺 repo 無害。
   （舊文：`/` 嘅 H1 係 LCP element，加自訂字體 = 多一個 render-blocking / FOUT 風險。
   實測見 §8 log 該行：preload 同源 48 kB，CLS / LCP element 都守到。）

6. **決定：手機掃光行 `linear`，唔行 `--ease-standard`。**
   `--ease-standard` = `cubic-bezier(0.22, 1, 0.36, 1)`，**半程就行咗 96% 路**
   （實測 `currentTime 800/1600` 嗰陣 translate 已經 +505px，條光早就出咗畫面），
   睇落似閃一閃唔似掃光。§1.5 嗰四條曲線係俾**入場／互動**用，唔係俾等速位移用；
   `live-beam` 一樣行 `linear`。**點樣反轉**：`card-art.css` 換返 `var(--ease-standard)`。

7. **決定：掃光多開一個真 DOM 節點（`<span class="card-sheen">`），唔用 pseudo。**
   mask 落邊個 element，transform 就連 mask 一齊郁 = 爆 §5「overlay 只准用 mask」條契約。
   所以外層揸 mask（唔郁）＋ 內層 `::before` 掃過。pseudo 冇 children，做唔到。
   個 span 係 `aria-hidden` 純裝飾、base style `display: none`，所以桌面／reduced-motion／
   爬蟲側零影響。**點樣反轉**：拆咗個 span，改用喺 `.card-art::after` 度 animate
   `background-position`（做得到，但變咗 paint-per-frame）。

**fix-visual 嘅欠單：**

1. **`.hub-note`（12 / 12.5px）同 `.provenance-body`（13px 平頭）仍然係硬數**，`--step--1`
   夾唔返（見 §1.3）。呢兩個冇「同一角色兩套字級」嘅缺陷做理由，所以冇順手收。
2. **掃光只有 `/card/[id]` 食到**（`CardArt` 得嗰度 call）。`/box/[id]` 個卡圖冇包 `CardArt`，
   要唔要一齊有，係另一單。
3. **`--holo-a1` 喺 dark 係 `rgba(255, 255, 255, 0.26)`**（globals.css:245；light 係 `.42`，:145），
   掃光相對含蓄。呢個係故意跟返
   WS2 桌面 sheen 同一對 token（唔准為咗手機另開一對）；覺得唔夠明顯就係改 token，
   兩邊一齊變。
4. dev server（未 minify）量到嘅 longtask 唔算數（WS1 欠單 ④ 同一條），呢次冇量 longtask。

**fix-heatmap-title 嘅決定**（owner 2026-08-17：「無論手機版定電腦版，我都想熱力圖儘量唔好遷就啲文字，
係文字遷就返個熱力圖……就咁 top 100 市值咪算囉」）：

1. **根因係 `.heatmap-section` grid 個 heading 行係 `auto`**：H1 換行幾多行、描述句幾多行、
   footer methodology 幾多行（zh 3 / ja ko 4 / en 5 行）全部直接由 `.heatmap-frame` 度扣。
   改前（`temp/fe05/heatmap-title/before.json`）1280px 個 frame：zh 372 / en 341 / ja 294；
   1024px：zh 327 / en 296 / ja 212。改後（`after.json`）五語言 1024 → 505–507、1280 → 513–515、
   1415 → 598–600、1920 → 774–775（±2px 係 ja/ko controls 闊 9–14px 令標題容器窄咗少少）；
   390 / 768 唔變（本來已經一行）。
2. **字級由容器決定、唔由語言決定**：`.heatmap-title { container-type: inline-size; flex: 1 1 0 }`，
   H1 `font-size: clamp(16px, min(4.6vw, calc(100cqi / 9.5)), 68px)`。五語言最長標題
   （ja「ワンピース TOP 100」）實測 ≈ 9.03em，9.5 留 5% 字體差（review 量到 981px 時 ja 只剩 2.8px slack）。
   因為公式唔認得文字，改 i18n heatmap 標題**一定要**過
   `scripts/test-fe-heatmap-title-width.mjs`（估算 em ≤ divisor × 0.97，內建兩條 negative case 證明會 fire）。
   唔識 `cqi` 嘅舊瀏覽器行 `@supports not` 解除 nowrap，照舊換行。
3. **981–1180px 個 H1 會細到 16–30px**（controls 720px 佔咗大半行）。呢個係故意：owner 揀 heatmap，
   唔揀大字。**點樣反轉**：clamp 樓底由 16px 升返（每升 1px，最窄嗰段 ja/ko 就早 ~9px 出「...」）。
4. **`overflow: hidden` + `line-height: 1.06` 會裁 Segoe UI 下伸部**（p / g 底部）：H1 加
   `padding-block: 0.16em` 擴大裁剪盒、`margin-block: -0.16em calc(12px - 0.16em)` 補返，版面高度不變。
   手機 rule 嘅 `margin-bottom` 跟住改做 `calc(4px - 0.16em)`。
5. **描述句同 methodology 唔係刪咗**：`t.heatmap.body` 仍然係 legend `aria-label`；
   `t.methodology.body` 全文喺 site footer（`header.tsx` `<Footer>`）。heatmap 區只係唔再重複。
   （share image 由 2026-08-17 晚起唔再畫任何文案，見 fix-share-image。）

**fix-owner-round-0817 嘅決定**（owner 2026-08-17 三段口頭 review，逐句對返）：

1. **「只搞電腦版，放大啲，用盡佢，留少少呼吸位」**：`@media (min-width: 981px)` 尾段一個 block（`--desk-gutter` / `--desk-max`），
   `.header-inner` / `.page-shell` 一齊由 `min(100%, 1440px)` 變 `min(100%, 2400px)`；1920 實測 shell 1905px、heatmap frame 1790×765
   （改前 1410×~600）。hub 頁（`.page-shell.hub-page`）另外鎖 1280——長文唔應該 100 字一行。表格 th 56 / td 88、縮圖 50×70、
   `.detail-art` 最高 680（`img` padding **唔郁**：26px = card-art.css `--card-art-pad`，holo mask 幾何靠佢）。
   卡名欄 20→26%（1440 闊「月亮伊布 VMAX 異圖 2…」截斷而編號欄一半係空）。
2. **「字體大小統一」**：以桌面為準，手機只喺真係擺唔落先細一級：nav 12→14；`.detail-metrics` label 9.5→11、數值 19→22（手機 14→20）、
   delta 10.5→12（手機 8→12）；story 14→15；identity dt 11.5 / dd 13；`.card-fact` 13→15。手機 `.chart-label` 冇郁——SVG viewBox 720 縮放，px 冇意義。
3. **「圖之後即刻走勢 → 市值/數量 → 再碌先簡介」**：`card-detail.tsx` DOM 順序 header（kicker/h1/set/identity）→ period → HistoryChart →
   metrics → data-time → card-fact → story → provenance。card-fact 係 GEO 可引用事實，只搬位唔刪；JSON-LD／meta 讀變數唔讀 DOM。Reveal 仍係 3 個。
4. **「search 永遠喺 menu，但唔准逼走 logo；升跌反轉搬第二度」**：`<SiteSearch>` 全頁常駐；升跌反轉掣 render 兩粒（`.updown-toggle-header` /
   `.updown-toggle-nav`），CSS 按斷點只出一粒：≥981 留 header 控制列，≤980 落 nav 行最右。實測 390 四語言 logo 79×34 原比例；≤400 再收 gap 8→6 /
   padding 7→6 / icon 掣 44→40 令 360 ja 都保得住（77px）；**320 ja 仍縮到 37px，接受**（iPhone SE 1 代級數）。
5. **「市值數字用返品牌橙」「現位底線橙」「箭嘴跟升跌色」**：`.heatmap-total-cap .cap-ticker { color: var(--accent) }`；
   `.primary-nav a::after` 由 `--ink` 1px 變 `--accent` 2px；升跌掣 icon 由 lucide `ArrowDownUp` 改手寫同一幾何嘅 SVG（兩個 `<g>` 各自
   `color: var(--positive)` / `var(--negative)`），`data-updown` 一反轉箭嘴顏色即跟。
6. **「EN/JP chip 加埋入卡榜同手機」**：`print-badge.tsx` 加 `languageBadge()`（同 box `langBadge` 共用 `.print-badge--compact`；只分 ja/en 兩色，
   ko/zhCN/zhTW 中性）；桌面喺 `.ranking-name` 下面 `.ranking-sub-row`，手機同編號一行 `.mobile-lang-badge`（9px）。原盒手機 list 之前根本冇 chip，一齊補。
7. **「原盒 Pokémon 一個種類就得，語言入排序」**：`BoxScope` 由四值變 `"all"|"optcg"|"ptcg"`（`tcgOfGroup()`；舊 URL `?group=ptcg-jp` 讀嗰陣
   demote 做 `ptcg`，唔改寫 URL）；`t.box.groups` 改 2 key、字照抄 `nav.onePiece/pokemon`；語言篩共用卡榜嘅 `?printLang=`（box `lang` "jp"↔"ja"），
   桌面 h2 下面 `.lang-filter` 全部/EN/JP，手機入 SortFilterSheet「語言」段 + filter chip；URL 帶 pool 冇嘅語言就寫返 "all"。
8. **順手修嘅舊 bug（[KNOWN]，出街版一直錯）**：WS3 count-up 將內頁三格數值包咗 `<span class="cap-ticker">`，撞正 `.detail-metrics span`
   （label 規則：9.5px、`--dt-color`），所以市值／PSA10 價／POP 一直係細灰字，得 6M 升跌／成交額／RAW 正常。
   `.detail-metrics strong > .cap-ticker { font-size/color: inherit }` 還原。**冇 test 守住**（CSS 計算值要 browser）——欠單。

**fix-tile-label-fit 嘅決定**（owner 2026-08-17：「睇唔到數字唔緊要，但唔好食咗啲 percentage……唔想見到啲字黐住張圖最左最右」）：

- 舊做法：字體 = 12% 短邊夾 8–14px，label `position:absolute; right:4px` 由右向左生長，tile `overflow:hidden` 就喺左邊裁走「+3」——1440 100 格
  1Y 都有成排 `89.1%`／`87.3%` 咁樣缺頭。
- 新做法（`lib/tile-style.ts` `fitTileLabel`）：可用闊 = tile 闊 − 2×inset(4) − 2×padX(3)；label 闊度用 canvas `measureText`（body 嘅 font-family、800）
  量一次 cache 一次（+4% 補 tabular-nums、+0.01em×字數補 letter-spacing）；`fontSize = min(舊上限, floor(可用闊 / em), floor(可用高 / 1.2))`。
  ≥8px 就出完整字；唔夠就試去小數；再唔夠 `move = null` 唔畫。手機 100 格時細格會冇數字——owner 講明接受；要救多啲可以將 `TILE_LABEL.minFont` 落 7。
- 幾何契約寫死喺兩邊（`TILE_LABEL` ↔ `.tile-move`），comment 互相指住；`exportHeatmap` 直接食同一個 `st.move/st.fontSize`，share 圖唔會另外爆。
- SSR 冇 canvas → 保守估值（digit 0.62em / % 0.95 / . 0.32）；實際 tile 只喺 client 量完 frame 先 render，估值只係 fallback。

**feat-currencies 嘅決定**（owner 2026-08-17：「加多啲貨幣、對應返個 logo」）：

1. **「logo」= 貨幣符號，唔係國旗。** 兩個理由，兩個都係硬嘅：emoji 國旗喺 **Windows 根本 render 唔到**
   （出兩個字母方格，即係 owner 自己部機睇唔到個 logo）；而且**一隻貨幣唔等於一個國家** ——
   EUR 冇國旗、USD 唔止美國用、CHF 有兩個國家。符號係貨幣自己嘅身份，唔使查表對國家。
   符號表 `currencySymbol` 喺 `lib/currency-meta.ts`，31 隻齊。
2. **本地化名由 ICU 出，唔手寫 31 × 5 表。** `Intl.DisplayNames(type: "currency", fallback: "none")`，
   per-locale module 級 cache。手寫 155 格一定會過時，而且五個語系冇人守得住。
   `fallback: "none"` → 冇資料回 `undefined`，包住 try/catch 回 `null`，**唔畫空 `.currency-name`**。
   只喺 client 用（選單撳開先 render）→ 冇 SSR / hydration mismatch。
3. **USD 釘最頂、唔屬任何組**（owner 2026-08-17「usd默認最頂」）：佢係 base 兼預設，埋喺美洲組第 16 位要捲先搵到。
   `currencyMenuOrder()` 先放 USD，`currencyMenuGroup()` 對 USD 回 `null`（唔出 heading）；`currencyRegion.USD` 仍然係 americas（geo 用）。
   之後**分組次序 亞太 → 美洲 → 歐洲 → 中東非洲**（`currencyRegionOrder`），唔跟正典次序。
   讀者主要喺 HK / TW / JP / KR，第一組就要係佢哋嗰批；組**入面**保持正典次序。
   heading 行 `role="presentation"`，**唔佔 option 序號** —— `select-control.tsx` 個 `index`
   仍然係扁平位置，所以 id / `aria-activedescendant` / 鍵盤上下同冇分組嗰陣一模一樣。
4. **只出有匯率嘅貨幣。** `availableCurrencies()` 讀 snapshot 個 `rates`，`Number.isFinite && > 0` 先入選單：
   baked snapshot 落後一日、未有新加嘅貨幣，用戶就唔會揀到一隻成頁「暫無資料」。
   兩重 fail-open：snapshot 讀唔到／filter 完係空 → 退返全部（header 唔可以因為資料層死而消失）；
   而**現時揀緊嗰隻永遠留喺選項入面**，否則個 select 個值唔喺 options 度。
   配套：`snapshot.ts` 個 rates 投影改成 `rates[c]?.value ?? NaN` —— 硬讀 `.value` 遇到缺貨幣會 TypeError 炸成頁，
   而家跌落 `formatMoney` 原本嘅 fail-closed（出「暫無資料」）。
5. **正典清單住喺三個地方，改就三個一齊改**（三份都有交叉指住嘅 comment）：
   `apps/web/src/lib/types.ts` `currencies`（FE + `Currency` type）、
   `packages/market-data/src/schema.ts` `CURRENCIES`（snapshot 契約）、
   `pipelines/fx_rates.py` `SUPPORTED_CURRENCIES`（FX 採集 + 驗證）。
   三份 2026-08-17 一齊改齊（`fx_rates.py` 喺 fe-db 改、live tree 鏡像）；fe-db 朝鏈 `morning_browser_lanes.ps1` +
   `refresh_publish.ps1` 加咗 `fx_rates.py → fx_db_load.py` 步驟（之前 037 鏈由頭到尾冇人行 FX，出街匯率停喺 08-02）。
   `live-db-snapshot.ts` 個 `rates` 由逐隻手寫改成行 `currencies` map，加貨幣唔使再改嗰度 —— bake 出街嘅 31 隻匯率就係靠呢度。
   **出街 snapshot 要 bake 過先有 31 隻**：FE deploy 咗但未 bake 嗰段時間，第 4 點令選單只出舊 snapshot 有嘅 7 隻。

### 明確非目標

唔遷 Tailwind / shadcn；唔上 WebGL / shader；`/` 唔加 marketing hero；字體只得 §1.3.1 嗰一隻 Inter latin
（唔加 display face / 第二隻 / CJK web font / latin-ext / opsz 檔；`zero` `cv*` `ss*` `-webkit-font-smoothing` 全部唔開）；
唔改 `heatmap.tsx` 嘅顏色算法（`exportHeatmap` 已按 owner 2026-08-17 要求重做，見 fix-share-image；tile 幾何亦已加 device-px snapping，見 fix-heatmap-align —— treemap「面積 ∝ 市值」算法本身冇郁）；唔加 `app/card/loading.tsx`；
唔為視覺效果加 npm dep；無 scroll-jacking / parallax；sparkline 同 ranking row 唔動；
唔為咗動畫放鬆任何 gate / 契約 / crawler 可見文字。
