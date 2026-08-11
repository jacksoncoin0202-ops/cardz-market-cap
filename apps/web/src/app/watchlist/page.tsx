import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { parseRequestedPage } from "@/lib/pagination";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

const PAGE_SIZE = 200;

/*
 * 呢版住喺 `app/watchlist/`，**唔喺** `app/(market)/` —— 同 `card/[id]` 同一個理由。
 * `(market)/loading.tsx` 會將組入面每條 route 都包一個 Suspense，shell 連住 200
 * 即刻沖出街，之後 `notFound()` 改唔到 status。呢版要出真 404，所以要企喺個組外面。
 * 唔准喺呢個 folder 加 `loading.tsx`：加咗就靜靜地打返轉頭，測試唔會紅。
 *
 * 至於「點解要出 404」：`?page=` 條數以前 `Number.parseInt(raw ?? "1")` 之後直接
 * 掟落 `scopeSnapshot`，而嗰邊 (server-snapshot.ts:142) 會 clamp 返落 1..pageCount。
 * clamp 本身冇錯——嗰個 function 仲有 API 同 /api/health 兩個叫方靠住佢唔好爆——
 * 錯喺呢版將 clamp 當成驗證：`/watchlist?page=99` 出 200 配第 1 頁內容，
 * canonical 又指返 `/watchlist`，即係一條唔存在嘅頁扮成功兼可索引。
 * 所以範圍判斷提返上嚟呢度做，clamp 留返做落閘。
 */
/* 兩個 export 都行呢個 helper，所以「呢一頁存唔存在」全 route 得一個判準。 */
async function requireWatchlistPage(params: Record<string, string | string[] | undefined>) {
  const requestedPage = parseRequestedPage(params.page);
  if (requestedPage === null) notFound();
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "watchlist", {
    page: requestedPage,
    pageSize: PAGE_SIZE,
  });
  const pageCount = Math.max(Math.ceil(snapshot.coverage.requestedCount / PAGE_SIZE), 1);
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
  /*
   * 第 1 頁保持原本嘅 title / canonical `/watchlist`（外面啲 link 指緊佢）。
   * 第 2 頁之後每頁自己一個 title 同 canonical，否則 N 版內容全部 canonical 去
   * 第 1 頁，等於叫搜尋器當佢哋唔存在。
   */
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
