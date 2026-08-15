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

const box = derive.deriveBoxWindow(monthly, 150, asOf, "90d");
check("box 90d as-of ready", box.changePct.status === "ready" && Math.abs(box.changePct.value - 50) < 0.01);

if (failed.length) {
  console.error("FAIL derive-windows:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS derive-windows (as-of, no-invent, no-fake-cap, sales-sum)");
