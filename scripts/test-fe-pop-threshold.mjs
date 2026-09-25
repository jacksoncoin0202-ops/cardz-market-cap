#!/usr/bin/env node
/*
 * 入圍門檻（PSA 10 POP ≥ 1000）契約 —— 2026-08-19。
 *
 * 呢個數係**政策**，唔係 magic number：pipeline 用佢揀邊張卡入宇宙，網站用佢答
 * 「點解搵唔到我張卡」。owner 2026-08-19 叫喺搜尋框下面常駐寫住呢句 —— 由嗰刻起，
 * 呢個數就散落喺 7 個地方（pipeline 1、snapshot 2、FE 文案 5 種語言 ×3 句）。
 *
 * 唔守嘅話會發生咩：有人改 pipeline 個 1000（例如收緊做 2000），網站照舊寫住 1,000，
 * **冇任何 error** —— 用戶搵唔到卡、睇住個提示以為自己數錯，我哋自己都唔知講緊大話。
 * 所以呢度唔係「檢查有冇寫」，係「pipeline 話幾多，每一句文案就要講返幾多」。
 *
 * 權威次序：pipelines/active_universe.py（真係篩嗰句）→ 其餘全部要跟佢。
 */
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail) => { if (!condition) failed.push(detail ? `${label}: ${detail}` : label); };
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

/* ── 權威：pipeline 真正篩卡嗰句 ───────────────────────────── */
const universe = read("pipelines/active_universe.py");
const gate = universe.match(/populationPsa10"\]\)\s*>=\s*(\d+)/);
check("T0: active_universe.py 搵到入圍門檻", !!gate, "篩卡嗰句 `int(row[\"populationPsa10\"]) >= N` 搵唔到 —— 條 pipeline 改咗寫法就要改呢個 test，唔准當冇事");
if (!gate) {
  console.error(`FAIL ${failed.length}`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
const MIN = Number(gate[1]);
/* 五種語言嘅文案全部用 en-US 千分位（ja/ko 都係寫 1,000，唔係 1000）。 */
const shown = new Intl.NumberFormat("en-US").format(MIN);

/* ── snapshot 兩處 ─────────────────────────────────────────── */
const seed = JSON.parse(read("data/public/seed-snapshot.json"));
check("T1: seed-snapshot universe.populationMin", seed.universe?.populationMin === MIN,
  `${seed.universe?.populationMin} ≠ ${MIN}`);

const liveDb = read("apps/web/src/lib/live-db-snapshot.ts");
const liveMin = liveDb.match(/populationMin:\s*(\d+)/);
check("T1: live-db-snapshot populationMin", Number(liveMin?.[1]) === MIN, `${liveMin?.[1]} ≠ ${MIN}`);

/* ── llms-full.txt（AI crawler 讀嗰份）────────────────────── */
const llms = read("apps/web/src/app/llms-full.txt/route.ts");
const llmsMin = llms.match(/MIN_POP_THRESHOLD\s*=\s*(\d+)/);
check("T2: llms-full MIN_POP_THRESHOLD", Number(llmsMin?.[1]) === MIN, `${llmsMin?.[1]} ≠ ${MIN}`);

/* ── FE 文案：五種語言 × 三句 ──────────────────────────────
 * 提取每個 key 嘅字串值再逐個驗，唔可以淨係數成個檔有幾多個「1,000」——
 * 咁樣一句寫啱、一句寫錯都會照過。
 * ───────────────────────────────────────────────────────── */
const i18n = read("apps/web/src/lib/i18n.ts");
const LOCALES = 5;
/* 只收 `key: "…"` 嘅值行（type block 個 `key: string;` 唔會中）。 */
const valuesOf = (key) => [...i18n.matchAll(new RegExp(`${key}:\\s*"((?:[^"\\\\]|\\\\.)*)"`, "g"))].map((m) => m[1]);

for (const key of ["searchPopRule", "searchUnqualified"]) {
  const values = valuesOf(key);
  check(`T3: ${key} 五種語言齊`, values.length === LOCALES, `得 ${values.length} 個`);
  const missing = values.filter((v) => !v.includes(shown));
  check(`T3: ${key} 每句都要講住 ${shown}`, missing.length === 0,
    `${missing.length} 句冇提 ${shown} → ${JSON.stringify(missing.slice(0, 2))}`);
}

/* methodology.body 係全站最長嗰段方法學文案，五種語言都寫住個數 —— 佢同上面兩句
   一齊改先叫一致，所以一齊守。
   ⚠️ 唔可以用「有冇提 PSA 10」嚟撈：全檔仲有第二、三段文案都提 PSA 10（實測撈到 7 段，
   多咗兩段英文），咁樣個 test 會告錯人。所以由 `methodology: {` 開始，攞佢入面第一個
   `body:` —— 一個 locale 一段，唔多唔少。
   ⚠️ 仲要由 `export const copy` 之後先開始搵：type block 個
   `methodology: { title: string; body: string }` 一樣中 regex，跟住個 4000 字窗口會
   滑落下一個 locale 真嘅 body，靜靜多計一段（實測 6 段）。 */
const copyStart = i18n.indexOf("export const copy");
check("T4: 搵到 copy 表", copyStart > 0);
const bodies = [...i18n.slice(copyStart).matchAll(/methodology:\s*\{/g)]
  .map((m) => i18n.slice(copyStart + m.index, copyStart + m.index + 4000).match(/body:\s*"((?:[^"\\]|\\.)*)"/)?.[1])
  .filter(Boolean);
check("T4: 搵到五種語言嘅 methodology 段", bodies.length === LOCALES, `得 ${bodies.length} 段`);
const staleBodies = bodies.filter((v) => !v.includes(shown));
check(`T4: methodology 每段都要講住 ${shown}`, staleBodies.length === 0,
  `${staleBodies.length} 段冇提 ${shown}`);

/* ── UI 真係出到 ─────────────────────────────────────────────
 * 有文案冇 call site = 等於冇寫（AGENTS 規矩：任何「有檢查但零 call site」都當冇）。
 * ───────────────────────────────────────────────────────── */
const search = read("apps/web/src/components/site-search.tsx");
check("T5: 搜尋框有 render searchPopRule", /t\.labels\.searchPopRule/.test(search),
  "site-search.tsx 冇用到 —— 文案寫咗但冇人見到");
check("T5: 提示唔係只喺 empty state 出",
  search.indexOf("searchPopRule") < search.indexOf("site-search-empty"),
  "searchPopRule 要喺 empty state **之前** render，即係未打字就見到");
const css = read("apps/web/src/app/globals.css");
check("T5: .site-search-note 有樣式", /\.site-search-note\s*\{/.test(css));

if (failed.length) {
  console.error(`FAIL ${failed.length}`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log(`PASS test-fe-pop-threshold（門檻 ${shown}，pipeline → snapshot ×2 → llms → 文案 ×15 → UI 全部對得住）`);
