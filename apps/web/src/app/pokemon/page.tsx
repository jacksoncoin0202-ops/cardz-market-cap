import type { Metadata } from "next";
import { RankingSurface } from "@/components/ranking-surface";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";

export const revalidate = 300;

export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  /* SERP 出 seo.pokemon（帶 head term）。版面零改動：H1 一路都係熱力圖標題，唔係 pokemonHero.title。 */
  const t = copy[locale].seo.pokemon;
  return marketMetadata(locale, t.title, t.description, "/pokemon");
}

export default async function PokemonPage({ searchParams }: { searchParams: PageSearchParams }) {
  const [params, locale] = await Promise.all([searchParams, localeFromSearchParams(searchParams)]);
  return <RankingSurface scope="pokemon" locale={locale} params={params} />;
}
