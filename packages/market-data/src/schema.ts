export const SNAPSHOT_SCHEMA_VERSION = "2.0.0" as const;

export const MARKET_STATUSES = [
  "ready",
  "accumulating",
  "stale",
  "unavailable",
] as const;

export const MARKET_WINDOWS = ["1d", "7d", "30d"] as const;
export const GRADERS = ["PSA", "BGS", "CGC", "SGC", "TAG"] as const;
export const COVERAGE_STATUSES = ["partial", "stale", "unavailable"] as const;
export const CURRENCIES = ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"] as const;

export type MarketStatus = (typeof MARKET_STATUSES)[number];
export type MarketWindow = (typeof MARKET_WINDOWS)[number];
export type Grader = (typeof GRADERS)[number];
export type CoverageStatus = (typeof COVERAGE_STATUSES)[number];
export type Locale = "en" | "zhTW" | "zhCN" | "ja";
export type Currency = (typeof CURRENCIES)[number];
export type Tcg = "pokemon" | "one-piece" | "other";
export type PublicImageKind = "raw_front";

export interface MarketMetric {
  value: number | null;
  status: MarketStatus;
  asOf: string | null;
}

export interface PopulationMetric extends MarketMetric {
  estimated: boolean;
}

export interface LocalizedText {
  en: string | null;
  zhTW: string | null;
  zhCN: string | null;
  ja: string | null;
}

export interface CollectorNumber {
  display: string;
  normalized: string;
  complete: boolean;
}

export interface PublicImage {
  src: string;
  sha256: string;
  kind: PublicImageKind;
  width: number;
  height: number;
  alt: LocalizedText;
  qcAt: string;
  variants?: Partial<Record<"200" | "600", string>>;
}

export interface TrackedSalesMetric {
  valueUsd: MarketMetric;
  count: MarketMetric;
  coverage: CoverageStatus;
  asOf: string | null;
}

export interface WindowMetrics {
  changePct: MarketMetric;
  trackedSales: TrackedSalesMetric;
}

export interface GraderPopulation {
  topGrade: string;
  total: PopulationMetric;
  topGradePopulation: PopulationMetric;
  topGradePopulationChangePct: Record<MarketWindow, MarketMetric>;
}

/**
 * One truthful daily close plus the tracked-sale aggregate discovered for that
 * date. This is deliberately not OHLC: no candle is emitted until source
 * transaction ordering can support one.
 */
export interface DailyHistoryPoint {
  at: string;
  priceUsd: number | null;
  priceStatus: MarketStatus;
  trackedSalesValueUsd: number | null;
  trackedSalesCount: number | null;
  salesCoverage: CoverageStatus;
}

export interface PublicCard {
  id: string;
  rank: number;
  tcg: Tcg;
  language: string;
  collectorNumber: CollectorNumber;
  identityStatus: "confirmed" | "demo_observed";
  names: LocalizedText;
  sets: LocalizedText;
  stories: LocalizedText;
  image: PublicImage;
  pricePsa10: MarketMetric;
  populationPsa10: PopulationMetric;
  marketCap: MarketMetric;
  windows: Record<MarketWindow, WindowMetrics>;
  graderPopulations: Record<Grader, GraderPopulation>;
  historyDaily: DailyHistoryPoint[];
}

export interface SnapshotGeneration {
  id: string;
  generatedAt: string;
  effectiveAt: string;
  contentSha256: string;
  mode: "demo" | "production";
  productionEligible: boolean;
  blockers: string[];
}

export interface PublicMarketSnapshot {
  schemaVersion: typeof SNAPSHOT_SCHEMA_VERSION;
  generation: SnapshotGeneration;
  universe: {
    populationMin: 1000;
    grade: "PSA 10";
    rankingMetric: "psa10_market_cap_usd";
    windows: typeof MARKET_WINDOWS;
    salesCoverage: "partial";
  };
  coverage: {
    top100Count: number;
    watchlistCount: number;
    changeReady: Record<MarketWindow, number>;
    salesReady: Record<MarketWindow, number>;
    graderPopulationReady: Record<Grader, number>;
    graderPopulationChangeReady: Record<Grader, Record<MarketWindow, number>>;
    completeIdentityCount: number;
    localizedStoryCount: Record<Locale, number>;
  };
  currencies: {
    base: "USD";
    supported: Currency[];
    rates: Record<Currency, MarketMetric>;
    asOf: string | null;
  };
  top100: PublicCard[];
  watchlist: PublicCard[];
}
