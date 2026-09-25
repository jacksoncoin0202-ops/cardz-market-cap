#!/usr/bin/env node
/*
 * 卡變幅窗：冇觀察撐住嘅錨唔出街（2026-09-26，DADDY「可信 > 覆蓋」）——同盒
 * `pipelines/sealed_operator.py` `_anchor_withheld` 同一條規矩、同一組數。
 *
 * 卡 history 係 sale-only：target 嗰刻嘅價 = target 或之前最後一單同 lane 成交，
 * 即係由成交日 carry 到 target。盒量「點嘅日子 − 佢最新成交」，卡度就係 target 日 − 錨日：
 *   · 成交錨：> max(3 日, N/10)（carried）或者 > 30 日（SOLD_WINDOW_D，expired）→ unavailable
 *   · 參考點（K 線／market）：自己就係觀察，淨係 > 45 日（MARKET_MAX_AGE_D）→ unavailable
 *   · 唔出 = unavailable；唔改錨、唔揀第二個點（成交錨舊咗都唔准退去參考點）。
 * 實測 generation 6f0d6e09：生產 30d 錨有舊到 89 日、1d 有舊到 109 日；365d 參考錨有 246 日。
 *
 * 呢個檔守：
 *   ① 每個窗嘅容忍邊界兩邊（入／出）都答啱；
 *   ② 參考點唔受 N/10 管（90d 參考點 40 日照出），但受 45 日管；
 *   ③ 成交錨太舊唔准退去新鮮參考點；
 *   ④ 四個數同盒 Python 原檔一樣（盒改卡唔改 → 紅）；
 *   ⑤ 規矩喺 producer 得一個執行點；
 *   ⑥ 每樣都有毒：喺真 producer 源碼種毒再行，一定要紅。
 *
 * 純靜態 + 真評估：唔開 DB，由 run_all_tests.py 個 `scripts/test-*.mjs` glob 收。
 * Run: node scripts/test-fe-window-stale-anchor.mjs
 */
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(join(ROOT, rel), "utf8");
const producer = read("apps/web/src/lib/live-db-snapshot.ts");
const operatorPy = read("pipelines/sealed_operator.py");
const composePy = read("pipelines/sealed_price_compose.py");
const require_ = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require_("typescript");
const dir = join(tmpdir(), `cardz-stale-anchor-${process.pid}`);
mkdirSync(dir, { recursive: true });

const failed = [];
let checks = 0;
const check = (label, condition, detail) => {
  checks += 1;
  if (!condition) failed.push(detail ? `${label}: ${detail}` : label);
};

let loads = 0;
const load = async (source) => {
  const start = source.indexOf("const LOCALES = [");
  if (start < 0) throw new Error("producer lost `const LOCALES = [`");
  const { outputText } = ts.transpileModule(`${source.slice(start)}\nexport { windowMetrics, chartLaneOf };\n`, {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  loads += 1;
  const file = join(dir, `producer-${loads}.mjs`);
  writeFileSync(file, outputText);
  return import(pathToFileURL(file).href);
};

const LANE = "pricecharting";
const AS_OF = "2026-09-25T09:29:29Z";
const PRICE = 1000;
const OLD = 800; // 1.25 倍：所有窗嘅 MAX_WINDOW_RATIO 以內，淨係試錨舊唔舊
const DAY = 86_400_000;
const sale = (date, price) => ({
  at: `${date}T00:00:00Z`, priceUsd: price, priceStatus: "ready",
  trackedSalesValueUsd: price, trackedSalesCount: 1, salesCoverage: "partial",
  salesVerifiedZero: false, priceSourceCode: `${LANE}_sales`, priceSourcePriority: 4,
});
const ref = (date, price) => ({
  at: `${date}T00:00:00Z`, priceUsd: price, priceStatus: "ready",
  trackedSalesValueUsd: null, trackedSalesCount: null, salesCoverage: "unavailable",
  salesVerifiedZero: false, priceSourceCode: LANE, priceSourcePriority: 0,
});
const DAYS = { "1d": 1, "7d": 7, "30d": 30, "90d": 90, "180d": 180, "365d": 365 };
// fixture 自己核數：錨日離 target 幾多日（同 producer 一樣按 UTC 日計）。
const gapOf = (code, date) => Math.floor((Date.parse(AS_OF) - DAYS[code] * DAY) / DAY) - Math.floor(Date.parse(`${date}T00:00:00Z`) / DAY);
const near = (value, want) => typeof value === "number" && Math.abs(value - want) < 1e-9;
const pct = (then) => (PRICE / then - 1) * 100;
const withheld = (m) => m.value === null && m.status === "unavailable" && m.asOf === null && m.sourceSwitched === false;

// [名, 窗, 成交錨日 | null, 參考點日 | null, 要 ready?, 預期錨價, 要嘅 gap]
const FIXTURES = [
  ["1d-in", "1d", "2026-09-21", null, true, OLD, 3],
  ["1d-out", "1d", "2026-09-20", null, false, null, 4],
  ["7d-in", "7d", "2026-09-15", null, true, OLD, 3],
  ["7d-out", "7d", "2026-09-14", null, false, null, 4],
  ["30d-in", "30d", "2026-08-23", null, true, OLD, 3],
  ["30d-out", "30d", "2026-08-22", null, false, null, 4], // vid 24（#47）形狀
  ["90d-in", "90d", "2026-06-18", null, true, OLD, 9], // N/10 = 9
  ["90d-out", "90d", "2026-06-17", null, false, null, 10],
  ["180d-in", "180d", "2026-03-11", null, true, OLD, 18],
  ["180d-out", "180d", "2026-03-10", null, false, null, 19],
  ["365d-in", "365d", "2025-08-26", null, true, OLD, 30], // N/10 = 36.5，但成交錨 30 日就過期
  ["365d-out", "365d", "2025-08-25", null, false, null, 31],
  ["ref90-in", "90d", null, "2026-05-18", true, OLD, 40], // 參考點唔受 N/10 管
  ["ref365-in", "365d", null, "2025-08-11", true, OLD, 45],
  ["ref365-out", "365d", null, "2025-08-10", false, null, 46], // FFI 形狀（盒 2025-07-01 PC 點）
  ["no-fallback", "365d", "2025-08-25", "2025-09-20", false, null, 31], // 成交錨舊、參考點新 → 照唔出
];
for (const [name, code, saleDay, refDay, , , gap] of FIXTURES) {
  const got = gapOf(code, saleDay ?? refDay);
  check(`fixture ${name}: 錨離 target ${gap} 日`, got === gap, `實際 ${got}`);
}

const cases = ({ windowMetrics, chartLaneOf }) => {
  const out = {};
  for (const [name, code, saleDay, refDay, ready, anchorPrice] of FIXTURES) {
    const history = [...(saleDay ? [sale(saleDay, OLD)] : []), sale("2026-09-25", PRICE)];
    const reference = refDay ? [ref(refDay, name === "no-fallback" ? 900 : OLD)] : [];
    const w = windowMetrics(history, PRICE, 100, AS_OF, chartLaneOf(`${LANE}_sales`), reference)[code];
    const ok = ready
      ? w.changePct.status === "ready" && near(w.changePct.value, pct(anchorPrice))
      : withheld(w.changePct) && (code in { "1d": 1, "7d": 1, "30d": 1 } ? withheld(w.marketCapChangePct) : true);
    out[name] = [`${name}: ${code} ${ready ? `ready ${pct(anchorPrice).toFixed(1)}%` : "unavailable（唔出數、唔出 0%）"}`,
      ok, JSON.stringify({ changePct: w.changePct, marketCapChangePct: w.marketCapChangePct })];
  }
  // 1d：成交之後 16 日冇新單，以前出 0.0%（錨 = 頭條自己）；而家唔出。
  {
    const w = windowMetrics([sale("2026-09-08", PRICE)], PRICE, 100, AS_OF, chartLaneOf(`${LANE}_sales`), []);
    out.zero = ["zero: 1d 錨 = 頭條自己但 16 日前 → unavailable，唔出 0.0%",
      withheld(w["1d"].changePct), JSON.stringify(w["1d"].changePct)];
  }
  return out;
};

const real = cases(await load(producer));
for (const [label, ok, detail] of Object.values(real)) check(label, ok, detail);

// ④ 同盒一樣嘅四個數：由 Python 原檔讀，唔喺度抄。
const pyConst = (text, name) => {
  const m = text.match(new RegExp(`^${name} = ([0-9.]+)\\b`, "m"));
  return m ? Number(m[1]) : null;
};
const tsConst = (text, name) => {
  const m = text.match(new RegExp(`^const ${name} = ([0-9.]+);`, "m"));
  return m ? Number(m[1]) : null;
};
const PAIRS = [
  ["ANCHOR_CARRY_FLOOR_D", "op", "ANCHOR_CARRY_FLOOR_D"],
  ["ANCHOR_CARRY_FRACTION", "op", "ANCHOR_CARRY_FRACTION"],
  ["SALE_ANCHOR_MAX_AGE_D", "compose", "SOLD_WINDOW_D"],
  ["MARKET_ANCHOR_MAX_AGE_D", "compose", "MARKET_MAX_AGE_D"],
];
const drift = (tsText, opText, composeText) => PAIRS.filter(([tsName, where, pyName]) => {
  const card = tsConst(tsText, tsName);
  const box = pyConst(where === "op" ? opText : composeText, pyName);
  return card === null || box === null || card !== box;
}).map(([tsName]) => tsName);
check("④ 卡四個數 = 盒 Python 原檔", drift(producer, operatorPy, composePy).length === 0,
  drift(producer, operatorPy, composePy).join(","));
check("④ plant：盒 floor 改 4、卡唔改 → 紅",
  drift(producer, operatorPy.replace(/^ANCHOR_CARRY_FLOOR_D = 3$/m, "ANCHOR_CARRY_FLOOR_D = 4"), composePy).includes("ANCHOR_CARRY_FLOOR_D"));
check("④ plant：盒 MARKET_MAX_AGE_D 改 60、卡唔改 → 紅",
  drift(producer, operatorPy, composePy.replace(/^MARKET_MAX_AGE_D = 45$/m, "MARKET_MAX_AGE_D = 60")).includes("MARKET_ANCHOR_MAX_AGE_D"));

// ⑤ 一個執行點：定義一次、windowMetrics 叫一次。
const count = (text, needle) => text.split(needle).length - 1;
check("⑤ anchorWithheld 定義一次", count(producer, "function anchorWithheld(") === 1);
check("⑤ anchorWithheld 得一個 call site", count(producer, "anchorWithheld(") === 2, `${count(producer, "anchorWithheld(")}`);

// ⑥ Plants —— 喺真 producer 源碼種毒，指定 case 一定要紅。
const PLANTS = [
  ["拎走 stale（錨照用）", "const anchor = implausible || stale ? null : found;", "const anchor = implausible ? null : found;",
    ["1d-out", "7d-out", "30d-out", "90d-out", "180d-out", "365d-out", "ref365-out", "zero"]],
  ["stale 唔入 status（變 accumulating）", 'implausible || stale ? "unavailable" : "accumulating"', 'implausible ? "unavailable" : "accumulating"',
    ["1d-out", "30d-out", "zero"]],
  ["放鬆 floor 3 → 4", "const ANCHOR_CARRY_FLOOR_D = 3;", "const ANCHOR_CARRY_FLOOR_D = 4;", ["1d-out", "7d-out", "30d-out"]],
  ["放鬆 fraction 0.10 → 0.12", "const ANCHOR_CARRY_FRACTION = 0.10;", "const ANCHOR_CARRY_FRACTION = 0.12;", ["90d-out", "180d-out"]],
  ["放鬆成交過期 30 → 31", "const SALE_ANCHOR_MAX_AGE_D = 30;", "const SALE_ANCHOR_MAX_AGE_D = 31;", ["365d-out"]],
  ["放鬆參考過期 45 → 46", "const MARKET_ANCHOR_MAX_AGE_D = 45;", "const MARKET_ANCHOR_MAX_AGE_D = 46;", ["ref365-out"]],
  ["收緊一日（> 變 >=）", "return gapDays > Math.max(", "return gapDays >= Math.max(", ["1d-in", "7d-in", "30d-in", "90d-in", "180d-in"]],
  ["參考點當成交錨判", 'saleAnchor ? "sale" : "reference"', '"sale"', ["ref90-in"]],
  ["成交錨舊咗就退去參考點",
    "const saleAnchor = latestBefore(history, targetMs + 1, currentSource, laneSales);",
    "const saleAnchor0 = latestBefore(history, targetMs + 1, currentSource, laneSales);\n"
    + "    const saleAnchor = saleAnchor0 && !anchorWithheld(saleAnchor0.at, targetMs, daysBack, \"sale\") ? saleAnchor0 : null;",
    ["no-fallback"]],
];
for (const [what, from, to, mustRed] of PLANTS) {
  const hits = count(producer, from);
  check(`⑥ plant 錨點唯一：${what}`, hits === 1, `搵到 ${hits} 處`);
  if (hits !== 1) continue;
  const planted = cases(await load(producer.replace(from, to)));
  const stillGreen = mustRed.filter((name) => planted[name][1]);
  check(`⑥ plant「${what}」→ ${mustRed.join("/")} 紅`, stillGreen.length === 0, `仲綠：${stillGreen.join(",")}`);
}

rmSync(dir, { recursive: true, force: true });
if (failed.length) {
  console.error(`FAIL test-fe-window-stale-anchor (${failed.length}/${checks})`);
  for (const f of failed) console.error(` - ${f}`);
  process.exit(1);
}
console.log(`PASS test-fe-window-stale-anchor (${checks} checks, ${PLANTS.length} producer plants red + 2 box-drift plants red)`);
