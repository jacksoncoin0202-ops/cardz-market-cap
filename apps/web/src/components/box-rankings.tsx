"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { BoxImage } from "./box-image";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { MetricDelta } from "./rankings";
import { copy } from "@/lib/i18n";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, Locale, SealedProductView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

function langBadge(product: SealedProductView): { className: string; label: string } {
  return product.lang === "jp"
    ? { className: "print-badge print-badge--ja print-badge--compact", label: "JP" }
    : { className: "print-badge print-badge--en print-badge--compact", label: "EN" };
}

function nativeLine(product: SealedProductView, locale: Locale): string | null {
  const native = product.priceNative;
  if (!native || native.currency !== "JPY") return null;
  return `¥${Math.round(native.amount).toLocaleString(locale === "en" ? "en-US" : "ja-JP")}`;
}

export function BoxRankings({ products, rates, locale, currency, href }: {
  products: SealedProductView[];
  rates: Record<Currency, number>;
  locale: Locale;
  currency: Currency;
  href: (path: string) => string;
}) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const router = useRouter();
  const title = t.box.boardTitle.replace("{count}", String(products.length));

  return (
    <section className="rankings-section" id="box-ranking" aria-labelledby="box-ranking-heading">
      <div className="ranking-heading">
        <div>
          <p className="section-kicker">{t.nav.box}</p>
          <h2 id="box-ranking-heading">{title}</h2>
        </div>
        <PeriodSelector compact />
      </div>
      {!products.length ? <p className="empty-state">{t.box.empty}</p> : (
        <>
          <div className="desktop-ranking-table">
            <table>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" />
                <col className="col-price" /><col className="col-sales" /><col className="col-change" /><col className="col-spark" />
              </colgroup>
              <thead><tr>
                <th>{t.labels.rank}</th>
                <th>{t.box.box}</th>
                <th>{t.box.release}</th>
                <th className="numeric">{t.labels.priceShort}</th>
                <th className="numeric">{t.periods[period]} {t.box.soldCountShort}</th>
                <th className="numeric">{t.periods[period]} {t.labels.changeShort}</th>
                <th className="numeric">{t.labels.salesTrendShort}</th>
              </tr></thead>
              <tbody>{products.map((product) => {
                const metrics = product.windows[period];
                const productUrl = href(`/box/${product.id}`);
                const badge = langBadge(product);
                const native = nativeLine(product, locale);
                const unpriced = product.priceUsd.value === null;
                return (
                  <tr
                    key={product.id}
                    className={`ranking-row-link hover-lift${unpriced ? " box-muted-row" : ""}`}
                    role="link"
                    tabIndex={0}
                    aria-label={`#${product.rank} ${product.name[locale] || product.name.en}`}
                    onClick={(event) => {
                      if ((event.target as HTMLElement).closest("a,button")) return;
                      router.push(productUrl);
                    }}
                    onKeyDown={(event) => {
                      if (event.key !== "Enter" && event.key !== " ") return;
                      if ((event.target as HTMLElement).closest("a,button")) return;
                      event.preventDefault();
                      router.push(productUrl);
                    }}
                  >
                    <td className="rank-cell">{product.rank}</td>
                    <td>
                      <Link href={productUrl}>
                        <div className="ranking-card-identity">
                          <div className="ranking-thumb box-thumb"><BoxImage image={product.image} sizes="56px" alt="" /></div>
                          <div className="ranking-name">
                            <strong>{product.name[locale] || product.name.en}</strong>
                            <span className="box-sub-row">
                              <span className="box-set-chip">{product.setCode}</span>
                              <span className={badge.className}>{badge.label}</span>
                              {product.status === "unreleased" && <span className="box-status-chip">{t.box.unreleased}</span>}
                            </span>
                          </div>
                        </div>
                      </Link>
                    </td>
                    <td className="collector-cell">{product.release ?? "—"}</td>
                    <td className="numeric price-cell">
                      <span className="price-now">{formatMetricMoney(product.priceUsd, currency, rates, locale)}</span>
                      {native && <span className="box-price-native">{native}</span>}
                      <MetricDelta metric={product.priceUsd} changePct={metrics.changePct} currency={currency} rates={rates} locale={locale} />
                    </td>
                    <td className="numeric">{metrics.soldCount > 0 ? formatInteger(metrics.soldCount, locale) : "—"}</td>
                    <td className={`numeric metric-${metricTone(metrics.changePct)}`}>{formatPercent(metrics.changePct, locale)}</td>
                    <td className="numeric spark-cell"><Sparkline values={product.historyDaily.map((point) => point.trackedSalesValueUsd).filter((value): value is number => typeof value === "number" && Number.isFinite(value))} label={t.labels.salesTrend} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="mobile-ranking-list">
            <div className="mobile-list-header" aria-hidden="true">
              <span className="mobile-col-info">{t.box.box}</span>
              <span className="mobile-col-right">{t.labels.priceShort}</span>
              <span className="mobile-col-spark">{t.labels.salesTrendShort}</span>
            </div>
            {products.map((product) => {
              const metrics = product.windows[period];
              const tone = metricTone(metrics.changePct);
              return (
                <Link className="mobile-rank-card" href={href(`/box/${product.id}`)} key={product.id}>
                  <span className="mobile-rank-index">{product.rank}</span>
                  <div className="ranking-thumb box-thumb"><BoxImage image={product.image} sizes="56px" alt="" /></div>
                  <div className="mobile-card-info">
                    <span className="mobile-card-sub"><span className="mobile-card-number">{product.setCode}</span></span>
                    <strong className="mobile-card-name">{product.name[locale] || product.name.en}</strong>
                  </div>
                  <div className="mobile-card-right">
                    <span className="mobile-card-price">{formatMetricMoney(product.priceUsd, currency, rates, locale)}</span>
                    <span className={`mobile-change-badge metric-${tone}`}>{formatPercent(metrics.changePct, locale)}</span>
                  </div>
                  <Sparkline values={product.historyDaily.map((point) => point.trackedSalesValueUsd).filter((value): value is number => typeof value === "number" && Number.isFinite(value))} label={t.labels.salesTrend} />
                </Link>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}
