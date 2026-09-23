import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { CardDetail } from "@/components/card-detail";
import { displayCardName } from "@/lib/card-name";
import { formatInteger, formatMoney, formatObservationDate } from "@/lib/format";
import { copy, localizedCardLanguage } from "@/lib/i18n";
import { cardShareLine, cardSubject, fillTemplate, geoCopy, relatedCards, shortSubject } from "@/lib/related-cards";
import { localeFromSearchParams, marketMetadata, type PageSearchParams } from "@/lib/route-metadata";
import { loadMarketSnapshot, singleCardSnapshot } from "@/lib/server-snapshot";
import { defaultMarketWindow } from "@/lib/types";

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

/*
 * marketRank 係跨 TCG 嘅全站排名，分母必須包含 top100 + watchlist 所有已排名卡。
 * 「ranked／已排名」限定 CardZ 收錄範圍，唔代表全世界嘅卡。
 */
function indexRankedCount(full: Awaited<ReturnType<typeof requireCard>>["full"]): number | null {
  const count = [...full.top100, ...full.watchlist]
    .filter((candidate) => candidate.marketRank >= 1).length;
  return count > 0 ? count : null;
}

export async function generateMetadata({ params, searchParams }: CardRouteProps): Promise<Metadata> {
  const [{ id }, locale] = await Promise.all([params, localeFromSearchParams(searchParams)]);
  const { full, card, snapshot } = await requireCard(id);
  const t = copy[locale];
  const labels = t.labels;
  const localName = displayCardName(card, locale, card.officialName || labels.viewCard);
  const tcgName = card.tcg === "One Piece" ? t.nav.onePiece : t.nav.pokemon;
  const rankedCount = indexRankedCount(full);
  const capValue = card.marketCap.value;
  const priceValue = card.pricePsa10.value;
  const popValue = card.populationPsa10.value;
  const capText = capValue !== null ? formatMoney(capValue, "USD", snapshot.rates, locale, true) : null;

  /*
   * <title> / og:title：「短卡名 #編號 語言 TCG · 全站排名 · 市值」。
   * （owner 2026-08-19）。
   *
   * 點解要改：舊式出街嘅係 PSA 全串，實測 rank #4 嗰張出咗
   * `2016 Pokemon Japanese XY Promo Full Art/Mario Pikachu Mario Pikachu Special Box 294/XY-P`
   * ——88 字，喺 WhatsApp／Telegram 嘅 unfurl 標題只見到頭一截「2016 Pokemon Japanese XY
   * Promo Full Art/Mario…」，即係**年份同 set 名食晒全部可見空間**，而人哋會唔會撳全靠
   * 嘅嗰兩個數（#4、$76.06M）連出場機會都冇。
   *
   * 60 字上限維持（owner 2026-08-16 定，`<title>` 仲要俾 layout template 加 18 字）。
   * 剝除次序（唔准調轉）：
   *   1. 短卡名尾巴（`shortSubject` 字界剪 + `…`）——最長兼最可以短嘅一截；
   *   2. `{TCG}` 個字 —— 張圖同 og:description 都攞得返；
   *   3. `· {市值}` 整段 —— og:description 攞得返。
   * `#{編號}`、印刷語言、`#{排名}` 永不剝：同一角色同一 set 嘅兩張卡淨靠編號分身份。
   */
  const TITLE_MAX = 60;
  /*
   * 印刷語言喺 title 度用**短 token**，唔用 badge 嗰句。
   * en 個 `printLanguage` template 出「Japanese print」= 14 字，喺 60 字預算入面食咗
   * 23%，實測會逼到卡名由「Mario Pikachu Special Box」剪剩「Mario Pikachu…」兼連
   * 「Pokémon」都要剝走。CJK 嗰句本身就係「日文版」三個字，唔使動。
   * ⚠️ 呢個 token 永不准剝：同一角色同一 set 嘅日英兩版靠佢分身份。
   */
  const LANG_TOKEN: Record<string, string> = { ja: "JP", ko: "KR", zhCN: "CN", zhTW: "TW" };
  const printLanguage = card.cardLanguage && card.cardLanguage !== "en"
    ? (locale === "en"
      ? LANG_TOKEN[card.cardLanguage] ?? null
      : labels.printLanguage.replace("{language}", localizedCardLanguage(card.cardLanguage, locale)))
    : null;
  const identity = [
    card.collectorNumber ? `#${card.collectorNumber}` : null,
    printLanguage,
  ].filter(Boolean).join(" ");
  const rankPart = card.marketRank >= 1
    ? fillTemplate(geoCopy[locale].shareRankNoTotal, { rank: card.marketRank })
    : null;
  const buildTitle = (subject: string, withIdentity: boolean, withTcg: boolean, withCap: boolean) => [
    [subject, withIdentity ? identity : null, withTcg ? tcgName : null].filter(Boolean).join(" "),
    rankPart,
    withCap ? capText : null,
  ].filter(Boolean).join(" · ");
  /*
   * 先用完整短卡名砌一次，爆咗先按**實際超出幾多字**剪 —— 唔好靠「固定段長度」倒扣：
   * 卡名同編號之間嗰個空格唔喺固定段入面，實測會少計一格，於是砌出 61 字（爆 1 字）
   * 再無謂咁降級剝走「Pokémon」。
   */
  const SUBJECT_FLOOR = 16;
  /*
   * 降級階梯嘅次序 = 邊樣最抵留低。編號（`#085/SVP`）**排喺卡名之前俾人剝**：
   * 實測「Pikachu With Grey Felt Hat Pokemon X Van Gogh」剩 27 字預算，斬成
   *「Pikachu With Grey Felt…」—— 個名嘅記認位（Van Gogh）冇咗，換返嚟嘅係一串
   * 只有收藏者先識讀嘅編號。氣泡標題係鈎，編號係收據；收據喺圖入面同頁面都仲有。
   * ⚠️ 但編號只喺**卡名真係入唔落**嗰陣先剝，唔係一開波就唔要。
   */
  const SUBJECT_KEEP = 30;
  const fullSubject = shortSubject(card, locale);
  let title = buildTitle(fullSubject, true, true, true);
  if (title.length > TITLE_MAX) {
    const budget = fullSubject.length - (title.length - TITLE_MAX);
    title = budget >= SUBJECT_KEEP
      ? buildTitle(shortSubject(card, locale, budget), true, true, true)
      : buildTitle(fullSubject, false, true, true);
  }
  if (title.length > TITLE_MAX) {
    const budget = Math.max(SUBJECT_FLOOR, fullSubject.length - (title.length - TITLE_MAX));
    title = buildTitle(shortSubject(card, locale, budget), false, true, true);
  }
  if (title.length > TITLE_MAX) title = buildTitle(shortSubject(card, locale, SUBJECT_FLOOR), false, false, true);
  if (title.length > TITLE_MAX) title = buildTitle(shortSubject(card, locale, SUBJECT_FLOOR), false, false, false);
  /* 全部剝完都仲爆（理論上唔會，但唔准出街先斷）→ 硬剪，起碼保住開頭。 */
  if (title.length > TITLE_MAX) title = `${title.slice(0, TITLE_MAX - 1).trimEnd()}…`;

  /*
   * description 由散文事實句改成點分隔嘅分享行（owner 2026-08-19）。
   *
   * 舊寫法留低嘅一句註係**實測錯**嘅：佢寫住「卡名／set／編號／市值／PSA 10 價／POP／
   * 日期／名次全部喺可見範圍入面」，但 `plainDescription()` 個 160 字 clamp 實際將出街
   * 嗰句斬到「…as of Aug 18…」——price / pop / 名次一個都入唔到 meta。
   * 新做法：同一份 `CardFactInput` 出兩個 variant —— 散文句繼續行頁面
   * `<p class="card-fact">` 同 JSON-LD（完整未斬，AI 爬蟲主要讀嗰兩度），點分隔行入三個
   * meta 出口（永遠 ≤160，唔會被 clamp 掂）。理由寫喺 `cardShareLine` 個註。
   * 三個數缺一就照舊跌返小故事。
   */
  const factDate = card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot.effectiveAt;
  const changeMetric = card.windows?.[defaultMarketWindow]?.changePct;
  /* fail-closed：status 唔係 ready/stale（累積中／未有數）就當冇數，唔准出「0.00%」扮平穩。 */
  const changeText = changeMetric
    && changeMetric.value !== null
    && Number.isFinite(changeMetric.value)
    && (changeMetric.status === "ready" || changeMetric.status === "stale")
    ? `${changeMetric.value > 0 ? "▲+" : changeMetric.value < 0 ? "▼" : "•"}${changeMetric.value.toFixed(1)}% ${defaultMarketWindow.toUpperCase()}`
    : null;
  const shareInput = capText !== null && priceValue !== null && popValue !== null
    ? {
      name: (localName && localName !== card.officialName ? localName : cardSubject(card)) || localName,
      set: card.setName[locale] || card.setName.en || t.status.unavailable,
      num: card.collectorNumber,
      cap: capText,
      price: formatMoney(priceValue, "USD", snapshot.rates, locale),
      pop: formatInteger(popValue, locale),
      date: formatObservationDate(factDate, locale),
      rank: card.marketRank,
      total: rankedCount,
    }
    : null;
  const description = shareInput
    ? cardShareLine(locale, { ...shareInput, change: changeText })
    : card.story?.[locale] || labels.viewCard;

  /*
   * og:image URL 帶 `?v={generation}`：Meta 自己嘅建議係「換 URL，唔好喺同一條 URL 覆蓋
   * bytes」。要講清楚佢買唔到咩 —— **唔會**令 Facebook 自動 refresh 舊 preview（FB 係
   * per-page-URL cache），已 send 咗嘅 WhatsApp／iMessage 訊息亦永遠唔會更新。佢保證嘅
   * 係：任何一家真係 re-scrape 嗰陣攞到嘅一定係當日 bytes，唔係 CDN 舊圖。
   * `readShareFormat`/`readTheme` 對未知 param 已經 default-through，OG route 唔使改。
   *
   * `&lang=` 跟返頁面語言：中文頁派出去嘅 link 出一張英文圖，就係「頁面同 preview
   * 講唔同語言」。`readShareLang` 認唔到（ja / ko —— 未 ship 字體）會跌返 en，
   * 所以呢度照傳 locale 就得，唔使喺呢邊維持第二張「邊個語言有圖」嘅表。
   *
   * imageAlt 由裸卡名改成載實數：alt 係讀屏同埋部分 unfurler 嘅純文字 fallback，
   * 只講個名等於將張圖入面全部數字掉咗。
   */
  const imageAlt = [
    /*
     * 短卡名，唔用 88 字嘅 PSA 全串 —— alt 係讀屏一句過讀出嚟，全串會蓋過後面啲數。
     * ⚠️ 但**唔可以用 `shortSubject` 個 44 字預設**：alt 冇長度壓力（唔上 <title>、
     * 唔上氣泡），斬到「…Pokemon X Van…」係將讀屏用戶嗰句斬走咗最關鍵嗰兩個字。
     * 排版先要 44，語意唔需要。
     */
    [shortSubject(card, locale, 80), card.collectorNumber ? `#${card.collectorNumber}` : null].filter(Boolean).join(" "),
    /* 數字段直接借用同一條分享行：alt 唔准講一套、meta 講另一套，亦唔准淨係得個名。 */
    shareInput ? description : null,
  ].filter(Boolean).join(" — ");
  return marketMetadata(
    locale,
    title,
    description,
    `/card/${id}`,
    `/api/og/card/${encodeURIComponent(id)}?v=${encodeURIComponent(snapshot.generation)}&lang=${encodeURIComponent(locale)}`,
    imageAlt || localName || "CardZ Marketcap",
    /* wide 個 OG route 出 JPEG（壓落 WhatsApp 600KB 閘），唔係站內默認嗰張 PNG。 */
    "image/jpeg",
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
