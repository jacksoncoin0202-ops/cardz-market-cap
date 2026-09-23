import { currencies, locales, themes, type Currency, type Locale, type MarketMetric, type Theme, type TrackedSalesMetric } from "./types";
import { copy } from "./i18n";

const intlLocale: Record<Locale, string> = {
  en: "en-US",
  "zh-TW": "zh-Hant-TW",
  "zh-CN": "zh-Hans-CN",
  ja: "ja-JP",
  ko: "ko-KR",
};

/* Intl formatter 快取（module-level）：new Intl.NumberFormat 每次要 load locale data，
   一格 tile / 一個 ticker frame 都 new 一個係浪費；key = locale|options JSON，
   同一組參數永遠攞返同一個 formatter。輸出同直接 new 一模一樣。 */
const numberFormatCache = new Map<string, Intl.NumberFormat>();
const dateFormatCache = new Map<string, Intl.DateTimeFormat>();
function numberFormat(locale: Locale, options: Intl.NumberFormatOptions): Intl.NumberFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let formatter = numberFormatCache.get(key);
  if (!formatter) {
    formatter = new Intl.NumberFormat(intlLocale[locale], options);
    numberFormatCache.set(key, formatter);
  }
  return formatter;
}
function dateFormat(locale: Locale, options: Intl.DateTimeFormatOptions): Intl.DateTimeFormat {
  const key = `${locale}|${JSON.stringify(options)}`;
  let formatter = dateFormatCache.get(key);
  if (!formatter) {
    formatter = new Intl.DateTimeFormat(intlLocale[locale], options);
    dateFormatCache.set(key, formatter);
  }
  return formatter;
}

export function normaliseLocale(value: string | null | undefined): Locale {
  return locales.includes(value as Locale) ? (value as Locale) : "en";
}

export function normaliseCurrency(value: string | null | undefined): Currency {
  return currencies.includes(value as Currency) ? (value as Currency) : "USD";
}

export function normaliseTheme(value: string | null | undefined): Theme {
  return themes.includes(value as Theme) ? (value as Theme) : "light";
}

/*
 * 顯示用升跌幅。熱力圖 label 1 位小數、榜／preview 2 位。
 * 印出來係 0% 就當 0：無正負號、無 ±$、無紅綠。Number(toFixed) 會留 −0，要抹走。
 */
export function displayedChangePct(value: number, decimals: number): number {
  const q = Number(value.toFixed(decimals));
  return q === 0 ? 0 : q;
}

/*
 * 中日韓 compact 用萬／億（万／亿、만／억）做單位，所以單位前面個數最大去到 9999。
 * Intl compact 預設 useGrouping "min2"（4 位數唔分組），加埋 2 位小數就出「US$3483.29萬」：
 * 6 位有效數字、冇千位分隔，難讀兼假精度。中日韓 compact 另外加：
 *   - useGrouping "always"：4 位數都分組 → 3,483萬
 *   - 最多 4 位有效數字，同 2 位小數比揀精度低嗰個（roundingPriority "lessPrecision"）
 *     → 3,483萬、243.3萬、38.04萬、1.23萬、11.31億；未夠萬嘅 2,134.87 → 2,135
 * en 唔郁：K/M/B 前面最多 3 位，$34.83M 本身已經夠短。非 compact（卡價）唔郁。
 * scripts/test-fe-money-format.mjs 鎖死上面啲例子；test-fe-ticker-unit.mjs 直接用呢個 formatMoney。
 */
const CJK_COMPACT: Intl.NumberFormatOptions = {
  useGrouping: "always",
  maximumSignificantDigits: 4,
  roundingPriority: "lessPrecision",
};

export function formatMoney(
  valueUsd: number | null,
  currency: Currency,
  rates: Record<Currency, number>,
  locale: Locale,
  compact = false,
): string {
  if (valueUsd === null || !Number.isFinite(valueUsd)) return copy[locale].status.unavailable;
  const rate = rates[currency];
  // 匯率 0 或負數只可能來自壞 FX feed —— fail-closed 出「暫無資料」，唔准出假零價。
  if (!Number.isFinite(rate) || rate <= 0) return copy[locale].status.unavailable;
  const converted = valueUsd * rate;
  return numberFormat(locale, {
    style: "currency",
    currency,
    maximumFractionDigits: compact ? 2 : converted < 100 ? 2 : 0,
    notation: compact ? "compact" : "standard",
    ...(compact && locale !== "en" ? CJK_COMPACT : {}),
  }).format(converted);
}

export function formatInteger(value: number | null, locale: Locale): string {
  if (value === null || !Number.isFinite(value)) return copy[locale].status.unavailable;
  return numberFormat(locale, { maximumFractionDigits: 0 }).format(value);
}

export function formatMetricMoney(
  metric: MarketMetric<number>,
  currency: Currency,
  rates: Record<Currency, number>,
  locale: Locale,
  compact = false,
): string {
  if (metric.value === null || metric.status === "accumulating" || metric.status === "unavailable") {
    return copy[locale].status[metric.status === "ready" ? "unavailable" : metric.status];
  }
  const value = formatMoney(metric.value, currency, rates, locale, compact);
  return value;
}

// Absolute money delta implied by a percentage change: baseline = value / (1 + pct/100).
export function formatDeltaMoney(
  metric: MarketMetric<number>,
  changePct: MarketMetric<number>,
  currency: Currency,
  rates: Record<Currency, number>,
  locale: Locale,
): string | null {
  if (
    metric.value === null || (metric.status !== "ready" && metric.status !== "stale") ||
    changePct.value === null || (changePct.status !== "ready" && changePct.status !== "stale") ||
    changePct.value <= -100
  ) return null;
  if (displayedChangePct(changePct.value, 2) === 0) return null;
  const baseline = metric.value / (1 + changePct.value / 100);
  const delta = metric.value - baseline;
  if (Math.abs(delta) < 0.005) return null;
  const formatted = formatMoney(Math.abs(delta), currency, rates, locale, true);
  return `${delta >= 0 ? "+" : "−"}${formatted}`;
}

export function formatMetricInteger(metric: MarketMetric<number>, locale: Locale): string {
  if (metric.value === null || metric.status === "accumulating" || metric.status === "unavailable") {
    return copy[locale].status[metric.status === "ready" ? "unavailable" : metric.status];
  }
  const value = formatInteger(metric.value, locale);
  return value;
}

export function formatPercent(metric: MarketMetric<number>, locale: Locale): string {
  if (metric.value === null || metric.status === "accumulating" || metric.status === "unavailable") {
    return copy[locale].status[metric.status === "ready" ? "unavailable" : metric.status];
  }
  if (!Number.isFinite(metric.value)) return copy[locale].status.unavailable;
  const shown = displayedChangePct(metric.value, 2);
  const sign = shown > 0 ? "+" : "";
  return `${sign}${shown.toFixed(2)}%`;
}

export function formatTrackedSales(
  sales: TrackedSalesMetric,
  currency: Currency,
  rates: Record<Currency, number>,
  locale: Locale,
): string {
  if (
    sales.coverage === "unavailable" ||
    sales.valueUsd.value === null ||
    sales.valueUsd.value <= 0 ||
    sales.count.value === null ||
    sales.count.value <= 0 ||
    sales.valueUsd.status === "unavailable"
  ) return copy[locale].labels.noSales;
  const value = formatMoney(sales.valueUsd.value, currency, rates, locale, true);
  return value;
}

/*
 * 觀察日期（價格／POP／BOX 嘅 asOf）。鎖死 UTC 兼只出日期，兩個原因：
 *  1. 價格來源本身係 DATE（market_price_observation.observed_date），冇時分秒。
 *     連時間一齊出會補返個 `00:00Z`，再按 runtime 時區譯，出「Mar 27, 2026,
 *     12:00 AM」（server UTC）／「8:00 AM」（香港）—— 造個唔存在嘅精度出嚟。
 *  2. 讀呢個值嘅 card-detail / heatmap / box 頁都係 "use client"，即係 SSR 出一次、
 *     hydrate 再出一次。Intl 唔指定 timeZone 就跟 runtime 時區，AWS 係 UTC 而
 *     用戶多數 UTC+8/+9，兩邊文字唔同 = hydration mismatch。
 * 以前另有個 formatDate（日期＋時間、冇 timeZone），/box 同 /box/[id] 用緊：2026-09-23
 * live 量到 JST 瀏覽器每次都 React #418（SSR「4:20 AM」對 hydrate「1:20 PM」），已刪。
 * scripts/test-fe-date-timezone.mjs 喺幾個時區跑同一批日期，字要一模一樣。
 */
export function formatObservationDate(value: string | null, locale: Locale): string {
  if (!value) return copy[locale].status.unavailable;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return copy[locale].status.unavailable;
  return dateFormat(locale, {
    dateStyle: "medium",
    timeZone: "UTC",
  }).format(date);
}

/*
 * 同一批觀察日期，喺價格圖個軸上面淨係要「月 日」。行返上面同一組規矩（pin UTC +
 * 經 intlLocale），因為佢讀緊同一批 DATE 值。
 *
 * 原本 history-chart.tsx 自己 new 一個 `Intl.DateTimeFormat(locale, …)`，兩樣都做漏：
 *  1. 冇 timeZone —— AWS（UTC）SSR 出「Jul 10」，America/* 嘅瀏覽器 hydrate 出
 *     「Jul 9」。軸標籤差一日，兼多兩個 hydration mismatch 節點。
 *  2. 直接傳 app 嘅 Locale（"en" / "zh-TW"）落 Intl，冇轉做 BCP-47（"en-US" /
 *     "zh-Hant"）。
 * 軸標籤揾唔到日期就出空字串（唔係 status.unavailable），因為個位淨係一個刻度。
 */
export function formatObservationDayMonth(value: string, locale: Locale): string {
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return "";
  return dateFormat(locale, {
    month: "short",
    day: "numeric",
    timeZone: "UTC",
  }).format(date);
}

export function metricTone(metric: MarketMetric<number>): "positive" | "negative" | "neutral" {
  if (
    (metric.status !== "ready" && metric.status !== "stale") ||
    metric.value === null ||
    !Number.isFinite(metric.value)
  ) return "neutral";
  const shown = displayedChangePct(metric.value, 2);
  if (shown === 0) return "neutral";
  return shown > 0 ? "positive" : "negative";
}
