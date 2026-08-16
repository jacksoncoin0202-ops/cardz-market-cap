"use client";

import { useEffect } from "react";

/*
 * 返上一頁要返返原本個位（owner 2026-08-17：碌到 #98 撳入卡頁，撳返上一頁彈返熱力圖頂）。
 *
 * 點解要自己揸：
 *  1. App Router 嘅 back 係 same-document popstate。實測（Playwright, 390×844）popstate 嗰刻
 *     個 document 仲係卡頁——高 4173px，而榜頁係 14498px——所以瀏覽器原生 restore 攞唔到
 *     12633 呢個位（夾到剩返卡頁高度），榜頁要再過一幀（+126ms）先 render 出嚟。
 *  2. `html { scroll-behavior: smooth }`（globals.css）令原生 restore 變咗成秒幾嘅動畫；
 *     卡頁自己碌過（實測 y=900）就索性一下都唔 restore，#98 差 11,709px。
 *  3. render 完之後個 document 仲會縮（實測 14,634 → 13,834，圖同 sparkline 落位），
 *     所以 restore 唔可以一次過就算，要 retry 到高度夠 + 停定。
 *
 * 所以：`history.scrollRestoration = "manual"` 熄咗原生嗰個（唔好兩邊打交），
 * 自己逐個 history entry 記低 scrollY（sessionStorage，key = pathname + search，
 * 因為 Next 個 `history.state` 得 `__NA` / internals tree，冇 per-entry key），
 * popstate（同埋 reload / 跨 document 嘅 back）先至 restore，用 behavior:"instant"。
 *
 * URL 係唯一真身：呢度一個 param 都唔寫、一個都唔讀，只係記個位。
 */

const STORE_PREFIX = "cardz-scroll:";
/* 每 150ms 最多寫一次 sessionStorage：passive listener + 節流，唔阻碌 */
const SAVE_THROTTLE_MS = 150;
/* 追到 1.5s 就收手：再唔到位多數係內容真係短咗，繼續搶 scroll 只會阻住用戶 */
const RESTORE_BUDGET_MS = 1500;
function entryKey(): string {
  return `${STORE_PREFIX}${window.location.pathname}${window.location.search}`;
}

function readSaved(key: string): number | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw === null) return null;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}

export function ScrollRestoration() {
  useEffect(() => {
    /* overlay 開住嗰陣 scroll-lock 會 body{position:fixed} 令 scrollY 變 0（touch 機），
       嗰下 scroll event 唔准當係用戶碌到頂——記咗 0 就等於返嚟直接彈返頂。 */
    const locked = () => document.documentElement.classList.contains("sheet-open");

    let restoring = false;
    let restoreFrame = 0;
    let saveTimer: ReturnType<typeof setTimeout> | null = null;

    const save = () => {
      saveTimer = null;
      if (restoring || locked()) return;
      try {
        window.sessionStorage.setItem(entryKey(), String(Math.round(window.scrollY)));
      } catch { /* 私隱模式／爆 quota：唔記得個位好過炸 */ }
    };

    const onScroll = () => {
      if (restoring || locked() || saveTimer) return;
      saveTimer = setTimeout(save, SAVE_THROTTLE_MS);
    };

    const stopRestore = () => {
      restoring = false;
      if (restoreFrame) cancelAnimationFrame(restoreFrame);
      restoreFrame = 0;
    };

    const restore = (target: number) => {
      stopRestore();
      restoring = true;
      const started = performance.now();
      const step = () => {
        restoreFrame = 0;
        if (!restoring) return;
        /* 內容未 render 晒就 scrollTo 到幾多得幾多，下一幀再追（唔可以一次就放棄）。
           behavior:"instant" 係硬性：html{scroll-behavior:smooth} 會令 restore 變動畫。 */
        window.scrollTo({ top: target, left: window.scrollX, behavior: "instant" });
        /* 到咗位都唔准即刻收：document 高度仲會郁（圖／sparkline 落位，實測 14,634 → 13,834），
           一次過就算會被之後嗰次 reflow 推走。所以追足 budget，除非用戶自己出手。 */
        if (performance.now() - started >= RESTORE_BUDGET_MS) {
          restoring = false;
          return;
        }
        restoreFrame = requestAnimationFrame(step);
      };
      restoreFrame = requestAnimationFrame(step);
    };

    /* 用戶自己出手就即刻收手，唔好同佢爭 scroll */
    const onUserScrollIntent = () => stopRestore();

    const onPopState = () => {
      /* popstate 嗰刻 location 已經係返咗去嗰條 URL，所以 key 攞到嘅就係目標位 */
      const target = readSaved(entryKey());
      if (target === null) return;
      restore(target);
    };

    const previousRestoration = history.scrollRestoration;
    try { history.scrollRestoration = "manual"; } catch { /* 舊瀏覽器唔支援就照行落去 */ }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("popstate", onPopState);
    window.addEventListener("pagehide", save);
    window.addEventListener("wheel", onUserScrollIntent, { passive: true });
    window.addEventListener("touchstart", onUserScrollIntent, { passive: true });
    window.addEventListener("keydown", onUserScrollIntent);

    /* 跨 document 嘅 back／forward／reload：scrollRestoration 設咗 manual，原生唔會做，
       所以呢度自己補返（bfcache 命中嗰種唔會行到呢度，瀏覽器已經還原晒）。 */
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (navigation?.type === "back_forward" || navigation?.type === "reload") {
      const target = readSaved(entryKey());
      if (target !== null) restore(target);
    }

    return () => {
      stopRestore();
      if (saveTimer) clearTimeout(saveTimer);
      try { history.scrollRestoration = previousRestoration; } catch { /* ignore */ }
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("pagehide", save);
      window.removeEventListener("wheel", onUserScrollIntent);
      window.removeEventListener("touchstart", onUserScrollIntent);
      window.removeEventListener("keydown", onUserScrollIntent);
    };
  }, []);
  return null;
}
