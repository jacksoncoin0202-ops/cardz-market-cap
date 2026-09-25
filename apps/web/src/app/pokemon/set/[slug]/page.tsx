import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { HubShell } from "@/components/seo-table";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { setHubView } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * Pokémon set hub。所有揀卡／排序／JSON-LD 喺 lib/seo-routes.ts 嘅 setHubView，
 * 呢度淨係接 route param。slug 揾唔到 set = 真 404（呢個 route 唔喺 (market) group
 * 下面，冇 loading.tsx 罩住，notFound() 出到 status 404 —— 同 card/[id] 一樣道理）。
 */
interface SetRouteProps {
  params: Promise<{ slug: string }>;
  searchParams: PageSearchParams;
}

async function requireView(params: SetRouteProps["params"], searchParams: PageSearchParams) {
  const [{ slug }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const view = setHubView(await loadMarketSnapshot(), "pokemon", slug, locale);
  if (!view) notFound();
  return { view, locale };
}

export async function generateMetadata({ params, searchParams }: SetRouteProps): Promise<Metadata> {
  const { view, locale } = await requireView(params, searchParams);
  return marketMetadata(locale, view.title, view.description, view.path);
}

export default async function PokemonSetPage({ params, searchParams }: SetRouteProps) {
  const { view } = await requireView(params, searchParams);
  return <HubShell view={view} />;
}
