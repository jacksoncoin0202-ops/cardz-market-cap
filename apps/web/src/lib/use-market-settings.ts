"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { normaliseCurrency, normaliseLocale, normaliseTheme } from "./format";
import { cardLanguages } from "./i18n";
import { defaultMarketWindow, marketWindows, type Currency, type Locale, type MarketWindow, type PrintLanguage, type Theme } from "./types";

export function normaliseMarketWindow(value: string | null | undefined): MarketWindow {
  return marketWindows.includes(value as MarketWindow) ? value as MarketWindow : defaultMarketWindow;
}

/* 語言篩選只過濾顯示，唔改排名。`all` 係預設、唔上 URL。 */
export type PrintLangFilter = PrintLanguage | "all";

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

export function useMarketSettings() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const locale = normaliseLocale(params.get("lang"));
  const currency = normaliseCurrency(params.get("currency"));
  const period = normaliseMarketWindow(params.get("period"));
  const printLang = normalisePrintLang(params.get("printLang"));
  const query = params.get("q") ?? "";
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
    for (const notify of themeListeners) notify();
  }, []);

  const update = useCallback((next: {
    locale?: Locale;
    currency?: Currency;
    period?: MarketWindow;
    theme?: Theme;
    printLang?: PrintLangFilter;
    query?: string;
    sort?: string;
    dir?: "asc" | "desc";
  }) => {
    if (next.theme) setTheme(next.theme);
    const nextParams = new URLSearchParams(params.toString());
    const nextLocale = next.locale ?? locale;
    const nextCurrency = next.currency ?? currency;
    const nextPeriod = next.period ?? period;
    const nextPrintLang = next.printLang ?? printLang;
    const nextQuery = next.query !== undefined ? next.query : query;
    const nextSort = next.sort !== undefined ? next.sort : sort;
    const nextDir = next.dir !== undefined ? next.dir : dir;
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
    if (!nextSort || nextSort === "rank") {
      nextParams.delete("sort");
      nextParams.delete("dir");
    } else {
      nextParams.set("sort", nextSort);
      if (nextDir === "desc") nextParams.delete("dir");
      else nextParams.set("dir", nextDir);
    }
    const suffix = nextParams.toString();
    router.replace(suffix ? `${pathname}?${suffix}` : pathname, { scroll: false });
  }, [currency, dir, locale, params, pathname, period, printLang, query, router, setTheme, sort]);

  const href = useCallback((path: string) => {
    const query = new URLSearchParams();
    if (locale !== "en") query.set("lang", locale);
    if (currency !== "USD") query.set("currency", currency);
    if (period !== defaultMarketWindow) query.set("period", period);
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  }, [currency, locale, period]);

  return { locale, currency, period, printLang, query, sort, dir, theme, update, href };
}
