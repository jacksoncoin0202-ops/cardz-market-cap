"use client";

import Link from "next/link";
import { PeriodSelector } from "./period-selector";
import { copy } from "@/lib/i18n";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import { graders, type Grader, type MarketMetric, type MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

function populationText(metric: MarketMetric<number>, locale: keyof typeof copy): string {
  const t = copy[locale];
  if (metric.value === null) return t.status[metric.status === "ready" ? "unavailable" : metric.status];
  const value = formatInteger(metric.value, locale);
  return metric.status === "stale" ? `${value} (${t.status.stale})` : value;
}

export function GraderPage({ grader, snapshot }: { grader: Grader; snapshot: MarketViewSnapshot }) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];
  const title = t.grader.title.replace("{grader}", t.grader.names[grader]);
  return (
    <div className="page-shell grader-page" data-cardz-generation={snapshot.generation}>
      <section className="grader-hero">
        <p className="section-kicker">{t.grader.eyebrow}</p>
        <h1>{title}</h1>
        <p>{t.grader.body}</p>
        <nav className="grader-tabs" aria-label={t.nav.graders}>
          {graders.map((item) => (
            <Link key={item} href={href(`/graders/${item.toLowerCase()}`)} data-active={item === grader ? "true" : "false"}>{t.grader.names[item]}</Link>
          ))}
        </nav>
      </section>
      {snapshot.mode === "preview" && <p className="preview-notice" role="status">{t.previewNotice}</p>}
      <section className="grader-ranking" aria-labelledby="grader-ranking-heading">
        <div className="ranking-heading">
          <div>
            <h2 id="grader-ranking-heading">{t.grader.topGradePopulation}</h2>
            <p>{grader === "PSA" ? t.grader.marketCapAvailable : t.grader.marketCapUnavailable}</p>
          </div>
          <PeriodSelector compact />
        </div>
        <div className="desktop-ranking-table grader-table">
          <table>
            <colgroup><col className="col-rank" /><col className="col-card" /><col className="col-number" /><col /><col /><col /><col /><col /></colgroup>
            <thead><tr>
              <th>{t.labels.rank}</th><th>{t.labels.card}</th><th>{t.labels.number}</th><th>{t.grader.topGrade}</th>
              <th className="numeric">{t.grader.topGradePopulation}</th><th className="numeric">{t.grader.totalPopulation}</th>
              <th className="numeric">{t.periods[period]} {t.grader.populationChange}</th><th className="numeric">{t.labels.marketCap}</th>
            </tr></thead>
            <tbody>{snapshot.top100.map((card) => {
              const population = card.graderPopulations[grader];
              return (
                <tr key={card.id}>
                  <td className="rank-cell">{card.rank}</td>
                  <td><Link href={href(`/card/${card.id}`)}><span className="grader-card"><img src={card.image.url} alt="" /><strong>{card.name[locale] || t.status.unavailable}</strong></span></Link></td>
                  <td className="collector-cell">{card.collectorNumber}</td>
                  <td>{population.topGrade || t.status.unavailable}</td>
                  <td className="numeric">{populationText(population.topGradePopulation, locale)}</td>
                  <td className="numeric">{populationText(population.total, locale)}</td>
                  <td className={`numeric metric-${metricTone(population.topGradePopulationChangePct[period])}`}>{formatPercent(population.topGradePopulationChangePct[period], locale)}</td>
                  <td className="numeric market-cap-cell">{grader === "PSA" ? formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true) : "—"}</td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
        <div className="mobile-ranking-list grader-mobile-list">
          {snapshot.top100.map((card) => {
            const population = card.graderPopulations[grader];
            return (
              <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                <span className="mobile-rank">#{card.rank}</span>
                <span className="grader-card"><img src={card.image.url} alt="" /><strong>{card.name[locale] || t.status.unavailable}</strong></span>
                <span className="mobile-number">{card.collectorNumber}</span>
                <dl>
                  <div><dt>{population.topGrade || t.grader.topGrade}</dt><dd>{populationText(population.topGradePopulation, locale)}</dd></div>
                  <div><dt>{t.grader.totalPopulation}</dt><dd>{populationText(population.total, locale)}</dd></div>
                  <div><dt>{t.periods[period]} {t.grader.populationChange}</dt><dd className={`metric-${metricTone(population.topGradePopulationChangePct[period])}`}>{formatPercent(population.topGradePopulationChangePct[period], locale)}</dd></div>
                  <div><dt>{t.labels.marketCap}</dt><dd>{grader === "PSA" ? formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true) : "—"}</dd></div>
                </dl>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
