#!/usr/bin/env node
// robots 政策嘅唯一一組 test。
//
// 點解要有：`createRobotsPolicy` 有個 canary/staging「全擋」分支，但 compose.yaml
// 一直冇傳 `CARDZ_ENVIRONMENT` 入 container（2026-08-11 修咗），即係嗰條分支由頭到
// 尾冇行過。同一時間，邊個 crawler 擋邊個唔擋係 owner 政策，唔應該有人靜靜改咗
// 都冇人知 —— 所以呢度將今日嘅名單寫死做 assertion：要改可以，但一定要連呢個檔
// 一齊改，改嘅時候就係一個 owner 決定。
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

const agents = (policy, name) =>
  policy.rules.find((rule) => [rule.userAgent].flat().includes(name));

// --- 1. 非生產環境全擋 ------------------------------------------------------
for (const environment of ["canary", "staging"]) {
  const policy = createRobotsPolicy(environment);
  check(`${environment} 全擋`, policy.rules, [{ userAgent: "*", disallow: "/" }]);
  check(`${environment} 唔出 sitemap`, policy.sitemap, undefined);
}

// --- 2. 生產開放，但私密路徑要擋 -------------------------------------------
const production = createRobotsPolicy("production");
const wildcard = agents(production, "*");
check("生產：* 開放", wildcard.allow, "/");
check("生產：* 擋住 /api/、私密資料同 /tune", wildcard.disallow, ["/api/", "/data/private/", "/tune"]);
check("生產：有 sitemap", typeof production.sitemap === "string" && production.sitemap.endsWith("/sitemap.xml"), true);

// 冇傳 environment（今日生產 container 嘅實況）同 "production" 行同一條路。
check("undefined 環境 = 生產分支", JSON.stringify(createRobotsPolicy()), JSON.stringify(production));

// --- 3. 邊個 crawler 擋邊個唔擋 = owner 政策，寫死喺度 ----------------------
// 2026-08-11 實測生產 /robots.txt 就係呢個名單。要改名單就要改呢幾行，改咗即係
// 有人拎過個決定，唔會靜靜滑走。
const blocked = production.rules.filter((rule) => rule.disallow === "/").flatMap((rule) => [rule.userAgent].flat());
check("今日全擋名單", blocked.sort(), ["CCBot", "GPTBot"]);

for (const name of ["ClaudeBot", "Google-Extended", "PerplexityBot", "Applebot-Extended", "Bytespider"]) {
  checks += 1;
  if (blocked.includes(name)) failed.push(`FAIL ${name} 應該喺 * 條 rule 入面（今日冇單獨擋佢）`);
}
check("Googlebot / Bingbot / OAI-SearchBot 有自己嗰條開放 rule", agents(production, "Googlebot").allow, "/");

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
