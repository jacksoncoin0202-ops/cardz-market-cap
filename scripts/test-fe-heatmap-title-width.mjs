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

/* em 估算（FE05 webfont 起，2026-08-17）：拉丁 / 數字逐字元對住 self-host Inter Variable 500 嘅 canvas
   advance（temp/fe05/review-webfont/measure-inter.mjs 量，dev :3901，document.fonts.ready 之後）；
   數字一律 0.6455 —— body `font-variant-numeric: tabular-nums` 由 H1 繼承，Inter tabular 數字等闊
   （proportional 嘅「1」只有 0.415，用 proportional 表會低估 "100" 0.23em）。
   CJK / kana / Hangul 唔係 Inter 出（latin subset 冇 glyph，跌落 OS 字），Windows Yu Gothic / JhengHei /
   Malgun 三家全部 1.00em/字（DOM 實測：3 字同 4 字標題差恰好 1.00em），一律當 1.0。
   再扣 H1 嘅 letter-spacing -0.035em/字。DOM 實測（100px, weight 500, ls -0.035em）對照：
     「ワンピース TOP 100」9.057（估 9.08）、「市值前 100 熱力圖」8.096（估 8.08）、「Top 100 heatmap」7.934（估 7.99）、
     「One Piece TOP 100」8.786、「Pokémon TOP 100」8.444、「원피스 TOP 100」7.127 —— 估算誤差 ≤ 0.7%。
   weight 600（C2 之後 h1 會升到 600）同一批標題 DOM 闊度只多 0.1–0.9%（ワンピース 9.066），0.97 安全係數包得住；
   換字體 / 改字距先要重跑 measure-inter.mjs 更新呢張表。 */
const INTER_500 = {
  " ": 0.2666, "·": 0.3032, "é": 0.5874, "è": 0.5874,
  a: 0.5679, b: 0.6182, c: 0.5771, d: 0.6182, e: 0.5874, f: 0.3794, g: 0.6196, h: 0.6016, i: 0.252, j: 0.252,
  k: 0.5591, l: 0.252, m: 0.8882, n: 0.6016, o: 0.604, p: 0.6182, q: 0.6182, r: 0.3867, s: 0.5386, t: 0.3403,
  u: 0.6016, v: 0.5747, w: 0.8291, x: 0.5571, y: 0.5752, z: 0.5591,
  A: 0.709, B: 0.6567, C: 0.7334, D: 0.7217, E: 0.603, F: 0.5894, G: 0.7476, H: 0.7446, I: 0.2725, J: 0.5752,
  K: 0.6875, L: 0.5654, M: 0.9126, N: 0.7563, O: 0.7666, P: 0.6416, Q: 0.7686, R: 0.6479, S: 0.646, T: 0.6528,
  U: 0.7402, V: 0.709, W: 1.0029, X: 0.7007, Y: 0.6963, Z: 0.6406,
};
const INTER_TABULAR_DIGIT = 0.6455;
export function estimateEm(text) {
  let em = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if (/[0-9]/.test(ch)) em += INTER_TABULAR_DIGIT;
    else if (ch in INTER_500) em += INTER_500[ch];
    else if (/\s/.test(ch)) em += INTER_500[" "];
    else if (/[A-Z]/.test(ch)) em += 0.72; // 表外大寫（唔應該出現）：Inter 500 大寫平均 0.69，取偏闊
    else if (/[a-zà-ÿ]/.test(ch)) em += 0.6; // 表外小寫 / 帶音標：Inter 500 小寫平均 0.54，取偏闊
    else if (cp >= 0xac00 && cp <= 0xd7af) em += 1.0; // Hangul（Malgun Gothic 實測 1.00；舊表 0.97 係 review 前估）
    else if (cp >= 0x3000) em += 1.0; // CJK / kana（Yu Gothic / JhengHei 實測 1.00）
    else em += 0.6;
  }
  return em - 0.035 * [...text].length;
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
    // × 0.97 安全係數：估算器 vs 真瀏覽器差 ≤ 0.7%（Inter 逐字元表）+ h1 升 600 多 ≤ 0.9%，剩返俾 OS CJK 字體差
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
