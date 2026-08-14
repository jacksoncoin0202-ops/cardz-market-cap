import type { Metadata } from "next";
import { Suspense } from "react";
import { BoxMarketPage } from "@/components/box-market-page";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { boxListSnapshot, loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  return marketMetadata(locale, copy[locale].boxHero.title, copy[locale].boxHero.body, "/box");
}

export default async function BoxPage() {
  const snapshot = boxListSnapshot(await loadMarketSnapshot());
  return (
    <Suspense>
      <BoxMarketPage snapshot={snapshot} />
    </Suspense>
  );
}
