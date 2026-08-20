"use client";

import Link from "next/link";
import { useRef } from "react";
import { FileSearch } from "lucide-react";
import { Breadcrumbs } from "./breadcrumbs";
import { EmptyState } from "./empty-state";
import { CardArt, SpotlightScope } from "./card-art";
import { CardImage } from "./card-image";
import { ShareMenu, type ShareMenuCopy } from "./share-menu";
import { CapTicker } from "./cap-ticker";
import { HistoryChart } from "./history-chart";
import { PeriodSelector } from "./period-selector";
import { DETAIL_PRINT_FIELDS, printIdentityRows } from "./print-badge";
import { Provenance } from "./provenance";
import { PriceDelta, MetricDelta, staleClass, staleTitle } from "./rankings";
import { RelatedCards } from "./related-cards";
import { Reveal } from "./reveal";
import { absolutePublicUrl, canonicalPublicUrl, datasetId, siteOrganization, StructuredData } from "./structured-data";
import { cardNameLangAttr, displayCardName } from "@/lib/card-name";
import { copy } from "@/lib/i18n";
import { formatInteger, formatMetricInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { plainDescription } from "@/lib/plain-text";
import { type ShareFormat, type ShareTarget } from "@/lib/share-destinations";
import { shareImageBlob } from "@/lib/share-file";
import { cardFactSentence, cardSubject, geoCopy, setHubPath, setSlug, tcgHubPath, type RelatedCardsPayload } from "@/lib/related-cards";
import { StoryPanel } from "./story-panel";
import { type MarketMetric, type MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";
import "@/app/styles/card-links.css";
import "@/app/styles/card-art.css";
import "@/app/styles/glow-badges.css";

/*
 * FE05 WS3：邊個數准 count-up。
 * `formatMetric*` 喺 accumulating / unavailable 嗰陣回嘅係一句狀態字（唔係數），
 * 所以唔可以淨係睇 `value !== null` —— 睇漏就會由 0 滾去「暫無資料」。
 * 三個 metric（市值 / PSA 10 價 / 鑑定數量）行同一句判斷，唔准逐個位各寫一次。
 */
function tickerValue(metric: MarketMetric<number>): number | null {
  if (metric.value === null) return null;
  if (metric.status === "accumulating" || metric.status === "unavailable") return null;
  return metric.value;
}

/*
 * 分享圖（owner 2026-08-19）：張圖由 `/api/og/card/[id]?format=post` server 側出，
 * 1080×1350（4:5）—— 貼落 Threads / X / IG feed 先食得晒 post 闊度（原因見 og route
 * 檔頭同 lib/share-image.ts `SHARE_MIN_ASPECT`）。點解唔喺 client 畫：
 * 熱力圖嗰張係即場 canvas（要跟用戶當下揀嘅格數／時段），卡片內頁嗰張淨係跟卡片
 * 本身，server 出得就 server 出 —— 順便同社交 unfurl 嗰張共用同一份 layout code，
 * 唔會有「分享出去嗰張同網頁對唔上」呢種問題（AGENTS.md 規矩 13）。
 *
 * ⚠️ 拆成獨立 component 唔係為咗好睇：`CardDetail` 揾唔到卡嗰陣會 early return，
 * hook 寫喺佢下面即刻變咗條件式呼叫（react-hooks/rules-of-hooks，2026-08-19 真係爆過）。
 * 呢度 hook 永遠行齊。同樣理由唔用 `useCallback`：React Compiler 開住，手寫 memo
 * 反而會令佢跳過 optimize（preserve-manual-memoization）。
 */
const SHARE_FETCH_TIMEOUT_MS = 20_000;

function ShareImageButton({ cardId, imageLang, title, copy: menuCopy }: {
  cardId: string;
  /* 介面語言。張圖入面啲字跟佢行（`api/og/card` 個 `?lang=`）—— 未 ship 字體嗰啲
     語言（ja / ko）route 會自己跌返 en，呢邊唔使再維持一張表。
     ⚠️ **唔准叫 `lang`**：JSX 入面 `lang=` 係 HTML 屬性，`test-fe-lang-attr` 會當你
     想寫個 DOM `lang`（`zh-TW` 落 DOM 係錯值）而擋住。呢個係 query param 唔係屬性。 */
  imageLang: string;
  title: string;
  copy: ShareMenuCopy;
}) {
  /*
   * ⚠️ warm：`navigator.share` 一定要喺 user activation 之內叫（見 lib/share-file.ts），
   * 而張圖成 1.4 MB。撳完先 fetch 喺 iOS Safari 會過咗 activation 期 → 冇 share sheet。
   * 所以 hover / focus / 撳落去嗰刻就開始攞，click handler 只係 await 一個已經飛緊嘅
   * promise。ref 記住個 promise 令佢 idempotent —— onWarm 一次互動會 fire 兩三次。
   *
   * 而家一張卡有三個尺寸（4:5 / 9:16 / 16:9），所以係一個 Map 唔係一個 ref：揀
   * Instagram warm 咗 4:5 之後再 hover 限時動態，兩張都要各自留住。ShareMenu 一開
   * 就先 warm 預設嗰個（七個目的地入面五個都係 `post`）。
   *
   * ⚠️ key 要連埋語言：張圖入面啲字係 server 按 `?lang=` 出嘅，而換語言係 query-only
   * soft navigation（`use-market-settings` 行 `router.replace`，同一條 pathname）——
   * 個 component 唔會 remount，個 ref 原封不動。淨係 key format 嘅話，英文版 warm 完
   * 再轉繁中，撳分享攞返嘅係**英文嗰張**，冇 error 冇 log。
   */
  const shareBlobs = useRef(new Map<string, Promise<Blob>>());
  const warmShareImage = (format: ShareFormat) => {
    const key = `${format}|${imageLang}`;
    if (shareBlobs.current.has(key)) return;
    /* ⚠️ 一定要有 timeout：冇 signal 嘅 fetch 可以吊死到天光，個掣就一路 busy（見
       copy-button.tsx `COPY_TIMEOUT_MS`）。20 秒係實測 1.3-1.4s 之上留足十幾倍水位。 */
    const pending = fetch(`/api/og/card/${encodeURIComponent(cardId)}?format=${format}&lang=${encodeURIComponent(imageLang)}`, {
      signal: typeof AbortSignal.timeout === "function" ? AbortSignal.timeout(SHARE_FETCH_TIMEOUT_MS) : undefined,
    })
      .then((response) => {
        if (!response.ok) throw new Error(`share image HTTP ${response.status}`);
        return response.blob();
      })
      .catch((error) => {
        /* 失敗唔可以黐住個 Map，否則之後撳幾多次都係同一個 rejected promise */
        shareBlobs.current.delete(key);
        throw error;
      });
    shareBlobs.current.set(key, pending);
  };
  const shareCardImage = async (target: ShareTarget) => {
    warmShareImage(target.format);
    const blob = await shareBlobs.current.get(`${target.format}|${imageLang}`)!;
    const pageUrl = `${window.location.origin}/card/${cardId}`;
    /* share sheet 嘅標題／正文同**圖入面**啲字而家一齊跟介面語言（見 og route 個 `?lang=`）。 */
    /* ⚠️ 一定要 `return` —— 掉咗個 outcome 嘅話，用戶撳走 share sheet（"dismissed"）
       喺 ShareMenu 嗰邊睇落同分享成功一模一樣，出綠剔兼讀屏報「圖片已匯出」。 */
    return await shareImageBlob(blob, {
      /* 檔名帶 format：桌面落載幾個尺寸落同一個 Downloads 都唔會撞名變 (1)(2) */
      filenameBase: `cardz-${cardId}-${target.format}`,
      title,
      text: `${title}\n${pageUrl}`,
      clipboardFallbackText: pageUrl,
    });
  };
  return (
    <ShareMenu
      surface="card"
      copy={menuCopy}
      triggerClassName="share-button share-image-button"
      onPick={shareCardImage}
      onWarm={(target) => warmShareImage(target.format)}
    />
  );
}

/*
 * `related` 由 route（server）計，因為卡頁行嘅係 `singleCardSnapshot`——client 側
 * 得返呢一張卡，砌唔到「同一個 set」「排名前後」。冇傳落嚟嗰陣個 prop 係
 * undefined，相關卡區塊直接唔出，其餘照行（所以未接線都 build 得到）。
 */
export function CardDetail({ id, snapshot, related }: {
  id: string;
  snapshot: MarketViewSnapshot;
  related?: RelatedCardsPayload | null;
}) {
  const { locale, currency, period, href } = useMarketSettings();
  const t = copy[locale];
  const card = snapshot.top100.find((item) => item.id === id);

  /* 呢度只係 client 側嘅保險：route（`card/[id]/page.tsx` 個 `requireCard`）已經 `notFound()` 咗，
     所以正常情況入唔到呢條路。文案／出路掣同以前一樣，只係換咗共用 EmptyState。 */
  if (!card) {
    return (
      <div className="page-shell">
        <EmptyState
          className="empty-detail"
          icon={FileSearch}
          title={t.labels.noCards}
          action={<Link className="primary-action" href={href("/")}>{t.nav.all}</Link>}
        />
      </div>
    );
  }

  const windowMetric = card.windows[period];
  const title = displayCardName(card, locale, card.officialName ?? "");
  const story = card.story?.[locale] || null;
  const geo = geoCopy[locale];
  const cardUrl = canonicalPublicUrl(`/card/${card.id}`);
  const localizedTcg = card.tcg === "One Piece" ? t.nav.onePiece : t.nav.pokemon;
  const setLabel = card.setName[locale] || card.setName.en || "";
  /*
   * 可引用事實用嘅日期同上面「資料時間」同一個值（卡自己嘅價格觀察日，冇先跌
   * snapshot 時間）。schema 同睇得見嗰句一定要講同一日，唔可以一句寫價格日、
   * 另一句寫 bake 日。
   */
  const factDate = card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot.effectiveAt;
  const capValue = card.marketCap.value;
  const priceValue = card.pricePsa10.value;
  const popValue = card.populationPsa10.value;
  /* count-up 只做呢三個「頁面主角」數字；ranking row 一律唔掂（100 行 × rAF）。 */
  const capTick = tickerValue(card.marketCap);
  const priceTick = tickerValue(card.pricePsa10);
  const popTick = tickerValue(card.populationPsa10);
  /*
   * GEO（owner 2026-08-16）：卡頁 H1 下面出一句自己站得住嘅事實——實體名、計法
   * （PSA 10 價 × PSA 10 鑑定數量）、日期、排名齊集，唔使睇圖表都答到問題。
   * 三個數有一個係 null 就成句唔出：「市值：暫無資料」呢種句唔可以攞去引用。
   * 「共 N 張」要完整 snapshot 先數到，冇 `related` 就淨講名次，唔准估個總數。
   */
  /*
   * 引用句唔用 H1 個全名：en 嘅 officialName 係 PSA 證書原句（年份 + set 名 + 編號
   * 全部喺入面），照塞落去會喺一句入面出三次 set 名、亦都爆 50 字上限。有本地化
   * 短名就用短名，冇就用剝走年份／set／編號之後嘅主體名。
   */
  const localName = displayCardName(card, locale, "");
  const factName = (localName && localName !== card.officialName ? localName : cardSubject(card))
    || localName
    || card.officialName
    || "";
  const cardFact = capValue !== null && priceValue !== null && popValue !== null
    ? cardFactSentence(locale, {
      name: factName,
      set: setLabel || t.status.unavailable,
      num: card.collectorNumber,
      cap: formatMoney(capValue, currency, snapshot.rates, locale, true),
      price: formatMoney(priceValue, currency, snapshot.rates, locale),
      pop: formatInteger(popValue, locale),
      date: formatObservationDate(factDate, locale),
      rank: card.marketRank,
      total: related?.tcgRankedCount ?? null,
      tcg: localizedTcg,
    })
    : null;
  const setPath = related?.setPath ?? setHubPath(card);
  const crumbs = [
    { label: t.nav.all, href: href("/") },
    { label: localizedTcg, href: href(related?.tcgPath ?? tcgHubPath(card.tcg)) },
    ...(setLabel && setSlug(card) ? [{ label: setLabel, href: href(setPath) }] : []),
    { label: title || t.status.unavailable },
  ];
  /*
   * 市值 / PSA 10 價 / 鑑定數量 / 名次全部係頁面上面睇得見嘅數，但以前一個都冇入
   * schema。逐條落 PropertyValue（USD 原值，唔跟顯示貨幣），null 嗰條唔出——
   * 缺數就係缺數，唔准出 0。
   */
  const additionalProperty = [
    capValue !== null ? { "@type": "PropertyValue", name: "marketCapUsd", value: capValue, unitText: "USD" } : null,
    priceValue !== null ? { "@type": "PropertyValue", name: "pricePsa10Usd", value: priceValue, unitText: "USD" } : null,
    popValue !== null ? { "@type": "PropertyValue", name: "populationPsa10", value: popValue, unitText: "count" } : null,
    card.marketRank >= 1 ? { "@type": "PropertyValue", name: "marketRank", value: card.marketRank } : null,
    factDate ? { "@type": "PropertyValue", name: "asOf", value: factDate } : null,
    card.cardLanguage ? { "@type": "PropertyValue", name: "printLanguage", value: card.cardLanguage } : null,
    card.setName.en ? { "@type": "PropertyValue", name: "setName", value: card.setName.en } : null,
  ].filter((entry) => entry !== null);
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      /*
       * 一張卡同時係作品同商品，所以行 multi-type：本身嗰個 VisualArtwork 冇錯，
       * 唔好為咗加 Product 而拆做兩個節點——同一件嘢兩個 @id 就係拆散實體。
       * 冇 offers / 冇 rating：我哋唔賣卡、亦冇評分，作一個出嚟就係假 schema。
       * BreadcrumbList 由 <Breadcrumbs> 出（同睇得見嗰條係同一份資料），呢度唔再出。
       */
      {
        "@type": ["Product", "VisualArtwork"],
        "@id": `${cardUrl}#card`,
        url: cardUrl,
        name: title || t.status.unavailable,
        alternateName: card.officialName && card.officialName !== title ? card.officialName : undefined,
        identifier: card.collectorNumber,
        sku: card.collectorNumber || undefined,
        image: absolutePublicUrl(card.image.url),
        brand: { "@type": "Brand", name: card.tcg === "One Piece" ? "One Piece Card Game" : "Pokémon" },
        isPartOf: card.setName.en ? { "@type": "CreativeWorkSeries", name: card.setName.en } : undefined,
        /*
         * JSON-LD 唔經 `marketMetadata`，所以要喺呢度自己 normalise 多一次。
         * 呢個 `description` 同 `<meta>` 嗰個係同一篇故事、同一個消毒規矩，
         * 唯獨走另一條路出街 —— 漏咗呢句就得 schema.org 嗰邊仲係生 markdown。
         * 冇故事就用上面睇得見嗰句事實，兩邊字一模一樣。
         */
        description: plainDescription(story ?? "") || cardFact || undefined,
        dateModified: card.pricePsa10.asOf || snapshot.effectiveAt || undefined,
        publisher: siteOrganization(),
        additionalProperty,
        isBasedOn: canonicalPublicUrl("/methodology"),
        subjectOf: { "@type": "Dataset", "@id": datasetId() },
      },
    ],
  };
  return (
    <div className="page-shell detail-page">
      <StructuredData value={structuredData} />
      <Breadcrumbs items={crumbs} label={geo.breadcrumbLabel} />
      <div className="detail-actions">
        <Link className="back-link" href={href("/")}>← {t.nav.all}</Link>
        {/* 得一粒分享掣：owner 2026-08-19 拆走「分享卡牌」（純複製連結）——
            分享出去要嘅係一張睇得晒數據嘅圖，唔係一條乾條連結，兩粒掣並排淨係整亂。
            連結仍然喺 share sheet 嘅正文入面一齊派（見 ShareImageButton）。
            toast 用 `share.done/error`（「圖片已匯出」），唔好借 labels.shareDone。 */}
        <ShareImageButton
          cardId={card.id}
          imageLang={locale}
          title={title}
          copy={{
            label: t.labels.shareImage,
            pick: t.labels.shareTo,
            status: t.labels.shareToStatus,
            other: t.labels.shareToOther,
            desktop: t.labels.shareToDesktop,
            frame: t.labels.shareRatioFrame,
            done: t.share.done,
            error: t.share.error,
          }}
        />
      </div>
      {/* `detail-grid-rail`：卡內頁專用嘅桌面排位。`.detail-grid` / `.detail-art` /
          `.detail-content` / `.detail-metrics` / `.data-time` 五個 class 全部同 box-detail.tsx
          共用，所以所有新 rule 一律靠呢個 class 收口 —— 原盒內頁一個 px 都唔會郁。 */}
      <article className="detail-grid detail-grid-rail">
        <section className="detail-art" aria-label={t.labels.imageAlt}>
          {/* 只有 #1 先發光（`.detail-rank-top`）——發光講「唯一」，第 2 名開始一樣係普通牌。 */}
          <span className={`detail-rank${card.marketRank === 1 ? " detail-rank-top" : ""}`}>#{card.marketRank}</span>
          {/*
            <CardArt> 只係包住 <CardImage>：card-image.tsx 係 100 格 heatmap 嘅 hot path，
            props 要保持 primitive，所以效果一律喺外層加，唔准加 prop 落佢度。
            mask 用同一張 _600（同 srcset 大圖同一個檔），冇 _600 就退返原圖。
          */}
          <CardArt maskSrc={card.image.variants?.["600"] ?? card.image.url}>
            {/* alt 用 `title`（= `displayCardName(card, locale, officialName)`）唔用 `officialName`：
                後者永遠英文，ja/ko/zh 讀屏用戶會聽到一句同版面唔同語言嘅卡名。 */}
            <CardImage image={card.image} sizes="(max-width: 680px) 90vw, 560px" loading="eager" alt={title} />
          </CardArt>
        </section>
        <div className="detail-content">
          <header className="detail-header">
            <p className="section-kicker">{card.tcg}</p>
            <h1 lang={cardNameLangAttr(card, locale)}>{title || t.status.unavailable}</h1>
            <p className="detail-set">{setLabel || t.status.unavailable}</p>
            {/* 印刷版本逐條併入現有 identity list：冇值嘅欄根本唔會回，
                所以完全冇資料嗰陣呢個 dl 同以前一模一樣。
                owner 2026-08-02：語言版本＋卡包來源喺內頁出齊（DETAIL_PRINT_FIELDS 包
                editionCode），唔再出 badge —— 欄位先係佢要嘅形式。 */}
            <dl className="identity-list">
              <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
              {printIdentityRows(card, locale, DETAIL_PRINT_FIELDS).map((row) => (
                <div key={row.key} data-field={row.key}><dt>{row.label}</dt><dd title={row.value}>{row.value}</dd></div>
              ))}
            </dl>
          </header>
          {/*
            FE05 WS3 scroll reveal：呢頁一共三個 <Reveal>（metrics / story / related，
            DOM 順序）加 history chart 自己個 draw-in，四個 —— 每頁上限 8 個 section 級 target。
            metrics 直接由原本嗰個 <section> 做 target（零多餘 DOM）；story 因為
            <StoryPanel> 自己出 <section>，所以退返一個 wrapper <div>（block layout，
            margin 照樣穿過去，實測 rect 零位移）。
          */}
          <div className="detail-period-row"><PeriodSelector compact /></div>
          {/*
            owner 2026-08-17：「圖之後即刻走勢 → 市值/數量 → 再碌先簡介」——
            所以 header 只留身份（kicker / h1 / set / identity），時段掣同走勢圖緊接住卡圖，
            metrics + 資料時間跟尾，簡介（可引用事實 + 故事）一律推到最底先出。
            手機同桌面同一份 DOM 順序，唔准用 CSS order 扮排位。
          */}
          <HistoryChart points={card.historyDaily} locale={locale} currency={currency} rates={snapshot.rates} />
        </div>
        {/*
          `.detail-content` 喺呢度收口：市值/數量同資料時間升做 `.detail-grid` 直屬 child，
          由 `.detail-grid-rail > … { grid-column: 1 / -1 }` 攤成一條全版 KPI 條。
          同 2026-08-19 `.detail-prose` 搬出右欄同一個做法：**淨係換 parent，DOM 次序一個字冇郁**
          （卡圖 → 標題 → 時段掣 → 走勢圖 → 市值/數量 → 資料時間 → 簡介），讀屏同 Tab 序一樣，
          亦都唔准改用 CSS `order`。手機 `.detail-grid` 本身就係 `display: block`（呢個檔 ~2141），
          而新 CSS 全部包喺 `@media (min-width: 981px)` 入面，所以 ≤980 由構造上郁唔到。
        */}
        <Reveal as="section" className="detail-metrics" aria-label={t.labels.marketCap}>
          <div><span>{t.labels.marketCap}</span><strong className={staleClass(card.marketCap, "metric-value-fit")} title={staleTitle(card.marketCap, locale)}>{capTick === null ? formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true) : <CapTicker key={capTick} value={capTick} format={(n) => formatMoney(n, currency, snapshot.rates, locale, true)} />}</strong><MetricDelta metric={card.marketCap} changePct={windowMetric.marketCapChangePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
          {/* FE05 WS3：PSA 10 價同鑑定數量跟返市值行同一個 ticker（JSX 出真實值，
              滾動只喺 hydrate 之後）。數量要 Math.round —— 中途嗰啲小數唔可以見街。 */}
          <div><span>{t.labels.price}</span><strong className={staleClass(card.pricePsa10, "detail-price-now")} title={staleTitle(card.pricePsa10, locale)}>{priceTick === null ? formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale) : <CapTicker key={priceTick} value={priceTick} format={(n) => formatMoney(n, currency, snapshot.rates, locale)} />}</strong><PriceDelta card={card} period={period} currency={currency} rates={snapshot.rates} locale={locale} /></div>
          <div><span>{t.labels.population}</span><strong className={staleClass(card.populationPsa10, "")} title={staleTitle(card.populationPsa10, locale)}>{popTick === null ? formatMetricInteger(card.populationPsa10, locale) : <CapTicker key={popTick} value={popTick} format={(n) => formatInteger(Math.round(n), locale)} />}</strong></div>
          <div><span>{t.periods[period]} {t.labels.change}</span><strong className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</strong>{windowMetric.changePct.sourceSwitched && <small className="muted-copy">{t.provenance.anchorSwitched}</small>}</div>
          <div className="wide-metric"><span>{t.periods[period]} {t.labels.trackedSales}</span><strong className="metric-value-fit">{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</strong><MetricDelta metric={windowMetric.trackedSales.valueUsd} changePct={windowMetric.trackedSalesChangePct} currency={currency} rates={snapshot.rates} locale={locale} /></div>
          {/* 冇 RAW 參考價就成格唔出，唔好畫住「暫無資料」霸位 */}
          {card.priceUngradedReference && card.priceUngradedReference.value !== null && (
            <div><span>{t.labels.ungradedReference}</span><strong className={staleClass(card.priceUngradedReference, "")} title={staleTitle(card.priceUngradedReference, locale)}>{formatMetricMoney(card.priceUngradedReference, currency, snapshot.rates, locale)}</strong></div>
          )}
        </Reveal>
        {/*
          「資料時間」講嘅係上面嗰堆數幾時嘅，唔係個 snapshot 幾時 bake。
          原本行 `snapshot.effectiveAt || card.pricePsa10.asOf`，而 effectiveAt 永遠有值，
          所以第二項係死 code，逐張卡都畫緊 generation 時間。實測 1286 張出街卡入面
          949 張（73.8%）個真實價格日期比 generation 早 8 日以上，最誇張嗰張
          （rk1231）價格係 2026-03-27，個頁面照寫「Data time: Aug 10, 2026」——
          差 136 日。市值 = 價 × POP，所以呢個日期一錯，成塊 metrics 都報錯時間。
          改用卡自己嗰個價格觀察日；冇價先跌返 snapshot 時間。
        */}
        <p className="data-time">
          {card.pricePsa10.sourcePeriodAt
            ? `${t.labels.pricePeriod}: ${formatObservationDate(card.pricePsa10.sourcePeriodAt, locale)} · `
            : null}
          {t.labels.checkedAt}: {formatObservationDate(card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot.effectiveAt, locale)}
        </p>
      </article>
      {/*
        長文區（可引用事實 / 故事 / 方法）2026-08-19 由右欄搬咗出嚟。
        點解：桌面 1440 實測，左欄（卡圖）680px 高、右欄 1746px 高 —— 卡圖下面
        成 1066 × 536px 淨係吉住，而三塊長文迫喺右半版，仲要各自帶住唔同嘅 ch cap
        （量到右邊線 1382 / 1315 / 1346 三條，就係「唔對齊」嘅來源）。
        搬出嚟之後三塊由 x=43 起（同麵包屑／返回／相關卡牌同一條左邊線），
        桌面分兩欄各 ~646px ≈ 68ch，仍然喺可讀範圍。
        DOM 次序冇變（一直都係 fact → story → method），手機 `.detail-grid` 本來就
        `display: block`，所以手機睇落完全一樣。card-fact 係 GEO 可引用純文字，
        JSON-LD／meta 讀嘅係 `cardFact` 變數唔讀 DOM，搬位同 schema 無關。
      */}
      <div className="detail-prose">
        {cardFact && <p className="card-fact">{cardFact}</p>}
        {/* 冇故事嗰陣 StoryPanel 回 null —— 唔好淨係包住個空 Reveal <div>，
            喺 grid 入面佢會佔實一格，右欄就會憑空跌低一截。 */}
        {story && <Reveal className="detail-story-slot"><StoryPanel title={t.labels.story} story={story} /></Reveal>}
        <Provenance updatedAt={card.pricePsa10.checkedAt || card.pricePsa10.asOf || snapshot.effectiveAt} />
      </div>
      {related && (
        /* SpotlightScope 只出一個冇樣式嘅 div + 一個 delegated pointermove（桌面 only），
           所以 related-cards.tsx 一行都唔使改，亦冇每張卡各自 attach listener。 */
        <Reveal>
          <SpotlightScope>
            <RelatedCards related={related} locale={locale} currency={currency} rates={snapshot.rates} href={href} />
          </SpotlightScope>
        </Reveal>
      )}
    </div>
  );
}
