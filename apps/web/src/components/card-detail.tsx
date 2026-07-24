"use client";

import Link from "next/link";
import { Share2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CardImage } from "./card-image";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { PriceDelta, MetricDelta } from "./rankings";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { graders, type MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

function ShareButton({ cardId, label, doneLabel, errorLabel }: { cardId: string; label: string; doneLabel: string; errorLabel: string }) {
  const [state, setState] = useState<"idle" | "done" | "error">("idle");
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timerRef.current) clearTimeout(timerRef.current); }, []);
  const share = useCallback(async () => {
    const url = `${window.location.origin}/card/${cardId}`;
    const finish = (next: "done" | "error") => {
      setState(next);
      if (timerRef.current) clearTimeout(timerRef.current);
      timerRef.current = setTimeout(() => setState("idle"), 2200);
    };
    if (navigator.share) {
      try { await navigator.share({ url }); finish("done"); } catch (error) {
        if (error instanceof DOMException && error.name === "AbortError") return;
        finish("error");
      }
      return;
    }
    try { await navigator.clipboard.writeText(url); finish("done"); } catch { finish("error"); }
  }, [cardId]);
  return (
    <button type="button" className="share-button" onClick={share} data-state={state}>
      <Share2 aria-hidden="true" size={14} strokeWidth={1.8} />
      <span>{state === "done" ? doneLabel : state === "error" ? errorLabel : label}</span>
    </button>
  );
}

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
      <div className="detail-actions">
        <Link className="back-link" href={href("/")}>← {t.nav.all}</Link>
        <ShareButton cardId={card.id} label={t.labels.share} doneLabel={t.labels.shareDone} errorLabel={t.labels.shareError} />
      </div>
      <article className="detail-grid">
        <section className="detail-art" aria-label={t.labels.imageAlt}>
          <span className="detail-rank">#{card.rank}</span>
          <CardImage image={card.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={card.image.alt[locale] || t.labels.imageAlt} />
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
            <div><span>{t.labels.marketCap}</span><strong className="metric-value-fit">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</strong><MetricDelta metric={card.marketCap} changePct={windowMetric.changePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.price}</span><strong className="detail-price-now">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</strong><PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.population}</span><strong>{formatMetricInteger(card.populationPsa10, locale)}</strong></div>
            <div><span>{t.periods[period]} {t.labels.change}</span><strong className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</strong></div>
            <div className="wide-metric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSales}</span><strong className="metric-value-fit">{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</strong><MetricDelta metric={windowMetric.trackedSales.valueUsd} changePct={windowMetric.changePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
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
                    <span>{grader} {population.topGrade || t.grader.topGrade}</span>
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
