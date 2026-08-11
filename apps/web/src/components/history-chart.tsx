"use client";

import { copy } from "@/lib/i18n";
import { formatMoney, formatObservationDayMonth } from "@/lib/format";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, PricePoint } from "@/lib/types";

interface HistoryChartProps {
  points: PricePoint[];
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
}

export function pointsForWindow(points: PricePoint[], days: number): PricePoint[] {
  const sorted = points
    .filter((point) => Number.isFinite(Date.parse(point.at)))
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const latest = sorted.at(-1);
  if (!latest) return [];
  if (days === 1) return sorted.slice(-2);
  const end = Date.parse(latest.at);
  const cutoff = end - days * 86_400_000;
  const inside = sorted.filter((point) => Date.parse(point.at) >= cutoff);
  const anchor = sorted.filter((point) => Date.parse(point.at) < cutoff).at(-1);
  return anchor ? [anchor, ...inside] : inside;
}

export function HistoryChart({ points, locale, currency, rates }: HistoryChartProps) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const days = period === "1d" ? 1 : period === "7d" ? 7 : 30;
  const selected = pointsForWindow(points, days);
  const prices = selected.filter((point) => point.priceUsd !== null && Number.isFinite(point.priceUsd));
  const sales = selected.filter((point) =>
    point.salesCoverage !== "unavailable" &&
    point.trackedSalesValueUsd !== null &&
    point.trackedSalesValueUsd > 0 &&
    point.trackedSalesCount !== null &&
    point.trackedSalesCount > 0 &&
    Number.isFinite(point.trackedSalesValueUsd),
  );
  if (prices.length < 2 && sales.length === 0) return <div className="history-empty"><p>{t.labels.noHistory}</p></div>;

  const width = 720;
  const height = 300;
  const pad = { top: 28, right: 20, bottom: 42, left: 74 };
  const plotWidth = width - pad.left - pad.right;
  const plotHeight = height - pad.top - pad.bottom;
  const priceValues = prices.map((point) => point.priceUsd as number);
  const min = priceValues.length ? Math.min(...priceValues) : 0;
  const max = priceValues.length ? Math.max(...priceValues) : 1;
  const spread = Math.max(max - min, Math.max(1, max * 0.02));
  const yMin = Math.max(0, min - spread * 0.2);
  const yMax = max + spread * 0.2;
  const startTime = Date.parse(selected[0]?.at ?? "");
  const endTime = Date.parse(selected.at(-1)?.at ?? "");
  const x = (point: PricePoint) => endTime <= startTime
    ? pad.left + plotWidth / 2
    : pad.left + (Date.parse(point.at) - startTime) / (endTime - startTime) * plotWidth;
  const y = (value: number) => pad.top + (yMax - value) / Math.max(1, yMax - yMin) * plotHeight;
  const line = selected.reduce<{ drawing: boolean; segments: string[] }>((state, point) => {
    if (point.priceUsd === null || !Number.isFinite(point.priceUsd)) return { drawing: false, segments: state.segments };
    const command = state.drawing ? "L" : "M";
    return { drawing: true, segments: [...state.segments, `${command}${x(point).toFixed(1)},${y(point.priceUsd).toFixed(1)}`] };
  }, { drawing: false, segments: [] }).segments.join(" ");
  const yTicks = Array.from({ length: 4 }, (_, index) => yMin + (yMax - yMin) * index / 3);
  const maxSales = Math.max(1, ...sales.map((point) => point.trackedSalesValueUsd ?? 0));
  const barBand = Math.max(2, Math.min(16, plotWidth / Math.max(1, selected.length) * 0.58));
  const date = (value: string) => formatObservationDayMonth(value, locale);

  return (
    <section className="history-panel" aria-labelledby="history-heading">
      <div className="history-heading">
        <h2 id="history-heading">{t.labels.history}</h2>
        <span>{t.periods[period]}</span>
      </div>
      <div className="chart-legend">
        <span><i className="price-key" />{t.labels.dailyPrice}</span>
        <span><i className="sales-key" />{t.labels.trackedSalesBars}</span>
      </div>
      <div className="chart-stage">
        <svg className="history-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-labelledby="chart-title chart-description" preserveAspectRatio="xMidYMid meet">
          <title id="chart-title">{t.labels.history}</title>
          <desc id="chart-description">{t.labels.dailyPrice}. {t.labels.salesHelp}</desc>
          {yTicks.map((tick) => (
            <g key={tick}>
              <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className="chart-gridline" />
              <text x={pad.left - 10} y={y(tick) + 4} textAnchor="end" className="chart-label">{formatMoney(tick, currency, rates, locale, true)}</text>
            </g>
          ))}
          {selected.map((point, index) => {
            if (
              point.salesCoverage === "unavailable" ||
              point.trackedSalesValueUsd === null ||
              point.trackedSalesValueUsd <= 0 ||
              point.trackedSalesCount === null ||
              point.trackedSalesCount <= 0
            ) return null;
            const barHeight = Math.max(1, (point.trackedSalesValueUsd / maxSales) * plotHeight * 0.28);
            return (
              <rect
                key={`sale-${point.at}-${index}`}
                x={x(point) - barBand / 2}
                y={height - pad.bottom - barHeight}
                width={barBand}
                height={barHeight}
                rx="1.5"
                className="sales-bar"
              >
                <title>{`${point.at}: ${formatMoney(point.trackedSalesValueUsd, currency, rates, locale)}`}</title>
              </rect>
            );
          })}
          {line && <path d={line} className="price-line" />}
          {selected.map((point, index) => point.priceUsd === null ? null : (
            <circle key={`price-${point.at}-${index}`} cx={x(point)} cy={y(point.priceUsd)} r="3" className="price-point">
              <title>{`${point.at}: ${formatMoney(point.priceUsd, currency, rates, locale)}`}</title>
            </circle>
          ))}
          {selected[0] && <text x={pad.left} y={height - 12} textAnchor="start" className="chart-label">{date(selected[0].at)}</text>}
          {selected.at(-1) && <text x={width - pad.right} y={height - 12} textAnchor="end" className="chart-label">{date(selected.at(-1)!.at)}</text>}
        </svg>
      </div>
    </section>
  );
}
