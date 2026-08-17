"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { CATALOG_LIST_CAP } from "./catalog-search";
import { normaliseCurrency, normaliseLocale, normaliseTheme } from "./format";
import { cardLanguages } from "./i18n";
import { DEFAULT_RANKING_PAGE_SIZE } from "./pagination";
import { defaultMarketWindow, marketWindows, type Currency, type Locale, type MarketWindow, type PrintLanguage, type Theme } from "./types";

export function normaliseMarketWindow(value: string | null | undefined): MarketWindow {
  return marketWindows.includes(value as MarketWindow) ? value as MarketWindow : defaultMarketWindow;
}

/* 語言篩選只過濾顯示，唔改排名。`all` 係預設、唔上 URL。 */
export type PrintLangFilter = PrintLanguage | "all";

/*
 * 搜尋榜「顯示更多」嘅行數（`show=<int>`）。
 *
 * 以前係 `rankings.tsx` 嘅 `useState`：撳兩下展開到 240 行、揀第 190 行入卡頁、
 * 撳返上一頁 —— state 冇咗，榜返返 80 行，`scroll-restoration.tsx` 嗰個 anchor
 * （`a[href^="/card/"]`）根本唔喺 DOM 入面，只可以跌返絕對 Y。URL 係唯一真相，
 * 所以展開量都要上 URL：back-nav 同分享連結都行返同一條路。
 *
 * 規矩：
 *  - 只喺 > 預設（`CATALOG_LIST_CAP`）先出現喺 URL，等預設 URL 保持乾淨；
 *  - 一律收埋做 CAP 嘅倍數（`?show=123` → 160），唔准出半版；
 *  - `MAX_SHOW` 係硬頂：`?show=99999999` 一次過 render 幾百萬行 = 主線程死。
 *    真正上限仲要俾 caller 按命中數再 clamp 一次（見 `rankings.tsx`）。
 */
const MAX_SHOW = CATALOG_LIST_CAP * 100;

function clampShow(raw: number): number {
  if (!Number.isFinite(raw) || raw <= CATALOG_LIST_CAP) return CATALOG_LIST_CAP;
  return Math.ceil(Math.min(raw, MAX_SHOW) / CATALOG_LIST_CAP) * CATALOG_LIST_CAP;
}

/* 嚴格淨數字：`Number.parseInt("240abc")` 會收貨 240，即係打錯都靜靜當啱。 */
export function normaliseShow(value: string | null | undefined): number {
  const text = (value ?? "").trim();
  if (!/^\d+$/.test(text)) return CATALOG_LIST_CAP;
  return clampShow(Number(text));
}

export function normalisePrintLang(value: string | null | undefined): PrintLangFilter {
  return cardLanguages.includes(value as PrintLanguage) ? value as PrintLanguage : "all";
}

/*
 * 主題係一件 client-only 事實（localStorage + OS 偏好），但 header 係 server render 嘅。
 *
 * 原本個寫法喺 `useState` initializer 度讀 localStorage、喺 render body 度讀
 * `matchMedia`。呢兩句喺 **hydration render** 一樣會行，所以 server 出
 * `<Moon aria-label="Dark mode">`，而一個 dark 偏好嘅瀏覽器第一次 render 就出
 * `<Sun aria-label="Light mode">` —— 唔同 element、唔同 aria-label，係 hydration
 * mismatch，React 會掉咗成個 Header subtree 重畫。
 *
 * `useSyncExternalStore` 就係為呢件事而設：hydration render 一定行
 * `getServerSnapshot`（固定 "light"，同 server 出嗰份 HTML 一致），hydrate 完先
 * sync 去真實值。同 heatmap.tsx 個 `isMobileTiles` 一模一樣嘅寫法。
 *
 * `storage` event 唔會喺自己嗰個 tab 度 fire，所以 same-tab 嘅寫入要自己叫返
 * subscriber —— 冇呢個 set，撳完 toggle 個 icon 唔會郁。
 */
const THEME_KEY = "cardz-theme";
const darkQuery = "(prefers-color-scheme: dark)";
const themeListeners = new Set<() => void>();

function subscribeTheme(onChange: () => void): () => void {
  const media = window.matchMedia(darkQuery);
  media.addEventListener("change", onChange);
  window.addEventListener("storage", onChange);
  themeListeners.add(onChange);
  return () => {
    media.removeEventListener("change", onChange);
    window.removeEventListener("storage", onChange);
    themeListeners.delete(onChange);
  };
}

function readTheme(): Theme {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === "dark" || stored === "light") return stored;
  } catch { /* ignore */ }
  return window.matchMedia(darkQuery).matches ? "dark" : "light";
}

function serverTheme(): Theme {
  return "light";
}

/*
 * 主題過渡只喺「用戶撳 toggle」嗰 260ms 內開：`<html class="theme-transitions">`
 * 加上去、計時拆走。以前係 pre-paint script 永久加住，結果任何 hover / 頁面切換
 * 都拖住 240ms 過渡。連撳兩下就重置計時，唔會中途拆走。
 */
const THEME_TRANSITION_MS = 260;
let themeTransitionTimer: ReturnType<typeof setTimeout> | null = null;

function armThemeTransitions(): void {
  const root = document.documentElement;
  root.classList.add("theme-transitions");
  if (themeTransitionTimer) clearTimeout(themeTransitionTimer);
  themeTransitionTimer = setTimeout(() => {
    root.classList.remove("theme-transitions");
    themeTransitionTimer = null;
  }, THEME_TRANSITION_MS);
}

/*
 * GEO 預設（owner 2026-08-16）：用戶明確揀過語言／貨幣就寫 cookie，middleware 讀
 * `cardz-lang` / `cardz-currency` + CF-IPCountry 決定首訪預設。返去 en / USD 都要寫，
 * 唔係 middleware 會以為用戶未揀過、再用地區推一次。
 */
function writePrefCookie(name: string, value: string): void {
  try {
    document.cookie = `${name}=${encodeURIComponent(value)}; Max-Age=31536000; Path=/; SameSite=Lax`;
  } catch { /* ignore */ }
}

/*
 * `router.replace` 唔係即時：由 call 到 `window.location.search` 真係變，實測 211/253/266ms
 * （一次 RSC round trip）。搜尋框 debounce 係 220ms，即係「打字 → 220ms 後寫 q」同
 * 「用戶喺 300ms 內撳排序」兩次寫入之間 location 根本仲未郁 —— 所以「call 嗰刻先讀 live
 * `window.location.search`」修唔到呢個 race（location 本身滯後過 debounce），兩次寫入照樣
 * 互相洗走（最終剩 `?q=char` 冇咗 sort，或者反方向剩 `?sort=price` 冇咗 q）。
 *
 * 修法：自己揸多一份「已寫出、未 commit」嘅權威。每次寫出去記低條鏈
 * `[寫之前嗰個 URL, 我寫過嘅每個值…]`：
 * - 下次 `update()` 見到 live URL 仲喺鏈入面 = 我哋嘅寫入未落地，就由鏈尾接落去 merge；
 * - live URL 唔喺鏈入面 = 有人另外改過 URL（back/forward、`<Link>`、外部 push），棄鏈跟 live。
 * 條鏈一定要 module-level：heatmap / rankings / header 各自 call 一次 `useMarketSettings()`，
 * per-hook 嘅 ref 擋唔到跨組件嗰半邊 race。SSR 唔准掂佢（只喺有 `window` 嗰條路行）。
 */
type PendingWrites = { path: string; chain: string[] };
let pendingWrites: PendingWrites | null = null;
/* 一條鏈最多記 12 個未 commit 值（實際最多兩三個）；爆咗就跌返「讀 live URL」嘅舊行為。 */
const MAX_PENDING_WRITES = 12;

function liveSearch(): string {
  const search = window.location.search;
  return search.startsWith("?") ? search.slice(1) : search;
}

export function useMarketSettings() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const locale = normaliseLocale(params.get("lang"));
  const currency = normaliseCurrency(params.get("currency"));
  const period = normaliseMarketWindow(params.get("period"));
  const printLang = normalisePrintLang(params.get("printLang"));
  const query = params.get("q") ?? "";
  const show = normaliseShow(params.get("show"));
  const sort = params.get("sort") ?? "rank";
  const dir = params.get("dir") === "asc" ? "asc" as const : "desc" as const;
  const urlTheme = params.get("theme");
  const storedTheme = useSyncExternalStore(subscribeTheme, readTheme, serverTheme);
  const theme: Theme = urlTheme ? normaliseTheme(urlTheme) : storedTheme;

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((nextTheme: Theme) => {
    try { window.localStorage.setItem(THEME_KEY, nextTheme); } catch { /* ignore */ }
    armThemeTransitions();
    for (const notify of themeListeners) notify();
  }, []);

  /* SSR／未 hydrate 冇 `window`，`update()` 就用 render 嗰份 params 兜底；客戶端讀 live URL。 */
  const paramsFallback = useRef(params);
  useEffect(() => { paramsFallback.current = params; }, [params]);

  /* 寫出去嘅嘢一 commit（`params` 追上鏈尾）就收返條鏈，唔好積住；pathname 一變（真
     navigate）亦即刻棄鏈 —— 新 route 嘅 query 同舊鏈冇關係。 */
  useEffect(() => {
    const pending = pendingWrites;
    if (!pending) return;
    if (pending.path !== pathname || params.toString() === pending.chain[pending.chain.length - 1]) {
      pendingWrites = null;
    }
  }, [params, pathname]);

  /*
   * `update()` 唔准 close over render 時嘅 `params`（stale-closure race），要喺 **call 嗰刻**
   * 讀返「而家真正生效嘅 query」再 derive 每個 field。呢個「而家」= 未 commit 嘅寫入優先，
   * 冇先至係 live `window.location.search`（點解要多呢層，見上面 `pendingWrites`）。
   */
  const update = useCallback((next: {
    locale?: Locale;
    currency?: Currency;
    period?: MarketWindow;
    theme?: Theme;
    printLang?: PrintLangFilter;
    query?: string;
    sort?: string;
    dir?: "asc" | "desc";
    page?: number;
    size?: number;
    show?: number;
  }) => {
    if (next.theme) setTheme(next.theme);
    const live = typeof window === "undefined" ? null : liveSearch();
    /* live URL 仲喺條鏈入面 = 我哋自己嗰啲 replace 未 commit，由鏈尾接落去，唔好由舊 URL 重新砌 */
    const pending = live !== null && pendingWrites?.path === pathname && pendingWrites.chain.includes(live)
      ? pendingWrites
      : null;
    const liveParams = new URLSearchParams(
      live === null
        ? paramsFallback.current.toString()
        : pending ? pending.chain[pending.chain.length - 1] : live,
    );
    const liveUrlTheme = liveParams.get("theme");
    /* 淨係轉 theme（localStorage 事實）就唔准 router.replace —— 以前每撳一下 toggle
       都行一次 RSC navigation。例外：URL 帶住 ?theme= 覆蓋緊，就要落埋個 param 先轉得到。 */
    const onlyTheme = Object.entries(next).every(([key, value]) => key === "theme" || value === undefined);
    if (onlyTheme && !(next.theme && liveUrlTheme)) return;
    if (next.locale) writePrefCookie("cardz-lang", next.locale);
    if (next.currency) writePrefCookie("cardz-currency", next.currency);
    const nextParams = new URLSearchParams(liveParams.toString());
    if (next.theme) nextParams.delete("theme");
    const nextLocale = next.locale ?? normaliseLocale(liveParams.get("lang"));
    const nextCurrency = next.currency ?? normaliseCurrency(liveParams.get("currency"));
    const nextPeriod = next.period ?? normaliseMarketWindow(liveParams.get("period"));
    const nextPrintLang = next.printLang ?? normalisePrintLang(liveParams.get("printLang"));
    const nextQuery = next.query !== undefined ? next.query : liveParams.get("q") ?? "";
    const nextSort = next.sort !== undefined ? next.sort : liveParams.get("sort") ?? "rank";
    const nextDir = next.dir !== undefined
      ? next.dir
      : liveParams.get("dir") === "asc" ? "asc" as const : "desc" as const;
    if (nextLocale === "en") nextParams.delete("lang");
    else nextParams.set("lang", nextLocale);
    if (nextCurrency === "USD") nextParams.delete("currency");
    else nextParams.set("currency", nextCurrency);
    if (nextPeriod === defaultMarketWindow) nextParams.delete("period");
    else nextParams.set("period", nextPeriod);
    if (nextPrintLang === "all") nextParams.delete("printLang");
    else nextParams.set("printLang", nextPrintLang);
    if (!nextQuery.trim()) nextParams.delete("q");
    else nextParams.set("q", nextQuery);
    if (next.page !== undefined) {
      if (next.page <= 1) nextParams.delete("page");
      else nextParams.set("page", String(next.page));
    }
    if (next.size !== undefined) {
      if (next.size === DEFAULT_RANKING_PAGE_SIZE) nextParams.delete("size");
      else nextParams.set("size", String(next.size));
    }
    if (next.show !== undefined) {
      const nextShow = clampShow(next.show);
      if (nextShow <= CATALOG_LIST_CAP) nextParams.delete("show");
      else nextParams.set("show", String(nextShow));
    }
    /* q 一變，舊嗰個展開量就係講緊另一批命中，一定要跌返預設；同一個 q 再寫一次
       （搜尋框每 220ms debounce 會寫同一個值）唔算變。呢句要行喺上面 set 之後，
       唔係「清 q 但留住 show=240」。 */
    if (next.query !== undefined && nextQuery.trim() !== (liveParams.get("q") ?? "").trim()) {
      nextParams.delete("show");
    }
    if (!nextSort || nextSort === "rank") {
      nextParams.delete("sort");
      nextParams.delete("dir");
    } else {
      nextParams.set("sort", nextSort);
      if (nextDir === "desc") nextParams.delete("dir");
      else nextParams.set("dir", nextDir);
    }
    const suffix = nextParams.toString();
    /* 先記低「我寫咗咩」再 replace：下一個手勢（可能係另一個組件）要即刻睇得到呢次寫入，
       等唔到 router commit。 */
    if (live !== null) {
      const chain = pending ? pending.chain : [live];
      pendingWrites = { path: pathname, chain: [...chain, suffix].slice(-MAX_PENDING_WRITES) };
    }
    router.replace(suffix ? `${pathname}?${suffix}` : pathname, { scroll: false });
  }, [pathname, router, setTheme]);

  const href = useCallback((path: string) => {
    const query = new URLSearchParams();
    if (locale !== "en") query.set("lang", locale);
    if (currency !== "USD") query.set("currency", currency);
    if (period !== defaultMarketWindow) query.set("period", period);
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  }, [currency, locale, period]);

  return { locale, currency, period, printLang, query, show, sort, dir, theme, update, href };
}
