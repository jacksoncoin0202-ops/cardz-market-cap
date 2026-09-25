import { type PublicCard as CanonicalCard, type PublicMarketSnapshot as CanonicalSnapshot } from "@cardz/market-data";
import { deriveLongWindows, longWindows } from "./derive-windows";
import { formatStoryForDisplay } from "./story-display";
import {
  currencies,
  intrinsicSize,
  marketWindows,
  producerWindows,
  type Currency,
  type LocalizedText,
  type MarketCardView,
  type MarketMetric,
  type MarketViewSnapshot,
  type MarketWindow,
} from "./types";

/*
 * Product mode is a pure projection of the canonical public snapshot. Missing
 * locale values stay missing: names, set titles, stories and image alt text
 * must be consolidated before publication, never repaired by the browser.
 */
function localised(value: CanonicalCard["names"] | CanonicalCard["sets"] | CanonicalCard["stories"] | undefined): LocalizedText {
  const clean = (text: string | null): string | null => {
    if (typeof text !== "string") return null;
    const trimmed = text.trim();
    return trimmed.length > 0 ? trimmed : null;
  };
  return {
    en: clean(value?.en ?? null) ?? "",
    "zh-TW": clean(value?.zhTW ?? null),
    "zh-CN": clean(value?.zhCN ?? null),
    ja: clean(value?.ja ?? null),
    ko: clean(value?.ko ?? null),
  };
}

function officialName(card: CanonicalCard): string | null {
  if (typeof card.officialName !== "string") return null;
  const value = card.officialName.trim();
  return value.length > 0 ? value : null;
}

function metric(value: CanonicalCard["pricePsa10"]): MarketMetric<number> {
  return {
    value: value.value,
    status: value.status,
    asOf: value.asOf,
    sourcePeriodAt: value.sourcePeriodAt ?? null,
    saleAt: value.saleAt ?? null,
    checkedAt: value.checkedAt ?? null,
    sourceSwitched: value.sourceSwitched ?? false,
  };
}

/*
 * 顯示層專用嘅 printing identity 投影。
 * 兩個危險位：
 *  1. producer 對未知 code 寫嘅係空字串 `""`，唔係 undefined —— 直接傳落去會出空 chip，
 *     所以逐條 `|| null` 正規化。
 *  2. canonical `PublicPrintingIdentity` 帶住 canonicalPrintingSha256 / evidenceSha256，
 *     呢兩條唔准出 DOM，所以逐條白名單抄，唔用 spread。
 * 冇 `printingIdentity` 嘅 snapshot（seed / legacy evidence）一律出 `null`，
 * 顯示層乜都唔應該畫。
 *
 * 白名單 2026-08-11 收窄到 `setCode` / `finishCode` —— 即係 print-badge 真正讀嗰
 * 兩條。原本仲抄住 `setName` / `collectorNumber` / `editionCode`，三條都係
 * `catalog_printing_identity` 嘅 printing_sha() 前像（指紋用字，唔跟 PSA 標籤），
 * 而三條喺 apps/web 一個讀者都冇。收返 editionCode 個 render 之後佢哋仍然照樣
 * 序列化落 RSC flight payload 同 `/api/v1/market`，實測 rank 1 出
 * `"editionCode":"SVP EN “Van gogh exhibition”"`。冇人畫 ≠ 冇出街，所以喺投影度斬。
 * 完整記錄仍然留喺 canonical snapshot（`packages/market-data` 嘅
 * PublicPrintingIdentity），呢度淨係唔再轉發。
 */
function printingIdentityView(card: CanonicalCard): MarketCardView["printingIdentity"] {
  const identity = card.printingIdentity;
  if (!identity) return null;
  return {
    setCode: identity.setCode || null,
    finishCode: identity.finishCode || null,
  };
}

function cardView(card: CanonicalCard): MarketCardView {
  const name = localised(card.names);
  const canonicalName = officialName(card);
  // 圖片信任來自 release receipt + rejection registry（owner 2026-08-02 拆舊 image QC gate）；
  // receipt 路徑出嘅 qcAt 永遠係 null，再要求 qcAt 就會全站 placeholder。
  const imageIsSafe = card.image.kind === "raw_front"
    && /^\/market-assets\/[a-f0-9]{64}\.webp$/.test(card.image.src);

  const windowView = (raw: CanonicalCard["windows"]["1d"], inventCap: boolean) => ({
    changePct: metric(raw.changePct),
    // Product no longer consumes multi-grader POP deltas. The producer emits
    // canonical marketCapChangePct; retained snapshots fall back to price only.
    // 長窗唔准借價%當市值%：出街歷史冇每日 pop，舊價×今日 pop = 作數。
    marketCapChangePct: metric(
      inventCap
        ? (raw.marketCapChangePct ?? raw.changePct)
        : (raw.marketCapChangePct ?? { value: null, status: "accumulating" as const, asOf: null }),
    ),
    // 成交額環比要 producer 出真數；冇就係計唔到，一樣唔准借價格變動。
    trackedSalesChangePct: metric(
      raw.trackedSalesChangePct ?? { value: null, status: "accumulating", asOf: null },
    ),
    trackedSales: {
      valueUsd: metric(raw.trackedSales.valueUsd),
      count: metric(raw.trackedSales.count),
      coverage: raw.trackedSales.coverage,
      asOf: raw.trackedSales.asOf,
    },
  });
  const producer = Object.fromEntries(producerWindows.map((window) => [
    window,
    windowView(card.windows[window], true),
  ]));
  const baked = card.windows as Record<string, CanonicalCard["windows"]["1d"] | undefined>;
  // 長窗後備：baked snapshot 冇長窗嗰陣先行呢條。混合錨同 producer 一樣——真成交
  // 優先，冇先至退參考點（R6b）。短窗唔經呢度（producer 出嘅照用）。
  const derived = deriveLongWindows(
    card.historyDaily,
    card.pricePsa10.value,
    card.pricePsa10.asOf,
    card.historyReference ?? [],
  );
  const windows = {
    ...producer,
    ...Object.fromEntries(longWindows.map((window) => [
      window,
      baked[window] ? windowView(baked[window], false) : derived[window],
    ])),
  } as MarketCardView["windows"];

  // `cardLanguage` 已經係 schema 正式欄位（packages/market-data/src/schema.ts），唔使再 cast。
  // 但仍然逐個值核一次：snapshot 係 runtime JSON，type 唔會幫你擋走樣嘅 code。
  const printLang: string | null | undefined = card.cardLanguage;
  const cardLanguage =
    printLang === "en" || printLang === "ja" || printLang === "ko"
      || printLang === "zhCN" || printLang === "zhTW"
      ? printLang
      : null;

  return {
    id: card.id,
    rank: card.rank,
    marketRank: card.marketRank,
    viewRank: card.viewRank,
    tcg: card.tcg === "one-piece" ? "One Piece" : card.tcg === "pokemon" ? "Pokémon" : "TCG",
    cardLanguage,
    printingIdentity: printingIdentityView(card),
    collectorNumber: card.collectorNumber.display,
    officialName: canonicalName,
    name,
    setName: localised(card.sets),
    story: {
      en: formatStoryForDisplay(card.stories?.en) ?? "",
      "zh-TW": formatStoryForDisplay(card.stories?.zhTW),
      "zh-CN": formatStoryForDisplay(card.stories?.zhCN),
      ja: formatStoryForDisplay(card.stories?.ja),
      ko: formatStoryForDisplay(card.stories?.ko),
    },
    image: {
      url: imageIsSafe ? card.image.src : "/card-placeholder.svg",
      alt: canonicalName,
      kind: imageIsSafe ? "raw_front" : "placeholder",
      variants: imageIsSafe ? card.image.variants : undefined,
      /*
       * 內在尺寸出到 `<img width height>`（FE05 WS-state）。舊 snapshot 冇呢兩個
       * field、live-db 量唔到會寫 0、placeholder 根本冇尺寸 —— 三種都 omit，
       * 唔准填 0 或者借另一張卡嘅比例。
       */
      ...(imageIsSafe ? intrinsicSize(card.image.width, card.image.height, "raw_front") : {}),
    },
    pricePsa10: metric(card.pricePsa10),
    // Older public snapshots predate this optional field. Keep the view contract
    // stable and render an explicit unavailable reference instead of borrowing PSA 10.
    priceUngradedReference: metric(
      card.priceUngradedReference ?? { value: null, status: "unavailable", asOf: null },
    ),
    populationPsa10: metric(card.populationPsa10),
    marketCap: metric(card.marketCap),
    windows,
    historyDaily: card.historyDaily.map((point) => ({ ...point })),
    historyReference: (card.historyReference ?? []).map((point) => ({ ...point })),
    salesSparkline: card.historyDaily
      .map((point) => point.trackedSalesValueUsd)
      .filter((value): value is number => value !== null && Number.isFinite(value)),
  };
}

export function normaliseSnapshot(snapshot: CanonicalSnapshot): MarketViewSnapshot {
  /*
   * 容忍 snapshot 少過 FE 清單：baked snapshot 可能係舊一日 bake 出、未有新加嘅貨幣，
   * 硬讀 `.value` 會 TypeError 炸成頁。缺就 NaN，`formatMoney` fail-closed 出「暫無資料」。
   */
  const rates = Object.fromEntries(currencies.map((currency) => [
    currency,
    snapshot.currencies.rates[currency]?.value ?? Number.NaN,
  ])) as Record<Currency, number>;
  const top100 = snapshot.top100.map(cardView);
  const watchlist = snapshot.watchlist.map(cardView);
  const cards = [...top100, ...watchlist];
  const windowReady = (field: "changePct" | "sales"): Record<MarketWindow, number> => (
    Object.fromEntries(marketWindows.map((window) => [
      window,
      cards.filter((card) => field === "changePct"
        ? card.windows[window].changePct.value !== null
        : card.windows[window].trackedSales.valueUsd.value !== null).length,
    ])) as Record<MarketWindow, number>
  );
  return {
    schemaVersion: snapshot.schemaVersion,
    generation: snapshot.generation.id,
    generatedAt: snapshot.generation.generatedAt,
    effectiveAt: snapshot.generation.effectiveAt,
    mode: "canonical",
    coverage: {
      claim: snapshot.coverage.claim,
      requestedCount: snapshot.coverage.requestedCount,
      verifiedCount: snapshot.coverage.verifiedCount,
      changeReady: windowReady("changePct"),
      salesReady: windowReady("sales"),
      completeIdentityCount: snapshot.coverage.completeIdentityCount,
      localizedStoryCount: {
        en: snapshot.coverage.localizedStoryCount.en,
        "zh-TW": snapshot.coverage.localizedStoryCount.zhTW,
        "zh-CN": snapshot.coverage.localizedStoryCount.zhCN,
        ja: snapshot.coverage.localizedStoryCount.ja,
        ko: snapshot.coverage.localizedStoryCount.ko,
      },
    },
    ratesAsOf: snapshot.currencies.asOf,
    rates,
    top100,
    watchlist,
  };
}
