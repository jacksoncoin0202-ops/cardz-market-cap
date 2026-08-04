import seedSnapshot from "../../../../data/public/seed-snapshot.json";
import editorialPack from "../../../../data/editorial/top100-stories.json";
import setNamePack from "../../../../data/editorial/set-names.json";
import { composeChangePct, type PublicCard as CanonicalCard, type PublicMarketSnapshot as CanonicalSnapshot } from "@cardz/market-data";
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
    entry.collectorNumber !== card.collectorNumber.display
  ) return card.stories;
  return {
    ...entry.stories,
    ko: card.stories.ko ?? null,
  };
}

type SetNameEntry = { zhTW?: string; zhCN?: string; ja?: string; ko?: string };

/*
 * Set 名嘅翻譯來源。呢個檔一直存在但從來冇人 import，而 snapshot 入面
 * `sets.zhTW / zhCN / ja` 三條全部係空字串（360 張卡逐張核過），
 * 所以五個語系嘅 set 名一律跌返英文。接返線之後 209/360 張卡有中日文 set 名。
 */
const editorialSetNames = setNamePack.entries as Record<string, SetNameEntry>;

function editorialSetEntry(en: string): SetNameEntry | undefined {
  if (!en) return undefined;
  const direct = editorialSetNames[en];
  if (direct) return direct;
  const candidates = [
    en,
    en.replace(/\s+&\s+/g, " and "),
    en.replace(/\s+and\s+/gi, " & "),
    en.replace(/^Pokemon\s+/i, ""),
    en.replace(/^One Piece\s+/i, ""),
    en.replace(/^Pokemon\s+/i, "").replace(/\s+&\s+/g, " and "),
    en.replace(/^One Piece\s+/i, "").replace(/\s+&\s+/g, " and "),
    en.replace(/Sword Shield/gi, "Sword and Shield"),
    en.replace(/Scarlet Violet/gi, "Scarlet and Violet"),
    `Pokemon ${en}`,
    `One Piece ${en}`,
  ];
  for (const key of candidates) {
    const hit = editorialSetNames[key];
    if (hit) return hit;
  }
  // case-insensitive last pass
  const lower = en.toLowerCase();
  for (const [key, value] of Object.entries(editorialSetNames)) {
    if (key.toLowerCase() === lower) return value;
  }
  return undefined;
}

function localisedSetName(sets: CanonicalCard["sets"]): LocalizedText {
  const en = sets.en || "";
  const editorial = editorialSetEntry(en);
  const zhTW = sets.zhTW || editorial?.zhTW || "";
  return {
    en,
    "zh-TW": zhTW || en,
    "zh-CN": sets.zhCN || editorial?.zhCN || zhTW || en,
    ja: sets.ja || editorial?.ja || en,
    // Prefer real KO set title from export/editorial; else keep English product title.
    ko: sets.ko || editorial?.ko || en,
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
      // Stories/sets may carry real KO text from operator export.
      ko: value.ko || null,
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
    ko: localizedCardName(en, stale ? null : value.ko, "ko"),
  };
}

function metric(value: CanonicalCard["pricePsa10"]): MarketMetric<number> {
  return { value: value.value, status: value.status, asOf: value.asOf };
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
 */
function printingIdentityView(card: CanonicalCard): MarketCardView["printingIdentity"] {
  const identity = card.printingIdentity;
  if (!identity) return null;
  return {
    setName: identity.setName || "",
    setCode: identity.setCode || null,
    collectorNumber: identity.collectorNumber || "",
    // 卡包名：owner 2026-08-02 批准出喺內頁／熱力圖彈卡（唔准入 Top 100 表）。
    editionCode: identity.editionCode || null,
    rarityCode: identity.rarityCode || null,
    parallelCode: identity.parallelCode || null,
    finishCode: identity.finishCode || null,
    printingCode: identity.printingCode || null,
  };
}

function cardView(card: CanonicalCard): MarketCardView {
  const name = localised(card.names, `Card ${card.rank}`, true);
  const stories = editorialStories(card);
  // 圖片信任來自 release receipt + rejection registry（owner 2026-08-02 拆舊 image QC gate）；
  // receipt 路徑出嘅 qcAt 永遠係 null，再要求 qcAt 就會全站 placeholder。
  const imageIsSafe = card.image.kind === "raw_front"
    && /^\/market-assets\/[a-f0-9]{64}\.webp$/.test(card.image.src);

  const windows = Object.fromEntries(marketWindows.map((window) => [window, {
    changePct: metric(card.windows[window].changePct),
    // Daily POP chase: growth from daily PSA10 points when present; else price-only cap change.
    marketCapChangePct: metric((() => {
      if (card.windows[window].marketCapChangePct) return card.windows[window].marketCapChangePct;
      const composed = composeChangePct(
        card.windows[window].changePct,
        card.graderPopulations.PSA?.topGradePopulationChangePct?.[window],
      );
      return composed.value !== null ? composed : card.windows[window].changePct;
    })()),
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
    // Older public snapshots predate this optional field. Keep the view contract
    // stable and render an explicit unavailable reference instead of borrowing PSA 10.
    priceUngradedReference: metric(
      card.priceUngradedReference ?? { value: null, status: "unavailable", asOf: null },
    ),
    populationPsa10: metric(card.populationPsa10),
    marketCap: metric(card.marketCap),
    windows,
    graderPopulations: Object.fromEntries(graders.map((grader) => {
      return [grader, {
        topGrade: card.graderPopulations[grader].topGrade,
        total: { ...metric(card.graderPopulations[grader].total), estimated: card.graderPopulations[grader].total.estimated },
        topGradePopulation: {
          ...metric(card.graderPopulations[grader].topGradePopulation),
          estimated: card.graderPopulations[grader].topGradePopulation.estimated,
        },
        topGradePopulationChangePct: Object.fromEntries(marketWindows.map((window) => [
          window,
          metric(card.graderPopulations[grader].topGradePopulationChangePct[window]),
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
    qcReceiptSha256: snapshot.generation.qcReceiptSha256,
    coverage: {
      claim: snapshot.coverage.claim,
      requestedCount: snapshot.coverage.requestedCount,
      verifiedCount: snapshot.coverage.verifiedCount,
    },
    ratesAsOf: snapshot.currencies.asOf,
    rates,
    top100: snapshot.top100.map(cardView),
    watchlist: snapshot.watchlist.map(cardView),
  };
}

export function getSeedSnapshot(): MarketViewSnapshot {
  return normaliseSnapshot(seedSnapshot as unknown as CanonicalSnapshot);
}
