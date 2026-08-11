#!/usr/bin/env node
// 改過公開 id 嘅卡：舊 URL 一定要有 308。
//
// 點解要有：public_card_alias（migration 041）一鑄 alias，snapshot 就唔再有舊 id，
// 舊 URL 硬 404 —— 而嗰三條已經入咗生產 sitemap。唯一補償係 next.config.ts 個
// redirects()。一個「寫咗個 map 但 config 冇讀」嘅版本睇落一模一樣，所以呢度直接
// 行 next.config.ts 本身出嚟嘅 rule list，唔係讀個 map 自己對自己。
//
// Run: node scripts/test-legacy-card-redirects.mjs

import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const url = (...parts) => `file://${join(ROOT, ...parts).replaceAll("\\", "/")}`;

const config = (await import(url("apps", "web", "next.config.ts"))).default;

// 唔 import 個 map，反而由 config 真正出嚟嗰批 rule 反推：咁樣「個 map 寫咗但
// redirects() 冇讀佢」呢種版本就 fail 得到。
const rules = await config.redirects();
const LEGACY_CARD_IDS = Object.fromEntries(
  rules
    .filter((rule) => rule.source.startsWith("/card/"))
    .map((rule) => [rule.source.slice("/card/".length), rule.destination.slice("/card/".length)]),
);

const failed = [];
let checks = 0;

const check = (label, got, want) => {
  checks += 1;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failed.push(`FAIL ${label}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
  }
};

check("next.config 有 redirects()", typeof config.redirects, "function");
const entries = Object.entries(LEGACY_CARD_IDS);
check("真係出到 /card/* 嘅轉向 rule", entries.length > 0, true);

for (const [oldId, newId] of entries) {
  check(`${oldId} 個新 id 喺 cmc_ 命名空間`, /^cmc_[0-9a-f]{20,24}$/.test(newId), true);
  check(`${oldId} 唔係指返自己`, oldId !== newId, true);
  for (const prefix of ["/card", "/api/v1/cards"]) {
    const rule = rules.find((r) => r.source === `${prefix}/${oldId}`);
    checks += 1;
    if (!rule) failed.push(`FAIL ${prefix}/${oldId} 冇 redirect —— 舊 URL 硬 404`);
    else if (rule.destination !== `${prefix}/${newId}`) {
      failed.push(`FAIL ${prefix}/${oldId} 指錯：${rule.destination}`);
    } else if (rule.permanent !== true) {
      failed.push(`FAIL ${prefix}/${oldId} 唔係永久轉向（搜尋引擎唔會轉權重）`);
    }
  }
}

// 新 id 唔可以又出現喺 key 側：咁樣會轉一個圈。
for (const newId of Object.values(LEGACY_CARD_IDS)) {
  checks += 1;
  if (newId in LEGACY_CARD_IDS) failed.push(`FAIL ${newId} 又係 key 又係 value —— 轉向會兜圈`);
}

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
