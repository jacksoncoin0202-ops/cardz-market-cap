import type { Metadata } from "next";
import { CardDetail } from "@/components/card-detail";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, singleCardSnapshot } from "@/lib/server-snapshot";

interface CardRouteProps {
  params: Promise<{ id: string }>;
  searchParams: PageSearchParams;
}

export async function generateMetadata({ params, searchParams }: CardRouteProps): Promise<Metadata> {
  const [{ id }, locale, snapshot] = await Promise.all([params, localeFromSearchParams(searchParams), loadMarketSnapshot()]);
  const card = [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id);
  const title = card?.name[locale] || copy[locale].labels.viewCard;
  const description = card?.story[locale] || copy[locale].labels.viewCard;
  // 每張卡出自己嗰張 OG（卡名 / set / 市值 / PSA 10 價同 POP），揾唔到卡就回落品牌預設圖。
  return marketMetadata(locale, title, description, `/card/${id}`, `/api/og/card/${encodeURIComponent(id)}`);
}

export default async function CardPage({ params }: CardRouteProps) {
  const { id } = await params;
  return <CardDetail id={id} snapshot={singleCardSnapshot(await loadMarketSnapshot(), id)} />;
}
