#!/usr/bin/env node
// Canonical host + alias 301 嘅唯一一組 test。
//
// 出街身份係 cardsmarketcap.com。Z / www / app 只係入口，HTML／sitemap／robots
// 要 301；/api/health 唔准轉，AWS ALB／Docker／日鏈 curl --fail 會當 301 唔健康。
//
// Run: node scripts/test-public-site.mjs

import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const { PUBLIC_SITE_URL, aliasHostRedirectLocation, siteOrganization } = await import(
  `file://${join(ROOT, "apps", "web", "src", "lib", "public-site.ts").replaceAll("\\", "/")}`
);

const failed = [];
let checks = 0;

const check = (label, got, want) => {
  checks += 1;
  if (got !== want) {
    failed.push(`FAIL ${label}\n  got  ${JSON.stringify(got)}\n  want ${JSON.stringify(want)}`);
  }
};

check("default public origin", PUBLIC_SITE_URL, "https://cardsmarketcap.com");
check("canonical host 唔轉", aliasHostRedirectLocation("cardsmarketcap.com", "/", ""), null);
check(
  "Z apex 連 query",
  aliasHostRedirectLocation("cardzmarketcap.com", "/", "?lang=zh-TW"),
  "https://cardsmarketcap.com/?lang=zh-TW",
);
check(
  "www.Z card path",
  aliasHostRedirectLocation("www.cardzmarketcap.com", "/card/cmc_fc229f7ae1b256b2119fa79b", ""),
  "https://cardsmarketcap.com/card/cmc_fc229f7ae1b256b2119fa79b",
);
check(
  "app.Z 首頁",
  aliasHostRedirectLocation("app.cardzmarketcap.com", "/", ""),
  "https://cardsmarketcap.com/",
);
check(
  "www.S → apex S",
  aliasHostRedirectLocation("www.cardsmarketcap.com", "/box", ""),
  "https://cardsmarketcap.com/box",
);
check("Z robots 都要轉", aliasHostRedirectLocation("cardzmarketcap.com", "/robots.txt", ""), "https://cardsmarketcap.com/robots.txt");
check("Z sitemap 都要轉", aliasHostRedirectLocation("cardzmarketcap.com", "/sitemap.xml", ""), "https://cardsmarketcap.com/sitemap.xml");
check("health 唔轉", aliasHostRedirectLocation("app.cardzmarketcap.com", "/api/health", ""), null);
check("localhost 唔轉", aliasHostRedirectLocation("localhost:3000", "/", ""), null);
check("unknown host 唔轉", aliasHostRedirectLocation("cardz-alb.amazonaws.com", "/", ""), null);

const org = siteOrganization("https://cardsmarketcap.com/");
check("org name", org.name, "Cards Marketcap");
check("org sameAs 有 Z", org.sameAs.includes("https://cardzmarketcap.com"), true);
check("org sameAs 冇自己", org.sameAs.includes("https://cardsmarketcap.com"), false);

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
