export const SNAPSHOT_SCHEMA_VERSION = "2.0.0" as const;

export const MARKET_STATUSES = [
  "ready",
  "accumulating",
  "stale",
  "unavailable",
] as const;

export const MARKET_WINDOWS = ["1d", "7d", "30d"] as const;
export const COVERAGE_STATUSES = ["complete", "partial", "stale", "unavailable"] as const;
export const CURRENCIES = ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"] as const;

export type MarketStatus = (typeof MARKET_STATUSES)[number];
export type MarketWindow = (typeof MARKET_WINDOWS)[number];
export const MARKET_WINDOW_DAYS: Readonly<Record<MarketWindow, number>> = Object.freeze({
  "1d": 1,
  "7d": 7,
  "30d": 30,
});
export type CoverageStatus = (typeof COVERAGE_STATUSES)[number];
export type Locale = "en" | "zhTW" | "zhCN" | "ja" | "ko";
export type Currency = (typeof CURRENCIES)[number];
export type Tcg = "pokemon" | "one-piece" | "other";
export type PublicImageKind = "raw_front";
export type SnapshotCoverageClaim = "verified-top-n" | "verified-top-100";

export interface MarketMetric {
  value: number | null;
  status: MarketStatus;
  /** Public "last updated" time. For PSA10 price this is checkedAt (043). */
  asOf: string | null;
  /** Source chart/period date (e.g. PriceCharting month head). Optional. */
  sourcePeriodAt?: string | null;
  /** Actual capture/check time used for freshness. Optional. */
  checkedAt?: string | null;
  /**
   * True when the current price and its historical anchor use different exact
   * providers. The provider names themselves are deliberately absent from this
   * type: the canonical snapshot is shipped to the browser verbatim, so a field
   * that carries one is a published field. Only the boolean survives.
   */
  sourceSwitched?: boolean;
}

export interface PopulationMetric extends MarketMetric {
  estimated: boolean;
}

export interface LocalizedText {
  en: string | null;
  zhTW: string | null;
  zhCN: string | null;
  ja: string | null;
  ko: string | null;
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
  /** Optional window market-cap change. 2026-08 policy: may equal price changePct; ΔPOP not product-maintained. */
  marketCapChangePct?: MarketMetric;
  /** 窗口成交金額對上一個同長度窗口嘅變動。同 `changePct` 冇任何數學關係。 */
  trackedSalesChangePct?: MarketMetric;
  trackedSales: TrackedSalesMetric;
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
  salesVerifiedZero: boolean;
}

export interface PublicPrintingIdentity {
  setName: string;
  collectorNumber: string;
  editionCode: string;
  finishCode: string;
  /** Short set code（例如 "EVS"）；唔係每個 producer 都有，所以 optional。 */
  setCode?: string | null;
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

/** Canonical card *print* language (not UI locale). */
export type CardLanguage = "en" | "ja" | "ko" | "zhCN" | "zhTW";

export interface PublicCard {
  id: string;
  /** Legacy display rank. Kept equal to `viewRank` for existing consumers. */
  rank: number;
  /** Rank in the canonical market-cap universe; 0 means awaiting a fresh price and unranked. */
  marketRank: number;
  /** Contiguous rank in the current public view; 0 preserves the unranked state. */
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
  /**
   * PSA/GemRate official English full name. This is the canonical public title;
   * locale aliases must never replace it. Optional only while reading retained
   * pre-026 snapshots; production validation requires a non-empty value.
   */
  officialName?: string;
  /** Optional locale aliases / translations. They are never title authority. */
  names?: LocalizedText;
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
  historyDaily: DailyHistoryPoint[];
}

export interface SnapshotGeneration {
  id: string;
  generatedAt: string;
  effectiveAt: string;
}

export interface PublicMarketSnapshot {
  schemaVersion: typeof SNAPSHOT_SCHEMA_VERSION;
  generation: SnapshotGeneration;
  universe: {
    memberCount: number;
    populationMin: 1000;
    grade: "PSA 10";
    rankingMetric: "psa10_market_cap_usd";
    windows: typeof MARKET_WINDOWS;
    salesCoverage: "partial";
  };
  coverage: {
    claim: SnapshotCoverageClaim;
    requestedCount: number;
    verifiedCount: number;
    top100Count: number;
    watchlistCount: number;
    changeReady: Record<MarketWindow, number>;
    salesReady: Record<MarketWindow, number>;
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
