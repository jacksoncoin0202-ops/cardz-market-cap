import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return marketMetadata(locale, copy[locale].pokemonHero.title, copy[locale].pokemonHero.body, "/pokemon");
}

export default async function PokemonPage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "pokemon");
  return <MarketPage kind="pokemon" snapshot={snapshot} />;
}
