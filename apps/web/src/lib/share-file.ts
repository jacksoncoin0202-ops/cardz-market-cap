/*
 * 「一張圖 blob → 出到用戶手上」嘅唯一實現。
 *
 * 本來只活喺 `components/heatmap.tsx` 個 `exportHeatmap` 入面。卡片內頁 2026-08-19 加
 * 分享圖之後就有第二個叫方 —— 但呢段嘢有四個好易寫漏嘅細節（AbortError 唔算錯、
 * NotAllowedError 要跌落 download、objectURL 要 revoke、fallback 要順手抄埋條 link），
 * 兩處各寫一次一定會有一邊漏（AGENTS.md 規矩 13）。
 *
 * ⚠️ `navigator.share` **一定要喺 user activation 之內**叫。即係話 blob 要喺撳掣之前／
 * 撳落去嗰刻已經準備緊（見 CopyButton 個 `onWarm`），唔可以撳完先慢慢 fetch 幾百 KB ——
 * activation 過咗期 Safari 會掟 `NotAllowedError`，用戶見到嘅係「冇彈 share sheet，
 * 直接落載咗個檔」。跌得返 download 唔算爆，但唔係我哋想要嘅路。
 */
export type ShareOutcome =
  /** 系統 share sheet 出咗，用戶揀咗嘢 */
  | "shared"
  /** 冇 Web Share（或者佢失敗），已經幫用戶 download 咗個檔 */
  | "downloaded"
  /** 用戶自己撳走 share sheet —— **唔係錯**，叫方唔好報 error，亦唔好再做後續動作 */
  | "dismissed";

export interface ShareImageOptions {
  /** 落載檔名，要連 `.png` */
  filename: string;
  /** share sheet 標題（跟返介面語言 —— 同圖入面一律英文嗰條規矩無關） */
  title: string;
  /** share sheet 正文，一般係「標題 + 換行 + 頁面連結」 */
  text: string;
  /** 冇得 share、要跌落 download 嗰陣順手抄入剪貼簿嘅文字（通常係頁面 URL） */
  clipboardFallbackText?: string;
}

export async function shareImageBlob(blob: Blob, opts: ShareImageOptions): Promise<ShareOutcome> {
  const file = new File([blob], opts.filename, { type: blob.type || "image/png" });
  const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
  const shareData: ShareData = { files: [file], title: opts.title, text: opts.text };

  /*
   * 1) 有 Web Share Level 2（Android Chrome / iOS Safari / Chrome）就出**系統 share sheet**：
   *    LINE、WhatsApp、IG、Threads、「儲存到相簿」全部由 OS 俾人揀，張圖 + 標題 + 連結一齊落。
   * 2) 用戶自己撳走 = "dismissed"，靜靜完成。
   * 3) 冇 share 或者 share 本身失敗先 fallback 落 download —— 呢個係最後一步，唔係第一步。
   */
  if (typeof nav.share === "function" && nav.canShare?.(shareData)) {
    try {
      await nav.share(shareData);
      return "shared";
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return "dismissed";
      /* NotAllowedError（activation 過期）／其他：跌落下面 download */
    }
  }

  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = opts.filename;
  anchor.click();
  URL.revokeObjectURL(url);
  if (opts.clipboardFallbackText) {
    /* 落載咗張圖但冇 share sheet 貼唔到條 link，順手抄入剪貼簿；抄唔到唔算錯。 */
    navigator.clipboard?.writeText(opts.clipboardFallbackText).catch(() => undefined);
  }
  return "downloaded";
}
