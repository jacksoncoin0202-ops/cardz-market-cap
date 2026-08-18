import { MarketPage } from "./market-page";
import { copy } from "@/lib/i18n";
import { RANKING_PAGE_SIZES, RANKING_ROW_CAP } from "@/lib/pagination";
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
  const hrefFor = rankingHrefFor(rankingPath(scope), params);
  /*
   * pager 由 MarketPage 喺榜尾（Rankings 之後、頁尾說明之前）render——之前做 sibling 會跌落
   * Provenance／about 之後，離個榜成版遠（owner 2026-08-16 晚 review）。
   *
   * 2026-08-18 起呢度**唔再 render 個 pager 出嚟**，只係傳純資料落去：`<RankingPager>`
   * 變咗 client component（要 IntersectionObserver + 接落去嘅 handler），而 function
   * 係過唔到 server → client 邊界嘅，所以 `hrefFor()` 喺呢度先行晒，出一堆現成 href。
   */
  const pagerData = !searching ? {
    scope,
    page,
    pageSize,
    pageCount,
    firstRank,
    lastRank,
    sizeLinks: RANKING_PAGE_SIZES.map((size) => ({
      size,
      href: hrefFor({ page: 1, size }),
      active: size === pageSize,
    })),
    prevHref: page > 1 ? hrefFor({ page: page - 1, size: pageSize }) : null,
    /* 每版一條 href（index 0 = 第 1 版）。size 100 × 1604 張 = 17 條，payload 忽略得。 */
    pageHrefs: Array.from({ length: pageCount }, (_, index) => hrefFor({ page: index + 1, size: pageSize })),
    labels: {
      showMore: t.labels.showMore,
      pageSize: t.labels.pageSizeLabel,
      previous: t.nav.previousPage,
      next: t.nav.nextPage,
      loading: t.labels.loadingMore,
      retry: t.errorPage.retry,
      /* {count} 喺呢度填死：pager 係 client component，唔想連 fillTemplate 都拖埋落去。 */
      rowCap: t.labels.rowCapReached.replace("{count}", String(RANKING_ROW_CAP)),
    },
  } : null;
  return <MarketPage kind={scope} snapshot={snapshot} pager={pagerData} />;
}
