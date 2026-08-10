import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return marketMetadata(locale, copy[locale].onePieceHero.title, copy[locale].onePieceHero.body, "/one-piece");
}

export default async function OnePiecePage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "one-piece");
  return <MarketPage kind="one-piece" snapshot={snapshot} />;
}
