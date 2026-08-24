#!/usr/bin/env node
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const sourcePath = new URL("../apps/web/src/lib/live-db-snapshot.ts", import.meta.url);
const source = readFileSync(sourcePath, "utf8");
const tree = ts.createSourceFile("live-db-snapshot.ts", source, ts.ScriptTarget.ES2022, true);
const declaration = tree.statements.find(
  (node) => ts.isFunctionDeclaration(node) && node.name?.text === "salesTotal",
);
if (!declaration) throw new Error("salesTotal declaration missing");
const isolated = `${declaration.getText(tree)}\nglobalThis.__salesTotal = salesTotal;`;
const javascript = ts.transpileModule(isolated, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
}).outputText;
const sandbox = {};
vm.runInNewContext(javascript, sandbox);
const salesTotal = sandbox.__salesTotal;
if (typeof salesTotal !== "function") throw new Error("salesTotal did not compile");

const end = Date.parse("2026-08-25T00:00:00Z");
const point = (at, value, count, coverage = "partial") => ({
  at,
  trackedSalesValueUsd: value,
  trackedSalesCount: count,
  salesCoverage: coverage,
  salesVerifiedZero: value === 0 && count === 0,
});

const healthy = salesTotal([
  point("2026-08-25T00:00:00Z", 20, 1),
  point("2026-08-24T00:00:00Z", 10, 2),
], end, 7);
if (JSON.stringify(healthy) !== JSON.stringify({
  value: 30, count: 3, asOf: "2026-08-25T00:00:00Z",
})) throw new Error(`healthy total/order failed: ${JSON.stringify(healthy)}`);

if (salesTotal([point("2026-08-25T00:00:00Z", null, null)], end, 7) !== null) {
  throw new Error("covered NULL day was converted to zero");
}
if (salesTotal([
  point("not-a-date", 99, 1),
  point("2026-08-25T00:00:00Z", 20, 1),
], end, 7) !== null) throw new Error("covered invalid-date day was silently dropped");
const verifiedZero = salesTotal([
  point("not-a-date", null, null, "unavailable"),
  point("2026-08-25T00:00:00Z", 0, 0),
], end, 7);
if (verifiedZero?.value !== 0 || verifiedZero?.count !== 0) {
  throw new Error(`verified numeric zero was lost: ${JSON.stringify(verifiedZero)}`);
}

const oldUnknownTotal = [{ trackedSalesValueUsd: null, trackedSalesCount: null }]
  .reduce((sum, item) => sum + (item.trackedSalesValueUsd ?? 0), 0);
if (oldUnknownTotal !== 0) throw new Error("negative fixture no longer proves old zero bias");
console.log("POSITIVE_OK sales windows preserve verified zero and return unknown for covered malformed days");
