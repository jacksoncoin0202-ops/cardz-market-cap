"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState, useTransition } from "react";
import { ArrowDown, ArrowUp, Inbox, SearchX, TrendingDown, TrendingUp } from "lucide-react";
import { CardImage } from "./card-image";
import { displayCardName } from "@/lib/card-name";
import { EmptyState } from "./empty-state";
import { ExploreBar, SortHeader } from "./explore-bar";
import { PeriodMenu, PeriodSelector } from "./period-selector";
import { SortFilterSheet } from "./sort-filter-sheet";
import { Sparkline } from "./sparkline";
import { loadCatalog, prefetchCatalog } from "@/lib/catalog-client";
import { CATALOG_LIST_CAP, catalogToCard, searchCatalog } from "@/lib/catalog-search";
import { cardLanguages, copy, localizedCardLanguage, localizedCardLanguageShort } from "@/lib/i18n";
import { formatDeltaMoney, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { tap } from "@/lib/haptic";
import { cardMatchesQuery, nextExploreSort, normaliseCardSort, sortCards } from "@/lib/list-explore";
import { URL_SHOW_CAP, useMarketSettings, type PrintLangFilter } from "@/lib/use-market-settings";
import { useMediaQuery } from "@/lib/use-media-query";
import type { RankingScope } from "@/lib/pagination";
import type { CatalogEntry, Currency, Locale, MarketCardView, MarketMetric, MarketViewSnapshot, MarketWindow, TrackedSalesMetric } from "@/lib/types";

/* globals.css `@media (max-width: 980px)` 度 .desktop-ranking-table 收起、.mobile-ranking-list
   出場。兩邊要係同一個斷點，唔係就會兩個都出／兩個都唔出。 */
const MOBILE_LIST_QUERY = "(max-width: 980px)";
/* 手機瘦身斷點（同 explore-bar.tsx / globals.css 手機 explore 段一致）：≤680 先收起
   語言列、時段選擇器同排序 chips，改行「搜尋框 + 排序 sheet」。 */
/* export 俾 box-rankings.tsx 用同一個斷點：globals.css ≤680 嗰段將 .ranking-heading 釘返做一行
   （h2 左、一粒 popover 掣右），所以凡係 .ranking-heading 入面嘅時段掣，≤680 一律要係 PeriodMenu，
   唔可以再係六粒掣嘅 PeriodSelector（/box 曾經漏咗，h2 被夾到一字一行）。 */
export const MOBILE_BAR_QUERY = "(max-width: 680px)";

interface RankingsProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  watchlist?: boolean;
  marketLabel?: string;
  searchScope?: RankingScope;
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
  period: MarketWindow;
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

/* status.stale 唔再係死 key：數值仍出（ready 一樣計法），但加虛線底 + title 話畀人知係舊價。 */
export function staleClass(metric: MarketMetric<unknown>, base = ""): string | undefined {
  if (metric.status !== "stale") return base || undefined;
  return base ? `${base} metric-stale` : "metric-stale";
}
export function staleTitle(metric: MarketMetric<unknown>, locale: Locale): string | undefined {
  return metric.status === "stale" ? copy[locale].status.stale : undefined;
}

function CardIdentity({ card, locale, unavailable }: { card: MarketCardView; locale: Locale; unavailable: string }) {
  const name = displayCardName(card, locale, unavailable);
  return (
    <div className="ranking-card-identity">
      <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" alt={name} /></div>
      <div className="ranking-name"><strong>{name}</strong></div>
    </div>
  );
}

function ChangeBadge({ card, period, locale }: { card: MarketCardView; period: MarketWindow; locale: Locale }) {
  const change = card.windows[period].changePct;
  const tone = metricTone(change);
  const Icon = tone === "positive" ? TrendingUp : tone === "negative" ? TrendingDown : null;
  return (
    <span className={`mobile-change-badge metric-${tone}`} title={change.sourceSwitched ? copy[locale].provenance.anchorSwitched : undefined}>
      {Icon && <Icon aria-hidden="true" size={13} strokeWidth={2} />}
      {formatPercent(change, locale)}
    </span>
  );
}

export function Rankings({ cards, locale, currency, snapshot, href, watchlist = false, marketLabel, searchScope = "all" }: RankingsProps) {
  const { period, printLang, query, show, showDirty, sort, dir, update } = useMarketSettings();
  const router = useRouter();
  const t = copy[locale];
  const cardSort = normaliseCardSort(sort);
  const [catalog, setCatalog] = useState<CatalogEntry[] | null>(null);
  /* 載索引失敗要同「未載完」分得開：catalog 一律保持 null（退化做當頁過濾），
     旗只係用嚟出提示。以前寫 setCatalog([]) —— 空索引 = 零命中，斷網一搜就變成
     「全部卡未合資格」，係講大話。 */
  const [catalogError, setCatalogError] = useState(false);
  /* 打字中（input 值 ≠ URL q）由 ExploreBar 報返上嚟——搜尋 debounce 嗰 220ms 加
     transition 嗰段，畫面仲係舊結果，讀屏唔應該當佢係最終答案。 */
  const [queryPending, setQueryPending] = useState(false);
  /* SSR 一定係 `catalog === null`（索引係 useEffect 先攞），所以直接拎佢做
     `aria-busy` 會令 server HTML 喺深鏈 `/?q=…` 永遠寫住 busy=true —— 有 JS
     嗰邊 3 秒後會翻返 false，冇 JS／靜態抓取嗰邊就永遠 busy。加呢粒 mount 旗，
     `aria-busy` 只喺 client 真係載緊索引嗰陣先 true。 */
  const [hydrated, setHydrated] = useState(false);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- mount 旗係 hydration 專用：server 一定要 render false，所以唯一寫得嘅時機就係 mount effect。
  useEffect(() => setHydrated(true), []);
  const isMobileList = useMediaQuery(MOBILE_LIST_QUERY);
  const isMobileBar = useMediaQuery(MOBILE_BAR_QUERY);
  const [sortSheetOpen, setSortSheetOpen] = useState(false);
  const searching = Boolean(query.trim());
  useEffect(() => {
    if (!searching) return;
    let cancelled = false;
    loadCatalog()
      .then((payload) => {
        if (cancelled) return;
        setCatalog(payload.entries);
        setCatalogError(false);
      })
      .catch(() => {
        if (!cancelled) setCatalogError(true);
      });
    return () => {
      cancelled = true;
    };
  }, [searching]);
  /* 範圍 = route（設計稿 §設計（手機）3）：/pokemon 只搜寶可夢、/ 搜全站。
     以前有個 client-only `liveScope`，冇 q 嗰陣係死掣、清完搜尋唔還原、kicker 又講錯範圍。 */
  const searchTcg = searchScope === "pokemon" ? "Pokémon" : searchScope === "one-piece" ? "One Piece" : undefined;
  const scopedCatalog = useMemo(() => {
    if (!catalog?.length) return [];
    return catalog.filter((entry) => entry.kind === "card" && (!searchTcg || entry.tcg === searchTcg));
  }, [catalog, searchTcg]);
  /* 篩選只列出榜上真係有嘅印刷語言。dev seed 帶 legacy key `language`，
     mapper 出 null，所以 dev 冇語言、冇 filter —— 呢個係正確行為。 */
  const availableLanguages = useMemo(() => {
    const pool = searching && scopedCatalog.length ? scopedCatalog : cards;
    const seen = new Set<string>();
    for (const item of pool) if (item.cardLanguage) seen.add(item.cardLanguage);
    return cardLanguages.filter((lang) => seen.has(lang));
  }, [cards, scopedCatalog, searching]);
  /* URL 揀咗個榜上冇嘅語言就當冇篩，唔准出空榜。 */
  const activeLang: PrintLangFilter =
    printLang !== "all" && availableLanguages.includes(printLang) ? printLang : "all";
  /* demote 唔准靜靜做：URL 仲寫住舊 printLang，pool 一變（換頁／索引載完）就會復活，
     294 張變返 1 張。所以要寫返 URL。條件本身就係 guard——寫完 printLang 就係 "all"，
     langDemoted 變 false，唔會 loop。
     索引仲載緊（searching && catalog === null）唔准寫：嗰陣個 pool 只係當頁嗰批卡，
     當唔到係成個榜嘅語言全集。 */
  const langPoolSettled = !searching || catalog !== null;
  const langDemoted = langPoolSettled && printLang !== "all" && !availableLanguages.includes(printLang);
  useEffect(() => {
    if (langDemoted) update({ printLang: "all" });
  }, [langDemoted, update]);
  /* 命中先算晒（分母要真數，唔可以出截斷數），render 先至截 visibleLimit。
     searchCatalog 傳唔傳 limit 都一樣要 map + sort 晒成個 pool 再 slice，所以喺呢度
     slice 開銷相同，但攞得返個 total。 */
  const catalogHits = useMemo(() => {
    if (!searching || !scopedCatalog.length) return [];
    const hits = searchCatalog(scopedCatalog, query, locale, { kind: "card" });
    return activeLang === "all" ? hits : hits.filter((entry) => entry.cardLanguage === activeLang);
  }, [activeLang, locale, query, scopedCatalog, searching]);
  /* 一個字母可以命中三千幾張：一次過 render 曬 = 89k DOM node、主線程一秒幾。
     先出 CATALOG_LIST_CAP（80）張，撳「再顯示」逐 80 加。
     展開量頭 480 行（`URL_SHOW_CAP`）係 URL state（`show=`，見 use-market-settings.ts）：
     以前純 React state，撳完入卡頁再撳返上一頁，state 冇咗、榜縮返 80 行，
     scroll-restoration 連嗰行 anchor 都搵唔返。q 一變由 `update()` 負責清走個 param。
     480 行之後嗰段係 session state（下面 `sessionShow`）——URL 唔可以描述一個
     大過一個 commit 預算嘅第一 paint。 */
  const [isPending, startShowMore] = useTransition();
  /*
   * 撳出嚟嘅展開量：URL 只帶到 `URL_SHOW_CAP`（480 行 = 一個 commit 嘅預算），
   * 撳多過嗰個數嘅部分只活喺呢一 session（每撳一下加 80 行，唔係一 paint 幾百行）。
   * 綁住 `query`：`update()` 一見 q 變就 delete `show`，呢邊要跟返同一條規矩，
   * 唔係搜「a」展開到 800 行、改搜「pikachu」會照住 800 行出。
   */
  const [sessionShow, setSessionShow] = useState<{ query: string; rows: number } | null>(null);
  const sessionRows = sessionShow?.query === query ? sessionShow.rows : 0;
  /* URL 講幾多就幾多，但唔准超過「命中數湊足一版」——`?show=8000` 打三張命中嘅
     搜尋，`remaining` 要係 0（唔出掣），下一次撳都由真實上限接落去。 */
  const maxVisible = Math.max(
    CATALOG_LIST_CAP,
    Math.ceil(catalogHits.length / CATALOG_LIST_CAP) * CATALOG_LIST_CAP,
  );
  const visibleLimit = Math.min(Math.max(show, sessionRows), maxVisible);
  /* `?show=abc` / `?show=123` 讀嗰陣係 80 / 160，但 URL 冇改過就會一路帶住個
     垃圾值傳落去（`update()` 照抄未提及嘅 param）。同 `langDemoted` 一樣嘅自我
     修正：寫一次返去，寫完 `showDirty` 就係 false，唔會 loop。 */
  useEffect(() => {
    if (showDirty) update({ show });
  }, [show, showDirty, update]);
  const shownHits = useMemo(
    () => (catalogHits.length > visibleLimit ? catalogHits.slice(0, visibleLimit) : catalogHits),
    [catalogHits, visibleLimit],
  );
  /* 篩選淨係隱藏行：viewRank / marketRank 照原樣出，唔准重新編號。
     搜尋打晒 bake 入站嘅卡，還原成同一張榜表，唔另開一列核突結果。 */
  const visibleCards = useMemo(() => {
    const langCards = activeLang === "all" ? cards : cards.filter((card) => card.cardLanguage === activeLang);
    if (!searching) return sortCards(langCards, cardSort, dir, period);
    /* catalog 未到（或者載失敗，兩者都係 null）先用當前榜頂住；一 load 完就只信全站索引，
       唔准跌返去當前頁過濾（唔係咁，大榜搜魯夫再切寶可夢會繼續出海賊王）。 */
    if (catalog === null) {
      return sortCards(langCards.filter((card) => cardMatchesQuery(card, query, locale)), cardSort, dir, period);
    }
    const inView = new Map(cards.map((card) => [card.id, card]));
    const rows = shownHits
      .map((entry) => inView.get(entry.id) ?? catalogToCard(entry))
      .filter((card): card is MarketCardView => Boolean(card));
    return sortCards(rows, cardSort, dir, period);
  }, [activeLang, cardSort, cards, catalog, dir, locale, period, query, searching, shownHits]);
  const applySort = (key: string) => {
    tap.select();
    const next = nextExploreSort(cardSort, dir, key);
    update({ sort: next.sort, dir: next.dir });
  };
  const rankedOnPage = cards.filter((card) => card.viewRank > 0);
  const firstRank = rankedOnPage[0]?.viewRank;
  const lastRank = rankedOnPage.at(-1)?.viewRank;
  const rankingTitle = firstRank === 1
    ? t.heatmap.rankingTitle.replace("{count}", String(rankedOnPage.length || cards.length))
    : firstRank && lastRank
      ? t.labels.rankingRange.replace("{from}", String(firstRank)).replace("{to}", String(lastRank))
      : t.heatmap.rankingTitle.replace("{count}", String(cards.length));
  const heading = searching ? t.labels.searchModeTitle : watchlist ? t.nav.watchlist : rankingTitle;
  const catalogCardCount = scopedCatalog.length;
  const resultTotal = searching && catalogCardCount ? catalogCardCount : cards.length;
  /* 用緊全站索引嗰陣，{shown} 要出命中總數而唔係「而家 render 緊幾多行」——
     出截斷數會令人以為全站得 80 張命中。 */
  const usingCatalog = searching && catalog !== null && catalogCardCount > 0;
  const resultShown = usingCatalog ? catalogHits.length : visibleCards.length;
  const remaining = usingCatalog ? Math.max(0, catalogHits.length - visibleLimit) : 0;
  const resultLabel = (searching || visibleCards.length !== cards.length)
    ? t.labels.resultCount.replace("{shown}", String(resultShown)).replace("{total}", String(resultTotal))
    : null;
  const sortKeys = [
    { key: "rank", label: t.labels.rank },
    { key: "price", label: t.labels.priceShort },
    { key: "pop", label: t.labels.populationShort },
    { key: "sales", label: t.labels.trackedSalesShort },
    { key: "change", label: t.labels.changeShort },
  ];
  const sortLabel = sortKeys.find((item) => item.key === cardSort)?.label ?? t.labels.rank;
  /* 全站搜尋連結：href() 已經帶住 lang / currency / period（唔帶 q），所以喺佢後面補返 q。
     範圍 = route，所以係真 navigate 去 `/`，唔係改一個 client state。 */
  const siteWideSearchHref = () => {
    const [path, search] = href("/").split("?");
    const params = new URLSearchParams(search);
    params.set("q", query.trim());
    return `${path}?${params.toString()}`;
  };
  /* 非預設先出 chip 行；冇非預設就一行都唔 render（設計稿 §設計（手機）5）。
     排序 chip 嘅 × 一次過還原 sort + dir（dir 冇 sort 就冇意思）。 */
  const filterChips: Array<{ key: string; label: string; removeLabel: string; onRemove: () => void }> = [];
  if (cardSort !== "rank") {
    filterChips.push({
      key: "sort",
      label: `${sortLabel} ${dir === "asc" ? "↑" : "↓"}`,
      removeLabel: t.labels.removeFilter.replace("{filter}", sortLabel),
      onRemove: () => update({ sort: "rank", dir: "desc" }),
    });
  }
  if (activeLang !== "all") {
    const langLabel = localizedCardLanguageShort(activeLang);
    filterChips.push({
      key: "lang",
      label: langLabel,
      removeLabel: t.labels.removeFilter.replace("{filter}", localizedCardLanguage(activeLang, locale)),
      onRemove: () => update({ printLang: "all" }),
    });
  }
  /*
   * `aria-busy`（FE05 WS4）：三種「而家見到嘅唔係最終結果」都要報——打緊字／
   * 全站索引仲載緊（`catalog === null` 而又冇 error，嗰陣只係當頁過濾）／
   * 「顯示更多」個 transition。落喺成個 section（佢就係 `aria-labelledby` 嗰個
   * 結果區）而唔係另包一層 div：加 wrapper 會改到現有 flow 版面。
   */
  const resultsBusy = queryPending || isPending || (hydrated && searching && catalog === null && !catalogError);
  return (
    <section className="rankings-section" id="market-ranking" aria-labelledby="ranking-heading" aria-busy={resultsBusy}>
      {/* 搜尋模式手機收起 kicker、h2 縮成一行（h2 要留住，section 嘅 aria-labelledby 指住佢） */}
      <div className="ranking-heading" data-searching={searching ? "true" : "false"}>
        <div>
          <p className="section-kicker">{watchlist ? t.labels.watchStatus : marketLabel ?? t.nav.all}</p>
          <h2 id="ranking-heading">{heading}</h2>
          {/* 語言列手機搬咗入排序 sheet */}
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
        {/* 手機收埋做 PeriodMenu popover，但一定要留喺呢一行（h2 右邊）：獨立一行嘅
            .mobile-list-toolbar 睇落似浮咗出嚟，owner 2026-08-17 叫拆走。 */}
        {isMobileBar ? <PeriodMenu /> : <PeriodSelector compact />}
      </div>
      <ExploreBar
        query={query}
        onQueryChange={(value) => update({ query: value })}
        placeholder={
          searchScope === "pokemon"
            ? t.labels.searchPlaceholderPokemon
            : searchScope === "one-piece"
              ? t.labels.searchPlaceholderOnePiece
              : t.labels.searchPlaceholder
        }
        searchLabel={
          searchScope === "pokemon"
            ? t.labels.searchLabelPokemon
            : searchScope === "one-piece"
              ? t.labels.searchLabelOnePiece
              : t.labels.searchLabel
        }
        clearLabel={t.labels.searchClear}
        resultLabel={resultLabel}
        sortKeys={sortKeys}
        sort={cardSort}
        dir={dir}
        onSort={applySort}
        highToLow={t.labels.sortHighToLow}
        lowToHigh={t.labels.sortLowToHigh}
        onSearchFocus={() => prefetchCatalog()}
        onOpenSortSheet={() => setSortSheetOpen(true)}
        sortSheetLabel={cardSort === "rank"
          ? t.labels.sortSheetTrigger
          : t.labels.sortSheetTriggerActive.replace("{label}", sortLabel)}
        sortSheetActive={filterChips.length > 0}
        inlineCountLabel={resultLabel ? t.labels.resultCountShort.replace("{count}", String(resultShown)) : null}
        announceLabel={resultLabel ? t.labels.resultCountAnnounce.replace("{count}", String(resultShown)) : null}
        onPendingChange={setQueryPending}
        filterChips={filterChips}
        sticky={searching}
      />
      <SortFilterSheet
        open={sortSheetOpen}
        onClose={() => setSortSheetOpen(false)}
        locale={locale}
        sortKeys={sortKeys}
        value={{ sort: cardSort, dir, printLang: activeLang }}
        availableLanguages={availableLanguages}
        /* 一個手勢一次寫入：三樣嘢一次過落 URL，唔會三次 router.replace 互相覆蓋 */
        onApply={(next) => update({ sort: next.sort, dir: next.dir, printLang: next.printLang, page: 1 })}
        /* 「還原」要連展開量一齊清（`show` ≤ 預設就等於由 URL 刪走），
           連撳出嚟嗰段 session 展開都要清，唔係 URL 返 80 行但畫面仲係 800 行 */
        onReset={() => {
          setSessionShow(null);
          update({ sort: "rank", dir: "desc", printLang: "all", page: 1, show: CATALOG_LIST_CAP });
        }}
      />
      {/* 索引載唔到就唔准扮全站搜過：有結果都要講明剩返當頁（冇結果嗰個 case 出喺 empty-state 入面） */}
      {searching && catalogError && visibleCards.length ? (
        <p className="empty-state-hint">{t.labels.catalogUnavailable}</p>
      ) : null}
      {!visibleCards.length ? (
        <EmptyState
          icon={searching ? SearchX : Inbox}
          title={searching ? t.labels.noSearchResults : t.labels.noCards}
          /* 索引載唔到 → 講「而家只有當頁」；索引 OK 但零命中 → 講「未合資格」。
             兩句都係原本嗰兩個 key，一個字都冇改。 */
          hint={searching && catalogError
            ? t.labels.catalogUnavailable
            : searching && searchScope === "all"
              ? t.labels.searchUnqualified
              : null}
          /* 分榜搜唔到就一粒掣去全站（範圍 = route），唔再叫人揀返個已經拆走嘅 dropdown */
          action={searching && !catalogError && searchScope !== "all" ? (
            <button
              type="button"
              className="empty-state-action"
              onClick={() => { tap.select(); router.push(siteWideSearchHref()); }}
            >
              {t.labels.searchAllSite.replace("{query}", query.trim())}
            </button>
          ) : null}
        />
      ) : null}
      {visibleCards.length ? (
        <>
          {/* 桌面 table 同手機 list 只 render 一個：以前兩份都 mount，DOM／sparkline 行兩次。
              SSR 同 hydration render 一律當桌面（useMediaQuery 個 server snapshot 係 false），
              hydrate 完先切，唔會 hydration mismatch。 */}
          {isMobileList ? null : (
          <div className="desktop-ranking-table">
            <table>
              <caption className="sr-only">{heading}</caption>
              <colgroup>
                <col className="col-rank" /><col className="col-card" /><col className="col-number" /><col className="col-price" />
                <col className="col-pop" /><col className="col-cap" /><col className="col-sales" /><col className="col-change" /><col className="col-spark" />
              </colgroup>
              <thead><tr>
                <SortHeader label={t.labels.rank} sortKey="rank" activeKey={cardSort} dir={dir} onSort={applySort} />
                <th scope="col">{t.labels.card}</th><th scope="col">{t.labels.number}</th>
                <SortHeader label={t.labels.priceShort} sortKey="price" activeKey={cardSort} dir={dir} onSort={applySort} className="numeric" />
                <SortHeader label={t.labels.populationShort} sortKey="pop" activeKey={cardSort} dir={dir} onSort={applySort} className="numeric" />
                <th scope="col" className="numeric">{t.labels.marketCapShort}</th>
                <SortHeader label={`${t.periods[period]} ${t.labels.trackedSalesShort}`} sortKey="sales" activeKey={cardSort} dir={dir} onSort={applySort} className="numeric" />
                <SortHeader label={`${t.periods[period]} ${t.labels.changeShort}`} sortKey="change" activeKey={cardSort} dir={dir} onSort={applySort} className="numeric" />
                <th scope="col" className="numeric">{t.labels.salesTrendShort}</th>
              </tr></thead>
              <tbody>{visibleCards.map((card) => {
                const metrics = card.windows[period];
                const cardUrl = href(`/card/${card.id}`);
                /* 成行係一條真 <a>（卡名 .row-link 用 ::after 鋪滿 .rank-row）：
                   中鍵／Cmd-click／右鍵複製連結全部返嚟，table 語意亦唔會被 role="link" 蓋走。 */
                return (
                  <tr key={card.id} className="rank-row">
                    {/* rank 欄得 26px：冇數字出「—」，成句「等待新鮮價格」放 title，唔准塞入格 */}
                    <td className="rank-cell" data-rank={card.viewRank > 0 ? String(card.viewRank) : undefined} title={card.viewRank > 0 ? undefined : t.labels.awaitingFreshPrice}>{card.viewRank > 0 ? card.viewRank : "—"}</td>
                    <td><Link href={cardUrl} className="row-link"><CardIdentity card={card} locale={locale} unavailable={t.status.unavailable} /></Link></td>
                    <td className="collector-cell">{card.collectorNumber}</td>
                    <td className="numeric price-cell">
                      <span className={staleClass(card.pricePsa10, "price-now")} title={staleTitle(card.pricePsa10, locale)}>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                      <PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className="numeric">
                      <span className={staleClass(card.populationPsa10, "pop-now")} title={staleTitle(card.populationPsa10, locale)}>{formatMetricInteger(card.populationPsa10, locale)}</span>
                    </td>
                    <td className="numeric market-cap-cell">
                      <span className={staleClass(card.marketCap, "price-now")} title={staleTitle(card.marketCap, locale)}>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                      <MetricDelta metric={card.marketCap} changePct={metrics.marketCapChangePct} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className="numeric sales-cell">
                      <span className="price-now">{formatTrackedSales(metrics.trackedSales, currency, snapshot.rates, locale)}</span>
                      <SalesDelta sales={metrics.trackedSales} changePct={metrics.trackedSalesChangePct} currency={currency} rates={snapshot.rates} locale={locale} />
                    </td>
                    <td className={`numeric metric-${metricTone(metrics.changePct)}`} title={metrics.changePct.sourceSwitched ? t.provenance.anchorSwitched : undefined}>{formatPercent(metrics.changePct, locale)}</td>
                    <td className="numeric spark-cell"><Sparkline values={card.salesSparkline} label={t.labels.salesTrend} /></td>
                  </tr>
                );
              })}</tbody>
            </table>
          </div>
          )}
          {isMobileList ? (
          <>
          <div className="mobile-ranking-list">
            <div className="mobile-list-header" aria-hidden="true">
              <span className="mobile-col-info">{t.labels.card}</span>
              <span className="mobile-col-right">{t.labels.priceShort}</span>
              {/* 呢欄闊 48px：用短過 salesTrendShort 嘅 salesTrendColumn，唔係英文摺兩行、
                  成個 header 由 27px 變 40px，第一張卡就跌出設計稿嘅 320px 外 */}
              <span className="mobile-col-spark">{t.labels.salesTrendColumn}</span>
            </div>
            {visibleCards.map((card) => (
              <Link className="mobile-rank-card" href={href(`/card/${card.id}`)} key={card.id}>
                <span className="mobile-rank-index" title={card.viewRank > 0 ? undefined : t.labels.awaitingFreshPrice}>{card.viewRank > 0 ? card.viewRank : "—"}</span>
                <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" alt={displayCardName(card, locale, t.status.unavailable)} /></div>
                <div className="mobile-card-info">
                  <span className="mobile-card-sub">
                    <span className="mobile-card-number">{card.collectorNumber}</span>
                  </span>
                  <strong className="mobile-card-name">{displayCardName(card, locale, t.status.unavailable)}</strong>
                  {card.marketCap.value !== null && (card.marketCap.status === "ready" || card.marketCap.status === "stale") && (
                    <span className="mobile-card-sub">
                      <span className={staleClass(card.marketCap, "mobile-card-cap")} title={staleTitle(card.marketCap, locale)}>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                    </span>
                  )}
                  {/* 第三行出 PSA10 POP：手機都睇到「價 × POP = 市值」條數點嚟 */}
                  {card.populationPsa10.value !== null && (card.populationPsa10.status === "ready" || card.populationPsa10.status === "stale") && (
                    <span className="mobile-card-sub">
                      <span className={staleClass(card.populationPsa10, "mobile-card-pop")} title={staleTitle(card.populationPsa10, locale)}>
                        {t.labels.populationShort} {formatMetricInteger(card.populationPsa10, locale)}
                      </span>
                    </span>
                  )}
                </div>
                <div className="mobile-card-right">
                  <span className="mobile-card-price">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                  <ChangeBadge card={card} period={period} locale={locale} />
                </div>
                <Sparkline values={card.salesSparkline} label={t.labels.salesTrend} />
              </Link>
            ))}
          </div>
          </>
          ) : null}
          {remaining > 0 ? (
            <button
              type="button"
              className="box-show-more"
              disabled={isPending}
              aria-busy={isPending}
              /* transition 包住 router.replace：commit 之前個掣 disabled，
                 所以連撳兩下唔會兩次都由同一個 visibleLimit 起算。 */
              onClick={() => startShowMore(() => {
                const nextRows = visibleLimit + CATALOG_LIST_CAP;
                setSessionShow({ query, rows: nextRows });
                /* 過咗 URL 硬頂就唔好再寫：URL 寫住 480，session state 帶住其餘，
                   唔係每撳一下都行一次 clamp 到同一個值嘅 router.replace。 */
                if (nextRows <= URL_SHOW_CAP) update({ show: nextRows });
              })}
            >
              {t.labels.showMoreResults
                .replace("{count}", String(Math.min(remaining, CATALOG_LIST_CAP)))
                .replace("{total}", String(catalogHits.length))}
            </button>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
