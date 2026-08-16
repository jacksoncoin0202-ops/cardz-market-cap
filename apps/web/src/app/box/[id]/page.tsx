import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { BoxDetail } from "@/components/box-detail";
import { copy } from "@/lib/i18n";
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
  const { product } = await requireBox(id);
  const t = copy[locale];
  const title = `${product.name[locale] || product.name.en} · ${t.nav.box}`;
  const description = product.story?.[locale] || t.boxHero.body;
  return marketMetadata(locale, title, description, `/box/${id}`);
}

export default async function BoxProductPage({ params }: BoxRouteProps) {
  const { id } = await params;
  const { snapshot, product } = await requireBox(id);
  return <BoxDetail product={product} snapshot={snapshot} />;
}
