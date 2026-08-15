export const locales = ["en", "zh-TW", "zh-CN", "ja", "ko"] as const;
export const currencies = ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"] as const;
export const marketWindows = ["1d", "7d", "30d", "90d", "180d", "365d"] as const;
export const producerWindows = ["1d", "7d", "30d"] as const;
export const marketWindowDays = {
  "1d": 1,
  "7d": 7,
  "30d": 30,
  "90d": 90,
  "180d": 180,
  "365d": 365,
} as const;
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
  /** Source chart/period date (e.g. PriceCharting month head 2026-08-01). */
  sourcePeriodAt?: string | null;
  /** Actual capture/check time used for daily freshness. */
  checkedAt?: string | null;
  anchorAt?: string | null;
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
   * 2026-08-11 起 `setName` / `collectorNumber` / `editionCode` 一樣停喺 canonical
   * snapshot：三條都係 printing_sha() 前像（指紋用字），而 apps/web 零讀者。
   * 顯示用嘅 set 名喺 `setName: LocalizedText`，編號喺頂層 `collectorNumber`。
   */
  printingIdentity: {
    setCode: string | null;
    finishCode: string | null;
  } | null;
  collectorNumber: string;
  /** Canonical PSA/GemRate official English full name used by every public title surface. */
  officialName: string | null;
  /** Locale aliases. List projection omits this; titles use `officialName`. */
  name?: LocalizedText;
  setName: LocalizedText;
  /** Detail-only. List projection omits this. */
  story?: LocalizedText;
  image: {
    url: string;
    /** Canonical image alternative text; always the same official name as the card title. */
    alt: string | null;
    kind: "raw_front" | "placeholder";
    variants?: Partial<Record<"200" | "600", string>>;
  };
  pricePsa10: MarketMetric<number>;
  /** Detail-only RAW / ungraded reference. List projection omits this. */
  priceUngradedReference?: MarketMetric<number>;
  populationPsa10: MarketMetric<number>;
  marketCap: MarketMetric<number>;
  windows: Record<MarketWindow, WindowMetrics>;
  /*
   * 完整每日歷史。只有 `/card/[id]` 用得着（HistoryChart）。
   * 榜頁（`scopeSnapshot` → `listCard`）會清空佢：實測佔榜頁 card bytes 約 89%，
   * 而榜頁根本冇組件讀佢，唯一嘅圖係 <Sparkline>，佢淨係要 `salesSparkline`。
   */
  historyDaily: PricePoint[];
  /*
   * `historyDaily` 入面非空且有限嘅 `trackedSalesValueUsd` 序列 —— <Sparkline>
   * 唯一會畫嘅數。喺 `normaliseSnapshot` 一次抽好，所以清空 historyDaily 之後
   * 榜頁嘅 sparkline 仍然逐點一樣。
   */
  salesSparkline: number[];
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
    /** live-db 模式先有；baked snapshot / scoped view 冇（optional）。 */
    changeReady?: Record<MarketWindow, number>;
    salesReady?: Record<MarketWindow, number>;
    completeIdentityCount?: number;
    localizedStoryCount?: Record<Locale, number>;
  };
  ratesAsOf: string | null;
  rates: Record<Currency, number>;
  top100: MarketCardView[];
  watchlist: MarketCardView[];
  /** BOX sidecar overlay. Absent when box-subset.json is missing. Never part of PSA10 seed. */
  sealed?: SealedViewBlock;
}

export const sealedGroups = ["optcg-en", "optcg-jp", "ptcg-en", "ptcg-jp"] as const;
export type SealedGroup = (typeof sealedGroups)[number];
export type SealedPriceKind = "sold" | "market" | "ask";

export interface SealedNative {
  amount: number;
  currency: string;
}

export interface SealedProductView {
  id: string;
  rank: number;
  game: "ptcg" | "optcg";
  lang: "en" | "jp";
  group: SealedGroup;
  setCode: string;
  name: LocalizedText;
  fullName: LocalizedText | null;
  release: string | null;
  packsPerBox: number;
  productKind: string;
  printWave: string;
  status: "active" | "unreleased";
  image: {
    url: string;
    kind: "box_front" | "placeholder";
    variants?: Partial<Record<"200" | "600", string>>;
  };
  story?: LocalizedText | null;
  priceUsd: MarketMetric<number>;
  priceKind: SealedPriceKind | null;
  priceNative: SealedNative | null;
  askFloorUsd: MarketMetric<number>;
  askFloorNative: SealedNative | null;
  windows: Record<MarketWindow, { changePct: MarketMetric<number>; soldCount: number }>;
  historyDaily: PricePoint[];
  salesSparkline: number[];
}

export interface SealedViewBlock {
  asOf: string;
  coverage: { total: number; priced: number; imaged: number };
  products: SealedProductView[];
}
import type {
  PublicCard as CanonicalPublicCard,
  PublicMarketSnapshot as CanonicalPublicMarketSnapshot,
} from "@cardz/market-data";

export type { CanonicalPublicCard, CanonicalPublicMarketSnapshot };
