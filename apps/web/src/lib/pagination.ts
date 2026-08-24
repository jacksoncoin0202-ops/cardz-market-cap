export const WATCHLIST_PAGE_SIZE = 200;
export const WATCHLIST_PAGE_SIZE_MAX = 500;

export function normalisePageSize(value: number | undefined): number {
  const candidate = Math.trunc(value ?? WATCHLIST_PAGE_SIZE);
  if (!Number.isFinite(candidate)) return WATCHLIST_PAGE_SIZE;
  return Math.min(Math.max(candidate, 1), WATCHLIST_PAGE_SIZE_MAX);
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
