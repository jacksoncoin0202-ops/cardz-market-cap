import seedSnapshot from "../../../../data/public/seed-snapshot.json";
import editorialPack from "../../../../data/editorial/top100-stories.json";
import setNamePack from "../../../../data/editorial/set-names.json";
import { composeChangePct } from "@cardz/market-data";
import type { PublicCard as CanonicalCard, PublicMarketSnapshot as CanonicalSnapshot } from "@cardz/market-data";
import { displayCardNameEn, localizedCardName } from "./card-names";
import {
  currencies,
  graders,
  marketWindows,
  type Currency,
  type LocalizedText,
  type MarketCardView,
  type MarketMetric,
  type MarketViewSnapshot,
  type MarketWindow,
  type TrackedSalesMetric,
} from "./types";

type EditorialEntry = (typeof editorialPack.entries)[number];

const readyEditorial = new Map(
  editorialPack.entries
    .filter((entry) => entry.status === "ready")
    .map((entry) => [entry.id, entry] as const),
);

function editorialStories(card: CanonicalCard): CanonicalCard["stories"] {
  const canonicalReady = Object.values(card.stories).every((story) => typeof story === "string" && story.trim().length > 0);
  if (canonicalReady) return card.stories;
  const entry = readyEditorial.get(card.id) as EditorialEntry | undefined;
  if (
    !entry ||
    entry.tcg !== card.tcg ||
    entry.cardLanguage !== card.language ||
    entry.collectorNumber !== card.collectorNumber.display
  ) return card.stories;
  return entry.stories;
}

type SetNameEntry = { zhTW?: string; zhCN?: string; ja?: string };

/*
 * Set 名嘅翻譯來源。呢個檔一直存在但從來冇人 import，而 snapshot 入面
 * `sets.zhTW / zhCN / ja` 三條全部係空字串（360 張卡逐張核過），
 * 所以五個語系嘅 set 名一律跌返英文。接返線之後 209/360 張卡有中日文 set 名。
 */
const editorialSetNames = setNamePack.entries as Record<string, SetNameEntry>;

function localisedSetName(sets: CanonicalCard["sets"]): LocalizedText {
  const en = sets.en || "";
  const editorial = editorialSetNames[en];
  const zhTW = sets.zhTW || editorial?.zhTW || "";
  return {
    en,
    "zh-TW": zhTW || en,
    "zh-CN": sets.zhCN || editorial?.zhCN || zhTW || en,
    ja: sets.ja || editorial?.ja || en,
    /*
     * 韓文 set 名一條來源都冇（見下面 localised() 註解）。但 set 名本身就係英文
     * 產品標題（「2014 XY Flashfire」），零翻譯之下 zh-TW / zh-CN / ja 三個語系
     * 一樣跌返 `en`。ko 單獨出 null 會令顯示層行 fallback 出「데이터 없음」，
     * 即係有一個完全正確、可顯示嘅值都唔出，反而似壞咗。
     * 出原文唔等於扮有韓文譯名 —— 禁止嘅係作一個譯名出嚟，唔係顯示原文。
     * 卡名唔同：`card-names.ts` 有獨立 KO 字典，所以嗰邊維持 ko: null。
     */
    ko: en,
  };
}

function localised(value: CanonicalCard["names"], fallback: string, translate?: boolean): LocalizedText {
  const raw = value.en || fallback;
  if (!translate) {
    return {
      en: raw,
      "zh-TW": value.zhTW || value.en || fallback,
      "zh-CN": value.zhCN || value.zhTW || value.en || fallback,
      ja: value.ja || value.en || fallback,
      /*
       * 之前呢度硬寫 `ko: value.en`，即係無論如何都出英文，仲要係扮成韓文內容出。
       * 實測全部韓文來源都唔存在：canonical snapshot 得 en/zhTW/zhCN/ja 四條 key，
       * top100-stories.json 150 條得 en/zhTW/zhCN/ja，set-names.json 172 條得 zhTW/zhCN/ja。
       * 所以韓文係真係冇 —— 出 `null`，等顯示層自己揀 fallback，唔准扮有。
       * 補數據嘅工單見 docs/DATA_GAPS.md。
       */
      ko: null,
    };
  }
  // When the upstream English name has no translation, the producer copies it into every
  // locale field. Repairing a truncated English name therefore has to discard those copies,
  // or zh/ja would keep rendering the truncation the repair just removed.
  const en = displayCardNameEn(raw);
  const stale = en !== raw;
  const zhFallback = stale ? "" : value.zhCN || value.zhTW || "";
  return {
    en,
    "zh-TW": localizedCardName(en, stale ? null : value.zhTW, "zh-TW"),
    "zh-CN": zhFallback || localizedCardName(en, null, "zh-CN"),
    ja: localizedCardName(en, stale ? null : value.ja, "ja"),
    ko: localizedCardName(en, null, "ko"),
  };
}

function metric(value: CanonicalCard["pricePsa10"]): MarketMetric<number> {
  return { value: value.value, status: value.status, asOf: value.asOf };
}

/*
 * 2026-07-27 用戶決定（上線畫面優先）：窗口指標空咗唔准成欄冇數，用最新鮮嘅
 * 有數窗口頂住 —— donor 順序固定 1d→7d→30d（跳過自己）。借返嚟嘅值 status
 * 一律標 "stale" 兼記低 fallbackWindow，等真數據源（eBay／自家插件）接返嚟
 * 之後可以還原 fail-closed。
 * 借嘅係成個窗口嘅完整結果 —— 同一個窗口之內嘅頂替（例如攞 changePct 當
 * 市值變動、或者喺合成公式入面溝唔同窗口嘅輸入）依然禁止。
 * 已知數據問題見 docs/evidence/2026-07-27-launch-fallback/DATA_CONCERNS.md。
 */
const windowFallbacks: Record<MarketWindow, readonly MarketWindow[]> = {
  "1d": ["7d", "30d"],
  "7d": ["1d", "30d"],
  "30d": ["1d", "7d"],
};

function displayable(value: MarketMetric<number>): boolean {
  return value.value !== null && (value.status === "ready" || value.status === "stale");
}

export function borrowWindowMetric(
  window: MarketWindow,
  pick: (window: MarketWindow) => MarketMetric<number>,
): MarketMetric<number> {
  const native = pick(window);
  if (displayable(native)) return native;
  for (const donor of windowFallbacks[window]) {
    const candidate = pick(donor);
    if (displayable(candidate)) return { ...candidate, status: "stale", fallbackWindow: donor };
  }
  return native;
}

function borrowTrackedSales(
  window: MarketWindow,
  pick: (window: MarketWindow) => TrackedSalesMetric,
): TrackedSalesMetric {
  // 可顯示條件同 formatTrackedSales 一致：coverage 唔係 unavailable、金額同單數都 > 0。
  const showable = (sales: TrackedSalesMetric) =>
    sales.coverage !== "unavailable"
    && sales.valueUsd.status !== "unavailable"
    && sales.valueUsd.value !== null && sales.valueUsd.value > 0
    && sales.count.value !== null && sales.count.value > 0;
  const native = pick(window);
  if (showable(native)) return native;
  for (const donor of windowFallbacks[window]) {
    const candidate = pick(donor);
    if (showable(candidate)) {
      return {
        ...candidate,
        valueUsd: { ...candidate.valueUsd, status: "stale", fallbackWindow: donor },
        count: { ...candidate.count, status: "stale", fallbackWindow: donor },
      };
    }
  }
  return native;
}

function cardView(card: CanonicalCard): MarketCardView {
  const name = localised(card.names, `Card ${card.rank}`, true);
  const stories = editorialStories(card);
  const imageIsSafe = card.image.kind === "raw_front"
    && Boolean(card.image.qcAt)
    && /^\/market-assets\/[a-f0-9]{64}\.webp$/.test(card.image.src);

  // 先逐個窗口砌好本窗口嘅真數，之後先跨窗口借（見 borrowWindowMetric 註解）。
  const rawWindows = Object.fromEntries(marketWindows.map((window) => [window, {
    changePct: metric(card.windows[window].changePct),
    // 市值變動 = (1+Δ價)(1+ΔPOP)−1。producer 出咗就直接用；舊 snapshot 冇呢條欄
    // 就即場由同一份 payload 入面兩條已出街嘅欄砌返（同一條式，`composeChangePct`）。
    // 兩個輸入有一個唔齊就出 null —— 唔准退返去用同窗口嘅 `changePct` 頂替。
    marketCapChangePct: metric(
      card.windows[window].marketCapChangePct
        ?? composeChangePct(
          card.windows[window].changePct,
          card.graderPopulations.PSA?.topGradePopulationChangePct?.[window],
        ),
    ),
    // 成交額環比要 producer 出真數；冇就係計唔到，一樣唔准借價格變動。
    trackedSalesChangePct: metric(
      card.windows[window].trackedSalesChangePct ?? { value: null, status: "accumulating", asOf: null },
    ),
    trackedSales: {
      valueUsd: metric(card.windows[window].trackedSales.valueUsd),
      count: metric(card.windows[window].trackedSales.count),
      coverage: card.windows[window].trackedSales.coverage,
      asOf: card.windows[window].trackedSales.asOf,
    },
  }])) as MarketCardView["windows"];

  return {
    id: card.id,
    rank: card.rank,
    tcg: card.tcg === "one-piece" ? "One Piece" : card.tcg === "pokemon" ? "Pokémon" : "TCG",
    language: card.language,
    collectorNumber: card.collectorNumber.display,
    name,
    setName: localisedSetName(card.sets),
    story: localised(stories, ""),
    image: {
      url: imageIsSafe ? card.image.src : "/card-placeholder.svg",
      alt: localised(card.image.alt, `${name.en} card artwork`),
      kind: imageIsSafe ? "raw_front" : "placeholder",
      variants: imageIsSafe ? card.image.variants : undefined,
    },
    pricePsa10: metric(card.pricePsa10),
    populationPsa10: metric(card.populationPsa10),
    marketCap: metric(card.marketCap),
    windows: Object.fromEntries(marketWindows.map((window) => [window, {
      changePct: borrowWindowMetric(window, (w) => rawWindows[w].changePct),
      marketCapChangePct: borrowWindowMetric(window, (w) => rawWindows[w].marketCapChangePct),
      trackedSalesChangePct: borrowWindowMetric(window, (w) => rawWindows[w].trackedSalesChangePct),
      trackedSales: borrowTrackedSales(window, (w) => rawWindows[w].trackedSales),
    }])) as MarketCardView["windows"],
    graderPopulations: Object.fromEntries(graders.map((grader) => {
      const changeRaw = Object.fromEntries(marketWindows.map((window) => [
        window,
        metric(card.graderPopulations[grader].topGradePopulationChangePct[window]),
      ])) as Record<MarketWindow, MarketMetric<number>>;
      return [grader, {
        topGrade: card.graderPopulations[grader].topGrade,
        total: { ...metric(card.graderPopulations[grader].total), estimated: card.graderPopulations[grader].total.estimated },
        topGradePopulation: {
          ...metric(card.graderPopulations[grader].topGradePopulation),
          estimated: card.graderPopulations[grader].topGradePopulation.estimated,
        },
        topGradePopulationChangePct: Object.fromEntries(marketWindows.map((window) => [
          window,
          borrowWindowMetric(window, (w) => changeRaw[w]),
        ])) as MarketCardView["graderPopulations"][typeof grader]["topGradePopulationChangePct"],
      }];
    })) as MarketCardView["graderPopulations"],
    historyDaily: card.historyDaily.map((point) => ({ ...point })),
  };
}

export function normaliseSnapshot(snapshot: CanonicalSnapshot): MarketViewSnapshot {
  const rates = Object.fromEntries(currencies.map((currency) => [
    currency,
    snapshot.currencies.rates[currency].value ?? Number.NaN,
  ])) as Record<Currency, number>;
  return {
    schemaVersion: snapshot.schemaVersion,
    generation: snapshot.generation.id,
    generatedAt: snapshot.generation.generatedAt,
    effectiveAt: snapshot.generation.effectiveAt,
    mode: snapshot.generation.mode === "production" && snapshot.generation.productionEligible ? "canonical" : "preview",
    ratesAsOf: snapshot.currencies.asOf,
    rates,
    top100: snapshot.top100.map(cardView),
    watchlist: snapshot.watchlist.map(cardView),
  };
}

export function getSeedSnapshot(): MarketViewSnapshot {
  return normaliseSnapshot(seedSnapshot as unknown as CanonicalSnapshot);
}
