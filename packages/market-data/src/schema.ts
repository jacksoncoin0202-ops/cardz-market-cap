import type { ReleaseProfileId } from "./release-policy.generated.js";

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
export type SnapshotCoverageClaim = "verified-top-n" | "verified-top-100";

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
  /** 參考價（PSA10）嘅窗口變動。淨係價，唔包 POP。 */
  changePct: MarketMetric;
  /**
   * 市值窗口變動 = (1+Δ價)(1+ΔPOP)−1。
   *
   * Optional：呢個 producer 版本之前出街嘅 snapshot 冇呢條欄，舊 payload 一樣
   * 要驗得過。消費端見唔到就自己由 `changePct` × `topGradePopulationChangePct`
   * 砌返（`composeChangePct`），**唔准**退返去單用 `changePct`。
   */
  marketCapChangePct?: MarketMetric;
  /** 窗口成交金額對上一個同長度窗口嘅變動。同 `changePct` 冇任何數學關係。 */
  trackedSalesChangePct?: MarketMetric;
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

export interface PublicPrintingIdentity {
  setName: string;
  collectorNumber: string;
  editionCode: string;
  parallelCode: string;
  finishCode: string;
  /**
   * Physical print language in the 7-part canonicalPrintingSha256 key
   * (`tcg|lang|set|collector|edition|parallel|finish`). The nullable type can
   * read retained legacy evidence, but strict production validation rejects a
   * missing language.
   */
  cardLanguage?: CardLanguage | null;
  canonicalPrintingSha256: string;
  evidenceSha256: string;
}

export interface CanonicalDbQcBinding {
  runId: string;
  receiptSha256: string;
  database: "cardz_market_cap";
  universeCandidateSha256: string;
}

/** Canonical card *print* language (not UI locale). */
export type CardLanguage = "en" | "ja" | "ko" | "zhCN" | "zhTW";

export interface PublicCard {
  id: string;
  /** Legacy display rank. Kept equal to `viewRank` for existing consumers. */
  rank: number;
  /** Rank in the canonical market-cap universe before view-specific filtering. */
  marketRank: number;
  /** Contiguous rank in the current public view. */
  viewRank: number;
  tcg: Tcg;
  /**
   * Physical print language of this catalog identity (en|ja|ko|zhCN|zhTW).
   * Distinct from interface locale. Optional only while reading legacy demo
   * evidence; strict production validation requires it.
   */
  cardLanguage?: CardLanguage | null;
  collectorNumber: CollectorNumber;
  /** Required by the production release gate; optional only for legacy demo snapshots. */
  printingIdentity?: PublicPrintingIdentity;
  identityStatus: "confirmed" | "provisional" | "demo_observed";
  names: LocalizedText;
  sets: LocalizedText;
  stories: LocalizedText;
  image: PublicImage;
  pricePsa10: MarketMetric;
  /**
   * Detail-only ungraded / RAW reference. It never participates in PSA 10
   * ranking, market-cap, price deltas, tracked sales, or history.
   * Optional so pre-field snapshots remain valid.
   */
  priceUngradedReference?: MarketMetric;
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
  qcReceiptSha256: string;
  /** Required for production; omitted by pre-QC/demo candidates. */
  dbQc?: CanonicalDbQcBinding;
  /** Required for production; omitted only by retained legacy/demo candidates. */
  releaseProfile?: ReleaseProfileId;
  /** SHA-256 of the named policy as generated from config/data-routing.json. */
  policySha256?: string;
  /** SHA-256 fingerprint of the canonical CARDZ Market Cap database evaluation. */
  dbFingerprint?: string;
  /** Immutable canonical evaluation backing this generation. */
  evaluationId?: number;
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
    claim: SnapshotCoverageClaim;
    requestedCount: 100;
    verifiedCount: number;
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
