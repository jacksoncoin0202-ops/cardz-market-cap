import type { Metadata } from "next";
import { CardDetail } from "@/components/card-detail";
import { copy, localizedCardLanguage } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, singleCardSnapshot } from "@/lib/server-snapshot";

interface CardRouteProps {
  params: Promise<{ id: string }>;
  searchParams: PageSearchParams;
}

export async function generateMetadata({ params, searchParams }: CardRouteProps): Promise<Metadata> {
  const [{ id }, locale, snapshot] = await Promise.all([params, localeFromSearchParams(searchParams), loadMarketSnapshot()]);
  const card = [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id);
  const labels = copy[locale].labels;
  /*
   * JA / EN 孖卡係兩張唔同嘅卡，但 `name[locale]` 兩張一模一樣 —— 之前 title、og:title、
   * canonical description 全部 byte-identical，搜尋引擎當重複內容。
   * 有印刷語言就綴上「日文版 / English print」，冇就照舊，唔准出 unavailable。
   */
  const printLanguage = card?.cardLanguage
    ? labels.printLanguage.replace("{language}", localizedCardLanguage(card.cardLanguage, locale))
    : null;
  const baseTitle = card?.name[locale] || labels.viewCard;
  const title = printLanguage ? `${baseTitle} · ${printLanguage}` : baseTitle;
  const description = card?.story[locale] || labels.viewCard;
  // 每張卡出自己嗰張 OG（卡名 / set / 市值 / PSA 10 價同 POP），揾唔到卡就回落品牌預設圖。
  return marketMetadata(locale, title, description, `/card/${id}`, `/api/og/card/${encodeURIComponent(id)}`);
}

export default async function CardPage({ params }: CardRouteProps) {
  const { id } = await params;
  return <CardDetail id={id} snapshot={singleCardSnapshot(await loadMarketSnapshot(), id)} />;
}
