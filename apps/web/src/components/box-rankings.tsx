"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { BoxImage } from "./box-image";
import { ExploreBar, SortHeader } from "./explore-bar";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { MetricDelta } from "./rankings";
import { copy } from "@/lib/i18n";
import { boxMatchesQuery, nextExploreSort, normaliseBoxSort, sortBoxes } from "@/lib/list-explore";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, Locale, SealedProductView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

const INITIAL_ROWS = 50;

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
  const { period, query, sort, dir, update } = useMarketSettings();
  const t = copy[locale];
  const router = useRouter();
  const [showAll, setShowAll] = useState(false);
  const boxSort = normaliseBoxSort(sort);
  const explored = useMemo(
    () => sortBoxes(products.filter((product) => boxMatchesQuery(product, query, locale)), boxSort, dir, period),
    [boxSort, dir, locale, period, products, query],
  );
  const visibleProducts = showAll ? explored : explored.slice(0, INITIAL_ROWS);
  const applySort = (key: string) => {
    const next = nextExploreSort(boxSort, dir, key);
    update({ sort: next.sort, dir: next.dir });
  };
  const title = t.box.boardTitle.replace("{count}", String(products.length));
  const resultLabel = (query.trim() || explored.length !== products.length)
    ? t.labels.resultCount.replace("{shown}", String(explored.length)).replace("{total}", String(products.length))
    : null;

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
          <ExploreBar
            query={query}
            onQueryChange={(value) => update({ query: value })}
            placeholder={t.labels.searchPlaceholderBox}
            searchLabel={t.labels.searchLabel}
            clearLabel={t.labels.searchClear}
            resultLabel={resultLabel}
            sortKeys={[
              { key: "rank", label: t.labels.rank },
              { key: "price", label: t.labels.priceShort },
              { key: "sold", label: t.box.soldCountShort },
              { key: "release", label: t.box.release },
            ]}
            sort={boxSort}
            dir={dir}
            onSort={applySort}
            highToLow={t.labels.sortHighToLow}
            lowToHigh={t.labels.sortLowToHigh}
          />
          {!explored.length ? <p className="empty-state">{t.labels.noSearchResultsBox}</p> : (
          <>
          <div className="desktop-ranking-table">
            <table>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" />
                <col className="col-price" /><col className="col-sales" /><col className="col-change" /><col className="col-spark" />
              </colgroup>
              <thead><tr>
                <SortHeader label={t.labels.rank} sortKey="rank" activeKey={boxSort} dir={dir} onSort={applySort} />
                <th>{t.box.box}</th>
                <SortHeader label={t.box.release} sortKey="release" activeKey={boxSort} dir={dir} onSort={applySort} />
                <SortHeader label={t.labels.priceShort} sortKey="price" activeKey={boxSort} dir={dir} onSort={applySort} className="numeric" />
                <SortHeader label={`${t.periods[period]} ${t.box.soldCountShort}`} sortKey="sold" activeKey={boxSort} dir={dir} onSort={applySort} className="numeric" />
                <th className="numeric">{t.periods[period]} {t.labels.changeShort}</th>
                <th className="numeric">{t.labels.salesTrendShort}</th>
              </tr></thead>
            <tbody>{visibleProducts.map((product) => {
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
                    <td className="numeric spark-cell"><Sparkline values={product.salesSparkline} label={t.labels.salesTrend} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          <div className="mobile-ranking-list">
            {/* 原盒手機 list：同主站卡片一樣用五欄 grid，趨勢 header 位交俾
                Sparkline 自己 render（數據夠先出），唔會再出「Sal tre」半行字。 */}
            <div className="mobile-list-header box-mobile-list-header" aria-hidden="true">
              <span className="mobile-col-info">{t.box.box}</span>
              <span className="mobile-col-right">{t.labels.priceShort}</span>
              <Sparkline values={[]} label={t.labels.salesTrendShort} />
            </div>
            {visibleProducts.map((product) => {
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
                  <Sparkline values={product.salesSparkline} label={t.labels.salesTrend} />
              </Link>
            );
          })}
          </div>
          {!showAll && explored.length > INITIAL_ROWS && (
            <button
              type="button"
              className="box-show-more"
              onClick={() => setShowAll(true)}
            >
              {t.box.showMore.replace("{count}", String(explored.length - INITIAL_ROWS))}
            </button>
          )}
          </>
          )}
        </>
      )}
    </section>
  );
}
