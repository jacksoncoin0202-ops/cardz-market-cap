/*
 * 攞一張分享圖（`/api/og/card/[id]` 或者 `/api/og/heatmap`）。
 *
 * ⚠️ 卡片內頁同熱力圖共用呢一個（AGENTS.md 規矩 13）。本來熱力圖有 retry 版、卡片
 * 內頁自己寫住一個 20 秒死 timeout —— 卡片內頁 2026-08-22 開 4K 之後，20 秒就係
 * 「揀咗 4K 一定 fail」。兩邊同一個病，唔可以兩份藥。
 *
 * 「一次 fetch 攞到」呢個假設喺 4K 度係錯嘅。
 * 2026-08-21 喺真站量：4K 每次都俾 gateway 喺 60 秒斬（504），但 server 冇停手，
 * 100–126 秒之後張圖已經喺 cache 度等緊。所以 4K 唔係「等耐啲」，係「踢一腳、
 * 等、再攞返**同一條 URL**」。
 *
 * 「同一條 URL」係關鍵：熱力圖 `stamp=now` 個 cache key 帶住分鐘，所以嗰邊會用
 * `at=` 釘死嗰一刻（見 `lib/share-stamp.ts`）。冇個 pin 就係每次 retry 都由頭 render。
 */
import {
  RESOLUTION_RETRY_BUDGET_MS,
  RESOLUTION_TIMEOUT_MS,
  SHARE_RETRY_FIRST_WAIT_MS,
  SHARE_RETRY_POLL_MS,
  SHARE_RETRY_STATUSES,
  type ShareResolution,
} from "./share-resolution";

/*
 * ⚠️ 個 timeout **跟清晰度走**，唔准寫死一個：1080p 45 秒，4K 實測 50–57 秒，
 * 沿用 45 秒就係次次喺就快出到嗰陣自己斬自己。真身喺 `lib/share-resolution.ts`
 * `RESOLUTION_TIMEOUT_MS`（CLI 都讀同一張表）。
 */
const shareFetchTimeoutMs = (res: ShareResolution) => RESOLUTION_TIMEOUT_MS[res];

export async function fetchShareBlob(path: string, res: ShareResolution, label: string): Promise<Blob> {
  const deadline = Date.now() + RESOLUTION_RETRY_BUDGET_MS[res];
  let why = "";
  let tries = 0;
  while (Date.now() < deadline) {
    tries += 1;
    const per = Math.min(shareFetchTimeoutMs(res), deadline - Date.now());
    let response: Response | null = null;
    try {
      response = await fetch(path, {
        signal: typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(per) : undefined,
      });
    } catch (error) {
      why = error instanceof Error ? error.message : String(error);
    }
    if (response?.ok) return await response.blob();
    /* 唔喺 retry 名單 = 唔係 gateway 唔想等，係真係錯。再試幾多次都一樣。 */
    if (response && !SHARE_RETRY_STATUSES.includes(response.status as (typeof SHARE_RETRY_STATUSES)[number])) {
      throw new Error(`${label} HTTP ${response.status}`);
    }
    if (response) why = `HTTP ${response.status}`;
    /* 第一腳之後等耐啲 —— 每次 cache miss 都係 server 度多開一個 render。 */
    const wait = tries === 1 ? SHARE_RETRY_FIRST_WAIT_MS : SHARE_RETRY_POLL_MS;
    if (Date.now() + wait >= deadline) break;
    await new Promise((done) => setTimeout(done, wait));
  }
  throw new Error(`${label} 攞唔到：${why}`);
}
