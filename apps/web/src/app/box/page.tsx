import type { Metadata } from "next";
import { Suspense } from "react";
import { BoxMarketPage } from "@/components/box-market-page";
import { formatInteger, formatObservationDate } from "@/lib/format";
import { copy } from "@/lib/i18n";
import { fillTemplate, geoCopy } from "@/lib/related-cards";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { boxListSnapshot, loadMarketSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * Title / description 改成關鍵詞行頭、品牌行尾（owner 2026-08-16）。
 * 原本個 title 係 hero 文案「Sold-first prices for sealed booster boxes」——講得啱，
 * 但一個目標詞都冇（booster box price / BOX 相場 / 原盒價格 / 부스터 박스 시세）。
 * Description 入面兩個數（有價 / 總數）同日期由 snapshot 填，唔准寫死。
 */
export async function generateMetadata({ searchParams }: { searchParams: PageSearchParams }): Promise<Metadata> {
  const locale = await localeFromSearchParams(searchParams);
  const geo = geoCopy[locale];
  const block = (await loadMarketSnapshot()).sealed;
  const description = block
    ? fillTemplate(geo.boxDescription, {
      priced: formatInteger(block.coverage.priced, locale),
      total: formatInteger(block.coverage.total, locale),
      date: formatObservationDate(block.asOf, locale),
    })
    : copy[locale].boxHero.body;
  return marketMetadata(locale, geo.boxTitle, description, "/box");
}

export default async function BoxPage() {
  const snapshot = boxListSnapshot(await loadMarketSnapshot());
  return (
    <Suspense>
      <BoxMarketPage snapshot={snapshot} />
    </Suspense>
  );
}
