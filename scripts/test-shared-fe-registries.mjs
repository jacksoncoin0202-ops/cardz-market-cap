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

const types = executeCommonJs(read("apps/web/src/lib/types.ts"), "types.ts");
const live = read("apps/web/src/lib/live-db-snapshot.ts");
const chart = read("apps/web/src/components/history-chart.tsx");
const server = read("apps/web/src/lib/server-snapshot.ts");
// The FE shows six windows; the first three are the producer windows the
// package registry owns, so they must be the registry, in order, same days.
if (JSON.stringify(types.producerWindows) !== JSON.stringify(schema.MARKET_WINDOWS)) {
  throw new Error(`app producer windows are not the package registry: ${JSON.stringify(types.producerWindows)}`);
}
if (JSON.stringify(types.marketWindows.slice(0, schema.MARKET_WINDOWS.length)) !== JSON.stringify(schema.MARKET_WINDOWS)) {
  throw new Error(`app window list does not start with the package registry: ${JSON.stringify(types.marketWindows)}`);
}
for (const window of types.marketWindows) {
  const want = schema.MARKET_WINDOW_DAYS[window] ?? Number.parseInt(window, 10);
  if (types.marketWindowDays[window] !== want) throw new Error(`app window ${window} => ${types.marketWindowDays[window]} days, want ${want}`);
}
// live-db-snapshot keeps its own window table; it must be the app table exactly.
const liveTable = /const WINDOWS = (\{[^}]*\}) as const;/.exec(live);
if (!liveTable) throw new Error("live DB window table not found");
const liveWindows = JSON.parse(liveTable[1].replace(/\s/g, ""));
if (JSON.stringify(liveWindows) !== JSON.stringify(types.marketWindowDays)) {
  throw new Error(`live DB window math drifted from the app table: ${liveTable[1]}`);
}
if (!chart.includes("marketWindowDays[period]")) throw new Error("history chart still has inline window math");
if (!server.includes("normalisePageSize(options?.pageSize)")) throw new Error("server snapshot still parses pageSize itself");
console.log("POSITIVE_OK FE windows and page-size normalization have one executable authority");
