import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const dynamic = "force-dynamic";

const PAGE_SIZE = 200;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return marketMetadata(locale, copy[locale].watchlistHero.title, copy[locale].watchlistHero.body, "/watchlist");
}

export default async function WatchlistPage({ searchParams }: { searchParams: PageSearchParams }) {
  const params = await searchParams;
  const rawPage = Array.isArray(params.page) ? params.page[0] : params.page;
  const requestedPage = Number.parseInt(rawPage ?? "1", 10);
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "watchlist", {
    page: Number.isFinite(requestedPage) ? requestedPage : 1,
    pageSize: PAGE_SIZE,
  });

  const total = snapshot.coverage.requestedCount;
  const pageCount = Math.max(Math.ceil(total / PAGE_SIZE), 1);
  const firstRank = snapshot.top100[0]?.marketRank;
  const lastRank = snapshot.top100.at(-1)?.marketRank;
  const page = firstRank === undefined ? 1 : Math.floor((firstRank - 101) / PAGE_SIZE) + 1;

  const baseQuery = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (key === "page" || value === undefined) continue;
    for (const entry of Array.isArray(value) ? value : [value]) baseQuery.append(key, entry);
  }
  const pageHref = (target: number): string => {
    const query = new URLSearchParams(baseQuery);
    if (target > 1) query.set("page", String(target));
    const suffix = query.toString();
    return `/watchlist${suffix ? `?${suffix}` : ""}`;
  };

  return (
    <>
      <MarketPage kind="watchlist" snapshot={snapshot} />
      {pageCount > 1 ? (
        <nav className="watchlist-pager" aria-label="Watchlist pages">
          {page > 1 ? <a href={pageHref(page - 1)}>‹</a> : <span aria-hidden="true">‹</span>}
          <span>
            #{firstRank}–#{lastRank} · {page}/{pageCount}
          </span>
          {page < pageCount ? <a href={pageHref(page + 1)}>›</a> : <span aria-hidden="true">›</span>}
        </nav>
      ) : null}
    </>
  );
}
