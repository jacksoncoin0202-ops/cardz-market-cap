export const WATCHLIST_PAGE_SIZE = 200;

/*
 * 揀得嘅每頁數量。
 *
 * owner 2026-08-19：「永遠得 500 呀，冇 1000 嘅。最多都係 500，唔可以有一千，剷曬
 * 1000 啲掣啦。」—— 所以 1000 由正選剷咗，改為喺下面 alias 返落 500（舊 link 唔 404，
 * 但亦唔會出返 1000 行）。
 *
 * 三排數量掣（ranking-surface / rankings / sort-filter-sheet）全部 map 呢個 array，
 * 所以呢度加一個數量 = 三個地方一齊多咗粒掣，冇得只加一邊。
 */
export const RANKING_PAGE_SIZES = [100, 200, 300, 500] as const;

export type RankingPageSize = (typeof RANKING_PAGE_SIZES)[number];
export const DEFAULT_RANKING_PAGE_SIZE: RankingPageSize = 100;

/*
 * **自動／撳掣接落去**嘅累積上限，同時亦係每頁數量嘅硬頂（見下面個 guard）。
 *
 * owner 2026-08-18：「碌下碌下⋯⋯原來 show 到成 800 個項目，部機就會 lag 機，我要
 * F5 refresh 一次先可以更新返，所以先提供到 500 個。」—— 佢投訴嘅係「我冇要求過，
 * 但佢自己累積到」，所以夾嘅係**累積**，唔係「一版可以有幾多行」。
 */
const AUTO_APPEND_ROW_CAP = 500;

/*
 * ⚠️ 加返一個大過 500 嘅數量落 RANKING_PAGE_SIZES 唔係「多咗個選項」，係直接違反
 * owner 2026-08-19 嗰句「最多都係 500，唔可以有一千」，而且 `rankingRowCap` 會跟住
 * pageSize 走，即係佢原本投訴嗰個 800 行 lag 機直接返晒嚟。
 *
 * 呢個 module 榜頁 / sitemap / API 都 import，所以炸喺 import 嗰刻 = `next build`
 * 即刻紅，唔會靜靜出咗街先發現。唔准改做 console.warn。
 */
const oversized = RANKING_PAGE_SIZES.filter((size) => size > AUTO_APPEND_ROW_CAP);
if (oversized.length > 0) {
  throw new Error(`pagination: 每頁數量唔准大過 ${AUTO_APPEND_ROW_CAP}（見到 ${oversized.join("、")}）——「最多都係 500，唔可以有一千」`);
}

/*
 * 出過街、可能已經俾人 bookmark 或者爬蟲收咗嘅舊數量 → 對返落而家仲有嘅數量。
 * 榜尾嗰排數量掣一直係真 `<a href="?size=N">`，砍走一個數量唔可以順手踢佢做 404；
 * 但**亦唔准照舊出返嗰個數量**，所以係「收貨 + 對落 500」，唔係「收貨 + 出 1000」。
 */
const LEGACY_PAGE_SIZE_ALIASES = new Map<number, RankingPageSize>([[1000, 500]]);

/*
 * 榜上同一時間最多 render 幾多行。
 *
 * 而家所有選得嘅數量都 ≤ 500（上面個 guard 保住），所以呢度實際永遠回 500。
 * 個 `Math.max` 唔准拆走：佢係「pageSize 本身永遠算數」嗰條規矩嘅唯一實現，
 * 拆咗之後日後改數量就會靜靜出現「揀 400 但淨係 render 到 300」呢種未夾過嘅組合。
 *
 * 實際效果（1604 張榜）：size 100 接到 500 行、200 接到 400 行、300／500 一版到底
 * 唔再接。過咗頂唔係死路，`›` 會揭去下一版。
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
 * `?size=` 只收 RANKING_PAGE_SIZES，加上 LEGACY_PAGE_SIZE_ALIASES 嗰啲舊值（會對返落
 * 而家嘅數量）。冇傳 = 100。兩邊都唔中就 `null`，叫方決定 404 定 400 —— 呢度唔會
 * clamp 去最近嘅合法值（`?size=750` 係錯，唔係 500）。
 */
export function parseRequestedPageSize(raw: string | string[] | undefined): RankingPageSize | null {
  if (raw === undefined) return DEFAULT_RANKING_PAGE_SIZE;
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined) return DEFAULT_RANKING_PAGE_SIZE;
  if (!/^[1-9]\d*$/.test(value)) return null;
  const size = Number(value);
  const offered: readonly number[] = RANKING_PAGE_SIZES;
  if (offered.includes(size)) return size as RankingPageSize;
  return LEGACY_PAGE_SIZE_ALIASES.get(size) ?? null;
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
