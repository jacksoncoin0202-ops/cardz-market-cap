"use client";

import Link from "next/link";
import { CardImage } from "./card-image";
import { CopyButton } from "./copy-button";
import { CapTicker } from "./cap-ticker";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { DETAIL_PRINT_FIELDS, printIdentityRows } from "./print-badge";
import { PriceDelta, MetricDelta } from "./rankings";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { displayTopGrade } from "@/lib/grade-label";
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
      <div className="detail-actions">
        <Link className="back-link" href={href("/")}>← {t.nav.all}</Link>
        <CopyButton getText={() => `${window.location.origin}/card/${card.id}`} label={t.labels.share} doneLabel={t.labels.shareDone} errorLabel={t.labels.shareError} preferNativeShare />
      </div>
      <article className="detail-grid">
        <section className="detail-art" aria-label={t.labels.imageAlt}>
          <span className="detail-rank">#{card.marketRank}</span>
          <CardImage image={card.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={card.image.alt[locale] || t.labels.imageAlt} />
        </section>
        <div className="detail-content">
          <header className="detail-header">
            <p className="section-kicker">{card.tcg}</p>
            <h1>{card.name[locale] || t.status.unavailable}</h1>
            <p className="detail-set">{card.setName[locale] || t.status.unavailable}</p>
            {/* 印刷版本逐條併入現有 identity list：冇值嘅欄根本唔會回，
                所以完全冇資料嗰陣呢個 dl 同以前一模一樣。
                owner 2026-08-02：語言版本＋卡包來源喺內頁出齊（DETAIL_PRINT_FIELDS 包
                editionCode），唔再出 badge —— 欄位先係佢要嘅形式。 */}
            <dl className="identity-list">
              <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
              {printIdentityRows(card, locale, DETAIL_PRINT_FIELDS).map((row) => (
                <div key={row.key}><dt>{row.label}</dt><dd>{row.value}</dd></div>
              ))}
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
            <div><span>{t.labels.marketCap}</span><strong className="metric-value-fit">{card.marketCap.value === null ? formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true) : <CapTicker key={card.marketCap.value} value={card.marketCap.value} format={(n) => formatMoney(n, currency, snapshot.rates, locale, true)} />}</strong><MetricDelta metric={card.marketCap} changePct={windowMetric.marketCapChangePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.price}</span><strong className="detail-price-now">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</strong><PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.population}</span><strong>{formatMetricInteger(card.populationPsa10, locale)}</strong></div>
            <div><span>{t.periods[period]} {t.labels.change}</span><strong className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</strong></div>
            <div className="wide-metric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSales}</span><strong className="metric-value-fit">{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</strong><MetricDelta metric={windowMetric.trackedSales.valueUsd} changePct={windowMetric.trackedSalesChangePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.ungradedReference}</span><strong>{formatMetricMoney(card.priceUngradedReference, currency, snapshot.rates, locale)}</strong></div>
          </section>
          <section className="grader-supply-panel" aria-labelledby="grader-supply-heading">
            <h2 id="grader-supply-heading">{t.nav.graders}</h2>
            <div className="grader-supply-grid">
              {graders.map((grader) => {
                const population = card.graderPopulations[grader];
                const total = population.total.value;
                const top = population.topGradePopulation.value;
                const gemPct = total !== null && total > 0 && top !== null ? (top / total) * 100 : null;
                return (
                  <div key={grader}>
                    <span>{grader} {displayTopGrade(grader, population.topGrade) || t.grader.topGrade}</span>
                    <strong>{formatMetricInteger(population.topGradePopulation, locale)}</strong>
                    <small>{formatMetricInteger(population.total, locale)}{gemPct !== null ? ` · ${gemPct.toFixed(1)}%` : ""}</small>
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
