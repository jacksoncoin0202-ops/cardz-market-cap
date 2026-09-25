import type { Metadata } from "next";
import { RankingSurface } from "@/components/ranking-surface";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";

export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  /* SERP 出 seo.onePiece（帶 head term）。版面零改動：H1 一路都係熱力圖標題，唔係 onePieceHero.title。 */
  const t = copy[locale].seo.onePiece;
  return marketMetadata(locale, t.title, t.description, "/one-piece");
}

export default async function OnePiecePage({ searchParams }: { searchParams: PageSearchParams }) {
  const [params, locale] = await Promise.all([searchParams, localeFromSearchParams(searchParams)]);
  return <RankingSurface scope="one-piece" locale={locale} params={params} />;
}
