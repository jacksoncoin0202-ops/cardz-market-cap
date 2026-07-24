import seedSnapshot from "../../../../data/public/seed-snapshot.json";
import editorialPack from "../../../../data/editorial/top100-stories.json";
import type { PublicCard as CanonicalCard, PublicMarketSnapshot as CanonicalSnapshot } from "@cardz/market-data";
import { localizedCardName } from "./card-names";
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
    entry.cardLanguage !== card.language ||
    entry.collectorNumber !== card.collectorNumber.display
  ) return card.stories;
  return entry.stories;
}

function localised(value: CanonicalCard["names"], fallback: string, translate?: boolean): LocalizedText {
  const en = value.en || fallback;
  if (!translate) {
    return {
      en,
      "zh-TW": value.zhTW || value.en || fallback,
      "zh-CN": value.zhCN || value.zhTW || value.en || fallback,
      ja: value.ja || value.en || fallback,
      ko: value.en || fallback,
    };
  }
  return {
    en,
    "zh-TW": localizedCardName(en, value.zhTW, "zh-TW"),
    "zh-CN": value.zhCN || value.zhTW || localizedCardName(en, null, "zh-CN"),
    ja: localizedCardName(en, value.ja, "ja"),
    ko: localizedCardName(en, null, "ko"),
  };
}

function metric(value: CanonicalCard["pricePsa10"]): MarketMetric<number> {
  return { value: value.value, status: value.status, asOf: value.asOf };
}

function cardView(card: CanonicalCard): MarketCardView {
  const name = localised(card.names, `Card ${card.rank}`, true);
  const stories = editorialStories(card);
  const imageIsSafe = card.image.kind === "raw_front"
    && Boolean(card.image.qcAt)
    && /^\/market-assets\/[a-f0-9]{64}\.webp$/.test(card.image.src);

  return {
    id: card.id,
    rank: card.rank,
    tcg: card.tcg === "one-piece" ? "One Piece" : card.tcg === "pokemon" ? "Pokémon" : "TCG",
    language: card.language,
    collectorNumber: card.collectorNumber.display,
    name,
    setName: localised(card.sets, ""),
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
      changePct: metric(card.windows[window].changePct),
      trackedSales: {
        valueUsd: metric(card.windows[window].trackedSales.valueUsd),
        count: metric(card.windows[window].trackedSales.count),
        coverage: card.windows[window].trackedSales.coverage,
        asOf: card.windows[window].trackedSales.asOf,
      },
    }])) as MarketCardView["windows"],
    graderPopulations: Object.fromEntries(graders.map((grader) => [grader, {
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
    }])) as MarketCardView["graderPopulations"],
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
