import type { Metadata } from "next";
import { BoxDetail } from "@/components/box-detail";
import { copy } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { boxDetailSnapshot, loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

interface BoxRouteProps {
  params: Promise<{ id: string }>;
  searchParams: PageSearchParams;
}

export async function generateMetadata({ params, searchParams }: BoxRouteProps): Promise<Metadata> {
  const [{ id }, locale] = await Promise.all([
    params,
    localeFromSearchParams(searchParams),
  ]);
  const snapshot = boxDetailSnapshot(await loadMarketSnapshot(), id);
  const product = snapshot.sealed?.products[0];
  const t = copy[locale];
  const title = product ? `${product.name[locale] || product.name.en} · ${t.nav.box}` : t.boxHero.title;
  const description = product?.story?.[locale] || product?.story?.en || t.boxHero.body;
  return marketMetadata(locale, title, description, `/box/${id}`);
}

export default async function BoxProductPage({ params }: BoxRouteProps) {
  const { id } = await params;
  const snapshot = boxDetailSnapshot(await loadMarketSnapshot(), id);
  const product = snapshot.sealed?.products[0] ?? null;
  return <BoxDetail product={product} snapshot={snapshot} />;
}
