"use client";

import { Heatmap } from "./heatmap";
import { Provenance } from "./provenance";
import { Rankings } from "./rankings";
import { absolutePublicUrl, siteOrganization, StructuredData } from "./structured-data";
import { copy, type Copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { MarketViewSnapshot } from "@/lib/types";

type MarketPageKind = "all" | "pokemon" | "one-piece" | "watchlist";

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
  /* 傳 raw title（保留 {count}）俾 Heatmap 自己按 visibleCards.length replace，
     咁 Tiles slider 改咗數量，標題同 Share image 都會跟住變。 */
  const heatmapTitle = kind === "pokemon"
    ? t.heatmap.pokemonTitle
    : kind === "one-piece"
      ? t.heatmap.onePieceTitle
      : t.heatmap.title;
  const marketLabel = kind === "pokemon" ? t.nav.pokemon : kind === "one-piece" ? t.nav.onePiece : t.nav.all;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Dataset",
        name: hero.title,
        description: hero.body,
        dateModified: snapshot.effectiveAt,
        measurementTechnique: "PSA 10 reference price multiplied by verified PSA 10 population",
        publisher: siteOrganization(absolutePublicUrl(href("/"))),
      },
      {
        "@type": "ItemList",
        /* structured data 用 full count（100），唔係 slider 嘅 visible count */
        name: marketHeatmapTitle(kind, cards.length, t),
        numberOfItems: cards.length,
        itemListElement: cards.map((card) => ({
          "@type": "ListItem",
          position: card.viewRank,
          name: card.officialName || t.status.unavailable,
          url: absolutePublicUrl(href(`/card/${card.id}`)),
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
      {kind === "watchlist" && (
        <section className="hero-section">
          <h1>{hero.title}</h1>
          <p className="hero-copy">{hero.body}</p>
        </section>
      )}
      {kind !== "watchlist" && (
        <Heatmap cards={cards} locale={locale} currency={currency} snapshot={snapshot} href={href} title={heatmapTitle} />
      )}
      <Rankings cards={cards} locale={locale} currency={currency} snapshot={snapshot} href={href} watchlist={kind === "watchlist"} marketLabel={marketLabel} />
      <Provenance updatedAt={snapshot.effectiveAt} />
    </div>
  );
}
