export const locales = ["en", "zh-TW", "zh-CN", "ja", "ko"] as const;
export const currencies = ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"] as const;
export const marketWindows = ["1d", "7d", "30d"] as const;
export const themes = ["light", "dark"] as const;

export type Locale = (typeof locales)[number];
export type Currency = (typeof currencies)[number];
export type MarketWindow = (typeof marketWindows)[number];
export type Theme = (typeof themes)[number];
export type MetricStatus = "ready" | "accumulating" | "stale" | "unavailable";
export type CoverageStatus = "complete" | "partial" | "stale" | "unavailable";
export type SnapshotCoverageClaim = "verified-top-n" | "verified-top-100";

/*
 * `en` 一定有值；其他語系冇來源時係 `null`，唔係英文副本。
 * 消費端一律 `card.setName[locale] || fallback`，所以 `null` 會跌落顯示層自己嘅
 * fallback（`t.status.unavailable` / `t.labels.imageAlt`），唔會扮咗有翻譯。
 */
export type LocalizedText = Record<Locale, string | null> & { en: string };

export interface MarketMetric<T> {
  value: T | null;
  status: MetricStatus;
  asOf: string | null;
  anchorAt?: string | null;
  priceAnchorSource?: string | null;
  sourceSwitched?: boolean;
}

export interface PricePoint {
  at: string;
  priceUsd: number | null;
  priceStatus: MetricStatus;
  trackedSalesValueUsd: number | null;
  trackedSalesCount: number | null;
  salesCoverage: CoverageStatus;
  salesVerifiedZero: boolean;
}

export interface TrackedSalesMetric {
  valueUsd: MarketMetric<number>;
  count: MarketMetric<number>;
  coverage: CoverageStatus;
  asOf: string | null;
}

export interface WindowMetrics {
  /** 參考價（PSA10）嘅窗口變動。淨係價，唔包 POP —— 唔好攞嚟當市值或成交額用。 */
  changePct: MarketMetric<number>;
  /** Producer 計算並發布嘅 canonical 市值窗口變動。 */
  marketCapChangePct: MarketMetric<number>;
  /** 窗口成交金額 vs 前一個同長度窗口。 */
  trackedSalesChangePct: MarketMetric<number>;
  trackedSales: TrackedSalesMetric;
}

/** Physical card print language (not UI locale). Canonical: en|ja|ko|zhCN|zhTW. */
export type PrintLanguage = "en" | "ja" | "ko" | "zhCN" | "zhTW";

export interface MarketCardView {
  id: string;
  /** Legacy alias for `viewRank`. */
  rank: number;
  marketRank: number;
  viewRank: number;
  tcg: string;
  /** Print language of this identity; null when unknown / not backfilled. */
  cardLanguage: PrintLanguage | null;
  /*
   * Display-safe projection of the canonical printing identity. `null` when the
   * snapshot carries no `printingIdentity` at all (legacy / seed evidence), and each
   * sub-field is `null` when the producer wrote an empty string for it.
   * Deliberately NOT the canonical `PublicPrintingIdentity`: that type carries
   * `canonicalPrintingSha256` / `evidenceSha256`, which must never reach the DOM.
   * `rarityCode` / `parallelCode` / `printingCode` deliberately stop at the
   * canonical snapshot and are not copied into this DOM-facing view type.
   * `editionCode`（卡包名）只用於 detail page / heatmap popup，唔入 Top 100 table。
   */
  printingIdentity: {
    setName: string;
    setCode: string | null;
    collectorNumber: string;
    editionCode?: string | null;
    finishCode: string | null;
  } | null;
  collectorNumber: string;
  /** Canonical PSA/GemRate official English full name used by every public title surface. */
  officialName: string | null;
  /** Optional locale aliases retained for non-title supporting uses. */
  name: LocalizedText;
  setName: LocalizedText;
  story: LocalizedText;
  image: {
    url: string;
    /** Canonical image alternative text; always the same official name as the card title. */
    alt: string | null;
    kind: "raw_front" | "placeholder";
    variants?: Partial<Record<"200" | "600", string>>;
  };
  pricePsa10: MarketMetric<number>;
  /** Detail-only RAW / ungraded reference; never used by rank, market cap, or deltas. */
  priceUngradedReference: MarketMetric<number>;
  populationPsa10: MarketMetric<number>;
  marketCap: MarketMetric<number>;
  windows: Record<MarketWindow, WindowMetrics>;
  historyDaily: PricePoint[];
}

export interface MarketViewSnapshot {
  schemaVersion: string;
  generation: string;
  generatedAt: string;
  effectiveAt: string;
  mode: "canonical" | "preview";
  coverage: {
    claim: SnapshotCoverageClaim;
    requestedCount: number;
    verifiedCount: number;
  };
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
