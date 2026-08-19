/*
 * 「一張圖 blob → 出到用戶手上」嘅唯一實現。
 *
 * 本來只活喺 `components/heatmap.tsx` 個 `exportHeatmap` 入面。卡片內頁 2026-08-19 加
 * 分享圖之後就有第二個叫方 —— 但呢段嘢有幾個好易寫漏嘅細節（AbortError 唔算錯、
 * 桌面唔准行 Web Share、anchor 要入 DOM、objectURL 要**延後**先 revoke、fallback 要
 * 順手抄埋條 link），兩處各寫一次一定會有一邊漏（AGENTS.md 規矩 13）。
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
  /** 連 `<a download>` 都俾瀏覽器（多數係 in-app webview）擋咗，唯有開新分頁俾佢自己長按儲存 */
  | "opened"
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

/*
 * ⚠️ **唔准縮短、唔准改做 0**（2026-08-19 owner 報「撳完轉圈好耐，跟住直頭冇反應」嗰單）。
 *
 * 落載係 async 開始嘅：`anchor.click()` 只係排低咗個 download，瀏覽器要遲一陣先真正
 * 去攞條 blob URL。喺同一個 synchronous block 入面 `revokeObjectURL` = 喺佢攞之前
 * 就收咗條 URL，**表現係撳完乜都冇發生** —— 冇 error、冇 console、冇 network entry。
 * 舊 code 就係緊接住 `click()` 直接 revoke。
 */
const REVOKE_DELAY_MS = 60_000;

/*
 * Web Share **只喺手機行**。
 *
 * Windows 11 三隻瀏覽器（Chrome / Edge / Firefox）share 一個 `files` 陣列一律回
 * `NotAllowedError: Permission denied`（mdn/browser-compat-data#21312，收咗做
 * "not planned"），而 `navigator.canShare({ files })` 喺出事之前仲要回 **true**。
 * 即係話舊 code 喺桌面一定會行入 share 條路，等 Windows share flyout 慢慢開、慢慢炒，
 * 先至跌返落 download —— 而嗰條 download 路本身又被上面條 revoke race 整死咗。
 *
 * 桌面用戶想要嘅本來就係「儲存張圖」，唔使經 OS share sheet。所以呢度直接唔問。
 */
function prefersNativeShare(): boolean {
  const uaData = (navigator as Navigator & { userAgentData?: { mobile?: boolean } }).userAgentData;
  if (typeof uaData?.mobile === "boolean") return uaData.mobile;
  /* Safari 冇 userAgentData。iPadOS 預設報 Mac UA，靠 touch point 認返。 */
  if (/Android|iPhone|iPod|iPad/i.test(navigator.userAgent)) return true;
  return navigator.maxTouchPoints > 1 && /Mac/i.test(navigator.userAgent);
}

/** 回 false = 呢個瀏覽器根本唔支援 `<a download>`（多數係 in-app webview），叫方要再跌一級。 */
function downloadBlob(blob: Blob, filename: string): boolean {
  const anchor = document.createElement("a");
  if (!("download" in anchor)) return false;
  const url = URL.createObjectURL(blob);
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = "noopener";
  anchor.style.display = "none";
  /* ⚠️ 一定要入咗 DOM 先 click：detached `<a>` 喺 Firefox 完全唔會落載。 */
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
  return true;
}

export async function shareImageBlob(blob: Blob, opts: ShareImageOptions): Promise<ShareOutcome> {
  const file = new File([blob], opts.filename, { type: blob.type || "image/png" });
  const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
  const shareData: ShareData = { files: [file], title: opts.title, text: opts.text };

  /*
   * 1) 手機有 Web Share Level 2（Android Chrome / iOS Safari）就出**系統 share sheet**：
   *    LINE、WhatsApp、IG、Threads、「儲存到相簿」全部由 OS 俾人揀，張圖 + 標題 + 連結一齊落。
   * 2) 用戶自己撳走 = "dismissed"，靜靜完成。
   * 3) 冇 share 或者 share 本身失敗先 fallback 落 download —— 呢個係最後一步，唔係第一步。
   */
  if (prefersNativeShare() && typeof nav.share === "function" && nav.canShare?.(shareData)) {
    try {
      await nav.share(shareData);
      return "shared";
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return "dismissed";
      /* NotAllowedError（activation 過期）／其他：跌落下面 download */
    }
  }

  const downloaded = downloadBlob(blob, opts.filename);
  if (opts.clipboardFallbackText) {
    /* 落載咗張圖但冇 share sheet 貼唔到條 link，順手抄入剪貼簿；抄唔到唔算錯。 */
    navigator.clipboard?.writeText(opts.clipboardFallbackText).catch(() => undefined);
  }
  if (downloaded) return "downloaded";

  /*
   * 最後一級：`<a download>` 都冇（老 webview）。開新分頁俾用戶自己長按儲存，總好過
   * 靜靜咩都唔發生。彈窗被擋就要嗌 —— 呢個係唯一一個真係交唔到貨嘅情況。
   */
  const url = URL.createObjectURL(blob);
  const opened = window.open(url, "_blank", "noopener");
  if (!opened) {
    URL.revokeObjectURL(url);
    throw new Error("share image: 落載同開新分頁都俾瀏覽器擋咗");
  }
  setTimeout(() => URL.revokeObjectURL(url), REVOKE_DELAY_MS);
  return "opened";
}
