#!/usr/bin/env node
// Canonical host + alias 301 嘅唯一一組 test。
//
// 出街身份係 cardzmarketcap.com / CARDZ。S domain 只係入口，HTML／sitemap／robots
// 要 301 去 Z；/api/health 同 app.cardzmarketcap.com 唔准轉。
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

check("default public origin", PUBLIC_SITE_URL, "https://cardzmarketcap.com");
check("canonical host 唔轉", aliasHostRedirectLocation("cardzmarketcap.com", "/", ""), null);
check("app.Z 唔轉", aliasHostRedirectLocation("app.cardzmarketcap.com", "/", ""), null);
check(
  "S apex 連 query",
  aliasHostRedirectLocation("cardsmarketcap.com", "/", "?lang=zh-TW"),
  "https://cardzmarketcap.com/?lang=zh-TW",
);
check(
  "www.S card path",
  aliasHostRedirectLocation("www.cardsmarketcap.com", "/card/cmc_fc229f7ae1b256b2119fa79b", ""),
  "https://cardzmarketcap.com/card/cmc_fc229f7ae1b256b2119fa79b",
);
check(
  "www.Z → apex Z",
  aliasHostRedirectLocation("www.cardzmarketcap.com", "/box", ""),
  "https://cardzmarketcap.com/box",
);
check("S robots 都要轉", aliasHostRedirectLocation("cardsmarketcap.com", "/robots.txt", ""), "https://cardzmarketcap.com/robots.txt");
check("S sitemap 都要轉", aliasHostRedirectLocation("cardsmarketcap.com", "/sitemap.xml", ""), "https://cardzmarketcap.com/sitemap.xml");
check("health 唔轉", aliasHostRedirectLocation("cardsmarketcap.com", "/api/health", ""), null);
check("app health 唔轉", aliasHostRedirectLocation("app.cardzmarketcap.com", "/api/health", ""), null);
check("localhost 唔轉", aliasHostRedirectLocation("localhost:3000", "/", ""), null);
check("unknown host 唔轉", aliasHostRedirectLocation("cardz-alb.amazonaws.com", "/", ""), null);

const org = siteOrganization("https://cardzmarketcap.com/");
check("org name", org.name, "CardZ Marketcap");
check("org sameAs 有 S", org.sameAs.includes("https://cardsmarketcap.com"), true);
check("org sameAs 冇自己", org.sameAs.includes("https://cardzmarketcap.com"), false);

for (const line of failed) console.log(line);
console.log(`${checks - failed.length}/${checks} checks passed`);
process.exit(failed.length ? 1 : 0);
