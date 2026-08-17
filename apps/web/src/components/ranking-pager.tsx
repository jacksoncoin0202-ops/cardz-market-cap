"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import { DEFAULT_RANKING_PAGE_SIZE, type RankingPageSize, type RankingScope } from "@/lib/pagination";

/*
 * 榜尾控制列。owner 2026-08-18 投訴兩件事，兩件都喺呢度修：
 *
 *  ① 「每次撳『展示更多』或者換排列數量，都彈我返去首頁上面」
 *     根因：呢啲掣本來全部係 `next/link` 嘅 `<Link>`，而 App Router 個 `<Link>`
 *     **預設 `scroll={true}`**——navigate 完會 `window.scrollTo(0,0)`。榜喺成版最底，
 *     所以每撳一下就由榜尾彈返 hero 頂。修法：全部 `scroll={false}`，再自己對返
 *     `#market-ranking`（榜頂）定位。**唔係唔郁 scroll**——換頁換數量本來就要返榜頂，
 *     只係唔應該返「成個網站嘅頂」。
 *
 *  ② 「碌到最盡會彈一彈，夾硬再拉就 load 咗個全新版面出嚟」
 *     根因：`?page=` 係**換頁**，唔係接落去。碌到底冇嘢再嚟（於是見到瀏覽器
 *     rubber-band 阻尼），撳完又成個 list 由 #501 重畫（於是「全新版面」）。
 *     修法：「展示更多」改做**接落去**（`onLoadMore`，rows concat 落原本嗰批下面），
 *     再加個 sentinel 喺 pager 上面 800px 位置，未碌到底就已經開始攞下一批 ——
 *     所以正常情況下**根本掂唔到個底**，阻尼自然唔會出現。
 *
 * 冇 JS 一樣要行得：`展示更多` / `‹ ›` / `100 200 300 500` 全部仍然係真 `<a href>`，
 * 爬蟲同關咗 JS 嘅瀏覽器照樣揭得到頁。append 只係喺有 handler 嗰陣 `preventDefault`
 * 之後接管。
 */

/* 自動接落去嘅上限：畫面已經有咁多行就唔再自動攞，要用戶自己撳。
   點解要有：一行係一張卡（圖 + sparkline + 6 個 metric），手機碌到 1600 行
   DOM 會頂唔順。點解係 800 唔係「無限」：800 行 = size 100 撳足 7 次都仲係自動，
   已經遠遠超過「我想繼續碌」嗰個訴求；再落去就應該由用戶明確表態。 */
export const AUTO_APPEND_ROW_CAP = 800;

/* 下邊 800px：仲差成版先到 pager 就已經開始攞下一批，以正常碌速夠時間 fetch + render，
   用戶感覺唔到停頓（太細例如 100px 就變「碌到底先開始 load」＝仍然見到阻尼）。
   上邊 1200px：**修飛過頭**。sentinel 得 1px 高，用戶一嘢 fling 落底（或者 `scrollTo(bottom)`）
   會喺兩個 frame 之間跳過佢 —— 實測 sentinel 停喺 viewport 上面 167px，IntersectionObserver
   由頭到尾冇 fire 過，rows 卡死喺 100。上邊留返 margin 之後，「已經企咗喺頁尾」都仲當交叉，
   照接落去。1200 > 一版高度，覆蓋到大部分桌面 viewport。 */
const SENTINEL_ROOT_MARGIN = "1200px 0px 800px 0px";

export type RankingSizeLink = { size: RankingPageSize; href: string; active: boolean };

/* server → client 只可以過純資料，所以 `hrefFor()` 喺 RankingSurface 度行晒，
   落到嚟已經係一堆現成 href。 */
export type RankingPagerData = {
  scope: RankingScope;
  page: number;
  pageSize: RankingPageSize;
  pageCount: number;
  firstRank?: number;
  lastRank?: number;
  sizeLinks: RankingSizeLink[];
  prevHref: string | null;
  nextHref: string | null;
  labels: {
    showMore: string;
    pageSize: string;
    previous: string;
    next: string;
    loading: string;
    retry: string;
  };
};

export function RankingPager({
  data,
  firstRank,
  lastRank,
  loadedRows,
  hasMore,
  loading = false,
  failed = false,
  onLoadMore,
}: {
  data: RankingPagerData;
  /* 接咗新一批之後個範圍會變（#1–#100 → #1–#200），所以由叫方傳實際 render 緊嗰個範圍，
     唔用 `data` 入面 SSR 嗰版。 */
  firstRank?: number;
  lastRank?: number;
  /* 而家榜上一共 render 緊幾多行（SSR 嗰批 + 接落去嗰批）。冇傳 = 冇 append 能力。 */
  loadedRows?: number;
  /* 仲有冇下一批可以接。`undefined` = 叫方唔識答（冇 JS／SSR），行返舊邏輯睇 `nextHref`。 */
  hasMore?: boolean;
  loading?: boolean;
  failed?: boolean;
  onLoadMore?: () => void;
}) {
  const { page, pageSize, pageCount, sizeLinks, prevHref, nextHref, labels } = data;
  const sentinelRef = useRef<HTMLDivElement>(null);

  const canAppend = Boolean(onLoadMore) && nextHref !== null && hasMore !== false;
  /* 失敗咗就唔准再自動試——否則 sentinel 仲喺 viewport 入面，會變成無限重試風暴。
     要繼續就由用戶撳「再試一次」。 */
  const canAutoAppend = canAppend && !failed && (loadedRows ?? 0) < AUTO_APPEND_ROW_CAP;

  /* 用 ref 攞最新嘅 handler／狀態：IntersectionObserver 只想 observe 一次，
     唔想每次 loading 一 toggle 就 disconnect + 重新 observe（嗰下會漏咗一次交叉）。 */
  const stateRef = useRef({ loading, onLoadMore, canAutoAppend });
  /* render 期間唔准寫 ref（react-hooks error 級 rule）；effect 趕得切，
     observer 個 callback 一定係 commit 之後先行到。 */
  useEffect(() => { stateRef.current = { loading, onLoadMore, canAutoAppend }; }, [loading, onLoadMore, canAutoAppend]);

  const observerRef = useRef<IntersectionObserver | null>(null);
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    /* 舊瀏覽器冇 IntersectionObserver：唔崩，退化成「淨係得個掣」。 */
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      const state = stateRef.current;
      if (!entries.some((entry) => entry.isIntersecting)) return;
      if (!state.canAutoAppend || state.loading) return;
      state.onLoadMore?.();
    }, { rootMargin: SENTINEL_ROOT_MARGIN });
    observer.observe(node);
    observerRef.current = observer;
    return () => { observer.disconnect(); observerRef.current = null; };
  }, []);

  /* IntersectionObserver 只喺**狀態轉變**嗰陣先 call callback。用戶企咗喺頁尾唔郁，
     接完一批之後 sentinel 仍然係「交叉緊」＝冇轉變＝下一批永遠唔會嚟（實測卡死喺 200 行）。
     所以每次 load 完就 unobserve + observe 一次，攞返一個 initial notification：
     仲係交叉緊就再接落去，唔係就靜靜等下次碌。呢個唔會變無限 loop —— 接落去嘅行插喺
     pager 上面，pager 會被推低成 6000px，sentinel 自然離開範圍。 */
  useEffect(() => {
    if (loading) return;
    const node = sentinelRef.current;
    const observer = observerRef.current;
    if (!node || !observer) return;
    observer.unobserve(node);
    observer.observe(node);
  }, [loading, loadedRows]);

  /* 換頁／換數量：`scroll={false}` 之後要自己定位，否則會停喺原本個 scroll 位睇住
     另一批卡。對 `#market-ranking`（榜頂）唔係文件頂 —— 呢個就係 owner 投訴嗰點。 */
  const toRankingTop = () => {
    document.getElementById("market-ranking")?.scrollIntoView({ block: "start" });
  };

  if (pageCount <= 1 && pageSize === DEFAULT_RANKING_PAGE_SIZE) return null;
  const range = firstRank !== undefined && lastRank !== undefined ? `#${firstRank}–#${lastRank}` : null;
  /* 接咗幾多頁落去，個 `1/3` 就要講返實情（`1–3/3`），唔可以接到尾都仲寫住 `1/3`。 */
  const loadedPages = loadedRows && loadedRows > pageSize ? Math.ceil(loadedRows / pageSize) - 1 : 0;
  const lastLoadedPage = Math.min(pageCount, page + loadedPages);
  const pageLabel = lastLoadedPage > page ? `${page}–${lastLoadedPage}/${pageCount}` : `${page}/${pageCount}`;
  return (
    <>
      {/* 接落去嘅觸發線。擺喺 pager 上面，配 rootMargin 800px = 仲有成版先開始攞。
          `aria-hidden`：純幾何用途，讀屏唔應該見到一個空 div。 */}
      <div ref={sentinelRef} className="ranking-feed-sentinel" aria-hidden="true" />
      <nav className="watchlist-pager ranking-pager" aria-label={labels.pageSize}>
        <span className="ranking-page-sizes" role="group" aria-label={labels.pageSize}>
          {sizeLinks.map((link) => (
            <Link
              key={link.size}
              href={link.href}
              scroll={false}
              onClick={toRankingTop}
              data-active={link.active ? "true" : "false"}
              aria-current={link.active ? "true" : undefined}
            >
              {link.size}
            </Link>
          ))}
        </span>
        {pageCount > 1 ? (
          <span className="ranking-pager-nav">
            {prevHref
              ? <Link href={prevHref} scroll={false} onClick={toRankingTop} rel="prev" aria-label={labels.previous}>‹</Link>
              : <span aria-hidden="true">‹</span>}
            <span aria-current="page">
              {range ? `${range} · ` : null}{pageLabel}
            </span>
            {nextHref
              ? <Link href={nextHref} scroll={false} onClick={toRankingTop} rel="next" aria-label={labels.next}>›</Link>
              : <span aria-hidden="true">›</span>}
            {/* 有 append 能力嗰陣，接到尾就唔好再留個掣（撳都冇嘢發生）；
                冇 handler（冇 JS）就照舊淨係睇 `nextHref`。 */}
            {nextHref && (onLoadMore ? canAppend : true) ? (
              <Link
                className="ranking-show-more"
                href={nextHref}
                scroll={false}
                aria-busy={loading || undefined}
                onClick={(event) => {
                  /* 有 handler 先接管；冇 JS／冇 handler 就照行返個 href 換頁（SEO + 退化路徑）。 */
                  if (!canAppend) return;
                  event.preventDefault();
                  if (loading) return;
                  onLoadMore?.();
                }}
              >
                {loading ? labels.loading : failed ? labels.retry : labels.showMore}
              </Link>
            ) : null}
          </span>
        ) : null}
      </nav>
      {/* 讀屏公告：接緊落一批。`aria-live="polite"` 唔會打斷用戶。 */}
      <p className="ranking-feed-status" role="status" aria-live="polite">
        {loading ? labels.loading : null}
      </p>
    </>
  );
}
