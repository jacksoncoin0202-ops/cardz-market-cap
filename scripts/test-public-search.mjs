#!/usr/bin/env node
// 證明公開 search／resolve 契約：缺 q／錯 lang 即 400；編號獨一先 resolve；
// 撞名唔准默默揀。transpile 純函數，唔引入新 runner。
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const LIB = join(ROOT, "apps/web/src/lib");
const require = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require("typescript");

const dir = join(tmpdir(), `cardz-public-search-${process.pid}`);
mkdirSync(dir, { recursive: true });

const rewrite = (source) => ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
}).outputText
  .replaceAll('from "./types"', 'from "./types.mjs"')
  .replaceAll('from "./list-explore"', 'from "./list-explore.mjs"')
  .replaceAll('from "./catalog-search"', 'from "./catalog-search.mjs"')
  .replaceAll('from "./public-site"', 'from "./public-site.mjs"');

for (const name of ["types", "public-site", "list-explore", "catalog-search", "public-search"]) {
  writeFileSync(join(dir, `${name}.mjs`), rewrite(readFileSync(join(LIB, `${name}.ts`), "utf8")));
}

const search = await import(pathToFileURL(join(dir, "catalog-search.mjs")).href);
const pub = await import(pathToFileURL(join(dir, "public-search.mjs")).href);
rmSync(dir, { recursive: true, force: true });

const failed = [];
const check = (label, condition) => {
  if (!condition) failed.push(label);
};

function card(overrides) {
  return {
    rank: overrides.marketRank,
    viewRank: overrides.marketRank,
    tcg: "Pokémon",
    cardLanguage: "en",
    printingIdentity: { setCode: "SV8", finishCode: null },
    collectorNumber: "025/193",
    name: { en: overrides.officialName ?? "", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    setName: { en: "Surging Sparks", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    image: { url: "/x.webp", alt: overrides.officialName, kind: "raw_front" },
    pricePsa10: { value: 1, status: "ready", asOf: null },
    populationPsa10: { value: 1, status: "ready", asOf: null },
    marketCap: { value: 1, status: "ready", asOf: null },
    windows: {},
    historyDaily: [],
    salesSparkline: [],
    ...overrides,
  };
}

const moonEn = card({
  id: "cmc_moon_en",
  officialName: "Umbreon VMAX 215/203",
  marketRank: 2,
  collectorNumber: "215/203",
  name: { en: "Umbreon VMAX 215/203", "zh-TW": "月亮伊布 VMAX 異圖 215/203", "zh-CN": null, ja: null, ko: null },
});
const moonJp = card({
  id: "cmc_moon_jp",
  officialName: "Umbreon ex 217/187",
  marketRank: 10,
  collectorNumber: "217/187",
  name: { en: "Umbreon ex 217/187", "zh-TW": "月亮伊布 ex 太晶慶典 217/187", "zh-CN": null, ja: null, ko: null },
});
const gold = card({
  id: "cmc_gold",
  officialName: "Gold Star Pikachu",
  marketRank: 412,
  collectorNumber: "104/101",
  name: { en: "Gold Star Pikachu", "zh-TW": "皮卡丘金星", "zh-CN": null, ja: null, ko: null },
});

const catalog = search.buildCatalogIndex({ top100: [moonEn, moonJp], watchlist: [gold] });

const empty = pub.parsePublicSearchParams(new URLSearchParams(""));
check("empty q is 400", "error" in empty && empty.error.includes("q is required"));

const badLang = pub.parsePublicSearchParams(new URLSearchParams("q=umbreon&lang=fr"));
check("unknown lang is 400", "error" in badLang && badLang.error.includes("Unknown lang"));

const badLimit = pub.parsePublicSearchParams(new URLSearchParams("q=umbreon&limit=0"));
check("limit 0 is 400", "error" in badLimit && badLimit.error.includes("limit"));

const ok = pub.parsePublicSearchParams(new URLSearchParams("q=217/187&lang=zh-tw"));
check("zh-tw folds to zh-TW", !("error" in ok) && ok.locale === "zh-TW" && ok.q === "217/187");

const numberHits = search.searchCatalog(catalog, "217/187", "en");
check("number search first", numberHits[0]?.id === "cmc_moon_jp");
check("number-exact kind", search.catalogMatchKind(numberHits[0], "217/187", "en") === "number-exact");

const resolvedNumber = pub.runPublicResolve(catalog, { q: "217/187", locale: "en", limit: 8 });
check("unique number resolves", resolvedNumber.resolved?.id === "cmc_moon_jp" && resolvedNumber.ambiguous === false);

const resolvedName = pub.runPublicResolve(catalog, { q: "月亮伊布", locale: "zh-TW", limit: 8 });
check("shared nickname is ambiguous", resolvedName.resolved === null && resolvedName.ambiguous === true);
check("ambiguous still lists both", resolvedName.hits.length === 2);

const zhHits = search.searchCatalog(catalog, "皮卡丘金星", "en");
check("zh name matches under en UI", zhHits[0]?.id === "cmc_gold");

const payload = pub.publicSearchBody(
  { generation: { id: "gen" }, generatedAt: "2026-08-18T00:00:00Z", effectiveAt: "2026-08-17T00:00:00Z" },
  { q: "217/187", locale: "en", limit: 8 },
  pub.runPublicSearch(catalog, { q: "217/187", locale: "en", limit: 8 }),
);
check("citation carries date", payload.citation === "CardZ Marketcap, https://cardzmarketcap.com, data as of 2026-08-17");
check("hit url is absolute card page", payload.hits[0].url === "https://cardzmarketcap.com/card/cmc_moon_jp");

const v1 = readFileSync(join(ROOT, "apps/web/src/app/api/v1/route.ts"), "utf8");
const llms = readFileSync(join(ROOT, "apps/web/src/app/llms.txt/route.ts"), "utf8");
check("api index lists search first among lookups", v1.indexOf("/api/v1/search") < v1.indexOf("/api/v1/market"));
check("llms.txt teaches search before dump", llms.indexOf("How to look up a card") < llms.indexOf("Full market JSON"));

if (failed.length) {
  for (const label of failed) console.log(`FAIL ${label}`);
  process.exit(1);
}
console.log(`${14}/14 public-search checks passed`);
