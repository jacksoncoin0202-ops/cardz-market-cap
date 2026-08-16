"use client";

import { useMemo, useState, useTransition } from "react";
import Link from "next/link";
import { BoxImage } from "./box-image";
import { ExploreBar, SortHeader } from "./explore-bar";
import { PeriodSelector } from "./period-selector";
import { Sparkline } from "./sparkline";
import { MetricDelta, staleClass, staleTitle } from "./rankings";
import { copy } from "@/lib/i18n";
import { tap } from "@/lib/haptic";
import { boxMatchesQuery, nextExploreSort, normaliseBoxSort, sortBoxes } from "@/lib/list-explore";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, Locale, SealedProductView } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

const INITIAL_ROWS = 50;
/* 「顯示更多」每次 +50，唔係一下 mount 幾百行 */
const PAGE_STEP = 50;

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
  const boxSort = normaliseBoxSort(sort);
  const explored = useMemo(
    () => sortBoxes(products.filter((product) => boxMatchesQuery(product, query, locale)), boxSort, dir, period),
    [boxSort, dir, locale, period, products, query],
  );
  const [visibleCount, setVisibleCount] = useState(INITIAL_ROWS);
  const [isPending, startShowMore] = useTransition();
  /* query / sort / dir / period / group（products）一變，explored 就係新 array —— render 期直接
     reset 返 50 行（React「adjust state on prop change」寫法），唔用 effect 免得先 mount 晒成千行再縮。 */
  const [seenExplored, setSeenExplored] = useState(explored);
  if (seenExplored !== explored) {
    setSeenExplored(explored);
    setVisibleCount(INITIAL_ROWS);
  }
  const visibleProducts = explored.length > visibleCount ? explored.slice(0, visibleCount) : explored;
  const remaining = explored.length - visibleProducts.length;
  const applySort = (key: string) => {
    tap.select();
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
              <caption className="sr-only">{title}</caption>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" />
                <col className="col-price" /><col className="col-sales" /><col className="col-change" /><col className="col-spark" />
              </colgroup>
              <thead><tr>
                <SortHeader label={t.labels.rank} sortKey="rank" activeKey={boxSort} dir={dir} onSort={applySort} />
                <th scope="col">{t.box.box}</th>
                <SortHeader label={t.box.release} sortKey="release" activeKey={boxSort} dir={dir} onSort={applySort} />
                <SortHeader label={t.labels.priceShort} sortKey="price" activeKey={boxSort} dir={dir} onSort={applySort} className="numeric" />
                <SortHeader label={`${t.periods[period]} ${t.box.soldCountShort}`} sortKey="sold" activeKey={boxSort} dir={dir} onSort={applySort} className="numeric" />
                <th scope="col" className="numeric">{t.periods[period]} {t.labels.changeShort}</th>
                <th scope="col" className="numeric">{t.labels.salesTrendShort}</th>
              </tr></thead>
            <tbody>{visibleProducts.map((product) => {
                const metrics = product.windows[period];
                const productUrl = href(`/box/${product.id}`);
                const badge = langBadge(product);
                const native = nativeLine(product, locale);
                const unpriced = product.priceUsd.value === null;
                /* 同 rankings.tsx 一樣：真 <a>（.row-link）用 ::after 鋪滿 .rank-row，唔再 role="link" + router.push */
                return (
                  <tr key={product.id} className={`rank-row hover-lift${unpriced ? " box-muted-row" : ""}`}>
                    <td className="rank-cell">{product.rank}</td>
                    <td>
                      <Link href={productUrl} className="row-link">
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
                      <span className={staleClass(product.priceUsd, "price-now")} title={staleTitle(product.priceUsd, locale)}>{formatMetricMoney(product.priceUsd, currency, rates, locale)}</span>
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
          {remaining > 0 && (
            <button
              type="button"
              className="box-show-more"
              disabled={isPending}
              aria-busy={isPending}
              onClick={() => startShowMore(() => setVisibleCount((count) => count + PAGE_STEP))}
            >
              {t.box.showMore.replace("{count}", String(remaining))}
            </button>
          )}
          </>
          )}
        </>
      )}
    </section>
  );
}
