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
 * F — 錨一定同現價同一條 lane（2026-09-25）：另一個市場嘅成交、多源嗰日嘅
 *     `exact_psa10_sales` 均價都唔准做錨。實測 Mimikyu 180d 錨咗 PC 一單 $95
 *     對 SNKRDUNK 現價 $21,063，出街 +22,072%。
 *     種返個 bug（剝走 lane 閘）之後就一定要錨返另一條 lane，證明閘真係擋緊嘢。
 * ───────────────────────────────────────────────────────────── */
{
  const other = (date, price, code = "snkrdunk_sales") => ({ ...salePoint(date, price), priceSourceCode: code });
  const sales = [
    salePoint("2026-07-20", 2300),                   // 30d 帶內，同 lane
    other("2026-07-23", 2000),                       // 30d 帶內，另一條 lane，仲近 target
    other("2026-08-21", 2600, "exact_psa10_sales"),  // 1d target 嗰日淨係得多源均價
    salePoint("2026-02-10", 1500),                   // 180d（target 02-23）之前，同 lane
    other("2026-02-20", 1200),                       // 180d 之前最後一點，但係另一條 lane
  ];
  const w = run(sales, []);
  check("F: 30d 揀同 lane 嗰單 2300，唔揀近啲嗰單另一條 lane 嘅 2000",
    w["30d"].changePct.status === "ready" && near(w["30d"].changePct.value, pct(2300)),
    JSON.stringify(w["30d"].changePct));
  check("F: 錨同 lane → 唔准 flag sourceSwitched", w["30d"].changePct.sourceSwitched === false,
    JSON.stringify(w["30d"].changePct));
  // 2026-09-26 as-of（冇 ±帶）：跳過多源嗰日，錨返 target 或之前最後一單同 lane（07-20 2300）。
  check("F: 多源嗰日嘅均價唔准做 1d 錨 → 錨返 ≤ target 最後一單同 lane 嘅 2300",
    w["1d"].changePct.status === "ready" && near(w["1d"].changePct.value, pct(2300)),
    JSON.stringify(w["1d"].changePct));
  check("F: 180d 跳過另一條 lane 嘅最後一點，錨返同 lane 嘅 1500",
    near(w["180d"].changePct.value, pct(1500)), JSON.stringify(w["180d"].changePct));

  // 冇 source 嘅點（2026-09-25 QC）：現價有 lane 嗰陣佢講唔出自己係同一條 lane，一樣唔准做錨；
  // 現價冇 lane（已剝碼 payload）就照舊唔揀 lane。
  const bare = [salePoint("2026-07-18", 2300), { ...salePoint("2026-07-23", 2000), priceSourceCode: null }];
  const lane = run(bare, []);
  check("F: 冇 source 嘅點唔准做錨 → 30d 錨返同 lane 嘅 2300",
    near(lane["30d"].changePct.value, pct(2300)), JSON.stringify(lane["30d"].changePct));
  const laneless = windowMetrics(bare, CURRENT_PRICE, POP, CURRENT_AS_OF, null, []);
  check("F: 現價冇 lane → 冇 source 嘅點照用（2000）",
    near(laneless["30d"].changePct.value, pct(2000)), JSON.stringify(laneless["30d"].changePct));
  // 同 lane 但冇 priceUsd 嘅點退去成交均價：標返自己條 lane，唔准變 sourceSwitched
  //（validate_daily_release.py 見到 ready + sourceSwitched 會擋成個 release）。
  const averaged = [{ ...salePoint("2026-07-23", 2000), priceUsd: null }];
  const avg = run(averaged, []);
  check("F: 同 lane 嘅成交均價後備 → 錨 2000，sourceSwitched false",
    near(avg["30d"].changePct.value, pct(2000)) && avg["30d"].changePct.sourceSwitched === false,
    JSON.stringify(avg["30d"].changePct));
  const AVG_LABEL = 'sourceCode: currentSource ? source : "exact_psa10_sales"';
  const avgHits = moduleBody.split(AVG_LABEL).length - 1;
  check("F: 均價標籤喺源碼入面有且只有一處（探針錨點唯一）", avgHits === 1, `搵到 ${avgHits} 處`);
  if (avgHits === 1) {
    const { windowMetrics: relabel } = await load(
      moduleBody.replace(AVG_LABEL, 'sourceCode: "exact_psa10_sales"'), "producer-avg-label.mjs");
    const r = run(averaged, [], relabel)["30d"].changePct;
    check("F: 均價標返 exact_psa10_sales 就會 sourceSwitched（證明標籤真係要緊）", r.sourceSwitched === true,
      JSON.stringify(r));
  }

  const LANE_GUARD = "if (currentSource && (!source || chartLaneOf(source) !== currentSource)) return null;";
  const OLD_GUARD = "if (source && currentSource && chartLaneOf(source) !== currentSource) return null;";
  const hits = moduleBody.split(LANE_GUARD).length - 1;
  check("F: lane 閘喺源碼入面有且只有一處（探針錨點唯一）", hits === 1, `搵到 ${hits} 處`);
  if (hits === 1) {
    const { windowMetrics: broken } = await load(moduleBody.replace(LANE_GUARD, ""), "producer-no-lane.mjs");
    const b = run(sales, [], broken);
    check("F: 剝走 lane 閘之後 30d 就錨到另一條 lane 嘅 2000（證明閘真係擋緊嘢）",
      near(b["30d"].changePct.value, pct(2000)), JSON.stringify(b["30d"].changePct));
    check("F: 剝走 lane 閘之後 1d 就錨到多源均價 2600",
      near(b["1d"].changePct.value, pct(2600)), JSON.stringify(b["1d"].changePct));
    const { windowMetrics: loose } = await load(moduleBody.replace(LANE_GUARD, OLD_GUARD), "producer-old-lane.mjs");
    const o = run(bare, [], loose);
    check("F: 換返舊閘（放過冇 source 嘅點）之後 30d 就錨到 2000（證明新閘擋緊嘢）",
      near(o["30d"].changePct.value, pct(2000)), JSON.stringify(o["30d"].changePct));
  }
}

/* ─────────────────────────────────────────────────────────────
 * G — 離晒譜嘅變幅唔出街（2026-09-25）：錨同現價差過窗口上限（升跌對稱），
 *     changePct 同 marketCapChangePct 都係 null / unavailable。實測 Latias 90d
 *     錨咗一單 $1,485（前後兩日 $17,100／$17,700），出街 +916.8%。
 * ───────────────────────────────────────────────────────────── */
{
  const withheld = (metric) => metric.value === null && metric.status === "unavailable"
    && metric.asOf === null && metric.sourceSwitched === false;
  const rise = run([salePoint("2026-05-20", 300)], []);   // 90d：3100 / 300 = 10.3 倍 > 4
  check("G: 90d 升 10 倍 → 唔出街，unavailable", withheld(rise["90d"].changePct),
    JSON.stringify(rise["90d"].changePct));
  const edge = run([salePoint("2026-05-20", 800)], []);   // 90d：3.875 倍 ≤ 4
  check("G: 90d 3.9 倍仲喺上限入面 → 照出",
    edge["90d"].changePct.status === "ready" && near(edge["90d"].changePct.value, pct(800)),
    JSON.stringify(edge["90d"].changePct));
  const drop = run([salePoint("2026-07-23", 10000)], []); // 30d：跌到 0.31 倍，即係 3.2 倍 > 3
  check("G: 30d 跌 69% → 唔出街，unavailable", withheld(drop["30d"].changePct),
    JSON.stringify(drop["30d"].changePct));
  check("G: 同一個錨嘅市值變幅一齊唔出", withheld(drop["30d"].marketCapChangePct),
    JSON.stringify(drop["30d"].marketCapChangePct));
  const ref = run([], [refPoint("2025-06-01", 400)]);     // 365d 參考錨：7.75 倍 > 7
  check("G: 長窗參考錨一樣受上限管", withheld(ref["365d"].changePct),
    JSON.stringify(ref["365d"].changePct));
  // 現價或錨 ≤ 0 算唔出倍數（2026-09-25 QC）：一樣唔出街，唔係 −100% 或者 accumulating。
  const at = (anchor, current) => windowMetrics([salePoint("2026-07-23", anchor)], current, POP,
    CURRENT_AS_OF, chartLaneOf(`${LANE}_sales`), [])["30d"].changePct;
  check("G: 現價 −5 → 唔出街，unavailable", withheld(at(1000, -5)), JSON.stringify(at(1000, -5)));
  check("G: 現價同錨都係 0 → 唔出街，unavailable", withheld(at(0, 0)), JSON.stringify(at(0, 0)));
  const NONPOSITIVE = "currentPrice <= 0 || found.priceUsd <= 0";
  const npHits = moduleBody.split(NONPOSITIVE).length - 1;
  check("G: ≤ 0 閘喺源碼入面有且只有一處（探針錨點唯一）", npHits === 1, `搵到 ${npHits} 處`);
  if (npHits === 1) {
    const { windowMetrics: open } = await load(moduleBody.replace(NONPOSITIVE, "false"), "producer-no-nonpositive.mjs");
    const n = open([salePoint("2026-07-23", 1000)], -5, POP, CURRENT_AS_OF, chartLaneOf(`${LANE}_sales`), [])["30d"].changePct;
    check("G: 拆咗 ≤ 0 閘之後現價 −5 就出返 ready（證明閘真係擋緊嘢）", n.status === "ready", JSON.stringify(n));
  }

  const LIMITS = '"1d": 3, "7d": 3, "30d": 3, "90d": 4, "180d": 5, "365d": 7,';
  const hits = moduleBody.split(LIMITS).length - 1;
  check("G: 上限表喺源碼入面有且只有一處（探針錨點唯一）", hits === 1, `搵到 ${hits} 處`);
  if (hits === 1) {
    const loose = '"1d": 1e9, "7d": 1e9, "30d": 1e9, "90d": 1e9, "180d": 1e9, "365d": 1e9,';
    const { windowMetrics: broken } = await load(moduleBody.replace(LIMITS, loose), "producer-no-limit.mjs");
    const b = run([salePoint("2026-05-20", 300)], [], broken);
    check("G: 拆咗上限之後 90d 就出返 +933%（證明上限真係擋緊嘢）",
      b["90d"].changePct.status === "ready" && near(b["90d"].changePct.value, pct(300)),
      JSON.stringify(b["90d"].changePct));
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
  // 2026-08-24 收尾改：投影由 `_x` rest-exclusion 改咗做顯式逐欄抄（eslint ratchet
  // 基線 8 已滿，每個棄用 destructure 計一個 warning）。呢條照舊釘「供應商代號唔准
  // 出街」，只係認新形狀：要 DailyHistoryPoint 回型 + 逐欄抄，唔准 spread draft、
  // 唔准出現 priceSource* key。
  const refProjection = (producer.match(/const reference = referenceDrafts[\s\S]{0,700}?\}\)\);/) ?? [""])[0];
  check("E: 參考點嘅供應商代號唔准出街（顯式逐欄抄：冇 spread、冇 priceSource* key）",
    /\.map\(\(draft\): DailyHistoryPoint => \(\{/.test(refProjection)
      && refProjection.includes("at: draft.at")
      && !refProjection.includes("...draft")
      && !/priceSource(Code|Priority)/.test(refProjection),
    "reference 投影要顯式逐欄抄（DailyHistoryPoint 回型），唔准 spread draft 或者帶 priceSource* key");
}

rmSync(dir, { recursive: true, force: true });

if (failed.length) {
  console.error(`FAIL test-fe-hybrid-window-anchor (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-hybrid-window-anchor (${checks} checks)`);
