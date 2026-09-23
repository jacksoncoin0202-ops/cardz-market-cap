"use client";

import Link from "next/link";
import { Breadcrumbs } from "./breadcrumbs";
import { CapTicker } from "./cap-ticker";
import { CopyButton } from "./copy-button";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { Provenance } from "./provenance";
import { MetricDelta, staleClass, staleTitle } from "./rankings";
import { BoxImage } from "./box-image";
import { tcgOfGroup } from "./box-group-selector";
import { absolutePublicUrl, canonicalPublicUrl, datasetId, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, metricTone } from "@/lib/format";
import { plainDescription } from "@/lib/plain-text";
import { appendParam, fillTemplate, geoCopy } from "@/lib/related-cards";
import type { Currency, MarketViewSnapshot, SealedProductView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";
import { StoryPanel } from "./story-panel";
import "@/app/styles/card-links.css";

export function BoxDetail({ product, snapshot }: {
  product: SealedProductView | null;
  snapshot: MarketViewSnapshot;
}) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];

  if (!product) {
    return (
      <div className="page-shell"><section className="empty-detail">
        <p>{t.box.empty}</p>
        <Link className="primary-action" href={href("/box")}>{t.nav.box}</Link>
      </section></div>
    );
  }

  const rates: Record<Currency, number> = snapshot.rates;
  const metrics = product.windows[period];
  const story = product.story?.[locale] || null;
  const gameLabel = product.game === "optcg" ? t.nav.onePiece : t.nav.pokemon;
  const kicker = `${t.nav.box} · ${gameLabel} ${product.lang.toUpperCase()}`;
  /* print_wave 代號出 label；冇對應（新代號）就照出 raw，唔好靜靜地食咗 */
  const printWaveLabel = (t.box.printWaves as Record<string, string>)[product.printWave] ?? product.printWave;
  const priceLabel = product.priceKind ? t.box.priceKind[product.priceKind] : t.labels.priceShort;
  const native = product.priceNative && product.priceNative.currency === "JPY"
    ? `¥${Math.round(product.priceNative.amount).toLocaleString(locale === "en" ? "en-US" : "ja-JP")}`
    : null;
  const askNative = product.askFloorNative && product.askFloorNative.currency === "JPY"
    ? `¥${Math.round(product.askFloorNative.amount).toLocaleString(locale === "en" ? "en-US" : "ja-JP")}`
    : null;
  const geo = geoCopy[locale];
  const boxName = product.name[locale] || product.name.en;
  const boxUrl = canonicalPublicUrl(`/box/${product.id}`);
  const factDate = formatObservationDate(product.priceUsd.asOf ?? snapshot.effectiveAt, locale);
  /*
   * GEO（owner 2026-08-16）：BOX 頁一句可引用事實——名、參考價、價種、日期齊。
   * 冇價唔准靜靜地隱形：出「追蹤中但未有參考價」嗰句，明講缺口係缺口。
   * 日期行 formatObservationDate（釘死 UTC、淨出日期），唔行 formatDate ——
   * 呢句係 SSR + hydrate 都要逐個字一樣嘅文字。
   */
  const boxFact = product.priceUsd.value !== null
    ? fillTemplate(geo.boxDetailFact, {
      name: boxName,
      price: formatMoney(product.priceUsd.value, currency, rates, locale),
      priceKind: priceLabel,
      date: factDate,
    })
    : fillTemplate(geo.boxDetailFactNoPrice, { name: boxName, date: factDate });
  /*
   * offers 只喺真係有「而家可以買到嘅價」先出：ask floor（最低要價）永遠算，
   * priceKind 係 market/ask 嗰陣 priceUsd 都算，兩者取低。priceKind = sold 而又
   * 冇 ask floor 就唔出 —— 一單已經完成嘅成交唔係 offer，攞去當 offer 就係作。
   * 型別用 AggregateOffer：我哋報嘅係全市場最低要價，唔係某一個賣家嘅單一報價。
   */
  const offerCandidates = [
    product.askFloorUsd.value,
    product.priceKind === "market" || product.priceKind === "ask" ? product.priceUsd.value : null,
  ].filter((value): value is number => value !== null && Number.isFinite(value) && value > 0);
  const offerPrice = offerCandidates.length ? Math.min(...offerCandidates) : null;
  const offers = offerPrice !== null
    ? {
      "@type": "AggregateOffer",
      lowPrice: offerPrice,
      priceCurrency: "USD",
      availability: product.status === "unreleased"
        ? "https://schema.org/PreOrder"
        : "https://schema.org/InStock",
      url: boxUrl,
    }
    : undefined;
  const boxAdditionalProperty = [
    product.priceUsd.value !== null
      ? { "@type": "PropertyValue", name: "referencePriceUsd", value: product.priceUsd.value, unitText: "USD" }
      : null,
    product.priceKind ? { "@type": "PropertyValue", name: "priceKind", value: product.priceKind } : null,
    product.askFloorUsd.value !== null
      ? { "@type": "PropertyValue", name: "askFloorUsd", value: product.askFloorUsd.value, unitText: "USD" }
      : null,
    product.packsPerBox > 0
      ? { "@type": "PropertyValue", name: "packsPerBox", value: product.packsPerBox, unitText: "count" }
      : null,
    product.printWave ? { "@type": "PropertyValue", name: "printWave", value: product.printWave } : null,
    product.priceUsd.asOf ? { "@type": "PropertyValue", name: "asOf", value: product.priceUsd.asOf } : null,
  ].filter((entry) => entry !== null);
  const crumbs = [
    { label: t.nav.all, href: href("/") },
    { label: t.nav.box, href: href("/box") },
    /* 麵包屑跟返 /box 上面嗰排掣：只到 TCG 一層（語言喺榜度篩），唔好指去一個唔存在嘅 group 值。 */
    { label: t.box.groups[tcgOfGroup(product.group)], href: appendParam(href("/box"), "group", tcgOfGroup(product.group)) },
    { label: boxName },
  ];
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      /* BreadcrumbList 由 <Breadcrumbs> 出（同睇得見嗰條同一份資料），呢度唔再出。 */
      {
        "@type": "Product",
        "@id": `${boxUrl}#product`,
        url: boxUrl,
        name: boxName,
        alternateName: product.name.en && product.name.en !== boxName ? product.name.en : undefined,
        identifier: product.setCode,
        sku: product.setCode || undefined,
        image: absolutePublicUrl(product.image.url),
        brand: { "@type": "Brand", name: product.game === "optcg" ? "One Piece Card Game" : "Pokémon" },
        /* 故事係 markdown，schema 係純文字 attribute —— 同卡頁行同一個消毒 helper。 */
        description: plainDescription(story ?? "") || boxFact,
        releaseDate: product.release || undefined,
        additionalProperty: boxAdditionalProperty,
        offers,
        /* 完整 Dataset 只喺 hub 定義；詳情頁純粹用 @id 引用，避免被當成欠欄位嘅 Dataset。 */
        subjectOf: { "@id": datasetId() },
      },
    ],
  };

  return (
    <div className="page-shell detail-page">
      <StructuredData value={structuredData} />
      <Breadcrumbs items={crumbs} label={geo.breadcrumbLabel} />
      <div className="detail-actions fade-up">
        <Link className="back-link" href={href("/box")}>← {t.nav.box}</Link>
        <CopyButton getText={() => `${window.location.origin}/box/${product.id}`} label={t.labels.share} doneLabel={t.labels.shareDone} errorLabel={t.labels.shareError} preferNativeShare />
      </div>
      <article className="detail-grid fade-up">
        <section className="detail-art box-detail-art" aria-label={product.name.en}>
          <span className="detail-rank">#{product.rank}</span>
          <BoxImage image={product.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={product.name[locale] || product.name.en} />
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
                <div><dt>{t.box.fullName}</dt><dd>{product.fullName[locale] || product.fullName.en}</dd></div>
              )}
              <div><dt>{t.box.setCode}</dt><dd>{product.setCode}</dd></div>
              {/* release 係 DATE 字串：同卡頁一樣 pin UTC 淨出日期，唔出 raw ISO */}
              {product.release && <div><dt>{t.box.release}</dt><dd>{formatObservationDate(product.release, locale)}</dd></div>}
              {product.packsPerBox > 0 && <div><dt>{t.box.packs}</dt><dd>{formatInteger(product.packsPerBox, locale)}</dd></div>}
              {product.printWave !== "std" && <div><dt>{t.box.print}</dt><dd>{printWaveLabel}</dd></div>}
              {product.status === "unreleased" && <div><dt>{t.labels.asOf}</dt><dd>{t.box.unreleased}</dd></div>}
            </dl>
          </header>
          <StoryPanel title={t.labels.story} story={story} />
          <div className="detail-period-row"><PeriodSelector compact /></div>
          <section className="detail-metrics box-detail-metrics" aria-label={priceLabel}>
            <div>
              <span>{priceLabel}</span>
              <strong className={staleClass(product.priceUsd, "metric-value-fit")} title={staleTitle(product.priceUsd, locale)}>
                {product.priceUsd.value === null
                  ? formatMetricMoney(product.priceUsd, currency, rates, locale)
                  : <CapTicker key={product.priceUsd.value} value={product.priceUsd.value} format={(n) => formatMoney(n, currency, rates, locale)} />}
              </strong>
              {native && <span className="box-price-native">{native}</span>}
              <MetricDelta metric={product.priceUsd} changePct={metrics.changePct} currency={currency} rates={rates} locale={locale} />
            </div>
            <div>
              <span>{t.periods[period]} {t.labels.change}</span>
              <strong className={`metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</strong>
            </div>
            <div>
              <span>{t.periods[period]} {t.box.soldCountShort}</span>
              <strong>{metrics.soldCount > 0 ? formatInteger(metrics.soldCount, locale) : "—"}</strong>
            </div>
            {product.askFloorUsd.value !== null && (
              <div className="wide-metric">
                <span>{t.box.askFloor}</span>
                <strong>{formatMetricMoney(product.askFloorUsd, currency, rates, locale)}</strong>
                {askNative && <span className="box-price-native">{askNative}</span>}
              </div>
            )}
          </section>
          <p className="data-time">{t.labels.asOf}: {formatObservationDate(product.priceUsd.asOf ?? snapshot.effectiveAt, locale)}</p>
          <HistoryChart points={product.historyDaily} locale={locale} currency={currency} rates={rates} />
        </div>
      </article>
      <Provenance updatedAt={product.priceUsd.asOf ?? snapshot.sealed?.asOf ?? snapshot.effectiveAt} kind="box" />
    </div>
  );
}
