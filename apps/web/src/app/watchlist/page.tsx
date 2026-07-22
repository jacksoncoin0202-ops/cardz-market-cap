import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return marketMetadata(locale, copy[locale].watchlistHero.title, copy[locale].watchlistHero.body, "/watchlist");
}

export default async function WatchlistPage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "watchlist");
  return <MarketPage kind="watchlist" snapshot={snapshot} />;
}
