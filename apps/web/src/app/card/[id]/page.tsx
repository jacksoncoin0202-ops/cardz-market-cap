import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CardDetail } from "@/components/card-detail";
import { displayCardName } from "@/lib/card-name";
import { formatInteger, formatMoney, formatObservationDate } from "@/lib/format";
import { copy, localizedCardLanguage } from "@/lib/i18n";
import { cardFactSentence, cardSubject, relatedCards } from "@/lib/related-cards";
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

/*
 * 兩個 export 都行呢個 helper，所以「有冇呢張卡」全 route 得一個判準。
 * `full` 一齊回：`related` 要完整 snapshot 先計到，而呢度已經 load 咗一次 ——
 * 唔回出去嘅話 page 就要再 `await loadMarketSnapshot()` 多一次，live-db mode
 * （`server-snapshot.ts:275-285` 冇 memo）即係同一個 request 打兩次 DB。
 */
async function requireCard(id: string) {
  const full = await loadMarketSnapshot();
  const snapshot = singleCardSnapshot(full, id);
  const card = snapshot.top100[0];
  if (!card) notFound();
  return { full, snapshot, card };
}

export async function generateMetadata({ params, searchParams }: CardRouteProps): Promise<Metadata> {
  const [{ id }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const { card, snapshot } = await requireCard(id);
  const t = copy[locale];
  const labels = t.labels;
  /*
   * owner 2026-08-16：<title> 跟 UI 語言出當地官方譯名（displayCardName，同 H1／sheet／熱力圖一致）；
   * 非英文 locale 而譯名同英文唔同時，英文 officialName 跟喺後面做搜尋／辨識 anchor。
   * 印刷語言只係 disambiguation suffix。
   */
  const printLanguage = card.cardLanguage
    ? labels.printLanguage.replace("{language}", localizedCardLanguage(card.cardLanguage, locale))
    : null;
  const localName = displayCardName(card, locale, card.officialName || labels.viewCard);
  /*
   * 60 字上限（GEO，owner 2026-08-16）：長 PSA 名試過整到成條 <title> 127 字，出街只
   * 見到頭幾十字，連 root layout 貼嘅「| CardZ Marketcap」都斬埋。所以由外向內剝，
   * 剝嘅一定係「可以冇」嗰截，唔係身份：
   *   1. 非英文 locale 嗰個「（英文 officialName）」係搜尋 anchor，爆咗就淨返譯名；
   *   2. 「· 日文版」純粹 disambiguation，爆咗直接唔出。
   * en 個 officialName 本身就係張卡嘅身份，一個字都唔准斬（斬咗就唔係嗰張卡）。
   */
  const TITLE_MAX = 60;
  const withOfficial = card.officialName && localName !== card.officialName
    ? `${localName}（${card.officialName}）`
    : localName;
  const baseTitle = withOfficial.length > TITLE_MAX ? localName : withOfficial;
  const withPrintLanguage = printLanguage ? `${baseTitle} · ${printLanguage}` : baseTitle;
  const title = withPrintLanguage.length > TITLE_MAX ? baseTitle : withPrintLanguage;
  /*
   * description 由「小故事開頭 160 字」改做關鍵詞行先嘅事實句（GEO，owner 2026-08-16）：
   * 卡名／set／編號／市值／PSA 10 價／POP／日期／名次全部喺可見範圍入面。直接行卡頁
   * 上面睇得見嗰句可引用事實（cardFactSentence），唔另開第六個版本——同一張卡喺
   * <meta> 同頁面上面一定講同一句（AGENTS 規矩 13）。三個數缺一就跌返小故事。
   * `total: null`：講「共 N 張」要完整 snapshot 兼多 20 幾字，plainDescription 160 字
   * 一斬就連名次都冇埋，寧願淨講名次。
   */
  const factDate = card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot.effectiveAt;
  const capValue = card.marketCap.value;
  const priceValue = card.pricePsa10.value;
  const popValue = card.populationPsa10.value;
  const factName = (localName && localName !== card.officialName ? localName : cardSubject(card)) || localName;
  const description = capValue !== null && priceValue !== null && popValue !== null
    ? cardFactSentence(locale, {
      name: factName,
      set: card.setName[locale] || card.setName.en || t.status.unavailable,
      num: card.collectorNumber,
      cap: formatMoney(capValue, "USD", snapshot.rates, locale, true),
      price: formatMoney(priceValue, "USD", snapshot.rates, locale),
      pop: formatInteger(popValue, locale),
      date: formatObservationDate(factDate, locale),
      rank: card.marketRank,
      total: null,
      tcg: card.tcg === "One Piece" ? t.nav.onePiece : t.nav.pokemon,
    })
    : card.story?.[locale] || labels.viewCard;
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
  /*
   * 呢一版**冇** `<Suspense>`，亦**冇** `app/card/loading.tsx` —— 兩樣都試過，兩樣都
   * 唔可以要（FE05 WS4，review 2026-08-17 收返）：
   *
   * 1. `app/card/loading.tsx`：segment loading 喺 layout 下面包 Suspense，shell 一沖
   *    出街 HTTP status 就鎖死 200，`notFound()` 變 soft-404（見上面 :13-26）。
   * 2. route 入面自己包 `<Suspense fallback={<CardDetailSkeleton/>}>`：`notFound()`
   *    contract 守得住（`requireCard` await 喺 boundary 外面就得），但 **crawler shell
   *    炸咗**。`CardDetail` 個 client subtree 有嘢喺 SSR 期間 suspend（`useMarketSettings`
   *    → `useSearchParams()`，use-market-settings.ts:121），一 suspend 就係成個
   *    boundary 嘅內容跌入 `<div hidden id="S:1">`，要行 `$RC` script 先 reveal。
   *    實測（temp/fe05/ws4-fix/scan.json）：有 boundary → h1 @byte 16963、第一個 hidden
   *    @byte 12486（h1 唔喺 shell）；拆走 boundary → h1 @byte 10057、第一個 hidden
   *    @byte 51140（h1 喺 shell）。唔行 JS 嘅 AI crawler 淨係讀 shell，而卡頁係 GEO
   *    主力頁（docs/CLOUDFLARE_AI_CRAWLER_UNBLOCK_20260816.md）。
   *    換返嚟嗰個骨架又證實冇出現過：CDP 限到 40KB/s 每 400ms 抽 DOM，fallback 一次
   *    都冇畫出嚟。零收益、有代價，所以拆。
   *
   * `related` 用返 `requireCard` 已經 load 咗嗰份完整 snapshot（`singleCardSnapshot`
   * 只得一張卡，砌唔到同 set 鄰居同「共 N 張」）。以前喺呢度再 `await
   * loadMarketSnapshot()` 一次，live-db mode（server-snapshot.ts:275-285 冇 memo）
   * 即係同一個 request 打兩次 DB。
   */
  const { full, snapshot } = await requireCard(id);
  const related = relatedCards(full, id);
  return <CardDetail id={id} snapshot={snapshot} related={related} />;
}
