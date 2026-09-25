import { notFound } from "next/navigation";
import {
  DEFAULT_RANKING_PAGE_SIZE,
  parseRequestedPage,
  parseRequestedPageSize,
  rankingPageCount,
  type RankingPageSize,
  type RankingScope,
} from "./pagination";
import { loadMarketSnapshot, scopeSnapshot } from "./server-snapshot";
import type { MarketViewSnapshot } from "./types";

export type { RankingScope };

export function rankingPath(scope: RankingScope): string {
  if (scope === "pokemon") return "/pokemon";
  if (scope === "one-piece") return "/one-piece";
  return "/";
}

export async function requireRankingPage(
  scope: RankingScope,
  params: Record<string, string | string[] | undefined>,
): Promise<{
  snapshot: MarketViewSnapshot;
  page: number;
  pageSize: RankingPageSize;
  pageCount: number;
  firstRank?: number;
  lastRank?: number;
}> {
  const requestedPage = parseRequestedPage(params.page);
  const requestedSize = parseRequestedPageSize(params.size);
  if (requestedPage === null || requestedSize === null) notFound();
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), scope, {
    page: requestedPage,
    pageSize: requestedSize,
  });
  const pageCount = rankingPageCount(snapshot.coverage.requestedCount, requestedSize);
  if (requestedPage > pageCount) notFound();
  const ranked = snapshot.top100.filter((card) => card.viewRank > 0);
  return {
    snapshot,
    page: requestedPage,
    pageSize: requestedSize,
    pageCount,
    firstRank: ranked[0]?.viewRank,
    lastRank: ranked.at(-1)?.viewRank,
  };
}

export function rankingHrefFor(
  path: string,
  params: Record<string, string | string[] | undefined>,
): (target: { page: number; size: RankingPageSize }) => string {
  const baseQuery = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (key === "page" || key === "size" || value === undefined) continue;
    for (const entry of Array.isArray(value) ? value : [value]) baseQuery.append(key, entry);
  }
  return (target) => {
    const query = new URLSearchParams(baseQuery);
    if (target.page > 1) query.set("page", String(target.page));
    if (target.size !== DEFAULT_RANKING_PAGE_SIZE) query.set("size", String(target.size));
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  };
}
