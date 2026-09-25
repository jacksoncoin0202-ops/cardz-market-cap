#!/usr/bin/env node
// 長窗 as-of：月線唔使啱啱好 ±5 日；冇錨就 accumulating，唔作 0%；唔用今日 pop 假一年前市值。
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = join(ROOT, "apps/web/src/lib/derive-windows.ts");
const require = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require("typescript");
const { outputText } = ts.transpileModule(readFileSync(SOURCE, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const dir = join(tmpdir(), `cardz-derive-windows-${process.pid}`);
mkdirSync(dir, { recursive: true });
const file = join(dir, "derive-windows.mjs");
writeFileSync(file, outputText);
const derive = await import(pathToFileURL(file).href);
rmSync(dir, { recursive: true, force: true });

const failed = [];
const check = (label, condition) => {
  if (!condition) failed.push(label);
};

const monthly = [
  { at: "2026-05-01T00:00:00Z", priceUsd: 100 },
  { at: "2026-06-01T00:00:00Z", priceUsd: 110 },
  { at: "2026-07-01T00:00:00Z", priceUsd: 120 },
  { at: "2026-08-01T00:00:00Z", priceUsd: 150 },
];
const asOf = "2026-08-15T00:00:00Z";
const windows = derive.deriveLongWindows(monthly, 150, asOf);
const ninety = windows["90d"].changePct;
check("90d uses May 1 as-of not empty nearest band", ninety.status === "ready" && Math.abs(ninety.value - 50) < 0.01);
check("90d does not invent cap%", windows["90d"].marketCapChangePct.value === null);

const newCard = [
  { at: "2025-11-01T00:00:00Z", priceUsd: 400 },
  { at: "2026-08-01T00:00:00Z", priceUsd: 500 },
];
const year = derive.deriveLongWindows(newCard, 500, asOf)["365d"];
check("new 2025 card 1Y is accumulating not 0", year.changePct.value === null && year.changePct.status === "accumulating");

const empty = derive.deriveLongWindows([], 80, asOf)["365d"];
check("no history stays accumulating", empty.changePct.value === null && empty.changePct.status === "accumulating");

const salesHistory = [
  { at: "2026-08-10T00:00:00Z", priceUsd: 10, trackedSalesValueUsd: 20, trackedSalesCount: 2, salesCoverage: "partial" },
  { at: "2026-08-14T00:00:00Z", priceUsd: 12, trackedSalesValueUsd: 30, trackedSalesCount: 1, salesCoverage: "partial" },
];
const sales = derive.salesTotal(salesHistory, Date.parse(asOf), 7);
check("sales sum is day-by-day", sales && sales.value === 50 && sales.count === 3);

// BOX：1d–365d 全部窗只顯示 producer（sealed_operator）出嘅數；冇 changePct = withhold／冇錨，
// 唔准用冇 lane 嘅 historyDaily 自己再計（monthly 會計出 +50）。
const boxDir = join(tmpdir(), `cardz-box-view-${process.pid}`);
mkdirSync(boxDir, { recursive: true });
for (const name of ["types", "derive-windows", "box-view"]) {
  const { outputText: js } = ts.transpileModule(readFileSync(join(ROOT, `apps/web/src/lib/${name}.ts`), "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
  });
  writeFileSync(join(boxDir, `${name}.mjs`), js.replace(/from "\.\/([\w-]+)"/g, 'from "./$1.mjs"'));
}
const boxView = await import(pathToFileURL(join(boxDir, "box-view.mjs")).href);
rmSync(boxDir, { recursive: true, force: true });
const boxBlock = boxView.boxBlockView({
  asOf,
  products: [{
    id: "ptcg-en-test", rank: 1, game: "ptcg", lang: "en", group: "ptcg-en", setCode: "T", names: { en: "Test" },
    release: null, productKind: "booster_box", printWave: "1", status: "active",
    price: { usd: 150, kind: "market", asOf },
    windows: { "7d": { changePct: -3, soldCount: 1 }, "90d": { soldCount: 0 }, "180d": { changePct: 12.5, soldCount: 3 } },
    historyDaily: monthly.map((point) => ({ date: point.at.slice(0, 10), priceUsd: point.priceUsd, soldCount: 0 })),
  }],
});
const bw = boxBlock.products[0].windows;
check("box withheld 90d stays accumulating, not re-derived", bw["90d"].changePct.value === null && bw["90d"].changePct.status === "accumulating");
check("box baked 180d shown", bw["180d"].changePct.status === "ready" && bw["180d"].changePct.value === 12.5 && bw["180d"].soldCount === 3);
check("box missing 365d accumulating", bw["365d"].changePct.value === null && bw["365d"].changePct.status === "accumulating");
check("box baked 7d shown", bw["7d"].changePct.status === "ready" && bw["7d"].changePct.value === -3);

if (failed.length) {
  console.error("FAIL derive-windows:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS derive-windows (as-of, no-invent, no-fake-cap, sales-sum, box shows producer windows only)");
