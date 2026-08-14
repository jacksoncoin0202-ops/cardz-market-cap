#!/usr/bin/env node
// FE03 data-wiring/page contract tests; no visual redesign assertions.
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const failed = [];
const check = (label, condition) => { if (!condition) failed.push(label); };
const read = (path) => readFileSync(join(ROOT, path), "utf8");

const pagination = read("apps/web/src/lib/pagination.ts");
const watchlist = read("apps/web/src/app/watchlist/page.tsx");
const sitemap = read("apps/web/src/app/sitemap.ts");
const health = read("apps/web/src/app/api/health/route.ts");
const liveDb = read("apps/web/src/lib/live-db-snapshot.ts");
const serverSnapshot = read("apps/web/src/lib/server-snapshot.ts");
const marketPage = read("apps/web/src/components/market-page.tsx");
const heatmap = read("apps/web/src/components/heatmap.tsx");
const rankings = read("apps/web/src/components/rankings.tsx");
const home = read("apps/web/src/app/(market)/page.tsx");
const pokemon = read("apps/web/src/app/(market)/pokemon/page.tsx");
const onePiece = read("apps/web/src/app/(market)/one-piece/page.tsx");

check("shared page size", /export const WATCHLIST_PAGE_SIZE = 200/.test(pagination));
check("watchlist imports page size", /WATCHLIST_PAGE_SIZE/.test(watchlist));
check("sitemap covers every page", /watchlistPageCount/.test(sitemap) && /WATCHLIST_PAGE_SIZE/.test(sitemap));
check("sitemap reads seed at runtime", /export const dynamic = "force-dynamic"/.test(sitemap));
check("query alternates keep page", /path\.includes\("\?"\).*&/.test(sitemap.replace(/\s+/g, " ")));
check("health counts full beyond-top100", /rankedBeyondTop100/.test(health) && /awaitingFreshPrice/.test(health));
check("scope includes every rank101+", /card\.marketRank >= 101/.test(serverSnapshot) && !/marketRank <= 300/.test(serverSnapshot));
check("rank0 retained without fake rank", /viewRank: 0/.test(serverSnapshot));
check("live DB uses verified quote view", /operator_resolved_canonical_metric_quote/.test(liveDb));
check("live DB has no mutable observation fallback", !/COALESCE\(quote\.price_usd, price\.price_usd\)/.test(liveDb));
check("no first-paint slice", !/firstPaintScope|MARKET_INITIAL_VISIBLE/.test(home + pokemon + onePiece + pagination + serverSnapshot + marketPage));
check("mobile heatmap default 23", /const MOBILE_TILE_COUNT = 23/.test(heatmap));
check("desktop heatmap uses full card count", /isMobileTiles \? MOBILE_TILE_COUNT : cards\.length/.test(heatmap));
check("heatmap title uses full catalog", /title\.replace\("\{count\}", String\(cards\.length\)\)/.test(heatmap));
check("ranking title uses full catalog", /rankingTitle\.replace\("\{count\}", String\(cards\.length\)\)/.test(rankings) || /t\.heatmap\.rankingTitle\.replace\("\{count\}", String\(cards\.length\)\)/.test(rankings));
check("FE03 visual component unchanged", !/separateAwaiting|awaiting-section|jsonLdPosition/.test(marketPage));

if (failed.length) {
  console.error("FAIL FE03 data wiring:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS FE03 full-catalog data wiring (16 contracts)");
