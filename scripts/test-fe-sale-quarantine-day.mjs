#!/usr/bin/env node
/*
 * 隔離 receipt 扣走成日啲成交嗰陣，唔准拖冧長窗 trackedSales —— 2026-09-25。
 *
 * 背景：pipelines/pc_sale_title_quarantine.py 由 2026-09-25 起多一個判別器
 * （sale_price_outlier.is_isolated_price_outlier，reason price_isolated_spike），第一次
 * rollout 會多 ~550 單 receipt-only entry（058 表下一輪 chain sync 先跟上）。舊 builder
 * 扣到零嗰日留低一個 coverage 'partial' + value null 嘅點，salesTotal 當「未知」，蓋住
 * 嗰日嘅每個窗 trackedSales 都變 unavailable（Top100 要 30d 成交）。
 *
 * 呢個檔守三樣嘢，全部行真源碼（抽 live-db-snapshot.ts 嘅 history builder + 真
 * loadSaleQuarantine + 真 windowMetrics），唔開 DB：
 *  Q1 receipt-only 扣到零嘅日子唔出點；90d/180d/365d trackedSales 照出，數 = 淨低真成交。
 *  Q2 Latias & Latios GX 170/181（variant 1148）真數：currentAsOf 2026-09-25T09:29:29Z、
 *     現價 $15,100 → 90d 錨係 06-26 $17,100（−11.7%），永遠唔係 06-27 嗰單 $1,485。
 *     receipt-only 同 DB 已剔走兩條路要出同一個結果。
 *  Q3 mutation：將「成日扣晒就唔出點」嗰句剝走，Q1 一定要紅（證明條閘有牙）。
 *
 * 由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 *
 * Run: node scripts/test-fe-sale-quarantine-day.mjs
 */
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PRODUCER_REL = "apps/web/src/lib/live-db-snapshot.ts";

const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};

const producer = readFileSync(join(ROOT, PRODUCER_REL), "utf8");
const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-sale-quarantine-day-${process.pid}`);
mkdirSync(dir, { recursive: true });
const transpile = (source, name) => {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const file = join(dir, name);
  writeFileSync(file, outputText);
  return pathToFileURL(file).href;
};

// loadSaleQuarantine / repoRoot 用同步 `require("node:fs")`（見 producer 頂嘅 block 註）；
// 轉咗 ESM 之後要俾返個 require 佢，先行得到真函數而唔係抄一份。
globalThis.require = createRequire(import.meta.url);

const moduleBody = producer.slice(producer.indexOf("const LOCALES = ["));
const lib = await import(transpile(
  `${moduleBody}\nexport { windowMetrics, chartLaneOf, day, numberValue, loadSaleQuarantine };\n`,
  "producer.mjs",
));

// 同 test-fe-history-sale-only 一樣：抽 inline 喺 buildLiveDbSnapshot 入面嘅 history
// builder，淨係將 `const saleQuarantine =` 嗰行換成 test 餵入嘅 map。
const BLOCK_START = "    type HistoryDraft = DailyHistoryPoint & {";
const BLOCK_END = "\n    const cards: PublicCard[] = coreRows.map((row) => {";
const loadBuilder = async (source, name) => {
  const startAt = source.indexOf(BLOCK_START);
  const endAt = source.indexOf(BLOCK_END);
  if (startAt < 0 || endAt <= startAt) return null;
  const slice = source.slice(startAt, endAt);
  const line = slice.split("\n").filter((row) => row.trim().startsWith("const saleQuarantine ="));
  if (line.length !== 1) return null;
  const wired = slice.replace(line[0], "    const saleQuarantine = injectedQuarantine;");
  const module = [
    "export async function buildHistories(ctx: any) {",
    "  const { salesRows, injectedQuarantine, day, numberValue } = ctx;",
    wired,
    "  return histories;",
    "}",
  ].join("\n");
  return (await import(transpile(module, name))).buildHistories;
};
const buildHistories = await loadBuilder(producer, "history-builder.mjs");
check("抽到 history builder（有且只有一行 `const saleQuarantine =`）", buildHistories !== null);

// 真 loadSaleQuarantine 讀一份臨時 receipt（CARDZ_REPO_ROOT 指去 temp root）。
const withReceipt = (entries, dbExcluded) => {
  const root = join(dir, `root-${Math.random().toString(36).slice(2)}`);
  const audit = join(root, "data/runtime/operator/audit");
  mkdirSync(audit, { recursive: true });
  writeFileSync(join(audit, "pc_sale_title_quarantine_current.json"), JSON.stringify({ entries }));
  const saved = process.env.CARDZ_REPO_ROOT;
  process.env.CARDZ_REPO_ROOT = root;
  try {
    return lib.loadSaleQuarantine(new Set(dbExcluded));
  } finally {
    if (saved === undefined) delete process.env.CARDZ_REPO_ROOT;
    else process.env.CARDZ_REPO_ROOT = saved;
  }
};

const historyOf = async (builder, variantId, rows, quarantine) => {
  const histories = await builder({
    salesRows: [rows], injectedQuarantine: quarantine, day: lib.day, numberValue: lib.numberValue,
  });
  return [...(histories.get(variantId)?.values() ?? [])].sort((a, b) => a.at.localeCompare(b.at));
};

/* ─────────────────────────────────────────────────────────────
 * Fixture：variant 1148 Latias & Latios GX 170/181，operator_accepted_psa10_sales_history
 * 2026-06-22…09-22 真數（2026-09-25 read-only 讀返）。06-27 $1,485、07-02 $1,908.06
 * 係成日得嗰一單；07-30 兩單入面嗰單 $4,662.42 係尖刺（另一單 $14,988.88 係真）。
 * ───────────────────────────────────────────────────────────── */
const LATIAS = 1148;
const CURRENT_PRICE = 15100;
const CURRENT_AS_OF = "2026-09-25T09:29:29Z";
const DAYS = [
  ["2026-06-22", 1, 19800], ["2026-06-25", 2, 35500], ["2026-06-26", 1, 17100], ["2026-06-27", 1, 1485],
  ["2026-06-29", 1, 17700], ["2026-07-02", 1, 1908.06], ["2026-07-03", 1, 17150], ["2026-07-05", 1, 15985.92],
  ["2026-07-09", 1, 15600], ["2026-07-13", 2, 31899], ["2026-07-14", 1, 18000], ["2026-07-17", 1, 15000],
  ["2026-07-18", 1, 15699], ["2026-07-19", 1, 15450], ["2026-07-20", 1, 17450], ["2026-07-21", 1, 14600],
  ["2026-07-23", 1, 15500], ["2026-07-30", 2, 19651.30], ["2026-08-01", 2, 31000], ["2026-08-03", 1, 15300],
  ["2026-08-07", 1, 15600], ["2026-08-09", 2, 30650], ["2026-08-12", 1, 15150], ["2026-08-13", 3, 54000],
  ["2026-08-14", 1, 18000], ["2026-08-17", 2, 30499], ["2026-08-18", 1, 15100], ["2026-08-20", 1, 14000],
  ["2026-08-21", 1, 17250], ["2026-08-23", 1, 14200], ["2026-08-28", 3, 52499], ["2026-08-29", 2, 29800],
  ["2026-08-30", 2, 33376.34], ["2026-08-31", 1, 15300], ["2026-09-01", 1, 15000], ["2026-09-03", 1, 13900],
  ["2026-09-05", 1, 20000], ["2026-09-06", 2, 35100], ["2026-09-07", 1, 19500], ["2026-09-09", 3, 51425.26],
  ["2026-09-13", 2, 32405], ["2026-09-14", 2, 30000], ["2026-09-18", 2, 30750], ["2026-09-22", 1, 15100],
];
const row = ([date, count, value]) => ({
  variant_id: LATIAS, observed_date: date, sales_count: count, sales_value_usd: value,
  sales_coverage_status: "partial", sales_verified_zero: 0, sales_source_codes: "pricecharting",
  sales_evidence_at: `${date}T00:00:00Z`,
});
const RECEIPT_ENTRIES = [
  // 同 pc_sale_title_quarantine.py 寫出嚟嘅 price entry 一樣形狀（多出嘅 field TS 唔睇）。
  { saleObservationId: 1920226, variantId: LATIAS, observedDate: "2026-06-27", unitPriceUsd: 1485, quantity: 1,
    transactionValueUsd: 1485, wantedCollectorNumber: "170", listingTitle: "x", reason: "price_isolated_spike",
    direction: "below_band", priorMedianUsd: "17650.000000", followingMedianUsd: "15942.960000" },
  { saleObservationId: 1920225, variantId: LATIAS, observedDate: "2026-07-02", unitPriceUsd: 1908.06, quantity: 1,
    transactionValueUsd: 1908.06, wantedCollectorNumber: "170", listingTitle: "x", reason: "price_isolated_spike" },
  { saleObservationId: 2249919, variantId: LATIAS, observedDate: "2026-07-30", unitPriceUsd: 4662.42, quantity: 1,
    transactionValueUsd: 4662.42, wantedCollectorNumber: "170", listingTitle: "x", reason: "price_isolated_spike" },
];
const allRows = DAYS.map(row);
// 058 表 sync 之後 view 本身嘅樣：成日扣晒嗰兩日冇咗行，07-30 淨返真嗰單。
const dbRows = DAYS
  .filter(([date]) => date !== "2026-06-27" && date !== "2026-07-02")
  .map(([date, count, value]) => (date === "2026-07-30" ? row([date, 1, 14988.88]) : row([date, count, value])));

const sumWindow = (fromDate, toDate) => {
  let value = 0;
  let count = 0;
  for (const r of dbRows) {
    if (r.observed_date >= fromDate && r.observed_date <= toDate) {
      value += r.sales_value_usd;
      count += r.sales_count;
    }
  }
  return { value, count };
};

const lane = lib.chartLaneOf("pricecharting_sales");
const runPath = async (builder, label, rows, quarantine) => {
  const history = await historyOf(builder, LATIAS, rows, quarantine);
  const windows = lib.windowMetrics(history, CURRENT_PRICE, 1000, CURRENT_AS_OF, lane);
  return { label, history, windows };
};

if (buildHistories) {
  const receiptQuarantine = withReceipt(RECEIPT_ENTRIES, []);
  check("真 loadSaleQuarantine 讀到三日", receiptQuarantine.size === 3, JSON.stringify([...receiptQuarantine]));
  const dbKnown = withReceipt(RECEIPT_ENTRIES, [1920225, 1920226, 2249919]);
  check("DB 表已識嘅 id 唔再扣第二次", dbKnown.size === 0, JSON.stringify([...dbKnown]));

  const paths = [
    await runPath(buildHistories, "receipt-only", allRows, receiptQuarantine),
    await runPath(buildHistories, "db-excluded", dbRows, new Map()),
  ];
  // 90d 當前窗 = [06-28, 09-25]；180d / 365d 由 03-30 / 2025-09-26 起，全部蓋住 06-27 + 07-02。
  const expected = {
    "90d": sumWindow("2026-06-28", "2026-09-25"),
    "180d": sumWindow("2026-03-30", "2026-09-25"),
    "365d": sumWindow("2025-09-26", "2026-09-25"),
  };
  for (const { label, history, windows } of paths) {
    const dates = history.map((point) => point.at.slice(0, 10));
    /* Q1 */
    check(`Q1 ${label}: 06-27 / 07-02（成日扣晒）唔出點`,
      !dates.includes("2026-06-27") && !dates.includes("2026-07-02"), dates.join(","));
    check(`Q1 ${label}: 冇任何 value null 但 coverage 已覆蓋嘅點`,
      history.every((point) => point.salesCoverage === "unavailable"
        || (Number.isFinite(point.trackedSalesValueUsd) && Number.isFinite(point.trackedSalesCount))),
      JSON.stringify(history.filter((point) => !Number.isFinite(point.trackedSalesValueUsd))));
    const jul30 = history.find((point) => point.at.startsWith("2026-07-30"));
    check(`Q1 ${label}: 07-30 淨返真嗰單 $14,988.88`,
      jul30?.trackedSalesCount === 1 && Math.abs(jul30.trackedSalesValueUsd - 14988.88) < 1e-6
        && Math.abs(jul30.priceUsd - 14988.88) < 1e-6,
      JSON.stringify(jul30));
    for (const code of ["90d", "180d", "365d"]) {
      const tracked = windows[code].trackedSales;
      check(`Q1 ${label}: ${code} trackedSales 照出（唔准 unavailable）`,
        tracked.valueUsd.status === "ready" && tracked.coverage === "partial", JSON.stringify(tracked));
      check(`Q1 ${label}: ${code} trackedSales = 淨低真成交`,
        Math.abs((tracked.valueUsd.value ?? NaN) - expected[code].value) < 1e-6
          && tracked.count.value === expected[code].count,
        `${JSON.stringify(tracked)} vs ${JSON.stringify(expected[code])}`);
    }
    /* Q2 */
    const ninety = windows["90d"].changePct;
    const want = (CURRENT_PRICE / 17100 - 1) * 100;
    check(`Q2 ${label}: 90d 錨 06-26 $17,100 → −11.7%`,
      ninety.status === "ready" && Math.abs(ninety.value - want) < 1e-9 && ninety.value.toFixed(1) === "-11.7",
      JSON.stringify(ninety));
    check(`Q2 ${label}: 90d 唔係錨 06-27 $1,485`,
      Math.abs((ninety.value ?? 0) - (CURRENT_PRICE / 1485 - 1) * 100) > 1, JSON.stringify(ninety));
    check(`Q2 ${label}: 07-30 日價唔再係混咗尖刺嘅 $9,825.65`,
      Math.abs((jul30?.priceUsd ?? 0) - 9825.65) > 1, JSON.stringify(jul30));
  }
  // 兩條路要一模一樣：TS 橋只係 DB 表下一輪先跟得上嗰段時間嘅替身。
  check("Q1/Q2 receipt-only 同 DB 已剔走嘅 history 完全一樣",
    JSON.stringify(paths[0].history) === JSON.stringify(paths[1].history));
  check("Q1/Q2 receipt-only 同 DB 已剔走嘅窗完全一樣",
    JSON.stringify(paths[0].windows) === JSON.stringify(paths[1].windows));

  // 對照：冇隔離嘅話 06-27 $1,485 就係 90d 錨，淨係靠 MAX_WINDOW_RATIO 收埋（unavailable）。
  const raw = await runPath(buildHistories, "no-quarantine", allRows, new Map());
  check("對照：冇隔離 → 90d 錨到 06-27 被倍數閘收埋",
    raw.windows["90d"].changePct.value === null && raw.windows["90d"].changePct.status === "unavailable",
    JSON.stringify(raw.windows["90d"].changePct));

  /* Q3 mutation：剝走「成日扣晒就唔出點」，舊 bug 一定要返嚟。 */
  const GUARD = "        if (dayCount <= 0) continue;\n";
  const broken = producer.replace(GUARD, "");
  check("Q3 mutation: 成功剝走 guard", broken !== producer);
  const brokenBuilder = await loadBuilder(broken, "history-builder-broken.mjs");
  if (brokenBuilder) {
    const bad = await runPath(brokenBuilder, "mutant", allRows, receiptQuarantine);
    check("Q3 mutation: 冇 guard → 90d trackedSales 變 unavailable（舊 bug 捉到）",
      bad.windows["90d"].trackedSales.valueUsd.value === null, JSON.stringify(bad.windows["90d"].trackedSales));
    check("Q3 mutation: 冇 guard → 365d trackedSales 變 unavailable",
      bad.windows["365d"].trackedSales.valueUsd.value === null, JSON.stringify(bad.windows["365d"].trackedSales));
  } else {
    check("Q3 mutation: 抽到 mutant builder", false);
  }
}

rmSync(dir, { recursive: true, force: true });

if (failed.length) {
  console.error(`FAIL test-fe-sale-quarantine-day (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-sale-quarantine-day (${checks} checks)`);
