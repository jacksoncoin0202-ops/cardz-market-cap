/*
 * 呢個骨架係榜頁形狀（rank / 縮圖 / 卡名 / 數值 一行行），所以只放喺榜頁嗰組。
 *
 * 佢原本喺 `app/loading.tsx`，即係 root-level：Next 會用佢喺 root layout 下面
 * 包一個 Suspense，令**每一條 route**（連 `/card/[id]`）都變成先沖 shell 再
 * stream 內容。後果唔止係詳情頁閃一版榜頁骨架，而係 HTTP status 喺 shell 沖出街
 * 嗰刻就鎖死 200 —— 全 app 任何 `notFound()` 都改唔到 status，實測
 * `/card/does-not-exist` 出 200 配 not-found body。搬入 `(market)` route group
 * 之後，榜頁（`/` `/pokemon` `/one-piece` `/tune`）骨架照舊，
 * `/card/[id]` 唔再被包住，`notFound()` 先回到真 404。
 *
 * `/watchlist` 之後亦都因為同一個理由搬咗出去（`?page=` 出範圍要回 404），
 * 所以佢已經冇咗骨架。
 *
 * 加新 route 嗰陣：要骨架就放入 `(market)/`，要控制 status 就放喺外面。
 * 反過嚟：唔准喺 `app/card/` 或者 `app/watchlist/` 加返 `loading.tsx` —— 加咗
 * 就靜靜地打返轉頭，兩邊嘅 404 一齊變返 200，而且冇任何嘢會紅。
 */
export default function Loading() {
  return (
    <div className="page-shell market-page-shell" aria-hidden="true">
      <div className="skeleton-hero">
        <div className="skeleton-block skeleton-kicker" />
        <div className="skeleton-block skeleton-title" />
        <div className="skeleton-block skeleton-copy" />
      </div>
      <div className="skeleton-block skeleton-strip" />
      <div className="skeleton-rows">
        {Array.from({ length: 8 }, (_, index) => (
          <div className="skeleton-row" key={index}>
            <div className="skeleton-block skeleton-rank" />
            <div className="skeleton-block skeleton-thumb" />
            <div className="skeleton-lines">
              <div className="skeleton-block skeleton-line-name" />
              <div className="skeleton-block skeleton-line-sub" />
            </div>
            <div className="skeleton-block skeleton-value" />
          </div>
        ))}
      </div>
    </div>
  );
}
