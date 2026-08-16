"use client";

import Link from "next/link";
import { Heatmap } from "./heatmap";
import { Provenance } from "./provenance";
import { Rankings } from "./rankings";
import { canonicalPublicUrl, datasetId, organizationId, siteOrganization, StructuredData } from "./structured-data";
import { displayCardName } from "@/lib/card-name";
import { formatMoney, formatObservationDate, formatPercent } from "@/lib/format";
import { copy, type Copy } from "@/lib/i18n";
import { cardSubject, fillTemplate } from "@/lib/related-cards";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";

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

export function MarketPage({ kind, snapshot }: { kind: MarketPageKind; snapshot: MarketViewSnapshot }) {
  const { locale, currency, href } = useMarketSettings();
  const t = copy[locale];
  const hero = kind === "pokemon" ? t.pokemonHero : kind === "one-piece" ? t.onePieceHero : kind === "watchlist" ? t.watchlistHero : t.hero;
  const cards = snapshot.top100;
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
        * hero 以前淨係 watchlist 出，即係 /、/pokemon、/one-piece 三版嘅 H1 係熱力圖標題
        * （「Top 100 market heatmap」）——搜尋／AI 睇落成個網站冇講過自己係咩。而家四個
        * kind 都出返 hero 做唯一 H1（熱力圖降做 h2），下面貼一段 3–5 行嘅可引用定義。
        * 首頁聲線係 art first，所以 intro-block 刻意細字、淡色，唔准搶熱力圖。
        */}
      <section className="hero-section">
        <h1>{hero.title}</h1>
        <p className="hero-copy">{hero.body}</p>
        {/*
          * <details> 默認摺埋（所有 viewport，SSR 同 client 一樣，冇 CLS）：實測 390 寬展開版
          * 令熱力圖跌到 ~1240px 先出現，違反「藝術先行／熱力圖第一屏」。摺埋後段字仍然
          * 喺 HTML 入面——Google 同 AI 引擎讀 raw HTML，唔理 open 與否；summary 行本身
          * 已帶 as-of 日期。日期用 formatObservationDate：鎖 UTC，SSR 同 hydrate 出同一串字。
          */}
        <details className="intro-block">
          <summary>{fillTemplate(t.intro.about, { date: formatObservationDate(snapshot.effectiveAt, locale) })}</summary>
          <div className="intro-body">
            <p>{t.intro.definition}</p>
            <p>{fillTemplate(t.intro.asOf, { date: formatObservationDate(snapshot.effectiveAt, locale) })}</p>
            {introSummary && <p>{introSummary}</p>}
            <p>{t.intro.disambiguation}</p>
            <p className="intro-links">
              <Link href={href("/methodology")}>{t.footerNav.methodology}</Link>
              <Link href={href("/faq")}>{t.footerNav.faq}</Link>
            </p>
          </div>
        </details>
      </section>
      {kind !== "watchlist" && (
        <Heatmap cards={heatmapCards} locale={locale} currency={currency} snapshot={snapshot} href={href} title={heatmapTitle} />
      )}
      <Rankings
        cards={cards}
        locale={locale}
        currency={currency}
        snapshot={snapshot}
        href={href}
        watchlist={kind === "watchlist"}
        marketLabel={marketLabel}
        searchScope={kind === "pokemon" || kind === "one-piece" ? kind : "all"}
      />
      <Provenance updatedAt={snapshot.effectiveAt} />
    </div>
  );
}
