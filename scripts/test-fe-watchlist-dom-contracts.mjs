#!/usr/bin/env node
// Execute the shipped watchlist projection, routes and JSX. No source regexes.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");

function execute(relativePath, dependencies = {}) {
  const filename = join(ROOT, relativePath);
  const source = readFileSync(filename, "utf8");
  const javascript = ts.transpileModule(source, {
    fileName: filename,
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
      esModuleInterop: true,
    },
  }).outputText;
  const module = { exports: {} };
  const localRequire = (name) => {
    if (Object.hasOwn(dependencies, name)) return dependencies[name];
    throw new Error(`${relativePath} requested unstubbed dependency ${name}`);
  };
  vm.runInNewContext(javascript, {
    module,
    exports: module.exports,
    require: localRequire,
    process,
    console,
    Response,
    URLSearchParams,
    Object,
    Array,
    Number,
    Math,
    Promise,
    Symbol,
    Set,
  }, { filename });
  return module.exports;
}

const pagination = execute("apps/web/src/lib/pagination.ts");
const serverSnapshot = execute("apps/web/src/lib/server-snapshot.ts", {
  "@cardz/market-data": {},
  "./market-media": { marketAssetObjectKey: () => null },
  "./pagination": pagination,
  "./snapshot": { normaliseSnapshot: (snapshot) => snapshot },
});

const story = { en: "story", "zh-TW": "story", "zh-CN": "story", ja: "story", ko: "story" };
const card = (marketRank, id = `card-${marketRank}`) => ({
  id,
  marketRank,
  rank: marketRank,
  viewRank: marketRank,
  tcg: marketRank % 2 ? "Pokémon" : "One Piece",
  story,
  historyDaily: [{ at: "2026-08-25", value: marketRank }],
});
const fixture = {
  generation: "fixture-generation",
  generatedAt: "2026-08-25T00:00:00Z",
  effectiveAt: "2026-08-25",
  coverage: { claim: "verified-top-n", requestedCount: 307, verifiedCount: 307 },
  top100: Array.from({ length: 100 }, (_, index) => card(index + 1)),
  watchlist: [
    ...Array.from({ length: 205 }, (_, index) => card(index + 101)),
    card(0, "awaiting-a"),
    card(0, "awaiting-b"),
  ],
};

function assert(condition, message) {
  if (!condition) throw new Error(message);
}

// Projection executes the real membership, ordering, pagination and payload trim.
const watch1 = serverSnapshot.scopeSnapshot(fixture, "watchlist", { page: 1, pageSize: 200 });
const watch2 = serverSnapshot.scopeSnapshot(fixture, "watchlist", { page: 2, pageSize: 200 });
const projected = [...watch1.top100, ...watch2.top100];
assert(projected.length === 207, `watchlist lost members: ${projected.length}`);
assert(new Set(projected.map((item) => item.id)).size === 207, "watchlist duplicated members across pages");
assert(projected.filter((item) => item.marketRank >= 101).length === 205, "rank 101+ projection is incomplete");
assert(projected.filter((item) => item.marketRank === 0 && item.viewRank === 0).length === 2, "rank-0 cards were lost or assigned a fake rank");
assert(projected.every((item) => item.historyDaily.length === 0), "list projection retained detail history");
assert(projected.every((item) => Object.values(item.story).every((value) => value === "")), "list projection retained detail story");

// Sitemap executes with the same fixture and must expose every watchlist page.
const sitemapModule = execute("apps/web/src/app/sitemap.ts", {
  "@/lib/pagination": pagination,
  "@/lib/server-snapshot": { loadMarketSnapshot: async () => fixture },
});
const sitemap = await sitemapModule.default();
const watchlistEntries = sitemap.filter((entry) => entry.url.includes("/watchlist"));
assert(watchlistEntries.length === 2, `sitemap watchlist pages=${watchlistEntries.length}`);
const sitemapPage2 = watchlistEntries.find((entry) => entry.url.endsWith("/watchlist?page=2"));
assert(sitemapPage2, "sitemap omitted watchlist page 2");
assert(sitemapPage2.alternates.languages.ja.endsWith("/watchlist?page=2&lang=ja"), "sitemap alternate lost the page query");

// Health route executes the production aggregation, not an imitation.
const healthModule = execute("apps/web/src/app/api/health/route.ts", {
  "@/lib/server-snapshot": {
    loadMarketSnapshot: async () => fixture,
    scopeSnapshot: serverSnapshot.scopeSnapshot,
  },
});
const healthResponse = await healthModule.GET();
const health = await healthResponse.json();
assert(healthResponse.status === 200 && health.status === "ok", "health route did not return an executable success response");
assert(health.surfaces.rankedBeyondTop100 === 205, `health ranked count=${health.surfaces.rankedBeyondTop100}`);
assert(health.surfaces.awaitingFreshPrice === 2, `health awaiting count=${health.surfaces.awaitingFreshPrice}`);
assert(health.surfaces.tcg101Plus === 207 && health.surfaces.tcg101To300 === 200, "health surface counts drifted");

// Tiny JSX runtime: execute the actual server component and render its returned
// element tree to HTML, including its real pager href calculations.
const Fragment = Symbol("Fragment");
const jsx = (type, props, key) => ({ type, props: props ?? {}, key });
const jsxRuntime = { jsx, jsxs: jsx, Fragment };
const escapeHtml = (value) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;");
function render(node) {
  if (node === null || node === undefined || node === false || node === true) return "";
  if (Array.isArray(node)) return node.map(render).join("");
  if (typeof node === "string" || typeof node === "number") return escapeHtml(node);
  if (node.type === Fragment) return render(node.props.children);
  if (typeof node.type === "function") return render(node.type(node.props));
  const attributes = Object.entries(node.props)
    .filter(([name, value]) => name !== "children" && value !== undefined && value !== null && value !== false)
    .map(([name, value]) => {
      const attr = name === "className" ? "class" : name;
      return value === true ? ` ${attr}` : ` ${attr}="${escapeHtml(value)}"`;
    }).join("");
  return `<${node.type}${attributes}>${render(node.props.children)}</${node.type}>`;
}

const NOT_FOUND = new Error("NEXT_NOT_FOUND");
const watchlistPage = execute("apps/web/src/app/watchlist/page.tsx", {
  "react/jsx-runtime": jsxRuntime,
  "next/navigation": { notFound: () => { throw NOT_FOUND; } },
  "@/components/market-page": {
    MarketPage: ({ kind, snapshot }) => jsx("section", {
      "data-testid": "market-page",
      "data-kind": kind,
      "data-count": snapshot.top100.length,
    }),
  },
  "@/lib/i18n": { copy: {} },
  "@/lib/pagination": pagination,
  "@/lib/route-metadata": {
    localeFromSearchParams: async () => "en",
    marketMetadata: () => ({}),
  },
  "@/lib/server-snapshot": {
    loadMarketSnapshot: async () => fixture,
    scopeSnapshot: serverSnapshot.scopeSnapshot,
  },
});

const firstHtml = render(await watchlistPage.default({ searchParams: Promise.resolve({ lang: "ja" }) }));
assert(firstHtml.includes('data-testid="market-page"') && firstHtml.includes('data-count="200"'), "watchlist server component did not render the projected cards");
assert(firstHtml.includes('aria-label="Watchlist pages"') && firstHtml.includes("1/2"), "watchlist pager DOM is missing");
assert(firstHtml.includes('href="/watchlist?lang=ja&amp;page=2"'), "watchlist next link lost the language query");

const secondHtml = render(await watchlistPage.default({ searchParams: Promise.resolve({ page: "2", lang: "ja" }) }));
assert(secondHtml.includes('data-count="7"') && secondHtml.includes("2/2"), "watchlist page 2 rendered the wrong slice");
assert(secondHtml.includes('href="/watchlist?lang=ja"'), "watchlist previous link is not canonical page 1");

for (const params of [{ page: "2junk" }, { page: "3" }]) {
  let rejected = false;
  try {
    await watchlistPage.default({ searchParams: Promise.resolve(params) });
  } catch (error) {
    rejected = error === NOT_FOUND;
  }
  assert(rejected, `invalid/out-of-range page was not a real notFound: ${JSON.stringify(params)}`);
}

console.log("POSITIVE_OK executable watchlist projection, sitemap, health route and rendered pager DOM");
