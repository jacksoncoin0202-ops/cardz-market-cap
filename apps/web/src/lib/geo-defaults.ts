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

/*
 * 國家 → 預設。冇喺表入面（連 US / 未知）一律 en / USD。
 * 站得五個語系，所以除咗東亞六個之外全部 `lang: "en"` —— 只係幫佢揀返本地貨幣，
 * 唔會扮有本地語言版。貨幣一定要喺 `currencies`（lib/types.ts）入面。
 */
const COUNTRY_DEFAULTS: Record<string, { lang: Locale; currency: Currency }> = {
  JP: { lang: "ja", currency: "JPY" },
  KR: { lang: "ko", currency: "KRW" },
  TW: { lang: "zh-TW", currency: "TWD" },
  HK: { lang: "zh-TW", currency: "HKD" },
  MO: { lang: "zh-TW", currency: "HKD" },
  CN: { lang: "zh-CN", currency: "CNY" },
  GB: { lang: "en", currency: "GBP" },
  // 亞太
  SG: { lang: "en", currency: "SGD" },
  MY: { lang: "en", currency: "MYR" },
  TH: { lang: "en", currency: "THB" },
  PH: { lang: "en", currency: "PHP" },
  ID: { lang: "en", currency: "IDR" },
  VN: { lang: "en", currency: "VND" },
  IN: { lang: "en", currency: "INR" },
  AU: { lang: "en", currency: "AUD" },
  NZ: { lang: "en", currency: "NZD" },
  // 美洲
  CA: { lang: "en", currency: "CAD" },
  MX: { lang: "en", currency: "MXN" },
  BR: { lang: "en", currency: "BRL" },
  // 歐洲（非歐元）
  CH: { lang: "en", currency: "CHF" },
  SE: { lang: "en", currency: "SEK" },
  NO: { lang: "en", currency: "NOK" },
  DK: { lang: "en", currency: "DKK" },
  PL: { lang: "en", currency: "PLN" },
  CZ: { lang: "en", currency: "CZK" },
  // 歐元區
  DE: { lang: "en", currency: "EUR" },
  FR: { lang: "en", currency: "EUR" },
  IT: { lang: "en", currency: "EUR" },
  ES: { lang: "en", currency: "EUR" },
  NL: { lang: "en", currency: "EUR" },
  BE: { lang: "en", currency: "EUR" },
  AT: { lang: "en", currency: "EUR" },
  PT: { lang: "en", currency: "EUR" },
  IE: { lang: "en", currency: "EUR" },
  FI: { lang: "en", currency: "EUR" },
  GR: { lang: "en", currency: "EUR" },
  // 中東・非洲
  AE: { lang: "en", currency: "AED" },
  SA: { lang: "en", currency: "SAR" },
  IL: { lang: "en", currency: "ILS" },
  TR: { lang: "en", currency: "TRY" },
  ZA: { lang: "en", currency: "ZAR" },
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

/*
 * 邊個 UA 唔好 geo-redirect（owner 2026-08-16）。
 *
 * 舊版係 /bot|crawl|spider|slurp|facebookexternalhit|preview/i —— 靠模糊字眼撞。撞唔中嘅
 * 代價唔細：爬蟲攞 `/` 會食到 302 去 `/?lang=ja`，索引到嘅就變咗日文版，canonical 亂晒。
 * 而好多要緊嘅 UA 根本冇「bot」呢三個字母：`ChatGPT-User`、`Perplexity-User`、
 * `Claude-User`、`anthropic-ai`、`meta-externalfetcher`、`python-requests`、`curl`…
 *
 * 所以而家逐個寫死（前半），generic 字眼留喺後半做網。`preview` 保留：舊行為，
 * 各種 link-preview fetcher 靠佢，剷咗冇著數。
 */
export const BOT_UA_PATTERN = new RegExp(
  [
    // 搜尋引擎
    "googlebot", "bingbot", "duckduckbot", "applebot", "yandex", "baiduspider", "yeti", "slurp",
    // 社交／通訊 link preview
    "facebookexternalhit", "twitterbot", "linkedinbot", "discordbot", "telegrambot", "whatsapp", "slackbot",
    // AI 搜尋 / 用戶觸發抓取
    "oai-searchbot", "chatgpt-user", "perplexitybot", "perplexity-user",
    "claude-user", "claude-searchbot", "duckassistbot", "mistralai-user", "youbot",
    // AI 訓練 / dataset
    "gptbot", "claudebot", "anthropic-ai", "google-extended", "ccbot", "amazonbot",
    "bytespider", "meta-externalagent", "meta-externalfetcher", "cohere-ai", "petalbot",
    // 自動化 / 監測 / HTTP client
    "headlesschrome", "lighthouse", "curl", "wget", "python-requests", "node-fetch", "axios", "go-http-client",
    // generic 網
    "bot", "crawl", "spider", "scrap", "fetch", "preview",
  ].join("|"),
  "i",
);
