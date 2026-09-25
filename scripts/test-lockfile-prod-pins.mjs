#!/usr/bin/env node
// 守住「裝 dev 工具唔准靜靜改咗 production build input」。
//
// 點解要有：2026-08-17 補 `eslint` + `eslint-config-next` 兩個 devDeps 嗰陣，npm 順手
// 將 **hoisted** 嘅 `caniuse-lite` 由 1.0.30001806 升到 1.0.30001809、
// `baseline-browser-mapping` 由 2.11.0 升到 2.11.14。呢兩個唔係 dev-only：
// `node_modules/next` 自己 depend 住佢哋（`caniuse-lite ^1.0.30001579`、
// `baseline-browser-mapping ^2.9.19`），佢哋餵 browserslist，即係 `next build` 轉譯
// 用嘅瀏覽器目標表。`apps/web/Dockerfile:17` 係裸 `npm ci`，所以下一次 production
// build 就會食新表——一個冇人打算做、亦冇人驗過嘅改動，落喺一個已經出咗街嘅站。
//
// 修法係將 hoisted（= prod 睇到嗰個）鎖返舊版，新版擺落
// `node_modules/browserslist/node_modules/*`（`browserslist` 係 dev-only，佢先要新版）。
// 呢個 tree shape npm 唔會自己維持——下次有人 `npm i <乜嘢 devDep>` 就可能再 hoist 一次。
// 所以呢度用 test 鎖死：要 bump 係得嘅，但一定要連呢個檔一齊改 = 一個明示決定。
//
// Run: node scripts/test-lockfile-prod-pins.mjs

import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const lock = JSON.parse(readFileSync(join(ROOT, "package-lock.json"), "utf8"));

// name -> production 樹入面必須係呢個版本。改之前問：`next build` 出嚟嘅嘢會唔會唔同？
const PROD_PINS = {
  "caniuse-lite": "1.0.30001806",
  "baseline-browser-mapping": "2.11.0",
};
// dev-only `browserslist` 要新過 PROD_PINS，所以佢有自己嘅 nested 副本。
const DEV_NESTED = {
  "caniuse-lite": "1.0.30001809",
  "baseline-browser-mapping": "2.11.14",
};

const failed = [];
let checks = 0;
const ok = (label, condition, detail = "") => {
  checks += 1;
  if (!condition) failed.push(`FAIL ${label}${detail ? `\n  ${detail}` : ""}`);
};

for (const [name, want] of Object.entries(PROD_PINS)) {
  const key = `node_modules/${name}`;
  const node = lock.packages[key];
  ok(`${key} 喺 lockfile 入面`, Boolean(node));
  if (!node) continue;
  ok(
    `${key} 鎖住 ${want}`,
    node.version === want,
    `got ${node.version} —— 呢個係 next 睇到嘅副本，郁佢 = 郁 production build input。` +
      `真係要 bump 就改 scripts/test-lockfile-prod-pins.mjs 嘅 PROD_PINS 一齊 commit。`,
  );
  ok(
    `${key} 唔係 dev-only`,
    node.dev !== true,
    "標咗 dev:true 即係 hoisting 變咗形，next 會攞唔到佢",
  );
}

for (const [name, want] of Object.entries(DEV_NESTED)) {
  const key = `node_modules/browserslist/node_modules/${name}`;
  const node = lock.packages[key];
  ok(
    `${key} 存在（dev-only browserslist 專用副本）`,
    Boolean(node),
    "冇咗佢即係 browserslist 會回去食 hoisted 嗰個，PROD_PINS 就會被 npm 頂上去",
  );
  if (!node) continue;
  ok(`${key} 係 ${want}`, node.version === want, `got ${node.version}`);
  ok(`${key} 標住 dev:true`, node.dev === true, `got dev=${node.dev}`);
}

// browserslist 自己嘅 range 一定要係 nested 副本先滿足到 —— 如果將來 range 放寬到
// 連 PROD_PINS 都收，呢個 nested 副本就係多餘嘅複雜度，應該拆返。
const browserslist = lock.packages["node_modules/browserslist"];
ok("node_modules/browserslist 存在且係 dev", Boolean(browserslist) && browserslist.dev === true);

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
