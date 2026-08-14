import type { Metadata } from "next";
import { MarketPage } from "@/components/market-page";
import { defaultMarketMetadata, localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";
import { firstPaintScope, loadMarketSnapshot, scopeSnapshot } from "@/lib/server-snapshot";

/* 榜單資料一日只翻一次（夜鏈 03:30 出新 ranking generation），所以唔需要逐個 request 重出。 */
export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  return defaultMarketMetadata(await localeFromSearchParams(searchParams));
}

export default async function HomePage() {
  const { snapshot, catalog } = firstPaintScope(scopeSnapshot(await loadMarketSnapshot(), "all"));
  return <MarketPage kind="all" snapshot={snapshot} catalog={catalog} />;
}
