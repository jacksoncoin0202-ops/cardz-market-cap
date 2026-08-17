#!/usr/bin/env node
// ESLint 棘輪（ratchet）—— `npm run lint` 嘅唯一自動 call site。
//
// 點解要有：2026-08-17 補返 `eslint` + `eslint-config-next` devDeps 同 `lint` script
// 之前，`apps/web/eslint.config.mjs` 喺度擺咗好耐但零 call site。裝返 eslint 本身
// **唔算修好** —— 呢個 repo 冇 CI、冇 husky、冇 git hook（`ls .github` / `.husky` 都冇），
// 所以一個「要人手記得去 run」嘅 linter 同冇 linter 係同一件事（AGENTS.md 規矩 9：
// 「有檢查但零 call site」當冇檢查）。
//
// 點解係 ratchet 而唔係「零 error 先過」：初次基線係 10 error / 9 warning，全部
// pre-existing src 問題。要即刻綠就只有兩條路——放鬆規則（規矩 10 禁止）或者喺
// tooling stage 順手改 15 個 src 檔（超出範圍、風險大過收益）。所以呢度改為鎖死
// 「唔准再多」：多過基線 = 紅；少過基線 = 都紅，逼你順手將 BASELINE 調低，個數
// 就只會單向收窄，唔會有人靜靜加返 error 食掉人哋修嘅額度。
//
// 2026-08-17 fix-lint：error 基線已經收到 **0**，warning 收到 8。即係話 error 呢邊
// 而家實質上就係「零 error 先過」，但個雙向比較照留 —— warning 側仲有 8 個要慢慢
// 收，而且下次有人修好一個都要順手改低，個棘輪先鎖得住。
//
// 呢個檔叫 `test-*.mjs` 係特登嘅：`scripts/run_all_tests.py` 會自動 glob
// `scripts/test-*.mjs`，即係 `npm test` 一跑就跑到佢，唔使再改 runner。
//
// 改基線嘅時候順手更新 BASELINE_SOURCE 嗰行日期，等下次有人知個數係幾時度出嚟。
//
// Run: node scripts/test-eslint-ratchet.mjs

import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "apps", "web");
const ESLINT_BIN = join(ROOT, "node_modules", "eslint", "bin", "eslint.js");

// 唯一嘅基線。要改，連呢兩個數一齊 commit —— 咁就係一個明示決定，唔係漂移。
const BASELINE = { errors: 0, warnings: 8 };
const BASELINE_SOURCE = "2026-08-17 fix-lint 收咗 10 error → 0（7 個 disable + 理由、3 個喺 heatmap.tsx 行 globalIgnores）、9 → 8 warning";

const failed = [];

if (!existsSync(ESLINT_BIN)) {
  // Fail-closed：linter 唔喺度就係「檢查冇行過」，唔准當 SKIP 靜靜綠。
  console.log(`FAIL eslint 未裝（${ESLINT_BIN} 唔存在）—— 行 \`npm ci\` 再試`);
  console.log("0/1 checks passed");
  process.exit(1);
}

const proc = spawnSync(process.execPath, [ESLINT_BIN, ".", "--format", "json"], {
  cwd: WEB,
  encoding: "utf8",
  maxBuffer: 64 * 1024 * 1024,
});

if (proc.error) {
  console.log(`FAIL eslint 起唔到：${proc.error.message}`);
  console.log("0/1 checks passed");
  process.exit(1);
}
// eslint 有 error 就 exit 1、有 fatal config 問題就 exit 2。1 係預期之內（我哋數返個數），
// 2 唔係 —— config 爆咗一定要紅，唔可以當「0 個 error」。
if (proc.status !== 0 && proc.status !== 1) {
  console.log(`FAIL eslint exit ${proc.status}（config 爆咗？）\n  ${(proc.stderr || "").trim().slice(0, 600)}`);
  console.log("0/1 checks passed");
  process.exit(1);
}

let results;
try {
  results = JSON.parse(proc.stdout);
} catch (err) {
  console.log(`FAIL eslint JSON 解唔到：${err.message}\n  ${(proc.stdout || "").slice(0, 400)}`);
  console.log("0/1 checks passed");
  process.exit(1);
}

const errors = results.reduce((n, r) => n + r.errorCount, 0);
const warnings = results.reduce((n, r) => n + r.warningCount, 0);

const compare = (label, got, want) => {
  if (got > want) {
    failed.push(
      `FAIL ${label} 由 ${want} 升到 ${got}（新增 ${got - want} 個）\n` +
        `  行 \`npm run lint\` 睇邊幾行；修好佢，唔准放鬆 rule 令佢綠。`,
    );
  } else if (got < want) {
    failed.push(
      `FAIL ${label} 由 ${want} 跌到 ${got} —— 好事，但要將 scripts/test-eslint-ratchet.mjs\n` +
        `  嘅 BASELINE.${label === "error" ? "errors" : "warnings"} 改做 ${got} 一齊 commit，個棘輪先鎖得住。`,
    );
  }
};

compare("error", errors, BASELINE.errors);
compare("warning", warnings, BASELINE.warnings);

for (const line of failed) console.log(line);
console.log(`eslint: ${errors} error / ${warnings} warning（基線 ${BASELINE.errors}/${BASELINE.warnings}，${BASELINE_SOURCE}）`);
console.log(`${2 - failed.length}/2 checks passed`);
process.exit(failed.length ? 1 : 0);
