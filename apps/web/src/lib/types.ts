export const locales = ["en", "zh-TW", "zh-CN", "ja", "ko"] as const;
/*
 * 貨幣正典次序（canonical order）：USD 一定係 index 0（base，所有價由 USD 換算）。
 * 同一份清單同一個次序活喺三個地方，改就要三個一齊改：
 *   1. 呢度（FE 選單 + `Currency` type）
 *   2. `packages/market-data/src/schema.ts` `CURRENCIES`（canonical snapshot 契約）
 *   3. `pipelines/fx_rates.py` `SUPPORTED_CURRENCIES`（FX 採集 + snapshot 驗證）
 * 三邊唔同步 = baked snapshot 缺匯率；FE 側靠 `availableCurrencies()`（server-snapshot.ts）
 * 過濾，缺嗰隻唔會俾人揀到，唔會出「暫無資料」。
 */
export const currencies = [
  "USD", "HKD", "TWD", "JPY", "KRW", "CNY", "SGD", "MYR", "THB", "PHP",
  "IDR", "VND", "INR", "AUD", "NZD", "EUR", "GBP", "CHF", "SEK", "NOK",
  "DKK", "PLN", "CZK", "CAD", "MXN", "BRL", "AED", "SAR", "ILS", "TRY",
  "ZAR",
] as const;
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
export const defaultMarketWindow: MarketWindow = "180d";
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

/*
 * 圖片內在尺寸嘅唯一收窄點（`image.width` / `image.height`）。三個投影
 * （`snapshot.ts` / `catalog-search.ts` / `box-view.ts`）都 call 佢，唔好逐處
 * 自己寫一套判斷 —— 同一條問題三份 copy 就會有一份放行咗 0（AGENTS.md 規矩 13）。
 * 兩個都係正整數先出，任何一邊缺／0／非有限就成對唔出：`<img>` 得一半尺寸
 * 冇 aspect-ratio，等於白做，而填 0 會令個盒真係塌成 0。
 */
/*
 * 卡圖 variant 畫布：`pipelines/build_asset_derivatives.py:13-15`
 * （`normalize_card_canvas` → 429×600 透明畫布等比置中，`_600` = 429×600、`_200` = 200×280）。
 * `srcSet()` 一定出 variants + `sizes`，所以瀏覽器**永遠唔會**畫 base 檔 —— 宣告尺寸
 * 只可以當「比例」用，而個比例要描述真係會畫嗰個檔。
 */
const CARD_CANVAS_ASPECT = 429 / 600;
/* `_200`（200×280 = 0.7143）同 429/600（0.7150）差 0.1%，所以 1% 容差擺得落兩個 variant。 */
const CARD_ASPECT_TOLERANCE = 0.01;

export function intrinsicSize(
  width: number | null | undefined,
  height: number | null | undefined,
  kind?: string | null,
): { width: number; height: number } | Record<string, never> {
  if (!Number.isFinite(width) || !Number.isFinite(height)) return {};
  const w = Math.round(width as number);
  const h = Math.round(height as number);
  if (w <= 0 || h <= 0) return {};
  /*
   * Fail-closed：base 檔嘅比例對唔上 variant 畫布就成對唔出。
   * 實測 `/api/v1/catalog` 1911 條：1597 張卡係 429×600，另外 6 張（719×1000 ×3、
   * 600×838、500×698、431×600）比例全部喺 0.6% 之內 —— 照出；淨係 **1 張** 宣告
   * 1000×730（橫向，差 91%），佢出街嗰個 `_600` 其實係 429×600 直度。個唯一
   * `width:auto` 消費者（`.preview-image img`，桌面熱力圖 hover）會照住宣告值開一個
   * 橫向盒，圖一到就跳 —— 正正係呢個 attribute pair 要防嘅 CLS。宣告錯不如唔宣告。
   * `box_front` 唔行呢個畫布（實測 1000×730 / 750×750 / 1600×1600 都有），所以只夾卡。
   * 正解係 bake 側寫 variant 尺寸（DESIGN.md 欠單 21），呢度只係唔准講大話。
   */
  if (kind === "raw_front" && Math.abs(w / h / CARD_CANVAS_ASPECT - 1) > CARD_ASPECT_TOLERANCE) return {};
  return { width: w, height: h };
}

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
    /*
     * Baked intrinsic pixels（`PublicImage.width/height`）。`<img width height>` 靠佢
     * 開盒佔位，圖未到之前唔會 0×0 再撐開（CLS）。**唔知就唔准填**：placeholder
     * 冇尺寸、live-db 冇量到就係 0，兩種都係 omit，唔准借另一張卡嘅比例。
     */
    width?: number;
    height?: number;
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

/*
 * 全站搜尋用嘅瘦身索引。榜頁 `scopeSnapshot` 只帶當前 view（例如 Top 100），
 * 所以搜尋唔可以只打 `cards` —— 要另外攞呢份（`/api/v1/catalog`）。
 * 唔掛上 RSC snapshot，避免每個榜頁多送 ~1600 行。
 */
export interface CatalogEntry {
  kind: "card" | "box";
  id: string;
  href: string;
  officialName: string | null;
  name: Partial<Record<Locale, string>>;
  collectorNumber: string;
  setName: Partial<Record<Locale, string>>;
  setCode: string | null;
  tcg: string;
  cardLanguage: PrintLanguage | null;
  marketRank: number;
  image: {
    url: string;
    alt: string | null;
    kind: "raw_front" | "placeholder" | "box_front";
    variants?: Partial<Record<"200" | "600", string>>;
    /** 同 `MarketCardView.image`：唔知就 omit。 */
    width?: number;
    height?: number;
  };
  pricePsa10?: MarketMetric<number>;
  populationPsa10?: MarketMetric<number>;
  marketCap?: MarketMetric<number>;
  windows?: MarketCardView["windows"];
  salesSparkline?: number[];
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
  /** 熱力圖永遠用市值前 100。榜表 `top100` 而家可以係任一頁 slice。 */
  lead100?: MarketCardView[];
  /** BOX sidecar overlay. Absent when box-subset.json is missing. Never part of PSA10 seed. */
  sealed?: SealedViewBlock;
}

export const sealedGroups = ["optcg-en", "optcg-jp", "ptcg-en", "ptcg-jp"] as const;
export type SealedGroup = (typeof sealedGroups)[number];
/* /box 上面只分 TCG（owner 2026-08-17）：語言唔再係一粒 group 掣，改用同卡榜一樣嘅
   EN/JP 語言篩（sort/filter）。資料層 `product.group` 仍然係四值，唔郁。 */
export const sealedTcgs = ["optcg", "ptcg"] as const;
export type SealedTcg = (typeof sealedTcgs)[number];
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
    /** 同 `MarketCardView.image`：唔知就 omit。 */
    width?: number;
    height?: number;
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
