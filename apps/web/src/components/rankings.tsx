"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo } from "react";
import { ArrowDown, ArrowUp, TrendingDown, TrendingUp } from "lucide-react";
import { CardImage } from "./card-image";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { Tooltip } from "./tooltip";
import { cardLanguages, copy, localizedCardLanguage } from "@/lib/i18n";
import { formatDeltaMoney, formatInteger, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { useMarketSettings, type PrintLangFilter } from "@/lib/use-market-settings";
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
  const { period, printLang, update } = useMarketSettings();
  const t = copy[locale];
  const router = useRouter();
  /* 篩選只列出榜上真係有嘅印刷語言。dev seed 帶 legacy key `language`，
     mapper 出 null，所以 dev 冇語言、冇 filter —— 呢個係正確行為。 */
  const availableLanguages = useMemo(() => {
    const seen = new Set<string>();
    for (const card of cards) if (card.cardLanguage) seen.add(card.cardLanguage);
    return cardLanguages.filter((lang) => seen.has(lang));
  }, [cards]);
  /* URL 揀咗個榜上冇嘅語言就當冇篩，唔准出空榜。 */
  const activeLang: PrintLangFilter =
    printLang !== "all" && availableLanguages.includes(printLang) ? printLang : "all";
  /* 篩選淨係隱藏行：viewRank / marketRank 照原樣出，唔准重新編號。 */
  const visibleCards = useMemo(
    () => (activeLang === "all" ? cards : cards.filter((card) => card.cardLanguage === activeLang)),
    [cards, activeLang],
  );
  const rankingTitle = t.heatmap.rankingTitle.replace("{count}", String(visibleCards.length));
  return (
    <section className="rankings-section" id="market-ranking" aria-labelledby="ranking-heading">
      <div className="ranking-heading">
        <div>
          <p className="section-kicker">{watchlist ? t.labels.watchStatus : marketLabel ?? t.nav.all}</p>
          <h2 id="ranking-heading">{watchlist ? t.nav.watchlist : rankingTitle}</h2>
          {availableLanguages.length > 1 && (
            <div className="lang-filter" role="group" aria-label={t.labels.language}>
              {(["all", ...availableLanguages] as PrintLangFilter[]).map((lang) => (
                <button
                  key={lang}
                  type="button"
                  aria-pressed={activeLang === lang}
                  onClick={() => update({ printLang: lang })}
                >
                  {activeLang === lang && <span className="lang-filter-pill" aria-hidden="true" />}
                  <span>{lang === "all" ? t.labels.languageFilterAll : localizedCardLanguage(lang, locale)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <PeriodSelector compact />
      </div>
      {!visibleCards.length ? <p className="empty-state">{t.labels.noCards}</p> : (
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
              <tbody>{visibleCards.map((card) => {
                const metrics = card.windows[period];
                const cardUrl = href(`/card/${card.id}`);
                return (
                  <tr
                    key={card.id}
                    className="ranking-row-link"
                    role="link"
                    tabIndex={0}
                    aria-label={`#${card.viewRank} ${card.name[locale] || t.status.unavailable}`}
                    onClick={(event) => {
                      // 入面嘅 <a>/<button>（卡名、tooltip）自己處理，唔好 double navigate
                      if ((event.target as HTMLElement).closest("a,button")) return;
                      router.push(cardUrl);
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      if ((event.target as HTMLElement).closest("a,button")) return;
                      event.preventDefault();
                      router.push(cardUrl);
                    }}
                  >
                    <td className="rank-cell">{card.viewRank}</td>
                    <td><Link href={cardUrl}><CardIdentity card={card} locale={locale} unavailable={t.status.unavailable} /></Link></td>
                    <td className="collector-cell">{card.collectorNumber}</td>
                    <td className="numeric price-cell">
                      <span className="price-now">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                      <PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className="numeric">
                      <span className="pop-now">{formatMetricInteger(card.populationPsa10, locale)}</span>
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
            {visibleCards.map((card) => (
              <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                <span className="mobile-rank-index">{card.viewRank}</span>
                <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></div>
                <div className="mobile-card-info">
                  <span className="mobile-card-sub">
                    <span className="mobile-card-number">{card.collectorNumber}</span>
                  </span>
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
