"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { normaliseCurrency, normaliseLocale, normaliseTheme } from "./format";
import { marketWindows, type Currency, type Locale, type MarketWindow, type Theme } from "./types";

export function normaliseMarketWindow(value: string | null | undefined): MarketWindow {
  return marketWindows.includes(value as MarketWindow) ? value as MarketWindow : "30d";
}

function readStoredTheme(): Theme | null {
  try {
    const stored = window.localStorage.getItem("cardz-theme");
    if (stored === "dark" || stored === "light") return stored;
  } catch { /* ignore */ }
  return null;
}

export function useMarketSettings() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const locale = normaliseLocale(params.get("lang"));
  const currency = normaliseCurrency(params.get("currency"));
  const period = normaliseMarketWindow(params.get("period"));
  const urlTheme = params.get("theme");
  const [overrideTheme, setOverrideTheme] = useState<Theme | null>(() =>
    typeof window === "undefined" ? null : readStoredTheme(),
  );
  const theme: Theme = urlTheme ? normaliseTheme(urlTheme)
    : (overrideTheme
      ?? (typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const setTheme = useCallback((nextTheme: Theme) => {
    try { window.localStorage.setItem("cardz-theme", nextTheme); } catch { /* ignore */ }
    setOverrideTheme(nextTheme);
  }, [setOverrideTheme]);

  const update = useCallback((next: { locale?: Locale; currency?: Currency; period?: MarketWindow; theme?: Theme }) => {
    if (next.theme) setTheme(next.theme);
    const query = new URLSearchParams(params.toString());
    const nextLocale = next.locale ?? locale;
    const nextCurrency = next.currency ?? currency;
    const nextPeriod = next.period ?? period;
    if (nextLocale === "en") query.delete("lang");
    else query.set("lang", nextLocale);
    if (nextCurrency === "USD") query.delete("currency");
    else query.set("currency", nextCurrency);
    if (nextPeriod === "30d") query.delete("period");
    else query.set("period", nextPeriod);
    const suffix = query.toString();
    router.replace(suffix ? `${pathname}?${suffix}` : pathname, { scroll: false });
  }, [currency, locale, params, pathname, period, router, setTheme]);

  const href = useCallback((path: string) => {
    const query = new URLSearchParams();
    if (locale !== "en") query.set("lang", locale);
    if (currency !== "USD") query.set("currency", currency);
    if (period !== "30d") query.set("period", period);
    const suffix = query.toString();
    return suffix ? `${path}?${suffix}` : path;
  }, [currency, locale, period]);

  return { locale, currency, period, theme, update, href };
}
