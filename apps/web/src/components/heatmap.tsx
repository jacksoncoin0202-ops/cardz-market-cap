"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PeriodSelector } from "./period-selector";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { rankedStripLayout } from "@/lib/ranked-strip-layout";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";

interface HeatmapProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  title: string;
  eyebrow: string;
}

function tileTint(card: MarketCardView, period: "1d" | "7d" | "30d"): string {
  const metric = card.windows[period].changePct;
  if ((metric.status !== "ready" && metric.status !== "stale") || metric.value === null || !Number.isFinite(metric.value)) {
    return "rgba(69, 75, 82, 0.38)";
  }
  const clamped = Math.max(-10, Math.min(10, metric.value));
  const freshness = metric.status === "stale" ? 0.68 : 1;
  const strength = (0.26 + Math.abs(clamped) / 10 * 0.34) * freshness;
  if (clamped > 0) return `rgba(27, 113, 78, ${strength})`;
  if (clamped < 0) return `rgba(151, 38, 70, ${strength})`;
  return "rgba(69, 75, 82, 0.32)";
}

function CardFacts({ card, locale, currency, snapshot }: Omit<HeatmapProps, "cards" | "href" | "title" | "eyebrow"> & { card: MarketCardView }) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const windowMetric = card.windows[period];
  return (
    <dl className="preview-facts">
      <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
      <div><dt>{t.labels.marketCap}</dt><dd>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</dd></div>
      <div><dt>{t.labels.price}</dt><dd>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.labels.population}</dt><dd>{formatMetricInteger(card.populationPsa10, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.trackedSales}</dt><dd>{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.change}</dt><dd className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</dd></div>
    </dl>
  );
}

function BottomSheet({ card, locale, currency, snapshot, href, onClose }: Omit<HeatmapProps, "cards" | "title" | "eyebrow"> & { card: MarketCardView; onClose: () => void }) {
  const t = copy[locale];
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const keepFocusInside = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const dialog = closeRef.current?.closest<HTMLElement>("[role='dialog']");
      const focusable = dialog ? Array.from(dialog.querySelectorAll<HTMLElement>("button, a[href], [tabindex]:not([tabindex='-1'])")) : [];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keepFocusInside);
    document.body.classList.add("sheet-open");
    return () => {
      document.removeEventListener("keydown", keepFocusInside);
      document.body.classList.remove("sheet-open");
    };
  }, [onClose]);
  return (
    <div className="sheet-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="bottom-sheet" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
        <div className="sheet-handle" />
        <button ref={closeRef} className="sheet-close" type="button" onClick={onClose}>{t.labels.close}</button>
        <div className="sheet-card-layout">
          <div className="sheet-image"><img src={card.image.url} alt={card.image.alt[locale] || t.labels.imageAlt} /></div>
          <div>
            <p className="rank-kicker">#{card.rank} / {card.tcg}</p>
            <h3 id="sheet-title">{card.name[locale] || t.status.unavailable}</h3>
            <p className="muted-copy">{card.setName[locale] || t.status.unavailable}</p>
            <CardFacts card={card} locale={locale} currency={currency} snapshot={snapshot} />
            <Link className="primary-action" href={href(`/card/${card.id}`)}>{t.labels.viewCard}</Link>
          </div>
        </div>
      </section>
    </div>
  );
}

export function Heatmap({ cards, locale, currency, snapshot, href, title, eyebrow }: HeatmapProps) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [active, setActive] = useState<MarketCardView | null>(null);
  const [sheetCard, setSheetCard] = useState<MarketCardView | null>(null);
  const lastTriggerRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  const tiles = useMemo(() => rankedStripLayout(
    cards.map((card) => ({ card, rank: card.rank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [cards, size.height, size.width]);

  const closeSheet = useCallback(() => {
    setSheetCard(null);
    requestAnimationFrame(() => lastTriggerRef.current?.focus());
  }, []);

  return (
    <section className="heatmap-section" aria-labelledby="heatmap-heading">
      <div className="heatmap-heading">
        <div>
          <p className="section-kicker">{eyebrow}</p>
          <h1 id="heatmap-heading">{title}</h1>
          <p>{t.heatmap.body}</p>
          {snapshot.mode === "preview" && <p className="heatmap-preview-notice" role="status">{t.previewNotice}</p>}
        </div>
        <PeriodSelector />
      </div>
      <div className="heatmap-frame" ref={frameRef} onMouseLeave={() => setActive(null)}>
        {tiles.map(({ item, x, y, width, height }) => {
          const card = item.card;
          return (
            <Link
              className="heatmap-tile"
              key={card.id}
              href={href(`/card/${card.id}`)}
              style={{ left: x, top: y, width, height }}
              aria-label={`#${card.rank} ${card.name[locale] || t.status.unavailable}, ${card.collectorNumber}`}
              onMouseEnter={() => setActive(card)}
              onFocus={() => setActive(card)}
              onClick={(event) => {
                if (!window.matchMedia("(hover: none), (pointer: coarse)").matches) return;
                event.preventDefault();
                lastTriggerRef.current = event.currentTarget;
                setSheetCard(card);
              }}
            >
              <img src={card.image.url} alt="" loading={card.rank <= 8 ? "eager" : "lazy"} />
              <span className="tile-tint" style={{ backgroundColor: tileTint(card, period) }} />
              <span className="tile-rank">#{card.rank}</span>
            </Link>
          );
        })}
        {active && (
          <aside className="heatmap-preview" aria-live="polite">
            <div className="preview-image"><img src={active.image.url} alt={active.image.alt[locale] || t.labels.imageAlt} /></div>
            <div className="preview-copy">
              <p className="rank-kicker">#{active.rank} / {active.tcg}</p>
              <h3>{active.name[locale] || t.status.unavailable}</h3>
              <p className="muted-copy">{active.setName[locale] || t.status.unavailable}</p>
              <CardFacts card={active} locale={locale} currency={currency} snapshot={snapshot} />
              <p className="preview-time">{t.labels.asOf}: {formatDate(active.windows[period].changePct.asOf ?? active.pricePsa10.asOf, locale)}</p>
              <Link className="primary-action" href={href(`/card/${active.id}`)}>{t.labels.viewCard}</Link>
            </div>
          </aside>
        )}
      </div>
      <div className="heatmap-footer">
        <div className="heatmap-legend" aria-label={t.heatmap.body}>
          <div><span className="legend-swatch down" />{t.heatmap.negative}</div>
          <div><span className="legend-swatch pending" />{t.heatmap.neutral}</div>
          <div><span className="legend-swatch up" />{t.heatmap.positive}</div>
          <div className="legend-count">{cards.length} / 100 {t.heatmap.count}</div>
        </div>
        <a className="ranking-jump" href="#market-ranking">{t.heatmap.viewRanking}</a>
      </div>
      {sheetCard && <BottomSheet card={sheetCard} locale={locale} currency={currency} snapshot={snapshot} href={href} onClose={closeSheet} />}
    </section>
  );
}
