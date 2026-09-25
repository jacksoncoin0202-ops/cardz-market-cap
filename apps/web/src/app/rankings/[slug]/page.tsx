import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { HubShell } from "@/components/seo-table";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { rankingView } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * /rankings/[slug] —— 12 條認可 slug 之外一律 404（rankingSpec() 回 null）。
 * 唔用 catch-all fuzzy match：亂 slug 出 200 空白頁係 SEO 自殺。
 */
interface RankingRouteProps {
  params: Promise<{ slug: string }>;
  searchParams: PageSearchParams;
}

async function requireView(params: RankingRouteProps["params"], searchParams: PageSearchParams) {
  const [{ slug }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const view = rankingView(await loadMarketSnapshot(), slug, locale);
  if (!view) notFound();
  return { view, locale };
}

export async function generateMetadata({ params, searchParams }: RankingRouteProps): Promise<Metadata> {
  const { view, locale } = await requireView(params, searchParams);
  return marketMetadata(locale, view.title, view.description, view.path);
}

export default async function RankingPage({ params, searchParams }: RankingRouteProps) {
  const { view } = await requireView(params, searchParams);
  return <HubShell view={view} />;
}
