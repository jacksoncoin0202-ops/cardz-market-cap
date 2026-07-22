"use client";

import Link from "next/link";
import { PeriodSelector } from "./period-selector";
import { copy } from "@/lib/i18n";
import { formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";

interface RankingsProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  watchlist?: boolean;
  marketLabel?: string;
}

function CardIdentity({ card, locale, unavailable }: { card: MarketCardView; locale: Locale; unavailable: string }) {
  return (
    <div className="ranking-card-identity">
      <div className="ranking-thumb"><img src={card.image.url} alt="" loading="lazy" /></div>
      <div className="ranking-name"><strong>{card.name[locale] || unavailable}</strong></div>
    </div>
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
          <h2 id="ranking-heading">{watchlist ? t.nav.watchlist : t.heatmap.rankingTitle}</h2>
        </div>
        <PeriodSelector compact />
      </div>
      {!cards.length ? <p className="empty-state">{t.labels.noCards}</p> : (
        <>
          <div className="desktop-ranking-table">
            <table>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" /><col className="col-price" />
                <col className="col-pop" /><col className="col-cap" /><col className="col-sales" /><col className="col-change" />
              </colgroup>
              <thead><tr>
                <th>{t.labels.rank}</th><th>{t.labels.card}</th><th>{t.labels.number}</th><th className="numeric">{t.labels.price}</th>
                <th className="numeric">{t.labels.population}</th><th className="numeric">{t.labels.marketCap}</th>
                <th className="numeric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSales}<sup>i</sup></span></th>
                <th className="numeric">{t.periods[period]} {t.labels.change}</th>
              </tr></thead>
              <tbody>{cards.map((card) => {
                const metrics = card.windows[period];
                return (
                  <tr key={card.id}>
                    <td className="rank-cell">{card.rank}</td>
                    <td><Link href={href(`/card/${card.id}`)}><CardIdentity card={card} locale={locale} unavailable={t.status.unavailable} /></Link></td>
                    <td className="collector-cell">{card.collectorNumber}</td>
                    <td className="numeric">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</td>
                    <td className="numeric">{formatMetricInteger(card.populationPsa10, locale)}</td>
                    <td className="numeric market-cap-cell">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</td>
                    <td className="numeric" title={t.labels.salesHelp}>{formatTrackedSales(metrics.trackedSales, currency, snapshot.rates, locale)}</td>
                    <td className={`numeric metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="mobile-ranking-list">
            {cards.map((card) => {
              const metrics = card.windows[period];
              return (
                <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                  <span className="mobile-rank">#{card.rank}</span>
                  <CardIdentity card={card} locale={locale} unavailable={t.status.unavailable} />
                  <span className="mobile-number">{card.collectorNumber}</span>
                  <dl>
                    <div><dt>{t.labels.marketCap}</dt><dd>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</dd></div>
                    <div><dt>{t.labels.price}</dt><dd>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</dd></div>
                    <div><dt>{t.labels.population}</dt><dd>{formatMetricInteger(card.populationPsa10, locale)}</dd></div>
                    <div><dt>{t.periods[period]} {t.labels.change}</dt><dd className={`metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</dd></div>
                    <div className="mobile-sales"><dt>{t.periods[period]} {t.labels.trackedSales}</dt><dd>{formatTrackedSales(metrics.trackedSales, currency, snapshot.rates, locale)}</dd></div>
                  </dl>
                </Link>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
