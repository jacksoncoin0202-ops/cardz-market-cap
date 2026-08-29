#!/usr/bin/env node
/*
 * 熱力圖 0% 中立區。owner 2026-08-29：
 *   「0% 都會有正負分。0% 係唔應該有正負分，同埋 0% 係中立區，唔應該有色塊。」
 *
 * 根因（兩處同一形狀）：方向／正負號跟 raw float，顯示跟 toFixed。
 *   0.04 印 "+0.0%"，direction=up，aMin=0.78 綠格。
 *   −0.04 印 "−0.0%"，direction=down，紅格。
 * 預設 deadzone=0 喺 UI 寫 ±0%（中立區），code 卻當「關閉」。
 *
 * 呢個檔真係 import tileStyle / formatPercent（Node strip types），唔係 grep 算數。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 *
 * 負控制：舊邏輯（raw > 0 → up）寫死喺下面 OLD_*，要對住合約紅。
 * 真函數要綠。種返 OLD 落 production 呢個檔就紅（AGENTS.md 規矩 9）。
 */
import { registerHooks } from "node:module";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith(".") && !/\.[a-z]+$/i.test(specifier)) {
      try { return next(`${specifier}.ts`, context); } catch { /* 跌返原本 specifier */ }
    }
    return next(specifier, context);
  },
});

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition, detail = "") => {
  if (!condition) failed.push(detail ? `${label} —— ${detail}` : label);
};

const { DEFAULT_TILE, tileColors, tileStyle, fitTileLabel } = await import(
  `file://${join(ROOT, "apps/web/src/lib/tile-style.ts").replaceAll("\\", "/")}`
);
const { displayedChangePct, formatDeltaMoney, formatPercent, metricTone } = await import(
  `file://${join(ROOT, "apps/web/src/lib/format.ts").replaceAll("\\", "/")}`
);

const colors = tileColors(true, DEFAULT_TILE);
const st = (value) => tileStyle(value, 120, 160, colors, DEFAULT_TILE);
const ready = (value) => ({ value, status: "ready", asOf: "2026-08-29T00:00:00Z" });
const usdRates = { USD: 1 };

/* ── 舊邏輯（種蟲對照；唔准再出現喺 production） ── */
function oldDirection(value) {
  return value !== null && value > 0 ? "up" : value !== null && value < 0 ? "down" : "neutral";
}
function oldMove(value) {
  if (value === null || !Number.isFinite(value)) return null;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

check("負控制：0.04 舊 direction 係 up（所以呢條合約捉得到舊蟲）", oldDirection(0.04) === "up");
check("負控制：−0.04 舊 direction 係 down", oldDirection(-0.04) === "down");
check("負控制：0.04 舊 label 帶正號", oldMove(0.04) === "+0.0%");
check("負控制：−0.04 舊 label 帶負號", oldMove(-0.04) === "-0.0%");

/* ── displayedChangePct ── */
check("0.04 @1dp → 0", displayedChangePct(0.04, 1) === 0);
check("−0.04 @1dp → 0（唔係 −0）", Object.is(displayedChangePct(-0.04, 1), 0));
check("0.05 @1dp → 0.1（同 toFixed(1)）", displayedChangePct(0.05, 1) === 0.1);
check("0.004 @2dp → 0", displayedChangePct(0.004, 2) === 0);
check("0.1 @1dp 保持 0.1", displayedChangePct(0.1, 1) === 0.1);
check("−1.26 @1dp → −1.3", displayedChangePct(-1.26, 1) === -1.3);

/* ── 熱力圖格：顯示 0.0% = 中立，無色、無正負號 ── */
for (const raw of [0, 0.04, -0.04, 0.049, -0.049, -0]) {
  const tile = st(raw);
  check(`tile(${raw}) direction=neutral`, tile.direction === "neutral", `got ${tile.direction}`);
  check(`tile(${raw}) bg=neutral（冇紅綠）`, tile.bg === colors.neutral, `got ${tile.bg}`);
  check(`tile(${raw}) 冇色板`, tile.plate === null, `got ${tile.plate}`);
  check(`tile(${raw}) label 係 0.0% 無正負號`, tile.move === "0.0%", `got ${tile.move}`);
}

const up = st(0.1);
check("0.1% 仍然係升", up.direction === "up" && up.move === "+0.1%" && up.bg !== colors.neutral);
const down = st(-0.1);
check("−0.1% 仍然係跌", down.direction === "down" && down.move === "-0.1%" && down.bg !== colors.neutral);
const big = st(12.34);
check("大升幅 label 保留一位小數同正號", big.direction === "up" && big.move === "+12.3%");

const dz = { ...DEFAULT_TILE, deadzone: 0.5 };
const inBand = tileStyle(0.4, 120, 160, colors, dz);
check("deadzone ±0.5 之內都係中立色", inBand.direction === "neutral" && inBand.bg === colors.neutral);
const outBand = tileStyle(0.6, 120, 160, colors, dz);
check("deadzone 之外照升", outBand.direction === "up" && outBand.move === "+0.6%");

const fittedZero = fitTileLabel(0.04, 120, 160);
check("fitTileLabel(0.04) 唔出 +0.0%", fittedZero.move === "0.0%");
const fittedNull = fitTileLabel(null, 120, 160);
check("缺數仍然冇 label（資料累積中）", fittedNull.move === null);

/* ── 同一頁榜／preview：顯示 0.00% 無正負分、無升降 tone ── */
check("formatPercent(0) = 0.00%", formatPercent(ready(0), "en") === "0.00%");
check("formatPercent(0.004) = 0.00% 無正號", formatPercent(ready(0.004), "en") === "0.00%");
check("formatPercent(−0.004) = 0.00% 無負號", formatPercent(ready(-0.004), "en") === "0.00%");
check("formatPercent(0.01) 保持 +0.01%", formatPercent(ready(0.01), "en") === "+0.01%");
check("formatPercent(−0.01) 保持 −0.01%", formatPercent(ready(-0.01), "en") === "-0.01%");
check("metricTone(0.004) = neutral", metricTone(ready(0.004)) === "neutral");
check("metricTone(−0.004) = neutral", metricTone(ready(-0.004)) === "neutral");
check("metricTone(0.01) = positive", metricTone(ready(0.01)) === "positive");
check("metricTone(−0.01) = negative", metricTone(ready(-0.01)) === "negative");

const zeroDelta = formatDeltaMoney(ready(1_000_000), ready(0.004), "USD", usdRates, "en");
check("顯示 0.00% 就唔出 ±$ 分", zeroDelta === null, `got ${zeroDelta}`);
const realDelta = formatDeltaMoney(ready(1_000_000), ready(1), "USD", usdRates, "en");
check("非 0% 仍然有 ±$", typeof realDelta === "string" && realDelta.startsWith("+"), `got ${realDelta}`);

if (failed.length) {
  console.error(`FAIL test-fe-heatmap-zero-neutral (${failed.length})`);
  for (const line of failed) console.error(`  - ${line}`);
  process.exit(1);
}
console.log("PASS test-fe-heatmap-zero-neutral — 顯示 0% 中立、無正負分、無色塊");
process.exit(0);
