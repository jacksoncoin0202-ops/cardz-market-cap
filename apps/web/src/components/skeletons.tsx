import "@/app/styles/skeleton.css";

/*
 * 共用骨架（FE05 WS4）。
 *
 * 原本淨係 `(market)/loading.tsx` 有一份 inline JSX；卡頁要 Suspense fallback
 * 嗰陣如果照抄一份，`.skeleton-*` 就會有兩個 owner，改一邊唔改另一邊。所以抽咗
 * 落嚟一個檔，CSS 亦都跟住由 `styles/skeleton.css` 一齊擁有。
 *
 * 骨架一律 `aria-hidden`：佢係佔位盒，唔係內容。讀屏靠 route 本身嘅 loading
 * 語意（Next 會用 Suspense）同埋真內容出現，唔靠呢啲盒講嘢。
 *
 * 幾何規矩：**借真頁嘅 class 攞 footprint**，骨架只塞入面（`heatmap-section`、
 * `detail-grid` / `detail-art` / `detail-content`…）。自己另起一套版面 = 兩套要
 * 同步嘅數字，swap 嗰下實跳版。
 */

/* 榜頁第一屏：heatmap 佔 100svh − header 嗰個 grid，同真頁同一個 class。 */
export function MarketHeroSkeleton() {
  return (
    <div className="heatmap-section skeleton-heatmap">
      <div className="skeleton-hero">
        {/* 真 heading 而家只有一行 H1 + 一行總市值（fix-heatmap-title），kicker / 描述句已冇 */}
        <div className="skeleton-block skeleton-title" />
        <div className="skeleton-block skeleton-copy" />
      </div>
      <div className="skeleton-block skeleton-canvas" />
      <div className="skeleton-block skeleton-strip" />
    </div>
  );
}

/* 榜表行：desktop 42×60 縮圖 + 82px 行，≤980px 五欄 grid（同 .mobile-rank-card）。 */
export function RankingRowsSkeleton({ rows = 8 }: { rows?: number }) {
  return (
    <div className="skeleton-rows">
      {Array.from({ length: rows }, (_, index) => (
        <div className="skeleton-row" key={index}>
          <div className="skeleton-block skeleton-rank" />
          <div className="skeleton-block skeleton-thumb" />
          <div className="skeleton-lines">
            <div className="skeleton-block skeleton-line-name" />
            <div className="skeleton-block skeleton-line-sub" />
          </div>
          <div className="skeleton-block skeleton-value" />
          <div className="skeleton-block skeleton-spark" />
        </div>
      ))}
    </div>
  );
}

/*
 * 卡頁骨架（`CardDetailSkeleton`）**冇咗**，唔係漏做（FE05 WS4，review 2026-08-17）。
 *
 * 兩個位都放唔落：`app/card/loading.tsx` 會鎖死 HTTP 200 打爛 `notFound()`；route 入面
 * 自己包 `<Suspense>` 又會令 `CardDetail` 成個 client subtree（`useSearchParams()` 喺 SSR
 * 期間 suspend）跌入 `<div hidden id="S:1">`，raw HTML 冇咗 `<h1>` 同市值 —— 卡頁係 GEO
 * 主力頁，收唔起。而且個 fallback 實測（CDP 限速 40KB/s）一次都冇畫出嚟。
 * 詳細數字同重開條件寫喺 `app/card/[id]/page.tsx` 個 default export 上面。
 */
