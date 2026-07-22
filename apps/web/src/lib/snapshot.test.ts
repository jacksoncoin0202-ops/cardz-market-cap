import { describe, expect, it } from "vitest";
import type { PublicMarketSnapshot } from "@cardz/market-data";
import { getSeedSnapshot, normaliseSnapshot } from "./snapshot";

const asOf = "2026-07-22T00:00:00.000Z";
const metric = (value: number | null) => ({
  value,
  status: value === null ? "accumulating" as const : "ready" as const,
  asOf,
});
const population = (value: number | null) => ({ ...metric(value), estimated: false });
const populationChanges = (value: number | null = null) => ({
  "1d": metric(value),
  "7d": metric(value),
  "30d": metric(value),
});
const sales = (value: number | null, count: number | null) => ({
  valueUsd: metric(value),
  count: metric(count),
  coverage: value === null ? "unavailable" as const : "partial" as const,
  asOf: value === null ? null : asOf,
});

function snapshot(): PublicMarketSnapshot {
  const card: PublicMarketSnapshot["top100"][number] = {
    id: "cmc_7fa922",
    rank: 1,
    tcg: "pokemon",
    language: "English",
    collectorNumber: { display: "085/SVP", normalized: "085SVP", complete: true },
    identityStatus: "confirmed",
    names: { en: "Pikachu", zhTW: "皮卡丘", zhCN: "皮卡丘", ja: "ピカチュウ" },
    sets: { en: "Promo", zhTW: "宣傳卡", zhCN: "宣传卡", ja: "プロモ" },
    stories: { en: "A documented market story.", zhTW: "完整市場故事。", zhCN: "完整市场故事。", ja: "市場の物語。" },
    image: {
      src: "/market-assets/7fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9.webp",
      sha256: "7fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9227fa9",
      kind: "raw_front",
      width: 630,
      height: 880,
      alt: { en: "Pikachu card", zhTW: "皮卡丘卡牌", zhCN: "皮卡丘卡牌", ja: "ピカチュウカード" },
      qcAt: asOf,
    },
    pricePsa10: metric(2993),
    populationPsa10: population(49496),
    marketCap: metric(148141528),
    windows: {
      "1d": { changePct: metric(null), trackedSales: sales(null, null) },
      "7d": { changePct: metric(1.25), trackedSales: sales(22440, 8) },
      "30d": { changePct: metric(-5.32), trackedSales: sales(61200, 21) },
    },
    graderPopulations: {
      PSA: { topGrade: "10", total: population(66000), topGradePopulation: population(49496), topGradePopulationChangePct: populationChanges(1.5) },
      BGS: { topGrade: "10", total: population(null), topGradePopulation: population(null), topGradePopulationChangePct: populationChanges() },
      CGC: { topGrade: "10", total: population(null), topGradePopulation: population(null), topGradePopulationChangePct: populationChanges() },
      SGC: { topGrade: "10", total: population(null), topGradePopulation: population(null), topGradePopulationChangePct: populationChanges() },
    },
    historyDaily: [
      { at: "2026-07-15", priceUsd: 2950, priceStatus: "ready", trackedSalesValueUsd: 3000, trackedSalesCount: 1, salesCoverage: "partial" },
      { at: "2026-07-22", priceUsd: 2993, priceStatus: "ready", trackedSalesValueUsd: 4200, trackedSalesCount: 2, salesCoverage: "partial" },
    ],
  };
  return {
    schemaVersion: "2.0.0",
    generation: { id: "generation-1", generatedAt: asOf, effectiveAt: asOf, contentSha256: "abc", mode: "production", productionEligible: true, blockers: [] },
    universe: { populationMin: 1000, grade: "PSA 10", rankingMetric: "psa10_market_cap_usd", windows: ["1d", "7d", "30d"], salesCoverage: "partial" },
    coverage: {
      top100Count: 1,
      watchlistCount: 0,
      changeReady: { "1d": 0, "7d": 1, "30d": 1 },
      salesReady: { "1d": 0, "7d": 1, "30d": 1 },
      graderPopulationReady: { PSA: 1, BGS: 0, CGC: 0, SGC: 0 },
      graderPopulationChangeReady: {
        PSA: { "1d": 1, "7d": 1, "30d": 1 },
        BGS: { "1d": 0, "7d": 0, "30d": 0 },
        CGC: { "1d": 0, "7d": 0, "30d": 0 },
        SGC: { "1d": 0, "7d": 0, "30d": 0 },
      },
      completeIdentityCount: 1,
      localizedStoryCount: { en: 1, zhTW: 1, zhCN: 1, ja: 1 },
    },
    currencies: {
      base: "USD",
      supported: ["USD", "HKD", "CNY", "GBP", "TWD"],
      rates: { USD: metric(1), HKD: metric(7.8), CNY: metric(7.2), GBP: metric(0.78), TWD: metric(32.5) },
      asOf,
    },
    top100: [card],
    watchlist: [],
  };
}

describe("canonical snapshot view adapter", () => {
  it("maps the v2 public contract without changing market values", () => {
    const view = normaliseSnapshot(snapshot());
    expect(view.mode).toBe("canonical");
    expect(view.effectiveAt).toBe(asOf);
    expect(view.top100[0].collectorNumber).toBe("085/SVP");
    expect(view.top100[0].name["zh-TW"]).toBe("皮卡丘");
    expect(view.top100[0].windows["7d"].trackedSales.valueUsd.value).toBe(22440);
    expect(view.top100[0].historyDaily.at(-1)?.priceUsd).toBe(2993);
    expect(view.top100[0].image.kind).toBe("raw_front");
    expect(view.top100[0].graderPopulations.PSA.topGradePopulationChangePct["30d"].value).toBe(1.5);
  });

  it("joins only identity-guarded, reviewed editorial copy into the staging view", () => {
    const view = getSeedSnapshot();
    expect(view.top100[0].collectorNumber).toBe("085/SVP");
    expect(view.top100[0].story.en).toContain("Van Gogh");
    expect(view.top100[0].story["zh-TW"]).toContain("梵高");
  });
});
