import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CardDetail } from "@/components/card-detail";
import { displayCardName } from "@/lib/card-name";
import { copy, localizedCardLanguage } from "@/lib/i18n";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, singleCardSnapshot } from "@/lib/server-snapshot";

export const revalidate = 300;

/*
 * 揾唔到卡 = HTTP 404，唔係 200 配一版「No eligible cards」。
 *
 * 原本個判斷淨係活喺 client component 入面（card-detail.tsx `if (!card)`），
 * route 由頭到尾唔知道張卡唔存在，所以 Next 照回 200 —— 典型 soft-404：舊 link、
 * 被 prune 走嘅卡、亂打嘅 id 全部變成可索引嘅空白頁。同一個 lookup 喺 API 側
 * （api/v1/cards/[id]/route.ts:10）一直做啱咗回 404，錯嘅係頁面呢邊冇 gate。
 *
 * 補返 `notFound()` 之後仲要再拆多一層：實測 production build 出 not-found 個
 * body，但 status 仍然係 200。因為 `app/loading.tsx` 本身係 root-level，Next 會
 * 用佢喺 layout 下面包一個 Suspense，shell（連 200）即刻沖出街，page 之後先
 * resolve —— 全 app 任何 `notFound()` 都改唔到 status。已經將個 skeleton 搬落
 * `app/(market)/loading.tsx`，`card/[id]` 唔再喺佢下面，呢句先真係回到 404。
 */
interface CardRouteProps {
  params: Promise<{ id: string }>;
  searchParams: PageSearchParams;
}

/* 兩個 export 都行呢個 helper，所以「有冇呢張卡」全 route 得一個判準。 */
async function requireCard(id: string) {
  const snapshot = singleCardSnapshot(await loadMarketSnapshot(), id);
  const card = snapshot.top100[0];
  if (!card) notFound();
  return { snapshot, card };
}

export async function generateMetadata({ params, searchParams }: CardRouteProps): Promise<Metadata> {
  const [{ id }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const { card } = await requireCard(id);
  const labels = copy[locale].labels;
  /*
   * owner 2026-08-16：<title> 跟 UI 語言出當地官方譯名（displayCardName，同 H1／sheet／熱力圖一致）；
   * 非英文 locale 而譯名同英文唔同時，英文 officialName 跟喺後面做搜尋／辨識 anchor。
   * 印刷語言只係 disambiguation suffix。
   */
  const printLanguage = card.cardLanguage
    ? labels.printLanguage.replace("{language}", localizedCardLanguage(card.cardLanguage, locale))
    : null;
  const localName = displayCardName(card, locale, card.officialName || labels.viewCard);
  const baseTitle = card.officialName && localName !== card.officialName ? `${localName}（${card.officialName}）` : localName;
  const title = printLanguage ? `${baseTitle} · ${printLanguage}` : baseTitle;
  const description = card.story?.[locale] || labels.viewCard;
  // 每張卡出自己嗰張 OG（卡名 / set / 市值 / PSA 10 價同 POP）。
  return marketMetadata(
    locale,
    title,
    description,
    `/card/${id}`,
    `/api/og/card/${encodeURIComponent(id)}`,
    localName || "CardZ Marketcap",
  );
}

export default async function CardPage({ params }: CardRouteProps) {
  const { id } = await params;
  const { snapshot } = await requireCard(id);
  return <CardDetail id={id} snapshot={snapshot} />;
}
