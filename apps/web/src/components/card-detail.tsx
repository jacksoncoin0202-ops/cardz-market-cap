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
import { formatMetricInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { plainDescription } from "@/lib/plain-text";
import { type MarketViewSnapshot } from "@/lib/types";
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
        name: card.officialName || t.status.unavailable,
        identifier: card.collectorNumber,
        image: absolutePublicUrl(card.image.url),
        /*
         * JSON-LD 唔經 `marketMetadata`，所以要喺呢度自己 normalise 多一次。
         * 呢個 `description` 同 `<meta>` 嗰個係同一篇故事、同一個消毒規矩，
         * 唯獨走另一條路出街 —— 漏咗呢句就得 schema.org 嗰邊仲係生 markdown。
         */
        description: plainDescription(story ?? "") || undefined,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: t.nav.all, item: absolutePublicUrl(href("/")) },
          { "@type": "ListItem", position: 2, name: card.officialName || t.status.unavailable },
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
          <CardImage image={card.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={card.officialName ?? ""} />
        </section>
        <div className="detail-content">
          <header className="detail-header">
            <p className="section-kicker">{card.tcg}</p>
            <h1>{card.officialName || t.status.unavailable}</h1>
            <p className="detail-set">{card.setName[locale] || t.status.unavailable}</p>
            {/* 印刷版本逐條併入現有 identity list：冇值嘅欄根本唔會回，
                所以完全冇資料嗰陣呢個 dl 同以前一模一樣。
                owner 2026-08-02：語言版本＋卡包來源喺內頁出齊（DETAIL_PRINT_FIELDS 包
                editionCode），唔再出 badge —— 欄位先係佢要嘅形式。 */}
            <dl className="identity-list">
              <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
              {printIdentityRows(card, locale, DETAIL_PRINT_FIELDS).map((row) => (
                <div key={row.key} data-field={row.key}><dt>{row.label}</dt><dd title={row.value}>{row.value}</dd></div>
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
            <div><span>{t.periods[period]} {t.labels.change}</span><strong className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</strong>{windowMetric.changePct.sourceSwitched && <small className="muted-copy">Historical anchor: {windowMetric.changePct.priceAnchorSource}</small>}</div>
            <div className="wide-metric"><span title={t.labels.salesHelp}>{t.periods[period]} {t.labels.trackedSales}</span><strong className="metric-value-fit">{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</strong><MetricDelta metric={windowMetric.trackedSales.valueUsd} changePct={windowMetric.trackedSalesChangePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
            <div><span>{t.labels.ungradedReference}</span><strong>{formatMetricMoney(card.priceUngradedReference, currency, snapshot.rates, locale)}</strong></div>
          </section>
          {/*
            「資料時間」講嘅係上面嗰堆數幾時嘅，唔係個 snapshot 幾時 bake。
            原本行 `snapshot.effectiveAt || card.pricePsa10.asOf`，而 effectiveAt 永遠有值，
            所以第二項係死 code，逐張卡都畫緊 generation 時間。實測 1286 張出街卡入面
            949 張（73.8%）個真實價格日期比 generation 早 8 日以上，最誇張嗰張
            （rk1231）價格係 2026-03-27，個頁面照寫「Data time: Aug 10, 2026」——
            差 136 日。市值 = 價 × POP，所以呢個日期一錯，成塊 metrics 都報錯時間。
            改用卡自己嗰個價格觀察日；冇價先跌返 snapshot 時間。
          */}
          <p className="data-time">{t.labels.asOf}: {formatObservationDate(card.pricePsa10.asOf || snapshot.effectiveAt, locale)}</p>
          <HistoryChart points={card.historyDaily} locale={locale} currency={currency} rates={snapshot.rates} />
        </div>
      </article>
    </div>
  );
}
