export const WATCHLIST_PAGE_SIZE = 200;

/*
 * 揀得嘅每頁數量。owner 2026-08-18 先收窄做 `[100,200,500]`，同日再放返
 * 「加返 300 落去囉，鍾意 1000 加埋都得，唔大問題」—— 所以五個全部係正選。
 * 加數量落呢度**唔會**令「碌下碌下自動接到 lag 機」返嚟，因為自動接嘅上限
 * 係獨立嘅 AUTO_APPEND_ROW_CAP，唔跟呢個 list 嘅最大值走（見下面）。
 */
export const RANKING_PAGE_SIZES = [100, 200, 300, 500, 1000] as const;

/*
 * 出過街、可能已經俾人 bookmark 或者爬蟲收咗，但**唔再喺任何 UI 出現**嘅舊值。
 * 榜尾嗰排數量掣一直係真 `<a href="?size=N">`，砍走一個數量唔可以順手踢佢做 404。
 * 而家係空嘅（300 已經放返正選）—— 留住個機制，下次再砍就有位擺。
 */
const LEGACY_PAGE_SIZES = [] as const;

export type RankingPageSize = (typeof RANKING_PAGE_SIZES)[number] | (typeof LEGACY_PAGE_SIZES)[number];
export const DEFAULT_RANKING_PAGE_SIZE: RankingPageSize = 100;

/*
 * **自動／撳掣接落去**嘅累積上限。
 *
 * owner 2026-08-18：「碌下碌下⋯⋯原來 show 到成 800 個項目，部機就會 lag 機，我要
 * F5 refresh 一次先可以更新返，所以先提供到 500 個。」—— 佢投訴嘅係「我冇要求過，
 * 但佢自己累積到」，所以夾嘅係**累積**，唔係「一版可以有幾多行」。
 */
const AUTO_APPEND_ROW_CAP = 500;

/*
 * 榜上同一時間最多 render 幾多行。
 *
 * `pageSize` 本身永遠算數：用戶主動撳「1000」就係佢自己要一版 1000 行，唔准收埋
 * 一半當冇事發生。受夾嘅只係**接落去嗰部分**。
 *
 * 呢度**唔准**寫返 `Math.max(...RANKING_PAGE_SIZES)`（2026-08-18 早上嗰版就係咁）：
 * 咁寫嘅話 owner 一加 `1000` 落選項，「自動接落去」個上限就靜靜由 500 跳去 1000，
 * 即係佢原本投訴嗰個 lag 直接返晒嚟，而且冇任何地方睇得出。
 *
 * 實際效果（1604 張榜）：size 100 接到 500 行、200 接到 400 行、300／500／1000
 * 一版到底唔再接（再接一整版就爆）。過咗頂唔係死路，`›` 會揭去下一版。
 */
export function rankingRowCap(pageSize: number): number {
  return Math.max(pageSize, AUTO_APPEND_ROW_CAP);
}

export type RankingScope = "all" | "pokemon" | "one-piece";

/* 頁數唯一計法：榜頁、舊 watchlist API、sitemap 都行呢度，兩邊唔准各自 ceil。空榜都當 1 頁。 */
export function rankingPageCount(cardCount: number, pageSize: number): number {
  return Math.max(Math.ceil(cardCount / Math.max(pageSize, 1)), 1);
}

export function watchlistPageCount(cardCount: number): number {
  return rankingPageCount(cardCount, WATCHLIST_PAGE_SIZE);
}

/*
 * `?size=` 只收 RANKING_PAGE_SIZES 加埋 LEGACY_PAGE_SIZES。冇傳 = 100。唔喺名單
 * 入面就 `null`，叫方決定 404 定 400 —— 呢度唔 clamp 去最近嘅合法值。
 */
export function parseRequestedPageSize(raw: string | string[] | undefined): RankingPageSize | null {
  if (raw === undefined) return DEFAULT_RANKING_PAGE_SIZE;
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined) return DEFAULT_RANKING_PAGE_SIZE;
  if (!/^[1-9]\d*$/.test(value)) return null;
  const size = Number(value) as RankingPageSize;
  const accepted: readonly number[] = [...RANKING_PAGE_SIZES, ...LEGACY_PAGE_SIZES];
  return accepted.includes(size) ? size : null;
}

/*
 * `?page=` 嘅唯一解析點。頁面同 API 都行呢度，唔准各自寫一套。
 *
 * 之前兩處各自解析，兩處都用 `Number.parseInt`，即係兩處都有同一個洞：parseInt
 * 見到前綴數字就收貨，`"2zzz"` → 2、`"01"` → 1、`" 3"` → 3。頁面嗰邊仲要
 * `?? "1"` 兜底，跟住條數落 `scopeSnapshot` 俾 clamp 咗，於是
 * `/watchlist?page=99` 回 200 配第 1 頁內容 —— 一條唔存在嘅頁扮成功。
 *
 * 所以呢度分開兩件事：
 *  - **格式**（呢個 function）：唔係 1 開頭嘅十進位正整數就 `null`，唔 clamp、
 *    唔兜底。`""` `"0"` `"-1"` `"01"` `"2zzz"` `"abc"` `"1.5"` 全部 null。
 *  - **範圍**（叫方）：`null` 或者超出 pageCount 點處理，由叫方按 surface 決定
 *    —— 頁面 `notFound()`、API 回 400。呢度唔幫佢哋揀。
 *
 * 冇傳（`undefined`）唔係錯，係「第 1 頁」，所以回 1 唔係 null。
 */
export function parseRequestedPage(raw: string | string[] | undefined): number | null {
  if (raw === undefined) return 1;
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined) return 1;
  if (!/^[1-9]\d*$/.test(value)) return null;
  return Number(value);
}
