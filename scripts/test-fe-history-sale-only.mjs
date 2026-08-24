#!/usr/bin/env node
/*
 * 「日線同變幅只可以由真成交嚟」契約 —— 2026-08-24（R6：K 線殘留踢出公開 snapshot）。
 *
 * 背景：頭條價 2026-08-23 已經改成「最新一單真 PSA10 成交」（見 test-fe-sale-date-label），
 * 但 `historyDaily[].priceUsd` 仲係由 `market_price_observation`（PriceCharting／SNKRDUNK
 * 嘅圖表點，即係「有價冇成交」嘅 K 線）餵——最近 30 日就有 3,530 個咁嘅點。而
 * `windows.*.changePct` / `marketCapChangePct` 全部錨喺呢條 history 上面，即係話出街嘅
 * 「+8.8%」其實係攞真成交同 K 線比。呢個唔會有 error，只會靜靜出錯數。
 *
 * 呢個檔守四樣嘢：
 *  ① observation 讀路（R6b，owner 2026-08-24 起合法返嚟）全檔只准一處，而且只准餵
 *     `historyReference` 呢條獨立 series；history builder 段落一個字都唔准掂佢
 *     （string 級，一眼睇得出有冇人駁錯線）。
 *  ② **真源碼行一次**：抽 live-db-snapshot.ts 嗰段 history builder 出嚟，餵住 K 線點 +
 *     成交點，K 線嗰日一定唔可以出現喺 historyDaily。
 *  ③ **真 windowMetrics 行一次**（同一份 history）：30d 變幅要等於成交對成交嘅差；
 *     窗內冇成交嘅 1d 要係 null（唔准借 K 線點填）。
 *  ④ 錨同現價同一條 lane 嗰陣唔准 flag `sourceSwitched`——全板 quote code 係
 *     `pricecharting_sales`，history 點係 `pricecharting_sales`，唔剝尾碼比較就會
 *     成板卡掛住「換咗來源」嘅提示。
 *  ⑤ /llms-full.txt 唔准再寫 reference price 係「reduced to a median」。
 *
 * 純靜態 + 真評估：唔開 DB、唔開瀏覽器，由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 *
 * Run: node scripts/test-fe-history-sale-only.mjs
 */
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PRODUCER_REL = "apps/web/src/lib/live-db-snapshot.ts";
const LLMS_REL = "apps/web/src/app/llms-full.txt/route.ts";

const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const producer = read(PRODUCER_REL);

/* ─────────────────────────────────────────────────────────────
 * H1 — observation 讀路只准餵 `historyReference`（R6b）；historyDaily 讀路零接觸。
 *      R6 原版係「成個 producer 唔准有呢條 SQL」；owner 2026-08-24 指示長窗
 *      （90/180/365）行混合錨 + ≥90d 圖表補深歷史，讀路先返嚟——但一定係另一條
 *      series，唔准再餵 historyDaily。
 * ───────────────────────────────────────────────────────────── */
check("H1: `INNER JOIN market_price_observation` 有且只有一處（reference 讀路）",
  producer.split("INNER JOIN market_price_observation").length - 1 === 1);
check("H1: `metric_kind='psa10_price'` 有且只有一處（同一條 reference query）",
  producer.split("metric_kind='psa10_price'").length - 1 === 1);
check("H1: `priceRows` 呢個 binding 完全冇咗（唔准淨係 query 咗唔用）",
  !/\bpriceRows\b/.test(producer));

/* ─────────────────────────────────────────────────────────────
 * 抽真源碼：① 成段 history builder（inline 喺 buildLiveDbSnapshot 入面）
 *          ② 成個 module（剝走 import）攞真 `windowMetrics`
 * ───────────────────────────────────────────────────────────── */
const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-history-sale-only-${process.pid}`);
mkdirSync(dir, { recursive: true });

const transpile = (source, name) => {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const file = join(dir, name);
  writeFileSync(file, outputText);
  return pathToFileURL(file).href;
};

const BLOCK_START = "    type HistoryDraft = DailyHistoryPoint & {";
const BLOCK_END = "\n    const cards: PublicCard[] = coreRows.map((row) => {";
const startAt = producer.indexOf(BLOCK_START);
const endAt = producer.indexOf(BLOCK_END);
check("抽到 history builder 段落", startAt >= 0 && endAt > startAt, `${startAt}/${endAt}`);

let buildHistories = null;
if (startAt >= 0 && endAt > startAt) {
  const slice = producer.slice(startAt, endAt);
  // 只封真接線（SQL join / identifier），唔封講歷史嘅註釋。
  check("H1: history builder 段落唔掂 observation/reference（historyDaily 照舊 sales-only）",
    !slice.includes("INNER JOIN market_price_observation")
    && !/\breferenceRows\b|\breferences\b|\bReferenceDraft\b/.test(slice));
  // 唯一一句改動：DB／receipt 讀路換成 test 餵入嘅隔離表。其餘一個字唔郁。
  const quarantineLine = slice.split("\n").filter((line) => line.trim().startsWith("const saleQuarantine ="));
  check("history builder 有且只有一行 `const saleQuarantine =`", quarantineLine.length === 1,
    `搵到 ${quarantineLine.length} 行`);
  const wired = slice.replace(quarantineLine[0] ?? "@@none@@", "    const saleQuarantine = injectedQuarantine;");
  const module = [
    "export async function buildHistories(ctx: any) {",
    "  const { priceRows, salesRows, coreRows, injectedQuarantine, day, numberValue, chartLaneOf } = ctx;",
    "  void priceRows; void coreRows; void chartLaneOf;",
    wired,
    "  return histories;",
    "}",
  ].join("\n");
  ({ buildHistories } = await import(transpile(module, "history-builder.mjs")));
}

// 成個 producer module（剝走 import 頭）—— 攞真 `windowMetrics`，唔喺 test 度抄一份。
const moduleBody = producer.slice(producer.indexOf("const LOCALES = ["));
const snapshotLib = await import(transpile(
  `${moduleBody}\nexport { windowMetrics, chartLaneOf, day, numberValue };\n`,
  "producer.mjs",
));

/* ─────────────────────────────────────────────────────────────
 * Fixture：一張卡，兩單真成交 + 兩個 K 線點（K 線點特登擺喺兩個窗嘅錨位）。
 * ───────────────────────────────────────────────────────────── */
const VARIANT = 1;
const CURRENT_PRICE = 3100;
const CURRENT_AS_OF = "2026-08-22T00:00:00Z";
const chartRows = [
  // 30d 錨帶（07-18…07-28）入面，特登比真成交（07-20）更近 target（07-23）。
  { variant_id: VARIANT, observed_date: "2026-07-25", price_usd: 2850, source_code: "pricecharting" },
  // 1d 錨帶（08-19…08-23，要嚴格舊過 08-22）入面，而嗰窗根本冇成交。
  { variant_id: VARIANT, observed_date: "2026-08-21", price_usd: 3000, source_code: "pricecharting" },
];
const saleRows = [
  {
    variant_id: VARIANT, observed_date: "2026-07-20", sales_count: 1, sales_value_usd: 2000,
    sales_coverage_status: "partial", sales_verified_zero: 0, sales_source_codes: "pricecharting",
    sales_evidence_at: "2026-07-20T00:00:00Z",
  },
  {
    variant_id: VARIANT, observed_date: "2026-08-22", sales_count: 1, sales_value_usd: 3100,
    sales_coverage_status: "partial", sales_verified_zero: 0, sales_source_codes: "pricecharting",
    sales_evidence_at: "2026-08-22T00:00:00Z",
  },
];
const coreRows = [{ variant_id: VARIANT, price_source_code: "pricecharting_sales" }];

let history = [];
if (buildHistories) {
  const histories = await buildHistories({
    priceRows: [chartRows],
    salesRows: [saleRows],
    coreRows,
    injectedQuarantine: new Map(),
    day: snapshotLib.day,
    numberValue: snapshotLib.numberValue,
    chartLaneOf: snapshotLib.chartLaneOf,
  });
  history = [...(histories.get(VARIANT)?.values() ?? [])].sort((a, b) => a.at.localeCompare(b.at));
}

/* ─────────────────────────────────────────────────────────────
 * H2 — 淨係有 K 線嘅日子唔准出現喺 historyDaily；成交日照出，價 = 成交價。
 * ───────────────────────────────────────────────────────────── */
{
  const dates = history.map((point) => point.at.slice(0, 10));
  check("H2: 只有 K 線嘅 2026-07-25 唔准入 historyDaily", !dates.includes("2026-07-25"), dates.join(","));
  check("H2: 只有 K 線嘅 2026-08-21 唔准入 historyDaily", !dates.includes("2026-08-21"), dates.join(","));
  check("H2: 兩個成交日照出", dates.includes("2026-07-20") && dates.includes("2026-08-22"), dates.join(","));
  check("H2: historyDaily 淨係得成交日", dates.length === 2, dates.join(","));
  const first = history.find((point) => point.at.startsWith("2026-07-20"));
  const last = history.find((point) => point.at.startsWith("2026-08-22"));
  check("H2: 07-20 個價 = 當日真成交", first?.priceUsd === 2000, JSON.stringify(first));
  check("H2: 08-22 個價 = 當日真成交", last?.priceUsd === 3100, JSON.stringify(last));
  check("H2: 成交點掛住 sale lane 碼", first?.priceSourceCode === "pricecharting_sales", first?.priceSourceCode);
}

/* ─────────────────────────────────────────────────────────────
 * H3 — 真 `windowMetrics`：30d 變幅 = 成交對成交；1d 窗內冇成交 → null。
 * ───────────────────────────────────────────────────────────── */
{
  const windows = snapshotLib.windowMetrics(
    history,
    CURRENT_PRICE,
    1000,
    CURRENT_AS_OF,
    snapshotLib.chartLaneOf("pricecharting_sales"),
  );
  const thirty = windows["30d"].changePct;
  const expected = (CURRENT_PRICE / 2000 - 1) * 100;   // 3100 vs 07-20 嗰單 2000 = +55%
  check("H3: 30d 變幅 = 成交對成交（唔准錨 K 線）",
    thirty.status === "ready" && Math.abs(thirty.value - expected) < 1e-9,
    JSON.stringify(thirty));
  check("H3: 30d 錨同現價同一條 lane → 唔准 flag sourceSwitched",
    thirty.sourceSwitched === false, JSON.stringify(thirty));

  const one = windows["1d"].changePct;
  check("H3: 1d 窗內冇成交 → null / accumulating（唔准借 K 線）",
    one.value === null && one.status === "accumulating", JSON.stringify(one));
  check("H3: 1d 市值變幅一樣要 null",
    windows["1d"].marketCapChangePct.value === null, JSON.stringify(windows["1d"].marketCapChangePct));
  check("H3: 30d 市值變幅跟返成交錨（價×POP）",
    Math.abs(windows["30d"].marketCapChangePct.value - expected) < 1e-9,
    JSON.stringify(windows["30d"].marketCapChangePct));
}

/* ─────────────────────────────────────────────────────────────
 * H4 — 空 / 得一點嘅 history 唔准炸親下游（窗計算 + 圖表切窗）。
 * ───────────────────────────────────────────────────────────── */
{
  const emptyWindows = snapshotLib.windowMetrics([], CURRENT_PRICE, 1000, CURRENT_AS_OF, "pricecharting");
  check("H4: 空 history → 全窗 accumulating，唔炸",
    Object.values(emptyWindows).every((w) => w.changePct.value === null && w.changePct.status === "accumulating"));
  const { pointsForWindow } = await import(transpile(read("apps/web/src/lib/history-window.ts"), "history-window.mjs"));
  check("H4: pointsForWindow 食空陣列 → []", pointsForWindow([], 30).length === 0);
  check("H4: pointsForWindow 食單點 30d → 得返嗰點", pointsForWindow(history.slice(0, 1), 30).length === 1);
  check("H4: pointsForWindow 食單點 1d → 得返嗰點", pointsForWindow(history.slice(0, 1), 1).length === 1);
}

rmSync(dir, { recursive: true, force: true });

/* ─────────────────────────────────────────────────────────────
 * H5 — /llms-full.txt 嘅 reference price 文案要講「最新一單真成交、剔走離群」。
 * ───────────────────────────────────────────────────────────── */
{
  const llms = read(LLMS_REL);
  const line = llms.split("\n").find((row) => row.trim().startsWith('"- Reference price:')) ?? "";
  check("H5: 搵到 reference price 嗰行", Boolean(line));
  check("H5: 唔准再講 `reduced to a median`", !/reduced to a median/i.test(line), line.trim().slice(0, 160));
  check("H5: 要講最新一單真 PSA 10 成交",
    /most recent completed PSA 10 sale/i.test(line), line.trim().slice(0, 160));
  check("H5: 要講剔走離群成交", /outside its own recent range|outlier/i.test(line), line.trim().slice(0, 160));
}

/* ─────────────────────────────────────────────────────────────
 * H6 — 日線價**未**做到 brief 講嘅「當日最新一單非離群成交」，呢個係已知欠單，
 *      唔准靜靜當交咗貨。behaviour 一日仲係「當日成交均價 + 冇離群帶」，
 *      producer 就一日要喺 history builder 段落入面帶住個欠單 marker。
 *      （fix 咗 behaviour = `salesValue / salesCount` 消失，呢組 check 自動放行。）
 * ───────────────────────────────────────────────────────────── */
{
  const MARKER = "R6-OPEN-TICKET: history-daily-latest-sale-and-outlier-band";
  const builderSlice = startAt >= 0 && endAt > startAt ? producer.slice(startAt, endAt) : "";
  const dayAverage = builderSlice.includes("point.priceUsd = salesValue / salesCount;");
  check("H6: 前提——日線價仲係當日成交均價", dayAverage,
    "均價寫法冇咗；如果真係改成『當日最新一單非離群成交』，連同 H6 一齊更新");
  check("H6: 均價／冇離群帶要喺 history builder 入面明文標欠單 marker",
    !dayAverage || builderSlice.includes(MARKER), `搵唔到 ${MARKER}`);
  check("H6: 欠單要寫明依家冇離群帶（離群帶淨係喺 quote 側）",
    !dayAverage || /冇離群帶/.test(builderSlice), "marker 段落冇講『冇離群帶』");
  check("H6: 欠單要講明點先算修好（要 sale 級排序 = 改 view／migration）",
    !dayAverage || /sale 級排序/.test(builderSlice), "marker 段落冇講修法");
}

if (failed.length) {
  console.error(`FAIL test-fe-history-sale-only (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-history-sale-only (${checks} checks)`);
