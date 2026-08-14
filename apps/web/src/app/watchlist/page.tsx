import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { parseRequestedPage, WATCHLIST_PAGE_SIZE } from "@/lib/pagination";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * 呢版住喺 `app/watchlist/`，**唔喺** `app/(market)/` —— 同 `card/[id]` 同一個理由。
 * `(market)/loading.tsx` 會將組入面每條 route 都包一個 Suspense，shell 連住 200
 * 即刻沖出街，之後 `notFound()` 改唔到 status。呢版要出真 404，所以要企喺個組外面。
 * 唔准喺呢個 folder 加 `loading.tsx`：加咗就靜靜地打返轉頭，測試唔會紅。
 *
 * `?page=` 必須喺 clamp 前驗證，否則 `/watchlist?page=99` 會扮第 1 頁出 200。
 * PAGE_SIZE 同 sitemap 共用，令 watchlist 頁數唔會各自漂移。
 */
async function requireWatchlistPage(params: Record<string, string | string[] | undefined>) {
  const requestedPage = parseRequestedPage(params.page);
  if (requestedPage === null) notFound();
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "watchlist", {
    page: requestedPage,
    pageSize: WATCHLIST_PAGE_SIZE,
  });
  const pageCount = Math.max(Math.ceil(snapshot.coverage.requestedCount / WATCHLIST_PAGE_SIZE), 1);
  if (requestedPage > pageCount) notFound();
  return {
    snapshot,
    page: requestedPage,
    pageCount,
    firstRank: snapshot.top100[0]?.marketRank,
    lastRank: snapshot.top100.at(-1)?.marketRank,
  };
}

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const [params, locale] = await Promise.all([searchParams, localeFromSearchParams(searchParams)]);
  const { page, firstRank, lastRank } = await requireWatchlistPage(params);
  const hero = copy[locale].watchlistHero;
  /* 第 2 頁之後每頁自己一個 title/canonical，唔准 canonical 返第 1 頁。 */
  if (page === 1) return marketMetadata(locale, hero.title, hero.body, "/watchlist");
  const range = firstRank !== undefined && lastRank !== undefined ? ` #${firstRank}–#${lastRank}` : "";
  return marketMetadata(locale, `${hero.title}${range}`, hero.body, `/watchlist?page=${page}`);
}

export default async function WatchlistPage({ searchParams }: { searchParams: PageSearchParams }) {
  const params = await searchParams;
  const { snapshot, page, pageCount, firstRank, lastRank } = await requireWatchlistPage(params);

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
