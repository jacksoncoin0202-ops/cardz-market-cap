export const locales = ["en", "zh-TW", "zh-CN", "ja"] as const;
export const currencies = ["USD", "HKD", "CNY", "GBP", "TWD"] as const;
export const marketWindows = ["1d", "7d", "30d"] as const;
export const graders = ["PSA", "BGS", "CGC", "SGC"] as const;

export type Locale = (typeof locales)[number];
export type Currency = (typeof currencies)[number];
export type MarketWindow = (typeof marketWindows)[number];
export type Grader = (typeof graders)[number];
export type MetricStatus = "ready" | "accumulating" | "stale" | "unavailable";
export type CoverageStatus = "partial" | "stale" | "unavailable";

export type LocalizedText = Record<Locale, string>;

export interface MarketMetric<T> {
  value: T | null;
  status: MetricStatus;
  asOf: string | null;
  anchorAt?: string | null;
}

export interface PricePoint {
  at: string;
  priceUsd: number | null;
  priceStatus: MetricStatus;
  trackedSalesValueUsd: number | null;
  trackedSalesCount: number | null;
  salesCoverage: CoverageStatus;
}

export interface TrackedSalesMetric {
  valueUsd: MarketMetric<number>;
  count: MarketMetric<number>;
  coverage: CoverageStatus;
  asOf: string | null;
}

export interface WindowMetrics {
  changePct: MarketMetric<number>;
  trackedSales: TrackedSalesMetric;
}

export interface GraderPopulationView {
  topGrade: string;
  total: MarketMetric<number> & { estimated?: boolean };
  topGradePopulation: MarketMetric<number> & { estimated?: boolean };
  topGradePopulationChangePct: Record<MarketWindow, MarketMetric<number>>;
}

export interface MarketCardView {
  id: string;
  rank: number;
  tcg: string;
  language: string;
  collectorNumber: string;
  name: LocalizedText;
  setName: LocalizedText;
  story: LocalizedText;
  image: {
    url: string;
    alt: LocalizedText;
    kind: "raw_front" | "placeholder";
  };
  pricePsa10: MarketMetric<number>;
  populationPsa10: MarketMetric<number>;
  marketCap: MarketMetric<number>;
  windows: Record<MarketWindow, WindowMetrics>;
  graderPopulations: Record<Grader, GraderPopulationView>;
  historyDaily: PricePoint[];
}

export interface MarketViewSnapshot {
  schemaVersion: string;
  generation: string;
  generatedAt: string;
  effectiveAt: string;
  mode: "canonical" | "preview";
  ratesAsOf: string | null;
  rates: Record<Currency, number>;
  top100: MarketCardView[];
  watchlist: MarketCardView[];
}
import type {
  PublicCard as CanonicalPublicCard,
  PublicMarketSnapshot as CanonicalPublicMarketSnapshot,
} from "@cardz/market-data";

export type { CanonicalPublicCard, CanonicalPublicMarketSnapshot };
