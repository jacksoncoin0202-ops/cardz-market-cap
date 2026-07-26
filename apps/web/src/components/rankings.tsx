"use client";

import Link from "next/link";
import { ArrowDown, ArrowUp, TrendingDown, TrendingUp } from "lucide-react";
import { CardImage } from "./card-image";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { Tooltip } from "./tooltip";
import { copy } from "@/lib/i18n";
import { formatDeltaMoney, formatInteger, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, MarketCardView, MarketMetric, MarketViewSnapshot, TrackedSalesMetric } from "@/lib/types";

interface RankingsProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  watchlist?: boolean;
  marketLabel?: string;
}

export function MetricDelta({ metric, changePct, currency, rates, locale }: {
  metric: MarketMetric<number>;
  changePct: MarketMetric<number>;
  currency: Currency;
  rates: Record<Currency, number>;
  locale: Locale;
}) {
  const delta = formatDeltaMoney(metric, changePct, currency, rates, locale);
  if (!delta) return null;
  return <DeltaChip delta={delta} />;
}

export function PriceDelta({ card, period, currency, rates, locale }: {
  card: MarketCardView;
  period: "1d" | "7d" | "30d";
  currency: Currency;
  rates: Record<Currency, number>;
  locale: Locale;
}) {
  return <MetricDelta metric={card.pricePsa10} changePct={card.windows[period].changePct} currency={currency} rates={rates} locale={locale} />;
}

function SalesDelta({ sales, changePct, currency, rates, locale }: {
  sales: TrackedSalesMetric;
  changePct: MarketMetric<number>;
  currency: Currency;
  rates: Record<Currency, number>;
  locale: Locale;
}) {
  if (sales.coverage === "unavailable" || sales.valueUsd.value === null || sales.valueUsd.value <= 0
    || sales.count.value === null || sales.count.value <= 0) return null;
  return <MetricDelta metric={sales.valueUsd} changePct={changePct} currency={currency} rates={rates} locale={locale} />;
}

/* 人口係存量型：永遠唔會跌，負 delta 夾做中性，唔顯示跌箭嘴 */
function PopulationDelta({ card, period, locale }: { card: MarketCardView; period: "1d" | "7d" | "30d"; locale: Locale }) {
  const change = card.graderPopulations.PSA.topGradePopulationChangePct[period];
  const pop = card.populationPsa10;
  if (change.value === null || (change.status !== "ready" && change.status !== "stale") || change.value <= 0) return null;
  if (pop.value === null || (pop.status !== "ready" && pop.status !== "stale") || change.value <= -100) return null;
  const baseline = pop.value / (1 + change.value / 100);
  const delta = Math.round(pop.value - baseline);
  if (delta <= 0) return null;
  return (
    <span className="price-delta metric-positive" data-dir="up">
      <ArrowUp aria-hidden="true" size={11} strokeWidth={2.4} />
      +{formatInteger(delta, locale)}
    </span>
  );
}

function DeltaChip({ delta }: { delta: string }) {
  const up = delta.startsWith("+");
  const Icon = up ? ArrowUp : ArrowDown;
  return (
    <span className={`price-delta metric-${up ? "positive" : "negative"}`} data-dir={up ? "up" : "down"}>
      <Icon aria-hidden="true" size={11} strokeWidth={2.4} />
      {delta}
    </span>
  );
}

function CardIdentity({ card, locale, unavailable }: { card: MarketCardView; locale: Locale; unavailable: string }) {
  const name = card.name[locale] || unavailable;
  return (
    <div className="ranking-card-identity">
      <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></div>
      <div className="ranking-name"><strong>{name}</strong></div>
    </div>
  );
}

function ChangeBadge({ card, period, locale }: { card: MarketCardView; period: "1d" | "7d" | "30d"; locale: Locale }) {
  const change = card.windows[period].changePct;
  const tone = metricTone(change);
  const Icon = tone === "positive" ? TrendingUp : tone === "negative" ? TrendingDown : null;
  return (
    <span className={`mobile-change-badge metric-${tone}`}>
      {Icon && <Icon aria-hidden="true" size={13} strokeWidth={2} />}
      {formatPercent(change, locale)}
    </span>
  );
}

export function Rankings({ cards, locale, currency, snapshot, href, watchlist = false, marketLabel }: RankingsProps) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  return (
    <section className="rankings-section" id="market-ranking" aria-labelledby="ranking-heading">
      <div className="ranking-heading">
        <div>
          <p className="section-kicker">{watchlist ? t.labels.watchStatus : marketLabel ?? t.nav.all}</p>
          <h2 id="ranking-heading">{watchlist ? t.nav.watchlist : t.heatmap.rankingTitle.replace("{count}", String(cards.length))}</h2>
        </div>
        <PeriodSelector compact />
      </div>
      {!cards.length ? <p className="empty-state">{t.labels.noCards}</p> : (
        <>
          <div className="desktop-ranking-table">
            <table>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" /><col className="col-price" />
                <col className="col-pop" /><col className="col-cap" /><col className="col-sales" /><col className="col-change" /><col className="col-spark" />
              </colgroup>
              <thead><tr>
                <th>{t.labels.rank}</th><th>{t.labels.card}</th><th>{t.labels.number}</th>
                <th className="numeric">{t.labels.priceShort}<Tooltip label={t.labels.price} text={t.labels.priceHelp} /></th>
                <th className="numeric">{t.labels.populationShort}<Tooltip label={t.labels.population} text={t.labels.populationHelp} /></th>
                <th className="numeric">{t.labels.marketCapShort}<Tooltip label={t.labels.marketCap} text={t.labels.marketCapHelp} /></th>
                <th className="numeric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSalesShort}</span></th>
                <th className="numeric">{t.periods[period]} {t.labels.changeShort}</th>
                <th className="numeric"><span title={t.labels.salesHelp}>{t.labels.salesTrendShort}</span></th>
              </tr></thead>
              <tbody>{cards.map((card) => {
                const metrics = card.windows[period];
                return (
                  <tr key={card.id}>
                    <td className="rank-cell">{card.rank}</td>
                    <td><Link href={href(`/card/${card.id}`)}><CardIdentity card={card} locale={locale} unavailable={t.status.unavailable} /></Link></td>
                    <td className="collector-cell">{card.collectorNumber}</td>
                    <td className="numeric price-cell">
                      <span className="price-now">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                      <PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className="numeric">
                      <span className="pop-now">{formatMetricInteger(card.populationPsa10, locale)}</span>
                      <PopulationDelta card={card} period={period} locale={locale} />
                    </td>
                    <td className="numeric market-cap-cell">
                      <span className="price-now">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                      <MetricDelta metric={card.marketCap} changePct={metrics.marketCapChangePct} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className="numeric sales-cell" title={t.labels.salesHelp}>
                      <span className="price-now">{formatTrackedSales(metrics.trackedSales, currency, snapshot.rates, locale)}</span>
                      <SalesDelta sales={metrics.trackedSales} changePct={metrics.trackedSalesChangePct} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className={`numeric metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</td>
                    <td className="numeric spark-cell"><Sparkline points={card.historyDaily} label={t.labels.salesTrend} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="mobile-ranking-list">
            <div className="mobile-list-header" aria-hidden="true">
              <span className="mobile-col-info">{t.labels.card}</span>
              <span className="mobile-col-right">{t.labels.priceShort}</span>
              <span className="mobile-col-spark">{t.labels.salesTrendShort}</span>
            </div>
            {cards.map((card) => (
              <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                <span className="mobile-rank-index">{card.rank}</span>
                <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></div>
                <div className="mobile-card-info">
                  <span className="mobile-card-number">{card.collectorNumber}</span>
                  <strong className="mobile-card-name">{card.name[locale] || t.status.unavailable}</strong>
                  {card.marketCap.value !== null && (card.marketCap.status === "ready" || card.marketCap.status === "stale") && (
                    <span className="mobile-card-sub">
                      <span className="mobile-card-cap">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                    </span>
                  )}
                </div>
                <div className="mobile-card-right">
                  <span className="mobile-card-price">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                  <ChangeBadge card={card} period={period} locale={locale} />
                </div>
                <Sparkline points={card.historyDaily} label={t.labels.salesTrend} />
              </Link>
            ))}
          </div>
        </>
      )}
    </section>
  );
}
