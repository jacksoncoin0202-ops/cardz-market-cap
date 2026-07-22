import { currencies, locales, type Currency, type Locale, type MarketMetric, type TrackedSalesMetric } from "./types";
import { copy } from "./i18n";

const intlLocale: Record<Locale, string> = {
  en: "en-US",
  "zh-TW": "zh-Hant-TW",
  "zh-CN": "zh-Hans-CN",
  ja: "ja-JP",
};

export function normaliseLocale(value: string | null | undefined): Locale {
  return locales.includes(value as Locale) ? (value as Locale) : "en";
}

export function normaliseCurrency(value: string | null | undefined): Currency {
  return currencies.includes(value as Currency) ? (value as Currency) : "USD";
}

export function formatMoney(
  valueUsd: number | null,
  currency: Currency,
  rates: Record<Currency, number>,
  locale: Locale,
  compact = false,
): string {
  if (valueUsd === null || !Number.isFinite(valueUsd)) return copy[locale].status.unavailable;
  const rate = rates[currency];
  if (!Number.isFinite(rate)) return copy[locale].status.unavailable;
  const converted = valueUsd * rate;
  return new Intl.NumberFormat(intlLocale[locale], {
    style: "currency",
    currency,
    maximumFractionDigits: compact ? 2 : converted < 100 ? 2 : 0,
    notation: compact ? "compact" : "standard",
  }).format(converted);
}

export function formatInteger(value: number | null, locale: Locale): string {
  if (value === null || !Number.isFinite(value)) return copy[locale].status.unavailable;
  return new Intl.NumberFormat(intlLocale[locale], { maximumFractionDigits: 0 }).format(value);
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
  return metric.status === "stale" ? `${value} (${copy[locale].status.stale})` : value;
}

export function formatMetricInteger(metric: MarketMetric<number>, locale: Locale): string {
  if (metric.value === null || metric.status === "accumulating" || metric.status === "unavailable") {
    return copy[locale].status[metric.status === "ready" ? "unavailable" : metric.status];
  }
  const value = formatInteger(metric.value, locale);
  return metric.status === "stale" ? `${value} (${copy[locale].status.stale})` : value;
}

export function formatPercent(metric: MarketMetric<number>, locale: Locale): string {
  if (metric.value === null || metric.status === "accumulating" || metric.status === "unavailable") {
    return copy[locale].status[metric.status === "ready" ? "unavailable" : metric.status];
  }
  const sign = metric.value > 0 ? "+" : "";
  const value = `${sign}${metric.value.toFixed(2)}%`;
  return metric.status === "stale" ? `${value} (${copy[locale].status.stale})` : value;
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
  ) return "—";
  const value = formatMoney(sales.valueUsd.value, currency, rates, locale, true);
  return sales.coverage === "stale" || sales.valueUsd.status === "stale" ? `${value} (${copy[locale].status.stale})` : value;
}

export function formatDate(value: string | null, locale: Locale): string {
  if (!value) return copy[locale].status.unavailable;
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return copy[locale].status.unavailable;
  return new Intl.DateTimeFormat(intlLocale[locale], {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

export function metricTone(metric: MarketMetric<number>): "positive" | "negative" | "neutral" {
  if (
    (metric.status !== "ready" && metric.status !== "stale") ||
    metric.value === null ||
    !Number.isFinite(metric.value) ||
    metric.value === 0
  ) return "neutral";
  return metric.value > 0 ? "positive" : "negative";
}
