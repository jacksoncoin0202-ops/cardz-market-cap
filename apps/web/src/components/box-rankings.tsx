"use client";

import { useEffect, useMemo, useRef, useState, useTransition } from "react";
import Link from "next/link";
import { PackageOpen } from "lucide-react";
import { BoxImage } from "./box-image";
import { EmptyState } from "./empty-state";
import { ExploreBar, SortHeader } from "./explore-bar";
import { PeriodMenu, PeriodSelector } from "./period-selector";
import { SortFilterSheet } from "./sort-filter-sheet";
import { Sparkline } from "./sparkline";
import { MetricDelta, MOBILE_BAR_QUERY, staleClass, staleTitle } from "./rankings";
import { copy, localizedCardLanguage, localizedCardLanguageShort } from "@/lib/i18n";
import { tap } from "@/lib/haptic";
import { boxMatchesQuery, nextExploreSort, normaliseBoxSort, sortBoxes } from "@/lib/list-explore";
import { formatInteger, formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import { DEFAULT_RANKING_PAGE_SIZE } from "@/lib/pagination";
import type { Currency, Locale, PrintLanguage, SealedProductView } from "@/lib/types";
import { useMarketSettings, type PrintLangFilter } from "@/lib/use-market-settings";
import { useMediaQuery } from "@/lib/use-media-query";
import { useRankingHeadingHeight } from "@/lib/use-ranking-heading-height";

const INITIAL_ROWS = 50;
/* 「顯示更多」每次 +50，唔係一下 mount 幾百行 */
const PAGE_STEP = 50;

function langBadge(product: SealedProductView): { className: string; label: string } {
  return product.lang === "jp"
    ? { className: "print-badge print-badge--ja print-badge--compact", label: "JP" }
    : { className: "print-badge print-badge--en print-badge--compact", label: "EN" };
}

/* 原盒資料嘅 `lang` 係 "en" | "jp"，但語言篩用嘅係卡榜嗰套 PrintLanguage（日文係 "ja"）。
   兩邊要對得返，先至可以共用同一個 ?printLang= param 同同一段 UI。 */
function boxPrintLang(product: SealedProductView): PrintLanguage {
  return product.lang === "jp" ? "ja" : "en";
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
  const { period, printLang, query, sort, dir, update } = useMarketSettings();
  const t = copy[locale];
  const boxSort = normaliseBoxSort(sort);
  /* 篩選只列出榜上真係有嘅語言（同 rankings.tsx 一樣，≤1 種就唔出）。 */
  const availableLanguages = useMemo(() => {
    const seen = new Set<PrintLanguage>();
    for (const product of products) seen.add(boxPrintLang(product));
    return (["en", "ja"] as PrintLanguage[]).filter((lang) => seen.has(lang));
  }, [products]);
  /* URL 揀咗個榜上冇嘅語言（例如 ?printLang=ko）就當冇篩，唔准出空榜。 */
  const activeLang: PrintLangFilter =
    printLang !== "all" && availableLanguages.includes(printLang) ? printLang : "all";
  /* 同 rankings.tsx：demote 唔准靜靜做，URL 仲寫住舊 printLang 嘅話，換 group／換頁
     個 pool 一變佢就會復活。寫返 URL；寫完條件自己就 false，唔會 loop。 */
  const langDemoted = printLang !== "all" && !availableLanguages.includes(printLang);
  useEffect(() => {
    if (langDemoted) update({ printLang: "all" });
  }, [langDemoted, update]);
  const explored = useMemo(() => {
    const pool = activeLang === "all" ? products : products.filter((product) => boxPrintLang(product) === activeLang);
    return sortBoxes(pool.filter((product) => boxMatchesQuery(product, query, locale)), boxSort, dir, period);
  }, [activeLang, boxSort, dir, locale, period, products, query]);
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
  /* BOX 冇範圍（route 已經係 /box），所以個 sheet 得排序 + 方向 + 語言三段；
     語言嗰段由 availableLanguages（>1 種）決定出唔出。 */
  const sortKeys = [
    { key: "rank", label: t.labels.rank },
    { key: "price", label: t.labels.priceShort },
    { key: "sold", label: t.box.soldCountShort },
    { key: "release", label: t.box.release },
  ];
  const sortLabel = sortKeys.find((item) => item.key === boxSort)?.label ?? t.labels.rank;
  const [sortSheetOpen, setSortSheetOpen] = useState(false);
  /* 同 rankings.tsx：有非預設先出 chip 行，排序同語言各一粒。 */
  const filterChips: Array<{ key: string; label: string; removeLabel: string; onRemove: () => void }> = [];
  if (boxSort !== "rank") {
    filterChips.push({
      key: "sort",
      label: `${sortLabel} ${dir === "asc" ? "↑" : "↓"}`,
      removeLabel: t.labels.removeFilter.replace("{filter}", sortLabel),
      onRemove: () => update({ sort: "rank", dir: "desc" }),
    });
  }
  if (activeLang !== "all") {
    filterChips.push({
      key: "lang",
      label: localizedCardLanguageShort(activeLang),
      removeLabel: t.labels.removeFilter.replace("{filter}", localizedCardLanguage(activeLang, locale)),
      onRemove: () => update({ printLang: "all" }),
    });
  }

  /* 同 rankings.tsx 一樣（FE05 WS4）：打緊字／「顯示更多」transition 期間，
     見到嘅唔係最終結果。BOX 冇全站索引，所以少咗 catalog 嗰一項。 */
  const [queryPending, setQueryPending] = useState(false);
  /* 同 rankings.tsx：≤680 .ranking-heading 係一行（h2 左、掣右），六粒掣擺唔落 → 收埋做 PeriodMenu popover。
     之前漏咗呢度，/box 手機版 h2 被夾到一字一行、六粒掣撐成一大塊（owner 2026-08-17 截圖）。 */
  const isMobileBar = useMediaQuery(MOBILE_BAR_QUERY);
  /* 同 rankings.tsx：桌面表頭釘喺 sticky 標題底下，要知標題實高 */
  const headingRef = useRef<HTMLDivElement>(null);
  useRankingHeadingHeight(headingRef);
  return (
    <section className="rankings-section" id="box-ranking" aria-labelledby="box-ranking-heading" aria-busy={queryPending || isPending}>
      <div className="ranking-heading" ref={headingRef}>
        <div>
          <p className="section-kicker">{t.nav.box}</p>
          <h2 id="box-ranking-heading">{title}</h2>
          {/* 語言列手機搬咗入排序 sheet（同卡榜一樣） */}
          {!isMobileBar && availableLanguages.length > 1 && (
            <div className="lang-filter" role="group" aria-label={t.labels.language}>
              {(["all", ...availableLanguages] as PrintLangFilter[]).map((lang) => (
                <button
                  key={lang}
                  type="button"
                  aria-pressed={activeLang === lang}
                  aria-label={lang === "all" ? t.labels.languageFilterAll : localizedCardLanguage(lang, locale)}
                  onClick={() => { tap.select(); update({ printLang: lang }); }}
                >
                  {activeLang === lang && <span className="lang-filter-pill" aria-hidden="true" />}
                  <span>{lang === "all" ? t.labels.languageFilterAllShort : localizedCardLanguageShort(lang)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        {isMobileBar ? <PeriodMenu /> : <PeriodSelector compact />}
      </div>
      {!products.length ? <EmptyState icon={PackageOpen} title={t.box.empty} /> : (
        <>
          <ExploreBar
            query={query}
            onQueryChange={(value) => update({ query: value })}
            placeholder={t.labels.searchPlaceholderBox}
            searchLabel={t.labels.searchLabel}
            clearLabel={t.labels.searchClear}
            resultLabel={resultLabel}
            sortKeys={sortKeys}
            sort={boxSort}
            dir={dir}
            onSort={applySort}
            highToLow={t.labels.sortHighToLow}
            lowToHigh={t.labels.sortLowToHigh}
            onOpenSortSheet={() => setSortSheetOpen(true)}
            sortSheetLabel={boxSort === "rank"
              ? t.labels.sortSheetTrigger
              : t.labels.sortSheetTriggerActive.replace("{label}", sortLabel)}
            sortSheetActive={filterChips.length > 0}
            onPendingChange={setQueryPending}
            filterChips={filterChips}
          />
          <SortFilterSheet
            open={sortSheetOpen}
            onClose={() => setSortSheetOpen(false)}
            locale={locale}
            sortKeys={sortKeys}
            /* /box 唔行 `?size=` 分頁（成個原盒榜一次過出），所以 sheet 唔出「每頁」
               嗰段，`pageSize` 淨係填個預設頂住 type，冇人讀。 */
            value={{ sort: boxSort, dir, printLang: activeLang, pageSize: DEFAULT_RANKING_PAGE_SIZE }}
            availableLanguages={availableLanguages}
            pageSizePicker={false}
            /* 手機冇語言列（收埋咗喺呢個 sheet），所以 printLang 一定要一齊寫返 URL */
            onApply={(next) => update({ sort: next.sort, dir: next.dir, printLang: next.printLang })}
            onReset={() => update({ sort: "rank", dir: "desc", printLang: "all" })}
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
              const mobileBadge = langBadge(product);
              return (
                <Link className="mobile-rank-card" href={href(`/box/${product.id}`)} key={product.id}>
                  <span className="mobile-rank-index">{product.rank}</span>
                  <div className="ranking-thumb box-thumb"><BoxImage image={product.image} sizes="56px" alt="" /></div>
                  <div className="mobile-card-info">
                    {/* owner 2026-08-17：語言唔再分 group，靠呢粒 EN/JP chip（同卡榜手機版一樣擺編號隔籬） */}
                    <span className="mobile-card-sub">
                      <span className="mobile-card-number">{product.setCode}</span>
                      <span className={`${mobileBadge.className} mobile-lang-badge`}>{mobileBadge.label}</span>
                    </span>
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
