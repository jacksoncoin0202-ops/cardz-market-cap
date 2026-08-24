#!/usr/bin/env node
/*
 * 混合錨政策（R6b，owner 2026-08-24 指示）—— 短窗同長窗**唔同**規矩，兩邊都要釘死。
 *
 *  ① 1d / 7d / 30d：**永遠只認真成交**。K 線／參考點（`market_price_observation`，
 *     喺公開 payload 叫 `historyReference`）一個都唔准做呢三個窗嘅錨。呢條係 owner
 *     hard rule：短窗係「最貼近最新市價」嘅嗰段，攞「有價冇成交」嘅圖表點做錨就係
 *     R6 原本要斬嗰個病（真成交 $2,000 → $3,100 應該 +55%，錨咗 K 線出 +8.8%）。
 *  ② 90d / 180d / 365d：有真成交錨就用真成交；**淨係**喺冇嘅時候先退去參考點。
 *     成交系列嘅最早一點中位數只有 74 日大（P25/P50/P75 = 47/74/113d），所以純成交
 *     嘅 180d 只錨得住 10.3% 卡；混合之後 99.3%。
 *  ③ 「現價」嗰邊永遠係最新一單真成交推出嚟嘅價（`currentPrice`），任何情況都唔變。
 *  ④ 參考點唔准生市值變幅：長窗本來就唔作市值（出街 history 冇每日 pop）。
 *
 * 條閘要有牙：呢度唔止斷言「短窗係 null」（拆咗後備一樣會 null，即係永遠 pass），
 * 仲會**種返個 bug 落去**——攞真 producer 源碼，將長窗嗰條界 `LONG_WINDOWS.has(code) ?`
 * 剝走，再行一次；剝走之後短窗一定要錨到 K 線（即係條界真係喺度做緊嘢）。
 *
 * 純靜態 + 真評估：唔開 DB、唔開瀏覽器，由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 *
 * Run: node scripts/test-fe-hybrid-window-anchor.mjs
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
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");

const producer = read(PRODUCER_REL);
const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-hybrid-anchor-${process.pid}`);
mkdirSync(dir, { recursive: true });
const transpile = (source, name) => {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  const file = join(dir, name);
  writeFileSync(file, outputText);
  return pathToFileURL(file).href;
};

// 真 `windowMetrics`（剝走 import 頭），唔喺 test 度抄一份錨點政策。
const moduleBody = producer.slice(producer.indexOf("const LOCALES = ["));
const load = (body, name) => import(transpile(`${body}\nexport { windowMetrics, chartLaneOf };\n`, name));
const { windowMetrics, chartLaneOf } = await load(moduleBody, "producer.mjs");

/* ─────────────────────────────────────────────────────────────
 * Fixture：一張卡，六個參考點，每個窗嘅錨位擺一個。
 * currentAsOf = 08-22，現價 3100（真成交推出嚟嗰個，永遠唔經 history）。
 * ───────────────────────────────────────────────────────────── */
const LANE = "pricecharting";
const CURRENT_PRICE = 3100;
const CURRENT_AS_OF = "2026-08-22T00:00:00Z";
const POP = 1000;

const refPoint = (date, price) => ({
  at: `${date}T00:00:00Z`,
  priceUsd: price,
  priceStatus: "ready",
  trackedSalesValueUsd: null,
  trackedSalesCount: null,
  salesCoverage: "unavailable",
  salesVerifiedZero: false,
  priceSourceCode: LANE,
  priceSourcePriority: 0,
});
const salePoint = (date, price) => ({
  at: `${date}T00:00:00Z`,
  priceUsd: price,
  priceStatus: "ready",
  trackedSalesValueUsd: price,
  trackedSalesCount: 1,
  salesCoverage: "partial",
  salesVerifiedZero: false,
  priceSourceCode: `${LANE}_sales`,
  priceSourcePriority: 4,
});

const reference = [
  refPoint("2026-08-21", 3000),   // 1d 錨帶
  refPoint("2026-08-15", 2900),   // 7d 錨帶
  refPoint("2026-07-23", 2800),   // 30d 錨帶
  refPoint("2026-05-20", 2000),   // 90d（target 05-24）之前最後一點
  refPoint("2026-02-01", 1500),   // 180d（target 02-23）之前最後一點
  refPoint("2025-06-01", 1000),   // 365d（target 2025-08-22）之前最後一點
];

const run = (history, ref, metrics = windowMetrics) => metrics(
  history, CURRENT_PRICE, POP, CURRENT_AS_OF, chartLaneOf(`${LANE}_sales`), ref,
);
const pct = (anchor) => (CURRENT_PRICE / anchor - 1) * 100;
const near = (value, want) => typeof value === "number" && Math.abs(value - want) < 1e-9;

/* ─────────────────────────────────────────────────────────────
 * A — 窗內只有參考點：1d / 7d / 30d 一定 null，長窗一定錨得住。
 * ───────────────────────────────────────────────────────────── */
{
  const w = run([], reference);
  for (const code of ["1d", "7d", "30d"]) {
    check(`A: ${code} 窗內只有 K 線 → changePct null / accumulating（唔准借參考點）`,
      w[code].changePct.value === null && w[code].changePct.status === "accumulating",
      JSON.stringify(w[code].changePct));
    check(`A: ${code} 市值變幅一樣要 null`,
      w[code].marketCapChangePct.value === null,
      JSON.stringify(w[code].marketCapChangePct));
  }
  const expect = { "90d": 2000, "180d": 1500, "365d": 1000 };
  for (const [code, anchor] of Object.entries(expect)) {
    check(`A: ${code} 冇真成交 → 退去參考錨 ${anchor}`,
      w[code].changePct.status === "ready" && near(w[code].changePct.value, pct(anchor)),
      JSON.stringify(w[code].changePct));
    check(`A: ${code} 錨同現價同一條 lane → 唔准 flag sourceSwitched`,
      w[code].changePct.sourceSwitched === false, JSON.stringify(w[code].changePct));
    check(`A: ${code} 參考錨唔准生市值變幅（出街 history 冇每日 pop）`,
      w[code].marketCapChangePct.value === null,
      JSON.stringify(w[code].marketCapChangePct));
  }
}

/* ─────────────────────────────────────────────────────────────
 * B — 真成交一出現：短窗即刻解得到，長窗改用真成交（唔再理參考點）。
 * ───────────────────────────────────────────────────────────── */
{
  const sales = [
    salePoint("2026-08-21", 2500),   // 1d
    salePoint("2026-08-15", 2400),   // 7d
    salePoint("2026-07-23", 2300),   // 30d
    salePoint("2026-05-20", 2200),   // 90d：同 K 線同一日，值唔同，用嚟分辨邊個贏
  ];
  const w = run(sales, reference);
  const expect = { "1d": 2500, "7d": 2400, "30d": 2300, "90d": 2200 };
  for (const [code, anchor] of Object.entries(expect)) {
    check(`B: ${code} 有真成交 → 錨真成交 ${anchor}`,
      w[code].changePct.status === "ready" && near(w[code].changePct.value, pct(anchor)),
      JSON.stringify(w[code].changePct));
  }
  check("B: 90d 真成交贏過同日 K 線（唔係 2000）",
    !near(w["90d"].changePct.value, pct(2000)), JSON.stringify(w["90d"].changePct));
  // 180d / 365d 嗰兩個錨位仍然冇真成交（最舊嗰單係 2026-05-20），照樣退參考點。
  check("B: 180d 仍然冇夠舊真成交 → 照退參考錨 1500",
    near(w["180d"].changePct.value, pct(1500)), JSON.stringify(w["180d"].changePct));
  check("B: 365d 仍然冇夠舊真成交 → 照退參考錨 1000",
    near(w["365d"].changePct.value, pct(1000)), JSON.stringify(w["365d"].changePct));
  // 短窗嘅市值變幅仍然由真成交錨推（inventCap 只服務短窗），證明後備冇滲入去。
  check("B: 1d 市值變幅 = 真成交錨 × POP",
    near(w["1d"].marketCapChangePct.value, pct(2500)),
    JSON.stringify(w["1d"].marketCapChangePct));
}

/* ─────────────────────────────────────────────────────────────
 * C — 冇參考點嗰陣行為同 R6 一模一樣（後備係加法，唔准改到舊路）。
 * ───────────────────────────────────────────────────────────── */
{
  const sales = [salePoint("2026-07-23", 2300)];
  const withRef = run(sales, []);
  const legacy = windowMetrics(sales, CURRENT_PRICE, POP, CURRENT_AS_OF, chartLaneOf(`${LANE}_sales`));
  check("C: 唔傳 reference 同傳空陣列 → 完全一樣",
    JSON.stringify(withRef) === JSON.stringify(legacy));
  check("C: 冇參考點 → 180d 照樣 null", withRef["180d"].changePct.value === null,
    JSON.stringify(withRef["180d"].changePct));
  check("C: 冇參考點 → 30d 照樣錨真成交", near(withRef["30d"].changePct.value, pct(2300)),
    JSON.stringify(withRef["30d"].changePct));
}

/* ─────────────────────────────────────────────────────────────
 * D — 種返個 bug：剝走長窗嗰條界，短窗就一定要錨到 K 線。
 *     剝走之後短窗仍然 null = 上面 A 組係空斷言，條閘冇牙。
 * ───────────────────────────────────────────────────────────── */
{
  const GUARD = "LONG_WINDOWS.has(code) ? latestBefore(reference,";
  const hits = moduleBody.split(GUARD).length - 1;
  check("D: 長窗後備嗰條界喺源碼入面有且只有一處（探針錨點唯一）", hits === 1, `搵到 ${hits} 處`);
  if (hits === 1) {
    const mutated = moduleBody.replace(GUARD, "true ? latestBefore(reference,");
    const { windowMetrics: broken } = await load(mutated, "producer-mutated.mjs");
    const w = run([], reference, broken);
    check("D: 剝走條界之後 1d 就錨到 K 線 3000（證明條界真係擋緊嘢）",
      near(w["1d"].changePct.value, pct(3000)), JSON.stringify(w["1d"].changePct));
    check("D: 剝走條界之後 7d 就錨到 K 線 2900",
      near(w["7d"].changePct.value, pct(2900)), JSON.stringify(w["7d"].changePct));
    check("D: 剝走條界之後 30d 就錨到 K 線 2800",
      near(w["30d"].changePct.value, pct(2800)), JSON.stringify(w["30d"].changePct));
  }
}

/* ─────────────────────────────────────────────────────────────
 * E — 參考點唔准滲入 `historyDaily`：兩條 series 分開出，唔准合併。
 * ───────────────────────────────────────────────────────────── */
{
  check("E: card 投影出兩條分開嘅 series",
    /historyDaily: history,/.test(producer) && /historyReference: reference,/.test(producer),
    "搵唔到 historyDaily / historyReference 兩條");
  check("E: `historyDaily` 唔准由 reference 餵",
    !/historyDaily: \[\s*\.\.\.\s*history/.test(producer) && !/history\.concat\(reference/.test(producer));
  check("E: 參考點嘅供應商代號唔准出街（同 historyDaily 一樣剝走）",
    /const reference = referenceDrafts[\s\S]{0,200}priceSourceCode: _[A-Za-z]+/.test(producer),
    "reference 投影冇剝走 priceSourceCode");
}

rmSync(dir, { recursive: true, force: true });

if (failed.length) {
  console.error(`FAIL test-fe-hybrid-window-anchor (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-hybrid-window-anchor (${checks} checks)`);
