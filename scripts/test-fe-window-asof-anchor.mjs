#!/usr/bin/env node
/*
 * 窗變幅 = 現價 對 「(now − 窗) 嗰刻當時嘅價」（as-of）—— 2026-09-26 價格審計。
 *
 * 「target 嗰刻當時嘅價」= target 或之前最後一單同 lane 成交。1d／7d／30d／長窗
 * 同一條規矩，冇 ±帶（coordinator 2026-09-26 拍板）：
 *   · 錨唔准遲過 target。以前 nearestPrice 喺 ±2/3/5 日揀最近一點，會揀到窗入面嘅
 *     成交（連頭條價自己嗰日）：rank 4（vid 2）1d 錨咗 09-25 自己個日均價出 −0.92%。
 *   · 窗入面冇成交 → 錨到頭條價自己 → 0%，係定義，唔准灰、唔准變「同上一單比」。
 *     vid 24（rank 47）30d：08-22 成交 $15,600，target 08-26 → 0.0%。
 *   · 同 lane（68b82c75）同 MAX_WINDOW_RATIO 照企。
 *   · 同 lane 睇 lane 自己嗰日嘅成交，唔睇合併日點個標籤（2026-09-26）：多源日（PC + SNK
 *     同日）入面 lane 自己嗰部分照做錨；混合均價照唔准。#60（vid 42）現價係 09-18 一單
 *     PC $1,291.05，嗰日 SNK 都有單 → 以前 7d 退去 09-01 $760 出 +69.9%；#64（vid 37）
 *     日日多源 → 7d 退咗成個月去 08-25 出 −28.3%。
 *
 * 條閘有牙：攞真 producer 源碼種毒再行一次 ——
 *   ① 塞返 1d／7d 嘅 ±帶（帶外就灰）→ F 一定紅；
 *   ② 錨可以遲過 target（look-ahead 3 日）→ B／C 一定紅；
 *   ③ 退返按標籤跳多源日 → L60／L64 一定紅；
 *   ④ 多源日冇 lane 自己嗰部分就用混合均價（放鬆 lane 閘）→ L60b 一定紅。
 *
 * 純靜態 + 真評估：唔開 DB，由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 * Run: node scripts/test-fe-window-asof-anchor.mjs
 */
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const producer = readFileSync(join(ROOT, "apps/web/src/lib/live-db-snapshot.ts"), "utf8");
const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-asof-anchor-${process.pid}`);
mkdirSync(dir, { recursive: true });

const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};

const load = async (source, name) => {
  const body = source.slice(source.indexOf("const LOCALES = ["));
  const { outputText } = ts.transpileModule(`${body}\nexport { windowMetrics, chartLaneOf };\n`, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const file = join(dir, name);
  writeFileSync(file, outputText);
  return import(pathToFileURL(file).href);
};

const LANE = "pricecharting";
const sale = (date, price, lane = LANE) => ({
  at: `${date}T00:00:00Z`, priceUsd: price, priceStatus: "ready",
  trackedSalesValueUsd: price, trackedSalesCount: 1, salesCoverage: "partial",
  salesVerifiedZero: false, priceSourceCode: `${lane}_sales`, priceSourcePriority: 4,
});
const near = (value, want) => typeof value === "number" && Math.abs(value - want) < 1e-6;
const pct = (now, then) => (now / then - 1) * 100;

const cases = ({ windowMetrics, chartLaneOf }) => {
  const run = (history, price, asOf) => windowMetrics(history, price, 100, asOf, chartLaneOf(`${LANE}_sales`), []);
  const out = {};
  // B — 7d：target 09-18T09:29。09-19 近過 09-16 但遲過 target → 錨 09-16。
  {
    const w = run([sale("2026-09-16", 1000), sale("2026-09-19", 1200), sale("2026-09-22", 1300)],
      1300, "2026-09-25T09:29:29Z");
    out.B = ["B: 7d 錨 target 嗰刻嘅價（09-16 $1,000 → +30%），唔係 09-19",
      near(w["7d"].changePct.value, 30), JSON.stringify(w["7d"].changePct)];
  }
  // C — 1d：頭條同日成交（09-25），target 09-24T08:06 → 錨 09-10 $2,600，唔准同自己比。
  {
    const w = run([sale("2026-09-10", 2600), sale("2026-09-25", 2742.84)], 2717.556721, "2026-09-25T08:06:00Z");
    out.C = ["C: 1d 錨 target 前最後一單（09-10），唔係頭條自己嗰日",
      near(w["1d"].changePct.value, pct(2717.556721, 2600)), JSON.stringify(w["1d"].changePct)];
  }
  // D — 30d：target 08-26T09:29。08-29 遲過 target；≤ target 最後一點係 07-01。
  {
    const w = run([sale("2026-07-01", 900), sale("2026-08-29", 950), sale("2026-09-20", 1000)],
      1000, "2026-09-25T09:29:29Z");
    out.D = ["D: 30d 錨 ≤ target 最後一點（07-01 $900 → +11.11%）",
      near(w["30d"].changePct.value, pct(1000, 900)), JSON.stringify(w["30d"].changePct)];
  }
  // F — 窗入面冇成交、上一單遠過任何 ±帶：1d／7d／30d 全部 0% ready（唔准灰）。
  {
    const w = run([sale("2026-08-01", 800), sale("2026-09-01", 1000)], 1000, "2026-09-25T09:29:29Z");
    for (const code of ["1d", "7d"]) {
      out[`F${code}`] = [`F: ${code} 窗入面冇成交 → 0.0% ready（唔准灰）`,
        w[code].changePct.value === 0 && w[code].changePct.status === "ready", JSON.stringify(w[code].changePct)];
    }
  }
  return out;
};

const real = cases(await load(producer, "producer.mjs"));
for (const [label, ok, detail] of Object.values(real)) check(label, ok, detail);

// 多源日：lane 自己嗰部分成交（第 7 個參數：日期 → lane → 合計），bake 由成交 view 按 lane 讀。
const laneDays = (entries) => new Map(Object.entries(entries).map(([date, lanes]) => [date,
  Object.fromEntries(Object.entries(lanes).map(([lane, [value, count]]) => [lane, { value, count }]))]));
const laneCases = ({ windowMetrics, chartLaneOf }) => {
  const out = {};
  // L60 — vid 42 形狀：現價 = 09-18 一單 PC $1,291.05；09-18 合併點係多源（PC + SNK）均價 $1,134.03。
  const h60 = [sale("2026-09-01", 760), sale("2026-09-15", 1035, "snkrdunk"),
    sale("2026-09-18", 1134.03, "exact_psa10"), sale("2026-09-24", 837, "snkrdunk")];
  const l60 = { "2026-09-01": { pricecharting: [760, 1] }, "2026-09-15": { snkrdunk: [1035, 1] },
    "2026-09-18": { pricecharting: [1291.05, 1], snkrdunk: [977.01, 1] }, "2026-09-24": { snkrdunk: [837, 1] } };
  {
    const w = windowMetrics(h60, 1291.05, 100, "2026-09-25T09:29:29Z", chartLaneOf("pricecharting_sales"), [], laneDays(l60));
    out.L60 = ["L60: 7d 錨 09-18 PC 自己嗰單（多源日）→ 0.0%，唔係 09-01 $760 +69.9%",
      w["7d"].changePct.value === 0 && w["7d"].changePct.status === "ready" && w["7d"].changePct.sourceSwitched === false,
      JSON.stringify(w["7d"].changePct)];
  }
  // L60b — 同一日冇 PC 嗰部分：唔准攞混合均價 $1,134.03，錨返 ≤ target 最後一單 PC（09-01 $760）。
  {
    const l60b = { ...l60, "2026-09-18": { snkrdunk: [977.01, 1], ebay: [1291.05, 1] } };
    const w = windowMetrics(h60, 1291.05, 100, "2026-09-25T09:29:29Z", chartLaneOf("pricecharting_sales"), [], laneDays(l60b));
    out.L60b = ["L60b: 多源日冇 lane 自己嗰部分 → 唔准用混合均價，錨 09-01 $760",
      near(w["7d"].changePct.value, pct(1291.05, 760)), JSON.stringify(w["7d"].changePct)];
  }
  // L64 — vid 37 形狀：SNK 現價 $384.88；09-17／09-18 多源，SNK 自己 09-18 係 2 單共 $810（均 $405）。
  {
    const h64 = [sale("2026-08-25", 536.69, "snkrdunk"), sale("2026-09-17", 411, "exact_psa10"),
      sale("2026-09-18", 418, "exact_psa10"), sale("2026-09-25", 388, "snkrdunk")];
    const l64 = { "2026-08-25": { snkrdunk: [536.69, 1] }, "2026-09-17": { snkrdunk: [405, 1], pricecharting: [417, 1] },
      "2026-09-18": { snkrdunk: [810, 2], pricecharting: [434, 1] }, "2026-09-25": { snkrdunk: [388, 1] } };
    const w = windowMetrics(h64, 384.882766, 100, "2026-09-25T08:06:00Z", chartLaneOf("snkrdunk_sales"), [], laneDays(l64));
    out.L64 = ["L64: 7d 錨 09-18 SNK 自己嗰部分（$405），唔係退去 08-25 $536.69 −28.3%",
      near(w["7d"].changePct.value, pct(384.882766, 405)) && w["7d"].changePct.sourceSwitched === false,
      JSON.stringify(w["7d"].changePct)];
  }
  return out;
};
for (const [label, ok, detail] of Object.values(laneCases(await load(producer, "producer-lane.mjs")))) check(label, ok, detail);

// A — vid 24 rank 47 形狀：30d 窗入面冇成交 → 0.0%，唔准變「同上一單比」−12.6%。
const { windowMetrics, chartLaneOf } = await load(producer, "producer-a.mjs");
{
  const w = windowMetrics([sale("2026-08-01", 17850), sale("2026-08-22", 15600)],
    15600, 100, "2026-09-25T09:29:29Z", chartLaneOf(`${LANE}_sales`), []);
  check("A: 30d 窗入面冇成交 → 0.0% ready", w["30d"].changePct.value === 0 && w["30d"].changePct.status === "ready",
    JSON.stringify(w["30d"].changePct));
}
// E — 68b82c75 照企：≤ target 得另一條 lane 嘅成交 → 唔准做錨（照灰）。
{
  const w = windowMetrics([sale("2026-09-17", 500, "snkrdunk"), sale("2026-09-22", 1300)],
    1300, 100, "2026-09-25T09:29:29Z", chartLaneOf(`${LANE}_sales`), []);
  check("E: 7d 唔准錨另一條 lane", w["7d"].changePct.value === null, JSON.stringify(w["7d"].changePct));
}
// G — MAX_WINDOW_RATIO 照企：7d 同 lane 錨 $300 對現價 $1,300（4.3×）→ unavailable。
{
  const w = windowMetrics([sale("2026-09-10", 300), sale("2026-09-22", 1300)],
    1300, 100, "2026-09-25T09:29:29Z", chartLaneOf(`${LANE}_sales`), []);
  check("G: 7d 超過 3× 唔出街（unavailable）",
    w["7d"].changePct.value === null && w["7d"].changePct.status === "unavailable", JSON.stringify(w["7d"].changePct));
}

// Plants — 喺真 producer 源碼上面種毒。
const ANCHOR = "const saleAnchor = latestBefore(history, targetMs + 1, currentSource, laneSales);";
check("plant 錨點：producer 有單一 as-of 錨", producer.includes(ANCHOR));
const band = cases(await load(producer.replace(ANCHOR, [
  "const asOfAnchor = latestBefore(history, targetMs + 1, currentSource, laneSales);",
  "const bandDays = code === \"1d\" ? 2 : code === \"7d\" ? 3 : Number.POSITIVE_INFINITY;",
  "const saleAnchor = asOfAnchor && new Date(asOfAnchor.at).valueOf() >= targetMs - bandDays * 86_400_000 ? asOfAnchor : null;",
].join("\n    ")), "plant-band.mjs"));
check("plant ①：塞返 1d ±帶 → F 紅", !band.F1d[1]);
check("plant ①：塞返 7d ±帶 → F 紅", !band.F7d[1]);
const ahead = cases(await load(producer.replace(ANCHOR,
  "const saleAnchor = latestBefore(history, targetMs + 3 * 86_400_000, currentSource, laneSales);"), "plant-ahead.mjs"));
check("plant ②：錨遲過 target → B 紅", !ahead.B[1]);
check("plant ②：錨遲過 target → C 紅", !ahead.C[1]);
const LANE_BRANCH = "if (currentSource && laneDay) {";
check("plant 錨點：anchorCandidate 有單一 lane 自己成交分支", producer.split(LANE_BRANCH).length === 2);
const byLabel = laneCases(await load(producer.replace(LANE_BRANCH, "if (false) {"), "plant-label.mjs"));
check("plant ③：退返按標籤跳多源日 → L60 紅", !byLabel.L60[1]);
check("plant ③：退返按標籤跳多源日 → L64 紅", !byLabel.L64[1]);
const OWN_MISS = "      : null;\n  }\n  const source =";
check("plant 錨點：lane 分支冇 lane 自己成交就 null", producer.split(OWN_MISS).length === 2);
const blended = laneCases(await load(producer.replace(OWN_MISS,
  "      : point.priceUsd === null ? null : { at: point.at, priceUsd: point.priceUsd, sourceCode: `${currentSource}_sales` };\n  }\n  const source ="),
  "plant-blended.mjs"));
check("plant ④：多源日用混合均價 → L60b 紅", !blended.L60b[1]);

rmSync(dir, { recursive: true, force: true });
if (failed.length) {
  console.error(`FAIL test-fe-window-asof-anchor (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-window-asof-anchor (${checks} checks)`);
