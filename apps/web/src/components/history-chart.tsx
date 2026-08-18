"use client";

import { useEffect, useRef, useState, type CSSProperties } from "react";
import { ChartLine } from "lucide-react";
import { EmptyState } from "./empty-state";
import { revealOnce } from "./reveal";
import { copy } from "@/lib/i18n";
import { formatMoney, formatObservationDayMonth } from "@/lib/format";
import { pointsForWindow } from "@/lib/history-window";
import { useMarketSettings } from "@/lib/use-market-settings";
import { marketWindowDays, type Currency, type Locale, type PricePoint } from "@/lib/types";
import "@/app/styles/history-chart.css";

interface HistoryChartProps {
  points: PricePoint[];
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
}

/* 入場最長嗰條：bar 最尾一條 delay 20×30ms + 420ms = 1020ms（線 900ms、點 760+240ms）。
   加 180ms buffer 先收 state，唔好喺 keyframe 未完就抽走條 rule。 */
const DRAW_TOTAL_MS = 1200;

/* bar 出唔出嘅條件抽咗做一個 predicate：render 嗰陣同計 stagger 序號嗰陣要同一句，
   兩處各寫一次就一定有一日行開（AGENTS.md 規矩 13）。 */
function hasSalesBar(point: PricePoint): point is PricePoint & { trackedSalesValueUsd: number; trackedSalesCount: number } {
  return point.salesCoverage !== "unavailable" &&
    point.trackedSalesValueUsd !== null &&
    point.trackedSalesValueUsd > 0 &&
    point.trackedSalesCount !== null &&
    point.trackedSalesCount > 0;
}

export function HistoryChart({ points, locale, currency, rates }: HistoryChartProps) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  /*
   * draw-in state（FE05 WS3）：JSX 預設**冇** data-draw，即係 SSR 出嘅係畫好嘅圖。
   * revealOnce 自己揸三個閘（reduced-motion / <981px / 已經喺視窗），唔夠條件就
   * 由頭到尾冇 state，亦冇 observer subscription。hook 要喺下面 early return 之前 call。
   */
  const panelRef = useRef<HTMLElement>(null);
  const [draw, setDraw] = useState<"in" | "done" | null>(null);
  useEffect(() => {
    const el = panelRef.current;
    if (!el) return;
    return revealOnce(el, () => setDraw("in"));
  }, []);
  /*
   * 入場播一次就升做 "done"。三條 draw rule 全部掛喺 [data-draw="in"]，所以 "done"
   * 之後換 period（bar / dot 嘅 React key 帶住 point.at，換窗即係全新節點）唔會再播。
   * 唔改就變成：畫好晒之後撳一下 7D，333 個價點靜音 760ms、15 條 bar 塌返落去再升，
   * 而條線仲喺度 —— 一次 routine 操作生 ~348 個 animation（審核 major #2）。
   * 清 state 唔會跳格：三條 rule 嘅終態同 base 樣（冇 dasharray / 冇 transform / opacity 1）。
   */
  useEffect(() => {
    if (draw !== "in") return;
    const timer = window.setTimeout(() => setDraw("done"), DRAW_TOTAL_MS);
    return () => window.clearTimeout(timer);
  }, [draw]);
  const days = marketWindowDays[period];
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
  /* `.history-empty` 個 class 留住（margin-top 24px 由佢出），只係內容換咗共用 EmptyState */
  if (prices.length < 2 && sales.length === 0) {
    return <EmptyState className="history-empty" icon={ChartLine} title={t.labels.noHistory} />;
  }

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
  /* stagger 序號按「真係畫得出嘅 bar」數：直接用 selected 個 index 會因為中間好多日
     冇成交而出現一格格空窗（延遲跳格），睇落似卡格。 */
  const barOrder = new Map<number, number>();
  selected.forEach((point, index) => {
    if (hasSalesBar(point)) barOrder.set(index, barOrder.size);
  });

  return (
    <section className="history-panel" aria-labelledby="history-heading" ref={panelRef} data-draw={draw ?? undefined}>
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
            if (!hasSalesBar(point)) return null;
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
                style={{ "--bar-i": Math.min(barOrder.get(index) ?? 0, 20) } as CSSProperties}
              >
                <title>{`${point.at}: ${formatMoney(point.trackedSalesValueUsd, currency, rates, locale)}`}</title>
              </rect>
            );
          })}
          {/* pathLength="1" 只係換咗 dasharray / dashoffset 嘅單位（變 0–1 比例），
              視覺上冇分別；draw-in 冇播嗰陣條線一樣係完整嘅。 */}
          {line && <path d={line} className="price-line" pathLength="1" />}
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
