import { currencies, locales, type Currency, type Locale } from "./types";

/*
 * 首次入站嘅語言／貨幣預設（owner 2026-08-16）：cookie 有就跟 cookie，冇就跟 IP 國家。
 * 純函數，middleware 用；冇 side effect，方便 node script 直接測。
 * 淨係 middleware 用嘅 pure 邏輯，唔好入 use-market-settings —— 嗰邊只認 URL param。
 */
export const DEFAULT_LOCALE: Locale = "en";
export const DEFAULT_CURRENCY: Currency = "USD";

export const LANG_COOKIE = "cardz-lang";
export const CURRENCY_COOKIE = "cardz-currency";

/* 國家 → 預設。冇喺表入面（連 US / 未知）一律 en / USD。 */
const COUNTRY_DEFAULTS: Record<string, { lang: Locale; currency: Currency }> = {
  JP: { lang: "ja", currency: "JPY" },
  KR: { lang: "ko", currency: "KRW" },
  TW: { lang: "zh-TW", currency: "TWD" },
  HK: { lang: "zh-TW", currency: "HKD" },
  MO: { lang: "zh-TW", currency: "HKD" },
  CN: { lang: "zh-CN", currency: "CNY" },
  GB: { lang: "en", currency: "GBP" },
};

export interface GeoDefaults {
  lang: Locale;
  currency: Currency;
}

function asLocale(value: string | null | undefined): Locale | null {
  return locales.includes(value as Locale) ? (value as Locale) : null;
}

function asCurrency(value: string | null | undefined): Currency | null {
  return currencies.includes(value as Currency) ? (value as Currency) : null;
}

/* 逐項獨立：lang cookie 有效就用 cookie lang，否則 geo lang；currency 同理。 */
export function resolveGeoDefaults(
  country: string | null | undefined,
  cookies: { lang?: string | null; currency?: string | null } = {},
): GeoDefaults {
  const code = (country ?? "").trim().toUpperCase();
  const geo = (/^[A-Z]{2}$/.test(code) && COUNTRY_DEFAULTS[code]) || { lang: DEFAULT_LOCALE, currency: DEFAULT_CURRENCY };
  return {
    lang: asLocale(cookies.lang) ?? geo.lang,
    currency: asCurrency(cookies.currency) ?? geo.currency,
  };
}

/*
 * 畀一條 URL 嘅 search，計出應唔應該 redirect：
 *  - lang / currency 兩個都喺 URL → null（唔郁）；
 *  - 只填缺嗰個，而且只喺 resolve 出嚟唔係預設（en / USD）先填；
 *  - 冇嘢要填 → null。
 * 回傳新嘅 URLSearchParams（保留其他 param）；redirect 後 URL 一定有 param，所以唔會迴圈。
 */
export function geoRedirectSearch(search: URLSearchParams, defaults: GeoDefaults): URLSearchParams | null {
  const hasLang = search.has("lang");
  const hasCurrency = search.has("currency");
  if (hasLang && hasCurrency) return null;
  const next = new URLSearchParams(search);
  let changed = false;
  if (!hasLang && defaults.lang !== DEFAULT_LOCALE) {
    next.set("lang", defaults.lang);
    changed = true;
  }
  if (!hasCurrency && defaults.currency !== DEFAULT_CURRENCY) {
    next.set("currency", defaults.currency);
    changed = true;
  }
  return changed ? next : null;
}

/* IP 國家 header 優先序：Cloudflare → Vercel → CloudFront → 通用。 */
export const COUNTRY_HEADERS = ["cf-ipcountry", "x-vercel-ip-country", "cloudfront-viewer-country", "x-country"] as const;

export const BOT_UA_PATTERN = /bot|crawl|spider|slurp|facebookexternalhit|preview/i;
