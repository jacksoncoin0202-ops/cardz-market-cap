"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Heatmap } from "./heatmap";
import { Provenance } from "./provenance";
import { Rankings } from "./rankings";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy, type Copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { MarketCardView, MarketCatalogItem, MarketViewSnapshot } from "@/lib/types";

type MarketPageKind = "all" | "pokemon" | "one-piece" | "watchlist";

export function marketHeatmapTitle(kind: MarketPageKind, cardCount: number, t: Copy): string {
  const marketTitle = kind === "pokemon"
    ? t.heatmap.pokemonTitle
    : kind === "one-piece"
      ? t.heatmap.onePieceTitle
      : t.heatmap.title;
  return marketTitle.replace("{count}", String(cardCount));
}

export function MarketPage({
  kind,
  snapshot,
  catalog,
}: {
  kind: MarketPageKind;
  snapshot: MarketViewSnapshot;
  catalog?: MarketCatalogItem[];
}) {
  const { locale, currency, href } = useMarketSettings();
  const t = copy[locale];
  const hero = kind === "pokemon" ? t.pokemonHero : kind === "one-piece" ? t.onePieceHero : kind === "watchlist" ? t.watchlistHero : t.hero;
  const [cards, setCards] = useState(snapshot.top100);
  const [loadingMore, setLoadingMore] = useState(false);
  const loadingRef = useRef(false);
  const sentinelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setCards(snapshot.top100);
  }, [snapshot]);

  const catalogCount = catalog?.length ?? snapshot.top100.length;
  const remaining = catalogCount - cards.length;
  const canLoadMore = kind !== "watchlist" && remaining > 0;

  const loadMore = useCallback(async () => {
    if (kind === "watchlist" || loadingRef.current) return;
    loadingRef.current = true;
    setLoadingMore(true);
    try {
      const response = await fetch(`/api/v1/market?scope=${kind}`);
      if (!response.ok) return;
      const payload = (await response.json()) as { cards?: MarketCardView[] };
      if (Array.isArray(payload.cards) && payload.cards.length) {
        setCards(payload.cards);
      }
    } finally {
      loadingRef.current = false;
      setLoadingMore(false);
    }
  }, [kind]);

  useEffect(() => {
    if (!canLoadMore || !sentinelRef.current) return;
    const node = sentinelRef.current;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) void loadMore();
    }, { rootMargin: "80px" });
    observer.observe(node);
    return () => observer.disconnect();
  }, [canLoadMore, loadMore]);

  /* 傳 raw title（保留 {count}）俾 Heatmap 自己按 visibleCards.length replace，
     咁 Tiles slider 改咗數量，標題同 Share image 都會跟住變。 */
  const heatmapTitle = kind === "pokemon"
    ? t.heatmap.pokemonTitle
    : kind === "one-piece"
      ? t.heatmap.onePieceTitle
      : t.heatmap.title;
  const marketLabel = kind === "pokemon" ? t.nav.pokemon : kind === "one-piece" ? t.nav.onePiece : t.nav.all;
  const jsonLdCards = catalog ?? cards;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Dataset",
        name: hero.title,
        description: hero.body,
        dateModified: snapshot.effectiveAt,
        measurementTechnique: "PSA 10 reference price multiplied by verified PSA 10 population",
        publisher: { "@type": "Organization", name: "CardZ Marketcap", url: absolutePublicUrl(href("/")) },
      },
      {
        "@type": "ItemList",
        /* structured data 用 full catalog（100），唔係 slider / 首屏 30 */
        name: marketHeatmapTitle(kind, jsonLdCards.length, t),
        numberOfItems: jsonLdCards.length,
        itemListElement: jsonLdCards.map((card) => ({
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
      {canLoadMore ? (
        <div ref={sentinelRef}>
          <button
            type="button"
            className="box-show-more"
            onClick={() => void loadMore()}
            disabled={loadingMore}
          >
            {t.box.showMore.replace("{count}", String(remaining))}
          </button>
        </div>
      ) : null}
      <Provenance updatedAt={snapshot.effectiveAt} />
    </div>
  );
}
