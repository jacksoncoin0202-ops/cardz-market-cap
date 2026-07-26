"use client";

import Link from "next/link";
import { CardImage } from "./card-image";
import { GraderShareDonut } from "./grader-share-donut";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { copy } from "@/lib/i18n";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import { displayTopGrade } from "@/lib/grade-label";
import { graders, type Grader, type MarketMetric, type MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

function populationText(metric: MarketMetric<number>, locale: keyof typeof copy): string {
  const t = copy[locale];
  if (metric.value === null) return t.status[metric.status === "ready" ? "unavailable" : metric.status];
  const value = formatInteger(metric.value, locale);
  return value;
}

interface GraderPageProps {
  grader: Grader;
  snapshot: MarketViewSnapshot;
  /** 全市場總數，五個版面一致。分母唔可以用篩完嘅 snapshot 計。 */
  shareTotals: Record<Grader, number>;
  /** 真係有卡入到榜嘅 grader，由 snapshot 推導。 */
  availableGraders: Grader[];
}

export function GraderPage({ grader, snapshot, shareTotals, availableGraders }: GraderPageProps) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];
  const title = t.grader.title.replace("{grader}", t.grader.names[grader]);
  const isEmpty = snapshot.top100.length === 0;
  /*
   * 只連去真係有數據嘅 grader；當前身處嘅 grader 一定保留，
   * 唔係直接開 /graders/tag 就會見到五個 tab 冇一個 active。
   */
  const tabs = graders.filter((item) => item === grader || availableGraders.includes(item));
  return (
    <div className="page-shell grader-page" data-cardz-generation={snapshot.generation}>
      <section className="grader-hero">
        <p className="section-kicker">{t.grader.eyebrow}</p>
        <h1>{title}</h1>
        <p>{t.grader.body}</p>
        <nav className="grader-tabs" aria-label={t.nav.graders}>
          {tabs.map((item) => (
            <Link key={item} href={href(`/graders/${item.toLowerCase()}`)} data-active={item === grader ? "true" : "false"}>{t.grader.names[item]}</Link>
          ))}
        </nav>
      </section>
      <GraderShareDonut totals={shareTotals} locale={locale} />
      <section className="grader-ranking" aria-labelledby="grader-ranking-heading">
        <div className="ranking-heading">
          <div>
            <h2 id="grader-ranking-heading">{t.grader.topGradePopulation}</h2>
            <p>{grader === "PSA" ? t.grader.marketCapAvailable : t.grader.marketCapUnavailable}</p>
          </div>
          <PeriodSelector compact />
        </div>
        {/* 得一個 empty state。之前 desktop 同 mobile 各出一次，同一句喺 DOM 出現兩次。 */}
        {isEmpty && <p className="empty-state">{t.labels.noCards}</p>}
        {!isEmpty && (
        <>
        <div className="desktop-ranking-table grader-table">
          <table>
            <colgroup><col className="col-rank" /><col className="col-card" /><col className="col-number" /><col className="col-grade" /><col className="col-pop" /><col className="col-total" /><col className="col-change" /><col className="col-cap" /></colgroup>
            <thead><tr>
              <th>{t.labels.rank}</th><th>{t.labels.card}</th><th>{t.labels.number}</th><th>{t.grader.topGradeShort}</th>
              <th className="numeric">{t.grader.topGradePopulationShort}</th><th className="numeric">{t.grader.totalPopulationShort}</th>
              <th className="numeric">{t.periods[period]} {t.grader.populationChangeShort}</th><th className="numeric">{t.labels.marketCapShort}</th>
            </tr></thead>
            <tbody>{snapshot.top100.map((card) => {
              const population = card.graderPopulations[grader];
              return (
                <tr key={card.id}>
                  <td className="rank-cell">{card.rank}</td>
                  <td><Link href={href(`/card/${card.id}`)}><span className="grader-card"><span className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></span><strong>{card.name[locale] || t.status.unavailable}</strong></span></Link></td>
                  <td className="collector-cell">{card.collectorNumber}</td>
                  <td>{displayTopGrade(grader, population.topGrade) || t.status.unavailable}</td>
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
          <div className="mobile-list-header" aria-hidden="true">
            <span className="mobile-col-info">{t.labels.card}</span>
            <span className="mobile-col-right">{t.labels.priceShort}</span>
            <span className="mobile-col-spark">{t.labels.salesTrendShort}</span>
          </div>
          {snapshot.top100.map((card) => {
            const population = card.graderPopulations[grader];
            const price = grader === "PSA" ? card.pricePsa10 : card.marketCap;
            const change = card.windows[period].changePct;
            const tone = metricTone(change);
            return (
              <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                <span className="mobile-rank-index">{card.rank}</span>
                <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></div>
                <div className="mobile-card-info">
                  <span className="mobile-card-number">{card.collectorNumber}</span>
                  <strong className="mobile-card-name">{card.name[locale] || t.status.unavailable}</strong>
                  <span className="mobile-card-sub">
                    <span className="mobile-card-cap">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                  </span>
                  <span className="mobile-card-pop">{displayTopGrade(grader, population.topGrade) || t.grader.topGrade} · {populationText(population.topGradePopulation, locale)}</span>
                </div>
                <div className="mobile-card-right">
                  <span className="mobile-card-price">{formatMetricMoney(price, currency, snapshot.rates, locale)}</span>
                  <span className={`mobile-change-badge metric-${tone}`}>{formatPercent(change, locale)}</span>
                </div>
                <Sparkline points={card.historyDaily} label={t.labels.salesTrend} />
              </Link>
            );
          })}
        </div>
        </>
        )}
      </section>
    </div>
  );
}
