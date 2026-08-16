#!/usr/bin/env node
// robots 政策嘅唯一一組 test。
//
// 點解要有：`createRobotsPolicy` 有個 canary/staging「全擋」分支，但 compose.yaml
// 一直冇傳 `CARDZ_ENVIRONMENT` 入 container（2026-08-11 修咗），即係嗰條分支由頭到
// 尾冇行過。同一時間，邊個 crawler 擋邊個唔擋係 owner 政策，唔應該有人靜靜改咗
// 都冇人知 —— 所以呢度將今日嘅名單寫死做 assertion：要改可以，但一定要連呢個檔
// 一齊改，改嘅時候就係一個 owner 決定。
//
// 2026-08-16 owner 決定「做盡」：AI 訓練爬蟲由全擋改做全開；`/api/` 唔再一刀切擋，
// `/api/v1/`（公開 JSON）同 `/api/og/`（social card 圖）明寫 Allow。呢個檔跟住改，
// 所以下面第 3 節由「今日全擋名單」變成「今日一個都唔擋」。
//
// Run: node scripts/test-robots-policy.mjs

import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { createRobotsPolicy } = await import(
  `file://${join(ROOT, "apps", "web", "src", "app", "robots.ts").replaceAll("\\", "/")}`
);

const failed = [];
let checks = 0;

const check = (label, got, want) => {
  checks += 1;
  if (JSON.stringify(got) !== JSON.stringify(want)) {
    failed.push(`FAIL ${label}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
  }
};

const ok = (label, condition, detail = "") => {
  checks += 1;
  if (!condition) failed.push(`FAIL ${label}${detail ? `\n  ${detail}` : ""}`);
};

const agents = (policy, name) =>
  policy.rules.find((rule) => [rule.userAgent].flat().includes(name));

const ALLOW = ["/", "/api/v1/", "/api/og/", "/llms.txt", "/llms-full.txt"];
const DISALLOW = ["/api/health", "/data/private/", "/tune", "/_next/"];

// --- 1. 非生產環境全擋 ------------------------------------------------------
for (const environment of ["canary", "staging"]) {
  const policy = createRobotsPolicy(environment);
  check(`${environment} 全擋`, policy.rules, [{ userAgent: "*", disallow: "/" }]);
  check(`${environment} 唔出 sitemap`, policy.sitemap, undefined);
}

// --- 2. 生產開放，但私密路徑要擋 -------------------------------------------
const production = createRobotsPolicy("production");
const wildcard = agents(production, "*");
check("生產：* 開放（含機讀面）", wildcard.allow, ALLOW);
check("生產：* 擋住 health / 私密資料 / tune / _next", wildcard.disallow, DISALLOW);
check("生產：有 sitemap", production.sitemap, "https://cardzmarketcap.com/sitemap.xml");

// 冇傳 environment（今日生產 container 嘅實況）同 "production" 行同一條路。
check("undefined 環境 = 生產分支", JSON.stringify(createRobotsPolicy()), JSON.stringify(production));

// 舊政策一刀切 `Disallow: /api/`，連 /api/v1/ 都爬唔到。呢條 assert 就係防止有人手快加返。
for (const rule of production.rules) {
  const disallow = [rule.disallow ?? []].flat();
  ok(
    `${[rule.userAgent].flat()[0]} 冇一刀切擋 /api/`,
    !disallow.includes("/api/") && !disallow.includes("/api"),
    `disallow = ${JSON.stringify(disallow)}`,
  );
}

// --- 3. 邊個 crawler 擋邊個唔擋 = owner 政策，寫死喺度 ----------------------
// owner 2026-08-16「做盡」：生產環境一個 UA 都唔全擋。要擋返邊個，就要改呢節。
const blocked = production.rules.filter((rule) => rule.disallow === "/").flatMap((rule) => [rule.userAgent].flat());
check("今日全擋名單（應該係空）", blocked.sort(), []);

// 四組 rule 逐組按用途檢查，改組別會即刻紅。
const groups = {
  "(a) 搜尋引擎": ["Googlebot", "Bingbot", "DuckDuckBot", "Applebot", "YandexBot", "Baiduspider", "Yeti"],
  "(b) AI 搜尋／用戶觸發": [
    "OAI-SearchBot",
    "ChatGPT-User",
    "PerplexityBot",
    "Perplexity-User",
    "Claude-SearchBot",
    "Claude-User",
    "DuckAssistBot",
    "MistralAI-User",
    "YouBot",
  ],
  "(c) AI 訓練": [
    "GPTBot",
    "ClaudeBot",
    "anthropic-ai",
    "Google-Extended",
    "CCBot",
    "Amazonbot",
    "Applebot-Extended",
    "meta-externalagent",
    "Bytespider",
    "cohere-ai",
    "PetalBot",
  ],
};
for (const [label, names] of Object.entries(groups)) {
  for (const name of names) {
    const rule = agents(production, name);
    ok(`${label}：${name} 有 rule`, Boolean(rule));
    if (rule) check(`${label}：${name} 開放`, rule.allow, ALLOW);
  }
}

// 三組要係三條獨立 rule，唔准合埋 —— 搜尋、AI 檢索、訓練係三個唔同嘅控制，合埋就冇得分開調。
const ruleOf = (name) => production.rules.indexOf(agents(production, name));
ok(
  "搜尋 / AI 檢索 / 訓練 分三條 rule",
  new Set([ruleOf("Googlebot"), ruleOf("OAI-SearchBot"), ruleOf("GPTBot")]).size === 3,
);

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
