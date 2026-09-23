#!/usr/bin/env node
/*
 * 每個冇 fallback 嘅 var(--x) 都要有人定義（2026-09-23 A10）：CSS 入面 `--x:`，或者 TS／TSX 用
 * 字串 "--x"（style.setProperty、inline style object）。
 * 未定義嘅 var() 令成條 declaration 喺 computed-value time 無效，color 會變 unset（即係繼承），
 * 唔報錯、靜靜冇效果。當日搵到三個：footer 連結 hover 同方法區 <dt> 用 --fg、/box「顯示更多」
 * 用 --text，全部冇定義過（要嘅係 --ink）。
 * 負控制：掃描一定要搵到 ≥100 個 var() 同埋 --ink 嘅定義，唔係即係 pattern 壞咗、test 空轉。
 * run_all_tests.py glob `scripts/test-*.mjs`。
 */
import { readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const files = [];
(function walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) walk(path);
    else if (/\.(css|ts|tsx)$/.test(name)) files.push(path);
  }
})(join(ROOT, "apps/web/src"));

const defined = new Set();
const used = [];
for (const file of files) {
  /* 注釋換做空格但保留換行，行號先啱 */
  const text = readFileSync(file, "utf8").replace(/\/\*[\s\S]*?\*\//g, (block) => block.replace(/[^\n]/g, " "));
  for (const m of text.matchAll(/(--[\w-]+)\s*:/g)) defined.add(m[1]);
  for (const m of text.matchAll(/["'`](--[\w-]+)["'`]/g)) defined.add(m[1]);
  for (const m of text.matchAll(/var\(\s*(--[\w-]+)\s*\)/g)) {
    used.push({ name: m[1], where: `${relative(ROOT, file).replaceAll("\\", "/")}:${text.slice(0, m.index).split("\n").length}` });
  }
}

const failed = [];
if (used.length < 100) failed.push(`掃描淨係搵到 ${used.length} 個 var() —— pattern 壞咗，test 會空轉`);
if (!defined.has("--ink")) failed.push("掃描睇唔到 --ink 嘅定義 —— pattern 壞咗");
for (const use of used) {
  if (!defined.has(use.name)) failed.push(`undefined CSS custom property ${use.name} at ${use.where}`);
}
if (failed.length) {
  console.error("FAIL test-fe-css-vars-defined\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log(`PASS test-fe-css-vars-defined (${used.length} 個冇 fallback 嘅 var()，${defined.size} 個名有定義)`);
