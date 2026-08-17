"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { Heatmap } from "./heatmap";
import { Provenance } from "./provenance";
import { RankingPager, type RankingPagerData } from "./ranking-pager";
import { Rankings } from "./rankings";
import { canonicalPublicUrl, datasetId, organizationId, siteOrganization, StructuredData } from "./structured-data";
import { displayCardName } from "@/lib/card-name";
import { formatMoney, formatObservationDate, formatPercent } from "@/lib/format";
import { copy, type Copy } from "@/lib/i18n";
import { cardSubject, fillTemplate } from "@/lib/related-cards";
import { fetchRankingPage } from "@/lib/ranking-feed";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";
import "@/app/styles/market-foot.css";
import "@/app/styles/glow-badges.css";

type MarketPageKind = "all" | "pokemon" | "one-piece" | "watchlist";

/*
 * 首屏引用句用嘅卡名（GEO，owner 2026-08-16）。唔用 officialName 全句：en 嗰句係 PSA
 * 證書原文（年份＋set 名＋編號），塞落三行嘅 intro 度即刻爆版。同 card-detail 嗰句
 * 可引用事實行同一個剝法（cardSubject），所以兩版講同一張卡會出同一個名。
 */
function introCardName(card: MarketCardView, locale: Locale): string {
  const localName = displayCardName(card, locale, "");
  return (localName && localName !== card.officialName ? localName : cardSubject(card))
    || localName
    || card.officialName
    || "";
}

export function marketHeatmapTitle(kind: MarketPageKind, cardCount: number, t: Copy): string {
  const marketTitle = kind === "pokemon"
    ? t.heatmap.pokemonTitle
    : kind === "one-piece"
      ? t.heatmap.onePieceTitle
      : t.heatmap.title;
  return marketTitle.replace("{count}", String(cardCount));
}

export function MarketPage({ kind, snapshot, pager }: { kind: MarketPageKind; snapshot: MarketViewSnapshot; pager?: RankingPagerData | null }) {
  const { locale, currency, href } = useMarketSettings();
  const t = copy[locale];
  const hero = kind === "pokemon" ? t.pokemonHero : kind === "one-piece" ? t.onePieceHero : kind === "watchlist" ? t.watchlistHero : t.hero;
  const cards = snapshot.top100;
  /*
   * 「碌到底接落去」攞返嚟嗰批（owner 2026-08-18）。**只餵去 `<Rankings>`**——
   * 熱力圖（`heatmapCards`）同 JSON-LD（`listedCards`）一律用返 SSR 嗰版 `cards`：
   *  · 熱力圖係「頭 100 格」，接多 500 行落去唔應該令佢變成 600 格；
   *  · ItemList 要對得返個 canonical URL 嗰頁嘅內容，client 接咗幾多行係用戶行為，
   *    寫落 structured data 就係同 crawler 講大話。
   *
   * 用 signature 綁住 `scope|page|size`：soft-nav 換頁／換數量嗰陣呢個 component
   * 唔會 unmount，state 唔自己清就會拎住上一頁嗰批 rows 接落新一頁下面（#101–#200
   * 跟住 #601–#700）。同 `rankings.tsx` 個 `sessionShow?.query === query` 一樣嘅寫法。
   */
  const feedSignature = pager ? `${pager.scope}|${pager.page}|${pager.pageSize}` : "";
  const [feed, setFeed] = useState<{ sig: string; rows: MarketCardView[]; nextPage: number; failed: boolean }>(
    { sig: "", rows: [], nextPage: 0, failed: false },
  );
  const [loadingMore, setLoadingMore] = useState(false);
  const fresh = feed.sig === feedSignature;
  const extraRows = fresh ? feed.rows : [];
  const feedFailed = fresh && feed.failed;
  const listCards = extraRows.length ? [...cards, ...extraRows] : cards;
  /* 接到尾就要收埋個掣：`nextHref` 係 SSR 嗰刻算嘅（page 1 < pageCount 就一直存在），
     接完最後一頁佢仍然係非 null，唔另外數就會留低一個撳極都冇反應嘅「展示更多」。 */
  const hasMore = pager ? (fresh ? feed.nextPage : pager.page + 1) <= pager.pageCount : false;

  /*
   * 呢兩個 ref 係「唔准連發」嘅唯一閘：IntersectionObserver 喺一次快碌入面可以連續
   * fire 幾下，而 React state 要下一次 render 先睇得到，淨靠 `loadingMore` 會漏。
   * side effect（fetch）唔准擺喺 `setFeed` 個 updater 入面 —— StrictMode 會行兩次
   * updater，即係一 render 打兩個 request。
   */
  const inFlight = useRef(false);
  const feedRef = useRef(feed);
  /* render 期間唔准寫 ref（react-hooks 有 error 級 rule）。effect 同樣趕得切：
     `loadMore` 淨係由 click / IntersectionObserver 叫，兩者都喺 commit 之後。 */
  useEffect(() => { feedRef.current = feed; }, [feed]);

  const loadMore = useCallback(() => {
    if (!pager || inFlight.current) return;
    const sig = `${pager.scope}|${pager.page}|${pager.pageSize}`;
    const prev = feedRef.current;
    const current = prev.sig === sig ? prev : { sig, rows: [], nextPage: pager.page + 1, failed: false };
    const target = current.nextPage;
    if (target > pager.pageCount) return;
    inFlight.current = true;
    setLoadingMore(true);
    /* 撳「再試一次」要即刻清走個紅旗，否則自動接落去仲係停手狀態。 */
    if (current !== prev || current.failed) setFeed({ ...current, failed: false });
    fetchRankingPage(pager.scope, target, pager.pageSize)
      .then((rows) => {
        setFeed((now) => (now.sig === sig && now.nextPage === target
          ? { sig, rows: [...now.rows, ...rows], nextPage: target + 1, failed: false }
          : now));
      })
      .catch(() => {
        /* 唔准靜靜當到咗尾：出 failed 旗，個掣變「再試一次」，自動接落去停手。 */
        setFeed((now) => (now.sig === sig ? { ...now, failed: true } : now));
      })
      .finally(() => {
        inFlight.current = false;
        setLoadingMore(false);
      });
  }, [pager]);
  const heatmapCards = snapshot.lead100 ?? cards.slice(0, 100);
  /* 傳 raw title（保留 {count}）俾 Heatmap 自己按 visibleCards.length replace，
     咁 Tiles slider 改咗數量，標題同 Share image 都會跟住變。 */
  const heatmapTitle = kind === "pokemon"
    ? t.heatmap.pokemonTitle
    : kind === "one-piece"
      ? t.heatmap.onePieceTitle
      : t.heatmap.title;
  const marketLabel = kind === "pokemon" ? t.nav.pokemon : kind === "one-piece" ? t.nav.onePiece : t.nav.all;
  /* JSON-LD 只出 canonical URL（冇 ?lang/currency/period），唔行 href()。
     watchlist 每頁自己一個 ItemList：名帶 rank 範圍，position 由 1 起，rank 0（等緊新價）唔入。 */
  const listedCards = cards.filter((card) => card.viewRank > 0);
  const firstRank = listedCards[0]?.viewRank;
  const lastRank = listedCards.at(-1)?.viewRank;
  const itemListName = kind === "watchlist" && firstRank !== undefined && lastRank !== undefined
    ? `${t.nav.watchlist} #${firstRank}–#${lastRank}`
    : marketHeatmapTitle(kind, heatmapCards.length, t);
  /*
   * 首屏市況一句（GEO，owner 2026-08-16）：三個數全部即場由 snapshot 計，一個都唔准寫死。
   * 市值 ≤0（等緊新價）唔入總數；7 日變動要 ready/stale 兼有限數先算。填唔齊就成句唔出——
   * 引用句寧願冇，都好過出半句或者 NaN（同 cardFactSentence 一樣 fail-closed）。
   */
  const capCards = cards.filter((card) => (card.marketCap.value ?? 0) > 0);
  const totalCapValue = capCards.reduce((sum, card) => sum + (card.marketCap.value ?? 0), 0);
  const topCard = capCards.reduce<MarketCardView | null>(
    (best, card) => (!best || (card.marketCap.value ?? 0) > (best.marketCap.value ?? 0) ? card : best),
    null,
  );
  const moverCard = cards.reduce<MarketCardView | null>((best, card) => {
    const change = card.windows["7d"]?.changePct;
    if (!change || change.value === null || !Number.isFinite(change.value)) return best;
    if (change.status !== "ready" && change.status !== "stale") return best;
    const bestValue = best?.windows["7d"]?.changePct.value ?? null;
    return bestValue === null || change.value > bestValue ? card : best;
  }, null);
  const introSummary = totalCapValue > 0 && topCard && moverCard
    ? fillTemplate(t.intro.summary, {
      total: formatMoney(totalCapValue, currency, snapshot.rates, locale, true),
      topName: introCardName(topCard, locale),
      topCap: formatMoney(topCard.marketCap.value, currency, snapshot.rates, locale, true),
      moverName: introCardName(moverCard, locale),
      moverPct: formatPercent(moverCard.windows["7d"].changePct, locale),
    })
    : null;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Dataset",
        /* 卡頁／BOX 頁嘅 subjectOf 同 hub 頁嘅 isBasedOn 全部指住呢個 @id。 */
        "@id": datasetId(),
        /*
         * name/description 唔跟 kind 變：呢個 @id 同 url 兩樣都係固定指住首頁嗰個
         * top-100 dataset，如果 /pokemon 用返自己嘅 hero 文案，就會變成同一個 @id
         * 喺唔同頁描述唔同嘢（entity 自相矛盾）。seo.dataset 係全站唯一來源，
         * seo-routes 同 /data 都行同一個。—— verify pass 2026-08-16
         */
        name: t.seo.dataset.name,
        description: t.seo.dataset.description,
        url: canonicalPublicUrl("/"),
        dateModified: snapshot.effectiveAt,
        measurementTechnique: "PSA 10 reference price multiplied by verified PSA 10 population",
        /* GEO 批（owner 2026-08-16）：明寫授權同免費，引擎先有明確引用許可信號。 */
        license: "https://creativecommons.org/licenses/by/4.0/",
        isAccessibleForFree: true,
        variableMeasured: ["PSA 10 reference price", "Verified PSA 10 population", "PSA 10 market cap"],
        distribution: [
          {
            "@type": "DataDownload",
            encodingFormat: "application/json",
            contentUrl: canonicalPublicUrl("/api/v1/market"),
          },
        ],
        publisher: { ...siteOrganization(), "@id": organizationId() },
      },
      {
        "@type": "ItemList",
        /* structured data 用 full count（100），唔係 slider 嘅 visible count */
        name: itemListName,
        numberOfItems: kind === "all" ? heatmapCards.filter((card) => card.viewRank > 0).length : listedCards.length,
        itemListElement: (kind === "all" ? heatmapCards.filter((card) => card.viewRank > 0) : listedCards).map((card, index) => ({
          "@type": "ListItem",
          position: index + 1,
          name: displayCardName(card, locale, t.status.unavailable),
          url: canonicalPublicUrl(`/card/${card.id}`),
        })),
      },
    ],
  };

  return (
    <div
      className={`page-shell market-page-shell${kind === "watchlist" ? " watchlist-page-shell" : ""}`}
      data-cardz-generation={snapshot.generation}
    >
      <StructuredData value={structuredData} />
      {/*
        * 首屏規矩（owner 2026-08-16 晚，hard）：市場頁一入到去就係熱力圖——只准一個標題
        * （熱力圖標題 = 唯一 H1）+ 總市值一行細字。GEO 嗰堆 hero 標題／定義／as-of／市況／
        * 消歧義**唔准喺首屏出**，但要留喺 HTML 俾搜尋同 AI 引擎讀，所以整段搬落頁尾
        * （Rankings 之後、Provenance 之前）做 h2 + 摺埋嘅 <details>。唔用 sr-only／display:none
        * 收埋——嗰種係「hidden text」，引擎會當垃圾；頁尾細字係正常內容。
        * watchlist 版冇熱力圖，hero 照舊喺頂做 H1（呢條 route 已經 308 走咗，純保底）。
        */}
      {kind === "watchlist" && (
        <section className="hero-section">
          <h1>{hero.title}</h1>
          <p className="hero-copy">{hero.body}</p>
        </section>
      )}
      {kind !== "watchlist" && (
        <Heatmap cards={heatmapCards} locale={locale} currency={currency} snapshot={snapshot} href={href} title={heatmapTitle} />
      )}
      <Rankings
        cards={listCards}
        locale={locale}
        currency={currency}
        snapshot={snapshot}
        href={href}
        watchlist={kind === "watchlist"}
        marketLabel={marketLabel}
        searchScope={kind === "pokemon" || kind === "one-piece" ? kind : "all"}
      />
      {/* 每頁 100／200／300／500 + 上下頁 + 「展示更多」：緊貼榜尾，唔准跌落頁尾說明之後。
          範圍字（#1–#200）要跟住接落去嘅實際行數走，所以傳 render 緊嗰批嘅頭尾 rank。 */}
      {pager ? (
        <RankingPager
          data={pager}
          firstRank={pager.firstRank}
          lastRank={listCards.filter((card) => card.viewRank > 0).at(-1)?.viewRank ?? pager.lastRank}
          loadedRows={listCards.length}
          hasMore={hasMore}
          loading={loadingMore}
          failed={feedFailed}
          onLoadMore={loadMore}
        />
      ) : null}
      {kind !== "watchlist" && (
        /*
         * 頁尾「關於呢個指數」：hero 標題（keyword-first，5 語）做 h2 + 一句 body，
         * 下面 <details> 摺埋定義／as-of／市況／消歧義。SSR 同 client 一樣閉合（冇 CLS）。
         * 日期用 formatObservationDate：鎖 UTC，SSR 同 hydrate 出同一串字。
         */
        <section className="index-about" aria-labelledby="index-about-heading">
          {/* 顯示上只得一行細字（summary）；標題／定義全部摺埋喺入面，engine 讀 raw HTML 照見。 */}
          <details className="intro-block">
            <summary>{fillTemplate(t.intro.about, { date: formatObservationDate(snapshot.effectiveAt, locale) })}</summary>
            <div className="intro-body">
              <h2 id="index-about-heading">{hero.title}</h2>
              <p className="index-about-body">{hero.body}</p>
              <p>{t.intro.definition}</p>
              <p>{fillTemplate(t.intro.asOf, { date: formatObservationDate(snapshot.effectiveAt, locale) })}</p>
              {introSummary && <p>{introSummary}</p>}
              {/*
                7 日最大升幅 chip（FE05 WS2）。moverCard 本來只係喺上面嗰句市況文字入面
                出現過名，冇自己嘅 render site；發光要有個實體，所以喺同一段補一粒 chip，
                順手多一條內鏈落嗰張卡。**唔落 heatmap tile／ranking row**：tile 由另一
                session 揸住，ranking row 100 行加發光 = 100 個 box-shadow。
                摺埋咗嘅 <details> 入面 = 唔喺 `/` 第一屏，符合首屏預算。
              */}
              {moverCard && (
                <p>
                  <Link className="mover-chip" href={href(`/card/${moverCard.id}`)}>
                    <span className="mover-chip-label">{t.periods["7d"]} {t.labels.change}</span>
                    <strong>{introCardName(moverCard, locale)}</strong>
                    <em>{formatPercent(moverCard.windows["7d"].changePct, locale)}</em>
                  </Link>
                </p>
              )}
              <p>{t.intro.disambiguation}</p>
              <p className="intro-links">
                <Link href={href("/methodology")}>{t.footerNav.methodology}</Link>
                <Link href={href("/faq")}>{t.footerNav.faq}</Link>
              </p>
            </div>
          </details>
        </section>
      )}
      <Provenance updatedAt={snapshot.effectiveAt} />
    </div>
  );
}
