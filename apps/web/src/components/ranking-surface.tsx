import { MarketPage } from "./market-page";
import { RankingPager } from "./ranking-pager";
import { copy } from "@/lib/i18n";
import {
  rankingHrefFor,
  rankingPath,
  requireRankingPage,
  type RankingScope,
} from "@/lib/ranking-surface";
import type { Locale } from "@/lib/types";

export async function RankingSurface({
  scope,
  locale,
  params,
}: {
  scope: RankingScope;
  locale: Locale;
  params: Record<string, string | string[] | undefined>;
}) {
  const { snapshot, page, pageSize, pageCount, firstRank, lastRank } = await requireRankingPage(scope, params);
  const t = copy[locale];
  const searching = typeof params.q === "string" && params.q.trim().length > 0;
  /* pager 由 MarketPage 喺榜尾（Rankings 之後、頁尾說明之前）render——之前做 sibling 會跌落
     Provenance／about 之後，離個榜成版遠（owner 2026-08-16 晚 review）。 */
  const pager = !searching ? (
    <RankingPager
      page={page}
      pageSize={pageSize}
      pageCount={pageCount}
      firstRank={firstRank}
      lastRank={lastRank}
      showMoreLabel={t.labels.showMore}
      pageSizeLabel={t.labels.pageSizeLabel}
      previousLabel={t.nav.previousPage}
      nextLabel={t.nav.nextPage}
      hrefFor={rankingHrefFor(rankingPath(scope), params)}
    />
  ) : null;
  return <MarketPage kind={scope} snapshot={snapshot} pager={pager} />;
}
