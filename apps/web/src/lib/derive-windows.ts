import type { MarketMetric, WindowMetrics } from "./types";

export const longWindows = ["90d", "180d", "365d"] as const;
export type LongWindow = (typeof longWindows)[number];
export const longWindowDays = { "90d": 90, "180d": 180, "365d": 365 } as const;

/*
 * 長窗（90d / 180d / 365d）唔跟 producer 嘅 nearest-day。
 * PC 月線每月 1 號先郁，緊帶 ±5/10 日會喺「啱啱好 90 日前」落空，睇落好似冇 1Y。
 * 誠實 as-of：用 asOf−N 當日或之前最後一個 ready 價。冇錨就 accumulating，唔發明數。
 * 1d / 7d / 30d 仍然用 snapshot 已發布嘅窗，呢度唔覆寫。
 */

const DAY_MS = 86_400_000;

export type HistoryPricePoint = {
  at?: string;
  date?: string;
  priceUsd?: number | null;
  trackedSalesValueUsd?: number | null;
  trackedSalesCount?: number | null;
  soldCount?: number;
  soldValueUsd?: number | null;
  salesCoverage?: string;
};

function atMs(point: HistoryPricePoint): number | null {
  const raw = point.at || (point.date ? `${point.date.slice(0, 10)}T00:00:00Z` : null);
  if (!raw) return null;
  const ms = Date.parse(raw);
  return Number.isFinite(ms) ? ms : null;
}

function pointAt(point: HistoryPricePoint): string | null {
  if (point.at) return point.at;
  return point.date ? `${point.date.slice(0, 10)}T00:00:00Z` : null;
}

function readyPrice(point: HistoryPricePoint): number | null {
  return typeof point.priceUsd === "number" && Number.isFinite(point.priceUsd) ? point.priceUsd : null;
}

export function latestPriceOnOrBefore(
  history: HistoryPricePoint[],
  targetMs: number,
): { at: string; priceUsd: number } | null {
  let winner: { at: string; priceUsd: number; ms: number } | null = null;
  for (const point of history) {
    const price = readyPrice(point);
    const ms = atMs(point);
    const at = pointAt(point);
    if (price === null || ms === null || at === null || ms > targetMs) continue;
    if (!winner || ms > winner.ms) winner = { at, priceUsd: price, ms };
  }
  return winner ? { at: winner.at, priceUsd: winner.priceUsd } : null;
}

export function percentage(current: number | null, previous: number | null): number | null {
  return current === null || previous === null || previous === 0
    ? null
    : (current / previous - 1) * 100;
}

export function salesTotal(
  history: HistoryPricePoint[],
  endMs: number,
  days: number,
): { value: number; count: number; asOf: string } | null {
  const startMs = endMs - (days - 1) * DAY_MS;
  let value = 0;
  let count = 0;
  let asOf: string | null = null;
  let any = false;
  for (const point of history) {
    const ms = atMs(point);
    const at = pointAt(point);
    if (ms === null || at === null || ms < startMs || ms > endMs) continue;
    if (point.salesCoverage === "unavailable") continue;
    const soldValue = point.trackedSalesValueUsd ?? point.soldValueUsd;
    const soldCount = point.trackedSalesCount ?? point.soldCount;
    if (soldValue == null && soldCount == null) continue;
    any = true;
    if (typeof soldValue === "number" && Number.isFinite(soldValue)) value += soldValue;
    if (typeof soldCount === "number" && Number.isFinite(soldCount)) count += soldCount;
    if (!asOf || at > asOf) asOf = at;
  }
  return any && asOf ? { value, count, asOf } : null;
}

function metric(value: number | null, status: MarketMetric<number>["status"], asOf: string | null): MarketMetric<number> {
  return { value, status, asOf };
}

export function deriveWindowMetrics(
  history: HistoryPricePoint[],
  currentPrice: number | null,
  currentAsOf: string | null,
  window: LongWindow,
  reference: HistoryPricePoint[] = [],
): WindowMetrics {
  const days = longWindowDays[window];
  const currentMs = currentAsOf ? Date.parse(currentAsOf) : Number.NaN;
  const targetMs = currentMs - days * DAY_MS;
  /* 混合錨（R6b）：真成交錨行先，冇先至退參考點（K 線）。呢個 function 只服務
     長窗（type `LongWindow`），所以短窗由構造上入唔到呢條後備。 */
  const anchor = Number.isFinite(currentMs)
    ? (latestPriceOnOrBefore(history, targetMs) ?? latestPriceOnOrBefore(reference, targetMs))
    : null;
  const priceChange = percentage(currentPrice, anchor?.priceUsd ?? null);
  const currentSales = Number.isFinite(currentMs) ? salesTotal(history, currentMs, days) : null;
  const previousSales = Number.isFinite(currentMs) ? salesTotal(history, currentMs - days * DAY_MS, days) : null;
  const salesChange = percentage(currentSales?.value ?? null, previousSales?.value ?? null);
  const accumulating = currentPrice === null ? "unavailable" : "accumulating";
  return {
    changePct: metric(priceChange, priceChange === null ? accumulating : "ready", priceChange === null ? null : currentAsOf),
    // 出街 history 冇每日 pop。舊價×今日 pop = 作一年前市值。唔作，灰。
    marketCapChangePct: metric(null, accumulating, null),
    trackedSalesChangePct: metric(
      salesChange,
      salesChange === null ? (currentSales ? "accumulating" : "unavailable") : "ready",
      salesChange === null ? null : currentSales?.asOf ?? null,
    ),
    trackedSales: {
      valueUsd: metric(currentSales?.value ?? null, currentSales ? "ready" : "unavailable", currentSales?.asOf ?? null),
      count: metric(currentSales?.count ?? null, currentSales ? "ready" : "unavailable", currentSales?.asOf ?? null),
      coverage: currentSales ? "partial" : "unavailable",
      asOf: currentSales?.asOf ?? null,
    },
  };
}

export function deriveLongWindows(
  history: HistoryPricePoint[],
  currentPrice: number | null,
  currentAsOf: string | null,
  reference: HistoryPricePoint[] = [],
): Record<LongWindow, WindowMetrics> {
  return Object.fromEntries(
    longWindows.map((window) => [
      window,
      deriveWindowMetrics(history, currentPrice, currentAsOf, window, reference),
    ]),
  ) as Record<LongWindow, WindowMetrics>;
}

export function deriveBoxWindow(
  history: HistoryPricePoint[],
  currentPrice: number | null,
  currentAsOf: string | null,
  window: LongWindow,
): { changePct: MarketMetric<number>; soldCount: number } {
  const days = longWindowDays[window];
  const currentMs = currentAsOf ? Date.parse(currentAsOf) : Number.NaN;
  const targetMs = currentMs - days * DAY_MS;
  const anchor = Number.isFinite(currentMs) ? latestPriceOnOrBefore(history, targetMs) : null;
  const priceChange = percentage(currentPrice, anchor?.priceUsd ?? null);
  const accumulating = currentPrice === null ? "unavailable" : "accumulating";
  const sales = Number.isFinite(currentMs) ? salesTotal(history, currentMs, days) : null;
  return {
    changePct: {
      value: priceChange,
      status: priceChange === null ? accumulating : "ready",
      asOf: priceChange === null ? null : currentAsOf,
    },
    soldCount: sales?.count ?? 0,
  };
}
