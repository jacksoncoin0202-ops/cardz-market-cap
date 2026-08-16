import type { Metadata } from "next";
import { HubShell } from "@/components/seo-table";
import { i18nPageString } from "@/lib/hub-copy";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { marketReportView } from "@/lib/seo-routes";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * /market-report —— 每日 snapshot 自動生成嘅市況報告（Article JSON-LD）。
 * 冇人手編輯步驟：所有數字（總市值、集中度、7d/30d 升跌、pop）由 marketReportView
 * 由 snapshot 計，日日 revalidate 就會自己更新。頁底有明文寫住呢點，唔准扮人手評論。
 */
interface MarketReportProps {
  searchParams: PageSearchParams;
}

export async function generateMetadata({ searchParams }: MarketReportProps): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  const view = marketReportView(await loadMarketSnapshot(), locale);
  const bag = copy[locale];
  return marketMetadata(
    locale,
    i18nPageString(bag, "pageTitles", "marketReport") ?? view.title,
    i18nPageString(bag, "pageDescriptions", "marketReport") ?? view.description,
    view.path,
  );
}

export default async function MarketReportPage({ searchParams }: MarketReportProps) {
  const locale = await localeFromSearchParams(searchParams);
  const view = marketReportView(await loadMarketSnapshot(), locale);
  return <HubShell view={view} />;
}
