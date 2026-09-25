#!/usr/bin/env node
// FE05 heatmap H1 契約（owner 2026-08-17：「文字遷就返個熱力圖」）。
// globals.css 用「容器闊度 ÷ DIVISOR」定 H1 字級，公式唔認得文字，所以每個語言嘅
// heatmap 標題（title / pokemonTitle / onePieceTitle）估算闊度一定要 ≤ DIVISOR em，
// 否則某個語言會出「...」或者換行偷 heatmap 高度。呢個 test 由 run_all_tests.py 自動 glob。
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition) => { if (!condition) failed.push(label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const css = read("apps/web/src/app/globals.css");
const i18n = read("apps/web/src/lib/i18n.ts");
const heatmapTsx = read("apps/web/src/components/heatmap.tsx");

/* em 估算（FE05 webfont 起，2026-08-17；type-scale commit 起 h1 = 600，表跟住換 600）：拉丁逐字元對住
   self-host Inter Variable **600** 嘅 canvas advance（temp/fe05/review-webfont/measure-inter.mjs 量，dev :3901，
   document.fonts.ready 之後，measure-inter.json `advances600`）；
   數字一律 0.6455 —— body `font-variant-numeric: tabular-nums` 由 H1 繼承，Inter tabular 數字等闊而且
   跨 weight 唔變（500 / 800 DOM 實測都係 0.6455；proportional 嘅「1」只有 0.415，用 proportional 表會低估 "100" 0.23em）。
   CJK / kana / Hangul 唔係 Inter 出（latin subset 冇 glyph，跌落 OS 字），Windows Yu Gothic / JhengHei /
   Malgun 三家全部 1.00em/字（DOM 實測：3 字同 4 字標題差恰好 1.00em），一律當 1.0。
   再扣 H1 嘅 letter-spacing/字 —— 由 globals.css `--track-hero`（fe05(cjk) 起獨立 token，-0.035em；CJK locale 唔清零，
   因為 ja「ワンピース TOP 100」清零就係 9.52em > 9.5×0.97，H1 會出「…」）讀返嚟，唔硬寫。DOM 實測（100px, weight 600, ls -0.035em）對照：
     「ワンピース TOP 100」9.066（估 9.06）、「市值前 100 熱力圖」8.093、「Top 100 heatmap」8.007、
     「One Piece TOP 100」8.838、「Pokémon TOP 100」8.501、「원피스 TOP 100」7.136 —— 估算誤差 ≤ 0.7%。
   （weight 500 嗰版：ワンピース 9.057 / Top 100 heatmap 7.934，600 只多 0.1–0.9%。）
   換字體 / 改 h1 字重 / 改字距先要重跑 measure-inter.mjs 更新呢張表。 */
const INTER_600 = {
  " ": 0.252, "·": 0.3188, "é": 0.5913, "è": 0.5913, a: 0.5742, b: 0.624, c: 0.5825, d: 0.624, e: 0.5913, f: 0.3887,
  g: 0.6255, h: 0.6123, i: 0.2617, j: 0.2617, k: 0.5693, l: 0.2617, m: 0.9004, n: 0.6118, o: 0.6089, p: 0.624,
  q: 0.624, r: 0.397, s: 0.5493, t: 0.353, u: 0.6123, v: 0.5869, w: 0.8394, x: 0.5688, y: 0.5884, z: 0.5659,
  A: 0.7275, B: 0.6592, C: 0.7368, D: 0.7222, E: 0.6055, F: 0.5879, G: 0.749, H: 0.7456, I: 0.2769, J: 0.5796,
  K: 0.7031, L: 0.5654, M: 0.9224, N: 0.7593, O: 0.7686, P: 0.645, Q: 0.7729, R: 0.6523, S: 0.6504, T: 0.6602,
  U: 0.7358, V: 0.7275, W: 1.02, X: 0.7197, Y: 0.7134, Z: 0.6523,
};
const INTER_TABULAR_DIGIT = 0.6455;

// H1 字距由 --track-hero 帶（.heatmap-heading h1 { letter-spacing: var(--track-hero) }），全 globals.css 只准宣告一次
// （CJK token block 唔准覆蓋佢 —— 覆蓋咗估算就同真頁面脫節）。負數 em；讀唔到就 fail，唔靜靜當 0。
const cssNoComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
const trackHeroDecls = [...cssNoComments.matchAll(/--track-hero:\s*([^;]+);/g)].map((m) => m[1].trim());
check("--track-hero declared exactly once in globals.css", trackHeroDecls.length === 1);
const TRACK_HERO_EM = trackHeroDecls.length === 1 ? Number(trackHeroDecls[0].match(/^(-?[\d.]+)em$/)?.[1] ?? NaN) : NaN;
check("--track-hero is an em value", Number.isFinite(TRACK_HERO_EM));
check("--track-hero is negative (Latin display tracking; 0 會令 ja 標題爆闊)", TRACK_HERO_EM < 0);
check("heatmap h1 letter-spacing uses var(--track-hero)", /\.heatmap-heading h1,\s*\.heatmap-heading h2 \{[^}]*letter-spacing:\s*var\(--track-hero\)/.test(cssNoComments));
export function estimateEm(text) {
  let em = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if (/[0-9]/.test(ch)) em += INTER_TABULAR_DIGIT;
    else if (ch in INTER_600) em += INTER_600[ch];
    else if (/\s/.test(ch)) em += INTER_600[" "];
    else if (/[A-Z]/.test(ch)) em += 0.72; // 表外大寫（唔應該出現）：Inter 600 大寫平均 0.70，取偏闊
    else if (/[a-zà-ÿ]/.test(ch)) em += 0.6; // 表外小寫 / 帶音標：Inter 600 小寫平均 0.55，取偏闊
    else if (cp >= 0xac00 && cp <= 0xd7af) em += 1.0; // Hangul（Malgun Gothic 實測 1.00；舊表 0.97 係 review 前估）
    else if (cp >= 0x3000) em += 1.0; // CJK / kana（Yu Gothic / JhengHei 實測 1.00）
    else em += 0.6;
  }
  return em + (Number.isFinite(TRACK_HERO_EM) ? TRACK_HERO_EM : 0) * [...text].length;
}

// H1 rule：nowrap + 容器 ÷ divisor
// 有兩條 rule 都係 `.heatmap-heading h1, .heatmap-heading h2 {`（一條同 .hero-section 共用），揀有 100cqi 嗰條
const h1Rule = [...css.matchAll(/\.heatmap-heading h1,\s*\.heatmap-heading h2 \{[^}]*\}/g)].map((m) => m[0]).find((r) => /100cqi/.test(r)) ?? "";
check("heatmap h1 rule present", h1Rule.length > 0);
check("heatmap h1 nowrap", /white-space:\s*nowrap/.test(h1Rule));
check("heatmap h1 ellipsis fallback", /text-overflow:\s*ellipsis/.test(h1Rule));
const divisor = Number(h1Rule.match(/100cqi\s*\/\s*([\d.]+)/)?.[1] ?? NaN);
check("heatmap h1 font-size uses 100cqi / divisor", Number.isFinite(divisor));
check("heatmap title column is inline-size container", /\.heatmap-title \{[^}]*container-type:\s*inline-size/.test(css));

// heading 唔准再擺描述句 / methodology（每語言唔同高度）
check("heatmap heading has no lede paragraph", !/<p>\{t\.heatmap\.body\}<\/p>/.test(heatmapTsx));
check("heatmap footer has no methodology-note", !/className="methodology-note"/.test(heatmapTsx));

// 每個 locale 嘅 heatmap 標題 ≤ divisor em
const heatmapBlocks = [...i18n.matchAll(/^\s{4}heatmap: \{([\s\S]*?)^\s{4}\},/gm)].map((m) => m[1]);
check("found five locale heatmap blocks", heatmapBlocks.length === 5);
const worst = [];
for (const block of heatmapBlocks) {
  for (const key of ["title", "pokemonTitle", "onePieceTitle"]) {
    const raw = block.match(new RegExp(`\\b${key}: "([^"]+)"`))?.[1];
    check(`heatmap.${key} present`, typeof raw === "string");
    if (!raw) continue;
    const text = raw.replace("{count}", "100");
    const em = estimateEm(text);
    worst.push([em, text]);
    // × 0.97 安全係數：估算器 vs 真瀏覽器差 ≤ 0.7%（Inter 600 逐字元表），剩返俾 OS CJK 字體差（Mac PingFang / Hiragino 未量）
    check(`heatmap.${key} "${text}" ≈ ${em.toFixed(2)}em ≤ ${divisor} × 0.97em`, em <= divisor * 0.97);
  }
}

// negative self-test：估算器真係會捉到舊標題（rule 9：證明個 gate 會 fire）
check("estimator flags old EN title", estimateEm("Top 100 market heatmap") > divisor);
check("estimator flags old JA title", estimateEm("時価総額トップ 100 ヒートマップ") > divisor);

if (failed.length) {
  console.error("FAIL test-fe-heatmap-title-width:\n - " + failed.join("\n - "));
  process.exit(1);
}
worst.sort((a, b) => b[0] - a[0]);
console.log(`PASS test-fe-heatmap-title-width (divisor ${divisor}em; widest ${worst[0][0].toFixed(2)}em "${worst[0][1]}")`);
