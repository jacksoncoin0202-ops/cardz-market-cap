import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { defaultMarketMetadata, localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  return defaultMarketMetadata(await localeFromSearchParams(searchParams));
}

export default async function HomePage() {
  const snapshot = scopeSnapshot(await loadMarketSnapshot(), "all");
  return <MarketPage kind="all" snapshot={snapshot} />;
}
