/*
 * 小故事係 markdown，但 `<meta name="description">` / `og:description` /
 * JSON-LD 全部係純文字 attribute。之前係逐字原文照塞，出街嘅結果：
 * `### Basic Info`、`*   ` 開頭嘅 bullet、`**粗體**` 星號，同埋真換行字元入咗
 * attribute 入面；最長嗰篇 5,330 字（`cmc_9949ee1ce83f6df9cfa96195` en，baked
 * snapshot），live DB `catalog_variant_locale` 更長，去到 5,849 字。搜尋器同社交
 * 卡片都係 155–200 字就斬，所以呢啲字唔止醜，係直頭冇人睇到。
 *
 * 特登唔擺入 `format.ts`：嗰個係 Intl／locale 呈現模組（錢、日期、百分比），呢個
 * 係字串消毒，兩件事。混埋一齊之後就冇人知邊個 function 應該識 locale。
 *
 * 掃走咩，係量過 1,286 張卡 × 2,579 篇故事之後定嘅，唔係照抄一套通用 markdown 規矩：
 *   - `### ` 標題：97 篇有
 *   - `*   ` bullet：255 篇有
 *   - `**粗**` 206 篇、`*斜*` 121 篇
 *   - 零篇有 code fence / code span / link / image / 引用 / 分隔線 / raw HTML /
 *     HTML entity / 刪除線 / 表格
 *
 * **底線一個都唔准掂。** 全批得 3 個 `_`，三個都係同一張卡（`cmc_51dbab...`）
 * 個真卡名「`______'s Pikachu`」（Celebrations 嗰張）。當 `__` 做粗體剝走就會出
 * 「's Pikachu」—— 剝壞真內容，衰過留低個星號。
 */

/* 斬到最尾唔准淨返半個字：淨係喺個空格已經行過 60% 先至退到嗰度。CJK 冇空格，
 * 自然行呢條 else，硬斬——嗰啲語言本身就係逐字斬。 */
const KEEP_RATIO = 0.6;

export function plainDescription(input: string, max = 160): string {
  const flat = input
    // ATX 標題：`### Basic Info` → `Basic Info`
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    // list bullet：連住後面嗰段空白一齊剝，唔係會留低一堆縮排
    .replace(/^\s{0,3}([-*+]|\d{1,9}[.)])\s+/gm, "")
    // 粗體先過，斜體後過；`_` 唔喺呢度出現，理由見上面
    .replace(/\*\*/g, "")
    .replace(/\*/g, "")
    // 換行 / 縮排全部變單一空格：attribute 入面唔可以有真換行
    .replace(/\s+/g, " ")
    .trim();
  if (flat.length <= max) return flat;

  const head = flat.slice(0, max - 1);
  const lastSpace = head.lastIndexOf(" ");
  const cut = lastSpace > max * KEEP_RATIO ? head.slice(0, lastSpace) : head;
  return `${cut.replace(/[\s.,;:!?、。，；：！？·\-–—]+$/u, "")}…`;
}
