import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { HubShell } from "@/components/seo-table";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { setHubView } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/* One Piece set hub。同 /pokemon/set/[slug] 一樣，分別只喺 game scope。 */
interface SetRouteProps {
  params: Promise<{ slug: string }>;
  searchParams: PageSearchParams;
}

async function requireView(params: SetRouteProps["params"], searchParams: PageSearchParams) {
  const [{ slug }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const view = setHubView(await loadMarketSnapshot(), "one-piece", slug, locale);
  if (!view) notFound();
  return { view, locale };
}

export async function generateMetadata({ params, searchParams }: SetRouteProps): Promise<Metadata> {
  const { view, locale } = await requireView(params, searchParams);
  return marketMetadata(locale, view.title, view.description, view.path);
}

export default async function OnePieceSetPage({ params, searchParams }: SetRouteProps) {
  const { view } = await requireView(params, searchParams);
  return <HubShell view={view} />;
}
