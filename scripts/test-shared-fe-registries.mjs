#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const read = (name) => readFileSync(join(ROOT, name), "utf8");
const executeCommonJs = (source, name) => {
  const javascript = ts.transpileModule(source, {
    fileName: name,
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  const exports = {};
  vm.runInNewContext(javascript, { exports, module: { exports }, Object, Number, Math });
  return exports;
};

const schema = executeCommonJs(read("packages/market-data/src/schema.ts"), "schema.ts");
if (JSON.stringify(schema.MARKET_WINDOWS) !== JSON.stringify(["1d", "7d", "30d"])) {
  throw new Error(`market window list drifted: ${JSON.stringify(schema.MARKET_WINDOWS)}`);
}
if (JSON.stringify(schema.MARKET_WINDOW_DAYS) !== JSON.stringify({ "1d": 1, "7d": 7, "30d": 30 })) {
  throw new Error(`market window durations drifted: ${JSON.stringify(schema.MARKET_WINDOW_DAYS)}`);
}

const pagination = executeCommonJs(read("apps/web/src/lib/pagination.ts"), "pagination.ts");
const sizes = [
  [undefined, 200], [0, 1], [1.9, 1], [300, 300], [1000, 500], [Number.NaN, 200],
];
for (const [input, expected] of sizes) {
  const actual = pagination.normalisePageSize(input);
  if (actual !== expected) throw new Error(`page size ${String(input)} => ${actual}, want ${expected}`);
}

const types = read("apps/web/src/lib/types.ts");
const live = read("apps/web/src/lib/live-db-snapshot.ts");
const chart = read("apps/web/src/components/history-chart.tsx");
const server = read("apps/web/src/lib/server-snapshot.ts");
if (!types.includes("export const marketWindows = MARKET_WINDOWS")) throw new Error("app window list is not the package registry");
if (!types.includes("export const marketWindowDays = MARKET_WINDOW_DAYS")) throw new Error("app durations are not the package registry");
if (!live.includes("Object.entries(MARKET_WINDOW_DAYS)")) throw new Error("live DB window math is not registry-driven");
if (!chart.includes("marketWindowDays[period]")) throw new Error("history chart still has inline window math");
if (!server.includes("normalisePageSize(options?.pageSize)")) throw new Error("server snapshot still parses pageSize itself");
console.log("POSITIVE_OK FE windows and page-size normalization have one executable authority");
