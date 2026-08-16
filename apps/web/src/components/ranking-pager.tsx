import Link from "next/link";
import { DEFAULT_RANKING_PAGE_SIZE, RANKING_PAGE_SIZES, type RankingPageSize } from "@/lib/pagination";

export function RankingPager({
  page,
  pageSize,
  pageCount,
  firstRank,
  lastRank,
  showMoreLabel,
  pageSizeLabel,
  previousLabel,
  nextLabel,
  hrefFor,
}: {
  page: number;
  pageSize: RankingPageSize;
  pageCount: number;
  firstRank?: number;
  lastRank?: number;
  showMoreLabel: string;
  pageSizeLabel: string;
  previousLabel: string;
  nextLabel: string;
  hrefFor: (target: { page: number; size: RankingPageSize }) => string;
}) {
  if (pageCount <= 1 && pageSize === DEFAULT_RANKING_PAGE_SIZE) return null;
  const range = firstRank !== undefined && lastRank !== undefined ? `#${firstRank}–#${lastRank}` : null;
  return (
    <nav className="watchlist-pager ranking-pager" aria-label={pageSizeLabel}>
      <span className="ranking-page-sizes" role="group" aria-label={pageSizeLabel}>
        {RANKING_PAGE_SIZES.map((size) => (
          <Link
            key={size}
            href={hrefFor({ page: 1, size })}
            data-active={size === pageSize ? "true" : "false"}
            aria-current={size === pageSize ? "true" : undefined}
          >
            {size}
          </Link>
        ))}
      </span>
      {/* 翻頁一組自己一個容器：同「每頁幾多」分開排，手機唔會亂 wrap（owner 2026-08-16 晚 review） */}
      {pageCount > 1 ? (
        <span className="ranking-pager-nav">
          {page > 1
            ? <Link href={hrefFor({ page: page - 1, size: pageSize })} rel="prev" aria-label={previousLabel}>‹</Link>
            : <span aria-hidden="true">‹</span>}
          <span aria-current="page">
            {range ? `${range} · ` : null}{page}/{pageCount}
          </span>
          {page < pageCount
            ? <Link href={hrefFor({ page: page + 1, size: pageSize })} rel="next" aria-label={nextLabel}>›</Link>
            : <span aria-hidden="true">›</span>}
          {page < pageCount ? (
            <Link className="ranking-show-more" href={hrefFor({ page: page + 1, size: pageSize })}>
              {showMoreLabel}
            </Link>
          ) : null}
        </span>
      ) : null}
    </nav>
  );
}
