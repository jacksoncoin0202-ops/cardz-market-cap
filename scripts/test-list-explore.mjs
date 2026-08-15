#!/usr/bin/env node
// 測 apps/web/src/lib/list-explore.ts 純函數：query、nulls-last、rank 唔重新編號、
// BOX sort 唔碰 PSA10 欄。用 TypeScript transpile，唔引入新 test runner。
import { createRequire } from "node:module";
import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const SOURCE = join(ROOT, "apps/web/src/lib/list-explore.ts");
const HEATMAP = join(ROOT, "apps/web/src/components/heatmap.tsx");
const SETTINGS = join(ROOT, "apps/web/src/lib/use-market-settings.ts");
const RANKINGS = join(ROOT, "apps/web/src/components/rankings.tsx");

const require = createRequire(join(ROOT, "apps/web/package.json"));
const ts = require("typescript");
const source = readFileSync(SOURCE, "utf8");
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 },
});
const dir = join(tmpdir(), `cardz-list-explore-${process.pid}`);
mkdirSync(dir, { recursive: true });
const file = join(dir, "list-explore.mjs");
writeFileSync(file, outputText);
const explore = await import(pathToFileURL(file).href);
rmSync(dir, { recursive: true, force: true });

const failed = [];
const check = (label, condition) => {
  if (!condition) failed.push(label);
};

const heatmap = readFileSync(HEATMAP, "utf8");
const settings = readFileSync(SETTINGS, "utf8");
const rankings = readFileSync(RANKINGS, "utf8");
check("heatmap has no list-explore", !heatmap.includes("list-explore") && !heatmap.includes("ExploreBar"));
check("href omits explore query", /if \(period !== "30d"\) query\.set\("period", period\);/.test(settings) && !/query\.set\("q"/.test(settings.split("const href")[1] ?? ""));
check("ranking title uses full catalog", /rankingTitle\.replace\("\{count\}", String\(cards\.length\)\)/.test(rankings));
check("box keys omit pop and cap", JSON.stringify(explore.boxSortKeys) === JSON.stringify(["rank", "price", "sold", "release"]));
check("card keys are rank price pop cap", JSON.stringify(explore.cardSortKeys) === JSON.stringify(["rank", "price", "pop", "cap"]));

function card(overrides = {}) {
  return {
    officialName: "Pikachu",
    collectorNumber: "025",
    setName: { en: "Base Set", "zh-TW": "基礎系列", "zh-CN": null, ja: null, ko: null },
    pricePsa10: { value: 100 },
    populationPsa10: { value: 2000 },
    marketCap: { value: 200000 },
    viewRank: 1,
    marketRank: 1,
    ...overrides,
  };
}

function box(overrides = {}) {
  return {
    name: { en: "OP01 Romance Dawn", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    fullName: { en: "One Piece OP01 Box", "zh-TW": null, "zh-CN": null, ja: null, ko: null },
    setCode: "OP01",
    priceUsd: { value: 180 },
    release: "2022-12-01",
    rank: 1,
    windows: { "1d": { soldCount: 2 }, "7d": { soldCount: 8 }, "30d": { soldCount: 20 } },
    ...overrides,
  };
}

check("empty query matches", explore.cardMatchesQuery(card(), "", "en"));
check("name query", explore.cardMatchesQuery(card(), "pika", "en"));
check("number query", explore.cardMatchesQuery(card(), "025", "en"));
check("set query locale", explore.cardMatchesQuery(card(), "基礎", "zh-TW"));
check("set query english fallback", explore.cardMatchesQuery(card(), "base", "ja"));
check("rarity-like text is not searchable", !explore.cardMatchesQuery(card({ officialName: "Pikachu", collectorNumber: "025" }), "secret rare", "en"));
check("invalid card sort falls back to rank", explore.normaliseCardSort("sold") === "rank");
check("invalid box sort falls back to rank", explore.normaliseBoxSort("pop") === "rank");
check("invalid box sort cap falls back", explore.normaliseBoxSort("cap") === "rank");

const ranked = [
  card({ officialName: "A", viewRank: 1, marketRank: 1, pricePsa10: { value: 10 }, marketCap: { value: 100 }, populationPsa10: { value: 50 } }),
  card({ officialName: "B", viewRank: 2, marketRank: 2, pricePsa10: { value: null }, marketCap: { value: 80 }, populationPsa10: { value: 90 } }),
  card({ officialName: "C", viewRank: 3, marketRank: 3, pricePsa10: { value: 30 }, marketCap: { value: null }, populationPsa10: { value: 20 } }),
];
const byPrice = explore.sortCards(ranked, "price", "desc");
check("price desc order", byPrice.map((item) => item.officialName).join(",") === "C,A,B");
check("null price last", byPrice.at(-1).officialName === "B");
check("viewRank not renumbered", byPrice.find((item) => item.officialName === "C").viewRank === 3);

const byRank = explore.sortCards(ranked, "rank", "asc");
check("rank sort keeps incoming order", byRank.map((item) => item.officialName).join(",") === "A,B,C");

const boxes = [
  box({ name: { en: "Alpha", "zh-TW": null, "zh-CN": null, ja: null, ko: null }, setCode: "AAA", rank: 1, priceUsd: { value: 50 }, release: "2024-01-01" }),
  box({ name: { en: "Beta", "zh-TW": null, "zh-CN": null, ja: null, ko: null }, setCode: "BBB", rank: 2, priceUsd: { value: null }, release: null }),
  box({ name: { en: "Gamma", "zh-TW": null, "zh-CN": null, ja: null, ko: null }, setCode: "CCC", rank: 3, priceUsd: { value: 90 }, release: "2023-01-01" }),
];
const boxByPrice = explore.sortBoxes(boxes, "price", "desc", "30d");
check("box price desc", boxByPrice.map((item) => item.setCode).join(",") === "CCC,AAA,BBB");
check("box null price last", boxByPrice.at(-1).setCode === "BBB");
check("box rank not renumbered", boxByPrice[0].rank === 3);
check("box set code search", explore.boxMatchesQuery(boxes[0], "aaa", "en"));
check("box name search", explore.boxMatchesQuery(boxes[2], "gamma", "en"));

const toggle = explore.nextExploreSort("rank", "desc", "price");
check("first click starts desc", toggle.sort === "price" && toggle.dir === "desc");
const flip = explore.nextExploreSort("price", "desc", "price");
check("same key flips dir", flip.sort === "price" && flip.dir === "asc");
const reset = explore.nextExploreSort("price", "asc", "rank");
check("rank click resets", reset.sort === "rank" && reset.dir === "desc");

if (failed.length) {
  console.error("FAIL list-explore:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS list-explore (" + [
  "query",
  "nulls-last",
  "rank-stable",
  "box-keys",
  "heatmap-untouched",
].join(", ") + ")");
