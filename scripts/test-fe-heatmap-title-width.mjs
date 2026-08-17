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

/* 粗略 em 估算（系統字：CJK 1em、Hangul 0.97、數字 0.6、大寫 0.7、小寫 0.55、空格 0.28），
   再扣 H1 嘅 letter-spacing ≈ -0.035em/字。用 Playwright canvas 量過：
   「市值前 100 熱力圖」8.08、「ワンピース TOP 100」9.03、「Top 100 market heatmap」10.93。 */
export function estimateEm(text) {
  let em = 0;
  for (const ch of text) {
    const cp = ch.codePointAt(0);
    if (/\s/.test(ch)) em += 0.28;
    else if (/[0-9]/.test(ch)) em += 0.6;
    else if (/[A-Z]/.test(ch)) em += 0.7;
    else if (/[a-zà-ÿ]/.test(ch)) em += 0.55;
    else if (cp >= 0xac00 && cp <= 0xd7af) em += 0.97; // Hangul（0.92 會低估 3.7%，review 實測「원피스 TOP 100」7.09em）
    else if (cp >= 0x3000) em += 1.0; // CJK / kana
    else em += 0.5;
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
    // × 0.97 安全係數：估算器 vs 真瀏覽器差 ±4%，divisor 本身已留 5% 俾字體差
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
