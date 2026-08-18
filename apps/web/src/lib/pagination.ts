export const WATCHLIST_PAGE_SIZE = 200;

/* 揀得嘅每頁數量（owner 2026-08-18：「俾人去揀 100、200 或者 500 個⋯⋯一千太誇張」）。
   300 冇人用，順手砍走 —— 但佢曾經係真 `<a href>` 出過街，見 LEGACY_PAGE_SIZES。 */
export const RANKING_PAGE_SIZES = [100, 200, 500] as const;

/*
 * 出過街、可能已經俾人 bookmark 或者爬蟲收咗嘅舊值。**唔再喺任何 UI 出現**，
 * 但 URL 照收 —— 榜尾嗰排數量掣一直係真 `<a href="?size=300">`，直接踢佢做 404
 * 就係將已收錄嘅 URL 打死。呢個 list 只會縮唔會長：新數量一律加落
 * RANKING_PAGE_SIZES。
 */
const LEGACY_PAGE_SIZES = [300] as const;

export type RankingPageSize = (typeof RANKING_PAGE_SIZES)[number] | (typeof LEGACY_PAGE_SIZES)[number];
export const DEFAULT_RANKING_PAGE_SIZE: RankingPageSize = 100;

/*
 * 榜上同一時間最多 render 幾多行（碌到底自動接落去 + 撳「展示更多」都受呢個數夾）。
 *
 * owner 2026-08-18：「碌下碌下⋯⋯原來 show 到成 800 個項目，部機就會 lag 機，
 * 我要 F5 refresh 一次先可以更新返，所以先提供到 500 個。」—— 即係「最多揀到嘅每頁
 * 數量」同「畫面最多幾多行」係同一個數，所以呢度**由 RANKING_PAGE_SIZES 推導**，
 * 唔准寫死。寫死嘅話下次有人加個 800 落去，cap 就會靜靜變咗「揀到但顯示唔到」。
 */
export const RANKING_ROW_CAP = Math.max(...RANKING_PAGE_SIZES);

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
