import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BoxDetail } from "@/components/box-detail";
import { formatMoney, formatObservationDate } from "@/lib/format";
import { fillTemplate, fitTitle, geoCopy } from "@/lib/related-cards";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { boxDetailSnapshot, loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

interface BoxRouteProps {
  params: Promise<{ id: string }>;
  searchParams: PageSearchParams;
}

/*
 * 揾唔到 box = HTTP 404，唔係 200 配「BOX data is being prepared」（soft-404，同 card/[id] 一樣嘅坑）。
 * generateMetadata 同 page 都行呢個 helper，「有冇呢個 box」全 route 得一個判準。
 * ⚠️ 唔准喺 app/box/ 加 loading.tsx：shell 一沖出街 status 就鎖死 200，notFound() 改唔到（見 (market)/loading.tsx）。
 */
async function requireBox(id: string) {
  const snapshot = boxDetailSnapshot(await loadMarketSnapshot(), id);
  const product = snapshot.sealed?.products[0];
  if (!product) notFound();
  return { snapshot, product };
}

export async function generateMetadata({ params, searchParams }: BoxRouteProps): Promise<Metadata> {
  const [{ id }, locale] = await Promise.all([
    params,
    localeFromSearchParams(searchParams),
  ]);
  const { product, snapshot } = await requireBox(id);
  const geo = geoCopy[locale];
  const name = product.name[locale] || product.name.en;
  /*
   * 關鍵詞行頭（owner 2026-08-16）：原本個 title 係「{名} · BOX」，一個目標詞都冇。
   * Description 唔再用小故事開頭 —— 呢版嘅查詢意圖係「幾錢」，所以第一句就係
   * 參考價同日期（真數，由 snapshot 出）。冇價就明講未有價，唔准靜靜地當有。
   * 貨幣一律 USD：canonical description 唔跟 ?currency 郁，郁咗就每個貨幣一份 meta。
   */
  const title = fitTitle(geo.boxDetailTitle, name);
  const description = product.priceUsd.value !== null
    ? fillTemplate(geo.boxDetailDescription, {
      name,
      price: formatMoney(product.priceUsd.value, "USD", snapshot.rates, locale),
      date: formatObservationDate(product.priceUsd.asOf ?? snapshot.effectiveAt, locale),
    })
    : fillTemplate(geo.boxDetailDescriptionNoPrice, { name });
  return marketMetadata(locale, title, description, `/box/${id}`);
}

export default async function BoxProductPage({ params }: BoxRouteProps) {
  const { id } = await params;
  const { snapshot, product } = await requireBox(id);
  return <BoxDetail product={product} snapshot={snapshot} />;
}
