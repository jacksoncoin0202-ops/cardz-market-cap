"use client";

import Link from "next/link";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { graders, type MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

export function CardDetail({ id, snapshot }: { id: string; snapshot: MarketViewSnapshot }) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];
  const card = snapshot.top100.find((item) => item.id === id);

  if (!card) {
    return (
      <div className="page-shell"><section className="empty-detail">
        <p>{t.labels.noCards}</p>
        <Link className="primary-action" href={href("/")}>{t.nav.all}</Link>
      </section></div>
    );
  }

  const windowMetric = card.windows[period];
  const story = card.story[locale];
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "VisualArtwork",
        name: card.name[locale] || t.status.unavailable,
        identifier: card.collectorNumber,
        image: absolutePublicUrl(card.image.url),
        inLanguage: card.language,
        description: story || undefined,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: t.nav.all, item: absolutePublicUrl(href("/")) },
          { "@type": "ListItem", position: 2, name: card.name[locale] || t.status.unavailable },
        ],
      },
    ],
  };
  return (
    <div className="page-shell detail-page">
      <StructuredData value={structuredData} />
      <Link className="back-link" href={href("/")}>← {t.nav.all}</Link>
      {snapshot.mode === "preview" && <p className="preview-notice" role="status">{t.previewNotice}</p>}
      <article className="detail-grid">
        <section className="detail-art" aria-label={t.labels.imageAlt}>
          <span className="detail-rank">#{card.rank}</span>
          <img src={card.image.url} alt={card.image.alt[locale] || t.labels.imageAlt} />
        </section>
        <div className="detail-content">
          <header className="detail-header">
            <p className="section-kicker">{card.tcg} / {card.language}</p>
            <h1>{card.name[locale] || t.status.unavailable}</h1>
            <p className="detail-set">{card.setName[locale] || t.status.unavailable}</p>
            <dl className="identity-list">
              <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
              <div><dt>{t.labels.language}</dt><dd>{card.language}</dd></div>
            </dl>
          </header>
          {story && (
            <section className="story-panel">
              <h2>{t.labels.story}</h2>
              <p>{story}</p>
            </section>
          )}
          <div className="detail-period-row"><PeriodSelector compact /></div>
          <section className="detail-metrics" aria-label={t.labels.marketCap}>
            <div><span>{t.labels.marketCap}</span><strong>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</strong></div>
            <div><span>{t.labels.price}</span><strong>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</strong></div>
            <div><span>{t.labels.population}</span><strong>{formatMetricInteger(card.populationPsa10, locale)}</strong></div>
            <div><span>{t.periods[period]} {t.labels.change}</span><strong className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</strong></div>
            <div className="wide-metric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSales}<sup>i</sup></span><strong>{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</strong></div>
          </section>
          <section className="grader-supply-panel" aria-labelledby="grader-supply-heading">
            <h2 id="grader-supply-heading">{t.nav.graders}</h2>
            <div className="grader-supply-grid">
              {graders.map((grader) => {
                const population = card.graderPopulations[grader];
                return (
                  <div key={grader}>
                    <span>{grader} {population.topGrade || t.grader.topGrade}</span>
                    <strong>{formatMetricInteger(population.topGradePopulation, locale)}</strong>
                  </div>
                );
              })}
            </div>
          </section>
          <p className="data-time">{t.labels.asOf}: {formatDate(snapshot.effectiveAt || card.pricePsa10.asOf, locale)}</p>
          <HistoryChart points={card.historyDaily} locale={locale} currency={currency} rates={snapshot.rates} />
        </div>
      </article>
    </div>
  );
}
