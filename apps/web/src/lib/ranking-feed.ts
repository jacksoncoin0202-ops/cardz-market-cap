import type { MarketCardView } from "./types";
import type { RankingScope } from "./pagination";

/*
 * 榜單「碌到底接落去」嘅取數（owner 2026-08-18：「撳展示更多唔應該彈我返首頁頂，
 * 我想繼續碌落去」）。
 *
 * 點解唔行 router：`<Link href="?page=2">` 係**換頁**——成個 list 由 #101 開始重畫，
 * 就算加咗 `scroll={false}` 唔彈頂，畫面一樣係「同一個位、另一批卡」，更加亂。要
 * 「繼續」就一定要**接落去原本嗰批下面**，即係 client 攞多一版 rows 出嚟 concat。
 *
 * 用返 `/api/v1/market`：佢本來就係同一個 baked snapshot 出嚟（`lib/data/market.ts`
 * → `scopeSnapshot`），欄位同 SSR 落嚟嗰批**一模一樣**（實測 `?scope=all&page=2&pageSize=100`
 * 出 100 行、rank 101–200，帶齊 image.variants / width / height、pricePsa10、marketCap、
 * windows、historyDaily、salesSparkline），所以接落去唔會出現「新嗰批冇 sparkline」呢種
 * 半殘行。**唔准為咗慳流量改用 `/api/v1/catalog`** —— catalog entry 冇價冇市值冇走勢，
 * `catalogToCard()` 補唔返，接落去會即刻見到兩種行。
 *
 * 個 route 自己有 `Cache-Control: public, max-age=60`，所以碌上碌落唔會逐次真係打 origin。
 */

/* API 個 param 叫 `pageSize`（唔係頁面 URL 嗰個 `size`）；scope 用返同一組字。 */
export function rankingFeedUrl(scope: RankingScope, page: number, pageSize: number): string {
  const query = new URLSearchParams({ scope, page: String(page), pageSize: String(pageSize) });
  return `/api/v1/market?${query.toString()}`;
}

/*
 * 攞一版返嚟。失敗一律 throw，叫方負責出「再試一次」——**唔准靜靜回空 array**：
 * 空 array 同「呢版真係冇卡」分唔開，用戶會以為榜到咗尾。
 */
export async function fetchRankingPage(
  scope: RankingScope,
  page: number,
  pageSize: number,
  signal?: AbortSignal,
): Promise<MarketCardView[]> {
  const response = await fetch(rankingFeedUrl(scope, page, pageSize), { signal });
  if (!response.ok) throw new Error(`ranking feed ${response.status}`);
  const payload = (await response.json()) as { cards?: MarketCardView[] };
  if (!Array.isArray(payload.cards)) throw new Error("ranking feed: cards 唔係 array");
  return payload.cards;
}
