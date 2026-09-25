import type { Metadata } from "next";
import { RankingSurface } from "@/components/ranking-surface";
import { defaultMarketMetadata, localeFromSearchParams, type PageSearchParams } from "@/lib/route-metadata";

export const revalidate = 300;

/*
 * 住喺 `app/page.tsx`，**唔喺** `app/(market)/`：組入面嘅 loading.tsx 會令
 * `notFound()` 改唔到 status。`?page=` / `?size=` 出範圍要真 404。
 */
export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  return defaultMarketMetadata(await localeFromSearchParams(searchParams));
}

export default async function HomePage({ searchParams }: { searchParams: PageSearchParams }) {
  const [params, locale] = await Promise.all([searchParams, localeFromSearchParams(searchParams)]);
  return <RankingSurface scope="all" locale={locale} params={params} />;
}
