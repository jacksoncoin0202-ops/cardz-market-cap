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
 *  3. render 完之後個 document 仲會郁（圖／sparkline 落位），所以 restore 唔可以一次過就算。
 *
 * 記位用 anchor，唔淨係記絕對 Y（2026-08-17 live 覆核：dark before y=2925 → after y=3010，
 * light before y=2865 → after 一樣 3010，即係「同一個 Y」喺兩次 render 唔係同一行——上面
 * 嘅圖／熱力圖／pager 高度會變）。所以除咗 Y，仲要記低「當時黐住 sticky 底下第一張卡」
 * 係邊張（href）同佢距離 viewport 頂幾多，返嚟就對返呢張卡擺位；搵唔返先用返 Y。
 *
 * 存檔時機：碌完（throttle 尾段）＋ 撳任何站內 <a>（capture phase，navigate 之前）＋
 * pagehide ＋ visibilitychange→hidden。淨靠 throttle 會執到 smooth-scroll 中途嗰個位。
 *
 * URL 係唯一真身：呢度一個 param 都唔寫、一個都唔讀，只係記個位。
 */

const STORE_PREFIX = "cardz-scroll:";
/* 每 150ms 最多寫一次 sessionStorage：passive listener + 節流，唔阻碌 */
const SAVE_THROTTLE_MS = 150;
/* 追到 2.5s 就收手：卡圖／sparkline 喺 back 之後先落位，1.5s 唔夠食晒 */
const RESTORE_BUDGET_MS = 2500;
/* 榜上每張卡都係一條 /card/ link——手機 .mobile-rank-card 本身係 <a>，桌面喺 td 入面 */
const ANCHOR_SELECTOR = 'a[href^="/card/"]';

type Saved = { y: number; href: string | null; off: number };

function entryKey(): string {
  return `${STORE_PREFIX}${window.location.pathname}${window.location.search}`;
}

/* sticky header + sticky 榜標題遮住嘅高度：anchor 唔可以揀藏喺佢哋後面嗰行 */
function stickyBottom(): number {
  let bottom = 0;
  document.querySelectorAll<HTMLElement>("header, .ranking-heading").forEach((el) => {
    const style = getComputedStyle(el);
    if (style.position !== "sticky" && style.position !== "fixed") return;
    const rect = el.getBoundingClientRect();
    /* 貼到位（top ≈ 佢個 sticky offset）先當佢遮住嘢 */
    if (rect.top <= (parseFloat(style.top) || 0) + 2 && rect.bottom > bottom) bottom = rect.bottom;
  });
  return bottom;
}

function findAnchor(): { href: string; off: number } | null {
  const limit = stickyBottom() - 1;
  const links = document.querySelectorAll<HTMLAnchorElement>(ANCHOR_SELECTOR);
  let above: { href: string; off: number } | null = null;
  for (const link of links) {
    const href = link.getAttribute("href");
    if (!href) continue;
    const top = link.getBoundingClientRect().top;
    /* 首選：sticky 底下第一張（用戶眼下嗰張） */
    if (top >= limit) return { href, off: top };
    /* 唔夠位就記住最貼近上面嗰張——碌到榜尾嗰陣（實測 scrollY 12593，row 100 喺 top=90
       而 stickyBottom=93）一張都揀唔到，就會跌返絕對 Y，正正係最脆嗰條路。
       off 容許負數：返嚟一樣係擺返同一個相對位。 */
    above = { href, off: top };
  }
  return above;
}

function anchorElement(href: string): HTMLAnchorElement | null {
  const links = document.querySelectorAll<HTMLAnchorElement>(ANCHOR_SELECTOR);
  for (const link of links) {
    if (link.getAttribute("href") === href) return link;
  }
  return null;
}

function readSaved(key: string): Saved | null {
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw === null) return null;
    const parsed = JSON.parse(raw) as Partial<Saved>;
    const y = Number(parsed?.y);
    if (!Number.isFinite(y) || y <= 0) return null;
    return {
      y,
      href: typeof parsed.href === "string" ? parsed.href : null,
      off: Number.isFinite(Number(parsed.off)) ? Number(parsed.off) : 0,
    };
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
      if (saveTimer) {
        clearTimeout(saveTimer);
        saveTimer = null;
      }
      if (restoring || locked()) return;
      const y = Math.round(window.scrollY);
      if (y <= 0) {
        /* 真係喺頂（唔係 scroll-lock 造成嘅 0）：清走舊記錄，唔好返嚟彈去上一次碌到嘅位 */
        try { window.sessionStorage.removeItem(entryKey()); } catch { /* ignore */ }
        return;
      }
      const anchor = findAnchor();
      const record: Saved = { y, href: anchor?.href ?? null, off: Math.round(anchor?.off ?? 0) };
      try {
        window.sessionStorage.setItem(entryKey(), JSON.stringify(record));
      } catch { /* 私隱模式／爆 quota：唔記得個位好過炸 */ }
    };

    const onScroll = () => {
      if (restoring || locked() || saveTimer) return;
      saveTimer = setTimeout(save, SAVE_THROTTLE_MS);
    };

    /* capture phase：Next 個 <Link> 會 preventDefault 之後自己 push，所以要喺佢之前存。
       throttle 嗰下可能仲喺 smooth-scroll 中途，唔可以當佢係最終位。 */
    const onClickCapture = (event: MouseEvent) => {
      const target = event.target as Element | null;
      const link = target?.closest?.("a[href]") as HTMLAnchorElement | null;
      if (!link) return;
      const href = link.getAttribute("href");
      if (!href || href.startsWith("#")) return;
      if (link.target && link.target !== "_self") return;
      /* 站內先算（相對路徑或者同 origin） */
      if (/^[a-z]+:/i.test(href) && link.origin !== window.location.origin) return;
      save();
    };

    const onVisibility = () => {
      if (document.visibilityState === "hidden") save();
    };

    const stopRestore = () => {
      restoring = false;
      if (restoreFrame) cancelAnimationFrame(restoreFrame);
      restoreFrame = 0;
    };

    const restore = (saved: Saved) => {
      stopRestore();
      restoring = true;
      const started = performance.now();
      let anchor: HTMLAnchorElement | null = null;
      const step = () => {
        restoreFrame = 0;
        if (!restoring) return;
        if (saved.href && (!anchor || !anchor.isConnected)) anchor = anchorElement(saved.href);
        /* 有返嗰張卡就用相對位：上面嘅圖／熱力圖／pager 高度變咗都唔影響，
           絕對 Y 會變咗第二行（live 實測 before y=2925/2865 → after 兩次都係 3010）。 */
        if (anchor) {
          const delta = anchor.getBoundingClientRect().top - saved.off;
          /* behavior:"instant" 係硬性：html{scroll-behavior:smooth} 會令 restore 變動畫 */
          if (Math.abs(delta) >= 0.5) window.scrollBy({ top: delta, left: 0, behavior: "instant" });
        } else {
          /* 榜未 render／換咗一批卡：用返絕對 Y 頂住，下一幀再睇 anchor 返咗未 */
          window.scrollTo({ top: saved.y, left: window.scrollX, behavior: "instant" });
        }
        /* 到咗位都唔准即刻收：卡圖 lazy load 落位會再推一次，所以追足 budget，
           除非用戶自己出手。 */
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
      const saved = readSaved(entryKey());
      if (!saved) return;
      restore(saved);
    };

    const previousRestoration = history.scrollRestoration;
    try { history.scrollRestoration = "manual"; } catch { /* 舊瀏覽器唔支援就照行落去 */ }

    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("popstate", onPopState);
    window.addEventListener("pagehide", save);
    document.addEventListener("visibilitychange", onVisibility);
    document.addEventListener("click", onClickCapture, true);
    window.addEventListener("wheel", onUserScrollIntent, { passive: true });
    window.addEventListener("touchstart", onUserScrollIntent, { passive: true });
    window.addEventListener("keydown", onUserScrollIntent);

    /* 跨 document 嘅 back／forward／reload：scrollRestoration 設咗 manual，原生唔會做，
       所以呢度自己補返（bfcache 命中嗰種唔會行到呢度，瀏覽器已經還原晒）。 */
    const navigation = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    if (navigation?.type === "back_forward" || navigation?.type === "reload") {
      const saved = readSaved(entryKey());
      if (saved) restore(saved);
    }

    return () => {
      stopRestore();
      if (saveTimer) clearTimeout(saveTimer);
      try { history.scrollRestoration = previousRestoration; } catch { /* ignore */ }
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("popstate", onPopState);
      window.removeEventListener("pagehide", save);
      document.removeEventListener("visibilitychange", onVisibility);
      document.removeEventListener("click", onClickCapture, true);
      window.removeEventListener("wheel", onUserScrollIntent);
      window.removeEventListener("touchstart", onUserScrollIntent);
      window.removeEventListener("keydown", onUserScrollIntent);
    };
  }, []);
  return null;
}
