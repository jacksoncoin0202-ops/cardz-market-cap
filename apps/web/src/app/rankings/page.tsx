import type { Metadata } from "next";
import { HubShell } from "@/components/seo-table";
import { i18nPageString } from "@/lib/hub-copy";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { rankingsIndexView } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * /rankings —— 12 條榜嘅索引頁。
 * 契約：標題／描述優先用 i18n.ts 嘅 pageTitles.rankings / pageDescriptions.rankings
 * （由另一位 owner 平行加緊，2026-08-16）；未落地就 fallback 返 hub-copy 嗰句，
 * 兩邊都唔會炸 build。
 */
interface RankingsIndexProps {
  searchParams: PageSearchParams;
}

export async function generateMetadata({ searchParams }: RankingsIndexProps): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  const view = rankingsIndexView(await loadMarketSnapshot(), locale);
  const bag = copy[locale];
  return marketMetadata(
    locale,
    i18nPageString(bag, "pageTitles", "rankings") ?? view.title,
    i18nPageString(bag, "pageDescriptions", "rankings") ?? view.description,
    view.path,
  );
}

export default async function RankingsIndexPage({ searchParams }: RankingsIndexProps) {
  const locale = await localeFromSearchParams(searchParams);
  const view = rankingsIndexView(await loadMarketSnapshot(), locale);
  return <HubShell view={view} />;
}
