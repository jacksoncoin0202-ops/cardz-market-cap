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

check("shared page size", /export const WATCHLIST_PAGE_SIZE = 200/.test(pagination));
check("watchlist imports page size", /WATCHLIST_PAGE_SIZE/.test(watchlist));
check("sitemap covers every page", /watchlistPageCount/.test(sitemap) && /WATCHLIST_PAGE_SIZE/.test(sitemap));
check("query alternates keep page", /path\.includes\("\?"\).*&/.test(sitemap.replace(/\s+/g, " ")));
check("health counts full beyond-top100", /rankedBeyondTop100/.test(health) && /awaitingFreshPrice/.test(health));
check("scope includes every rank101+", /card\.marketRank >= 101/.test(serverSnapshot) && !/marketRank <= 300/.test(serverSnapshot));
check("rank0 retained without fake rank", /viewRank: 0/.test(serverSnapshot));
check("live DB uses verified quote view", /operator_resolved_canonical_metric_quote/.test(liveDb));
check("live DB has no mutable observation fallback", !/COALESCE\(quote\.price_usd, price\.price_usd\)/.test(liveDb));
check("FE03 visual component unchanged", !/separateAwaiting|awaiting-section|jsonLdPosition/.test(marketPage));

if (failed.length) {
  console.error("FAIL FE03 data wiring:\n" + failed.map((item) => ` - ${item}`).join("\n"));
  process.exit(1);
}
console.log("PASS FE03 full-catalog data wiring (10 contracts)");
