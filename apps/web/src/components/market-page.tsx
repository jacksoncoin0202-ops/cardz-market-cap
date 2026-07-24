"use client";

import { GradingPulse } from "./grading-pulse";
import { Heatmap } from "./heatmap";
import { Rankings } from "./rankings";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { MarketViewSnapshot } from "@/lib/types";

export function MarketPage({ kind, snapshot }: { kind: "all" | "pokemon" | "one-piece" | "watchlist"; snapshot: MarketViewSnapshot }) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];
  const hero = kind === "pokemon" ? t.pokemonHero : kind === "one-piece" ? t.onePieceHero : kind === "watchlist" ? t.watchlistHero : t.hero;
  const cards = snapshot.top100;
  const heatmapTitle = kind === "pokemon" ? t.heatmap.pokemonTitle : kind === "one-piece" ? t.heatmap.onePieceTitle : t.heatmap.title;
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
      },
      {
        "@type": "ItemList",
        name: heatmapTitle,
        numberOfItems: cards.length,
        itemListElement: cards.map((card) => ({
          "@type": "ListItem",
          position: card.rank,
          name: card.name[locale] || t.status.unavailable,
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
          <p className="section-kicker">{hero.eyebrow}</p>
          <h1>{hero.title}</h1>
          <p className="hero-copy">{hero.body}</p>
        </section>
      )}
      {kind !== "watchlist" && (
        <Heatmap cards={cards} locale={locale} currency={currency} snapshot={snapshot} href={href} title={heatmapTitle} eyebrow={hero.eyebrow} />
      )}
      {kind !== "watchlist" && <GradingPulse cards={cards} locale={locale} period={period} />}
      <Rankings cards={cards} locale={locale} currency={currency} snapshot={snapshot} href={href} watchlist={kind === "watchlist"} marketLabel={marketLabel} />
    </div>
  );
}
