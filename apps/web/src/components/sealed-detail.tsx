"use client";

import Link from "next/link";
import { CapTicker } from "./cap-ticker";
import { CopyButton } from "./copy-button";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { MetricDelta } from "./rankings";
import { SealedBoxImage } from "./sealed-image";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatDate, formatInteger, formatMetricMoney, formatMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, MarketViewSnapshot, SealedProductView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

/* Sealed (原盒) detail — clone of CardDetail's skeleton. Box art keeps its
   original aspect: contain-fit, no crop, no glow. */
export function SealedDetail({ product, snapshot }: {
  product: SealedProductView | null;
  snapshot: MarketViewSnapshot;
}) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];

  if (!product) {
    return (
      <div className="page-shell"><section className="empty-detail">
        <p>{t.sealed.empty}</p>
        <Link className="primary-action" href={href("/box")}>{t.nav.sealed}</Link>
      </section></div>
    );
  }

  const rates: Record<Currency, number> = snapshot.rates;
  const metrics = product.windows[period];
  const story = product.story ? (product.story[locale] || product.story.en) : null;
  const gameLabel = product.game === "optcg" ? "One Piece" : "Pokémon";
  const kicker = `${t.nav.sealed} · ${gameLabel} ${product.lang.toUpperCase()}`;
  const priceLabel = product.priceKind ? t.sealed.priceKind[product.priceKind] : t.labels.priceShort;
  const native = product.priceNative && product.priceNative.currency === "JPY"
    ? `¥${Math.round(product.priceNative.amount).toLocaleString(locale === "en" ? "en-US" : "ja-JP")}`
    : null;
  const askNative = product.askFloorNative && product.askFloorNative.currency === "JPY"
    ? `¥${Math.round(product.askFloorNative.amount).toLocaleString(locale === "en" ? "en-US" : "ja-JP")}`
    : null;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Product",
        name: product.name[locale] || product.name.en,
        identifier: product.setCode,
        image: absolutePublicUrl(product.image.url),
        description: story || undefined,
        releaseDate: product.release || undefined,
      },
      {
        "@type": "BreadcrumbList",
        itemListElement: [
          { "@type": "ListItem", position: 1, name: t.nav.sealed, item: absolutePublicUrl(href("/box")) },
          { "@type": "ListItem", position: 2, name: product.name[locale] || product.name.en },
        ],
      },
    ],
  };

  return (
    <div className="page-shell detail-page">
      <StructuredData value={structuredData} />
      <div className="detail-actions fade-up">
        <Link className="back-link" href={href("/box")}>← {t.nav.sealed}</Link>
        <CopyButton getText={() => `${window.location.origin}/box/${product.id}`} label={t.labels.share} doneLabel={t.labels.shareDone} errorLabel={t.labels.shareError} preferNativeShare />
      </div>
      <article className="detail-grid fade-up">
        <section className="detail-art sealed-detail-art" aria-label={product.name.en}>
          <span className="detail-rank">#{product.rank}</span>
          <SealedBoxImage image={product.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={product.name[locale] || product.name.en} />
        </section>
        <div className="detail-content">
          <header className="detail-header">
            <p className="section-kicker">{kicker}</p>
            <h1>{product.name[locale] || product.name.en}</h1>
            {product.name.ja && product.name.ja !== product.name.en && (
              <p className="detail-set">{product.name.ja}</p>
            )}
            <dl className="identity-list">
              {product.fullName && (
                <div><dt>{t.sealed.fullName}</dt><dd>{product.fullName[locale] || product.fullName.en}</dd></div>
              )}
              <div><dt>{t.sealed.setCode}</dt><dd>{product.setCode}</dd></div>
              {product.release && <div><dt>{t.sealed.release}</dt><dd>{product.release}</dd></div>}
              {product.packsPerBox > 0 && <div><dt>{t.sealed.packs}</dt><dd>{formatInteger(product.packsPerBox, locale)}</dd></div>}
              {product.printWave !== "std" && <div><dt>{t.sealed.print}</dt><dd>{product.printWave}</dd></div>}
              {product.status === "unreleased" && <div><dt>{t.labels.asOf}</dt><dd>{t.sealed.unreleased}</dd></div>}
            </dl>
          </header>
          {story && (
            <section className="story-panel">
              <h2>{t.labels.story}</h2>
              <p>{story}</p>
            </section>
          )}
          <div className="detail-period-row"><PeriodSelector compact /></div>
          {/* 5 tiles 保持齊格：窄屏 2 欄時 ask 波幅舖滿成條，唔會剩單格。
              wide-metric 喺 ≥980px 嘅 4 欄下 span 2，同主站卡頁 trackedSales 行為一致。 */}
          <section className="detail-metrics sealed-detail-metrics" aria-label={priceLabel}>
            <div>
              <span>{priceLabel}</span>
              <strong className="metric-value-fit">
                {product.priceUsd.value === null
                  ? formatMetricMoney(product.priceUsd, currency, rates, locale)
                  : <CapTicker key={product.priceUsd.value} value={product.priceUsd.value} format={(n) => formatMoney(n, currency, rates, locale)} />}
              </strong>
              {native && <span className="sealed-price-native">{native}</span>}
              <MetricDelta metric={product.priceUsd} changePct={metrics.changePct} currency={currency} rates={rates} locale={locale} />
            </div>
            <div>
              <span>{t.periods[period]} {t.labels.change}</span>
              <strong className={`metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</strong>
            </div>
            <div>
              <span>{t.periods[period]} {t.sealed.soldCountShort}</span>
              <strong>{metrics.soldCount > 0 ? formatInteger(metrics.soldCount, locale) : "—"}</strong>
            </div>
            {product.askFloorUsd.value !== null && (
              <div className="wide-metric">
                <span>{t.sealed.askFloor}</span>
                <strong>{formatMetricMoney(product.askFloorUsd, currency, rates, locale)}</strong>
                {askNative && <span className="sealed-price-native">{askNative}</span>}
              </div>
            )}
          </section>
          <p className="data-time">{t.labels.asOf}: {formatDate(product.priceUsd.asOf ?? snapshot.effectiveAt, locale)}</p>
          <HistoryChart points={product.historyDaily} locale={locale} currency={currency} rates={rates} />
        </div>
      </article>
    </div>
  );
}
