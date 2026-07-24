"use client";

import { useMemo } from "react";
import { copy } from "@/lib/i18n";
import { formatPercent, metricTone } from "@/lib/format";
import { graders, type Grader, type Locale, type MarketCardView, type MarketMetric, type MarketWindow } from "@/lib/types";

interface GraderStat {
  grader: Grader;
  topGradePop: number;
  change: MarketMetric<number>;
}

function readyValue(metric: MarketMetric<number>): number {
  return metric.value !== null && Number.isFinite(metric.value) ? metric.value : 0;
}

export function GradingPulse({ cards, locale, period }: { cards: MarketCardView[]; locale: Locale; period: MarketWindow }) {
  const t = copy[locale];
  const stats = useMemo<GraderStat[]>(() => {
    const aggregate = graders.map((grader) => {
      let topGradePop = 0;
      let numerator = 0;
      let denominator = 0;
      let asOf: string | null = null;
      for (const card of cards) {
        const population = card.graderPopulations[grader];
        const current = readyValue(population.topGradePopulation);
        topGradePop += current;
        const change = population.topGradePopulationChangePct[period];
        if (change.value === null || !Number.isFinite(change.value)) continue;
        const baseline = current / (1 + change.value / 100);
        numerator += current - baseline;
        denominator += baseline;
        if (!asOf && change.asOf) asOf = change.asOf;
      }
      const value = denominator > 0 ? (numerator / denominator) * 100 : 0;
      return { grader, topGradePop, change: { value, status: "ready" as const, asOf } };
    });
    return aggregate.sort((a, b) => b.topGradePop - a.topGradePop);
  }, [cards, period]);

  const total = stats.reduce((sum, stat) => sum + stat.topGradePop, 0);
  const windowCaption = t.gradingPulse.windowCaption.replace("{period}", t.periods[period]);

  return (
    <section className="grading-pulse" aria-labelledby="grading-pulse-heading">
      <div className="grading-pulse-heading">
        <div>
          <p className="section-kicker">{t.gradingPulse.eyebrow}</p>
          <h2 id="grading-pulse-heading">{t.gradingPulse.title}</h2>
          <p className="grading-pulse-subtitle">{t.gradingPulse.subtitle} · {windowCaption}</p>
        </div>
      </div>
      <div className="grading-pulse-body">
        <div className="grading-share-bar" role="img" aria-label={t.gradingPulse.share}>
          {stats.map((stat) => (
            <span
              key={stat.grader}
              className={`grading-share-segment grading-${stat.grader.toLowerCase()}`}
              style={{ width: `${total > 0 ? (stat.topGradePop / total) * 100 : 0}%` }}
            />
          ))}
        </div>
        <ul className="grading-pulse-list">
          {stats.map((stat) => {
            const share = total > 0 ? (stat.topGradePop / total) * 100 : 0;
            const delta = stat.change.value ?? 0;
            const deltaCount = Math.round(stat.topGradePop - stat.topGradePop / (1 + delta / 100));
            return (
              <li key={stat.grader} className="grading-pulse-item">
                <span className={`grading-dot grading-${stat.grader.toLowerCase()}`} aria-hidden="true" />
                <span className="grading-name">{t.grader.names[stat.grader]}</span>
                <span className="grading-delta">{deltaCount >= 0 ? "+" : ""}{deltaCount.toLocaleString()}</span>
                <span className={`grading-pct metric-${metricTone(stat.change)}`}>{formatPercent(stat.change, locale)}</span>
                <span className="grading-share-label">{share.toFixed(1)}%</span>
              </li>
            );
          })}
        </ul>
      </div>
    </section>
  );
}
